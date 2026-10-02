"""SPARQL 风格基本图模式（BGP）查询，支持 OPTIONAL 左连接、FILTER 筛选与 UNION 并集。

支持的语法（关键字只接受大写）：

    SELECT (变量... | *) WHERE { 模式 ('.' 模式)* '.'? }
    模式 := 三元组模式
          | OPTIONAL { 三元组模式 ('.' 三元组模式)* '.'? }
          | FILTER ( 表达式 )
          | { 分支 } UNION { 分支 } (UNION { 分支 })*

- 变量：'?' 后跟至少一个 Unicode 字母、数字或下划线。
- 常量：不含空白且不以 '?' 开头的名称；或双引号包裹的 JSON 字符串，
  字符串解码后按完整字符串精确比较。
- 三元组模式固定为主语、谓语、宾语三项；谓语必须是常量。
- OPTIONAL 块内可含一个或多个三元组模式，不得嵌套；按左连接处理：
  块内存在匹配时用所有匹配扩展绑定，否则保留原绑定且块内新变量未绑定。
  多个 OPTIONAL 块按出现顺序依次处理。
- FILTER 表达式只支持：
  BOUND(?v)、!BOUND(?v)、?v = ?w、?v != ?w、
  字符串常量与变量或字符串常量的 = / != 比较；
  涉及未绑定变量的等值或不等值比较结果为假；多个 FILTER 按逻辑与过滤。
- UNION 只在 WHERE 主体内连接相邻的花括号分支，连续 UNION 从左到右结合；
  每个分支含一个或多个三元组模式（点号规则与 WHERE 体一致），分支内不得
  再出现 UNION、OPTIONAL 或 FILTER，分支不得为空。求值时各分支分别生成解
  再合并：与外层绑定同名的变量取值必须一致，仅在一侧绑定的变量保留该绑定；
  某分支无匹配时采用其余分支的结果，全部分支都无匹配时结果为空。
  FILTER 在所有分支合并完成后执行，可引用任一分支产生的变量。
- '*' 按模式（含 OPTIONAL 块内模式）从左到右首次出现的顺序投影全部变量；
  投影变量未绑定时结果行中以 None 占位。

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
_TOK_LPAREN = "LPAREN"
_TOK_RPAREN = "RPAREN"
_TOK_DOT = "DOT"
_TOK_STAR = "STAR"
_TOK_BANG = "BANG"
_TOK_EQ = "EQ"
_TOK_NE = "NE"
_TOK_VAR = "VAR"
_TOK_NAME = "NAME"
_TOK_STRING = "STRING"

_DELIMITERS = frozenset('{}."?*')
# FILTER 圆括号内额外识别为独立 token 的字符；括号外这些字符仍是名称的一部分，
# 以保持不含 OPTIONAL/FILTER 的查询词法与旧行为完全一致。
_FILTER_DELIMITERS = frozenset('()!=')

# 模式项：("?", 变量名) 或 ("=", 字面值)
_VAR = "?"
_LIT = "="

# 模式子句类型
_CLAUSE_BGP = "BGP"
_CLAUSE_OPTIONAL = "OPTIONAL"
_CLAUSE_FILTER = "FILTER"
_CLAUSE_UNION = "UNION"

# FILTER 表达式类型
_EXPR_BOUND = "BOUND"
_EXPR_NOT_BOUND = "NOT_BOUND"
_EXPR_EQ = "EQ"
_EXPR_NE = "NE"


class QueryResult:
    """查询结果：投影变量元组 + 等宽不可变行元组（未绑定值为 None）。

    rows 已按投影取值去重并按字典序排序；无匹配时为空元组。
    """

    __slots__ = ("_variables", "_rows")

    def __init__(self, variables: Tuple[str, ...], rows: Tuple[Tuple[Any, ...], ...]) -> None:
        self._variables = tuple(variables)
        self._rows = tuple(tuple(row) for row in rows)

    @property
    def variables(self) -> Tuple[str, ...]:
        return self._variables

    @property
    def rows(self) -> Tuple[Tuple[Any, ...], ...]:
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


def _find_filter_spans(text: str) -> List[Tuple[int, int]]:
    """返回每个 FILTER 表达式外层圆括号的字符区间 [(左括号位置, 右括号位置)]。

    只在 FILTER 关键字（前接起始或空白）后紧跟可选空白与 '(' 时开启区间，
    按嵌套圆括号配平找到对应 ')'；扫描时跳过双引号字符串。区间之外的
    '('、')'、'!'、'=' 都只是普通名称的一部分（旧词法行为）。
    """
    spans: List[Tuple[int, int]] = []
    n = len(text)
    i = 0
    while i < n:
        ch = text[i]
        if ch == '"':
            i = _skip_string(text, i)
            continue
        if ch == "?":
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            i = j
            continue
        if ch.isspace() or ch in _DELIMITERS or ch in _FILTER_DELIMITERS:
            i += 1
            continue
        start = i
        while (
            i < n
            and not text[i].isspace()
            and text[i] not in _DELIMITERS
            and text[i] not in _FILTER_DELIMITERS
        ):
            i += 1
        word = text[start:i]
        # 只有处于模式边界（起始、'{'、'}'、')' 或 '.' 之后，允许中间空白）
        # 的 FILTER 才是子句关键字，否则它只是普通名称的一部分（旧词法兼容）。
        k0 = start
        while k0 > 0 and text[k0 - 1].isspace():
            k0 -= 1
        boundary = k0 == 0 or text[k0 - 1] in "{}.)"
        if word != "FILTER" or not boundary:
            continue
        j = i
        while j < n and text[j].isspace():
            j += 1
        if j >= n or text[j] != "(":
            continue
        lparen = j
        depth = 0
        k = j
        while k < n:
            c = text[k]
            if c == '"':
                k = _skip_string(text, k)
                continue
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if k < n:
            spans.append((lparen, k))
            i = k + 1
        else:
            # 外层圆括号未配平：仍把剩余文本标记为 FILTER 区间，
            # 让解析器在 FILTER 结构处抛出“缺少右圆括号”，而非按三元组误报。
            spans.append((lparen, n - 1))
            i = n
    return spans


def _skip_string(text: str, start: int) -> int:
    """start 指向起始双引号，返回闭合双引号之后的位置（未闭合时到串尾）。"""
    j = start + 1
    n = len(text)
    while j < n:
        if text[j] == "\\":
            j += 2
            continue
        if text[j] == '"':
            return j + 1
        j += 1
    return j


def _tokenize(text: str) -> List[_Token]:
    filter_spans = _find_filter_spans(text)
    span_starts = {start for start, _ in filter_spans}
    span_i = 0

    def in_filter(pos: int) -> bool:
        nonlocal span_i
        while span_i < len(filter_spans) and pos > filter_spans[span_i][1]:
            span_i += 1
        return span_i < len(filter_spans) and filter_spans[span_i][0] <= pos <= filter_spans[span_i][1]

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
        if in_filter(i):
            if ch == "(":
                tokens.append(_Token(_TOK_LPAREN, ch, i))
                i += 1
                continue
            if ch == ")":
                tokens.append(_Token(_TOK_RPAREN, ch, i))
                i += 1
                continue
            if ch == "!":
                if i + 1 < n and text[i + 1] == "=":
                    tokens.append(_Token(_TOK_NE, "!=", i))
                    i += 2
                else:
                    tokens.append(_Token(_TOK_BANG, "!", i))
                    i += 1
                continue
            if ch == "=":
                tokens.append(_Token(_TOK_EQ, "=", i))
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
        # 普通名称：FILTER 区间内额外以 ()!= 为界，区间外它们仍是名称字符
        delimiters = _DELIMITERS | _FILTER_DELIMITERS if in_filter(i) else _DELIMITERS
        start = i
        j = i
        while j < n and not text[j].isspace() and text[j] not in delimiters:
            # 'FILTER(' 无空格时，左括号是独立区间起点，名称只取到 FILTER
            if j > start and j in span_starts:
                break
            j += 1
        tokens.append(_Token(_TOK_NAME, text[start:j], start))
        i = j
    return tokens


class _Parser:
    """把 token 流编译为 (投影变量名列表, 模式子句列表, 是否星号投影)。

    模式子句为三元组：
    - (_CLAUSE_BGP, patterns, None)
    - (_CLAUSE_OPTIONAL, patterns, None)
    - (_CLAUSE_FILTER, None, 表达式元组)
    - (_CLAUSE_UNION, branches, None)：branches 为分支列表，
      每个分支是一组三元组模式
    每个三元组模式为 ((种类, 值), (种类, 值), (种类, 值))，谓语恒为字面值。
    模式序号在整个 WHERE 体内连续编号，BGP、OPTIONAL 与 UNION 分支中的
    三元组一并计数；FILTER 不占用三元组序号。
    """

    def __init__(self, text: str, tokens: List[_Token]) -> None:
        self._text = text
        self._tokens = tokens
        self._i = 0
        # 三元组模式序号在整个 WHERE 体内连续编号（含 OPTIONAL 块内模式）
        self._pattern_index = 0

    def _peek(self) -> _Token:
        return self._tokens[self._i]

    def _eof(self) -> bool:
        return self._i >= len(self._tokens)

    def _error(self, message: str, pos: int) -> "OntologyError":
        return OntologyError(f"{message}（字符位置 {pos}）")

    def _here_pos(self) -> int:
        return self._peek().pos if not self._eof() else len(self._text)

    def _next_is(self, kind: str) -> bool:
        return self._i + 1 < len(self._tokens) and self._tokens[self._i + 1].kind == kind

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

    def parse(self) -> Tuple[Tuple[str, ...], List[tuple], bool]:
        self._expect_keyword("SELECT")
        projection, star = self._parse_projection()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._i += 1
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        self._validate_projection(projection, clauses)
        return tuple(name for name, _ in projection), clauses, star

    def _parse_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        if self._eof():
            raise self._error("SELECT 后缺少投影变量或 '*'", len(self._text))
        if self._peek().kind == _TOK_STAR:
            self._i += 1
            # '*' 后必须紧跟 WHERE
            if self._eof() or self._peek().kind != _TOK_NAME or self._peek().value != "WHERE":
                raise self._error("'*' 与 WHERE 之间存在未知语句成分", self._here_pos())
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
            raise self._error("SELECT 后缺少投影变量或 '*'", self._here_pos())
        return names, False

    def _parse_group_body(self, optional: bool, union_branch: bool = False, branch_no: int = 0) -> List[tuple]:
        """解析 '{' 之后直到匹配 '}' 的模式组，返回子句列表（已消费 '}'）。

        union_branch 为 True 时按 UNION 分支解析：只允许三元组模式，
        不得再出现 UNION、OPTIONAL 或 FILTER；branch_no 为分支序号（从 1 起）。
        """
        if union_branch:
            scope = f"UNION 第 {branch_no} 分支"
        else:
            scope = "OPTIONAL 块" if optional else "WHERE 模式体"
        clauses: List[tuple] = []
        pending: List[Tuple[Any, Any, Any]] = []
        # prev_dot：上一个 token 是否为点号（拒绝连续点号）；
        # clause_ok：当前位置是否允许出现 OPTIONAL/FILTER 子句
        # （组首、点号之后或前一子句之后）。
        prev_dot = False
        clause_ok = True
        lbrace_pos = self._tokens[self._i - 1].pos

        def finish_pattern() -> None:
            if len(pending) != 3:
                pos = pending[-1][2] if pending else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            if p[0] == _VAR:
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 的谓语不能是变量 {p[1]}"
                    f"（字符位置 {p[2]}）"
                )
            clauses.append((_CLAUSE_BGP, [(s, p, o)], None))
            pending.clear()
            self._pattern_index += 1

        while True:
            if self._eof():
                raise self._error(f"{scope}缺少右花括号 '}}'", len(self._text))
            tok = self._peek()

            if tok.kind == _TOK_RBRACE:
                if pending:
                    finish_pattern()
                self._i += 1
                break

            if tok.kind == _TOK_DOT:
                if pending:
                    finish_pattern()
                elif prev_dot or not clauses:
                    raise self._error(
                        "点号 '.' 只能出现在一条完整三元组模式或子句之后",
                        tok.pos,
                    )
                prev_dot = True
                clause_ok = True
                self._i += 1
                continue

            # 子句关键字只在模式边界成立；未跟起始符号的关键字按普通常量处理。
            if union_branch:
                # UNION 分支内只允许三元组模式
                if tok.kind == _TOK_LBRACE:
                    raise self._error(
                        f"UNION 第 {branch_no} 分支内不得嵌套花括号组或 UNION",
                        tok.pos,
                    )
                if clause_ok and tok.kind == _TOK_NAME and self._next_is(_TOK_LBRACE) and tok.value == "UNION":
                    raise self._error(
                        f"UNION 第 {branch_no} 分支内不得再嵌套 UNION",
                        tok.pos,
                    )
                if clause_ok and tok.kind == _TOK_NAME and self._next_is(_TOK_LBRACE) and tok.value == "OPTIONAL":
                    raise self._error(
                        f"UNION 第 {branch_no} 分支内只允许三元组模式，不得使用 OPTIONAL",
                        tok.pos,
                    )
                if clause_ok and tok.kind == _TOK_NAME and self._next_is(_TOK_LPAREN) and tok.value == "FILTER":
                    raise self._error(
                        f"UNION 第 {branch_no} 分支内只允许三元组模式，不得使用 FILTER",
                        tok.pos,
                    )

            if not optional and not union_branch and clause_ok and tok.kind == _TOK_LBRACE:
                # WHERE 主体内的花括号组：必须是 UNION 子句的左分支
                clauses.append(self._parse_union_clause())
                prev_dot = False
                clause_ok = True
                continue

            if (
                not optional
                and not union_branch
                and clause_ok
                and tok.kind == _TOK_NAME
                and tok.value == "UNION"
                and self._next_is(_TOK_LBRACE)
            ):
                raise self._error(
                    "UNION 两侧必须是独立的花括号组：左侧缺少 '{' 分组",
                    tok.pos,
                )

            if optional and clause_ok and tok.kind == _TOK_NAME and tok.value == "UNION" and self._next_is(_TOK_LBRACE):
                raise self._error(
                    "OPTIONAL 块内只允许出现三元组模式，不得使用 UNION",
                    tok.pos,
                )

            if clause_ok and tok.kind == _TOK_NAME and tok.value == "OPTIONAL" and self._next_is(_TOK_LBRACE):
                if optional:
                    raise self._error(
                        "OPTIONAL 块内不得再嵌套 OPTIONAL 块",
                        tok.pos,
                    )
                clauses.append(self._parse_optional_clause())
                prev_dot = False
                clause_ok = True
                continue

            if clause_ok and tok.kind == _TOK_NAME and tok.value == "FILTER" and self._next_is(_TOK_LPAREN):
                if optional:
                    raise self._error(
                        "OPTIONAL 块内只允许出现三元组模式，不得使用 FILTER",
                        tok.pos,
                    )
                clauses.append(self._parse_filter_clause())
                prev_dot = False
                clause_ok = True
                continue

            if len(pending) == 3:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 项数过多："
                    f"模式之间需要用 '.' 分隔",
                    tok.pos,
                )
            if tok.kind == _TOK_VAR:
                pending.append((_VAR, tok.value, tok.pos))
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                pending.append((_LIT, tok.value, tok.pos))
            else:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 中存在未知语句成分"
                    f" {tok.value!r}",
                    tok.pos,
                )
            prev_dot = False
            clause_ok = False
            self._i += 1

        if not any(kind != _CLAUSE_FILTER for kind, _, _ in clauses):
            if union_branch:
                raise OntologyError(
                    f"UNION 第 {branch_no} 分支为空或不含三元组模式"
                    f"（字符位置 {lbrace_pos}）"
                )
            raise OntologyError(
                f"{'OPTIONAL' if optional else 'WHERE'} 花括号内的模式组为空"
                f"（字符位置 {lbrace_pos}）"
            )
        return clauses

    def _parse_union_clause(self) -> tuple:
        """解析 '{ ... } (UNION { ... })+'，当前 token 为首个 '{'。"""
        branches = [self._parse_union_branch(1)]
        # 花括号组后必须紧跟 UNION，否则不是合法的 UNION 子句
        if (
            self._eof()
            or self._peek().kind != _TOK_NAME
            or self._peek().value != "UNION"
        ):
            raise self._error(
                "花括号组后缺少 UNION 关键字：WHERE 内的花括号组必须构成"
                " '{ ... } UNION { ... }'",
                self._here_pos(),
            )
        branch_no = 1
        while (
            not self._eof()
            and self._peek().kind == _TOK_NAME
            and self._peek().value == "UNION"
        ):
            self._i += 1
            if self._eof() or self._peek().kind != _TOK_LBRACE:
                raise self._error(
                    "UNION 后缺少左花括号 '{'：两侧必须是独立的花括号组",
                    self._here_pos(),
                )
            branch_no += 1
            branches.append(self._parse_union_branch(branch_no))
        return (_CLAUSE_UNION, branches, None)

    def _parse_union_branch(self, branch_no: int) -> list:
        """解析 UNION 的单个分支 '{ ... }'，当前 token 为 '{'，返回三元组模式列表。"""
        self._i += 1
        inner = self._parse_group_body(
            optional=False, union_branch=True, branch_no=branch_no
        )
        patterns: List[Tuple[Any, Any, Any]] = []
        for kind, group, _ in inner:
            if kind != _CLAUSE_BGP:  # pragma: no cover - 解析器已拒绝分支内子句
                raise OntologyError(
                    f"UNION 第 {branch_no} 分支内只允许出现三元组模式"
                )
            patterns.extend(group)
        return patterns

    def _parse_optional_clause(self) -> tuple:
        """解析 OPTIONAL { ... }，关键字为当前 token。"""
        kw = self._peek()
        self._i += 1
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error(
                "OPTIONAL 后缺少左花括号 '{'",
                self._here_pos(),
            )
        self._i += 1
        inner = self._parse_group_body(optional=True)
        patterns: List[Tuple[Any, Any, Any]] = []
        for kind, group, _ in inner:
            if kind != _CLAUSE_BGP:  # pragma: no cover - 解析器已拒绝嵌套分组
                raise self._error(
                    "OPTIONAL 块内只允许出现三元组模式，不得嵌套",
                    kw.pos,
                )
            patterns.extend(group)
        return (_CLAUSE_OPTIONAL, patterns, None)

    def _parse_filter_clause(self) -> tuple:
        """解析 FILTER ( 表达式 )，关键字为当前 token。"""
        self._i += 1
        if self._eof() or self._peek().kind != _TOK_LPAREN:
            raise self._error(
                "FILTER 后缺少左圆括号 '('",
                self._here_pos(),
            )
        lparen = self._peek()
        self._i += 1
        expr = self._parse_filter_expr(lparen.pos)
        if self._eof() or self._peek().kind != _TOK_RPAREN:
            raise self._error(
                "FILTER 表达式缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._i += 1
        return (_CLAUSE_FILTER, None, expr)

    def _parse_filter_expr(self, lparen_pos: int) -> tuple:
        """解析圆括号内的单个表达式，'(' 与 ')' 已由调用方消费。"""
        if self._eof():
            raise OntologyError(
                f"FILTER 表达式为空（字符位置 {lparen_pos}）"
            )
        tok = self._peek()

        # !BOUND(?v)
        if tok.kind == _TOK_BANG:
            bang = tok
            self._i += 1
            if (
                self._eof()
                or self._peek().kind != _TOK_NAME
                or self._peek().value != "BOUND"
            ):
                raise self._error(
                    "FILTER 中 '!' 后只允许 BOUND(?v) 形式",
                    bang.pos,
                )
            bound_tok = self._peek()
            self._i += 1
            var_tok = self._expect_bound_variable(bound_tok.pos)
            self._expect_expr_end(bound_tok.pos)
            return (_EXPR_NOT_BOUND, var_tok.value)

        # BOUND(?v)
        if tok.kind == _TOK_NAME and tok.value == "BOUND":
            self._i += 1
            var_tok = self._expect_bound_variable(tok.pos)
            self._expect_expr_end(tok.pos)
            return (_EXPR_BOUND, var_tok.value)

        # 左操作数：变量或字符串常量（普通名称不是合法表达式）
        if tok.kind == _TOK_VAR:
            left = (_VAR, tok.value, tok.pos)
            self._i += 1
        elif tok.kind == _TOK_STRING:
            left = (_LIT, tok.value, tok.pos)
            self._i += 1
        else:
            raise self._error(
                f"未知 FILTER 表达式形式：{tok.value!r} 不是合法的表达式开头",
                tok.pos,
            )

        if self._eof() or self._peek().kind not in (_TOK_EQ, _TOK_NE):
            raise self._error(
                "FILTER 比较表达式需要 '=' 或 '!=' 运算符",
                self._here_pos(),
            )
        op_tok = self._peek()
        self._i += 1

        if self._eof():
            raise self._error(
                f"FILTER 表达式在运算符 {op_tok.value!r} 后缺少右操作数"
                f"（字符位置 {op_tok.pos}）",
                op_tok.pos,
            )
        right_tok = self._peek()
        if right_tok.kind == _TOK_VAR:
            right = (_VAR, right_tok.value, right_tok.pos)
        elif right_tok.kind == _TOK_STRING:
            right = (_LIT, right_tok.value, right_tok.pos)
        else:
            raise self._error(
                f"FILTER 中 {op_tok.value!r} 的右操作数只能是变量或字符串常量，"
                f"遇到 {right_tok.value!r}",
                right_tok.pos,
            )
        self._i += 1
        self._expect_expr_end(right_tok.pos)
        kind = _EXPR_EQ if op_tok.kind == _TOK_EQ else _EXPR_NE
        return (kind, left, right)

    def _expect_bound_variable(self, bound_pos: int) -> _Token:
        """消费 BOUND 后面的 '(' 变量 ')'，返回变量 token。"""
        if self._eof() or self._peek().kind != _TOK_LPAREN:
            raise self._error(
                "BOUND 后缺少左圆括号 '('",
                self._here_pos(),
            )
        self._i += 1
        if self._eof() or self._peek().kind != _TOK_VAR:
            raise self._error(
                "BOUND(...) 中必须且只能出现一个变量",
                self._here_pos(),
            )
        var_tok = self._peek()
        self._i += 1
        if self._eof() or self._peek().kind != _TOK_RPAREN:
            raise self._error(
                f"BOUND({var_tok.value} 后缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._i += 1
        return var_tok

    def _expect_expr_end(self, anchor_pos: int) -> None:
        """表达式解析后，下一个 token 必须是外层 FILTER 的右圆括号。"""
        if self._eof():
            raise OntologyError(
                f"FILTER 表达式不完整，缺少右圆括号 ')'（字符位置 {anchor_pos}）"
            )
        tok = self._peek()
        if tok.kind != _TOK_RPAREN:
            raise self._error(
                f"未知 FILTER 表达式形式：{tok.value!r} 之后存在多余成分",
                tok.pos,
            )

    def _validate_projection(self, projection, clauses) -> None:
        used = set()
        for s, _p, o in _all_patterns(clauses):
            for item in (s, o):
                if item[0] == _VAR:
                    used.add(item[1])
        for name, pos in projection:
            if name not in used:
                raise OntologyError(
                    f"投影变量 {name} 未在任何三元组模式中出现（字符位置 {pos}）"
                )


def _compile_query(text) -> Tuple[Tuple[str, ...], List[tuple], bool]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    tokens = _tokenize(text)
    return _Parser(text, tokens).parse()


def _all_patterns(clauses) -> List[Tuple[Any, Any, Any]]:
    """按子句顺序取出全部三元组模式（含 OPTIONAL 块与 UNION 分支内模式）。"""
    patterns: List[Tuple[Any, Any, Any]] = []
    for kind, group, _ in clauses:
        if kind == _CLAUSE_FILTER:
            continue
        if kind == _CLAUSE_UNION:
            for branch in group:
                patterns.extend(branch)
        else:
            patterns.extend(group)
    return patterns


def _star_variables(clauses) -> Tuple[str, ...]:
    """按模式从左到右、首次出现的顺序收集全部变量（谓语必为常量）。"""
    order: List[str] = []
    seen = set()
    for s, p, o in _all_patterns(clauses):
        for item in (s, p, o):
            if item[0] == _VAR and item[1] not in seen:
                seen.add(item[1])
                order.append(item[1])
    return tuple(order)


def _match_patterns(facts, bindings: List[dict], patterns) -> List[dict]:
    """对一组三元组模式按序做内连接，返回所有扩展后的绑定。"""
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
    return bindings


def _eval_filter(expr: tuple, binding: dict) -> bool:
    kind = expr[0]
    if kind == _EXPR_BOUND:
        return expr[1] in binding
    if kind == _EXPR_NOT_BOUND:
        return expr[1] not in binding

    def value_of(operand) -> Any:
        tag, name, _pos = operand
        if tag == _LIT:
            return name
        # 变量未绑定时等值/不等值比较均为假
        return binding.get(name)

    left = value_of(expr[1])
    right = value_of(expr[2])
    if left is None or right is None:
        return False
    equal = left == right
    return equal if kind == _EXPR_EQ else not equal


def run_query(model, text) -> QueryResult:
    """在 model.triples 上执行查询，返回去重并按字典序排序后的 QueryResult。"""
    projection, clauses, star = _compile_query(text)
    if star:
        projection = _star_variables(clauses)

    facts = model.triples
    bindings: List[dict] = [{}]
    for kind, patterns, expr in clauses:
        if kind == _CLAUSE_FILTER:
            bindings = [b for b in bindings if _eval_filter(expr, b)]
        elif kind == _CLAUSE_UNION:
            # 各分支分别从当前绑定出发生成解，再合并（并集）：
            # 与外层绑定同名的变量由 _match_patterns 保证取值一致；
            # 某分支无匹配时仅采用其余分支的结果。
            joined: List[dict] = []
            for binding in bindings:
                for branch in patterns:
                    joined.extend(_match_patterns(facts, [dict(binding)], branch))
            bindings = joined
        elif kind == _CLAUSE_OPTIONAL:
            joined: List[dict] = []
            for binding in bindings:
                matches = _match_patterns(facts, [dict(binding)], patterns)
                if matches:
                    joined.extend(matches)
                else:
                    # 无匹配：保留原绑定一次，块内新变量保持未绑定
                    joined.append(binding)
            bindings = joined
        else:
            bindings = _match_patterns(facts, bindings, patterns)
        if not bindings:
            break

    rows = {tuple(binding.get(name) for name in projection) for binding in bindings}
    # None（未绑定）排在所有字符串之前，保证混合取值时字典序排序稳定。
    ordered = tuple(
        sorted(
            rows,
            key=lambda row: tuple((0, "") if value is None else (1, value) for value in row),
        )
    )
    return QueryResult(projection, ordered)
