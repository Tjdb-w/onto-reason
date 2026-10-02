"""本体模型：解析结果的只读视图。

模型内部以不可变集合保存数据，所有读取接口返回新的不可变序列，
不会修改已解析的数据，因此同一模型可重复读取且结果稳定。
"""

from __future__ import annotations

import itertools
from typing import Dict, FrozenSet, Iterator, List, Optional, Tuple

from .errors import OntologyError
from .query import QueryResult, run_query

_VARIABLE_PREFIX = "?"


def _is_variable(value: str) -> bool:
    return value.startswith(_VARIABLE_PREFIX)


class Triple(tuple):
    """一条三元组 (subject, predicate, object)，按字典序可比较。"""

    __slots__ = ()

    def __new__(cls, subject: str, predicate: str, object_: str) -> "Triple":
        return super().__new__(cls, (subject, predicate, object_))

    @property
    def subject(self) -> str:
        return self[0]

    @property
    def predicate(self) -> str:
        return self[1]

    @property
    def object(self) -> str:
        return self[2]

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"Triple({self[0]!r}, {self[1]!r}, {self[2]!r})"


class DerivedTriple:
    """一条推理结论：三元组 + 来源规则 id。"""

    __slots__ = ("_triple", "_rule_id")

    def __init__(self, triple: Triple, rule_id) -> None:
        self._triple = triple
        self._rule_id = rule_id

    @property
    def triple(self) -> Triple:
        return self._triple

    @property
    def subject(self) -> str:
        return self._triple.subject

    @property
    def predicate(self) -> str:
        return self._triple.predicate

    @property
    def object(self) -> str:
        return self._triple.object

    @property
    def rule_id(self):
        return self._rule_id

    def __iter__(self) -> Iterator:
        return iter((self._triple, self._rule_id))

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, DerivedTriple)
            and self._triple == other._triple
            and self._rule_id == other._rule_id
        )

    def __hash__(self) -> int:
        return hash((self._triple, self._rule_id))

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return f"DerivedTriple({self._triple!r}, rule_id={self._rule_id!r})"


class Proof:
    """一条三元组的证明树节点：显式事实或一次规则推导。

    - kind：``"explicit"``（输入 triples 中已有的事实）或 ``"rule"``
      （规则经具体常量替换后推出本节点 triple）。
    - triple：本节点证明的三元组（Triple）。
    - ruleId：显式节点为 None；规则节点为来源规则的原始 id（字符串或整数）。
    - premises：规则节点各前提的证明节点，按 if 模式的原顺序排列，
      可从根追到显式叶子；显式节点为空元组。

    所有公开属性只读，premises 为不可变元组；两个 Proof 在 kind、triple、
    ruleId 与 premises 全部相同时相等，可哈希。
    """

    __slots__ = ("_kind", "_triple", "_rule_id", "_premises", "_leaves")

    def __init__(self, kind: str, triple: Triple, rule_id, premises) -> None:
        self._kind = kind
        self._triple = triple
        self._rule_id = rule_id
        self._premises = tuple(premises)
        if kind == "explicit":
            self._leaves = 1
        else:
            self._leaves = sum(premise._leaves for premise in self._premises)

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def triple(self) -> Triple:
        return self._triple

    @property
    def ruleId(self):
        return self._rule_id

    @property
    def premises(self) -> Tuple["Proof", ...]:
        return self._premises

    def __eq__(self, other) -> bool:
        return (
            isinstance(other, Proof)
            and self._kind == other._kind
            and self._triple == other._triple
            and self._rule_id == other._rule_id
            and self._premises == other._premises
        )

    def __hash__(self) -> int:
        return hash((self._kind, self._triple, self._rule_id, self._premises))

    def __repr__(self):  # pragma: no cover - 便于调试
        if self._kind == "explicit":
            return f"Proof(explicit, {self._triple!r})"
        return (
            f"Proof(rule, {self._triple!r}, ruleId={self._rule_id!r}, "
            f"premises={self._premises!r})"
        )


def _proof_sort_key(proof: Proof):
    """最短证明的稳定排序键：kind、ruleId 字符串形式、triple、premises 递归。"""
    return (
        proof.kind,
        str(proof.ruleId),
        proof.triple,
        tuple(_proof_sort_key(premise) for premise in proof.premises),
    )


