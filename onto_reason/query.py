"""SPARQL 风格基本图模式（BGP）查询，支持 OPTIONAL 左连接、FILTER 筛选、UNION 并集
与谓语位置的属性路径。

支持的语法（关键字只接受大写）：

    SELECT (投影项... | *) WHERE { 模式 ('.' 模式)* '.'? }
    投影项 := 变量
            | '(' 聚合 'AS' 变量 ')'
    聚合 := 'COUNT' '(' '*' ')'
          | 'COUNT' '(' 变量 ')'
          | 'COUNT' '(' 'DISTINCT' 变量 ')'
          | 'MIN' '(' 变量 ')'
          | 'MAX' '(' 变量 ')'
    ASK WHERE { 模式 ('.' 模式)* '.'? }
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
  投影全部变量；投影变量未绑定时结果行中以 None 占位。出现聚合项或
  GROUP BY 时不得使用星号。
- SELECT 投影除普通变量外还接受聚合项，统一写成 '(表达式 AS ?别名)'：
  COUNT(*)、COUNT(?v)、COUNT(DISTINCT ?v)、MIN(?v)、MAX(?v)。普通变量
  与聚合项可按任意顺序混排，输出变量严格按投影顺序；别名必须是不与任何
  普通投影变量重名的新变量，聚合别名之间也不得重复。其余函数名、星号
  用于 MIN/MAX、DISTINCT 用于 MIN/MAX、缺参数或多参数、缺 AS、别名非
  变量、括号不配对或括号内出现多余嵌套，均为语法错误。
- WHERE 右花括号之后还可选 GROUP BY ?变量+：组键必须全部作为普通变量
  出现在投影中、不得重复；出现聚合或分组时，投影中的每个普通变量都
  必须是组键（其余信息只能经聚合别名输出）。求值时先按既有语义求值与
  FILTER 过滤，再按组键值把解分组（未绑定的组键以 None 为值归为同一
  组），最后逐组计算聚合：有 GROUP BY 时每个不同组键值一行、无解则
  无行；无 GROUP BY 时所有解属于同一个组，即使没有解也产生一行。
  COUNT(*) 统计组内全部解；COUNT(?v) 只统计 ?v 已绑定的解；
  COUNT(DISTINCT ?v) 在已绑定值上按值去重计数（返回非负整数）；
  MIN(?v)/MAX(?v) 在组内已绑定的字符串值上按 Unicode 字典序取最小/
  最大值，没有已绑定值时为 None。聚合参数变量不要求出现在模式中。
- SELECT 在 WHERE 模式体右花括号之后还接受可选的解序列修饰符，按
  GROUP BY、HAVING、ORDER BY、LIMIT、OFFSET 的顺序出现，各自至多一次：
    SELECT 投影 WHERE { 模式体 }
      (GROUP BY 分组变量+)? (HAVING 条件)? (ORDER BY 排序键+)?
      (LIMIT n)? (OFFSET m)?
- GROUP BY 之后、ORDER BY 之前可出现至多一个 HAVING 子句，对分组聚合
  结果逐组筛选；HAVING 不得出现在其他位置，关键字只接受大写，重复、
  倒置，或在既无聚合投影又无 GROUP BY 的 SELECT 中使用，均抛
  OntologyError。HAVING 后是一个条件，或用大写 AND 连接的多个条件
  （按逻辑与筛选）：
    条件 := BOUND(值) | !BOUND(值) | 值 比较运算符 操作数
    值 := 组键变量 | 聚合别名 | 匿名聚合调用
    比较运算符 := = | != | < | <= | > | >=
  匿名聚合调用为 COUNT(*)、COUNT(?v)、COUNT(DISTINCT ?v)、MIN(?v)、
  MAX(?v)，不要求输出别名，不得使用其他函数，聚合参数变量不要求出现
  在 WHERE 中；比较的右操作数还可取同样的值形式、字符串常量或非负
  整数常量。普通变量必须是组键或投影中的聚合别名。COUNT 产生整数，
  MIN/MAX 产生字符串或 None；组键未绑定或任一比较值为 None 时比较
  为假，BOUND(值) 仅在值不是 None 时为真。数字按数值比较，字符串按
  Unicode 字典序比较，非 None 混合类型的 = 与顺序比较为假、!= 为真。
  HAVING 在分组聚合完成后逐组执行，不进入输出投影，也不改变 ORDER BY
  可引用的名称；被过滤的组不参与去重、排序、OFFSET 与 LIMIT。无
  GROUP BY 的聚合查询仍只有一个组，空解组也按 COUNT 为 0、MIN/MAX 为
  None 计算后再筛选；有 GROUP BY 时无解不产生组。合法查询没有组通过
  时返回空 QueryResult，而不是异常。
  排序键为投影变量 ?v、ASC(?v)（升序，与裸变量相同）或 DESC(?v)（降序）；
  同一排序变量只能出现一次，且必须出现在投影中（聚合查询下可以是组键
  普通变量或聚合别名；'*' 投影下为任一模式变量）。
  LIMIT 与 OFFSET 只接受非负十进制整数；LIMIT 省略时不截断，OFFSET 默认 0。
  执行时先按既有语义求值、投影（聚合时为分组聚合结果）、去重并按投影
  字典序排列；有 ORDER BY 时在此基础上做稳定排序：同一排序键下未绑定
  值（None）先于绑定值，DESC 对该键相反；多个排序键按出现顺序比较，
  全部相同则回到投影字典序。
  最后跳过 OFFSET 条并保留至多 LIMIT 条（LIMIT 0 得到空结果）。
  ASK、CONSTRUCT、DESCRIBE 不接受这些修饰符，WHERE 花括号后仍拒绝任何后缀。
- ASK 与 SELECT 共用同一 WHERE 模式体语法与求值语义，但不做投影：
  不接受 SELECT、变量列表或 '*'，WHERE 花括号之后也不允许任何后缀成分；
  至少存在一个满足全部条件的最终绑定时返回 True，否则返回 False。
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
_TOK_LT = "LT"
_TOK_LE = "LE"
_TOK_GT = "GT"
_TOK_GE = "GE"
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

# SELECT 解序列修饰符关键字（按允许出现的顺序；GROUP BY 只用于结构检查）
_MODIFIER_WORDS = ("GROUP", "HAVING", "ORDER", "LIMIT", "OFFSET")
# GROUP BY / ORDER BY 结构内的关键字（用于大小写校验）
_ORDER_WORDS = ("BY", "ASC", "DESC")
# 聚合投影支持的函数名（大写）；SELECT 头部的其余大写单词按未知成分报错
_AGGREGATE_WORDS = ("COUNT", "MIN", "MAX")

# 聚合类型
_AGG_COUNT = "COUNT"
_AGG_COUNT_DISTINCT = "COUNT_DISTINCT"
_AGG_MIN = "MIN"
_AGG_MAX = "MAX"


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
        tok, end = _scan_string_token(text, i)
        self.pos = end
        return tok


def _scan_string_token(text: str, start: int):
    """start 指向起始双引号，返回 (STRING token, 下一个字符位置)。

    与主词法的字符串规则完全一致：JSON 解码，未闭合或非法时抛词法错误。
    """
    n = len(text)
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
    return _Token(_TOK_STRING, value, start), j + 1


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


def _tail_tokens(text: str, start: int) -> List[_Token]:
    """对 WHERE 右花括号之后的修饰符区域做独立词法扫描。

    与主词法不同：'('、')' 始终是独立 token，'?' 起变量，其余连续非空白、
    非圆括号、非 '?' 的字符为一个单词（整数、关键字或未知成分都由解析器
    进一步甄别）。该区域不允许字符串常量，出现 '"' 时按普通单词字符处理，
    最终会在解析器处报未知语句成分。
    遇到大写单词 HAVING 时停止本词法，其后文本改由 _having_tokens 扫描
    （HAVING 条件区需要字符串常量、比较运算符与 COUNT(*) 的 '*'）。
    """
    tokens: List[_Token] = []
    n = len(text)
    i = start
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "?":
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            if j == i + 1:
                raise OntologyError(
                    f"词法错误：'?' 后必须至少跟随一个字母、数字或下划线"
                    f"（字符位置 {i}）"
                )
            tokens.append(_Token(_TOK_VAR, text[i:j], i))
            i = j
            continue
        if ch == "(":
            tokens.append(_Token(_TOK_LPAREN, ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token(_TOK_RPAREN, ch, i))
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in "()?":
            j += 1
        tokens.append(_Token(_TOK_NAME, text[i:j], i))
        if text[i:j] == "HAVING":
            tokens.extend(_having_tokens(text, j))
            break
        i = j
    return tokens


def _having_tokens(text: str, start: int) -> List[_Token]:
    """对 HAVING 关键字之后的条件与后续修饰符区域做词法扫描。

    与尾部修饰符词法的差异：'"' 起 JSON 字符串常量，'*' 是独立 token
    （COUNT(*) 的参数），'='、'!='、'<'、'<='、'>'、'>=' 是比较运算符，
    '!' 是 BOUND 的否定前缀；'('、')' 与 '?' 起变量的规则不变。该区域
    产出的 token 是 ORDER BY、LIMIT、OFFSET 所需词法的超集，因此 HAVING
    条件结束后可以用同一 token 序列继续解析既有修饰符。
    """
    tokens: List[_Token] = []
    n = len(text)
    i = start
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "?":
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            if j == i + 1:
                raise OntologyError(
                    f"词法错误：'?' 后必须至少跟随一个字母、数字或下划线"
                    f"（字符位置 {i}）"
                )
            tokens.append(_Token(_TOK_VAR, text[i:j], i))
            i = j
            continue
        if ch == "(":
            tokens.append(_Token(_TOK_LPAREN, ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token(_TOK_RPAREN, ch, i))
            i += 1
            continue
        if ch == "*":
            tokens.append(_Token(_TOK_STAR, ch, i))
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
        if ch == "<":
            if i + 1 < n and text[i + 1] == "=":
                tokens.append(_Token(_TOK_LE, "<=", i))
                i += 2
            else:
                tokens.append(_Token(_TOK_LT, "<", i))
                i += 1
            continue
        if ch == ">":
            if i + 1 < n and text[i + 1] == "=":
                tokens.append(_Token(_TOK_GE, ">=", i))
                i += 2
            else:
                tokens.append(_Token(_TOK_GT, ">", i))
                i += 1
            continue
        if ch == '"':
            tok, i = _scan_string_token(text, i)
            tokens.append(tok)
            continue
        j = i
        while j < n and not text[j].isspace() and text[j] not in '()*?!<>="':
            j += 1
        tokens.append(_Token(_TOK_NAME, text[i:j], i))
        i = j
    return tokens


def _head_tokens(text: str, start: int) -> List[_Token]:
    """对 SELECT 与 WHERE 之间的投影区做独立词法扫描。

    与尾部修饰符扫描类似：'('、')'、'*' 始终是独立 token，'?' 起变量，
    其余连续非空白、非圆括号、非 '*'、非 '?' 的字符为一个单词（变量名、
    COUNT/MIN/MAX/AS/DISTINCT/WHERE 等关键字或未知成分由解析器甄别）。
    投影区不允许字符串常量，'"' 按普通单词字符处理，最终按未知成分报错。
    """
    tokens: List[_Token] = []
    n = len(text)
    i = start
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "?":
            j = i + 1
            while j < n and _is_var_char(text[j]):
                j += 1
            if j == i + 1:
                # 裸 '?'（如 WHERE 体内的路径量词 p?）只可能出现在 WHERE
                # 之后，头部解析在 WHERE 处即停止，不会读到它；这里降级为
                # 普通单词而不是直接报词法错误。若头部中真出现裸 '?'，由
                # 解析器按未知语句成分/非法聚合参数拒绝。
                tokens.append(_Token(_TOK_NAME, "?", i))
                i += 1
                continue
            tokens.append(_Token(_TOK_VAR, text[i:j], i))
            i = j
            continue
        if ch == "(":
            tokens.append(_Token(_TOK_LPAREN, ch, i))
            i += 1
            continue
        if ch == ")":
            tokens.append(_Token(_TOK_RPAREN, ch, i))
            i += 1
            continue
        if ch == "*":
            tokens.append(_Token(_TOK_STAR, ch, i))
            i += 1
            continue
        if ch in "{}.\"":
            # 头部区域不允许出现这些模式体字符；产出单字符单词交由解析器
            # 按未知语句成分报错（WHERE 之后的同类字符不会进入头部扫描）。
            tokens.append(_Token(_TOK_NAME, ch, i))
            i += 1
            continue
        j = i
        # 与普通词法的名称定界符保持一致：'{}.' 与 '"' 也是单词边界，
        # 使 'WHERE{'、'*WHERE{' 等紧贴写法与基线行为相同；头部解析在
        # WHERE 处即停止，其后的定界符 token 不会被消费。
        while (
            j < n
            and not text[j].isspace()
            and text[j] not in "()*?{}.\""
        ):
            j += 1
        tokens.append(_Token(_TOK_NAME, text[i:j], i))
        i = j
    return tokens


def _head_error(message: str, pos: int) -> OntologyError:
    return OntologyError(f"{message}（字符位置 {pos}）")


def _parse_select_head(text: str, start: int):
    """解析 SELECT 之后、WHERE 之前的投影区。

    返回 (投影项列表, 是否星号投影, WHERE 字符位置)。投影项为：
    - ("v", 变量名, 字符位置)：普通投影变量；
    - ("a", 别名, 聚合种类, 参数变量或 None, 字符位置)：聚合项，
      聚合种类为 _AGG_COUNT / _AGG_COUNT_DISTINCT / _AGG_MIN / _AGG_MAX，
      COUNT(*) 的参数为 None。
    星号只能单独出现且紧接 WHERE；普通变量与 '(表达式 AS ?别名)' 聚合项
    可按任意顺序混排（输出变量按投影顺序），但别名必须是不与任何普通
    投影变量重名的新变量。其余词法、大小写、参数与嵌套形式问题一律抛
    OntologyError。
    """
    tokens = _head_tokens(text, start)
    if not tokens:
        raise _head_error(
            "查询缺少关键字 SELECT，或存在未知语句成分", len(text)
        )

    first = tokens[0]
    if first.kind != _TOK_NAME or first.value != "SELECT":
        if (
            first.kind == _TOK_NAME
            and first.value != first.value.upper()
            and first.value.upper() == "SELECT"
        ):
            raise _head_error(
                f"关键字 {first.value!r} 大小写不合规：只接受大写形式",
                first.pos,
            )
        raise _head_error(
            f"查询缺少关键字 SELECT，或存在未知语句成分 {first.value!r}",
            first.pos,
        )

    rest = tokens[1:]
    if not rest:
        raise _head_error("SELECT 后缺少投影变量、聚合项或 '*'", len(text))

    if rest[0].kind == _TOK_STAR:
        if (
            len(rest) >= 2
            and rest[1].kind == _TOK_NAME
            and rest[1].value == "WHERE"
        ):
            return [], True, rest[1].pos
        pos = rest[1].pos if len(rest) > 1 else len(text)
        raise _head_error("'*' 与 WHERE 之间存在未知语句成分", pos)

    items: List[tuple] = []
    plain_names: dict = {}
    alias_names: set = set()
    i = 0
    while i < len(rest):
        tok = rest[i]
        if tok.kind == _TOK_NAME and tok.value == "WHERE":
            if not items:
                raise _head_error("SELECT 后缺少投影变量或聚合项", tok.pos)
            return items, False, tok.pos
        if tok.kind == _TOK_LPAREN:
            item, i = _parse_aggregate_item(rest, i)
            alias = item[1]
            if alias in plain_names:
                raise _head_error(
                    f"聚合别名 {alias} 与普通投影变量重名：别名必须是新变量",
                    item[4],
                )
            if alias in alias_names:
                raise _head_error(f"聚合别名 {alias} 重复", item[4])
            alias_names.add(alias)
            items.append(item)
            continue
        if tok.kind == _TOK_VAR:
            if tok.value in plain_names:
                raise _head_error(f"投影变量 {tok.value} 重复", tok.pos)
            if tok.value in alias_names:
                raise _head_error(
                    f"投影变量 {tok.value} 与聚合别名重名：别名必须是新变量",
                    tok.pos,
                )
            plain_names[tok.value] = tok.pos
            items.append(("v", tok.value, tok.pos))
            i += 1
            continue
        if (
            tok.kind == _TOK_NAME
            and tok.value != tok.value.upper()
            and tok.value.upper() in ("WHERE", "AS", "DISTINCT") + _AGGREGATE_WORDS
        ):
            raise _head_error(
                f"关键字 {tok.value!r} 大小写不合规：只接受大写形式",
                tok.pos,
            )
        raise _head_error(
            f"SELECT 后只能出现变量或 '(表达式 AS ?别名)' 形式的聚合项，"
            f"遇到未知语句成分 {tok.value!r}",
            tok.pos,
        )
    raise _head_error("SELECT 投影之后缺少关键字 WHERE", len(text))


def _parse_aggregate_item(tokens: List[_Token], i: int):
    """解析一个聚合项 '( COUNT(*) | COUNT(?v) | COUNT(DISTINCT ?v) |
    MIN(?v) | MAX(?v) AS ?别名 )'，tokens[i] 为外层左圆括号。

    返回 (("a", 别名, 聚合种类, 参数变量或 None, 起始位置), 下一索引)。
    """
    start = tokens[i].pos
    i += 1
    if i >= len(tokens):
        raise _head_error("聚合项缺少右圆括号 ')' 或内容不完整", start)

    func = tokens[i]
    if func.kind != _TOK_NAME:
        raise _head_error(
            f"圆括号内必须是 COUNT/MIN/MAX 聚合表达式，"
            f"遇到未知语句成分 {func.value!r}",
            func.pos,
        )
    if func.value not in _AGGREGATE_WORDS:
        if (
            func.value != func.value.upper()
            and func.value.upper() in _AGGREGATE_WORDS + ("SUM", "AVG")
        ):
            raise _head_error(
                f"关键字 {func.value!r} 大小写不合规：只接受大写形式",
                func.pos,
            )
        raise _head_error(
            f"不支持的聚合函数 {func.value!r}：仅支持 COUNT、MIN、MAX",
            func.pos,
        )
    i += 1

    if i >= len(tokens) or tokens[i].kind != _TOK_LPAREN:
        pos = tokens[i].pos if i < len(tokens) else start
        raise _head_error(f"{func.value} 后缺少左圆括号 '('", pos)
    i += 1

    distinct = False
    if i < len(tokens) and tokens[i].kind == _TOK_NAME and tokens[i].value == "DISTINCT":
        distinct = True
        i += 1
    elif (
        i < len(tokens)
        and tokens[i].kind == _TOK_NAME
        and tokens[i].value != tokens[i].value.upper()
        and tokens[i].value.upper() == "DISTINCT"
    ):
        raise _head_error(
            f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式",
            tokens[i].pos,
        )

    if i >= len(tokens):
        raise _head_error(f"{func.value}(...) 的聚合参数不完整", start)
    arg_tok = tokens[i]

    if func.value == "COUNT":
        if distinct:
            if arg_tok.kind != _TOK_VAR:
                raise _head_error(
                    f"COUNT(DISTINCT ...) 中必须且只能出现一个变量，"
                    f"遇到 {arg_tok.value!r}",
                    arg_tok.pos,
                )
            kind = _AGG_COUNT_DISTINCT
            operand = arg_tok.value
        elif arg_tok.kind == _TOK_STAR:
            kind = _AGG_COUNT
            operand = None
        elif arg_tok.kind == _TOK_VAR:
            kind = _AGG_COUNT
            operand = arg_tok.value
        else:
            raise _head_error(
                f"COUNT 的参数形式非法：只接受 '*'、?变量 或 DISTINCT ?变量，"
                f"遇到 {arg_tok.value!r}",
                arg_tok.pos,
            )
    else:
        if distinct:
            raise _head_error(
                f"{func.value} 不支持 DISTINCT 参数：只接受单个变量",
                arg_tok.pos,
            )
        if arg_tok.kind != _TOK_VAR:
            raise _head_error(
                f"{func.value} 的参数形式非法：括号内必须且只能出现一个变量，"
                f"遇到 {arg_tok.value!r}",
                arg_tok.pos,
            )
        kind = _AGG_MIN if func.value == "MIN" else _AGG_MAX
        operand = arg_tok.value
    i += 1

    if i >= len(tokens) or tokens[i].kind != _TOK_RPAREN:
        pos = tokens[i].pos if i < len(tokens) else start
        raise _head_error(
            f"{func.value} 聚合参数或嵌套形式非法：参数只能是 '*'、"
            f"DISTINCT ?变量 或单个变量，圆括号内存在多余或缺少成分",
            pos,
        )
    i += 1

    if i >= len(tokens) or tokens[i].kind != _TOK_NAME or tokens[i].value != "AS":
        pos = tokens[i].pos if i < len(tokens) else start
        if (
            i < len(tokens)
            and tokens[i].kind == _TOK_NAME
            and tokens[i].value != tokens[i].value.upper()
            and tokens[i].value.upper() == "AS"
        ):
            raise _head_error(
                f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式",
                tokens[i].pos,
            )
        raise _head_error(
            f"聚合项必须以 'AS ?别名' 结尾，{func.value}(...) 之后缺少 AS",
            pos,
        )
    i += 1

    if i >= len(tokens) or tokens[i].kind != _TOK_VAR:
        pos = tokens[i].pos if i < len(tokens) else start
        raise _head_error("AS 后必须跟一个别名变量 ?名称", pos)
    alias_tok = tokens[i]
    alias = alias_tok.value
    i += 1

    if i >= len(tokens) or tokens[i].kind != _TOK_RPAREN:
        pos = tokens[i].pos if i < len(tokens) else start
        raise _head_error(
            f"聚合项 {alias} 缺少右圆括号 ')' 或括号内存在多余成分",
            pos,
        )
    i += 1
    return ("a", alias, kind, operand, start), i


class _Parser:
    """把 token 流编译为 (SELECT 投影项, 模式子句列表, 是否星号投影, 解序列修饰符)。

    SELECT 的关键字与投影区由独立的头部扫描器 (_head_tokens /
    _parse_select_head) 解析，WHERE 起恢复历史词法；ASK、CONSTRUCT、
    DESCRIBE 仍完全由历史词法解析。

    模式子句为三元组：
    - (_CLAUSE_BGP, patterns, None)
    - (_CLAUSE_OPTIONAL, patterns, None)
    - (_CLAUSE_FILTER, None, 表达式元组)
    - (_CLAUSE_UNION, branches, None)：branches 为分支模式列表的列表
    每个三元组模式为 (主语项, 谓语, 宾语项)，主语/宾语为 (种类, 值, 位置)，
    谓语为 _Path（普通属性名是只含一个 edge 节点的路径）。
    模式序号在整个 WHERE 体内连续编号，BGP、OPTIONAL 与 UNION 分支中的
    三元组一并计数；FILTER 不占用三元组序号。
    解序列修饰符为 (排序键元组, LIMIT 或 None, OFFSET)：排序键为
    (变量名, 是否降序, 字符位置) 三元组；只有 SELECT 的 parse 会返回
    非空修饰符，其余语句形态在右花括号后仍拒绝任何后缀。
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

    def parse(self):
        """解析 SELECT 查询，返回 (投影项, 模式子句, 是否星号, 解序列修饰符)。

        投影项为 ("v", 变量名, 位置) 或
        ("a", 别名, 聚合种类, 参数变量或 None, 位置)；星号投影时为空列表。
        解序列修饰符为 (分组键, HAVING 子句, 排序键, LIMIT 或 None, OFFSET)，
        分组键与排序键均为 (变量名, 字符位置) / (变量名, 是否降序, 字符位置)
        元组，HAVING 子句为 None 或 (关键字字符位置, 条件元组)。
        """
        # SELECT 关键字与整个投影区都由独立的头部扫描器切分：普通词法下
        # '(' 等字符可能是名称的一部分，头部扫描可以在不改动历史词法的
        # 前提下识别 '(聚合表达式 AS ?别名)'。模式体再从 WHERE 起恢复
        # 历史词法，保证空查询、小写 select 等错误与 WHERE 体行为不变。
        start = 0
        while start < len(self._text) and self._text[start].isspace():
            start += 1
        head, star, where_pos = _parse_select_head(self._text, start)
        # 模式体完全沿用历史词法：从 WHERE 关键字起重新切分。
        self._lexer.rewind(where_pos)
        self._queue.clear()
        self._expect_keyword("WHERE")
        if self._eof() or self._peek().kind != _TOK_LBRACE:
            raise self._error("WHERE 后缺少左花括号 '{'", self._here_pos())
        self._advance()
        clauses = self._parse_group_body(optional=False)
        # _parse_group_body 已消费右花括号；右花括号之后只允许解序列修饰符
        modifiers = self._parse_solution_modifiers(star)
        group_keys, having, order_keys, _limit, _offset = modifiers
        self._validate_select_head(head, star, group_keys, clauses)
        self._validate_having(head, star, group_keys, having)
        self._validate_order_keys(head, star, order_keys, clauses, group_keys)
        return head, clauses, star, modifiers

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

    def _validate_select_head(self, head, star: bool, group_keys, clauses) -> None:
        """聚合投影下的全部语义约束（无聚合时与历史投影校验等价）。

        - 普通投影变量必须在三元组模式中出现；
        - 出现聚合项时不得使用星号（星号情况在修饰符解析处已拒绝）；
        - GROUP BY 组键必须以普通变量投影、不得重复；
        - 有 GROUP BY 时，普通投影变量只能是组键；无 GROUP BY 时，投影
          不能只由普通变量与聚合项混合之外的形式构成——只要出现聚合项，
          每个普通变量都必须是组键。
        聚合参数变量不要求出现在模式中：未出现时它在每个解中均未绑定，
        COUNT(?v) 计为 0、MIN/MAX 为 None。
        """
        if star:
            used = set(_star_variables(clauses))
            # 星号投影下不允许出现聚合项或 GROUP BY（解析层已保证），
            # 这里仅保留与历史行为一致的防御性检查。
            if group_keys:  # pragma: no cover - 修饰符解析处已拒绝
                raise OntologyError(
                    "分组查询不得使用星号投影 '*'（字符位置 0）"
                )
            return

        used = set()
        for s, _p, o in _all_patterns(clauses):
            for item in (s, o):
                if item[0] == _VAR:
                    used.add(item[1])

        plain = [item for item in head if item[0] == "v"]
        aggs = [item for item in head if item[0] == "a"]
        grouped = bool(group_keys) or bool(aggs)

        for item in plain:
            _tag, name, pos = item
            if name not in used:
                raise OntologyError(
                    f"投影变量 {name} 未在任何三元组模式中出现（字符位置 {pos}）"
                )

        group_seen = set()
        for name, pos in group_keys:
            if name in group_seen:  # pragma: no cover - 解析层已拒绝重复
                raise OntologyError(
                    f"GROUP BY 分组键 {name} 重复（字符位置 {pos}）"
                )
            group_seen.add(name)
            match = next((item for item in plain if item[1] == name), None)
            if match is None:
                raise OntologyError(
                    f"GROUP BY 分组键 {name} 必须作为普通变量出现在投影中"
                    f"（字符位置 {pos}）"
                )

        if grouped:
            for item in plain:
                _tag, name, pos = item
                if name not in group_seen:
                    raise OntologyError(
                        f"投影变量 {name} 既不是 GROUP BY 组键也不是聚合别名："
                        f"聚合投影中的普通变量必须全部是分组键（字符位置 {pos}）"
                    )

    def _validate_order_keys(self, head, star, order_keys, clauses, group_keys) -> None:
        """排序变量必须出现在投影中（'*' 投影下为任一模式变量）。

        聚合投影下排序键可引用普通投影变量（即组键）或聚合别名。
        """
        if not order_keys:
            return
        if star:
            projected = set(_star_variables(clauses))
        else:
            projected = {item[1] for item in head}
        for name, _descending, pos in order_keys:
            if name not in projected:
                raise OntologyError(
                    f"排序变量 {name} 未在投影中出现（字符位置 {pos}）"
                )

    def _validate_having(self, head, star: bool, group_keys, having) -> None:
        """HAVING 子句的语义约束。

        HAVING 只能用于带聚合投影或 GROUP BY 的 SELECT；条件中的普通变量
        必须是 GROUP BY 组键或投影中的聚合别名（别名不得与组键、普通投影
        变量或其他别名重名由投影校验保证，因此两者不会混淆）。聚合参数
        变量不要求出现在模式中。
        """
        if having is None:
            return
        kw_pos, conditions = having
        aggs = [] if star else [item for item in head if item[0] == "a"]
        if not aggs and not group_keys:
            raise OntologyError(
                "HAVING 只能用于带聚合投影或 GROUP BY 的 SELECT 查询"
                f"（字符位置 {kw_pos}）"
            )
        key_names = {name for name, _pos in group_keys}
        alias_names = {item[1] for item in aggs}
        for cond in conditions:
            if cond[0] == "cmp":
                values = (cond[2], cond[3])
            else:
                values = (cond[1],)
            for value in values:
                if value[0] != "var":
                    continue
                name = value[1]
                if name not in key_names and name not in alias_names:
                    raise OntologyError(
                        f"HAVING 条件中的变量 {name} 既不是 GROUP BY 组键也不是"
                        f"聚合别名（字符位置 {value[2]}）"
                    )

    # ---------- SELECT 解序列修饰符（WHERE 右花括号之后） ----------
    #
    # 修饰符只作用于 SELECT：GROUP BY 分组键+、HAVING 条件、ORDER BY 排序键+、
    # LIMIT 非负整数、OFFSET 非负整数，按此顺序各自至多出现一次。右花括号之后
    # 的文本由独立的尾部词法扫描（'('、')' 是独立 token，'?' 起变量，其余连续
    # 非空白字符为单词），不影响主词法对模式体的既有切分；遇到 HAVING 关键字
    # 后改用 HAVING 词法（字符串常量、比较运算符、'*'）。

    def _parse_solution_modifiers(self, star: bool) -> tuple:
        """解析 WHERE 右花括号之后的修饰符序列。

        返回 (分组键, HAVING 子句, 排序键, LIMIT, OFFSET)：分组键为
        (变量名, 字符位置) 元组，HAVING 子句为 None 或 (关键字字符位置,
        条件元组)，排序键为 (变量名, 是否降序, 字符位置) 元组，LIMIT 缺省
        为 None（不截断），OFFSET 缺省为 0。self._last 此时是模式体的右
        花括号。修饰符按 GROUP BY、HAVING、ORDER BY、LIMIT、OFFSET 的
        顺序各至多出现一次。
        """
        tokens = _tail_tokens(self._text, self._last.pos + 1)
        group_keys: List[tuple] = []
        having = None
        order_keys: List[tuple] = []
        limit = None
        offset = 0
        group_pos = None
        # stage：0 可出现 GROUP/HAVING，1 可出现 HAVING/ORDER，2 可出现
        # ORDER/LIMIT，3 可出现 LIMIT/OFFSET，4 只能出现 OFFSET，5 全部结束
        stage = 0
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if tok.kind == _TOK_NAME and tok.value in _MODIFIER_WORDS:
                index = _MODIFIER_WORDS.index(tok.value)
                if index < stage:
                    raise OntologyError(
                        f"修饰符 {tok.value} 重复或顺序错误：应按 GROUP BY、"
                        f"HAVING、ORDER BY、LIMIT、OFFSET 的顺序各出现一次"
                        f"（字符位置 {tok.pos}）"
                    )
                stage = index + 1
                if tok.value == "GROUP":
                    group_pos = tok.pos
                    group_keys, i = self._parse_group_by(tokens, i)
                elif tok.value == "HAVING":
                    having, i = self._parse_having_clause(tokens, i)
                elif tok.value == "ORDER":
                    order_keys, i = self._parse_order_by(tokens, i)
                elif tok.value == "LIMIT":
                    limit, i = self._parse_int_modifier(tokens, i)
                else:
                    offset, i = self._parse_int_modifier(tokens, i)
            elif (
                tok.kind == _TOK_NAME
                and tok.value != tok.value.upper()
                and tok.value.upper()
                in _MODIFIER_WORDS + _ORDER_WORDS + ("DISTINCT",)
            ):
                raise OntologyError(
                    f"关键字 {tok.value!r} 大小写不合规：只接受大写形式"
                    f"（字符位置 {tok.pos}）"
                )
            else:
                raise OntologyError(
                    f"右花括号后存在未知语句成分 {tok.value!r}"
                    f"（字符位置 {tok.pos}）"
                )
        if star and group_keys:
            raise OntologyError(
                "分组查询不得使用星号投影 '*'（字符位置 "
                f"{group_pos if group_pos is not None else 0}）"
            )
        return (tuple(group_keys), having, tuple(order_keys), limit, offset)

    def _parse_having_clause(self, tokens: List[_Token], i: int):
        """解析 HAVING 条件子句，tokens[i] 为 HAVING；返回 (子句, 下一索引)。

        子句为 (关键字字符位置, 条件元组)；每个条件为：
        - ("bound", 值, 位置)：BOUND(值)；
        - ("not_bound", 值, 位置)：!BOUND(值)；
        - ("cmp", 运算符, 左值, 右值, 位置)：比较条件。
        值节点为 ("var", 变量名, 位置)、("agg", 聚合种类, 参数变量或 None,
        位置)、("lit", 字符串, 位置) 或 ("int", 非负整数, 位置)；常量只允许
        出现在比较的右操作数。多个条件用大写 AND 连接，按逻辑与筛选。
        """
        kw_pos = tokens[i].pos
        i += 1
        conditions = []
        cond, i = self._parse_having_condition(tokens, i)
        conditions.append(cond)
        while (
            i < len(tokens)
            and tokens[i].kind == _TOK_NAME
            and tokens[i].value == "AND"
        ):
            i += 1
            cond, i = self._parse_having_condition(tokens, i)
            conditions.append(cond)
        if (
            i < len(tokens)
            and tokens[i].kind == _TOK_NAME
            and tokens[i].value != tokens[i].value.upper()
            and tokens[i].value.upper() == "AND"
        ):
            raise OntologyError(
                f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式"
                f"（字符位置 {tokens[i].pos}）"
            )
        return (kw_pos, tuple(conditions)), i

    def _parse_having_condition(self, tokens: List[_Token], i: int):
        """解析单个 HAVING 条件，返回 (条件节点, 下一索引)。"""
        if i >= len(tokens):
            raise OntologyError(
                f"HAVING 后缺少条件（字符位置 {len(self._text)}）"
            )
        tok = tokens[i]

        # !BOUND(值)
        if tok.kind == _TOK_BANG:
            i += 1
            if (
                i >= len(tokens)
                or tokens[i].kind != _TOK_NAME
                or tokens[i].value != "BOUND"
            ):
                if (
                    i < len(tokens)
                    and tokens[i].kind == _TOK_NAME
                    and tokens[i].value != tokens[i].value.upper()
                    and tokens[i].value.upper() == "BOUND"
                ):
                    raise OntologyError(
                        f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式"
                        f"（字符位置 {tokens[i].pos}）"
                    )
                raise OntologyError(
                    f"HAVING 中 '!' 后只允许 BOUND(值) 形式"
                    f"（字符位置 {tok.pos}）"
                )
            i += 1
            value, i = self._parse_having_bound_value(tokens, i)
            return ("not_bound", value, tok.pos), i

        # BOUND(值)
        if tok.kind == _TOK_NAME and tok.value == "BOUND":
            i += 1
            value, i = self._parse_having_bound_value(tokens, i)
            return ("bound", value, tok.pos), i
        if (
            tok.kind == _TOK_NAME
            and tok.value != tok.value.upper()
            and tok.value.upper() == "BOUND"
        ):
            raise OntologyError(
                f"关键字 {tok.value!r} 大小写不合规：只接受大写形式"
                f"（字符位置 {tok.pos}）"
            )

        # 比较条件：左操作数必须是值（组键变量、聚合别名或匿名聚合调用）
        left, i = self._parse_having_value(tokens, i, allow_constant=False)
        if i >= len(tokens) or tokens[i].kind not in (
            _TOK_EQ,
            _TOK_NE,
            _TOK_LT,
            _TOK_LE,
            _TOK_GT,
            _TOK_GE,
        ):
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(
                "HAVING 比较条件需要 =、!=、<、<=、>、>= 运算符"
                f"（字符位置 {pos}）"
            )
        op = tokens[i]
        i += 1
        right, i = self._parse_having_value(tokens, i, allow_constant=True)
        return ("cmp", op.value, left, right, op.pos), i

    def _parse_having_bound_value(self, tokens: List[_Token], i: int):
        """消费 BOUND 后面的 '(' 值 ')'，返回 (值节点, 下一索引)。"""
        if i >= len(tokens) or tokens[i].kind != _TOK_LPAREN:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(f"BOUND 后缺少左圆括号 '('（字符位置 {pos}）")
        i += 1
        value, i = self._parse_having_value(tokens, i, allow_constant=False)
        if i >= len(tokens) or tokens[i].kind != _TOK_RPAREN:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(
                f"BOUND(...) 缺少右圆括号 ')'（字符位置 {pos}）"
            )
        i += 1
        return value, i

    def _parse_having_value(self, tokens: List[_Token], i: int, allow_constant: bool):
        """解析 HAVING 条件中的一个值或比较操作数，返回 (值节点, 下一索引)。

        allow_constant 时（比较的右操作数）还接受字符串常量与非负整数常量。
        """
        if i >= len(tokens):
            raise OntologyError(
                f"HAVING 条件缺少操作数（字符位置 {len(self._text)}）"
            )
        tok = tokens[i]
        if tok.kind == _TOK_VAR:
            return ("var", tok.value, tok.pos), i + 1
        if tok.kind == _TOK_NAME:
            if tok.value in _AGGREGATE_WORDS:
                return self._parse_having_aggregate(tokens, i)
            if (
                tok.value != tok.value.upper()
                and tok.value.upper() in _AGGREGATE_WORDS + ("SUM", "AVG")
            ):
                raise OntologyError(
                    f"关键字 {tok.value!r} 大小写不合规：只接受大写形式"
                    f"（字符位置 {tok.pos}）"
                )
            if allow_constant and tok.value.isascii() and tok.value.isdigit():
                return ("int", int(tok.value), tok.pos), i + 1
            if i + 1 < len(tokens) and tokens[i + 1].kind == _TOK_LPAREN:
                raise OntologyError(
                    f"不支持的聚合函数 {tok.value!r}：仅支持 COUNT、MIN、MAX"
                    f"（字符位置 {tok.pos}）"
                )
            expected = (
                "HAVING 操作数只能是组键变量、聚合别名、聚合调用、字符串常量"
                "或非负整数常量"
                if allow_constant
                else "HAVING 操作数只能是组键变量、聚合别名或聚合调用"
            )
            raise OntologyError(
                f"{expected}，遇到 {tok.value!r}（字符位置 {tok.pos}）"
            )
        if allow_constant and tok.kind == _TOK_STRING:
            return ("lit", tok.value, tok.pos), i + 1
        raise OntologyError(
            f"HAVING 条件中存在未知语句成分 {tok.value!r}（字符位置 {tok.pos}）"
        )

    def _parse_having_aggregate(self, tokens: List[_Token], i: int):
        """解析 HAVING 中的匿名聚合调用（不带 AS 别名），tokens[i] 为函数名。

        返回 (("agg", 聚合种类, 参数变量或 None, 位置), 下一索引)。
        """
        func = tokens[i]
        i += 1
        if i >= len(tokens) or tokens[i].kind != _TOK_LPAREN:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(
                f"{func.value} 后缺少左圆括号 '('（字符位置 {pos}）"
            )
        i += 1

        distinct = False
        if i < len(tokens) and tokens[i].kind == _TOK_NAME and tokens[i].value == "DISTINCT":
            distinct = True
            i += 1
        elif (
            i < len(tokens)
            and tokens[i].kind == _TOK_NAME
            and tokens[i].value != tokens[i].value.upper()
            and tokens[i].value.upper() == "DISTINCT"
        ):
            raise OntologyError(
                f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式"
                f"（字符位置 {tokens[i].pos}）"
            )

        if i >= len(tokens):
            raise OntologyError(
                f"{func.value}(...) 的聚合参数不完整（字符位置 {func.pos}）"
            )
        arg_tok = tokens[i]

        if func.value == "COUNT":
            if distinct:
                if arg_tok.kind != _TOK_VAR:
                    raise OntologyError(
                        f"COUNT(DISTINCT ...) 中必须且只能出现一个变量，"
                        f"遇到 {arg_tok.value!r}（字符位置 {arg_tok.pos}）"
                    )
                kind = _AGG_COUNT_DISTINCT
                operand = arg_tok.value
            elif arg_tok.kind == _TOK_STAR:
                kind = _AGG_COUNT
                operand = None
            elif arg_tok.kind == _TOK_VAR:
                kind = _AGG_COUNT
                operand = arg_tok.value
            else:
                raise OntologyError(
                    f"COUNT 的参数形式非法：只接受 '*'、?变量 或 DISTINCT ?变量，"
                    f"遇到 {arg_tok.value!r}（字符位置 {arg_tok.pos}）"
                )
        else:
            if distinct:
                raise OntologyError(
                    f"{func.value} 不支持 DISTINCT 参数：只接受单个变量"
                    f"（字符位置 {arg_tok.pos}）"
                )
            if arg_tok.kind != _TOK_VAR:
                raise OntologyError(
                    f"{func.value} 的参数形式非法：括号内必须且只能出现一个变量，"
                    f"遇到 {arg_tok.value!r}（字符位置 {arg_tok.pos}）"
                )
            kind = _AGG_MIN if func.value == "MIN" else _AGG_MAX
            operand = arg_tok.value
        i += 1

        if i >= len(tokens) or tokens[i].kind != _TOK_RPAREN:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(
                f"{func.value} 聚合参数或嵌套形式非法：参数只能是 '*'、"
                f"DISTINCT ?变量 或单个变量，圆括号内存在多余或缺少成分"
                f"（字符位置 {pos}）"
            )
        i += 1
        return ("agg", kind, operand, func.pos), i

    def _parse_group_by(self, tokens: List[_Token], i: int):
        """解析 GROUP BY 分组键列表，tokens[i] 为 GROUP；返回 (分组键, 下一索引)。

        分组键只接受一个或多个互不重复的变量 ?v；其余形式（ASC/DESC、
        常量、表达式、圆括号）均为语法错误。
        """
        i += 1
        if (
            i >= len(tokens)
            or tokens[i].kind != _TOK_NAME
            or tokens[i].value != "BY"
        ):
            if (
                i < len(tokens)
                and tokens[i].kind == _TOK_NAME
                and tokens[i].value.upper() == "BY"
            ):
                raise OntologyError(
                    f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式"
                    f"（字符位置 {tokens[i].pos}）"
                )
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(f"GROUP 后缺少关键字 BY（字符位置 {pos}）")
        i += 1

        keys: List[tuple] = []
        seen = set()
        while i < len(tokens) and tokens[i].kind == _TOK_VAR:
            tok = tokens[i]
            if tok.value in seen:
                raise OntologyError(
                    f"GROUP BY 分组键 {tok.value} 重复（字符位置 {tok.pos}）"
                )
            seen.add(tok.value)
            keys.append((tok.value, tok.pos))
            i += 1
        if not keys:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(
                f"GROUP BY 后缺少分组变量：分组键必须是一个或多个查询变量"
                f"（字符位置 {pos}）"
            )
        return keys, i

    def _parse_order_by(self, tokens: List[_Token], i: int):
        """解析 ORDER BY 排序键列表，tokens[i] 为 ORDER；返回 (排序键, 下一索引)。"""
        i += 1
        if (
            i >= len(tokens)
            or tokens[i].kind != _TOK_NAME
            or tokens[i].value != "BY"
        ):
            if (
                i < len(tokens)
                and tokens[i].kind == _TOK_NAME
                and tokens[i].value.upper() == "BY"
            ):
                raise OntologyError(
                    f"关键字 {tokens[i].value!r} 大小写不合规：只接受大写形式"
                    f"（字符位置 {tokens[i].pos}）"
                )
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(f"ORDER 后缺少关键字 BY（字符位置 {pos}）")
        i += 1

        keys: List[tuple] = []
        seen = set()
        while i < len(tokens):
            tok = tokens[i]
            if tok.kind == _TOK_VAR:
                name, descending = tok.value, False
                i += 1
            elif tok.kind == _TOK_NAME and tok.value in ("ASC", "DESC"):
                func = tok
                i += 1
                if i >= len(tokens) or tokens[i].kind != _TOK_LPAREN:
                    pos = tokens[i].pos if i < len(tokens) else len(self._text)
                    raise OntologyError(
                        f"{func.value} 后缺少左圆括号 '('（字符位置 {pos}）"
                    )
                i += 1
                if i >= len(tokens) or tokens[i].kind != _TOK_VAR:
                    pos = tokens[i].pos if i < len(tokens) else len(self._text)
                    raise OntologyError(
                        f"{func.value}(...) 中必须且只能出现一个变量"
                        f"（字符位置 {pos}）"
                    )
                name = tokens[i].value
                i += 1
                if i >= len(tokens) or tokens[i].kind != _TOK_RPAREN:
                    pos = tokens[i].pos if i < len(tokens) else len(self._text)
                    raise OntologyError(
                        f"{func.value}({name} 的圆括号不配对：缺少右圆括号 ')'"
                        f"（字符位置 {pos}）"
                    )
                i += 1
                descending = func.value == "DESC"
            elif (
                tok.kind == _TOK_NAME
                and tok.value != tok.value.upper()
                and tok.value.upper() in ("ASC", "DESC")
            ):
                raise OntologyError(
                    f"关键字 {tok.value!r} 大小写不合规：只接受大写形式"
                    f"（字符位置 {tok.pos}）"
                )
            elif (
                tok.kind == _TOK_NAME
                and i + 1 < len(tokens)
                and tokens[i + 1].kind == _TOK_LPAREN
            ):
                raise OntologyError(
                    f"ORDER BY 只支持 ASC/DESC 排序函数，遇到 {tok.value!r}"
                    f"（字符位置 {tok.pos}）"
                )
            else:
                break
            if name in seen:
                raise OntologyError(
                    f"排序变量 {name} 重复（字符位置 {tok.pos}）"
                )
            seen.add(name)
            keys.append((name, descending, tok.pos))
        if not keys:
            pos = tokens[i].pos if i < len(tokens) else len(self._text)
            raise OntologyError(f"ORDER BY 后缺少排序键（字符位置 {pos}）")
        return keys, i

    def _parse_int_modifier(self, tokens: List[_Token], i: int):
        """解析 LIMIT/OFFSET 的非负十进制整数，tokens[i] 为关键字。"""
        word = tokens[i].value
        i += 1
        if i >= len(tokens):
            raise OntologyError(
                f"{word} 后缺少非负整数（字符位置 {len(self._text)}）"
            )
        tok = tokens[i]
        if tok.kind != _TOK_NAME or not (tok.value.isascii() and tok.value.isdigit()):
            raise OntologyError(
                f"{word} 后必须是非负十进制整数，遇到 {tok.value!r}"
                f"（字符位置 {tok.pos}）"
            )
        i += 1
        try:
            value = int(tok.value)
        except ValueError:
            raise OntologyError(
                f"{word} 的整数超出可表示范围（字符位置 {tok.pos}）"
            )
        return value, i


