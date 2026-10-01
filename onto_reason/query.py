"""SPARQL 风格基本图模式（BGP）查询。

支持的语法（关键字只接受大写）：

    SELECT (变量... | *) WHERE {
        三元组模式 ('.' 三元组模式)* '.'?
        ( (OPTIONAL { 三元组模式 ('.' 三元组模式)* '.'? }
           | FILTER ( 表达式 )) '.'? )*
    }

- 变量：'?' 后跟至少一个 Unicode 字母、数字或下划线。
- 常量：不含空白且不以 '?' 开头的名称；或双引号包裹的 JSON 字符串，
  字符串解码后按完整字符串精确比较。
- 三元组模式固定为主语、谓语、宾语三项；谓语必须是常量。
- '*' 按变量在必需模式、再到各 OPTIONAL 模式中首次出现的顺序投影全部变量。
- OPTIONAL 块内可含一个或多个三元组模式，不得嵌套，按左连接求值：
  块内无匹配时保留原绑定一次，块内新变量保持未绑定。
- FILTER 表达式支持：BOUND(?v)、!BOUND(?v)、?v = ?w、?v != ?w，
  以及双引号字符串常量与变量或字符串常量的 = / != 比较；
  比较按完整字符串精确判断，任一操作数未绑定时等值与不等值均为假；
  多个 FILTER 按逻辑与共同过滤结果。

查询在 OntologyModel.triples（显式 + 推理三元组）上做嵌套循环连接匹配，
同一变量跨模式绑定同一字符串，一条事实可被多个模式复用。未绑定的投影
变量在结果行中以 None 占位。任何词法或语法错误都抛出带字符位置或模式
序号的 OntologyError；正常求值产生的未绑定值与假条件不视为异常。
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
_TOK_LPAREN = "LPAREN"
_TOK_RPAREN = "RPAREN"
_TOK_BANG = "BANG"
_TOK_EQ = "EQ"
_TOK_NE = "NE"

_DELIMITERS = frozenset('{}."?*()!=')

# 模式项：("?", 变量名, 位置) 或 ("=", 字面值, 位置)
_VAR = "?"
_LIT = "="

# 绑定字典中“未绑定”的内部哨兵（三元组取值都是非 None 字符串）
_UNBOUND = object()

# FILTER 表达式 AST：
#   ("bound", 变量名)
#   ("not", 子表达式)
#   ("cmp", "=" | "!=", 左操作数, 右操作数)
# 操作数：("var", 变量名) 或 ("str", 字符串值)


class QueryResult:
    """查询结果：投影变量元组 + 等宽不可变字符串行元组。

    rows 已按投影取值去重并按字典序排序（未绑定占位 None 排在字符串之前，
    与 SPARQL ASC 排序一致）；无匹配时为空元组。
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
        # 普通名称：消费到分隔符或空白为止
        start = i
        j = i
        while j < n and not text[j].isspace() and text[j] not in _DELIMITERS:
            j += 1
        tokens.append(_Token(_TOK_NAME, text[start:j], start))
        i = j
    return tokens


_CLAUSE_KEYWORDS = frozenset(("OPTIONAL", "FILTER"))


