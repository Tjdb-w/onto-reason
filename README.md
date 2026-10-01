# Onto Reason

本体验证与规则推理引擎：解析本体定义，支持基于规则的蕴含推理与 SPARQL 风格查询，并对不一致的本体给出可定位的诊断。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

- `OntologyEngine.parse(text)`：解析 JSON 本体定义，做规则前向链推理，返回只读的 `OntologyModel`。
- `OntologyModel`：通过 `explicit_triples`、`derived_triples`、`triples`、`entails`、`source_rule` 暴露显式事实与推理结论。
- `OntologyModel.query(text)`：执行 SPARQL 风格基本图模式查询，如 `SELECT ?x ?y WHERE { ?x knows ?y }`，返回 `QueryResult(variables, rows)`；显式事实与推理结论均可命中，结果去重并按字典序排列。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。