def _compile_query(text, properties: frozenset):
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
    """在 model.triples 上执行查询，返回应用解序列修饰符后的 QueryResult。

    普通 SELECT 先按既有语义求值、投影、去重并按投影字典序排列。

    出现聚合项（'(COUNT/MIN/MAX(...) AS ?别名)'）或 GROUP BY 时走分组
    聚合路径：WHERE 先按既有语义求值与 FILTER 过滤，再按组键值把解分组；
    有 GROUP BY 时每个不同组键值输出一行（无解则无行），无 GROUP BY 时
    即使没有解也产生一个空组行（COUNT 为 0、MIN/MAX 为 None）。每行按
    投影顺序由组键值与聚合结果构成：COUNT(*) 统计组内解数（非负整数），
    COUNT(?v) 只统计 ?v 已绑定的解，COUNT(DISTINCT ?v) 再按绑定值去重；
    MIN(?v)/MAX(?v) 在组内已绑定的字符串值中按 Unicode 字典序取最小/
    最大，无已绑定时为 None。有 HAVING 时在分组聚合完成后逐组筛选，
    被过滤的组不参与后续去重、排序、OFFSET 与 LIMIT；没有组通过时
    返回空 QueryResult。

    两条路径都先按投影去重并字典序排序，再有 ORDER BY 时做稳定排序
    （同键未绑定值先于绑定值，DESC 相反；多键按出现顺序比较，全相同
    则保留投影字典序），最后跳过 OFFSET 条并保留至多 LIMIT 条。
    """
    properties = frozenset(getattr(model, "declared_properties", ()))
    head, clauses, star, (group_keys, having, order_keys, limit, offset) = (
        _compile_query(text, properties)
    )

    matcher = _PathMatcher(model.triples)
    bindings = _eval_clauses(matcher, clauses)

    has_aggregate = any(item[0] == "a" for item in head)
    if not star and (has_aggregate or group_keys):
        rows = _aggregate_bindings(head, group_keys, bindings, having)
        projection = tuple(item[1] for item in head)
    else:
        if star:
            projection = _star_variables(clauses)
        else:
            projection = tuple(item[1] for item in head)
        rows = {
            tuple(binding.get(name) for name in projection) for binding in bindings
        }

    # None（未绑定）排在所有字符串之前，保证混合取值时字典序排序稳定。
    ordered = sorted(rows, key=_projection_sort_key)
    if order_keys:
        index = {name: i for i, name in enumerate(projection)}
        # Python 排序稳定：从最后一个排序键开始依次排序，多键按出现顺序
        # 生效，全部键相同的行保持此前的投影字典序。
        for name, descending, _pos in reversed(order_keys):
            column = index[name]
            ordered.sort(
                key=lambda row, c=column: _projection_sort_key((row[c],)),
                reverse=descending,
            )
    if offset:
        ordered = ordered[offset:]
    if limit is not None:
        ordered = ordered[:limit]
    return QueryResult(projection, tuple(ordered))


