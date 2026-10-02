"""OntologyEngine：解析 JSON 本体定义并做规则前向链推理。

输入约定（UTF-8 文本形式的 JSON）：

- 根对象必须包含 classes、properties、individuals、triples、rules 五个字段，
  可选包含 consistency 字段。
- classes / properties / individuals：字符串名称数组；名称在三个数组的并集内不得重复。
- triples：显式三元组数组，每项为 {"subject": ..., "predicate": ..., "object": ...}，
  三个值均为字符串，不允许变量；predicate 必须是已声明属性。
- rules：规则数组，每项为 {"id": ..., "if": [...], "then": [...]}；
  id 为字符串或整数且不得重复；if/then 为三元组模式数组，结构与 triples 相同，
  主语/宾语可以是以 "?" 开头的变量；then 中出现的变量必须在 if 中被绑定。
- consistency：可选对象，恰含 classMembershipPredicate、disjointClasses、
  functionalProperties 三个字段。

推理：对规则做前向链推理直至不动点。结论按 (主语, 谓语, 宾语) 字典序输出，
同一三元组被多条规则推出时保留最先推出它的规则 id；显式三元组优先于推理结论。

一致性诊断在结构校验与不动点推理完成后进行：同一个体同时属于一条
disjointClasses 约束中的两个类、或在一条 functionalProperties 约束的属性上
有两个不同取值，parse 抛出 InconsistencyError 且不返回模型；diagnose 不抛
异常，改为返回 ValidationReport（is_consistent 为 False、model 为 None、
diagnostics 按与异常冲突行相同的顺序列出全部两两冲突）。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from .errors import InconsistencyError, OntologyError
from .model import OntologyModel, Triple
from .report import ValidationReport

_ROOT_FIELDS = ("classes", "properties", "individuals", "triples", "rules")
_TRIPLE_KEYS = frozenset({"subject", "predicate", "object"})
_RULE_KEYS = frozenset({"id", "if", "then"})
_VARIABLE_PREFIX = "?"

_CONSISTENCY_KEYS = frozenset(
    {"classMembershipPredicate", "disjointClasses", "functionalProperties"}
)
_DISJOINT_KEYS = frozenset({"id", "classes"})
_FUNCTIONAL_KEYS = frozenset({"id", "property"})

# 冲突类型的稳定排序：disjointClasses 在 functionalProperties 之前。
_CONFLICT_DISJOINT = 0
_CONFLICT_FUNCTIONAL = 1


def _is_variable(value: str) -> bool:
    return value.startswith(_VARIABLE_PREFIX)


def _is_id(value) -> bool:
    """consistency 约束 id：非空字符串或整数（bool 不算）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, str) and bool(value)


class _RulePattern:
    """一条编译后的规则：id + if 模式列表 + then 模式列表。"""

    __slots__ = ("rule_id", "if_patterns", "then_patterns")

    def __init__(self, rule_id, if_patterns, then_patterns) -> None:
        self.rule_id = rule_id
        self.if_patterns = if_patterns  # List[Tuple[str, str, str]]
        self.then_patterns = then_patterns


class _DisjointConstraint:
    __slots__ = ("constraint_id", "classes")

    def __init__(self, constraint_id, classes: Tuple[str, ...]) -> None:
        self.constraint_id = constraint_id
        self.classes = tuple(classes)


class _FunctionalConstraint:
    __slots__ = ("constraint_id", "property")

    def __init__(self, constraint_id, property_: str) -> None:
        self.constraint_id = constraint_id
        self.property = property_


class _Consistency:
    __slots__ = ("membership_predicate", "disjoint", "functional")

    def __init__(self, membership_predicate, disjoint, functional) -> None:
        self.membership_predicate = membership_predicate
        self.disjoint = disjoint
        self.functional = functional


