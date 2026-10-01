"""OntologyEngine：解析 JSON 本体定义并做规则前向链推理。

输入约定（UTF-8 文本形式的 JSON）：

- 根对象必须包含 classes、properties、individuals、triples、rules 五个字段。
- classes / properties / individuals：字符串名称数组；名称在三个数组的并集内不得重复。
- triples：显式三元组数组，每项为 {"subject": ..., "predicate": ..., "object": ...}，
  三个值均为字符串，不允许变量；predicate 必须是已声明属性。
- rules：规则数组，每项为 {"id": ..., "if": [...], "then": [...]}；
  id 为字符串或整数且不得重复；if/then 为三元组模式数组，结构与 triples 相同，
  主语/宾语可以是以 "?" 开头的变量；then 中出现的变量必须在 if 中被绑定。

推理：对规则做前向链推理直至不动点。结论按 (主语, 谓语, 宾语) 字典序输出，
同一三元组被多条规则推出时保留最先推出它的规则 id；显式三元组优先于推理结论。

根对象可选包含 consistency 字段做一致性诊断，其结构由 onto_reason.consistency
模块校验：原有校验与不动点推理完成后再检查不相交类与函数属性约束，
存在语义冲突时抛 InconsistencyError（OntologyError 的子类）且不返回模型。
"""

from __future__ import annotations

import json
from typing import Dict, List, Optional, Tuple

from .consistency import check_consistency, parse_consistency
from .errors import OntologyError
from .model import OntologyModel, Triple

_ROOT_FIELDS = ("classes", "properties", "individuals", "triples", "rules")
_TRIPLE_KEYS = frozenset({"subject", "predicate", "object"})
_RULE_KEYS = frozenset({"id", "if", "then"})
_VARIABLE_PREFIX = "?"


def _is_variable(value: str) -> bool:
    return value.startswith(_VARIABLE_PREFIX)


class _RulePattern:
    """一条编译后的规则：id + if 模式列表 + then 模式列表。"""

    __slots__ = ("rule_id", "if_patterns", "then_patterns")

    def __init__(self, rule_id, if_patterns, then_patterns) -> None:
        self.rule_id = rule_id
        self.if_patterns = if_patterns  # List[Tuple[str, str, str]]
        self.then_patterns = then_patterns


class OntologyEngine:
    """公开入口：解析本体定义文本，返回可重复读取的 OntologyModel。"""

    def parse(self, text) -> OntologyModel:
        """解析 UTF-8 文本形式的 JSON 本体定义并完成前向链推理。"""
        data = self._load_json(text)
        self._validate_root(data)
        properties = self._collect_names(data)
        explicit = self._parse_explicit_triples(data["triples"], properties)
        rules = self._parse_rules(data["rules"], properties)
        derived = self._forward_chain(explicit, rules)
        # consistency 段先做结构校验；语义冲突在原有校验与不动点推理之后检查。
        spec = parse_consistency(
            data, frozenset(data["classes"]), properties
        )
        if spec is not None:
            check_consistency(
                spec,
                explicit,
                derived,
                frozenset(data["individuals"]),
                frozenset(data["classes"]),
            )
        return OntologyModel(explicit, derived)

    def __call__(self, text) -> OntologyModel:
        return self.parse(text)

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

    def _collect_names(self, data) -> frozenset:
        """校验 classes/properties/individuals 并返回已声明属性集合。"""
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
        return frozenset(data["properties"])

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
