"""结构化一致性诊断报告：OntologyEngine.diagnose 的返回值。

- is_consistent：本体是否通过全部一致性约束。
- model：一致时为推理完成后的 OntologyModel；存在冲突时为 None。
- diagnostics：诊断元组，无冲突时为空元组；每条诊断为字段固定的字典，
  读取接口返回新的副本，不会改变报告内部数据，同一输入重复诊断结果稳定。
- explain_diagnostic(index)：给出指定诊断中两条冲突事实的证明，
  显式事实为单个 explicit Proof，推理事实为全部最短完整规则证明。
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Tuple

from .errors import OntologyError
from .model import OntologyModel, Proof, Triple


def _freeze(value: Any) -> Any:
    """把诊断中的 list/dict 复制为 tuple/dict 快照，隔离调用方的后续修改。"""
    if isinstance(value, dict):
        return {key: _freeze(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _model_signature(model: Optional[OntologyModel]):
    """模型按其显式/推理内容比较；None 只与 None 相等。"""
    if model is None:
        return None
    return model.explicit_triples, model.derived_triples


class ValidationReport:
    """本体一致性诊断的只读结果。

    一致时 model 为推理后的 OntologyModel、diagnostics 为空元组；
    不一致时公开的 model 为 None、diagnostics 列出全部两两冲突，顺序与
    parse 抛出的 InconsistencyError 中的冲突行一致。不一致时报告内部仍
    持有推理后的模型与每条诊断对应的两条证据三元组，仅供
    explain_diagnostic 追溯证明，不通过任何公开字段暴露。
    """

    __slots__ = (
        "_is_consistent",
        "_model",
        "_diagnostics",
        "_evidence_model",
        "_evidence_triples",
    )

    def __init__(
        self,
        is_consistent: bool,
        model: Optional[OntologyModel],
        diagnostics,
        evidence_model: Optional[OntologyModel] = None,
        evidence_triples: Optional[Tuple[Tuple[Triple, Triple], ...]] = None,
    ) -> None:
        self._is_consistent = bool(is_consistent)
        self._model = model
        self._diagnostics: Tuple[dict, ...] = tuple(
            _freeze(diagnostic) for diagnostic in diagnostics
        )
        self._evidence_model = evidence_model
        self._evidence_triples: Tuple[Tuple[Triple, Triple], ...] = tuple(
            (pair[0], pair[1]) for pair in (evidence_triples or ())
        )

    @property
    def is_consistent(self) -> bool:
        return self._is_consistent

    @property
    def model(self) -> Optional[OntologyModel]:
        """一致时为推理后的 OntologyModel；不一致时为 None。"""
        return self._model

    @property
    def diagnostics(self) -> Tuple[dict, ...]:
        """诊断字典的新元组；重复读取返回内容相同的独立副本。"""
        return tuple(_freeze(diagnostic) for diagnostic in self._diagnostics)

    def explain_diagnostic(self, index: int) -> Tuple[Tuple[Proof, ...], Tuple[Proof, ...]]:
        """返回 diagnostics[index] 中两条冲突事实的完整证明。

        返回值为与该诊断 evidence 两项顺序一致的二元组：

        - disjointClassMembership：两项分别证明个体经当前
          classMembershipPredicate 表达的两项类成员事实
          (subject, classMembershipPredicate, class)；
        - functionalPropertyValue：两项分别证明个体在约束属性上的两个
          不同取值事实 (subject, property, object)。
        - asymmetricPropertyPair：两项分别证明约束属性上的两条反向事实
          (first, property, second) 与 (second, property, first)，first/second
          为无序节点对按字典序排列后的两端；自反违反时两项指向同一事实
          (subject, property, subject)。
        - inverseFunctionalPropertyValue：两项分别证明同一宾语被两个不同
          主语指向的事实 (first, property, object) 与
          (second, property, object)，first/second 为按字典序排列的主语。
        - irreflexivePropertySelf：两项指向同一自反事实
          (subject, property, subject)。

        显式事实的证明元组恰含一个 kind 为 "explicit"、ruleId 为 None、
        premises 为空的 Proof；推理事实的证明元组为 OntologyModel.explain
        给出的全部最短完整证明，保留原始规则 id、规则 if 模式顺序与稳定
        排序，premises 逐层指向推导前提直至显式叶子；成环规则下有限结束
        且不包含依赖循环的证明。重复调用结果一致，返回的元组、Proof 与
        嵌套 premises 均为只读快照，调用方修改不影响后续调用。

        下标非整数（bool 不算）、为负数、越界，或 diagnostics 为空时
        抛出 OntologyError，消息包含收到的下标或空诊断说明。
        """
        if isinstance(index, bool) or not isinstance(index, int):
            raise OntologyError(
                "explain_diagnostic 的下标必须是整数，"
                f"收到 {index!r}（{type(index).__name__}）"
            )
        count = len(self._diagnostics)
        if count == 0:
            raise OntologyError(
                f"diagnostics 为空，不存在可解释的诊断（收到下标 {index!r}）"
            )
        if index < 0 or index >= count:
            raise OntologyError(
                f"诊断下标 {index!r} 越界：共有 {count} 条诊断，"
                f"合法下标范围为 0 至 {count - 1}"
            )
        if self._evidence_model is None or index >= len(self._evidence_triples):
            # 仅由 OntologyEngine.diagnose 构造的报告才带追溯所需的模型与证据；
            # 手工构造的 ValidationReport 无此内部数据。
            raise OntologyError(
                f"诊断下标 {index!r} 缺少可追溯的推理模型与证据三元组："
                "该报告不是由 OntologyEngine.diagnose 产生"
            )
        first, second = self._evidence_triples[index]
        return (
            tuple(self._evidence_model.explain(*first)),
            tuple(self._evidence_model.explain(*second)),
        )

    def __len__(self) -> int:
        return len(self._diagnostics)

    def __iter__(self) -> Iterator[dict]:
        return iter(self.diagnostics)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ValidationReport):
            return NotImplemented
        return (
            self._is_consistent == other._is_consistent
            and _model_signature(self._model) == _model_signature(other._model)
            and self._diagnostics == other._diagnostics
        )

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return (
            f"ValidationReport(is_consistent={self._is_consistent!r}, "
            f"model={'None' if self._model is None else 'OntologyModel(...)'}, "
            f"diagnostics={self._diagnostics!r})"
        )
