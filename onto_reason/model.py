"""本体模型：解析结果的只读视图。

模型内部以不可变集合保存数据，所有读取接口返回新的不可变序列，
不会修改已解析的数据，因此同一模型可重复读取且结果稳定。
"""

from __future__ import annotations

import itertools
from typing import Iterator, Optional, Tuple

from .errors import OntologyError
from .query import QueryResult, run_query

_VARIABLE_PREFIX = "?"

_EXPLICIT_KIND = "explicit"
_RULE_KIND = "rule"


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
    """一条三元组的证明树节点（只读快照）。

    - kind 为 "explicit"：triple 是输入中的显式事实，ruleId 为 None，
      premises 为空元组；
    - kind 为 "rule"：triple 由规则 ruleId 经具体常量替换后推出，
      premises 按该规则 if 模式的原顺序给出各前提的证明节点，
      沿 premises 可一直追到显式叶子。
    所有公开属性只读；相等性按 kind/triple/ruleId/premises 结构比较。
    """

    __slots__ = ("_kind", "_triple", "_rule_id", "_premises")

    def __init__(self, kind: str, triple: Triple, rule_id, premises) -> None:
        self._kind = kind
        self._triple = triple
        self._rule_id = rule_id
        self._premises = tuple(premises)

    @property
    def kind(self) -> str:
        """证明种类："explicit" 或 "rule"。"""
        return self._kind

    @property
    def triple(self) -> Triple:
        """本节点证明的三元组。"""
        return self._triple

    @property
    def ruleId(self):
        """推出该三元组的规则 id（保留原始字符串或整数）；显式事实为 None。"""
        return self._rule_id

    @property
    def premises(self) -> Tuple["Proof", ...]:
        """前提证明节点元组，按规则 if 模式的原顺序排列。"""
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

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return (
            f"Proof(kind={self._kind!r}, triple={self._triple!r}, "
            f"ruleId={self._rule_id!r}, premises={self._premises!r})"
        )


def _leaf_count(proof: Proof) -> int:
    """证明长度：树中显式叶子的数量。"""
    if proof.kind == _EXPLICIT_KIND:
        return 1
    return sum(_leaf_count(premise) for premise in proof.premises)


def _proof_sort_key(proof: Proof):
    """稳定排序键：kind、ruleId 的字符串形式、节点 triple、premises 顺序。"""
    return (
        proof.kind,
        str(proof.ruleId),
        proof.triple,
        tuple(_proof_sort_key(premise) for premise in proof.premises),
    )


def _unify_then(pattern, triple: Triple):
    """把 then 模式与基三元组对齐，返回变量→常量绑定；不一致时返回 None。"""
    s, p, o = pattern
    if p != triple.predicate:
        return None
    binding = {}
    for term, value in ((s, triple.subject), (o, triple.object)):
        if _is_variable(term):
            if term in binding:
                if binding[term] != value:
                    return None
            else:
                binding[term] = value
        elif term != value:
            return None
    return binding


def _instantiate(pattern, binding) -> Triple:
    """按绑定把三元组模式替换为基三元组（模式中的变量均已绑定）。"""
    s, p, o = pattern
    return Triple(
        binding.get(s, s) if _is_variable(s) else s,
        p,
        binding.get(o, o) if _is_variable(o) else o,
    )


