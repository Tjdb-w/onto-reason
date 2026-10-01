"""Onto Reason 的错误类型。"""


class OntologyError(Exception):
    """本体定义解析、校验或推理过程中出现的统一错误。

    消息中会包含可定位的信息（字段、数组项、规则 id 或三元组位置）。
    """


class InconsistencyError(OntologyError):
    """本体结构合法但违反一致性约束时抛出的语义错误。

    消息中按（约束 id 的字符串表示、冲突类型、subject）字典序列出全部冲突，
    每条冲突注明参与事实来自显式事实还是推理规则及其规则 id。
    """
