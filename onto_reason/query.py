"""SPARQL 风格基本图模式（BGP）查询。

支持的语法（关键字只接受大写）：

    SELECT (变量... | *) WHERE { 三元组模式 ('.' 三元组模式)* '.'? }

- 变量：'?' 后跟至少一个 Unicode 字母、数字或下划线。
- 常量：不含空白且不以 '?' 开头的名称；或双引号包裹的 JSON 字符串，
  字符串解码后按完整字符串精确比较。
- 三元组模式固定为主语、谓语、宾语三项；谓语必须是常量。
- '*' 按模式从左到右首次出现的顺序投影全部变量。

查询在 OntologyModel.triples（显式 + 推理三元组）上做嵌套循环连接匹配，
同一变量跨模式绑定同一字符串，一条事实可被多个模式复用。
任何词法或语法错误都抛出带字符位置或模式序号的 OntologyError。
"""

from __future__ import annotations

import json
from typing import Any, List, Tuple

from .errors import OntologyError

# token 种类
_TOK_LBRACE = "LBRACE"
_TOK_RBRACE = "RBRACE"
_TOK_DOT = "DOT"
_TOK_STAR = "STAR"
_TOK_VAR = "VAR"
_TOK_NAME = "NAME"
_TOK_STRING = "STRING"

_DELIMITERS = frozenset('{}."?*')

# 模式项：("?", 变量名) 或 ("=", 字面值)
_VAR = "?"
_LIT = "="


class QueryResult:
    """查询结果：投影变量元组 + 等宽不可变字符串行元组。

    rows 已按投影取值去重并按字典序排序；无匹配时为空元组。
    """

    __slots__ = ("_variables", "_rows")

    def __init__(self, variables: Tuple[str, ...], rows: Tuple[Tuple[str, ...], ...]) -> None:
        self._variables = tuple(variables)
        self._rows = tuple(tuple(row) for row in rows)

    @property
    def variables(self) -> Tuple[str, ...]:
        return self._variables

    @property
    def rows(self) -> Tuple[Tuple[str, ...], ...]:
        return self._rows

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, QueryResult)
            and self._variables == other._variables
            and self._rows == other._rows
        )

    def __hash__(self) -> int:
        return hash((self._variables, self._rows))

    def __len__(self) -> int:
        return len(self._rows)

    def __iter__(self):
        return iter(self._rows)

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"QueryResult(variables={self._variables!r}, rows={self._rows!r})"


class _Token:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind: str, value: str, pos: int) -> None:
        self.kind = kind
        self.value = value
        self.pos = pos


def _is_var_char(ch: str) -> bool:
    # Unicode 字母、数字或下划线
    return ch == "_" or ch.isalnum()


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "{":
            tokens.append(_Token(_TOK_LBRACE, ch, i))
            i += 1
            continue
        if ch == "}":
            tokens.append(_Token(_TOK_RBRACE, ch, i))
            i += 1
            continue
        if ch == ".":
            tokens.append(_Token(_TOK_DOT, ch, i))
            i += 1
            continue
        if ch == "*":
            tokens.append(_Token(_TOK_STAR, ch, i))
            i += 1
            continue
        if ch == "?":
            start = i
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            if j == i + 1:
                raise OntologyError(
                    f"词法错误：'?' 后必须至少跟随一个字母、数字或下划线"
                    f"（字符位置 {start}）"
                )
            tokens.append(_Token(_TOK_VAR, text[start:j], start))
            i = j
            continue
        if ch == '"':
            start = i
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == '"':
                    break
                j += 1
            if j >= n:
                raise OntologyError(f"词法错误：字符串未闭合（字符位置 {start}）")
            raw = text[start : j + 1]
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise OntologyError(
                    f"词法错误：非法的 JSON 字符串（字符位置 {start}）：{exc.msg}"
                ) from exc
            if not isinstance(value, str):  # pragma: no cover - 双引号内容必为 JSON 字符串
                raise OntologyError(
                    f"词法错误：字符串常量必须解码为字符串（字符位置 {start}）"
                )
            tokens.append(_Token(_TOK_STRING, value, start))
            i = j + 1
            continue
        # 普通名称：消费到分隔符或空白为止
        start = i
        j = i
        while j < n and not text[j].isspace() and text[j] not in _DELIMITERS:
            j += 1
        tokens.append(_Token(_TOK_NAME, text[start:j], start))
        i = j
    return tokens


