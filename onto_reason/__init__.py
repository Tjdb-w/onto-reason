"""Onto Reason：本体定义解析、规则蕴含推理与一致性诊断。"""

from .engine import OntologyEngine
from .errors import InconsistencyError, OntologyError
from .model import DerivedTriple, OntologyModel, Triple
from .query import QueryResult

__all__ = [
    "OntologyEngine",
    "OntologyError",
    "InconsistencyError",
    "OntologyModel",
    "Triple",
    "DerivedTriple",
    "QueryResult",
]
