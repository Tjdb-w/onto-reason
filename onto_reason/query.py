"""SPARQL 风格基本图模式（BGP）查询。

支持的查询语法（关键字仅接受大写）：

    SELECT ?x ?y WHERE { ?x knows ?y . ?y likes ?z }
    SELECT *   WHERE { ?x knows ?y }

- SELECT 后为一个或多个变量，或单个星号；星号按模式从左到右首次出现的
  顺序投影全部变量。
- WHERE 花括号内为点号分隔的三元组模式；主语、宾语可为变量或常量，
  谓语必须是常量。
- 变量为 ``?`` 后跟至少一个 Unicode 字母、数字或下划线；常量为不含空白且
  不以 ``?`` 开头的名称，或双引号包裹的 JSON 字符串（解码后按完整字符串
  精确比较）。
- 模式在 OntologyModel.triples（显式事实 + 推理结论）上做连接匹配，
  同一变量跨模式绑定同一字符串，一条事实可被多个模式复用。

任何词法或语法问题都抛出带字符位置或模式序号的 OntologyError。
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any, Dict, List, Tuple

from .errors import OntologyError

# 单字符标点：花括号、点号、星号；它们不能作为常量名称的一部分。
_PUNCT = frozenset("{}.*")
_VAR_LEAD = "?"
_QUOTE = '"'
_ESCAPE = "\\"

_KW_SELECT = "SELECT"
_KW_WHERE = "WHERE"


class QueryResult:
    """查询结果：投影变量 + 等宽不可变字符串行。

    - variables：投影变量元组（保留查询文本中的 ``?`` 前缀）。
    - rows：每行一个不可变字符串元组，宽度与 variables 相同；行按投影
      取值去重后按字典序排列，无匹配时为空元组。
    """

    __slots__ = ("_variables", "_rows")

    def __init__(self, variables, rows) -> None:
        self._variables = tuple(variables)
        self._rows = tuple(tuple(row) for row in rows)

    @property
    def variables(self) -> Tuple[str, ...]:
        return self._variables

    @property
    def rows(self) -> Tuple[Tuple[str, ...], ...]:
        return self._rows

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, QueryResult)
            and self._variables == other._variables
            and self._rows == other._rows
        )

    def __hash__(self) -> int:
        return hash((self._variables, self._rows))

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"QueryResult(variables={self._variables!r}, rows={self._rows!r})"


# term 元组：("var", "?x") 或 ("const", "alice")
_Term = Tuple[str, str]


def _is_var_char(ch: str) -> bool:
    # 仅接受 Unicode 字母（L*）、十进制数字（Nd）与下划线；
    # 排除 isalnum 会放行的罗马数字、带圈数字等 N* 符号。
    category = unicodedata.category(ch)
    return ch == "_" or category[0] == "L" or category == "Nd"


class _Tokenizer:
    """把查询文本切为 token：(种类, 值, 起始字符位置)。"""

    def __init__(self, text: str) -> None:
        self._text = text
        self._tokens: List[Tuple[str, Any, int]] = []

    def tokenize(self) -> List[Tuple[str, Any, int]]:
        text = self._text
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if ch.isspace():
                i += 1
            elif ch == _VAR_LEAD:
                i = self._read_variable(i)
            elif ch == _QUOTE:
                i = self._read_string(i)
            elif ch in _PUNCT:
                self._tokens.append(("PUNCT", ch, i))
                i += 1
            else:
                i = self._read_name(i)
        return self._tokens

    def _read_variable(self, start: int) -> int:
        text = self._text
        j = start + 1
        n = len(text)
        while j < n and _is_var_char(text[j]):
            j += 1
        if j == start + 1:
            raise OntologyError(
                f"变量词法不合法（字符位置 {start + 1}）：'?' 后至少需要一个字母、"
                f"数字或下划线"
            )
        self._tokens.append(("VAR", text[start:j], start))
        return j

    def _read_string(self, start: int) -> int:
        text = self._text
        n = len(text)
        j = start + 1
        while j < n and text[j] != _QUOTE:
            if text[j] == _ESCAPE:
                j += 2
            else:
                j += 1
        if j >= n:
            raise OntologyError(
                f"字符串常量缺少右引号（字符位置 {start + 1}）"
            )
        raw = text[start : j + 1]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise OntologyError(
                f"字符串常量不是合法的 JSON 字符串（字符位置 {start + 1}）：{exc.msg}"
            ) from exc
        self._tokens.append(("STR", value, start))
        return j + 1

    def _read_name(self, start: int) -> int:
        text = self._text
        n = len(text)
        j = start
        while j < n:
            ch = text[j]
            if ch.isspace() or ch in _PUNCT or ch == _VAR_LEAD or ch == _QUOTE:
                break
            j += 1
        self._tokens.append(("NAME", text[start:j], start))
        return j


class _ParsedQuery:
    __slots__ = ("projection", "patterns")

    def __init__(self, projection: Tuple[str, ...], patterns: Tuple[Tuple[_Term, _Term, _Term], ...]) -> None:
        self.projection = projection
        self.patterns = patterns


class _QueryParser:
    """把 token 流解析为投影变量与三元组模式，失败时抛 OntologyError。"""

    def __init__(self, tokens: List[Tuple[str, Any, int]]) -> None:
        self._tokens = tokens
        self._i = 0

    def parse(self) -> _ParsedQuery:
        self._expect_keyword(_KW_SELECT, "查询缺少 SELECT 关键字")
        projection = self._parse_projection()
        self._expect_keyword(_KW_WHERE, "查询缺少 WHERE 关键字")
        patterns = self._parse_pattern_body()
        if self._i < len(self._tokens):
            kind, value, pos = self._tokens[self._i]
            raise OntologyError(
                f"未知语句成分 {self._render(kind, value)!r}（字符位置 {pos + 1}）："
                f"右花括号之后不允许再有内容"
            )
        return _ParsedQuery(projection, patterns)

    # ---------- 各语法片段 ----------

    def _parse_projection(self) -> Tuple[str, ...]:
        if self._i >= len(self._tokens):
            raise OntologyError(
                f"SELECT 后需要变量列表或 '*'（字符位置 {self._tokens_end_pos() + 1}）"
            )
        kind, value, pos = self._tokens[self._i]
        if kind == "PUNCT" and value == "*":
            self._i += 1
            if self._i < len(self._tokens):
                nkind, nvalue, npos = self._tokens[self._i]
                if not (nkind == "NAME" and nvalue == _KW_WHERE):
                    raise OntologyError(
                        f"SELECT 的 '*' 必须单独使用，不能与其他投影项混用，"
                        f"在字符位置 {npos + 1} 收到 {self._render(nkind, nvalue)!r}"
                    )
            return ("*",)
        if kind != "VAR":
            raise OntologyError(
                f"SELECT 后只能是变量或 '*'，收到 {self._render(kind, value)!r}"
                f"（字符位置 {pos + 1}）"
            )
        seen: Dict[str, int] = {}
        order: List[str] = []
        while self._i < len(self._tokens) and self._tokens[self._i][0] == "VAR":
            kind, value, pos = self._tokens[self._i]
            if value in seen:
                raise OntologyError(
                    f"投影变量 {value} 重复出现（字符位置 {pos + 1}）"
                )
            seen[value] = pos
            order.append(value)
            self._i += 1
        return tuple(order)

    def _parse_pattern_body(self) -> Tuple[Tuple[_Term, _Term, _Term], ...]:
        if self._i >= len(self._tokens) or self._tokens[self._i][:2] != ("PUNCT", "{"):
            if self._i < len(self._tokens):
                kind, value, pos = self._tokens[self._i]
            else:
                kind, value, pos = "EOF", "", self._tokens_end_pos()
            raise OntologyError(
                f"WHERE 后缺少左花括号 '{{'（字符位置 {pos + 1}）"
            )
        self._i += 1

        body: List[Tuple[str, Any, int]] = []
        closed = False
        while self._i < len(self._tokens):
            kind, value, pos = self._tokens[self._i]
            if kind == "PUNCT" and value == "}":
                self._i += 1
                closed = True
                break
            body.append(self._tokens[self._i])
            self._i += 1
        if not closed:
            raise OntologyError(
                f"模式体缺少右花括号 '}}'（字符位置 {self._tokens_end_pos() + 1}）"
            )
        if not body:
            raise OntologyError(
                "空模式体：WHERE 后的花括号内至少需要一个三元组模式"
            )

        patterns: List[Tuple[_Term, _Term, _Term]] = []
        segment: List[Tuple[str, Any, int]] = []
        for kind, value, pos in body:
            if kind == "PUNCT" and value == ".":
                self._finish_segment(segment, len(patterns), patterns)
                segment = []
            elif kind == "PUNCT":
                raise OntologyError(
                    f"模式 {len(patterns)} 中出现非法标点 {value!r}"
                    f"（字符位置 {pos + 1}）"
                )
            else:
                segment.append((kind, value, pos))
        # 末尾段：允许点号收尾（尾随点号），否则必须是一个完整模式。
        self._finish_segment(segment, len(patterns), patterns, allow_empty=True)
        return tuple(patterns)

    def _finish_segment(self, segment, index, patterns, allow_empty: bool = False) -> None:
        if not segment:
            if allow_empty and patterns:
                return
            raise OntologyError(
                f"三元组模式 {index} 项数不足：应为 主语 谓语 宾语 三项"
            )
        if len(segment) < 3:
            raise OntologyError(
                f"三元组模式 {index} 项数不足：应为 主语 谓语 宾语 三项，"
                f"实际有 {len(segment)} 项"
            )
        if len(segment) > 3:
            kind, value, pos = segment[3]
            raise OntologyError(
                f"三元组模式 {index} 项数过多：应为 主语 谓语 宾语 三项，"
                f"多余成分 {self._render(kind, value)!r}（字符位置 {pos + 1}）"
            )
        terms: List[_Term] = []
        for slot, (kind, value, pos) in zip(("主语", "谓语", "宾语"), segment):
            if kind == "VAR":
                terms.append(("var", value))
            elif kind in ("NAME", "STR"):
                terms.append(("const", value))
            else:  # pragma: no cover - 标点已在调用处拦截
                raise OntologyError(
                    f"模式 {index} 的{slot}不是合法的变量或常量"
                    f"（字符位置 {pos + 1}）"
                )
        if terms[1][0] == "var":
            raise OntologyError(
                f"三元组模式 {index} 的谓语不能是变量 {terms[1][1]!r}：谓语必须是常量"
            )
        patterns.append((terms[0], terms[1], terms[2]))

    # ---------- 工具 ----------

    def _expect_keyword(self, keyword: str, missing_msg: str) -> None:
        if self._i >= len(self._tokens):
            raise OntologyError(f"{missing_msg}（查询结束位置 {self._tokens_end_pos() + 1}）")
        kind, value, pos = self._tokens[self._i]
        if kind == "NAME" and value == keyword:
            self._i += 1
            return
        lowered = value if kind == "NAME" else None
        hint = ""
        if lowered is not None and lowered == keyword.lower():
            hint = "：关键字只接受大写形式"
        raise OntologyError(
            f"{missing_msg}，在字符位置 {pos + 1} 收到 "
            f"{self._render(kind, value)!r}{hint}"
        )

    def _render(self, kind: str, value: Any) -> str:
        if kind == "EOF":
            return "<查询结束>"
        return str(value)

    def _tokens_end_pos(self) -> int:
        # 供“查询结束”类诊断定位。
        return self._tokens[-1][2] if self._tokens else 0


def _star_projection(parsed: _ParsedQuery) -> Tuple[str, ...]:
    """星号投影：按模式从左到右、主谓宾顺序取变量首次出现顺序。"""
    order: List[str] = []
    seen = set()
    for s, _p, o in parsed.patterns:
        for kind, name in (s, o):
            if kind == "var" and name not in seen:
                seen.add(name)
                order.append(name)
    return tuple(order)


def parse_query(text) -> _ParsedQuery:
    """解析查询文本，返回投影变量与模式；非法时抛 OntologyError。"""
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str，收到 {type(text).__name__}"
        )
    tokens = _Tokenizer(text).tokenize()
    if not tokens:
        raise OntologyError("查询为空：缺少 SELECT 关键字")
    parsed = _QueryParser(tokens).parse()

    pattern_vars = set()
    for s, _p, o in parsed.patterns:
        for kind, name in (s, o):
            if kind == "var":
                pattern_vars.add(name)

    if parsed.projection == ("*",):
        # 模式中没有任何变量时投影零列：模式命中则得到一行空元组，否则空集。
        projection = _star_projection(parsed)
    else:
        var_pos = {value: pos for kind, value, pos in tokens if kind == "VAR"}
        for name in parsed.projection:
            if name not in pattern_vars:
                raise OntologyError(
                    f"投影变量 {name} 未在 WHERE 模式中出现"
                    f"（字符位置 {var_pos[name] + 1}）"
                )
        projection = parsed.projection
    return _ParsedQuery(projection, parsed.patterns)


def execute_query(model, text) -> QueryResult:
    """在模型的全部三元组（显式 + 推理）上执行查询。"""
    parsed = parse_query(text)

    # model.triples 已按字典序排列，枚举顺序确定，重复执行结果一致。
    facts = model.triples
    bindings: List[Dict[str, str]] = [{}]
    for (sk, sv), (pk, pv), (ok, ov) in parsed.patterns:
        next_bindings: List[Dict[str, str]] = []
        for binding in bindings:
            for fact in facts:
                # 解析阶段已保证谓语为常量。
                if fact.predicate != pv:
                    continue
                matched = dict(binding)
                if sk == "var":
                    if sv in matched and fact.subject != matched[sv]:
                        continue
                    matched[sv] = fact.subject
                elif fact.subject != sv:
                    continue
                if ok == "var":
                    if ov in matched and fact.object != matched[ov]:
                        continue
                    matched[ov] = fact.object
                elif fact.object != ov:
                    continue
                next_bindings.append(matched)
        bindings = next_bindings
        if not bindings:
            break

    unique_rows = {
        tuple(binding[var] for var in parsed.projection) for binding in bindings
    }
    rows = tuple(sorted(unique_rows))
    return QueryResult(parsed.projection, rows)
