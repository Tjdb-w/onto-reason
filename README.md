# Onto Reason

本体验证与规则推理引擎：解析本体定义，支持基于规则的蕴含推理与 SPARQL 风格查询，并对不一致的本体给出可定位的诊断。

## 范围

本仓库从零开始实现上述方向的可用工具，不依赖外部同类实现。

## 状态

初始基线：只有本说明，尚无实现。

## 约定

- 公开行为以 README 与源码为准。
- 后续需求在此基线上增量实现。

## 查询

`OntologyModel.query` 接受 SPARQL 风格的 SELECT 查询：

```
SELECT (?变量 ... | *) WHERE {
  三元组模式 ('.' 三元组模式)*
  (OPTIONAL { 三元组模式 ('.' 三元组模式)* }
   | FILTER ( 表达式 )
   | '{' 三元组模式 ('.' 三元组模式)* '}'
     (UNION '{' 三元组模式 ('.' 三元组模式)* '}')+)*
}
```

- `OPTIONAL { ... }` 按左连接处理：有匹配时用所有匹配扩展绑定，
  无匹配时保留原绑定一次且块内变量未绑定；OPTIONAL 不得嵌套。
- `{ ... } UNION { ... }` 把相邻花括号分支的解取并集，连续 UNION 从左到右
  结合；分支内只含三元组模式（不得嵌套 UNION/OPTIONAL/FILTER，不得为空），
  仅在一分支绑定的变量随该分支保留，UNION 之后的 FILTER 在合并结果上执行。
- `FILTER` 表达式支持 `BOUND(?v)`、`!BOUND(?v)`、`?v = ?w`、`?v != ?w`
  以及字符串常量参与的 `=` / `!=` 比较；涉及未绑定变量的比较结果为假，
  多个 FILTER 按逻辑与共同过滤。
- 未绑定的投影变量在结果行中以 `None` 占位；结果按投影去重并按字典序排序。

## 结构化诊断

`OntologyEngine.parse` 在发现语义冲突（互斥类、函数型属性多值）时抛出
`InconsistencyError`；需要程序化读取诊断的调用方可改用
`OntologyEngine.diagnose`。它接受与 `parse` 相同的输入（`str` 或 UTF-8
`bytes`），输入类型、JSON、UTF-8 或结构非法时同样抛出带定位信息的
`OntologyError`，但语义冲突不抛异常，而是返回 `ValidationReport`：

- `is_consistent`：无冲突为 `True`，否则为 `False`。
- `model`：一致时为完成前向链推理后的 `OntologyModel`（与 `parse` 的结果
  内容相同）；冲突时为 `None`。
- `diagnostics`：全部可发现两两冲突的元组，顺序与 `InconsistencyError`
  中的冲突行一致；无冲突为空元组。

每条诊断是字段固定为 `kind`、`constraintId`、`subject`、`evidence`、
`message` 的字典：

- `kind` 为 `disjointClassMembership`（互斥类）或
  `functionalPropertyValue`（函数型属性多值）。
- `constraintId` 保留约束的原始字符串或整数 id；`subject` 为冲突个体。
- `message` 与 `parse` 抛出的对应冲突行（含 `[...]` 前缀与正文）完全一致。
- `evidence` 恰含两个条目并按类名（互斥类）或取值（函数型属性）字典序
  排列：互斥类条目为 `{"class": ..., "source": ...}`，函数型属性条目为
  `{"object": ..., "source": ...}`。显式事实的 `source` 为
  `{"kind": "explicit"}`；推理事实为
  `{"kind": "derived", "ruleId": <原始规则 id>}`。

重复诊断同一输入得到内容相等的报告。

