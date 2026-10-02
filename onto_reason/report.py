"""结构化一致性诊断报告：OntologyEngine.diagnose 的返回值。

- is_consistent：本体是否通过全部一致性约束。
- model：一致时为推理完成后的 OntologyModel；存在冲突时为 None。
- diagnostics：诊断元组，无冲突时为空元组；每条诊断为字段固定的字典，
  读取接口返回新的副本，不会改变报告内部数据，同一输入重复诊断结果稳定。
- explain_diagnostic(index)：给出第 index 条诊断中两条证据事实的证明，
  顺序与该诊断的 evidence 一致，元素为 Proof 元组。
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
    不一致时 model 为 None、diagnostics 列出全部两两冲突，顺序与
    parse 抛出的 InconsistencyError 中的冲突行一致。

    不一致报告内部仍保留推理后的模型与每条诊断对应的两条证据三元组，
    仅供 explain_diagnostic 追溯证明使用，不通过任何公开字段暴露。
    """

    __slots__ = (
        "_is_consistent",
        "_model",
        "_diagnostics",
        "_explanation_model",
        "_evidence_triples",
    )

    def __init__(
        self,
        is_consistent: bool,
        model: Optional[OntologyModel],
        diagnostics,
        _evidence_triples: Tuple[Tuple[Triple, Triple], ...] = (),
        _explanation_model: Optional[OntologyModel] = None,
    ) -> None:
        self._is_consistent = bool(is_consistent)
        self._model = model
        self._diagnostics: Tuple[dict, ...] = tuple(
            _freeze(diagnostic) for diagnostic in diagnostics
        )
        self._explanation_model = _explanation_model
        self._evidence_triples = tuple(tuple(pair) for pair in _evidence_triples)

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

    def explain_diagnostic(self, index) -> Tuple[Tuple[Proof, ...], Tuple[Proof, ...]]:
        """返回第 index 条诊断两条证据事实的证明，顺序与 evidence 一致。

        返回二元组，每项是对应证据三元组的 Proof 元组：显式事实恰含一个
        kind 为 "explicit"、ruleId 为 None、premises 为空的证明；推理事实
        为 OntologyModel.explain 给出的全部最短完整证明（保留原始规则 id、
        规则 if 模式顺序与稳定排序，premises 逐层追到显式叶子，不含依赖
        循环的证明）。disjointClassMembership 的两项证明对应个体经当前
        classMembershipPredicate 表达的两项类成员事实；
        functionalPropertyValue 的两项证明对应个体在约束属性上的两个
        不同取值事实。

        重复调用结果一致；返回的元组、Proof 与嵌套 premises 均为只读
        快照，调用方修改不影响后续调用。下标为非整数、布尔值、负数或
        越界，以及 diagnostics 为空时调用，均抛出 OntologyError，消息
        包含收到的下标或空诊断说明。
        """
        if not self._diagnostics:
            raise OntologyError(
                f"explain_diagnostic 收到下标 {index!r}，但当前报告的 "
                f"diagnostics 为空，没有可追溯的诊断"
            )
        if isinstance(index, bool) or not isinstance(index, int):
            raise OntologyError(
                f"explain_diagnostic 的诊断下标必须是整数，收到 {index!r}"
                f"（类型 {type(index).__name__}）"
            )
        if index < 0 or index >= len(self._diagnostics):
            raise OntologyError(
                f"explain_diagnostic 的诊断下标越界：收到 {index!r}，"
                f"有效范围为 0 到 {len(self._diagnostics) - 1}"
            )
        explanation_model = self._explanation_model or self._model
        if explanation_model is None:  # pragma: no cover - 引擎产出的冲突报告必带模型
            raise OntologyError(
                f"explain_diagnostic 无法追溯下标 {index!r}：报告缺少推理模型"
            )
        pair = self._evidence_triples[index]
        return tuple(
            explanation_model.explain(triple.subject, triple.predicate, triple.object)
            for triple in pair
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