def _aggregate_bindings(head, group_keys, bindings, having=None):
    """把求值得到的绑定按组键分组并计算聚合，返回行集合（未排序）。

    组键在绑定中未绑定时以 None 作为组值，所有未绑定解归入同一组。
    无 GROUP BY 时所有解属于同一个组，且即使解列表为空也产生一行；
    有 GROUP BY 但没有任何解时不产生行。有 HAVING 时在聚合完成后逐组
    筛选，被过滤的组不进入行集合。
    """
    key_names = tuple(name for name, _pos in group_keys)
    aggs = [item for item in head if item[0] == "a"]

    groups: dict = {}
    if key_names:
        for binding in bindings:
            key = tuple(binding.get(name) for name in key_names)
            groups.setdefault(key, []).append(binding)
    else:
        # 单一空键组：没有解时同样保留该组，产生一行聚合结果。
        groups[()] = list(bindings)

    rows = set()
    for key, members in groups.items():
        key_values = dict(zip(key_names, key))
        alias_values = {
            alias: _compute_aggregate(kind, operand, members)
            for _tag, alias, kind, operand, _pos in aggs
        }
        if having is not None and not _eval_having(
            having, key_values, alias_values, members
        ):
            continue
        row = []
        for item in head:
            if item[0] == "v":
                row.append(key_values.get(item[1]))
            else:
                row.append(alias_values[item[1]])
        rows.add(tuple(row))
    return rows


