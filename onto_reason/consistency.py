"""一致性诊断：解析 consistency 段并在不动点推理后检测语义冲突。

consistency 段结构（根对象的可选字段，出现时必须恰含三个字段）：

- classMembershipPredicate：非空字符串，且必须是 properties 中已声明的属性。
- disjointClasses：对象数组，每项为 {"id": ..., "classes": [...]}；
  id 为非空字符串或整数，跨两个数组合并后不得重复；
  classes 至少列出两个互不相同且已声明的类名。
- functionalProperties：对象数组，每项为 {"id": ..., "property": ...}；
  id 规则同上；property 必须是已声明属性。

检测在原有校验与前向链不动点推理完成后进行：

- 类成员事实取显式事实与推理结论中谓语为 classMembershipPredicate、
  主语为已声明个体、宾语为已声明类名的三元组。
- 同一 subject 属于一个 disjointClasses 项中的两个类即冲突。
- 同一 subject 对一个 functionalProperties 项的属性有两个不同宾语即冲突，
  完全相同的三元组不重复计数。

结构或取值错误抛 OntologyError（定位到字段、数组项或值）；
语义冲突抛 InconsistencyError，消息按（约束 id 的字符串表示、冲突类型、
subject）字典序列出全部冲突，同一输入消息稳定。
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .errors import InconsistencyError, OntologyError

_CONSISTENCY_KEYS = frozenset(
    {"classMembershipPredicate", "disjointClasses", "functionalProperties"}
)
_DISJOINT_KEYS = frozenset({"id", "classes"})
_FUNCTIONAL_KEYS = frozenset({"id", "property"})

_DISJOINT = "disjointClasses"
_FUNCTIONAL = "functionalProperties"


def _valid_id(value) -> bool:
    """id 必须是非空字符串或整数（bool 不算整数）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return bool(value)
    return isinstance(value, int)


class _DisjointConstraint:
    __slots__ = ("constraint_id", "classes")

    def __init__(self, constraint_id, classes: Tuple[str, ...]) -> None:
        self.constraint_id = constraint_id
        self.classes = classes  # 已排序，便于稳定枚举类对


class _FunctionalConstraint:
    __slots__ = ("constraint_id", "property")

    def __init__(self, constraint_id, property_: str) -> None:
        self.constraint_id = constraint_id
        self.property = property_


def parse_consistency(data, classes: Set[str], properties: Set[str]):
    """校验 consistency 段并返回 (成员谓语, 不相交类约束列表, 函数属性约束列表)。

    根对象不含 consistency 时返回 None；结构或取值错误抛 OntologyError。
    """
    if "consistency" not in data:
        return None
    raw = data["consistency"]
    if not isinstance(raw, dict):
        raise OntologyError(
            f"字段 'consistency' 必须是对象，收到 {type(raw).__name__}"
        )
    keys = set(raw)
    if keys != _CONSISTENCY_KEYS:
        missing = _CONSISTENCY_KEYS - keys
        extra = keys - _CONSISTENCY_KEYS
        detail = []
        if missing:
            detail.append("缺少 " + ", ".join(sorted(missing)))
        if extra:
            detail.append("多出 " + ", ".join(sorted(extra)))
        raise OntologyError(f"字段 'consistency' 结构不合法（{'; '.join(detail)}）")

    predicate = raw["classMembershipPredicate"]
    if not isinstance(predicate, str) or not predicate:
        raise OntologyError(
            "consistency.classMembershipPredicate 必须是非空字符串，"
            f"收到 {predicate!r}"
        )
    if predicate not in properties:
        raise OntologyError(
            "consistency.classMembershipPredicate 引用了未声明的属性 "
            f"{predicate!r}"
        )

    for field in (_DISJOINT, _FUNCTIONAL):
        value = raw[field]
        if not isinstance(value, list):
            raise OntologyError(
                f"consistency.{field} 必须是数组，收到 {type(value).__name__}"
            )

    seen_ids: Set[object] = set()
    disjoint: List[_DisjointConstraint] = []
    for index, item in enumerate(raw[_DISJOINT]):
        where = f"consistency.{_DISJOINT}[{index}]"
        constraint_id = _check_item_shape(item, where, _DISJOINT_KEYS, seen_ids)
        class_names = item["classes"]
        if not isinstance(class_names, list):
            raise OntologyError(
                f"{where}.classes 必须是类名数组，收到 {type(class_names).__name__}"
            )
        if len(class_names) < 2:
            raise OntologyError(
                f"{where}.classes 至少需要列出两个类名，收到 {class_names!r}"
            )
        checked: List[str] = []
        local_seen: Set[str] = set()
        for pos, name in enumerate(class_names):
            loc = f"{where}.classes[{pos}]"
            if not isinstance(name, str) or not name:
                raise OntologyError(
                    f"{loc} 必须是非空字符串类名，收到 {name!r}"
                )
            if name not in classes:
                raise OntologyError(f"{loc} 引用了未声明的类 {name!r}")
            if name in local_seen:
                raise OntologyError(
                    f"{where}.classes 中的类名 {name!r} 重复"
                )
            local_seen.add(name)
            checked.append(name)
        disjoint.append(_DisjointConstraint(constraint_id, tuple(sorted(checked))))

    functional: List[_FunctionalConstraint] = []
    for index, item in enumerate(raw[_FUNCTIONAL]):
        where = f"consistency.{_FUNCTIONAL}[{index}]"
        constraint_id = _check_item_shape(item, where, _FUNCTIONAL_KEYS, seen_ids)
        property_name = item["property"]
        if not isinstance(property_name, str) or not property_name:
            raise OntologyError(
                f"{where}.property 必须是非空字符串，收到 {property_name!r}"
            )
        if property_name not in properties:
            raise OntologyError(
                f"{where}.property 引用了未声明的属性 {property_name!r}"
            )
        functional.append(_FunctionalConstraint(constraint_id, property_name))

    return predicate, disjoint, functional


