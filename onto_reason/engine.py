"""本体 JSON 定义的校验与确定性前向链规则推理。

输入根对象：

    {
      "classes":     ["Person", ...],
      "properties":  ["knows", ...],
      "individuals": ["alice", ...],
      "triples":     [["alice", "knows", "bob"], ...],
      "rules": [
        {"id": "r1",
         "if":   [["?x", "knows", "?y"]],
         "then": [["?y", "knows", "?x"]]}
      ]
    }

校验顺序固定为：JSON 语法 -> 根对象字段类型 -> 名称重复 ->
显式三元组结构 -> 规则结构（含变量绑定）-> then 合法性。
任一环节失败抛出 :class:`OntologyError`，消息带规则 id 或数组下标。

推理为朴素前向链（fixpoint）：规则按输入顺序应用，直到不再产生新三元组；
同一三元组不会重复生成，来源归属于输入顺序中最早推出它的规则。
结果与集合迭代顺序无关，同一输入重复解析结果一致。
"""

import json
from typing import Any, Dict, List, Set, Tuple

from .errors import OntologyError
from .model import OntologyModel, Triple

_VAR_PREFIX = "?"


class _Rule:
    """内部规则表示（已通过结构校验）。"""

    __slots__ = ("id", "if_", "then")

    def __init__(self, rule_id: str, if_: List[List[str]],
                 then: List[List[str]]) -> None:
        self.id = rule_id
        self.if_ = if_
        self.then = then


