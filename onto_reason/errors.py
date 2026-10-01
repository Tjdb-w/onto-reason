"""Onto Reason 的错误类型。"""


class OntologyError(Exception):
    """本体定义解析、校验或推理过程中出现的统一错误。

    消息中会包含可定位的信息（规则 id 或三元组位置）。
    """
