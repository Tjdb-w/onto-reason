"""结构化一致性诊断报告：OntologyEngine.diagnose 的返回值。

- is_consistent：本体是否通过全部一致性约束。
- model：一致时为推理完成后的 OntologyModel；存在冲突时为 None。
- diagnostics：诊断元组，无冲突时为空元组；每条诊断为字段固定的字典，
  读取接口返回新的副本，不会改变报告内部数据，同一输入重复诊断结果稳定。
"""

from __future__ import annotations

from typing import Any, Iterator, Optional, Tuple

from .model import OntologyModel


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
    """

    __slots__ = ("_is_consistent", "_model", "_diagnostics")

    def __init__(
        self,
        is_consistent: bool,
        model: Optional[OntologyModel],
        diagnostics,
    ) -> None:
        self._is_consistent = bool(is_consistent)
        self._model = model
        self._diagnostics: Tuple[dict, ...] = tuple(
            _freeze(diagnostic) for diagnostic in diagnostics
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
