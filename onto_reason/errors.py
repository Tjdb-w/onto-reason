"""统一异常类型。"""


class OntologyError(ValueError):
    """输入本体定义存在语法或语义问题时抛出。

    消息中尽量包含可定位信息：规则 ``id`` 或三元组在其所在数组中的下标。
    继承 :class:`ValueError`，因此同时可被 ``except ValueError`` 捕获。
    """