class OntologyEngine:
    """本体定义解析器与规则蕴含推理引擎（无状态、可重复调用）。"""

    # ------------------------------------------------------------ 公开入口

    def parse(self, text) -> OntologyModel:
        """解析 UTF-8 文本（``str`` 或 ``bytes``）形式的 JSON 本体定义。

        成功返回 :class:`OntologyModel`；任何语法或语义问题抛出
        :class:`OntologyError`。引擎本身不保存解析状态，对同一输入
        重复调用得到等价模型。
        """
        data = self._load_json(text)
        classes, properties, individuals = self._validate_declarations(data)
        explicit = self._validate_triples(data, set(properties), set(individuals))
        rules = self._validate_rules(data, set(properties), set(individuals))
        inferred = self._forward_chain(explicit, rules)
        return OntologyModel(
            classes=classes,
            properties=properties,
            individuals=individuals,
            explicit=explicit,
            inferred=inferred,
        )

    def __call__(self, text) -> OntologyModel:
        return self.parse(text)

    # ------------------------------------------------------------ 1. JSON

    @staticmethod
    def _load_json(text) -> Any:
        if isinstance(text, str):
            payload = text
        elif isinstance(text, (bytes, bytearray)):
            try:
                payload = bytes(text).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OntologyError(f"输入不是合法的 UTF-8 文本: {exc}") from exc
        else:
            raise OntologyError(
                f"输入必须是 str 或 bytes 类型的 JSON 文本，实际为 {type(text).__name__}"
            )
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise OntologyError(
                f"JSON 语法错误（第 {exc.lineno} 行第 {exc.colno} 列）: {exc.msg}"
            ) from exc

    # ------------------------------------------------------------ 2. 声明区

    @staticmethod
    def _validate_declarations(data: Any):
        if not isinstance(data, dict):
            raise OntologyError("JSON 根对象必须是对象（object）")

        list_fields = ("classes", "properties", "individuals", "triples", "rules")
        for field in list_fields:
            if field not in data:
                raise OntologyError(f"根对象缺少必需字段 {field!r}")
            if not isinstance(data[field], list):
                raise OntologyError(
                    f"根字段 {field!r} 必须是数组，实际为 {type(data[field]).__name__}"
                )

        declarations: Dict[str, str] = {}
        result = {}
        for field in ("classes", "properties", "individuals"):
            names = []
            for index, item in enumerate(data[field]):
                if not isinstance(item, str):
                    raise OntologyError(
                        f"{field}[{index}] 必须是字符串名称，实际为 {type(item).__name__}"
                    )
                if item == "":
                    raise OntologyError(f"{field}[{index}] 不能是空字符串名称")
                if item.startswith(_VAR_PREFIX):
                    raise OntologyError(
                        f"{field}[{index}] 名称 {item!r} 非法：名称不能以 "
                        f"{_VAR_PREFIX!r} 开头（该前缀用于规则变量）"
                    )
                if item in declarations:
                    raise OntologyError(
                        f"名称重复：{item!r} 同时声明在 {declarations[item]} 与 {field}"
                        f"（下标 {index}），所有 class/property/individual 名称必须唯一"
                    )
                declarations[item] = field
                names.append(item)
            result[field] = names
        return result["classes"], result["properties"], result["individuals"]

    # ------------------------------------------------------------ 3. 三元组

    def _validate_triples(
        self, data: Any, properties: Set[str], individuals: Set[str]
    ) -> List[Triple]:
        triples = []
        for index, raw in enumerate(data["triples"]):
            where = f"triples[{index}]"
            s, p, o = self._parse_ground_pattern(raw, where, properties, individuals)
            triples.append(Triple(s, p, o))
        return triples

    @staticmethod
    def _parse_ground_pattern(
        raw: Any, where: str, properties: Set[str], individuals: Set[str]
    ) -> Tuple[str, str, str]:
        """校验一条不允许出现变量的三元组（显式三元组使用）。"""
        terms = OntologyEngine._check_triple_shape(raw, where)
        s, p, o = terms
        if s.startswith(_VAR_PREFIX) or o.startswith(_VAR_PREFIX):
            raise OntologyError(
                f"{where} = {terms!r}：显式三元组的主语和宾语不能使用变量"
            )
        if s not in individuals:
            raise OntologyError(
                f"{where} = {terms!r}：主语 {s!r} 不是已声明的 individual"
            )
        if o not in individuals:
            raise OntologyError(
                f"{where} = {terms!r}：宾语 {o!r} 不是已声明的 individual"
            )
        if p not in properties:
            raise OntologyError(
                f"{where} = {terms!r}：谓语 {p!r} 不是已声明的 property"
            )
        return s, p, o

    @staticmethod
    def _check_triple_shape(raw: Any, where: str) -> List[str]:
        """三元组必须是长度恰为 3 的数组，且每项都是字符串。"""
        if not isinstance(raw, list):
            raise OntologyError(
                f"{where} 必须是包含 [主语, 谓语, 宾语] 的数组，"
                f"实际为 {type(raw).__name__}"
            )
        if len(raw) != 3:
            raise OntologyError(
                f"{where} = {raw!r}：三元组必须恰好包含 3 个元素，实际有 {len(raw)} 个"
            )
        terms = []
        for pos, term in enumerate(raw):
            if not isinstance(term, str):
                role = ("主语", "谓语", "宾语")[pos]
                raise OntologyError(
                    f"{where} = {raw!r}：{role}必须是字符串，实际为 {type(term).__name__}"
                )
            if term == "":
                role = ("主语", "谓语", "宾语")[pos]
                raise OntologyError(f"{where} = {raw!r}：{role}不能是空字符串")
            terms.append(term)
        return terms

    # ------------------------------------------------------------ 4. 规则

    def _validate_rules(
        self, data: Any, properties: Set[str], individuals: Set[str]
    ) -> List[_Rule]:
        rules: List[_Rule] = []
        seen_ids: Set[str] = set()
        for index, raw in enumerate(data["rules"]):
            where = f"rules[{index}]"
            if not isinstance(raw, dict):
                raise OntologyError(
                    f"{where} 必须是对象，实际为 {type(raw).__name__}"
                )
            for field in ("id", "if", "then"):
                if field not in raw:
                    raise OntologyError(f"{where} 缺少必需字段 {field!r}")

            rule_id = raw["id"]
            if not isinstance(rule_id, str) or rule_id == "":
                raise OntologyError(
                    f"{where} 的 id 必须是非空字符串，实际为 {rule_id!r}"
                )
            if rule_id in seen_ids:
                raise OntologyError(f"规则 id 重复：{rule_id!r}")
            seen_ids.add(rule_id)

            label = f"规则 {rule_id!r}"
            for field in ("if", "then"):
                if not isinstance(raw[field], list):
                    raise OntologyError(
                        f"{label} 的 {field} 必须是三元组数组，"
                        f"实际为 {type(raw[field]).__name__}"
                    )

            if_patterns = [
                self._check_rule_pattern(item, f"{label} if[{i}]",
                                         properties, individuals)
                for i, item in enumerate(raw["if"])
            ]
            then_patterns = [
                self._check_rule_pattern(item, f"{label} then[{i}]",
                                         properties, individuals)
                for i, item in enumerate(raw["then"])
            ]

            # 变量绑定：then 中出现的每个变量都必须在 if 中出现过。
            bound: Set[str] = set()
            for s, _p, o in if_patterns:
                if s.startswith(_VAR_PREFIX):
                    bound.add(s)
                if o.startswith(_VAR_PREFIX):
                    bound.add(o)
            for i, (s, _p, o) in enumerate(then_patterns):
                for term in (s, o):
                    if term.startswith(_VAR_PREFIX) and term not in bound:
                        raise OntologyError(
                            f"{label} then[{i}] = {[s, _p, o]!r}："
                            f"变量 {term} 未在 if 中绑定，then 中的变量必须由 if 约束"
                        )

            if not then_patterns:
                raise OntologyError(f"{label} 的 then 至少要包含一条三元组")

            rules.append(_Rule(rule_id, if_patterns, then_patterns))
        return rules

    @staticmethod
    def _check_rule_pattern(
        raw: Any, where: str, properties: Set[str], individuals: Set[str]
    ) -> List[str]:
        """校验规则中的三元组：谓语必须是已声明属性；
        主语/宾语必须是已声明个体或 ?变量。"""
        terms = OntologyEngine._check_triple_shape(raw, where)
        s, p, o = terms

        if p.startswith(_VAR_PREFIX):
            raise OntologyError(
                f"{where} = {terms!r}：谓语不能是变量，必须是已声明的 property"
            )
        if p not in properties:
            raise OntologyError(
                f"{where} = {terms!r}：谓语 {p!r} 不是已声明的 property"
            )
        for role, term in (("主语", s), ("宾语", o)):
            if term.startswith(_VAR_PREFIX):
                if len(term) == 1:
                    raise OntologyError(
                        f"{where} = {terms!r}：{role}变量缺少名称（不能只有 {_VAR_PREFIX!r}）"
                    )
            elif term not in individuals:
                raise OntologyError(
                    f"{where} = {terms!r}：{role} {term!r} 既不是已声明的 individual，"
                    f"也不是以 {_VAR_PREFIX} 开头的变量"
                )
        return terms

    # ------------------------------------------------------------ 5. 前向链

    @staticmethod
    def _forward_chain(
        explicit: List[Triple], rules: List[_Rule]
    ) -> List[Tuple[Triple, str]]:
        # 事实集合以三元组建表示；显式事实不记录规则来源。
        facts: Set[Tuple[str, str, str]] = {t.as_tuple() for t in explicit}
        sources: Dict[Tuple[str, str, str], str] = {}

        # Fixpoint：每轮按输入顺序应用所有规则。规则应用基于当前事实快照
        # 计算本轮全部候选结论，再统一并入，保证行为不依赖集合迭代顺序。
        while True:
            changed = False
            for rule in rules:
                conclusions = OntologyEngine._apply_rule(rule, facts)
                # 排序使加入顺序确定（来源归属因此确定且可复现）。
                for key in sorted(conclusions):
                    if key not in facts:
                        facts.add(key)
                        sources[key] = rule.id
                        changed = True
            if not changed:
                break

        return [(Triple(*key), rule_id) for key, rule_id in sources.items()]

    @staticmethod
    def _apply_rule(
        rule: _Rule, facts: Set[Tuple[str, str, str]]
    ) -> Set[Tuple[str, str, str]]:
        """枚举 if 的全部匹配绑定，生成 then 结论集合。

        采用按模式顺序的嵌套循环连接；事实按字典序遍历，
        同变量位置做一致性校验。
        """
        bindings: List[Dict[str, str]] = [{}]
        for ps, pp, po in rule.if_:
            next_bindings: List[Dict[str, str]] = []
            for env in bindings:
                for fs, fp, fo in sorted(facts):
                    if fp != pp:
                        continue
                    candidate = dict(env)
                    if not OntologyEngine._unify(ps, fs, candidate):
                        continue
                    if not OntologyEngine._unify(po, fo, candidate):
                        continue
                    next_bindings.append(candidate)
            bindings = next_bindings
            if not bindings:
                break

        conclusions: Set[Tuple[str, str, str]] = set()
        for env in bindings:
            for ts, tp, to in rule.then:
                conclusions.add(
                    (
                        OntologyEngine._resolve(ts, env),
                        tp,
                        OntologyEngine._resolve(to, env),
                    )
                )
        return conclusions

    @staticmethod
    def _unify(pattern: str, value: str, env: Dict[str, str]) -> bool:
        """个体常量要求字面相等；变量要求与既有绑定一致，否则建立绑定。"""
        if pattern.startswith(_VAR_PREFIX):
            existing = env.get(pattern)
            if existing is None:
                env[pattern] = value
                return True
            return existing == value
        return pattern == value

    @staticmethod
    def _resolve(term: str, env: Dict[str, str]) -> str:
        if term.startswith(_VAR_PREFIX):
            return env[term]
        return term
