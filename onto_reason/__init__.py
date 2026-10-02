"""Onto Reason：本体定义解析与规则前向链推理。"""

from .engine import OntologyEngine
from .errors import InconsistencyError, OntologyError
from .model import DerivedTriple, OntologyModel, Triple
from .query import QueryResult
from .report import ValidationReport

__all__ = [
    "OntologyEngine",
    "OntologyError",
    "InconsistencyError",
    "OntologyModel",
    "Triple",
    "DerivedTriple",
    "QueryResult",
    "ValidationReport",
]
