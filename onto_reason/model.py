"""解析后的本体模型：只读的三元组容器与蕴含判断。

模型在 :class:`onto_reason.engine.OntologyEngine` 内一次性构建完成，
此后所有读取接口都不改变内部状态，同一模型可被任意重复读取，
多次读取得到的对象彼此独立（修改返回值不会污染模型）。
"""

from typing import List, Optional, Tuple

from .errors import OntologyError


class Triple:
    """一条主语-谓语-宾语三元组。

    字段只读；支持解包 ``s, p, o = triple``、相等比较、哈希与排序，
    排序按 ``(subject, predicate, object)`` 的字典序。
    """

    __slots__ = ("_subject", "_predicate", "_object")

    def __init__(self, subject: str, predicate: str, object: str) -> None:
        self._subject = subject
        self._predicate = predicate
        self._object = object

    @property
    def subject(self) -> str:
        return self._subject

    @property
    def predicate(self) -> str:
        return self._predicate

    @property
    def object(self) -> str:
        return self._object

    def as_tuple(self) -> Tuple[str, str, str]:
        return self._subject, self._predicate, self._object

    def __iter__(self):
        return iter(self.as_tuple())

    def __eq__(self, other) -> bool:
        if not isinstance(other, Triple):
            return NotImplemented
        return self.as_tuple() == other.as_tuple()

    def __hash__(self) -> int:
        return hash(self.as_tuple())

    def __lt__(self, other) -> bool:
        if not isinstance(other, Triple):
            return NotImplemented
        return self.as_tuple() < other.as_tuple()

    def __repr__(self) -> str:
        return f"Triple({self._subject!r}, {self._predicate!r}, {self._object!r})"


class OntologyModel:
    """已解析、已完成推理的只读本体模型。

    不应在包外直接构造；由 ``OntologyEngine.parse`` 返回。
    内部以排序后的列表 + 集合保存，读接口一律返回副本，
    因此重复读取必然得到相同结果，且外部无法篡改内部数据。
    """

    def __init__(
        self,
        classes: List[str],
        properties: List[str],
        individuals: List[str],
        explicit: List[Triple],
        inferred: List[Tuple[Triple, str]],
    ) -> None:
        # 声明信息按名称字典序保存（输入已做过去重校验）。
        self._classes = sorted(classes)
        self._properties = sorted(properties)
        self._individuals = sorted(individuals)

        # 显式三元组：按 (s,p,o) 字典序去重。
        deduped = sorted({t.as_tuple(): t for t in explicit}.values())
        self._explicit: List[Triple] = deduped
        self._explicit_set = {t.as_tuple() for t in deduped}

        # 推理结论：同一三元组只保留首个来源（见 engine 中的确定规则），
        # 按 (s,p,o) 字典序排列。
        inferred_sorted = sorted(inferred, key=lambda item: item[0].as_tuple())
        self._inferred: List[Triple] = [t for t, _rule_id in inferred_sorted]
        self._sources: dict = {t.as_tuple(): rule_id for t, rule_id in inferred_sorted}

        self._all: List[Triple] = sorted(set(self._explicit) | set(self._inferred))
        self._inferred_set = {t.as_tuple() for t in self._inferred}

    # ------------------------------------------------------------------ 声明

    @property
    def classes(self) -> List[str]:
        return list(self._classes)

    @property
    def properties(self) -> List[str]:
        return list(self._properties)

    @property
    def individuals(self) -> List[str]:
        return list(self._individuals)

    # ------------------------------------------------------------------ 读取

    def explicit_triples(self) -> List[Triple]:
        """返回全部显式三元组，按主语、谓语、宾语字典序排列。"""
        return list(self._explicit)

    def inferred_triples(self) -> List[Triple]:
        """返回全部推理三元组（不含显式），字典序稳定排列。"""
        return list(self._inferred)

    def triples(self) -> List[Triple]:
        """返回显式与推理的全部三元组，显式结论不重复出现，字典序排列。"""
        return list(self._all)

    def origin(self, triple) -> Tuple[str, Optional[str]]:
        """返回三元组的来源标记。

        结果为 ``("explicit", None)`` 或 ``("derived", 规则id)``。
        若三元组既非显式也无法推出，抛出 :class:`OntologyError`。
        接受 :class:`Triple` 或 ``(subject, predicate, object)`` 序列。
        """
        key = self._as_key(triple, "origin")
        if key in self._explicit_set:
            return "explicit", None
        rule_id = self._sources.get(key)
        if key in self._inferred_set or rule_id is not None:
            return "derived", rule_id
        raise OntologyError(f"三元组 {key} 不存在：既非显式声明也无法由规则推出")

    def source_rule(self, triple) -> Optional[str]:
        """推理三元组返回其来源规则 id；显式三元组返回 ``None``；
        不存在的三元组抛出 :class:`OntologyError`。"""
        return self.origin(triple)[1]

    def is_explicit(self, triple) -> bool:
        """三元组是否由输入显式声明。"""
        return self._as_key(triple, "is_explicit") in self._explicit_set

    def is_inferred(self, triple) -> bool:
        """三元组是否由规则推理产生（不含显式声明）。"""
        return self._as_key(triple, "is_inferred") in self._inferred_set

    def entails(self, triple) -> bool:
        """蕴含判断：显式三元组或规则可推出的三元组均视为被蕴含。

        该方法是纯读取，不会改变模型的任何已解析数据。
        """
        key = self._as_key(triple, "entails")
        return key in self._explicit_set or key in self._inferred_set

    # 兼容英文别名，便于调用方按 README 术语使用。
    def list_explicit(self) -> List[Triple]:
        return self.explicit_triples()

    def list_inferred(self) -> List[Triple]:
        return self.inferred_triples()

    def list_triples(self) -> List[Triple]:
        return self.triples()

    # ------------------------------------------------------------------ 内部

    @staticmethod
    def _as_key(triple, where: str) -> Tuple[str, str, str]:
        if isinstance(triple, Triple):
            return triple.as_tuple()
        if isinstance(triple, tuple) and len(triple) == 3:
            s, p, o = triple
            if all(isinstance(x, str) for x in (s, p, o)):
                return s, p, o
        raise OntologyError(
            f"{where}: 参数必须是 Triple 或 (subject, predicate, object) 字符串三元组"
        )

    def __repr__(self) -> str:
        return (
            f"OntologyModel(classes={len(self._classes)}, "
            f"properties={len(self._properties)}, "
            f"individuals={len(self._individuals)}, "
            f"explicit={len(self._explicit)}, inferred={len(self._inferred)})"
        )