def _eval_having(having, key_values: dict, alias_values: dict, members: List[dict]) -> bool:
    """逐组执行 HAVING：全部条件按逻辑与满足时该组通过，否则被过滤。"""
    _kw_pos, conditions = having
    for cond in conditions:
        kind = cond[0]
        if kind == "bound":
            if _having_value(cond[1], key_values, alias_values, members) is None:
                return False
            continue
        if kind == "not_bound":
            if _having_value(cond[1], key_values, alias_values, members) is not None:
                return False
            continue
        _tag, op, left_node, right_node, _pos = cond
        left = _having_value(left_node, key_values, alias_values, members)
        right = _having_value(right_node, key_values, alias_values, members)
        if not _compare_having(op, left, right):
            return False
    return True


def _having_value(node, key_values: dict, alias_values: dict, members: List[dict]):
    """解析 HAVING 值节点：组键变量取组键值，聚合别名取该组的投影聚合
    结果，匿名聚合调用在该组解序列上即时计算，常量原样返回。"""
    tag = node[0]
    if tag == "var":
        name = node[1]
        if name in key_values:
            return key_values[name]
        return alias_values.get(name)
    if tag == "agg":
        return _compute_aggregate(node[1], node[2], members)
    return node[1]  # "lit" 字符串常量 / "int" 非负整数常量