class _Parser:
    """把 token 流编译为 (投影变量名列表, 必需模式, 子句列表, 是否星号)。"""

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

    def _here_pos(self) -> int:
        return self._peek().pos if not self._eof() else len(self._text)

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

    def parse(self):
        self._expect_keyword("SELECT")
        projection, star = self._parse_projection()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            pos = self._here_pos()
            raise self._error("WHERE 后缺少左花括号 '{'", pos)
        lbrace_pos = self._peek().pos
        self._i += 1
        patterns, clauses = self._parse_where_body(lbrace_pos)
        # _parse_where_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        self._validate_projection(projection, patterns, clauses)
        return tuple(name for name, _ in projection), patterns, clauses, star

    def _parse_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        if self._eof():
            raise self._error("SELECT 后缺少投影变量或 '*'", len(self._text))
        if self._peek().kind == _TOK_STAR:
            self._i += 1
            # '*' 后必须紧跟 WHERE
            if self._eof() or self._peek().kind != _TOK_NAME or self._peek().value != "WHERE":
                pos = self._here_pos()
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
            pos = self._here_pos()
            raise self._error("SELECT 后缺少投影变量或 '*'", pos)
        return names, False

    # ---- WHERE 体与三元组模式组 ---------------------------------------

    def _finish_group_pattern(self, patterns, items, index, lbrace_pos, label) -> None:
        """把缓冲的至多三个项收结成一条三元组模式，非法时按模式序号报错。"""
        if len(items) != 3:
            pos = items[-1][2] if items else lbrace_pos
            raise OntologyError(
                f"{label}的三元组模式 {index} 项数不足：需要主语、谓语、宾语三项"
                f"（字符位置 {pos}）"
            )
        s, p, o = items
        if p[0] == _VAR:
            raise OntologyError(
                f"{label}的三元组模式 {index} 的谓语不能是变量 {p[1]}"
                f"（字符位置 {p[2]}）"
            )
        patterns.append((s, p, o))

    def _parse_where_body(self, lbrace_pos: int):
        """解析 WHERE '{' 之后的模式体（起始 '{' 已消费），消费结束 '}'。"""
        label = "WHERE 花括号内"
        patterns: List[Tuple[Any, Any, Any]] = []
        clauses: List[Tuple[str, Any]] = []
        items: List[Tuple[str, str, int]] = []
        pattern_index = 0
        optional_index = 0
        in_clauses = False
        separator_seen = False

        while True:
            if self._eof():
                raise self._error("模式体缺少右花括号 '}'", len(self._text))
            tok = self._peek()

            if tok.kind == _TOK_RBRACE:
                if items:
                    self._finish_group_pattern(
                        patterns, items, pattern_index, lbrace_pos, label
                    )
                self._i += 1
                break

            if in_clauses:
                # 子句阶段：只允许 OPTIONAL / FILTER / 分隔点号 / '}'
                if tok.kind == _TOK_DOT:
                    if separator_seen:
                        raise self._error(
                            "点号 '.' 只能作为 OPTIONAL/FILTER 子句之间的分隔符，"
                            "不能连续出现",
                            tok.pos,
                        )
                    separator_seen = True
                    self._i += 1
                    continue
                if tok.kind == _TOK_NAME and tok.value == "OPTIONAL":
                    clauses.append(("optional", self._parse_optional(optional_index)))
                    optional_index += 1
                    separator_seen = False
                    continue
                if tok.kind == _TOK_NAME and tok.value == "FILTER":
                    clauses.append(("filter", self._parse_filter()))
                    separator_seen = False
                    continue
                raise self._error(
                    f"OPTIONAL/FILTER 之后只能出现 OPTIONAL、FILTER 或右花括号 '}}'，"
                    f"遇到未知语句成分 {tok.value!r}",
                    tok.pos,
                )

            if tok.kind == _TOK_DOT:
                if not items:
                    raise self._error(
                        "点号 '.' 只能出现在一条完整三元组模式之后",
                        tok.pos,
                    )
                self._finish_group_pattern(
                    patterns, items, pattern_index, lbrace_pos, label
                )
                pattern_index += 1
                items = []
                self._i += 1
                continue

            if tok.kind == _TOK_NAME and tok.value in _CLAUSE_KEYWORDS:
                if items:
                    # 子句关键字直接跟在完整三元组后（省略分隔点号）
                    self._finish_group_pattern(
                        patterns, items, pattern_index, lbrace_pos, label
                    )
                    pattern_index += 1
                    items = []
                if not patterns:
                    raise self._error(
                        f"{tok.value} 之前至少需要一个必需三元组模式",
                        tok.pos,
                    )
                in_clauses = True
                if tok.value == "OPTIONAL":
                    clauses.append(("optional", self._parse_optional(optional_index)))
                    optional_index += 1
                else:
                    clauses.append(("filter", self._parse_filter()))
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
        return patterns, clauses

    def _parse_optional(self, optional_index: int):
        """解析 OPTIONAL { ... }（入口 token 为 NAME(OPTIONAL)），返回模式列表。"""
        self._i += 1
        label = f"OPTIONAL 块 {optional_index}"
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error(f"{label} 后缺少左花括号 '{{'", self._here_pos())
        lbrace_pos = self._peek().pos
        self._i += 1
        return self._parse_triple_group(lbrace_pos, label)

    def _parse_triple_group(self, lbrace_pos: int, label: str):
        """解析一个花括号内的三元组模式组（起始 '{' 已消费），消费结束 '}'。"""
        patterns: List[Tuple[Any, Any, Any]] = []
        items: List[Tuple[str, str, int]] = []
        pattern_index = 0

        while True:
            if self._eof():
                raise self._error(f"{label}缺少右花括号 '}}'", len(self._text))
            tok = self._peek()
            if tok.kind == _TOK_RBRACE:
                if items:
                    self._finish_group_pattern(
                        patterns, items, pattern_index, lbrace_pos, label
                    )
                self._i += 1
                break
            if tok.kind == _TOK_DOT:
                if not items:
                    raise self._error(
                        f"点号 '.' 只能出现在{label}中一条完整三元组模式之后",
                        tok.pos,
                    )
                self._finish_group_pattern(
                    patterns, items, pattern_index, lbrace_pos, label
                )
                pattern_index += 1
                items = []
                self._i += 1
                continue
            if tok.kind == _TOK_NAME and tok.value in _CLAUSE_KEYWORDS:
                raise self._error(
                    f"{label}内不得嵌套 OPTIONAL 或 FILTER",
                    tok.pos,
                )
            if tok.kind == _TOK_LBRACE:
                raise self._error(
                    f"{label}内不得嵌套花括号分组",
                    tok.pos,
                )
            if len(items) == 3:
                raise self._error(
                    f"{label}的三元组模式 {pattern_index} 项数过多："
                    f"模式之间需要用 '.' 分隔",
                    tok.pos,
                )
            if tok.kind == _TOK_VAR:
                items.append((_VAR, tok.value, tok.pos))
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                items.append((_LIT, tok.value, tok.pos))
            else:
                raise self._error(
                    f"{label}的三元组模式 {pattern_index} 中存在未知语句成分"
                    f" {tok.value!r}",
                    tok.pos,
                )
            self._i += 1

        if not patterns:
            raise OntologyError(
                f"{label}内的三元组模式组为空（字符位置 {lbrace_pos}）"
            )
        return patterns

    # ---- FILTER 表达式 -------------------------------------------------

    def _parse_filter(self):
        """解析 FILTER ( 表达式 )（入口 token 为 NAME(FILTER)），返回表达式 AST。"""
        self._i += 1
        if self._eof() or self._peek().kind != _TOK_LPAREN:
            raise self._error("FILTER 后缺少左括号 '('", self._here_pos())
        lp_pos = self._peek().pos
        self._i += 1
        expr = self._parse_filter_expr(lp_pos)
        if self._eof() or self._peek().kind != _TOK_RPAREN:
            raise self._error(
                "FILTER 表达式缺少右括号 ')'，或括号后存在多余成分",
                self._here_pos(),
            )
        self._i += 1
        return expr

    def _parse_filter_expr(self, lp_pos: int):
        if self._eof():
            raise OntologyError(f"FILTER 表达式为空（字符位置 {lp_pos}）")

        tok = self._peek()
        if tok.kind == _TOK_RPAREN:
            raise OntologyError(f"FILTER 表达式为空（字符位置 {lp_pos}）")
        negated = False
        if tok.kind == _TOK_BANG:
            negated = True
            bang_pos = tok.pos
            self._i += 1
            if self._eof():
                raise self._error("'!' 后缺少表达式 BOUND(?v)", len(self._text))
            tok = self._peek()

        if tok.kind == _TOK_NAME and tok.value == "BOUND":
            bound_pos = tok.pos
            self._i += 1
            if self._eof() or self._peek().kind != _TOK_LPAREN:
                raise self._error("BOUND 后缺少左括号 '('", self._here_pos())
            self._i += 1
            if self._eof() or self._peek().kind != _TOK_VAR:
                raise self._error(
                    "BOUND() 内必须是且只能是一个变量",
                    self._here_pos(),
                )
            var_tok = self._peek()
            self._i += 1
            if self._eof() or self._peek().kind != _TOK_RPAREN:
                raise self._error(
                    "BOUND() 缺少右括号 ')'，或括号内存在多余成分",
                    self._here_pos(),
                )
            self._i += 1
            expr = ("bound", var_tok.value, var_tok.pos, bound_pos)
            if negated:
                expr = ("not", expr)
            return expr

        if negated:
            raise self._error(
                "'!' 只能出现在 BOUND(?v) 之前",
                bang_pos,
            )

        # 等值比较：操作数 ('=' | '!=') 操作数
        left = self._parse_filter_operand()
        if self._eof() or self._peek().kind not in (_TOK_EQ, _TOK_NE):
            raise self._error(
                "FILTER 表达式缺少比较运算符 '=' 或 '!='，或表达式形式未知",
                self._here_pos(),
            )
        op_tok = self._peek()
        self._i += 1
        op = "=" if op_tok.kind == _TOK_EQ else "!="
        right = self._parse_filter_operand()
        return ("cmp", op, left, right, op_tok.pos)

    def _parse_filter_operand(self):
        if self._eof():
            raise self._error(
                "FILTER 表达式缺少变量或双引号字符串操作数",
                len(self._text),
            )
        tok = self._peek()
        if tok.kind == _TOK_VAR:
            self._i += 1
            return ("var", tok.value, tok.pos)
        if tok.kind == _TOK_STRING:
            self._i += 1
            return ("str", tok.value, tok.pos)
        raise self._error(
            f"FILTER 表达式中存在未知操作数 {tok.value!r}："
            f"只允许变量或双引号字符串常量",
            tok.pos,
        )

    # ---- 校验 -----------------------------------------------------------

    def _validate_projection(self, projection, patterns, clauses) -> None:
        used = set()
        for s, _p, o in patterns:
            for item in (s, o):
                if item[0] == _VAR:
                    used.add(item[1])
        filter_vars: List[Tuple[str, int]] = []
        for kind, payload in clauses:
            if kind == "optional":
                for s, _p, o in payload:
                    for item in (s, o):
                        if item[0] == _VAR:
                            used.add(item[1])
            else:
                self._collect_filter_vars(payload, filter_vars)
        for name, pos in projection:
            if name not in used:
                raise OntologyError(
                    f"投影变量 {name} 未在任何三元组模式中出现（字符位置 {pos}）"
                )
        for name, pos in filter_vars:
            if name not in used:
                raise OntologyError(
                    f"FILTER 中的变量 {name} 未在任何三元组模式中出现"
                    f"（字符位置 {pos}）"
                )

    def _collect_filter_vars(self, expr, out) -> None:
        tag = expr[0]
        if tag == "bound":
            out.append((expr[1], expr[2]))
        elif tag == "not":
            self._collect_filter_vars(expr[1], out)
        else:
            for operand in (expr[2], expr[3]):
                if operand[0] == "var":
                    out.append((operand[1], operand[2]))


