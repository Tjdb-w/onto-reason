"""Onto Reason 的错误类型。"""


class OntologyError(Exception):
    """本体定义解析、校验或推理过程中出现的统一错误。

    消息中会包含可定位的信息（规则 id 或三元组位置）。
    """


class InconsistencyError(OntologyError):
    """本体通过结构校验与不动点推理后发现的语义冲突。

    冲突包括：同一个体同时属于一对互斥类（disjointClasses），
    同一个体在函数型属性上拥有两个不同取值（functionalProperties），
    或一条 asymmetricProperties 约束属性上同时存在互相反向的三元组
    (a, p, b) 与 (b, p, a)（a 等于 b 时 (a, p, a) 单独构成一次自反违反）。
    抛出本错误时不返回解析模型。
    """
