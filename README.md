# Onto Reason

本体验证与规则推理引擎：解析本体定义，支持基于规则的蕴含推理与 SPARQL 风格查询，并对不一致的本体给出可定位的诊断。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 功能

- 解析 UTF-8 文本或 bytes 形式的 JSON 本体定义（classes/properties/individuals/triples/rules）。
- 规则前向链推理直至不动点，区分 `explicit_triples`、`derived_triples` 与 `source_rule`。
- 在显式事实与推理结论上执行 SPARQL 风格基本图模式查询。
- 可选的 `consistency` 一致性诊断：
  - `classMembershipPredicate`：指向已声明属性，用于识别类成员事实；
  - `disjointClasses`：不相交类约束（每项含 `id` 与至少两个不同的已声明类名）；
  - `functionalProperties`：函数属性约束（每项含 `id` 与已声明 `property`）。
  - 约束 `id` 为非空字符串或整数，跨两个数组合并后不得重复。
  - 原有校验与不动点推理完成后再检测：同一已声明个体属于同一约束中的两个不相交类、
    或同一主语对函数属性有两个不同宾语即冲突。
  - 结构或取值错误抛 `OntologyError`（定位到字段、数组项或值）；
    语义冲突抛 `InconsistencyError`（`OntologyError` 子类，公开导出）且不返回模型，
    消息按约束 id 的字符串表示、冲突类型、subject 字典序列出全部冲突及事实来源。

## 状态

JSON 解析校验、规则前向链推理、SPARQL 风格查询与一致性诊断均已实现。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
