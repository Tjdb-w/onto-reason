"""onto_reason —— 零依赖本体定义解析与规则蕴含推理。

公开入口：

- :class:`OntologyEngine`：接收 UTF-8 文本（或 bytes）形式的 JSON 本体定义，
  校验后返回可重复读取的 :class:`OntologyModel`。
- :class:`OntologyModel`：列出显式三元组、推理三元组、全部三元组（显式优先），
  支持单三元组蕴含判断与来源规则查询；读取接口不改变已解析数据。
- :class:`Triple`：``subject/predicate/object`` 三元组，可直接解包。
- :class:`OntologyError`：所有输入校验与推理错误的统一异常。

仅使用 Python 标准库（``json`` 等），不依赖任何外部同类引擎。
"""

from .errors import OntologyError
from .model import Triple, OntologyModel
from .engine import OntologyEngine

__all__ = ["OntologyEngine", "OntologyModel", "Triple", "OntologyError"]
