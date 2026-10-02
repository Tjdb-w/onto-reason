"""本体模型：解析结果的只读视图。

模型内部以不可变集合保存数据，所有读取接口返回新的不可变序列，
不会修改已解析的数据，因此同一模型可重复读取且结果稳定。
"""

from __future__ import annotations

from typing import Iterator, Optional, Tuple

from .query import QueryResult, run_query


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


class OntologyModel:
    """解析与推理完成后的本体模型。

    - explicit_triples：显式三元组，按 (主语, 谓语, 宾语) 字典序排列。
    - derived_triples：推理结论，同样按字典序排列，每条带来源规则 id。
    - entails(s, p, o)：判断单条三元组是否被蕴含（显式或推理得出）。
    所有读取接口均返回新对象，不改变模型内部数据。
    """

    __slots__ = ("_explicit", "_derived", "_facts", "_source")

    def __init__(self, explicit, derived) -> None:
        # explicit / derived: dict[Triple, rule_id 或 None]
        self._explicit = frozenset(explicit)
        self._derived = frozenset(
            DerivedTriple(triple, rule_id) for triple, rule_id in derived.items()
        )
        self._facts = frozenset(explicit) | frozenset(derived)
        self._source = dict(derived)

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

    def entails(self, subject: str, predicate: str, object_: str) -> bool:
        """判断 (subject, predicate, object_) 是否被当前模型蕴含。"""
        return Triple(subject, predicate, object_) in self._facts

    def source_rule(self, subject: str, predicate: str, object_: str) -> Optional[object]:
        """返回推理出该三元组的规则 id；显式或未知三元组返回 None。"""
        return self._source.get(Triple(subject, predicate, object_))

    def query(self, text: str) -> QueryResult:
        """执行 SPARQL 风格 SELECT 查询，在显式与推理三元组上匹配。

        例如 SELECT ?x ?y WHERE { ?x knows ?y . ?y likes ?z }；
        模式体中还可以出现 OPTIONAL { ... }（左连接，无匹配时块内变量未绑定）、
        FILTER ( 表达式 )（BOUND/!BOUND 与 =/!= 比较，多个 FILTER 逻辑与）
        以及 { ... } UNION { ... }（多分支并集，分支内只含三元组模式，
        FILTER 在所有分支合并完成后执行）。
        查询不修改模型，重复执行结果一致。词法或语法错误抛出 OntologyError。
        """
        return run_query(self, text)