class OntologyModel:
    """解析与推理完成后的本体模型。

    - explicit_triples：显式三元组，按 (主语, 谓语, 宾语) 字典序排列。
    - derived_triples：推理结论，同样按字典序排列，每条带来源规则 id。
    - entails(s, p, o)：判断单条三元组是否被蕴含（显式或推理得出）。
    - explain(s, p, o)：给出该三元组的全部最短完整证明（Proof 元组）。
    - declared_properties：本体中已声明的属性名（查询谓语/属性路径据此校验）。
    所有读取接口均返回新对象，不改变模型内部数据。
    """

    __slots__ = ("_explicit", "_derived", "_facts", "_source", "_properties", "_rules")

    def __init__(self, explicit, derived, properties=frozenset(), rules=()) -> None:
        # explicit / derived: dict[Triple, rule_id 或 None]
        # rules: (rule_id, if 模式, then 模式) 元组序列，模式为 (s, p, o) 字符串三元组
        self._explicit = frozenset(explicit)
        self._derived = frozenset(
            DerivedTriple(triple, rule_id) for triple, rule_id in derived.items()
        )
        self._facts = frozenset(explicit) | frozenset(derived)
        self._source = dict(derived)
        self._properties = frozenset(properties)
        self._rules = tuple(
            (
                rule_id,
                tuple(tuple(pattern) for pattern in if_patterns),
                tuple(tuple(pattern) for pattern in then_patterns),
            )
            for rule_id, if_patterns, then_patterns in rules
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

    def explain(self, subject: str, predicate: str, object_) -> Tuple[Proof, ...]:
        """返回 (subject, predicate, object_) 的全部最短完整证明。

        - 显式事实：返回恰一个 kind 为 "explicit" 的证明（即使它同时也能
          被规则推出，显式证明优先）；
        - 推理结论：返回全部显式叶子数最少的完整证明；规则有多个 then
          或不同替换能推出目标时全部保留，按 kind、ruleId 的字符串形式、
          节点 triple 和 premises 顺序稳定排列，重复树去重；
        - 未被蕴含的三元组：返回空元组。
        证明不依赖推理循环（成环规则上有限结束）。subject、predicate、
        object 任一不是字符串时抛出 OntologyError。
        """
        for position, (name, value) in enumerate(
            (("subject", subject), ("predicate", predicate), ("object", object_)),
            start=1,
        ):
            if not isinstance(value, str):
                raise OntologyError(
                    f"explain 的第 {position} 个参数（{name}）必须是字符串，"
                    f"收到 {value!r}"
                )
        goal = Triple(subject, predicate, object_)
        if goal in self._explicit:
            return (Proof(_EXPLICIT_KIND, goal, None, ()),)
        if goal not in self._facts:
            return ()
        proofs = self._prove(goal, frozenset())
        if not proofs:  # pragma: no cover - 推理结论必有证明，此处仅作防御
            return ()
        best = min(_leaf_count(proof) for proof in proofs)
        chosen = [proof for proof in proofs if _leaf_count(proof) == best]
        unique = list(dict.fromkeys(chosen))
        unique.sort(key=_proof_sort_key)
        return tuple(unique)

    # ---------- 证明枚举（后向链接，按路径阻断循环） ----------

    def _prove(self, goal: Triple, blocked: frozenset) -> list:
        """枚举 goal 的全部无环证明（未按叶子数过滤）。

        blocked 为当前根到此节点路径上已出现的目标三元组；前提落入
        blocked 的替换被丢弃，因此成环规则上证明枚举必然有限结束，
        且不会返回依赖循环的证明。
        """
        if goal in self._explicit:
            return [Proof(_EXPLICIT_KIND, goal, None, ())]
        if goal not in self._facts:
            return []
        blocked = blocked | {goal}
        fact_list = sorted(self._facts)
        proofs = []
        for rule_id, if_patterns, then_patterns in self._rules:
            for then in then_patterns:
                partial = _unify_then(then, goal)
                if partial is None:
                    continue
                for binding in self._match(if_patterns, fact_list, partial):
                    premises = tuple(
                        _instantiate(pattern, binding) for pattern in if_patterns
                    )
                    if any(triple in blocked for triple in premises):
                        continue
                    options = []
                    for triple in premises:
                        sub = self._prove(triple, blocked)
                        if not sub:
                            break
                        options.append(sub)
                    else:
                        for combo in itertools.product(*options):
                            proofs.append(Proof(_RULE_KIND, goal, rule_id, combo))
        return proofs

    @staticmethod
    def _match(patterns, fact_list, initial) -> list:
        """从给定部分绑定出发，对 if 模式按序做连接匹配（与推理同序）。"""
        bindings = [dict(initial)]
        for s, p, o in patterns:
            next_bindings = []
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
