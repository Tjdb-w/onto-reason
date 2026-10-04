"""SPARQL 风格基本图模式（BGP）查询，支持 OPTIONAL 左连接、FILTER 筛选、UNION 并集
与谓语位置的属性路径。

支持的语法（关键字只接受大写）：

    SELECT (变量... | *) WHERE { 模式 ('.' 模式)* '.'? }
        (ORDER BY 排序键+)? (LIMIT 非负整数)? (OFFSET 非负整数)?
    ASK WHERE { 模式 ('.' 模式)* '.'? }
    排序键 := 变量 | ASC(变量) | DESC(变量)   -- 普通变量等价于 ASC(变量)
    模式 := 三元组模式
          | OPTIONAL { 三元组模式 ('.' 三元组模式)* '.'? }
          | FILTER ( 表达式 )
          | '{' 三元组模式 ('.' 三元组模式)* '.'? '}'
            (UNION '{' 三元组模式 ('.' 三元组模式)* '.'? '}')+

- 变量：'?' 后跟至少一个 Unicode 字母、数字或下划线。
- 常量：不含空白且不以 '?' 开头的名称；或双引号包裹的 JSON 字符串，
  字符串解码后按完整字符串精确比较。
- 三元组模式固定为主语、谓语、宾语三项；主语、宾语为常量或变量，
  谓语为常量属性名或属性路径，路径中的属性名必须是已声明属性。
- 属性路径只作用于 OntologyModel.triples（显式 + 推理事实的并集），
  求值得到去重的节点对集合：
    p        属性 p 的直接边；
    ^p       逆向 p（翻转节点对）；
    a/b      序列：先走 a 再走 b（关系复合）；
    a|b      选择：两支节点对取并集；
    p?       零次或一次（零次分支只覆盖三元组中实际出现过的节点）；
    p*       零次或多次（自反传递闭包，环状数据上有限结束）；
    p+       一次或多次（传递闭包，环状数据上有限结束）；
    ( ... )  圆括号只用于分组；运算符两侧允许任意空白。
- OPTIONAL 块内可含一个或多个三元组模式，不得嵌套；按左连接处理：
  块内存在匹配时用所有匹配扩展绑定，否则保留原绑定且块内新变量未绑定。
  多个 OPTIONAL 块按出现顺序依次处理。OPTIONAL 块内也允许属性路径。
- UNION 连接 WHERE 主体内相邻的两个或多个花括号分支，连续 UNION 从左到右
  结合；每个分支含一个或多个三元组模式，分支内不得再出现 UNION、OPTIONAL
  或 FILTER，分支不得为空。求值时各分支分别从当前绑定独立生成解再取并集：
  同名变量与既有绑定不一致的解被丢弃，仅在一分支绑定的变量随该分支保留，
  最终仍未绑定的投影变量以 None 占位。某分支无匹配时仍采用其他分支的结果。
  分支内同样允许属性路径。
- FILTER 表达式只支持：
  BOUND(?v)、!BOUND(?v)、?v = ?w、?v != ?w、
  字符串常量与变量或字符串常量的 = / != 比较；
  涉及未绑定变量的等值或不等值比较结果为假；多个 FILTER 按逻辑与过滤。
  FILTER 保持既有语义，属性路径不是合法的比较操作数。
  UNION 之后的 FILTER 在所有分支合并完成后执行，可引用任一分支的变量。
- '*' 按模式（含 OPTIONAL 块与 UNION 分支内模式）从左到右首次出现的顺序
  投影全部变量；投影变量未绑定时结果行中以 None 占位。
- ASK 与 SELECT 共用同一 WHERE 模式体语法与求值语义，但不做投影：
  不接受 SELECT、变量列表或 '*'，WHERE 花括号之后也不允许任何后缀成分；
  至少存在一个满足全部条件的最终绑定时返回 True，否则返回 False。
- 解序列修饰符只作用于 SELECT，位于 WHERE 模式体右花括号之后，按
  ORDER BY、LIMIT、OFFSET 的顺序各至多出现一次（均可省略）：
  ORDER BY 后至少一个排序键，普通变量或 ASC(?v) 按升序、DESC(?v) 按降序，
  同一排序变量只能出现一次，且必须是投影中的变量（'*' 投影时为任一模式
  变量）；LIMIT 与 OFFSET 只接受非负十进制整数，LIMIT 省略时不截断，
  OFFSET 默认 0。执行时先按既有语义求值、投影、去重并按投影字典序排列，
  再按排序键稳定排序（未绑定值在同一键下先于绑定值，DESC 对该键相反；
  多键按出现顺序比较，全部相同则保持投影字典序），最后跳过 OFFSET 条并
  保留至多 LIMIT 条；LIMIT 为 0 时返回空 QueryResult。ASK、CONSTRUCT、
  DESCRIBE 不接受这些修饰符，WHERE 花括号后仍拒绝任何后缀。
- CONSTRUCT { 三元组模板... } WHERE { 模式体 }：WHERE 模式体与
  SELECT/ASK 完全同语法、同语义；模板由一个或多个固定三项的三元组模板
  组成，模板之间用点号分隔，末尾点号可省略。模板主语/宾语为变量或常量，
  谓语只接受已声明的常量属性名（不接受变量谓语、属性路径、OPTIONAL、
  FILTER 或 UNION）。对每个通过 WHERE 条件的最终绑定逐项实例化模板；
  主语或宾语变量在该绑定中未绑定时不生成对应三元组，其余模板继续处理。
  结果为按字典序去重排序的 Triple 元组，不写回模型。
- DESCRIBE (变量... | *) WHERE { 模式体 }：投影位置只接受一个或多个
  互不重复的查询变量，或单独一个 '*'；WHERE 模式体与 SELECT/ASK 完全同
  语法、同语义。'*' 表示描述每个最终绑定中当前已绑定的全部变量值；指定
  变量时分别取其绑定值，未绑定变量在本次解中忽略。对每个待描述名称 N，
  结果取显式与推理三元组并集中主语或宾语为 N 的全部三元组；所有解产生的
  描述合并后按 (主语, 谓语, 宾语) 字典序去重排序。无匹配绑定、变量均未
  绑定或描述集合为空时返回空元组；查询不修改模型，重复执行结果一致。

查询在 OntologyModel.triples（显式 + 推理三元组）上做嵌套循环连接匹配，
同一变量跨模式绑定同一字符串，一条事实可被多个模式复用。
任何词法或语法错误都抛出带字符位置、模式序号或具体谓语的 OntologyError。
"""

from __future__ import annotations

import json
from collections import deque
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
_TOK_HAT = "HAT"
_TOK_SLASH = "SLASH"
_TOK_PIPE = "PIPE"
_TOK_PLUS = "PLUS"
_TOK_QUESTION = "QUESTION"
_TOK_VAR = "VAR"
_TOK_NAME = "NAME"
_TOK_STRING = "STRING"