def _shortest_proofs(candidates: List[Proof]) -> Tuple[Proof, ...]:
    """从候选证明中筛出显式叶子数最少者，稳定排序并去除重复树。"""
    if not candidates:
        return ()
    best = min(proof._leaves for proof in candidates)
    shortest = [proof for proof in candidates if proof._leaves == best]
    shortest.sort(key=_proof_sort_key)
    result: List[Proof] = []
    seen = set()
    for proof in shortest:
        if proof not in seen:
            seen.add(proof)
            result.append(proof)
    return tuple(result)


def _match_then_pattern(pattern, goal: Triple) -> Optional[Dict[str, str]]:
    """把 then 模式与目标基三元组对齐，返回部分变量绑定；不匹配返回 None。"""
    s, p, o = pattern
    if p != goal.predicate:
        return None
    binding: Dict[str, str] = {}
    for term, value in ((s, goal.subject), (o, goal.object)):
        if _is_variable(term):
            if term in binding and binding[term] != value:
                return None
            binding[term] = value
        elif term != value:
            return None
    return binding


def _match_if_patterns(patterns, fact_list, seed) -> List[Dict[str, str]]:
    """对 if 模式按序做连接匹配，从种子绑定出发返回所有满足的变量绑定。"""
    bindings: List[Dict[str, str]] = [dict(seed)]
    for s, p, o in patterns:
        next_bindings: List[Dict[str, str]] = []
        for binding in bindings:
            for fact in fact_list:
                if fact.predicate != p:
                    continue
                if _is_variable(s):
                    if s in binding and fact.subject != binding[s]:
                        continue
                elif fact.subject != s:
                    continue
                extended = dict(binding)
                if _is_variable(s):
                    extended[s] = fact.subject
                if _is_variable(o):
                    if o in extended and fact.object != extended[o]:
                        continue
                elif fact.object != o:
                    continue
                if _is_variable(o):
                    extended[o] = fact.object
                next_bindings.append(extended)
        bindings = next_bindings
        if not bindings:
            break
    return bindings