def _compile_query(text):
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    tokens = _tokenize(text)
    return _Parser(text, tokens).parse()


def _star_variables(patterns, clauses) -> Tuple[str, ...]:
    """按变量在必需模式、再到各 OPTIONAL 模式中首次出现的顺序收集。"""
    order: List[str] = []
    seen = set()

    def note(item) -> None:
        if item[0] == _VAR and item[1] not in seen:
            seen.add(item[1])
            order.append(item[1])

    for s, p, o in patterns:
        for item in (s, p, o):
            note(item)
    for kind, payload in clauses:
        if kind != "optional":
            continue
        for s, p, o in payload:
            for item in (s, p, o):
                note(item)
    return tuple(order)


def _try_extend(binding, s, p, o, fact):
    """事实与单个三元组模式匹配时返回扩展后的新绑定字典，否则返回 None。"""
    if fact.predicate != p[1]:
        return None
    extended = dict(binding)
    if s[0] == _VAR:
        current = extended.get(s[1], _UNBOUND)
        if current is not _UNBOUND and fact.subject != current:
            return None
        extended[s[1]] = fact.subject
    elif fact.subject != s[1]:
        return None
    # 同一模式内主语/宾语可能是同一变量（如 ?x p ?x）
    if o[0] == _VAR:
        current = extended.get(o[1], _UNBOUND)
        if current is not _UNBOUND and fact.object != current:
            return None
        extended[o[1]] = fact.object
    elif fact.object != o[1]:
        return None
    return extended