class _Parser:
    """把 token 流编译为 (投影变量名列表, 模式列表)。"""

    def __init__(self, text: str, tokens: List[_Token]) -> None:
        self._text = text
        self._tokens = tokens
        self._i = 0

    def _peek(self) -> _Token:
        return self._tokens[self._i]

    def _eof(self) -> bool:
        return self._i >= len(self._tokens)

    def _error(self, message: str, pos: int) -> "OntologyError":
        return OntologyError(f"{message}（字符位置 {pos}）")

    def _expect_keyword(self, word: str) -> _Token:
        if self._eof():
            pos = len(self._text)
            raise self._error(f"查询缺少关键字 {word}，或存在未知语句成分", pos)
        tok = self._peek()
        if tok.kind != _TOK_NAME or tok.value != word:
            raise self._error(
                f"查询缺少关键字 {word}，或存在未知语句成分 {tok.value!r}",
                tok.pos,
            )
        self._i += 1
        return tok

    def parse(self) -> Tuple[Tuple[str, ...], List[Tuple[Any, Any, Any]], bool]:
        self._expect_keyword("SELECT")
        projection, star = self._parse_projection()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            pos = self._peek().pos if not self._eof() else len(self._text)
            raise self._error("WHERE 后缺少左花括号 '{'", pos)
        lbrace = self._peek()
        self._i += 1
        patterns = self._parse_pattern_body(lbrace.pos)
        # _parse_pattern_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        self._validate_projection(projection, patterns)
        return tuple(name for name, _ in projection), patterns, star

    def _parse_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        if self._eof():
            raise self._error("SELECT 后缺少投影变量或 '*'", len(self._text))
        if self._peek().kind == _TOK_STAR:
            self._i += 1
            # '*' 后必须紧跟 WHERE
            if self._eof() or self._peek().kind != _TOK_NAME or self._peek().value != "WHERE":
                pos = self._peek().pos if not self._eof() else len(self._text)
                raise self._error("'*' 与 WHERE 之间存在未知语句成分", pos)
            return [], True

        names: List[Tuple[str, int]] = []
        seen = set()
        while not self._eof():
            tok = self._peek()
            if tok.kind == _TOK_NAME and tok.value == "WHERE":
                break
            if tok.kind != _TOK_VAR:
                raise self._error(
                    f"SELECT 后只能出现变量或 '*'，遇到未知语句成分 {tok.value!r}",
                    tok.pos,
                )
            if tok.value in seen:
                raise self._error(
                    f"投影变量 {tok.value} 重复（字符位置 {tok.pos}）",
                    tok.pos,
                )
            seen.add(tok.value)
            names.append((tok.value, tok.pos))
            self._i += 1
        if not names:
            pos = self._peek().pos if not self._eof() else len(self._text)
            raise self._error("SELECT 后缺少投影变量或 '*'", pos)
        return names, False

    def _parse_pattern_body(self, lbrace_pos: int) -> List[Tuple[Any, Any, Any]]:
        patterns: List[Tuple[Any, Any, Any]] = []
        items: List[Tuple[str, str, int]] = []

        def finish_pattern(index: int) -> None:
            if len(items) != 3:
                pos = items[-1][2] if items else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {index} 项数不足：需要主语、谓语、宾语三项"
                    f"（字符位置 {pos}）"
                )
            s, p, o = items
            if p[0] == _VAR:
                raise OntologyError(
                    f"三元组模式 {index} 的谓语不能是变量 {p[1]}"
                    f"（字符位置 {p[2]}）"
                )
            patterns.append((s, p, o))

        pattern_index = 0
        while True:
            if self._eof():
                raise self._error("模式体缺少右花括号 '}'", len(self._text))
            tok = self._peek()
            if tok.kind == _TOK_RBRACE:
                if items:
                    # 花括号前存在未用 '.' 结束的模式
                    finish_pattern(pattern_index)  # 项数不足时在此报错
                    pattern_index += 1
                self._i += 1
                break
            if tok.kind == _TOK_DOT:
                if not items:
                    raise self._error(
                        "点号 '.' 只能出现在一条完整三元组模式之后",
                        tok.pos,
                    )
                finish_pattern(pattern_index)
                pattern_index += 1
                items = []
                self._i += 1
                continue
            if len(items) == 3:
                raise self._error(
                    f"三元组模式 {pattern_index} 项数过多：模式之间需要用 '.' 分隔",
                    tok.pos,
                )
            if tok.kind == _TOK_VAR:
                items.append((_VAR, tok.value, tok.pos))
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                items.append((_LIT, tok.value, tok.pos))
            else:
                raise self._error(
                    f"三元组模式 {pattern_index} 中存在未知语句成分 {tok.value!r}",
                    tok.pos,
                )
            self._i += 1

        if not patterns:
            raise OntologyError(
                f"WHERE 花括号内的模式体为空（字符位置 {lbrace_pos}）"
            )
        return patterns

    def _validate_projection(self, projection, patterns) -> None:
        used = set()
        for s, _p, o in patterns:
            for item in (s, o):
                if item[0] == _VAR:
                    used.add(item[1])
        for name, pos in projection:
            if name not in used:
                raise OntologyError(
                    f"投影变量 {name} 未在任何三元组模式中出现（字符位置 {pos}）"
                )


