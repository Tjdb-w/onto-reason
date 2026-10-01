"""Onto Reason：本体定义解析与规则蕴含推理。"""

from .engine import OntologyEngine
from .errors import OntologyError
from .model import DerivedTriple, OntologyModel, Triple
from .query import QueryResult

__all__ = [
    "OntologyEngine",
    "OntologyError",
    "OntologyModel",
    "Triple",
    "DerivedTriple",
    "QueryResult",
]