def _compare_having(op: str, left, right) -> bool:
    """HAVING 比较：任一操作数为 None（含组键未绑定）时为假；数字按数值、
    字符串按 Unicode 字典序比较；非 None 混合类型时 = 与顺序比较为假、
    != 为真。"""
    if left is None or right is None:
        return False
    if type(left) is not type(right):
        return op == "!="
    if op == "=":
        return left == right
    if op == "!=":
        return left != right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if op == ">":
        return left > right
    return left >= right


def _compute_aggregate(kind: str, operand, members: List[dict]):
    """在一个组的解序列上计算单个聚合值。

    COUNT(*) 统计全部解；COUNT(?v) 只统计变量已绑定的解；
    COUNT(DISTINCT ?v) 在已绑定值上按值去重计数；
    MIN/MAX 在已绑定字符串值中按 Unicode 字典序取极值，无值为 None。
    """
    if kind == _AGG_COUNT:
        if operand is None:
            return len(members)
        return sum(1 for binding in members if binding.get(operand) is not None)
    if kind == _AGG_COUNT_DISTINCT:
        return len(
            {
                binding[operand]
                for binding in members
                if binding.get(operand) is not None
            }
        )
    values = [
        binding[operand]
        for binding in members
        if binding.get(operand) is not None
    ]
    if not values:
        return None
    return min(values) if kind == _AGG_MIN else max(values)


def _projection_sort_key(row) -> tuple:
    """投影字典序排序键：None（未绑定）排在所有字符串之前。"""
    return tuple((0, "") if value is None else (1, value) for value in row)


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
