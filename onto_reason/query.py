"""SPARQL 风格基本图模式（BGP）查询，支持属性路径、OPTIONAL 左连接、FILTER 筛选与 UNION 并集。

支持的语法（关键字只接受大写）：

    SELECT (变量... | *) WHERE { 模式 ('.' 模式)* '.'? }
    模式 := 三元组模式
          | OPTIONAL { 三元组模式 ('.' 三元组模式)* '.'? }
          | FILTER ( 表达式 )
          | '{' 三元组模式 ('.' 三元组模式)* '.'? '}'
            (UNION '{' 三元组模式 ('.' 三元组模式)* '.'? '}')+

- 变量：'?' 后跟至少一个 Unicode 字母、数字或下划线。
- 常量：不含空白且不以 '?' 开头的名称；或双引号包裹的 JSON 字符串，
  字符串解码后按完整字符串精确比较。
- 三元组模式固定为主语、谓语、宾语三项；主语和宾语必须是常量或变量，
  谓语必须是常量属性名或属性路径，不能是变量。
- 属性路径只作用于谓语位置，由已声明属性名组成，支持：
    ^p      逆向：沿 p 的反向边连接；
    a/b     序列：先走 a 再走 b（关系复合）；
    a|b     选择：a、b 任选一支（并集）；
    p?      零次或一次；p* 零次或多次；p+ 一次或多次；
    ( ... ) 圆括号仅用于分组；运算符两侧允许任意空白。
  路径求值得到去重的节点对集合；p?/p* 的零次分支只连接当前模型全部
  三元组中实际出现过的主语或宾语，不产生新术语绑定；p+ 在环状数据上
  也会有限结束。路径同样可用于 OPTIONAL 块与 UNION 分支内。
- OPTIONAL 块内可含一个或多个三元组模式，不得嵌套；按左连接处理：
  块内存在匹配时用所有匹配扩展绑定，否则保留原绑定且块内新变量未绑定。
  多个 OPTIONAL 块按出现顺序依次处理。
- UNION 连接 WHERE 主体内相邻的两个或多个花括号分支，连续 UNION 从左到右
  结合；每个分支含一个或多个三元组模式，分支内不得再出现 UNION、OPTIONAL
  或 FILTER，分支不得为空。求值时各分支分别从当前绑定独立生成解再取并集：
  同名变量与既有绑定不一致的解被丢弃，仅在一分支绑定的变量随该分支保留，
  最终仍未绑定的投影变量以 None 占位。某分支无匹配时仍采用其他分支的结果。
- FILTER 表达式只支持：
  BOUND(?v)、!BOUND(?v)、?v = ?w、?v != ?w、
  字符串常量与变量或字符串常量的 = / != 比较；
  涉及未绑定变量的等值或不等值比较结果为假；多个 FILTER 按逻辑与过滤。
  UNION 之后的 FILTER 在所有分支合并完成后执行，可引用任一分支的变量。
  属性路径不是比较表达式，FILTER 语义不因路径语法改变。
- '*' 投影按模式（含 OPTIONAL 块与 UNION 分支内模式）从左到右首次出现的
  顺序投影全部变量；投影变量未绑定时结果行中以 None 占位。

查询在 OntologyModel.triples（显式 + 推理三元组）上做嵌套循环连接匹配，
同一变量跨模式绑定同一字符串，一条事实可被多个模式复用。
任何词法或语法错误都抛出带字符位置或模式/分支序号的 OntologyError。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

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
# 仅在谓语路径位置识别的 token
_TOK_CARET = "CARET"
_TOK_PIPE = "PIPE"
_TOK_SLASH = "SLASH"
_TOK_QMARK = "QMARK"
_TOK_PLUS = "PLUS"

_DELIMITERS = frozenset('{}."?*')
# FILTER 圆括号内额外识别为独立 token 的字符；括号外这些字符仍是名称的一部分，
# 以保持不含 OPTIONAL/FILTER 的查询词法与旧行为完全一致。
_FILTER_DELIMITERS = frozenset('()!=')
# 谓语路径位置额外成为词法边界/运算符的字符；只在路径扫描模式下生效，
# 主语、宾语位置的同名常量分词结果保持不变。
_PATH_DELIMITERS = frozenset('^|?+/()')

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

# 谓语为属性路径时的标记；普通常量谓语仍为三元组 (_LIT, 名称, 位置)。
_PRED_PATH = "PATH"
# 路径 AST 节点（全部为不可变可哈希元组）：
# ("N", 属性名, 起始位置) 属性叶子；("NS", 字符串常量, 起始位置) 字符串叶子
# （只允许单独作为谓语，等价于旧的字符串常量谓语；嵌入复合路径时报错）；
# ("I", 子节点) 逆向；("S", (子节点...)) 序列；("A", (子节点...)) 选择；
# ("Q", 量词('?'/'*'/'+'), 子节点, 位置) 重复。
_PATH_LEAF = "N"
_PATH_STRLEAF = "NS"
_PATH_INV = "I"
_PATH_SEQ = "S"
_PATH_ALT = "A"
_PATH_QUANT = "Q"

# 字符串常量单独作谓语时的内部标记（匹配行为与普通字面值一致）。
_LIT_STR = "S="


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


class _Scanner:
    """按需产生 token 的扫描器。

    path_mode 只应在三元组模式的谓语槽位打开：此时 ^ | / ? * + ( ) 作为
    属性路径运算符；其余位置的词法与历史实现逐字一致（这些字符在主语、
    宾语位置仍可作为普通名称的组成部分）。
    """

    __slots__ = (
        "text", "n", "i", "filter_spans", "span_i", "path_mode",
        "_span_starts", "_buffered", "_has_buffer", "_buf_span_i",
        "_last_advanced_end",
    )

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.i = 0
        self.filter_spans = _find_filter_spans(text)
        self.span_i = 0
        self.path_mode = False
        self._span_starts = {start for start, _ in self.filter_spans}
        self._buffered: Optional[_Token] = None
        # None 缓冲表示尚未前瞻；_scan 在 EOF 也返回 None，因此另用标志区分。
        self._has_buffer = False
        # 前瞻 token 扫描前的 FILTER 区间指针，便于切换词法模式时回退重扫。
        self._buf_span_i = 0
        # 最近一次消费的 token 结束位置（用于判断两词之间是否有空白）。
        self._last_advanced_end = 0

    # ---------- 前瞻与消费 ----------

    def set_path_mode(self, flag: bool) -> None:
        """切换词法模式。

        若已有按旧模式扫描的前瞻 token，则把扫描位置回退到该 token 起点，
        下一次 peek 会按新模式重新分词，保证谓语槽位之外的词法结果不变。
        """
        if flag == self.path_mode:
            return
        self.path_mode = flag
        if self._has_buffer:
            self.i = self._buffered.pos
            self.span_i = self._buf_span_i
            self._buffered = None
            self._has_buffer = False

    def peek(self) -> Optional[_Token]:
        if not self._has_buffer:
            self._buf_span_i = self.span_i
            self._buffered = self._scan()
            self._has_buffer = True
        return self._buffered

    def advance(self) -> None:
        if self._has_buffer and self._buffered is not None:
            self._last_advanced_end = (
                self._buffered.pos + len(self._buffered.value)
            )
        self._buffered = None
        self._has_buffer = False

    def second_token(self) -> Optional[_Token]:
        """在当前位置之后再试探扫描一个 token（按非路径模式）。

        用于识别 'OPTIONAL {'、'FILTER (' 这类双 token 关键字边界；
        试探扫描后完整复位，不改变正式分词状态与前瞻缓冲。
        """
        saved_i = self.i
        saved_span_i = self.span_i
        saved_mode = self.path_mode
        self.path_mode = False
        nxt = self._scan()
        self.i = saved_i
        self.span_i = saved_span_i
        self.path_mode = saved_mode
        return nxt

    # ---------- FILTER 区间判定 ----------

    def _in_filter(self, pos: int) -> bool:
        while self.span_i < len(self.filter_spans) and pos > self.filter_spans[self.span_i][1]:
            self.span_i += 1
        return (
            self.span_i < len(self.filter_spans)
            and self.filter_spans[self.span_i][0] <= pos <= self.filter_spans[self.span_i][1]
        )

    # ---------- 单 token 扫描 ----------

    def _scan(self) -> Optional[_Token]:
        text = self.text
        n = self.n
        while self.i < n and text[self.i].isspace():
            self.i += 1
        if self.i >= n:
            return None
        i = self.i
        ch = text[i]

        def emit(kind: str, value: str, length: int) -> _Token:
            self.i = i + length
            return _Token(kind, value, i)

        if ch == "{":
            return emit(_TOK_LBRACE, ch, 1)
        if ch == "}":
            return emit(_TOK_RBRACE, ch, 1)
        if ch == ".":
            return emit(_TOK_DOT, ch, 1)
        # '*' 在任何位置都是独立 token：既用于 SELECT * 投影，也用于路径 p*。
        if ch == "*":
            return emit(_TOK_STAR, ch, 1)
        if ch == '"':
            return self._scan_string(i)

        # 谓语路径位置：^ | / ? + 与圆括号都是路径语法 token。
        if self.path_mode:
            if ch == "?":
                # '?' 后紧跟变量字符时整体是变量词，独立的 '?' 才是零或
                # 一次量词；与非路径位置对 '?' 的判定保持一致。
                if i + 1 < n and _is_var_char(text[i + 1]):
                    j = i + 1
                    while j < n and _is_var_char(text[j]):
                        j += 1
                    self.i = j
                    return _Token(_TOK_VAR, text[i:j], i)
                return emit(_TOK_QMARK, ch, 1)
            if ch == "^":
                return emit(_TOK_CARET, ch, 1)
            if ch == "|":
                return emit(_TOK_PIPE, ch, 1)
            if ch == "/":
                return emit(_TOK_SLASH, ch, 1)
            if ch == "+":
                return emit(_TOK_PLUS, ch, 1)
            if ch == "(":
                return emit(_TOK_LPAREN, ch, 1)
            if ch == ")":
                return emit(_TOK_RPAREN, ch, 1)

        if self._in_filter(i):
            if ch == "(":
                return emit(_TOK_LPAREN, ch, 1)
            if ch == ")":
                return emit(_TOK_RPAREN, ch, 1)
            if ch == "!":
                if i + 1 < n and text[i + 1] == "=":
                    return emit(_TOK_NE, "!=", 2)
                return emit(_TOK_BANG, "!", 1)
            if ch == "=":
                return emit(_TOK_EQ, "=", 1)

        if ch == "?":
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            if j == i + 1:
                raise OntologyError(
                    f"词法错误：'?' 后必须至少跟随一个字母、数字或下划线"
                    f"（字符位置 {i}）"
                )
            self.i = j
            return _Token(_TOK_VAR, text[i:j], i)

        # 普通名称：路径位置额外以路径运算符为界；
        # FILTER 区间内额外以 ()!= 为界，其他位置它们仍是名称字符。
        delimiters = _DELIMITERS
        if self.path_mode:
            delimiters = delimiters | _PATH_DELIMITERS
        if self._in_filter(i):
            delimiters = delimiters | _FILTER_DELIMITERS
        start = i
        j = i
        while j < n and not text[j].isspace() and text[j] not in delimiters:
            # 'FILTER(' 无空格时，左括号是独立区间起点，名称只取到 FILTER
            if not self.path_mode and j > start and j in self._span_starts:
                break
            j += 1
        self.i = j
        return _Token(_TOK_NAME, text[start:j], start)

    def _scan_string(self, start: int) -> _Token:
        text = self.text
        n = self.n
        j = start + 1
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
        self.i = j + 1
        return _Token(_TOK_STRING, value, start)


class _Parser:
    """把 token 流编译为 (投影变量名列表, 模式子句列表, 是否星号投影)。

    模式子句为三元组：
    - (_CLAUSE_BGP, patterns, None)
    - (_CLAUSE_OPTIONAL, patterns, None)
    - (_CLAUSE_FILTER, None, 表达式元组)
    - (_CLAUSE_UNION, branches, None)：branches 为分支模式列表的列表
    每个三元组模式为 (主语项, 谓语, 宾语项)：主语/宾语为 (种类, 值, 位置)，
    谓语为常量字面值三元组 (_LIT, 属性名, 位置) 或 (_PRED_PATH, 路径 AST)。
    模式序号在整个 WHERE 体内连续编号，BGP、OPTIONAL 与 UNION 分支中的
    三元组一并计数；FILTER 不占用三元组序号。
    """

    def __init__(self, text: str, scanner: _Scanner) -> None:
        self._text = text
        self._sc = scanner
        # 三元组模式序号在整个 WHERE 体内连续编号（含 OPTIONAL 块内模式）
        self._pattern_index = 0

    def _peek(self) -> Optional[_Token]:
        return self._sc.peek()

    def _eof(self) -> bool:
        return self._sc.peek() is None

    def _advance(self) -> None:
        self._sc.advance()

    def _error(self, message: str, pos: int) -> "OntologyError":
        return OntologyError(f"{message}（字符位置 {pos}）")

    def _here_pos(self) -> int:
        tok = self._peek()
        return tok.pos if tok is not None else len(self._text)

    def _next_is(self, kind: str) -> bool:
        if self._peek() is None:
            return False
        nxt = self._sc.second_token()
        return nxt is not None and nxt.kind == kind

    def _expect_keyword(self, word: str) -> _Token:
        tok = self._peek()
        if tok is None:
            pos = len(self._text)
            raise self._error(f"查询缺少关键字 {word}，或存在未知语句成分", pos)
        if tok.kind != _TOK_NAME or tok.value != word:
            raise self._error(
                f"查询缺少关键字 {word}，或存在未知语句成分 {tok.value!r}",
                tok.pos,
            )
        self._advance()
        return tok

    def parse(self) -> Tuple[Tuple[str, ...], List[tuple], bool]:
        self._expect_keyword("SELECT")
        projection, star = self._parse_projection()
        self._expect_keyword("WHERE")
        tok = self._peek()
        if tok is None or tok.kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        lbrace_pos = tok.pos
        self._advance()
        clauses = self._parse_group_body(optional=False, lbrace_pos=lbrace_pos)
        # _parse_group_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        self._validate_projection(projection, clauses)
        return tuple(name for name, _ in projection), clauses, star

    def _parse_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        self._sc.set_path_mode(False)
        tok = self._peek()
        if tok is None:
            raise self._error("SELECT 后缺少投影变量或 '*'", len(self._text))
        if tok.kind == _TOK_STAR:
            self._advance()
            # '*' 后必须紧跟 WHERE
            nxt = self._peek()
            if nxt is None or nxt.kind != _TOK_NAME or nxt.value != "WHERE":
                raise self._error("'*' 与 WHERE 之间存在未知语句成分", self._here_pos())
            return [], True

        names: List[Tuple[str, int]] = []
        seen = set()
        while True:
            tok = self._peek()
            if tok is None:
                break
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
            self._advance()
        if not names:
            raise self._error("SELECT 后缺少投影变量或 '*'", self._here_pos())
        return names, False

    def _read_subject_or_object(self) -> Tuple[Any, Any, Any]:
        """读取主语/宾语槽位的一个词（调用时已是非路径扫描模式）。"""
        tok = self._peek()
        if tok.kind == _TOK_VAR:
            term = (_VAR, tok.value, tok.pos)
        elif tok.kind in (_TOK_NAME, _TOK_STRING):
            term = (_LIT, tok.value, tok.pos)
        else:
            raise self._error(
                f"三元组模式 {self._pattern_index} 中存在未知语句成分"
                f" {tok.value!r}",
                tok.pos,
            )
        self._advance()
        return term

    def _parse_group_body(self, optional: bool, lbrace_pos: int) -> List[tuple]:
        """解析 '{' 之后直到匹配 '}' 的模式组，返回子句列表（已消费 '}'）。"""
        scope = "OPTIONAL 块" if optional else "WHERE 模式体"
        clauses: List[tuple] = []
        pending: List[Tuple[Any, Any, Any]] = []
        # prev_dot：上一个 token 是否为点号（拒绝连续点号）；
        # clause_ok：当前位置是否允许出现 OPTIONAL/FILTER 子句
        # （组首、点号之后或前一子句之后）。
        prev_dot = False
        clause_ok = True

        def finish_pattern() -> None:
            if len(pending) != 3:
                pos = pending[-1][2] if pending else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            if len(p) == 3 and p[0] == _VAR:
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 的谓语不能是变量 {p[1]}"
                    f"（字符位置 {p[2]}）"
                )
            clauses.append((_CLAUSE_BGP, [(s, p, o)], None))
            pending.clear()
            self._pattern_index += 1

        while True:
            # 谓语槽位（pending 恰有一项）才打开路径扫描模式。
            self._sc.set_path_mode(len(pending) == 1)
            tok = self._peek()
            if tok is None:
                raise self._error(f"{scope}缺少右花括号 '}}'", len(self._text))

            if tok.kind == _TOK_RBRACE:
                if pending:
                    finish_pattern()
                self._advance()
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
                self._advance()
                continue

            # 子句关键字只在模式边界成立；未跟起始符号的关键字按普通常量处理。
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

            # UNION 选择分支：'{' 分支 '}' (UNION '{' 分支 '}')+，只在 WHERE 主体出现
            if clause_ok and tok.kind == _TOK_LBRACE:
                if optional:
                    raise self._error(
                        "OPTIONAL 块内只允许出现三元组模式，不得嵌套花括号组",
                        tok.pos,
                    )
                clauses.append(self._parse_union_clause())
                prev_dot = False
                clause_ok = True
                continue

            # UNION 关键字两侧必须是相邻的独立花括号组
            if clause_ok and tok.kind == _TOK_NAME and tok.value == "UNION":
                raise self._error(
                    "UNION 两侧必须是相邻的独立花括号组",
                    tok.pos,
                )

            if len(pending) == 3:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 项数过多："
                    f"模式之间需要用 '.' 分隔",
                    tok.pos,
                )

            if len(pending) == 1:
                # 谓语：常量属性名或属性路径，在此整体消费。
                pending.append(self._parse_predicate())
                prev_dot = False
                clause_ok = False
                continue

            # 主语或宾语槽位
            pending.append(self._read_subject_or_object())
            prev_dot = False
            clause_ok = False

        if not any(kind != _CLAUSE_FILTER for kind, _, _ in clauses):
            raise OntologyError(
                f"{'OPTIONAL' if optional else 'WHERE'} 花括号内的模式组为空"
                f"（字符位置 {lbrace_pos}）"
            )
        return clauses

    def _parse_optional_clause(self) -> tuple:
        """解析 OPTIONAL { ... }，关键字为当前 token。"""
        kw = self._peek()
        self._advance()
        tok = self._peek()
        if tok is None or tok.kind != _TOK_LBRACE:
            raise self._error(
                "OPTIONAL 后缺少左花括号 '{'",
                self._here_pos(),
            )
        lbrace_pos = tok.pos
        self._advance()
        inner = self._parse_group_body(optional=True, lbrace_pos=lbrace_pos)
        patterns: List[Tuple[Any, Any, Any]] = []
        for kind, group, _ in inner:
            if kind != _CLAUSE_BGP:  # pragma: no cover - 解析器已拒绝嵌套分组
                raise self._error(
                    "OPTIONAL 块内只允许出现三元组模式，不得嵌套",
                    kw.pos,
                )
            patterns.extend(group)
        return (_CLAUSE_OPTIONAL, patterns, None)

    def _parse_union_clause(self) -> tuple:
        """解析 '{' 分支 '}' (UNION '{' 分支 '}')+，首个 '{' 为当前 token。

        连续 UNION 从左到右结合；并集满足结合律，因此扁平化为一个子句，
        分支按出现顺序编号（从 1 开始）用于错误定位。
        """
        branches: List[List[Tuple[Any, Any, Any]]] = []
        while True:
            index = len(branches) + 1
            lbrace = self._peek()
            self._advance()  # 消费分支的 '{'
            branches.append(self._parse_union_branch(index, lbrace.pos))
            tok = self._peek()
            if tok is not None and tok.kind == _TOK_NAME and tok.value == "UNION":
                self._advance()
                nxt = self._peek()
                if nxt is None or nxt.kind != _TOK_LBRACE:
                    raise self._error(
                        f"UNION 后缺少左花括号 '{{'（分支 {index + 1}）",
                        self._here_pos(),
                    )
                continue
            break
        if len(branches) < 2:
            raise self._error(
                "相邻花括号组之间缺少 UNION 关键字（分支 1）",
                self._here_pos(),
            )
        return (_CLAUSE_UNION, branches, None)

    def _parse_union_branch(self, index: int, lbrace_pos: int) -> List[Tuple[Any, Any, Any]]:
        """解析 UNION 单个分支 '{' 之后直到匹配 '}' 的三元组模式（已消费 '}'）。

        分支内只允许一个或多个三元组模式，不得嵌套 UNION、OPTIONAL 或
        FILTER，也不得为空；模式之间与分支末尾沿用可选点号规则。
        """
        patterns: List[Tuple[Any, Any, Any]] = []
        pending: List[Tuple[Any, Any, Any]] = []
        prev_dot = False

        def finish_pattern() -> None:
            if len(pending) != 3:
                pos = pending[-1][2] if pending else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            if len(p) == 3 and p[0] == _VAR:
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 的谓语不能是变量 {p[1]}"
                    f"（字符位置 {p[2]}）"
                )
            patterns.append((s, p, o))
            pending.clear()
            self._pattern_index += 1

        while True:
            self._sc.set_path_mode(len(pending) == 1)
            tok = self._peek()
            if tok is None:
                raise self._error(
                    f"UNION 分支 {index} 缺少右花括号 '}}'",
                    len(self._text),
                )

            if tok.kind == _TOK_RBRACE:
                if pending:
                    finish_pattern()
                self._advance()
                break

            if tok.kind == _TOK_DOT:
                if pending:
                    finish_pattern()
                elif prev_dot or not patterns:
                    raise self._error(
                        "点号 '.' 只能出现在一条完整三元组模式或子句之后",
                        tok.pos,
                    )
                prev_dot = True
                self._advance()
                continue

            # 分支边界（组首或点号之后）上的保留字与嵌套组一律拒绝
            if not pending:
                if tok.kind == _TOK_LBRACE:
                    raise self._error(
                        f"UNION 分支 {index} 内不得嵌套花括号组",
                        tok.pos,
                    )
                if tok.kind == _TOK_NAME and tok.value == "UNION":
                    raise self._error(
                        f"UNION 分支 {index} 内不得嵌套 UNION",
                        tok.pos,
                    )
                if (
                    tok.kind == _TOK_NAME
                    and tok.value == "OPTIONAL"
                    and self._next_is(_TOK_LBRACE)
                ):
                    raise self._error(
                        f"UNION 分支 {index} 内只允许出现三元组模式，不得使用 OPTIONAL",
                        tok.pos,
                    )
                if (
                    tok.kind == _TOK_NAME
                    and tok.value == "FILTER"
                    and self._next_is(_TOK_LPAREN)
                ):
                    raise self._error(
                        f"UNION 分支 {index} 内只允许出现三元组模式，不得使用 FILTER",
                        tok.pos,
                    )

            if len(pending) == 3:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 项数过多："
                    f"模式之间需要用 '.' 分隔",
                    tok.pos,
                )

            if len(pending) == 1:
                pending.append(self._parse_predicate())
                prev_dot = False
                continue

            pending.append(self._read_subject_or_object())
            prev_dot = False

        if not patterns:
            raise OntologyError(
                f"UNION 分支 {index} 为空：分支内没有三元组模式"
                f"（字符位置 {lbrace_pos}）"
            )
        return patterns

    # ---------- 属性路径解析（谓语槽位，扫描器处于路径模式） ----------

    def _parse_predicate(self) -> tuple:
        """解析整个谓语：裸属性名降级为旧的字面值三元组，否则包装为路径。

        单独的变量词（?p）不当场报错，而是作为普通项保留，由模式结束时的
        校验按旧顺序先查项数再报“谓语不能是变量”。

        '|'、'/' 与 '?'/'*'/'+' 量词总会延续路径（运算符两侧允许空白），
        缺操作数等问题在路径递归解析处报错；路径主体结束后，只有 '^'、
        '('、')' 可能与宾语常量开头冲突——它们与上一个词直接相邻时判为
        不支持的路径写法，有空白分隔时谓语结束、符号交还宾语词法。
        """
        first = self._peek()
        if first is not None and first.kind == _TOK_VAR:
            self._advance()
            tail = self._peek()
            if (
                tail is not None
                and tail.kind in (_TOK_CARET, _TOK_LPAREN, _TOK_RPAREN)
                and tail.pos == self._sc._last_advanced_end
            ):
                raise self._error(
                    f"不支持的属性路径写法：谓语不能是变量 {first.value}"
                    f"后再接路径运算符 {tail.value!r}",
                    tail.pos,
                )
            return (_VAR, first.value, first.pos)
        node = self._parse_path_alternative()
        # 字符串常量只有在整个谓语就是它一个时才合法（沿用旧的字符串常量
        # 谓语语义）；一旦嵌入复合路径（如 "p"/q、^"p"、("p")*），即属
        # 不支持的路径写法，在该字符串位置报错。
        if node[0] == _PATH_STRLEAF:
            return (_LIT_STR, node[1], node[2])
        for inner in _iter_path_nodes(node):
            if inner[0] == _PATH_STRLEAF:
                raise self._error(
                    "不支持的属性路径写法：路径只能由属性名组成，"
                    "字符串常量不能作为路径成员",
                    inner[2],
                )
        tail = self._peek()
        if (
            tail is not None
            and tail.kind in (_TOK_CARET, _TOK_LPAREN, _TOK_RPAREN)
            and tail.pos == self._sc._last_advanced_end
        ):
            if tail.kind == _TOK_RPAREN:
                raise self._error(
                    "属性路径括号不配对：出现了没有对应左圆括号的 ')'",
                    tail.pos,
                )
            if tail.kind == _TOK_CARET:
                raise self._error(
                    "不支持的属性路径写法：'^' 只能位于属性名或圆括号分组之前",
                    tail.pos,
                )
            raise self._error(
                "不支持的属性路径写法：属性名与圆括号分组之间缺少序列运算符 '/'",
                tail.pos,
            )
        if node[0] == _PATH_LEAF:
            return (_LIT, node[1], node[2])
        return (_PRED_PATH, node)


    def _parse_path_alternative(self) -> tuple:
        """path := sequence ('|' sequence)*，'|' 从左到右扁平结合。"""
        node = self._parse_path_sequence()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != _TOK_PIPE:
                return node
            self._advance()
            right = self._parse_path_sequence()
            if node[0] == _PATH_ALT:
                node = (_PATH_ALT, node[1] + (right,))
            else:
                node = (_PATH_ALT, (node, right))

    def _parse_path_sequence(self) -> tuple:
        """sequence := step ('/' step)*，'/' 优先级高于 '|'。"""
        node = self._parse_path_step()
        while True:
            tok = self._peek()
            if tok is None or tok.kind != _TOK_SLASH:
                return node
            self._advance()
            right = self._parse_path_step()
            if node[0] == _PATH_SEQ:
                node = (_PATH_SEQ, node[1] + (right,))
            else:
                node = (_PATH_SEQ, (node, right))

    def _parse_path_step(self) -> tuple:
        """step := '^'? primary ('?' | '*' | '+')?。

        与 SPARQL 一致，量词绑定到主元素：^p* 解释为 ^(p*)。
        """
        tok = self._peek()
        inverse = False
        inv_pos = -1
        if tok is not None and tok.kind == _TOK_CARET:
            inverse = True
            inv_pos = tok.pos
            self._advance()
            nxt = self._peek()
            if nxt is None or nxt.kind not in (
                _TOK_NAME, _TOK_STRING, _TOK_LPAREN
            ):
                raise self._error(
                    "属性路径运算符 '^' 后缺少属性名或圆括号分组",
                    inv_pos,
                )
        node = self._parse_path_primary()
        quant_tok = self._peek()
        if quant_tok is not None and quant_tok.kind in (_TOK_QMARK, _TOK_STAR, _TOK_PLUS):
            quant = quant_tok.value
            qpos = quant_tok.pos
            self._advance()
            doubled = self._peek()
            if doubled is not None and doubled.kind in (_TOK_QMARK, _TOK_STAR, _TOK_PLUS):
                raise self._error(
                    f"不支持的属性路径写法：量词 {quant!r} 后不能再跟量词"
                    f" {doubled.value!r}",
                    doubled.pos,
                )
            node = (_PATH_QUANT, quant, node, qpos)
        if inverse:
            node = (_PATH_INV, node, inv_pos)
        return node

    def _parse_path_primary(self) -> tuple:
        """primary := 属性名 | JSON 字符串常量 | '(' path ')'。"""
        tok = self._peek()
        if tok is None:
            raise self._error(
                "属性路径运算符后缺少操作数：需要已声明属性名或圆括号分组",
                len(self._text),
            )
        if tok.kind == _TOK_NAME:
            self._advance()
            return (_PATH_LEAF, tok.value, tok.pos)
        if tok.kind == _TOK_STRING:
            self._advance()
            return (_PATH_STRLEAF, tok.value, tok.pos)
        if tok.kind == _TOK_VAR:
            # 变量只有在整个谓语就是单个变量时才允许进入并由模式结束校验
            # 报错；一旦出现在路径运算符之后，属于缺操作数/路径里混入变量。
            raise OntologyError(
                f"属性路径中不能出现变量 {tok.value}：谓语只能由已声明属性名"
                f"组成（字符位置 {tok.pos}）"
            )
        if tok.kind == _TOK_QMARK:
            # 独立的 '?' 量词没有操作数：不支持的路径写法。
            raise self._error(
                "属性路径量词 '?' 前缺少属性名或圆括号分组",
                tok.pos,
            )
        if tok.kind == _TOK_STAR:
            raise self._error(
                "属性路径量词 '*' 前缺少属性名或圆括号分组",
                tok.pos,
            )
        if tok.kind == _TOK_LPAREN:
            lparen_pos = tok.pos
            self._advance()
            nxt = self._peek()
            if nxt is not None and nxt.kind == _TOK_RPAREN:
                raise self._error(
                    "不支持的属性路径写法：圆括号分组内不能为空",
                    lparen_pos,
                )
            node = self._parse_path_alternative()
            closing = self._peek()
            if closing is None or closing.kind != _TOK_RPAREN:
                raise self._error(
                    "属性路径缺少右圆括号 ')'，圆括号只用于路径分组",
                    self._here_pos() if closing is None else closing.pos,
                )
            self._advance()
            return node
        if tok.kind in (_TOK_PIPE, _TOK_SLASH, _TOK_PLUS):
            raise self._error(
                f"属性路径运算符 {tok.value!r} 前缺少操作数（属性名或圆括号分组）",
                tok.pos,
            )
        if tok.kind == _TOK_CARET:
            raise self._error(
                "属性路径运算符 '^' 后缺少属性名或圆括号分组（不能连续出现 '^'）",
                tok.pos,
            )
        if tok.kind == _TOK_RPAREN:
            raise self._error(
                "属性路径括号不配对：出现了没有对应左圆括号的 ')'",
                tok.pos,
            )
        if tok.kind in (_TOK_RBRACE, _TOK_DOT):
            raise self._error(
                "属性路径运算符后缺少操作数：需要已声明属性名或圆括号分组",
                tok.pos,
            )
        raise self._error(
            f"三元组模式 {self._pattern_index} 的谓语位置存在未知语句成分"
            f" {tok.value!r}",
            tok.pos,
        )

    def _parse_filter_clause(self) -> tuple:
        """解析 FILTER ( 表达式 )，关键字为当前 token。"""
        self._sc.set_path_mode(False)
        self._advance()
        tok = self._peek()
        if tok is None or tok.kind != _TOK_LPAREN:
            raise self._error(
                "FILTER 后缺少左圆括号 '('",
                self._here_pos(),
            )
        lparen = tok
        self._advance()
        expr = self._parse_filter_expr(lparen.pos)
        tok = self._peek()
        if tok is None or tok.kind != _TOK_RPAREN:
            raise self._error(
                "FILTER 表达式缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._advance()
        return (_CLAUSE_FILTER, None, expr)

    def _parse_filter_expr(self, lparen_pos: int) -> tuple:
        """解析圆括号内的单个表达式，'(' 与 ')' 已由调用方消费。"""
        self._sc.set_path_mode(False)
        tok = self._peek()
        if tok is None:
            raise OntologyError(
                f"FILTER 表达式为空（字符位置 {lparen_pos}）"
            )

        # !BOUND(?v)
        if tok.kind == _TOK_BANG:
            bang = tok
            self._advance()
            nxt = self._peek()
            if nxt is None or nxt.kind != _TOK_NAME or nxt.value != "BOUND":
                raise self._error(
                    "FILTER 中 '!' 后只允许 BOUND(?v) 形式",
                    bang.pos,
                )
            bound_tok = nxt
            self._advance()
            var_tok = self._expect_bound_variable(bound_tok.pos)
            self._expect_expr_end(bound_tok.pos)
            return (_EXPR_NOT_BOUND, var_tok.value)

        # BOUND(?v)
        if tok.kind == _TOK_NAME and tok.value == "BOUND":
            self._advance()
            var_tok = self._expect_bound_variable(tok.pos)
            self._expect_expr_end(tok.pos)
            return (_EXPR_BOUND, var_tok.value)

        # 左操作数：变量或字符串常量（普通名称不是合法表达式）
        if tok.kind == _TOK_VAR:
            left = (_VAR, tok.value, tok.pos)
            self._advance()
        elif tok.kind == _TOK_STRING:
            left = (_LIT, tok.value, tok.pos)
            self._advance()
        else:
            raise self._error(
                f"未知 FILTER 表达式形式：{tok.value!r} 不是合法的表达式开头",
                tok.pos,
            )

        op_tok = self._peek()
        if op_tok is None or op_tok.kind not in (_TOK_EQ, _TOK_NE):
            raise self._error(
                "FILTER 比较表达式需要 '=' 或 '!=' 运算符",
                self._here_pos(),
            )
        self._advance()

        right_tok = self._peek()
        if right_tok is None:
            raise self._error(
                f"FILTER 表达式在运算符 {op_tok.value!r} 后缺少右操作数"
                f"（字符位置 {op_tok.pos}）",
                op_tok.pos,
            )
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
        self._advance()
        self._expect_expr_end(right_tok.pos)
        kind = _EXPR_EQ if op_tok.kind == _TOK_EQ else _EXPR_NE
        return (kind, left, right)

    def _expect_bound_variable(self, bound_pos: int) -> _Token:
        """消费 BOUND 后面的 '(' 变量 ')'，返回变量 token。"""
        tok = self._peek()
        if tok is None or tok.kind != _TOK_LPAREN:
            raise self._error(
                "BOUND 后缺少左圆括号 '('",
                self._here_pos(),
            )
        self._advance()
        tok = self._peek()
        if tok is None or tok.kind != _TOK_VAR:
            raise self._error(
                "BOUND(...) 中必须且只能出现一个变量",
                self._here_pos(),
            )
        var_tok = tok
        self._advance()
        tok = self._peek()
        if tok is None or tok.kind != _TOK_RPAREN:
            raise self._error(
                f"BOUND({var_tok.value} 后缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._advance()
        return var_tok

    def _expect_expr_end(self, anchor_pos: int) -> None:
        """表达式解析后，下一个 token 必须是外层 FILTER 的右圆括号。"""
        tok = self._peek()
        if tok is None:
            raise OntologyError(
                f"FILTER 表达式不完整，缺少右圆括号 ')'（字符位置 {anchor_pos}）"
            )
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
    scanner = _Scanner(text)
    return _Parser(text, scanner).parse()


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
    """按模式从左到右、首次出现的顺序收集全部变量（谓语恒为常量或路径）。"""
    order: List[str] = []
    seen = set()
    for s, p, o in _all_patterns(clauses):
        # 谓语不含变量；仅主语、宾语可能引入新变量。
        for item in (s, o):
            if item[0] == _VAR and item[1] not in seen:
                seen.add(item[1])
                order.append(item[1])
    return tuple(order)


def _iter_path_nodes(node: tuple):
    """深度优先遍历路径 AST，产出全部节点（含叶子）。"""
    yield node
    tag = node[0]
    if tag in (_PATH_LEAF, _PATH_STRLEAF):
        return
    if tag == _PATH_QUANT:
        yield from _iter_path_nodes(node[2])
    else:
        for child in node[1] if tag != _PATH_INV else (node[1],):
            yield from _iter_path_nodes(child)


def _validate_paths(clauses, declared: frozenset) -> None:
    """谓语中的属性名（含裸常量谓语与路径叶子）必须是已声明属性。"""
    for _s, p, _o in _all_patterns(clauses):
        if len(p) == 2 and p[0] == _PRED_PATH:
            for node in _iter_path_nodes(p[1]):
                if node[0] != _PATH_LEAF:
                    continue
                name, pos = node[1], node[2]
                if name not in declared:
                    raise OntologyError(
                        f"属性路径引用了未声明的属性 {name!r}"
                        f"（谓语位置，字符位置 {pos}）"
                    )
        elif len(p) == 3 and p[0] == _LIT and p[1] not in declared:
            raise OntologyError(
                f"谓语引用了未声明的属性 {p[1]!r}"
                f"（谓语位置，字符位置 {p[2]}）"
            )
        # _LIT_STR（字符串常量单独作谓语）沿用旧行为：按字符串值匹配，
        # 不做已声明属性校验，匹配不到时返回空结果。


class _PathEngine:
    """在模型全部三元组（显式 + 推理）上把属性路径求值为去重节点对集合。"""

    __slots__ = ("_by_predicate", "_universe", "_cache")

    def __init__(self, facts) -> None:
        by_predicate: Dict[str, set] = {}
        universe = set()
        for fact in facts:
            by_predicate.setdefault(fact.predicate, set()).add(
                (fact.subject, fact.object)
            )
            universe.add(fact.subject)
            universe.add(fact.object)
        self._by_predicate = by_predicate
        self._universe = frozenset(universe)
        self._cache: Dict[tuple, frozenset] = {}

    def evaluate(self, node: tuple) -> frozenset:
        cached = self._cache.get(node)
        if cached is not None:
            return cached
        tag = node[0]
        if tag == _PATH_LEAF:
            result = frozenset(self._by_predicate.get(node[1], frozenset()))
        elif tag == _PATH_INV:
            result = frozenset((b, a) for a, b in self.evaluate(node[1]))
        elif tag == _PATH_SEQ:
            result = self._evaluate_sequence(node[1])
        elif tag == _PATH_ALT:
            merged: set = set()
            for child in node[1]:
                merged.update(self.evaluate(child))
            result = frozenset(merged)
        else:  # _PATH_QUANT
            result = self._evaluate_quant(node[1], node[2])
        self._cache[node] = result
        return result

    def _evaluate_sequence(self, children) -> frozenset:
        pairs = self.evaluate(children[0])
        for child in children[1:]:
            pairs = self._compose(pairs, self.evaluate(child))
        return pairs

    def _evaluate_quant(self, quant: str, child: tuple) -> frozenset:
        identity = frozenset((node, node) for node in self._universe)
        base = self.evaluate(child)
        if quant == "?":
            return base | identity
        # p+：在子路径出边邻接表上做宽度优先扩张，每轮只保留新增节点对；
        # 邻接表只构建一次，节点集合有限，环状数据上也必然到达不动点。
        adjacency: Dict[str, list] = {}
        for mid, tail in base:
            adjacency.setdefault(mid, []).append(tail)
        reach: set = set(base)
        frontier = set(base)
        while frontier:
            nxt: set = set()
            for head, mid in frontier:
                for tail in adjacency.get(mid, ()):
                    pair = (head, tail)
                    if pair not in reach:
                        reach.add(pair)
                        nxt.add(pair)
            frontier = nxt
        if quant == "*":
            return frozenset(reach) | identity
        return frozenset(reach)

    @staticmethod
    def _compose(left: frozenset, right: frozenset) -> frozenset:
        index: Dict[str, list] = {}
        for mid, tail in right:
            index.setdefault(mid, []).append(tail)
        out: set = set()
        for head, mid in left:
            for tail in index.get(mid, ()):
                out.add((head, tail))
        return frozenset(out)


def _match_patterns(facts, bindings: List[dict], patterns, path_engine) -> List[dict]:
    """对一组三元组模式按序做内连接，返回所有扩展后的绑定。"""
    for s, p, o in patterns:
        path = len(p) == 2 and p[0] == _PRED_PATH
        pairs = path_engine.evaluate(p[1]) if path else None
        next_bindings: List[dict] = []
        for binding in bindings:
            if path:
                iterable = pairs
                for fact_subject, fact_object in iterable:
                    extended = _extend_path_binding(binding, s, o, fact_subject, fact_object)
                    if extended is not None:
                        next_bindings.append(extended)
                continue
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


def _extend_path_binding(binding: dict, s, o, fact_subject: str, fact_object: str):
    """路径节点对上的单条匹配；与既有绑定冲突时返回 None。"""
    if s[0] == _VAR:
        if s[1] in binding and fact_subject != binding[s[1]]:
            return None
    elif fact_subject != s[1]:
        return None
    extended = dict(binding)
    if s[0] == _VAR:
        extended[s[1]] = fact_subject
    # 同一模式内主语/宾语可能是同一变量（如 ?x p+ ?x）
    if o[0] == _VAR:
        if o[1] in extended and fact_object != extended[o[1]]:
            return None
    elif fact_object != o[1]:
        return None
    if o[0] == _VAR:
        extended[o[1]] = fact_object
    return extended


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
    _validate_paths(clauses, frozenset(model.properties))
    if star:
        projection = _star_variables(clauses)

    facts = model.triples
    path_engine = _PathEngine(facts)
    bindings: List[dict] = [{}]
    for kind, patterns, expr in clauses:
        if kind == _CLAUSE_FILTER:
            bindings = [b for b in bindings if _eval_filter(expr, b)]
        elif kind == _CLAUSE_OPTIONAL:
            joined: List[dict] = []
            for binding in bindings:
                matches = _match_patterns(
                    facts, [dict(binding)], patterns, path_engine
                )
                if matches:
                    joined.extend(matches)
                else:
                    # 无匹配：保留原绑定一次，块内新变量保持未绑定
                    joined.append(binding)
            bindings = joined
        elif kind == _CLAUSE_UNION:
            # 每个分支从当前绑定独立求值，结果取并集：同名变量与既有绑定
            # 冲突时该分支匹配自然落空，仅一分支绑定的变量随该分支保留。
            unioned: List[dict] = []
            for binding in bindings:
                for branch in patterns:
                    unioned.extend(
                        _match_patterns(
                            facts, [dict(binding)], branch, path_engine
                        )
                    )
            bindings = unioned
        else:
            bindings = _match_patterns(facts, bindings, patterns, path_engine)
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