def _match_group(group_patterns, seeds, facts):
    """从种子绑定集合出发，顺序匹配一组三元组模式，返回扩展后的绑定列表。"""
    bindings = list(seeds)
    for s, p, o in group_patterns:
        next_bindings: List[dict] = []
        for binding in bindings:
            for fact in facts:  # 同一事实可被多个模式复用
                extended = _try_extend(binding, s, p, o, fact)
                if extended is not None:
                    next_bindings.append(extended)
        bindings = next_bindings
        if not bindings:
            break
    return bindings


def _operand_value(operand, binding):
    if operand[0] == "var":
        return binding.get(operand[1])  # 未绑定 → None（事实取值永不为 None）
    return operand[1]


def _eval_filter(expr, binding) -> bool:
    tag = expr[0]
    if tag == "bound":
        return expr[1] in binding
    if tag == "not":
        return not _eval_filter(expr[1], binding)
    _, op, left, right, _pos = expr
    lv = _operand_value(left, binding)
    rv = _operand_value(right, binding)
    # 任一操作数未绑定：等值与不等值均为假
    if lv is None or rv is None:
        return False
    if op == "=":
        return lv == rv
    return lv != rv


def _row_sort_key(row: Tuple[Any, ...]):
    # None（未绑定占位）排在字符串之前，其余按完整字符串字典序
    return tuple((0, "") if value is None else (1, value) for value in row)


def run_query(model, text) -> QueryResult:
    """在 model.triples 上执行查询，返回去重并按字典序排序后的 QueryResult。"""
    projection, patterns, clauses, star = _compile_query(text)
    if star:
        projection = _star_variables(patterns, clauses)

    facts = model.triples
    bindings = _match_group(patterns, [{}], facts)

    for kind, payload in clauses:
        if not bindings:
            break
        if kind == "optional":
            joined: List[dict] = []
            for binding in bindings:
                # 左连接：块内有匹配则用全部匹配扩展，否则保留原绑定一次
                matches = _match_group(payload, [binding], facts)
                if matches:
                    joined.extend(matches)
                else:
                    joined.append(binding)
            bindings = joined
        else:
            bindings = [b for b in bindings if _eval_filter(payload, b)]

    rows = {tuple(binding.get(name) for name in projection) for binding in bindings}
    return QueryResult(projection, tuple(sorted(rows, key=_row_sort_key)))