def _check_item_shape(item, where: str, expected_keys: Set[str], seen_ids: Set[str]):
    """校验约束对象的形状与 id，返回 id；跨数组通过 seen_ids 去重。"""
    if not isinstance(item, dict):
        raise OntologyError(
            f"{where} 必须是包含 id 的对象，收到 {item!r}"
        )
    keys = set(item)
    if keys != expected_keys:
        missing = expected_keys - keys
        extra = keys - expected_keys
        detail = []
        if missing:
            detail.append("缺少 " + ", ".join(sorted(missing)))
        if extra:
            detail.append("多出 " + ", ".join(sorted(extra)))
        raise OntologyError(f"{where} 结构不合法（{'; '.join(detail)}）")
    constraint_id = item["id"]
    if not _valid_id(constraint_id):
        raise OntologyError(
            f"{where}.id 必须是非空字符串或整数，收到 {constraint_id!r}"
        )
    if constraint_id in seen_ids:
        raise OntologyError(
            f"consistency 约束 id {constraint_id!r} 重复（{where}）"
        )
    seen_ids.add(constraint_id)
    return constraint_id


def check_consistency(
    spec,
    explicit: List,
    derived: Dict[object, object],
    individuals: Set[str],
    classes: Set[str],
) -> None:
    """在不动点推理结果上检测冲突；存在冲突时抛 InconsistencyError。"""
    predicate, disjoint_constraints, functional_constraints = spec

    # 事实来源：显式事实 None；推理结论为规则 id。
    sources: Dict[object, object] = {triple: None for triple in explicit}
    sources.update(derived)
    facts = tuple(sorted(sources))

    membership: Dict[str, Dict[str, object]] = {}
    by_property: Dict[str, Dict[str, Dict[str, object]]] = {}
    for triple in facts:
        if (
            triple.predicate == predicate
            and triple.subject in individuals
            and triple.object in classes
        ):
            membership.setdefault(triple.subject, {})[triple.object] = sources[triple]
        by_property.setdefault(triple.predicate, {}).setdefault(
            triple.subject, {}
        ).setdefault(triple.object, sources[triple])

    # 每项为 (排序键, 展示行)；排序键保证同一输入消息稳定。
    entries: List[Tuple[Tuple, str]] = []

    for constraint in disjoint_constraints:
        allowed = set(constraint.classes)
        for subject in sorted(membership):
            owned = allowed & membership[subject].keys()
            if len(owned) < 2:
                continue
            ordered = sorted(owned)
            for pos in range(len(ordered)):
                for next_pos in range(pos + 1, len(ordered)):
                    c1, c2 = ordered[pos], ordered[next_pos]
                    line = (
                        f"约束 {constraint.constraint_id!r} {_DISJOINT}："
                        f"subject {subject!r} 同时属于互不相交的类 {c1!r} 与 {c2!r}"
                        f"（{c1!r} {_describe_source(membership[subject][c1])}；"
                        f"{c2!r} {_describe_source(membership[subject][c2])}）"
                    )
                    key = (
                        str(constraint.constraint_id),
                        _DISJOINT,
                        subject,
                        c1,
                        c2,
                    )
                    entries.append((key, line))

    for constraint in functional_constraints:
        subjects = by_property.get(constraint.property, {})
        for subject in sorted(subjects):
            objects = subjects[subject]
            if len(objects) < 2:
                continue
            ordered_objects = sorted(objects)
            detail = "；".join(
                f"{obj!r} {_describe_source(objects[obj])}" for obj in ordered_objects
            )
            line = (
                f"约束 {constraint.constraint_id!r} {_FUNCTIONAL}："
                f"subject {subject!r} 对属性 {constraint.property!r} "
                f"存在 {len(ordered_objects)} 个不同宾语（{detail}）"
            )
            key = (
                str(constraint.constraint_id),
                _FUNCTIONAL,
                subject,
            )
            entries.append((key, line))

    if not entries:
        return

    entries.sort(key=lambda entry: entry[0])
    lines = [
        f"{index}. {line}"
        for index, (_, line) in enumerate(entries, start=1)
    ]
    raise InconsistencyError(
        f"检测到 {len(entries)} 处一致性冲突：\n" + "\n".join(lines)
    )


def _describe_source(rule_id) -> str:
    """显式事实标注为显式事实，推理结论标注来源规则 id。"""
    if rule_id is None:
        return "来自显式事实"
    return f"来自规则 {rule_id!r}"