class OntologyEngine:
    """公开入口：解析本体定义文本，返回可重复读取的 OntologyModel。"""

    def parse(self, text) -> OntologyModel:
        """解析 UTF-8 文本形式的 JSON 本体定义并完成前向链推理。

        发现语义冲突时抛出 InconsistencyError，不返回模型；结构与输入错误
        抛出 OntologyError。需要结构化诊断时使用 diagnose。
        """
        model, conflicts = self._build(text)
        if conflicts is not None:
            lines = [f"本体一致性诊断发现 {len(conflicts)} 处冲突："]
            lines.extend(conflict["message"] for conflict in conflicts)
            raise InconsistencyError("\n".join(lines))
        return model

    def diagnose(self, text) -> ValidationReport:
        """与 parse 相同的输入与结构校验，语义冲突以 ValidationReport 返回。

        输入类型、JSON、UTF-8 或结构非法仍抛 OntologyError（消息与 parse
        完全一致）。无冲突时报告的 is_consistent 为 True、model 为推理后的
        OntologyModel、diagnostics 为空元组；有冲突时 is_consistent 为 False、
        model 为 None、diagnostics 列出全部两两冲突，顺序与 parse 的
        InconsistencyError 冲突行一致。
        """
        model, conflicts = self._build(text)
        if conflicts is not None:
            return ValidationReport(False, None, conflicts)
        return ValidationReport(True, model, ())

    def __call__(self, text) -> OntologyModel:
        return self.parse(text)

    # ---------- 解析、推理与一致性检查的共同流程 ----------

    def _build(self, text) -> Tuple[OntologyModel, Optional[List[dict]]]:
        """返回 (模型, 冲突列表)；无冲突时冲突列表为 None。

        结构非法在抛出 OntologyError 前终止，与历史 parse 行为一致；
        一致性检查不通过时模型不返回（调用方只取冲突列表）。
        """
        data = self._load_json(text)
        self._validate_root(data)
        classes, properties, individuals = self._collect_names(data)
        explicit = self._parse_explicit_triples(data["triples"], properties)
        rules = self._parse_rules(data["rules"], properties)
        consistency = self._parse_consistency(data, classes, properties)
        derived = self._forward_chain(explicit, rules)
        conflicts: Optional[List[dict]] = None
        if consistency is not None:
            found = self._collect_conflicts(
                explicit, derived, consistency, classes, individuals
            )
            if found:
                conflicts = found
        model = OntologyModel(
            explicit,
            derived,
            properties,
            rules=tuple(
                (rule.rule_id, tuple(rule.if_patterns), tuple(rule.then_patterns))
                for rule in rules
            ),
        )
        return model, conflicts

    # ---------- 解析与校验 ----------

    def _load_json(self, text):
        if isinstance(text, (bytes, bytearray)):
            try:
                text = bytes(text).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OntologyError(f"输入不是合法的 UTF-8 文本: {exc}") from exc
        if not isinstance(text, str):
            raise OntologyError(
                f"输入必须是 str 或 bytes 类型的 JSON 文本，收到 {type(text).__name__}"
            )
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise OntologyError(
                f"JSON 语法错误: 第 {exc.lineno} 行第 {exc.colno} 列: {exc.msg}"
            ) from exc

    def _validate_root(self, data) -> None:
        if not isinstance(data, dict):
            raise OntologyError(
                f"根节点必须是 JSON 对象，收到 {type(data).__name__}"
            )
        for field in _ROOT_FIELDS:
            if field not in data:
                raise OntologyError(f"根对象缺少必需字段 {field!r}")
            if not isinstance(data[field], list):
                raise OntologyError(
                    f"字段 {field!r} 必须是数组，收到 {type(data[field]).__name__}"
                )

    def _collect_names(self, data) -> Tuple[frozenset, frozenset, frozenset]:
        """校验 classes/properties/individuals，返回三个已声明名称集合。"""
        seen = {}
        for field in ("classes", "properties", "individuals"):
            for index, name in enumerate(data[field]):
                if not isinstance(name, str) or not name:
                    raise OntologyError(
                        f"字段 {field!r} 第 {index} 项必须是非空字符串，"
                        f"收到 {name!r}"
                    )
                if name in seen:
                    raise OntologyError(
                        f"名称 {name!r} 重复声明（出现在 {seen[name]!r} 与 {field!r}）"
                    )
                seen[name] = field
        return (
            frozenset(data["classes"]),
            frozenset(data["properties"]),
            frozenset(data["individuals"]),
        )

    def _check_triple_shape(self, item, where: str) -> dict:
        if not isinstance(item, dict):
            raise OntologyError(
                f"{where} 必须是包含 subject/predicate/object 的对象，"
                f"收到 {item!r}"
            )
        keys = set(item)
        if keys != _TRIPLE_KEYS:
            missing = _TRIPLE_KEYS - keys
            extra = keys - _TRIPLE_KEYS
            detail = []
            if missing:
                detail.append("缺少 " + ", ".join(sorted(missing)))
            if extra:
                detail.append("多出 " + ", ".join(sorted(extra)))
            raise OntologyError(f"{where} 结构不合法（{'; '.join(detail)}）")
        for key in ("subject", "predicate", "object"):
            if not isinstance(item[key], str) or not item[key]:
                raise OntologyError(
                    f"{where} 的 {key!r} 必须是非空字符串，收到 {item[key]!r}"
                )
        return item

    def _parse_explicit_triples(self, triples, properties) -> List[Triple]:
        result: List[Triple] = []
        seen = set()
        for index, item in enumerate(triples):
            where = f"triples[{index}]"
            self._check_triple_shape(item, where)
            s, p, o = item["subject"], item["predicate"], item["object"]
            for key, value in (("subject", s), ("predicate", p), ("object", o)):
                if _is_variable(value):
                    raise OntologyError(
                        f"{where} 的 {key!r} 不允许使用变量 {value!r}（显式三元组必须是基三元组）"
                    )
            if p not in properties:
                raise OntologyError(
                    f"{where} 引用了未声明的属性 {p!r}"
                )
            triple = Triple(s, p, o)
            if triple not in seen:
                seen.add(triple)
                result.append(triple)
        return result

    def _parse_rules(self, rules, properties) -> List[_RulePattern]:
        parsed: List[_RulePattern] = []
        seen_ids = set()
        for index, item in enumerate(rules):
            where = f"rules[{index}]"
            if not isinstance(item, dict):
                raise OntologyError(
                    f"{where} 必须是包含 id/if/then 的对象，收到 {item!r}"
                )
            keys = set(item)
            if keys != _RULE_KEYS:
                missing = _RULE_KEYS - keys
                extra = keys - _RULE_KEYS
                detail = []
                if missing:
                    detail.append("缺少 " + ", ".join(sorted(missing)))
                if extra:
                    detail.append("多出 " + ", ".join(sorted(extra)))
                raise OntologyError(f"{where} 结构不合法（{'; '.join(detail)}）")

            rule_id = item["id"]
            if isinstance(rule_id, bool) or not isinstance(rule_id, (str, int)):
                raise OntologyError(
                    f"{where} 的 id 必须是字符串或整数，收到 {rule_id!r}"
                )
            if isinstance(rule_id, str) and not rule_id:
                raise OntologyError(f"{where} 的 id 不能为空字符串")
            if rule_id in seen_ids:
                raise OntologyError(f"规则 id {rule_id!r} 重复（{where}）")
            seen_ids.add(rule_id)
            where = f"规则 {rule_id!r}"

            for field in ("if", "then"):
                if not isinstance(item[field], list):
                    raise OntologyError(
                        f"{where} 的 {field!r} 必须是三元组数组，"
                        f"收到 {type(item[field]).__name__}"
                    )

            if_patterns = self._parse_patterns(item["if"], properties, where, "if")
            then_patterns = self._parse_patterns(item["then"], properties, where, "then")

            bound = set()
            for s, _, o in if_patterns:
                if _is_variable(s):
                    bound.add(s)
                if _is_variable(o):
                    bound.add(o)
            for pos, (s, _, o) in enumerate(then_patterns):
                for key, value in (("subject", s), ("object", o)):
                    if _is_variable(value) and value not in bound:
                        raise OntologyError(
                            f"{where} 的 then[{pos}] 中变量 {value!r} 未在 if 中绑定"
                        )
            parsed.append(_RulePattern(rule_id, if_patterns, then_patterns))
        return parsed

    def _parse_patterns(self, patterns, properties, where: str, field: str):
        result = []
        for pos, item in enumerate(patterns):
            loc = f"{where} 的 {field}[{pos}]"
            self._check_triple_shape(item, loc)
            s, p, o = item["subject"], item["predicate"], item["object"]
            if _is_variable(p):
                raise OntologyError(
                    f"{loc} 的谓语不允许是变量 {p!r}（谓语只能是已声明属性）"
                )
            if p not in properties:
                raise OntologyError(f"{loc} 引用了未声明的属性 {p!r}")
            result.append((s, p, o))
        return result

    # ---------- consistency 结构校验 ----------

    def _parse_consistency(self, data, classes: frozenset, properties: frozenset):
        if "consistency" not in data:
            return None
        root = data["consistency"]
        if not isinstance(root, dict):
            raise OntologyError(
                "字段 'consistency' 必须是对象，"
                f"收到 {type(root).__name__}"
            )
        keys = set(root)
        if keys != _CONSISTENCY_KEYS:
            missing = _CONSISTENCY_KEYS - keys
            extra = keys - _CONSISTENCY_KEYS
            detail = []
            if missing:
                detail.append("缺少 " + ", ".join(sorted(missing)))
            if extra:
                detail.append("多出 " + ", ".join(sorted(extra)))
            raise OntologyError(f"consistency 结构不合法（{'; '.join(detail)}）")

        predicate = root["classMembershipPredicate"]
        if not isinstance(predicate, str) or not predicate:
            raise OntologyError(
                "consistency['classMembershipPredicate'] 必须是非空字符串，"
                f"收到 {predicate!r}"
            )
        if predicate not in properties:
            raise OntologyError(
                "consistency['classMembershipPredicate'] 引用了未声明的属性 "
                f"{predicate!r}"
            )

        seen_ids = {}
        disjoint = self._parse_disjoint(root["disjointClasses"], classes, seen_ids)
        functional = self._parse_functional(
            root["functionalProperties"], properties, seen_ids
        )
        return _Consistency(predicate, disjoint, functional)

    def _claim_constraint_id(self, constraint_id, where: str, seen_ids: dict) -> None:
        if not _is_id(constraint_id):
            raise OntologyError(
                f"{where}['id'] 必须是非空字符串或整数，收到 {constraint_id!r}"
            )
        if constraint_id in seen_ids:
            raise OntologyError(
                f"consistency 约束 id {constraint_id!r} 重复"
                f"（{seen_ids[constraint_id]} 与 {where}）"
            )
        seen_ids[constraint_id] = where

    def _parse_disjoint(self, items, classes: frozenset, seen_ids: dict):
        if not isinstance(items, list):
            raise OntologyError(
                "consistency['disjointClasses'] 必须是对象数组，"
                f"收到 {type(items).__name__}"
            )
        result = []
        for index, item in enumerate(items):
            where = f"consistency['disjointClasses'][{index}]"
            if not isinstance(item, dict):
                raise OntologyError(
                    f"{where} 必须是包含 id/classes 的对象，收到 {item!r}"
                )
            keys = set(item)
            if keys != _DISJOINT_KEYS:
                missing = _DISJOINT_KEYS - keys
                extra = keys - _DISJOINT_KEYS
                detail = []
                if missing:
                    detail.append("缺少 " + ", ".join(sorted(missing)))
                if extra:
                    detail.append("多出 " + ", ".join(sorted(extra)))
                raise OntologyError(f"{where} 结构不合法（{'; '.join(detail)}）")

            self._claim_constraint_id(item["id"], where, seen_ids)

            names = item["classes"]
            if not isinstance(names, list):
                raise OntologyError(
                    f"{where}['classes'] 必须是字符串数组，"
                    f"收到 {type(names).__name__}"
                )
            for pos, name in enumerate(names):
                if not isinstance(name, str) or not name:
                    raise OntologyError(
                        f"{where}['classes'][{pos}] 必须是非空字符串，收到 {name!r}"
                    )
            if len(names) < 2:
                raise OntologyError(
                    f"{where}['classes'] 至少需要列出两个类，收到 {len(names)} 个"
                )
            local_seen = set()
            for name in names:
                if name in local_seen:
                    raise OntologyError(
                        f"{where}['classes'] 中类名 {name!r} 重复"
                    )
                local_seen.add(name)
            for name in names:
                if name not in classes:
                    raise OntologyError(
                        f"{where}['classes'] 引用了未声明的类 {name!r}"
                    )
            result.append(_DisjointConstraint(item["id"], names))
        return result

    def _parse_functional(self, items, properties: frozenset, seen_ids: dict):
        if not isinstance(items, list):
            raise OntologyError(
                "consistency['functionalProperties'] 必须是对象数组，"
                f"收到 {type(items).__name__}"
            )
        result = []
        for index, item in enumerate(items):
            where = f"consistency['functionalProperties'][{index}]"
            if not isinstance(item, dict):
                raise OntologyError(
                    f"{where} 必须是包含 id/property 的对象，收到 {item!r}"
                )
            keys = set(item)
            if keys != _FUNCTIONAL_KEYS:
                missing = _FUNCTIONAL_KEYS - keys
                extra = keys - _FUNCTIONAL_KEYS
                detail = []
                if missing:
                    detail.append("缺少 " + ", ".join(sorted(missing)))
                if extra:
                    detail.append("多出 " + ", ".join(sorted(extra)))
                raise OntologyError(f"{where} 结构不合法（{'; '.join(detail)}）")

            self._claim_constraint_id(item["id"], where, seen_ids)

            property_ = item["property"]
            if not isinstance(property_, str) or not property_:
                raise OntologyError(
                    f"{where}['property'] 必须是非空字符串，收到 {property_!r}"
                )
            if property_ not in properties:
                raise OntologyError(
                    f"{where}['property'] 引用了未声明的属性 {property_!r}"
                )
            result.append(_FunctionalConstraint(item["id"], property_))
        return result

    # ---------- 前向链推理 ----------

    def _forward_chain(self, explicit: List[Triple], rules: List[_RulePattern]) -> Dict[Triple, object]:
        """返回 dict[Triple, rule_id]：仅包含推理结论（不含显式三元组）。"""
        facts: Dict[Triple, Optional[object]] = {t: None for t in explicit}
        while True:
            changed = False
            # 每轮按排序后的事实列表匹配，保证绑定枚举顺序确定。
            fact_list = sorted(facts)
            for rule in rules:
                for binding in self._match(rule.if_patterns, fact_list):
                    for pattern in rule.then_patterns:
                        triple = Triple(
                            binding.get(pattern[0], pattern[0]),
                            pattern[1],
                            binding.get(pattern[2], pattern[2]),
                        )
                        if triple not in facts:
                            facts[triple] = rule.rule_id
                            changed = True
            if not changed:
                break
        return {t: rid for t, rid in facts.items() if rid is not None}

    def _match(self, patterns, fact_list) -> List[Dict[str, str]]:
        """对 if 模式按序做连接匹配，返回所有满足的变量绑定。"""
        bindings: List[Dict[str, str]] = [{}]
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
                    # 宾语变量需与同一模式内已绑定的主语变量保持一致（如 (?x, p, ?x)）。
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

    # ---------- 一致性诊断 ----------

    def _collect_conflicts(
        self,
        explicit: List[Triple],
        derived: Dict[Triple, object],
        consistency: _Consistency,
        classes: frozenset,
        individuals: frozenset,
    ) -> List[dict]:
        """收集全部两两冲突，按与 InconsistencyError 相同的顺序排列。"""
        # 全部事实的来源表：显式事实为 None，推理结论为来源规则 id。
        fact_source: Dict[Triple, Optional[object]] = {t: None for t in explicit}
        fact_source.update(derived)

        membership = self._membership_facts(
            fact_source, consistency.membership_predicate, classes, individuals
        )
        # 各收集器返回 (排序键, 诊断字典) 元组列表。
        conflicts = []
        conflicts.extend(
            self._disjoint_conflicts(membership, consistency.disjoint)
        )
        conflicts.extend(
            self._functional_conflicts(fact_source, consistency.functional)
        )
        if not conflicts:
            return []

        # 按约束 id 的字符串表示、冲突类型、subject、冲突条目字典序排列。
        conflicts.sort(key=lambda item: item[0])
        return [diagnostic for _, diagnostic in conflicts]

    def _membership_facts(
        self,
        fact_source: Dict[Triple, Optional[object]],
        predicate: str,
        classes: frozenset,
        individuals: frozenset,
    ) -> Dict[Tuple[str, str], Optional[object]]:
        """收集 {(subject, class): 来源}：仅保留已声明个体与已声明类上的类成员事实。"""
        membership: Dict[Tuple[str, str], Optional[object]] = {}
        for triple, source in fact_source.items():
            if triple.predicate != predicate:
                continue
            if triple.subject not in individuals:
                continue
            if triple.object not in classes:
                continue
            membership.setdefault((triple.subject, triple.object), source)
        return membership

    def _disjoint_conflicts(self, membership, constraints):
        conflicts = []
        for constraint in constraints:
            names = sorted(constraint.classes)
            holders: Dict[str, List[str]] = {}
            for subject, class_name in membership:
                if class_name in names:
                    holders.setdefault(subject, []).append(class_name)
            for subject, owned in holders.items():
                if len(owned) < 2:
                    continue
                owned_set = frozenset(owned)
                for pos, first in enumerate(names):
                    if first not in owned_set:
                        continue
                    for second in names[pos + 1 :]:
                        if second not in owned_set:
                            continue
                        src1 = membership[(subject, first)]
                        src2 = membership[(subject, second)]
                        message = (
                            f"[disjointClasses id={constraint.constraint_id!r}] "
                            f"个体 {subject!r} 同时属于互斥类 {first!r} 与 {second!r}："
                            f"{first!r} 为{self._describe_source(src1)}；"
                            f"{second!r} 为{self._describe_source(src2)}"
                        )
                        diagnostic = {
                            "kind": "disjointClassMembership",
                            "constraintId": constraint.constraint_id,
                            "subject": subject,
                            "evidence": (
                                {"class": first, "source": self._source_info(src1)},
                                {"class": second, "source": self._source_info(src2)},
                            ),
                            "message": message,
                        }
                        key = (
                            str(constraint.constraint_id),
                            _CONFLICT_DISJOINT,
                            subject,
                            first,
                            second,
                        )
                        conflicts.append((key, diagnostic))
        return conflicts

    def _functional_conflicts(self, fact_source: Dict[Triple, Optional[object]], constraints):
        conflicts = []
        for constraint in constraints:
            property_ = constraint.property
            # subject -> {object: 来源}
            by_subject: Dict[str, Dict[str, Optional[object]]] = {}
            for triple, source in fact_source.items():
                if triple.predicate != property_:
                    continue
                by_subject.setdefault(triple.subject, {}).setdefault(
                    triple.object, source
                )
            for subject, objects in by_subject.items():
                if len(objects) < 2:
                    continue
                ordered = sorted(objects)
                for pos, first in enumerate(ordered):
                    for second in ordered[pos + 1 :]:
                        src1 = objects[first]
                        src2 = objects[second]
                        message = (
                            f"[functionalProperties id={constraint.constraint_id!r}] "
                            f"个体 {subject!r} 在函数型属性 {property_!r} 上有两个不同取值 "
                            f"{first!r} 与 {second!r}："
                            f"{first!r} 为{self._describe_source(src1)}；"
                            f"{second!r} 为{self._describe_source(src2)}"
                        )
                        diagnostic = {
                            "kind": "functionalPropertyValue",
                            "constraintId": constraint.constraint_id,
                            "subject": subject,
                            "evidence": (
                                {"object": first, "source": self._source_info(src1)},
                                {"object": second, "source": self._source_info(src2)},
                            ),
                            "message": message,
                        }
                        key = (
                            str(constraint.constraint_id),
                            _CONFLICT_FUNCTIONAL,
                            subject,
                            property_,
                            first,
                            second,
                        )
                        conflicts.append((key, diagnostic))
        return conflicts

    @staticmethod
    def _source_info(source) -> dict:
        """结构化事实来源：显式事实为 {'kind': 'explicit'}；推理事实带原始规则 id。"""
        if source is None:
            return {"kind": "explicit"}
        return {"kind": "derived", "ruleId": source}

    @staticmethod
    def _describe_source(source) -> str:
        if source is None:
            return "显式事实（explicit_triples）"
        return f"推理结论（derived_triples，source_rule={source!r}）"
