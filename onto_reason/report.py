"""结构化一致性诊断报告：OntologyEngine.diagnose 的返回值。

- is_consistent：本体是否通过全部一致性约束（无任何两两冲突）。
- model：一致时为完成前向链推理后的 OntologyModel；冲突时为 None。
- diagnostics：全部可发现冲突的结构化诊断元组，顺序与 parse 抛出的
  InconsistencyError 中的冲突行一致；无冲突时为空元组。

报告与其读取视图均不可变：diagnostics 固化为元组，重复诊断同一输入
得到内容相等的报告。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from .model import OntologyModel


class ValidationReport:
    """一次结构化诊断的结果：一致性标记、模型（可能为 None）与诊断元组。"""

    __slots__ = ("_is_consistent", "_model", "_diagnostics")

    def __init__(
        self,
        is_consistent: bool,
        model: Optional[OntologyModel],
        diagnostics: Tuple[Dict[str, Any], ...],
    ) -> None:
        self._is_consistent = bool(is_consistent)
        self._model = model
        self._diagnostics = tuple(diagnostics)

    @property
    def is_consistent(self) -> bool:
        """无冲突为 True；存在语义冲突为 False。"""
        return self._is_consistent

    @property
    def model(self) -> Optional[OntologyModel]:
        """一致时为推理后的 OntologyModel；冲突时为 None。"""
        return self._model

    @property
    def diagnostics(self) -> Tuple[Dict[str, Any], ...]:
        """全部冲突诊断，顺序与 InconsistencyError 的冲突行一致。"""
        return self._diagnostics

    @staticmethod
    def _model_key(model: Optional[OntologyModel]):
        if model is None:
            return None
        return (model.explicit_triples, model.derived_triples)

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, ValidationReport)
            and self._is_consistent == other._is_consistent
            and self._model_key(self._model) == self._model_key(other._model)
            and self._diagnostics == other._diagnostics
        )

    def __repr__(self) -> str:  # pragma: no cover - 便于调试
        return (
            f"ValidationReport(is_consistent={self._is_consistent!r}, "
            f"model={self._model!r}, diagnostics={self._diagnostics!r})"
        )