class OntologyModel:
    """解析与推理完成后的本体模型。

    - explicit_triples：显式三元组，按 (主语, 谓语, 宾语) 字典序排列。
    - derived_triples：推理结论，同样按字典序排列，每条带来源规则 id。
    - entails(s, p, o)：判断单条三元组是否被蕴含（显式或推理得出）。
    - explain(s, p, o)：给出单条三元组的全部最短证明（Proof 元组）。
    - declared_properties：本体中已声明的属性名（查询谓语/属性路径据此校验）。
    所有读取接口均返回新对象，不改变模型内部数据。
    """

    __slots__ = ("_explicit", "_derived", "_facts", "_source", "_properties", "_rules")

    def __init__(self, explicit, derived, properties=frozenset(), rules=()) -> None:
        # explicit / derived: dict[Triple, rule_id 或 None]
        self._explicit = frozenset(explicit)
        self._derived = frozenset(
            DerivedTriple(triple, rule_id) for triple, rule_id in derived.items()
        )
        self._facts = frozenset(explicit) | frozenset(derived)
        self._source = dict(derived)
        self._properties = frozenset(properties)
        # 编译后规则的不可变快照：(rule_id, if 模式元组, then 模式元组)。
        self._rules = tuple(
            (
                rule.rule_id,
                tuple(tuple(pattern) for pattern in rule.if_patterns),
                tuple(tuple(pattern) for pattern in rule.then_patterns),
            )
            for rule in rules
        )

    @property
    def explicit_triples(self) -> Tuple[Triple, ...]:
        """显式三元组，按字典序稳定排列。"""
        return tuple(sorted(self._explicit))

    @property
    def derived_triples(self) -> Tuple[DerivedTriple, ...]:
        """推理结论（含来源规则 id），按三元组字典序稳定排列。"""
        return tuple(sorted(self._derived, key=lambda d: (d.triple, str(d.rule_id))))

    @property
    def triples(self) -> Tuple[Triple, ...]:
        """全部三元组（显式 + 推理），按字典序稳定排列。"""
        return tuple(sorted(self._facts))

    @property
    def declared_properties(self) -> Tuple[str, ...]:
        """本体中已声明的属性名，按字典序稳定排列。"""
        return tuple(sorted(self._properties))

    def entails(self, subject: str, predicate: str, object_: str) -> bool:
        """判断 (subject, predicate, object_) 是否被当前模型蕴含。"""
        return Triple(subject, predicate, object_) in self._facts

    def source_rule(self, subject: str, predicate: str, object_: str) -> Optional[object]:
        """返回推理出该三元组的规则 id；显式或未知三元组返回 None。"""
        return self._source.get(Triple(subject, predicate, object_))

    def explain(self, subject: str, predicate: str, object_: str) -> Tuple[Proof, ...]:
        """返回 (subject, predicate, object_) 的全部最短证明，为不可变 Proof 元组。

        - 显式事实：返回恰一个 kind 为 "explicit" 的证明（ruleId 为 None、
          premises 为空元组）；即使同一三元组也能被规则推出，也只返回显式证明。
        - 推理事实：返回全部显式叶子数最少的完整证明；规则有多个 then 或
          不同替换能推出目标时全部保留，按 kind、ruleId 字符串形式、节点
          triple 与 premises 顺序稳定排列，相同结构与来源的重复树去重。
          规则成环时有限结束，不返回依赖循环的较长证明。
        - 未被蕴含的三元组：返回空元组。

        subject、predicate、object_ 任一不是字符串时抛出 OntologyError，
        消息中指出参数位置。重复调用返回内容相同的元组。
        """
        for position, (name, value) in enumerate(
            (("subject", subject), ("predicate", predicate), ("object", object_)),
            start=1,
        ):
            if not isinstance(value, str):
                raise OntologyError(
                    f"explain 的第 {position} 个参数（{name}）必须是字符串，"
                    f"收到 {type(value).__name__} 类型的 {value!r}"
                )
        goal = Triple(subject, predicate, object_)
        if goal in self._explicit:
            return (Proof("explicit", goal, None, ()),)
        if goal not in self._facts:
            return ()
        return self._prove(goal, frozenset(), {})

    def _prove(
        self,
        goal: Triple,
        active: FrozenSet[Triple],
        memo: Dict[Tuple[Triple, FrozenSet[Triple]], Optional[Tuple[Proof, ...]]],
    ) -> Optional[Tuple[Proof, ...]]:
        """返回 goal 的全部最短无环证明（已排序去重）。

        active 为当前证明路径上的祖先三元组：goal 已在 active 中时返回 None，
        表示继续展开将依赖循环，该方向不产生有效证明；goal 的所有推导都
        依赖循环时返回空元组。显式事实直接返回显式证明（显式优先）。
        """
        if goal in self._explicit:
            return (Proof("explicit", goal, None, ()),)
        if goal in active:
            return None
        key = (goal, active)
        if key in memo:
            return memo[key]
        fact_list = sorted(self._facts)
        candidates: List[Proof] = []
        for rule_id, if_patterns, then_patterns in self._rules:
            for then_pattern in then_patterns:
                seed = _match_then_pattern(then_pattern, goal)
                if seed is None:
                    continue
                for binding in _match_if_patterns(if_patterns, fact_list, seed):
                    premise_options = []
                    for s, p, o in if_patterns:
                        premise = Triple(binding.get(s, s), p, binding.get(o, o))
                        sub = self._prove(premise, active | {goal}, memo)
                        if not sub:
                            # 前提依赖循环或在此路径下无可行证明，放弃该替换。
                            break
                        premise_options.append(sub)
                    else:
                        for combo in itertools.product(*premise_options):
                            candidates.append(Proof("rule", goal, rule_id, combo))
        result = _shortest_proofs(candidates)
        memo[key] = result
        return result

    def query(self, text: str) -> QueryResult:
        """执行 SPARQL 风格 SELECT 查询，在显式与推理三元组上匹配。

        例如 SELECT ?x ?y WHERE { ?x knows ?y . ?y likes ?z }；
        模式体中还可以出现 OPTIONAL { ... }（左连接，无匹配时块内变量未绑定）、
        FILTER ( 表达式 )（BOUND/!BOUND 与 =/!= 比较，多个 FILTER 逻辑与）
        以及 { ... } UNION { ... }（多个花括号分支取并集，分支内仅含三元组
        模式，连续 UNION 从左到右结合）。
        谓语位置除常量属性名外还接受属性路径，只作用于本模型的
        explicit + derived 三元组并集：^p 逆向、a/b 序列复合、a|b 取并集、
        p?/p*/p+ 分别为零或一次/零或多次/一次或多次，圆括号只用于分组，
        路径中的属性名必须已声明；p?/p* 的零次分支只连接三元组中实际出现
        过的节点，p+ 在环状数据上也会有限结束。OPTIONAL 与 UNION 块内同样
        可以使用属性路径；FILTER 不把路径当作比较操作数。
        查询不修改模型，重复执行结果一致。词法或语法错误抛出 OntologyError。
        """
        return run_query(self, text)