def _compile_query(text) -> Tuple[Tuple[str, ...], List[Tuple[Any, Any, Any]], bool]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    tokens = _tokenize(text)
    return _Parser(text, tokens).parse()


def _star_variables(patterns) -> Tuple[str, ...]:
    """按模式从左到右、首次出现的顺序收集全部变量（谓语必为常量）。"""
    order: List[str] = []
    seen = set()
    for s, p, o in patterns:
        for item in (s, p, o):
            if item[0] == _VAR and item[1] not in seen:
                seen.add(item[1])
                order.append(item[1])
    return tuple(order)


def run_query(model, text) -> QueryResult:
    """在 model.triples 上执行查询，返回去重并按字典序排序后的 QueryResult。"""
    projection, patterns, star = _compile_query(text)
    if star:
        projection = _star_variables(patterns)

    facts = model.triples
    bindings: List[dict] = [{}]
    for s, p, o in patterns:
        next_bindings: List[dict] = []
        for binding in bindings:
            for fact in facts:  # 同一事实可被多个模式复用
                if fact.predicate != p[1]:
                    continue
                if s[0] == _VAR:
                    if s[1] in binding and fact.subject != binding[s[1]]:
                        continue
                elif fact.subject != s[1]:
                    continue
                extended = dict(binding)
                if s[0] == _VAR:
                    extended[s[1]] = fact.subject
                # 同一模式内主语/宾语可能是同一变量（如 ?x p ?x）
                if o[0] == _VAR:
                    if o[1] in extended and fact.object != extended[o[1]]:
                        continue
                elif fact.object != o[1]:
                    continue
                if o[0] == _VAR:
                    extended[o[1]] = fact.object
                next_bindings.append(extended)
        bindings = next_bindings
        if not bindings:
            break

    rows = {tuple(binding.get(name) for name in projection) for binding in bindings}
    return QueryResult(projection, tuple(sorted(rows)))