# 普通词法模式下切分名称的定界符（与历史词法完全一致）
_DELIMITERS = frozenset('{}."?*')
# FILTER 圆括号内额外识别为独立 token 的字符；括号外这些字符仍是名称的一部分，
# 以保持不含 OPTIONAL/FILTER 的查询词法与旧行为完全一致。
_FILTER_DELIMITERS = frozenset("()!=")

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

    rows 已按投影取值去重；无 ORDER BY 时按投影字典序排列，有 ORDER BY 时
    按排序键稳定排序（键全部相同保持投影字典序），再应用 OFFSET 与 LIMIT；
    无匹配或 LIMIT 为 0 时为空元组。
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


_MODE_NORMAL = "normal"
_MODE_PATH = "path"

# 路径模式下切分名称的定界符：空白与全部路径/模式结构字符；
# '!'、'=' 在路径模式下仍允许出现在名称里（FILTER 语义不会进入谓语槽）。
_PATH_NAME_DELIMITERS = frozenset('{}."*^/|+?() \t\n\r\x0b\x0c')


class _Lexer:
    """带模式的字符流词法分析器（按需逐个产出 token）。

    - 普通模式逐字节复刻历史词法：{}."?* 为独立 token，FILTER 圆括号区间内
      ()!= 为独立 token，其余字符（包括 ^ / | + 以及区间外的括号、叹号、等号）
      都可以是名称的一部分；
    - 路径模式只在三元组谓语槽开启：^ / | + 与圆括号成为路径运算符，
      单独出现的 '?'（后不随变量字符）成为量词 QUESTION。'*' 在两种模式下
      都是 STAR（投影星号或路径量词由解析器区分）。
    模式在读完主语后、解析谓语前切换，宾语与后续 token 仍按普通模式切分，
    因此主语/宾语位置的旧词法（如名为 'a/b' 的常量）完全不变。
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.pos = 0
        self.mode = _MODE_NORMAL
        self.filter_spans = _find_filter_spans(text)
        self.span_starts = {start for start, _ in self.filter_spans}
        self.span_i = 0

    def rewind(self, pos: int) -> None:
        # 重置字符位置与 FILTER 区间游标：区间判定只随位置单调前进，
        # 回退后需要从头重新对齐。
        self.pos = pos
        self.span_i = 0

    def set_mode(self, mode: str) -> None:
        self.mode = mode

    def _in_filter(self, pos: int) -> bool:
        while self.span_i < len(self.filter_spans) and pos > self.filter_spans[self.span_i][1]:
            self.span_i += 1
        return (
            self.span_i < len(self.filter_spans)
            and self.filter_spans[self.span_i][0] <= pos <= self.filter_spans[self.span_i][1]
        )

    def next(self):
        """返回下一个 token；输入结束时返回 None。"""
        text = self.text
        n = self.n
        i = self.pos
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            self.pos = i
            return None
        ch = text[i]
        if self.mode == _MODE_PATH:
            return self._next_path(i, ch, text, n)
        return self._next_normal(i, ch, text, n)

    def _next_normal(self, i: int, ch: str, text: str, n: int) -> _Token:
        if ch == "{":
            self.pos = i + 1
            return _Token(_TOK_LBRACE, ch, i)
        if ch == "}":
            self.pos = i + 1
            return _Token(_TOK_RBRACE, ch, i)
        if ch == ".":
            self.pos = i + 1
            return _Token(_TOK_DOT, ch, i)
        if ch == "*":
            self.pos = i + 1
            return _Token(_TOK_STAR, ch, i)
        if self._in_filter(i):
            if ch == "(":
                self.pos = i + 1
                return _Token(_TOK_LPAREN, ch, i)
            if ch == ")":
                self.pos = i + 1
                return _Token(_TOK_RPAREN, ch, i)
            if ch == "!":
                if i + 1 < n and text[i + 1] == "=":
                    self.pos = i + 2
                    return _Token(_TOK_NE, "!=", i)
                self.pos = i + 1
                return _Token(_TOK_BANG, "!", i)
            if ch == "=":
                self.pos = i + 1
                return _Token(_TOK_EQ, "=", i)
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
            self.pos = j
            return _Token(_TOK_VAR, text[start:j], start)
        if ch == '"':
            return self._next_string(i, text, n)
        # 普通名称：FILTER 区间内额外以 ()!= 为界，区间外它们仍是名称字符
        delimiters = _DELIMITERS | _FILTER_DELIMITERS if self._in_filter(i) else _DELIMITERS
        start = i
        j = i
        while j < n and not text[j].isspace() and text[j] not in delimiters:
            # 'FILTER(' 无空格时，左括号是独立区间起点，名称只取到 FILTER
            if j > start and j in self.span_starts:
                break
            j += 1
        self.pos = j
        return _Token(_TOK_NAME, text[start:j], start)

    def _next_path(self, i: int, ch: str, text: str, n: int) -> _Token:
        simple = {
            "{": _TOK_LBRACE,
            "}": _TOK_RBRACE,
            ".": _TOK_DOT,
            "*": _TOK_STAR,
            "(": _TOK_LPAREN,
            ")": _TOK_RPAREN,
            "^": _TOK_HAT,
            "/": _TOK_SLASH,
            "|": _TOK_PIPE,
            "+": _TOK_PLUS,
        }
        if ch in simple:
            self.pos = i + 1
            return _Token(simple[ch], ch, i)
        if ch == "?":
            # 后随变量字符时仍是变量（如宾语 ?y），否则才是路径量词
            if i + 1 < n and _is_var_char(text[i + 1]):
                j = i + 1
                while j < n and _is_var_char(text[j]):
                    j += 1
                self.pos = j
                return _Token(_TOK_VAR, text[i:j], i)
            self.pos = i + 1
            return _Token(_TOK_QUESTION, "?", i)
        if ch == '"':
            return self._next_string(i, text, n)
        start = i
        j = i
        while (
            j < n
            and not text[j].isspace()
            and text[j] not in _PATH_NAME_DELIMITERS
        ):
            j += 1
        self.pos = j
        return _Token(_TOK_NAME, text[start:j], start)

    def _next_string(self, i: int, text: str, n: int) -> _Token:
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
        self.pos = j + 1
        return _Token(_TOK_STRING, value, start)


class _Path:
    """谓语位置上编译后的属性路径：tree 为路径 AST，pos 为起始字符位置。

    AST 节点（嵌套元组，可哈希）：
    - ("edge", 属性名)：单条属性边（含 JSON 字符串形式的常量谓语）；
    - ("inv", 子树)：逆向，翻转节点对；
    - ("seq", 左, 右)：序列，关系复合；
    - ("alt", 左, 右)：选择，节点对取并集；
    - ("zeroone", 子树)：零次或一次；
    - ("zero", 子树)：零次或多次；
    - ("oneplus", 子树)：一次或多次。
    """

    __slots__ = ("tree", "pos")

    def __init__(self, tree: tuple, pos: int) -> None:
        self.tree = tree
        self.pos = pos


def _pending_item_pos(item) -> int:
    """pending 槽中主语/宾语为 (种类, 值, 位置) 元组，谓语为 _Path。"""
    if isinstance(item, _Path):
        return item.pos
    return item[2]


class _Parser:
    """把 token 流编译为 (投影变量名列表, 模式子句列表, 是否星号投影, 解序列修饰符)。

    模式子句为三元组：
    - (_CLAUSE_BGP, patterns, None)
    - (_CLAUSE_OPTIONAL, patterns, None)
    - (_CLAUSE_FILTER, None, 表达式元组)
    - (_CLAUSE_UNION, branches, None)：branches 为分支模式列表的列表
    每个三元组模式为 (主语项, 谓语, 宾语项)，主语/宾语为 (种类, 值, 位置)，
    谓语为 _Path（普通属性名是只含一个 edge 节点的路径）。
    模式序号在整个 WHERE 体内连续编号，BGP、OPTIONAL 与 UNION 分支中的
    三元组一并计数；FILTER 不占用三元组序号。
    解序列修饰符为 (排序键, LIMIT, OFFSET)：排序键为 ((变量名, 是否降序), ...)
    或 None（无 ORDER BY）；LIMIT 为非负整数或 None（不截断）；OFFSET 为非负
    整数（默认 0）。只有 SELECT 的 parse 会返回修饰符，其余入口不接受后缀。
    """

    def __init__(self, text: str, lexer: "_Lexer", properties: frozenset) -> None:
        self._text = text
        self._lexer = lexer
        self._queue = deque()
        self._properties = frozenset(properties)
        # 三元组模式序号在整个 WHERE 体内连续编号（含 OPTIONAL 块内模式）
        self._pattern_index = 0
        # 上一个已消费 token；谓语槽开始的原始字符位置
        self._last = None
        self._pred_start = len(text)

    def _fill(self, count: int = 0) -> None:
        """保证预读队列至少含 count+1 个 token（EOF 后队列不再增长）。"""
        while len(self._queue) <= count:
            tok = self._lexer.next()
            if tok is None:
                break
            self._queue.append(tok)

    def _peek(self) -> _Token:
        self._fill(0)
        return self._queue[0]

    def _peek2(self):
        self._fill(1)
        return self._queue[1] if len(self._queue) > 1 else None

    def _eof(self) -> bool:
        self._fill(0)
        return not self._queue

    def _advance(self) -> None:
        self._fill(0)
        self._last = self._queue.popleft()

    def _error(self, message: str, pos: int) -> "OntologyError":
        return OntologyError(f"{message}（字符位置 {pos}）")

    def _here_pos(self) -> int:
        return self._peek().pos if not self._eof() else len(self._text)

    def _next_is(self, kind: str) -> bool:
        tok = self._peek2()
        return tok is not None and tok.kind == kind

    def _enter_path_mode(self) -> None:
        """谓语槽开始：切到路径词法模式并重切尚未按该模式读取的字符。"""
        self._lexer.rewind(self._pred_start)
        self._lexer.set_mode(_MODE_PATH)
        self._queue.clear()

    def _try_constant_predicate(self, start: int):
        """按旧词法读取谓语槽的一整个名称：若恰为已声明属性则作为常量边。

        这样名称中含路径字符（如已声明属性 'a/b'、'x(y)'）的旧查询不会被
        新路径规则拆散；普通属性名也走这条常量边，结果与单节点路径一致。
        返回 None 表示该名称不是已声明属性，应改按属性路径解析。
        """
        text = self._text
        n = len(text)
        j = start
        while j < n and not text[j].isspace() and text[j] not in '{}."?*':
            j += 1
        name = text[start:j]
        k = j
        while k < n and text[k].isspace():
            k += 1
        following = text[k] if k < n else ""
        glued_qvar = (
            following == "?" and k + 1 < n and _is_var_char(text[k + 1])
        )
        # 名称后（允许隔着空白）紧跟路径运算符时按路径解析：
        # knows*、knows+、knows/b、a|b 以及空格形式 knows * 等。
        # 裸 '?'（后不随变量字符，如 knows?）是路径量词；紧接 '?变量'
        # （knows?y）则是旧词法“谓语 knows + 宾语变量 ?y”的合法写法。
        bare_question = following == "?" and not glued_qvar
        path_continuation = following in "*/|+" or bare_question
        declared = bool(name) and name in self._properties
        if declared and not path_continuation and (
            glued_qvar or following in (".", '"', "}", "?") or following == ""
        ):
            self._lexer.rewind(j)  # 留在普通模式，宾语按旧词法切分
            self._queue.clear()
            return _Path(("edge", name), start)
        return None

    def _leave_path_mode(self) -> None:
        """谓语槽结束：终止符（宾语/点/右花括号）按普通模式重新切分。"""
        self._lexer.set_mode(_MODE_NORMAL)
        if self._queue:
            self._lexer.rewind(self._queue[0].pos)
            self._queue.clear()

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
        self._advance()
        return tok

    def parse(self) -> Tuple[Tuple[str, ...], List[tuple], bool, tuple]:
        self._expect_keyword("SELECT")
        projection, star = self._parse_projection()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._advance()
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号；其后只允许解序列修饰符
        modifiers = self._parse_solution_modifiers()
        self._validate_projection(projection, clauses)
        order_keys = self._validate_order_keys(modifiers[0], projection, clauses, star)
        return (
            tuple(name for name, _ in projection),
            clauses,
            star,
            (order_keys, modifiers[1], modifiers[2]),
        )

    def parse_ask(self) -> List[tuple]:
        """解析 ASK WHERE { ... }，返回模式子句列表（无投影）。

        ASK 不做投影：关键字 ASK 之后必须紧跟 WHERE，花括号之后不允许
        任何后缀成分；SELECT、变量列表或 '*' 都会按缺少关键字/未知成分报错。
        """
        self._expect_keyword("ASK")
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._advance()
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        return clauses

    def parse_construct(self) -> Tuple[List[tuple], List[tuple]]:
        """解析 CONSTRUCT { 模板... } WHERE { 模式体 }，返回 (模板列表, 模式子句列表)。

        模板为 (主语项, 谓语属性名, 宾语项) 三元组：主语/宾语为 (种类, 值, 位置)，
        谓语是已声明属性名的常量字符串。WHERE 模式体与 SELECT/ASK 完全同语法；
        花括号之后不允许任何后缀成分。
        """
        self._expect_keyword("CONSTRUCT")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("CONSTRUCT 后缺少左花括号 '{'", self._here_pos())
        lbrace_pos = self._peek().pos
        self._advance()
        templates = self._parse_template_body(lbrace_pos)
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._advance()
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        return templates, clauses

    def _parse_template_body(self, lbrace_pos: int) -> List[tuple]:
        """解析 CONSTRUCT '{' 之后直到匹配 '}' 的三元组模板（已消费 '}'）。

        模板之间用点号分隔，末尾点号可省略；主语/宾语为变量或常量，
        谓语只接受已声明的常量属性名：变量谓语、属性路径写法与未声明
        属性都在这里报错。模板序号从 0 开始，用于错误定位。
        """
        templates: List[tuple] = []
        pending: List[Tuple[Any, Any, Any]] = []
        prev_dot = False

        def finish_template() -> None:
            index = len(templates)
            if len(pending) != 3:
                pos = _pending_item_pos(pending[-1]) if pending else lbrace_pos
                raise OntologyError(
                    f"CONSTRUCT 模板 {index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            templates.append((s, p[1], o))
            pending.clear()

        while True:
            if self._eof():
                raise self._error("CONSTRUCT 模板缺少右花括号 '}'", len(self._text))
            tok = self._peek()

            if tok.kind == _TOK_RBRACE:
                if pending:
                    finish_template()
                self._advance()
                break

            if tok.kind == _TOK_DOT:
                if pending:
                    finish_template()
                elif prev_dot or not templates:
                    raise self._error(
                        "点号 '.' 只能出现在一条完整三元组模板之后",
                        tok.pos,
                    )
                prev_dot = True
                self._advance()
                continue

            if len(pending) == 3:
                raise self._error(
                    f"CONSTRUCT 模板 {len(templates)} 项数过多："
                    f"模板之间需要用 '.' 分隔",
                    tok.pos,
                )
            if len(pending) == 1:
                # 谓语槽：只接受已声明的常量属性名；变量谓语、属性路径
                # 写法（* ? + ^ / | 与圆括号）与字符串常量一律拒绝。
                if tok.kind == _TOK_VAR:
                    raise OntologyError(
                        f"CONSTRUCT 模板 {len(templates)} 的谓语不能是变量 "
                        f"{tok.value}（字符位置 {tok.pos}）"
                    )
                if tok.kind != _TOK_NAME:
                    raise self._error(
                        f"CONSTRUCT 模板 {len(templates)} 的谓语必须是已声明的"
                        f"常量属性名，不接受属性路径或字符串常量",
                        tok.pos,
                    )
                if tok.value not in self._properties:
                    raise OntologyError(
                        f"CONSTRUCT 模板 {len(templates)} 的谓语引用了未声明的"
                        f"属性 {tok.value!r}（字符位置 {tok.pos}）"
                    )
                pending.append(("P", tok.value, tok.pos))
                self._advance()
            elif tok.kind == _TOK_VAR:
                pending.append((_VAR, tok.value, tok.pos))
                self._advance()
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                pending.append((_LIT, tok.value, tok.pos))
                self._advance()
            else:
                raise self._error(
                    f"CONSTRUCT 模板 {len(templates)} 中存在未知语句成分"
                    f" {tok.value!r}",
                    tok.pos,
                )
            prev_dot = False

        if not templates:
            raise OntologyError(
                f"CONSTRUCT 花括号内的三元组模板为空（字符位置 {lbrace_pos}）"
            )
        return templates

    def parse_describe(self) -> Tuple[Tuple[str, ...], List[tuple], bool]:
        """解析 DESCRIBE (变量... | *) WHERE { ... }。

        返回 (投影变量名元组, 模式子句列表, 是否星号投影)。投影位置只接受
        一个或多个互不重复的查询变量，或单独一个 '*'；与 SELECT 不同，
        DESCRIBE 不要求投影变量出现在模式中——这类变量在任何解中都未绑定，
        按“未绑定变量在本次解中忽略”的语义处理。WHERE 模式体与 SELECT/ASK
        完全同语法；花括号之后不允许任何后缀成分。
        """
        self._expect_keyword("DESCRIBE")
        projection, star = self._parse_describe_projection()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._advance()
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号
        if not self._eof():
            tok = self._peek()
            raise self._error(f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos)
        return tuple(name for name, _ in projection), clauses, star

    def _parse_describe_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        """解析 DESCRIBE 后的投影：一个或多个变量，或单独一个 '*'。"""
        if self._eof():
            raise self._error("DESCRIBE 后缺少投影变量或 '*'", len(self._text))
        if self._peek().kind == _TOK_STAR:
            self._advance()
            # '*' 后必须紧跟 WHERE，不允许与变量混写
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
                    f"DESCRIBE 后只能出现变量或 '*'，遇到未知语句成分 {tok.value!r}",
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
            raise self._error("DESCRIBE 后缺少投影变量或 '*'", self._here_pos())
        return names, False

    def _parse_projection(self) -> Tuple[List[Tuple[str, int]], bool]:
        if self._eof():
            raise self._error("SELECT 后缺少投影变量或 '*'", len(self._text))
        if self._peek().kind == _TOK_STAR:
            self._advance()
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
            self._advance()
        if not names:
            raise self._error("SELECT 后缺少投影变量或 '*'", self._here_pos())
        return names, False

    # ---------- 解序列修饰符（仅 SELECT，WHERE 右花括号之后） ----------
    #
    #   修饰符 := (ORDER BY 排序键+)? (LIMIT 非负整数)? (OFFSET 非负整数)?
    #   排序键 := 变量 | ASC(变量) | DESC(变量)
    # 三者均可选、各至多一次，且必须按上述顺序出现；关键字只接受大写。
    # 普通词法下 '('、')' 不是独立 token：ASC(?x) 被切为 NAME 'ASC('、
    # VAR '?x'、NAME ')'，ASC (?x) 被切为 NAME 'ASC'、NAME '('、VAR、NAME ')'，
    # 排序函数按这两种形态识别，其余含括号的名称一律报错。

    def _parse_solution_modifiers(self) -> tuple:
        """解析 WHERE 模式体之后的 ORDER BY / LIMIT / OFFSET，返回
        (排序键列表, LIMIT, OFFSET)；排序键为 [(变量名, 位置, 是否降序)] 或
        None（无 ORDER BY），LIMIT 为 int 或 None，OFFSET 默认为 0。"""
        order_keys = None
        limit = None
        offset = 0
        # stage：0 还允许 ORDER BY，1 还允许 LIMIT，2 还允许 OFFSET，3 全部结束
        stage = 0
        while not self._eof():
            tok = self._peek()
            if tok.kind != _TOK_NAME:
                raise self._error(
                    f"右花括号后存在未知语句成分 {tok.value!r}", tok.pos
                )
            word = tok.value
            upper = word.upper()
            if upper in ("ORDER", "LIMIT", "OFFSET") and word != upper:
                raise OntologyError(
                    f"修饰符关键字 {upper} 必须大写，收到 {word!r}"
                    f"（字符位置 {tok.pos}）"
                )
            if word == "ORDER":
                if stage != 0:
                    raise OntologyError(
                        "修饰符 ORDER BY 重复出现或位置错误："
                        "必须位于 LIMIT 与 OFFSET 之前"
                        f"（字符位置 {tok.pos}）"
                    )
                self._advance()
                order_keys = self._parse_order_by_keys()
                stage = 1
            elif word == "LIMIT":
                if stage > 1:
                    raise OntologyError(
                        "修饰符 LIMIT 重复出现或位置错误："
                        "必须位于 OFFSET 之前且至多一次"
                        f"（字符位置 {tok.pos}）"
                    )
                self._advance()
                limit = self._parse_modifier_integer("LIMIT")
                stage = 2
            elif word == "OFFSET":
                if stage > 2:
                    raise OntologyError(
                        f"修饰符 OFFSET 重复出现（字符位置 {tok.pos}）"
                    )
                self._advance()
                offset = self._parse_modifier_integer("OFFSET")
                stage = 3
            else:
                raise self._error(
                    f"右花括号后存在未知语句成分 {word!r}", tok.pos
                )
        return (order_keys, limit, offset)

    def _parse_order_by_keys(self) -> list:
        """解析 ORDER 之后的 BY 与排序键列表（ORDER 已消费）。"""
        if (
            self._eof()
            or self._peek().kind != _TOK_NAME
            or self._peek().value != "BY"
        ):
            if (
                not self._eof()
                and self._peek().kind == _TOK_NAME
                and self._peek().value.upper() == "BY"
            ):
                raise OntologyError(
                    f"修饰符关键字 BY 必须大写，收到 {self._peek().value!r}"
                    f"（字符位置 {self._peek().pos}）"
                )
            raise self._error("ORDER 后缺少关键字 BY", self._here_pos())
        self._advance()

        keys: list = []
        seen = set()
        while not self._eof():
            tok = self._peek()
            if tok.kind == _TOK_VAR:
                name, pos, descending = tok.value, tok.pos, False
                self._advance()
            elif (
                tok.kind == _TOK_NAME
                and tok.value not in ("ORDER", "LIMIT", "OFFSET")
            ):
                name, pos, descending = self._parse_order_function(tok)
            else:
                break
            if name in seen:
                raise OntologyError(
                    f"排序变量 {name} 重复出现（字符位置 {pos}）"
                )
            seen.add(name)
            keys.append((name, pos, descending))
        if not keys:
            raise OntologyError(
                "ORDER BY 后缺少排序键：需要变量、ASC(?v) 或 DESC(?v)"
                f"（字符位置 {self._here_pos()}）"
            )
        return keys

    def _parse_order_function(self, tok: _Token) -> tuple:
        """解析 ASC(?v) / DESC(?v) 排序键，tok 为函数名所在的 NAME token。

        普通词法下括号粘连在名称里：'ASC(' 是名称与左圆括号粘连的形态，
        'ASC' 后随独立的 NAME '(' 是带空白的形态；右括号总是独立的 NAME ')'。
        """
        value = tok.value
        if value.endswith("(") and "(" not in value[:-1] and ")" not in value:
            func, glued_paren = value[:-1], True
        elif "(" not in value and ")" not in value:
            func, glued_paren = value, False
        else:
            raise OntologyError(
                f"ORDER BY 中存在非法排序键 {value!r}：排序键必须是变量、"
                f"ASC(?v) 或 DESC(?v)（字符位置 {tok.pos}）"
            )
        upper = func.upper()
        if upper in ("ASC", "DESC") and func != upper:
            raise OntologyError(
                f"排序函数 {upper} 必须大写，收到 {func!r}"
                f"（字符位置 {tok.pos}）"
            )
        if func not in ("ASC", "DESC"):
            raise OntologyError(
                f"ORDER BY 只支持 ASC/DESC 排序函数，收到 {func!r}"
                f"（字符位置 {tok.pos}）"
            )
        self._advance()
        if not glued_paren:
            if (
                self._eof()
                or self._peek().kind != _TOK_NAME
                or self._peek().value != "("
            ):
                raise self._error(
                    f"排序函数 {func} 后缺少左圆括号 '('", self._here_pos()
                )
            self._advance()
        if self._eof() or self._peek().kind != _TOK_VAR:
            raise self._error(
                f"{func}(...) 中必须且只能出现一个变量", self._here_pos()
            )
        var_tok = self._peek()
        self._advance()
        if (
            self._eof()
            or self._peek().kind != _TOK_NAME
            or self._peek().value != ")"
        ):
            raise self._error(
                f"{func}({var_tok.value} 后缺少右圆括号 ')'", self._here_pos()
            )
        self._advance()
        return var_tok.value, var_tok.pos, func == "DESC"

    def _parse_modifier_integer(self, keyword: str) -> int:
        """解析 LIMIT/OFFSET 之后的非负十进制整数（关键字已消费）。"""
        if self._eof():
            raise OntologyError(
                f"{keyword} 后缺少非负十进制整数（字符位置 {len(self._text)}）"
            )
        tok = self._peek()
        if (
            tok.kind != _TOK_NAME
            or not tok.value
            or any(ch not in "0123456789" for ch in tok.value)
        ):
            raise OntologyError(
                f"{keyword} 只接受非负十进制整数，收到 {tok.value!r}"
                f"（字符位置 {tok.pos}）"
            )
        self._advance()
        return int(tok.value)

    def _validate_order_keys(self, order_keys, projection, clauses, star):
        """校验排序变量都在投影中，返回 ((变量名, 是否降序), ...) 或 None。"""
        if order_keys is None:
            return None
        if star:
            projected = set(_star_variables(clauses))
        else:
            projected = {name for name, _ in projection}
        for name, pos, _descending in order_keys:
            if name not in projected:
                raise OntologyError(
                    f"排序变量 {name} 未在投影中出现（字符位置 {pos}）"
                )
        return tuple((name, descending) for name, _pos, descending in order_keys)

    def _parse_group_body(self, optional: bool) -> List[tuple]:
        """解析 '{' 之后直到匹配 '}' 的模式组，返回子句列表（已消费 '}'）。"""
        scope = "OPTIONAL 块" if optional else "WHERE 模式体"
        clauses: List[tuple] = []
        pending: List[Tuple[Any, Any, Any]] = []
        # prev_dot：上一个 token 是否为点号（拒绝连续点号）；
        # clause_ok：当前位置是否允许出现 OPTIONAL/FILTER 子句
        # （组首、点号之后或前一子句之后）。
        prev_dot = False
        clause_ok = True
        lbrace_pos = self._last.pos

        def finish_pattern() -> None:
            if len(pending) != 3:
                pos = _pending_item_pos(pending[-1]) if pending else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            if not isinstance(p, _Path):
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
                if tok.kind == _TOK_VAR:
                    # 谓语位置的变量先作为槽位收集，交由 finish_pattern
                    # 按“先项数、后变量谓语”的既有顺序报错
                    pending.append((_VAR, tok.value, tok.pos))
                    self._advance()
                else:
                    # 谓语位置：优先按旧词法识别“恰好是已声明属性”的常量名
                    # （名称中允许含路径字符）；否则按属性路径解析。
                    self._pred_start = tok.pos
                    path = self._try_constant_predicate(tok.pos)
                    if path is None:
                        # 仅在该槽按路径模式重切词法，结束后宾语恢复普通词法。
                        self._enter_path_mode()
                        path = self._parse_path()
                        self._leave_path_mode()
                    pending.append(path)
            elif tok.kind == _TOK_VAR:
                pending.append((_VAR, tok.value, tok.pos))
                self._advance()
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                pending.append((_LIT, tok.value, tok.pos))
                self._advance()
            else:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 中存在未知语句成分"
                    f" {tok.value!r}",
                    tok.pos,
                )
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
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error(
                "OPTIONAL 后缺少左花括号 '{'",
                self._here_pos(),
            )
        self._advance()
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

    def _parse_union_clause(self) -> tuple:
        """解析 '{' 分支 '}' (UNION '{' 分支 '}')+，首个 '{' 为当前 token。

        连续 UNION 从左到右结合；并集满足结合律，因此扁平化为一个子句，
        分支按出现顺序编号（从 1 开始）用于错误定位。
        """
        branches: List[List[Tuple[Any, Any, Any]]] = []
        while True:
            index = len(branches) + 1
            self._advance()  # 消费分支的 '{'
            branches.append(self._parse_union_branch(index))
            if (
                not self._eof()
                and self._peek().kind == _TOK_NAME
                and self._peek().value == "UNION"
            ):
                self._advance()
                if self._eof() or self._peek().kind != _TOK_LBRACE:
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

    def _parse_union_branch(self, index: int) -> List[Tuple[Any, Any, Any]]:
        """解析 UNION 单个分支 '{' 之后直到匹配 '}' 的三元组模式（已消费 '}'）。

        分支内只允许一个或多个三元组模式，不得嵌套 UNION、OPTIONAL 或
        FILTER，也不得为空；模式之间与分支末尾沿用可选点号规则。
        """
        patterns: List[Tuple[Any, Any, Any]] = []
        pending: List[Tuple[Any, Any, Any]] = []
        prev_dot = False
        lbrace_pos = self._last.pos

        def finish_pattern() -> None:
            if len(pending) != 3:
                pos = _pending_item_pos(pending[-1]) if pending else lbrace_pos
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 项数不足："
                    f"需要主语、谓语、宾语三项（字符位置 {pos}）"
                )
            s, p, o = pending
            if not isinstance(p, _Path):
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 的谓语不能是变量 {p[1]}"
                    f"（字符位置 {p[2]}）"
                )
            patterns.append((s, p, o))
            pending.clear()
            self._pattern_index += 1

        while True:
            if self._eof():
                raise self._error(
                    f"UNION 分支 {index} 缺少右花括号 '}}'",
                    len(self._text),
                )
            tok = self._peek()

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
                if tok.kind == _TOK_VAR:
                    # 谓语位置的变量先作为槽位收集，交由 finish_pattern
                    # 按“先项数、后变量谓语”的既有顺序报错
                    pending.append((_VAR, tok.value, tok.pos))
                    self._advance()
                else:
                    # 谓语位置：优先按旧词法识别“恰好是已声明属性”的常量名
                    # （名称中允许含路径字符）；否则按属性路径解析。
                    self._pred_start = tok.pos
                    path = self._try_constant_predicate(tok.pos)
                    if path is None:
                        # 仅在该槽按路径模式重切词法，结束后宾语恢复普通词法。
                        self._enter_path_mode()
                        path = self._parse_path()
                        self._leave_path_mode()
                    pending.append(path)
            elif tok.kind == _TOK_VAR:
                pending.append((_VAR, tok.value, tok.pos))
                self._advance()
            elif tok.kind in (_TOK_NAME, _TOK_STRING):
                pending.append((_LIT, tok.value, tok.pos))
                self._advance()
            else:
                raise self._error(
                    f"三元组模式 {self._pattern_index} 中存在未知语句成分"
                    f" {tok.value!r}",
                    tok.pos,
                )
            prev_dot = False

        if not patterns:
            raise OntologyError(
                f"UNION 分支 {index} 为空：分支内没有三元组模式"
                f"（字符位置 {lbrace_pos}）"
            )
        return patterns

    # ---------- 属性路径解析（谓语位置） ----------
    #
    # 文法（'|' 优先级最低，'/' 次之，量词 ? * + 绑定到前一主项，'^' 为前缀）：
    #   alternative := sequence ('|' sequence)*
    #   sequence    := postfix ('/' postfix)*
    #   postfix     := primary ('?' | '*' | '+')?
    #   primary     := NAME | STRING | '(' alternative ')' | '^' primary
    # 路径只在三元组的谓语槽被调用，因此 '.'、'}' 天然作为路径结束符。

    def _parse_path(self) -> _Path:
        start = self._here_pos()
        tree = self._parse_path_alternative()
        return _Path(tree, start)

    def _parse_path_alternative(self) -> tuple:
        tree = self._parse_path_sequence()
        while not self._eof() and self._peek().kind == _TOK_PIPE:
            op = self._peek()
            self._advance()
            if self._path_at_end():
                raise self._error(
                    f"属性路径的选择运算符 '|' 后缺少路径（三元组模式 "
                    f"{self._pattern_index} 的谓语）",
                    op.pos,
                )
            right = self._parse_path_sequence()
            tree = ("alt", tree, right)
        return tree

    def _parse_path_sequence(self) -> tuple:
        tree = self._parse_path_postfix()
        while not self._eof() and self._peek().kind == _TOK_SLASH:
            op = self._peek()
            self._advance()
            if self._path_at_end():
                raise self._error(
                    f"属性路径的序列运算符 '/' 后缺少路径（三元组模式 "
                    f"{self._pattern_index} 的谓语）",
                    op.pos,
                )
            right = self._parse_path_postfix()
            tree = ("seq", tree, right)
        return tree

    def _parse_path_postfix(self) -> tuple:
        tree = self._parse_path_primary()
        if self._eof() or self._peek().kind not in (
            _TOK_QUESTION,
            _TOK_STAR,
            _TOK_PLUS,
        ):
            return tree
        quant = self._peek()
        self._advance()
        kind = {
            _TOK_QUESTION: "zeroone",
            _TOK_STAR: "zero",
            _TOK_PLUS: "oneplus",
        }[quant.kind]
        tree = (kind, tree)
        # 量词不得叠加（如 p**、p?+），这是不支持的路径写法
        if not self._eof() and self._peek().kind in (
            _TOK_QUESTION,
            _TOK_STAR,
            _TOK_PLUS,
        ):
            extra = self._peek()
            raise self._error(
                f"不支持的属性路径写法：量词 {extra.value!r} 不能叠加"
                f"（三元组模式 {self._pattern_index} 的谓语）",
                extra.pos,
            )
        return tree

    def _parse_path_primary(self) -> tuple:
        if self._eof():
            raise OntologyError(
                f"属性路径缺少属性名或子路径（三元组模式 "
                f"{self._pattern_index} 的谓语，字符位置 {len(self._text)}）"
            )
        tok = self._peek()
        if tok.kind == _TOK_HAT:
            hat = tok
            self._advance()
            if self._path_at_end():
                raise self._error(
                    f"属性路径的逆向符 '^' 后缺少属性名或子路径"
                    f"（三元组模式 {self._pattern_index} 的谓语）",
                    hat.pos,
                )
            return ("inv", self._parse_path_primary())
        if tok.kind == _TOK_LPAREN:
            lparen = tok
            self._advance()
            if not self._eof() and self._peek().kind == _TOK_RPAREN:
                raise self._error(
                    f"不支持的属性路径写法：圆括号分组为空"
                    f"（三元组模式 {self._pattern_index} 的谓语）",
                    lparen.pos,
                )
            tree = self._parse_path_alternative()
            if self._eof() or self._peek().kind != _TOK_RPAREN:
                raise self._error(
                    f"属性路径的圆括号不配对：缺少右圆括号 ')'"
                    f"（三元组模式 {self._pattern_index} 的谓语，"
                    f"左圆括号字符位置 {lparen.pos}）",
                    self._here_pos(),
                )
            self._advance()
            return tree
        if tok.kind == _TOK_NAME:
            self._advance()
            if tok.value not in self._properties:
                raise OntologyError(
                    f"三元组模式 {self._pattern_index} 的谓语引用了未声明的"
                    f"属性 {tok.value!r}（字符位置 {tok.pos}）"
                )
            return ("edge", tok.value)
        if tok.kind == _TOK_STRING:
            # 字符串常量谓语沿用旧的精确比较词法，不参与已声明属性校验
            self._advance()
            return ("edge", tok.value)
        raise self._error(
            f"属性路径中存在不支持的写法 {tok.value!r}：这里需要属性名、"
            f"'^' 或圆括号分组（三元组模式 {self._pattern_index} 的谓语）",
            tok.pos,
        )

    def _path_at_end(self) -> bool:
        """运算符之后是否已无可用的路径主项（EOF、模式/分组结束符或变量）。

        变量只能是谓语之后的宾语，绝不可能是路径的一部分，因此运算符后紧跟
        变量同样属于“运算符缺边”，按缺少路径报错而不是把变量误当路径主项。
        """
        if self._eof():
            return True
        return self._peek().kind in (
            _TOK_DOT,
            _TOK_RBRACE,
            _TOK_RPAREN,
            _TOK_SLASH,
            _TOK_PIPE,
            _TOK_QUESTION,
            _TOK_STAR,
            _TOK_PLUS,
            _TOK_VAR,
        )

    def _parse_filter_clause(self) -> tuple:
        """解析 FILTER ( 表达式 )，关键字为当前 token。"""
        self._advance()
        if self._eof() or self._peek().kind != _TOK_LPAREN:
            raise self._error(
                "FILTER 后缺少左圆括号 '('",
                self._here_pos(),
            )
        lparen = self._peek()
        self._advance()
        expr = self._parse_filter_expr(lparen.pos)
        if self._eof() or self._peek().kind != _TOK_RPAREN:
            raise self._error(
                "FILTER 表达式缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._advance()
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
            self._advance()
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

        if self._eof() or self._peek().kind not in (_TOK_EQ, _TOK_NE):
            raise self._error(
                "FILTER 比较表达式需要 '=' 或 '!=' 运算符",
                self._here_pos(),
            )
        op_tok = self._peek()
        self._advance()

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
        self._advance()
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
        self._advance()
        if self._eof() or self._peek().kind != _TOK_VAR:
            raise self._error(
                "BOUND(...) 中必须且只能出现一个变量",
                self._here_pos(),
            )
        var_tok = self._peek()
        self._advance()
        if self._eof() or self._peek().kind != _TOK_RPAREN:
            raise self._error(
                f"BOUND({var_tok.value} 后缺少右圆括号 ')'",
                self._here_pos(),
            )
        self._advance()
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


def _compile_query(text, properties: frozenset) -> Tuple[Tuple[str, ...], List[tuple], bool, tuple]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    lexer = _Lexer(text)
    return _Parser(text, lexer, frozenset(properties)).parse()


def _compile_ask(text, properties: frozenset) -> List[tuple]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    lexer = _Lexer(text)
    return _Parser(text, lexer, frozenset(properties)).parse_ask()


def _compile_construct(text, properties: frozenset) -> Tuple[List[tuple], List[tuple]]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    lexer = _Lexer(text)
    return _Parser(text, lexer, frozenset(properties)).parse_construct()


def _compile_describe(text, properties: frozenset) -> Tuple[Tuple[str, ...], List[tuple], bool]:
    if not isinstance(text, str):
        raise OntologyError(
            f"查询文本必须是 str 类型，收到 {type(text).__name__}"
        )
    lexer = _Lexer(text)
    return _Parser(text, lexer, frozenset(properties)).parse_describe()


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
    """按模式从左到右、首次出现的顺序收集全部变量（谓语为路径，不含变量）。"""
    order: List[str] = []
    seen = set()
    for s, _p, o in _all_patterns(clauses):
        for item in (s, o):
            if item[0] == _VAR and item[1] not in seen:
                seen.add(item[1])
                order.append(item[1])
    return tuple(order)


class _PathMatcher:
    """把属性路径 AST 求值为去重的节点对集合。

    边集合只来自构造时给定的事实并集（OntologyModel.triples：显式 + 派生）；
    序列取关系复合、选择取并集、逆向翻转节点对；p?/p* 的零次分支取
    “全部三元组中实际出现过的主语或宾语”上的恒等节点对，不引入新术语；
    p+/p* 按每个起点做带 visited 的深度优先搜索求传递闭包，访问过的节点
    不重复展开，环状数据上必然有限结束。
    各 AST 子树的结果按节点缓存，重复查询同一模型时求值过程完全一致。
    """

    __slots__ = ("_edges", "_nodes", "_cache")

    def __init__(self, facts) -> None:
        edges: dict = {}
        nodes: set = set()
        for fact in facts:
            edges.setdefault(fact.predicate, set()).add(
                (fact.subject, fact.object)
            )
            nodes.add(fact.subject)
            nodes.add(fact.object)
        self._edges = edges
        self._nodes = frozenset(nodes)
        self._cache: dict = {}

    def identity(self) -> frozenset:
        return frozenset((node, node) for node in self._nodes)

    def eval(self, tree: tuple) -> frozenset:
        cached = self._cache.get(tree)
        if cached is not None:
            return cached
        tag = tree[0]
        if tag == "edge":
            pairs = frozenset(self._edges.get(tree[1], ()))
        elif tag == "inv":
            pairs = frozenset((v, u) for u, v in self.eval(tree[1]))
        elif tag == "seq":
            left = self.eval(tree[1])
            right = self.eval(tree[2])
            # 以中段节点连接：先走左路径再走右路径
            right_by_mid: dict = {}
            for mid, end in right:
                right_by_mid.setdefault(mid, []).append(end)
            composed = set()
            for start, mid in left:
                for end in right_by_mid.get(mid, ()):  # 中段不接续即无复合结果
                    composed.add((start, end))
            pairs = frozenset(composed)
        elif tag == "alt":
            pairs = self.eval(tree[1]) | self.eval(tree[2])
        elif tag == "zeroone":
            pairs = self.eval(tree[1]) | self.identity()
        elif tag == "oneplus":
            pairs = self._closure_positive(self.eval(tree[1]))
        else:  # tag == "zero"
            base = self.eval(tree[1])
            pairs = self._closure_positive(base) | self.identity()
        self._cache[tree] = pairs
        return pairs

    @staticmethod
    def _closure_positive(base: frozenset) -> frozenset:
        """一层或多层的传递闭包：从每个起点沿 base 边做带 visited 的 DFS，
        环状数据上访问过的节点不会重复展开，必然有限结束。"""
        adjacency: dict = {}
        for start, end in base:
            adjacency.setdefault(start, []).append(end)
        reachable = set()
        for source in adjacency:
            seen = set()
            stack = list(adjacency[source])
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                reachable.add((source, node))
                stack.extend(adjacency.get(node, ()))
        return frozenset(reachable)


def _match_patterns(matcher: _PathMatcher, bindings: List[dict], patterns) -> List[dict]:
    """对一组三元组模式按序做内连接，返回所有扩展后的绑定。"""
    for s, p, o in patterns:
        pairs = matcher.eval(p.tree)
        next_bindings: List[dict] = []
        for binding in bindings:
            for subj, obj in pairs:  # 节点对已去重，同一事实可被多个模式复用
                if s[0] == _VAR:
                    if s[1] in binding and subj != binding[s[1]]:
                        continue
                elif subj != s[1]:
                    continue
                extended = dict(binding)
                if s[0] == _VAR:
                    extended[s[1]] = subj
                # 同一模式内主语/宾语可能是同一变量（如 ?x p ?x）
                if o[0] == _VAR:
                    if o[1] in extended and obj != extended[o[1]]:
                        continue
                elif obj != o[1]:
                    continue
                if o[0] == _VAR:
                    extended[o[1]] = obj
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


def _eval_clauses(matcher: _PathMatcher, clauses) -> List[dict]:
    """按子句顺序在 matcher 上求值，返回全部最终绑定（无匹配时为空列表）。

    SELECT 与 ASK 共用这一段求值逻辑：BGP 内连接、OPTIONAL 左连接、
    UNION 各分支独立匹配后合并、FILTER 按逻辑与过滤此前全部解。
    """
    bindings: List[dict] = [{}]
    for kind, patterns, expr in clauses:
        if kind == _CLAUSE_FILTER:
            bindings = [b for b in bindings if _eval_filter(expr, b)]
        elif kind == _CLAUSE_OPTIONAL:
            joined: List[dict] = []
            for binding in bindings:
                matches = _match_patterns(matcher, [dict(binding)], patterns)
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
                    unioned.extend(_match_patterns(matcher, [dict(binding)], branch))
            bindings = unioned
        else:
            bindings = _match_patterns(matcher, bindings, patterns)
        if not bindings:
            break
    return bindings


def run_query(model, text) -> QueryResult:
    """在 model.triples 上执行查询，返回去重排序并应用解序列修饰符后的 QueryResult。

    先按既有语义求值、投影、去重并按投影字典序排列；有 ORDER BY 时按排序键
    稳定排序（未绑定值在同一键下先于绑定值，DESC 对该键相反，全部键相同则
    保持投影字典序）；最后跳过 OFFSET 条并保留至多 LIMIT 条。
    """
    properties = frozenset(getattr(model, "declared_properties", ()))
    projection, clauses, star, modifiers = _compile_query(text, properties)
    if star:
        projection = _star_variables(clauses)
    order_keys, limit, offset = modifiers

    matcher = _PathMatcher(model.triples)
    bindings = _eval_clauses(matcher, clauses)

    rows = {tuple(binding.get(name) for name in projection) for binding in bindings}
    # None（未绑定）排在所有字符串之前，保证混合取值时字典序排序稳定。
    ordered = sorted(
        rows,
        key=lambda row: tuple((0, "") if value is None else (1, value) for value in row),
    )
    if order_keys:
        # 稳定排序：从最后一个排序键开始逐键排序，全部键相同的行保持
        # 上一步的投影字典序；DESC 只翻转该键（未绑定值随之排到最后）。
        for name, descending in reversed(order_keys):
            index = projection.index(name)
            ordered.sort(
                key=lambda row, i=index: (0, "") if row[i] is None else (1, row[i]),
                reverse=descending,
            )
    if offset:
        ordered = ordered[offset:]
    if limit is not None:
        ordered = ordered[:limit]
    return QueryResult(projection, ordered)


def run_ask(model, text) -> bool:
    """在 model.triples 上执行 ASK 查询：至少一个最终解返回 True，否则 False。"""
    properties = frozenset(getattr(model, "declared_properties", ()))
    clauses = _compile_ask(text, properties)
    matcher = _PathMatcher(model.triples)
    return bool(_eval_clauses(matcher, clauses))


def run_construct(model, text) -> tuple:
    """在 model.triples 上执行 CONSTRUCT 查询，返回去重排序后的 Triple 元组。

    WHERE 模式体与 SELECT/ASK 同语义求值；对每个最终绑定逐项实例化模板，
    主语或宾语变量未绑定时跳过该模板，其余模板继续。生成的三元组不写回
    模型，重复执行同一模型与查询结果一致。
    """
    from .model import Triple  # 延迟导入，避免与 model 模块循环依赖

    properties = frozenset(getattr(model, "declared_properties", ()))
    templates, clauses = _compile_construct(text, properties)
    matcher = _PathMatcher(model.triples)
    bindings = _eval_clauses(matcher, clauses)

    generated = set()
    for binding in bindings:
        for s, predicate, o in templates:
            subject = s[1] if s[0] == _LIT else binding.get(s[1])
            object_ = o[1] if o[0] == _LIT else binding.get(o[1])
            if subject is None or object_ is None:
                # 主语或宾语变量未绑定：不生成该三元组，继续处理其余模板
                continue
            generated.add(Triple(subject, predicate, object_))
    return tuple(sorted(generated))


def run_describe(model, text) -> tuple:
    """在 model.triples 上执行 DESCRIBE 查询，返回去重排序后的 Triple 元组。

    WHERE 模式体与 SELECT/ASK 同语义求值：'*' 对每个最终绑定取其当前已
    绑定的全部变量值作为待描述名称；指定变量时分别取其绑定值，未绑定变量
    在本次解中忽略。对每个待描述名称 N，收集显式与推理三元组并集中主语或
    宾语为 N 的全部三元组，不因来源是显式事实还是某条规则而有所区别；全部
    解的描述合并后按 (主语, 谓语, 宾语) 字典序去重排序。无匹配绑定、变量
    均未绑定或描述集合为空时返回空元组。查询不修改模型与已有证明，重复执行
    同一模型与查询结果一致。
    """
    properties = frozenset(getattr(model, "declared_properties", ()))
    projection, clauses, star = _compile_describe(text, properties)
    if star:
        projection = _star_variables(clauses)

    matcher = _PathMatcher(model.triples)
    bindings = _eval_clauses(matcher, clauses)

    names = set()
    if star:
        # 每个最终绑定只描述其中当前已绑定的变量值（与投影顺序无关）
        for binding in bindings:
            names.update(binding.values())
    else:
        for binding in bindings:
            for name in projection:
                value = binding.get(name)
                if value is not None:
                    names.add(value)

    described = set()
    for triple in model.triples:
        if triple.subject in names or triple.object in names:
            described.add(triple)
    return tuple(sorted(described))
