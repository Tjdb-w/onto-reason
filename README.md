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
- 三元组模式的谓语除常量属性名外还接受属性路径，路径只在模型的显式与
  推理三元组并集上求值，得到去重的节点对集合：
  `^p` 逆向，`a/b` 先走 `a` 再走 `b`（关系复合），`a|b` 两支取并集，
  `p?`、`p*`、`p+` 分别为零或一次、零或多次、一次或多次，圆括号只用于
  分组，运算符两侧允许空白，路径中的属性名必须已声明。
  `p?`/`p*` 的零次分支只连接当前全部三元组中实际出现过的主语或宾语，
  不产生新术语绑定；`p+`/`p*` 在环状数据上也会有限结束。OPTIONAL 与
  UNION 块内同样可以使用属性路径；FILTER 不把路径当作比较操作数。
- 未绑定的投影变量在结果行中以 `None` 占位；结果按投影去重并按字典序排序。

## 一致性诊断

`OntologyEngine.parse` 在结构校验与不动点推理完成后检查 `consistency` 约束，
发现互斥类成员或函数型属性多值冲突时抛出 `InconsistencyError`，不返回模型。

`OntologyEngine.diagnose` 接受与 `parse` 相同的输入（`str` 或 UTF-8 `bytes`），
结构、JSON、UTF-8 非法时同样抛出带定位信息的 `OntologyError`；语义冲突不抛异常，
而是返回只读的 `ValidationReport`：

- `is_consistent`：无冲突为 `True`，有冲突为 `False`。
- `model`：一致时为推理后的 `OntologyModel`（与 `parse` 返回值语义相同）；
  有冲突时为 `None`。
- `diagnostics`：诊断元组，无冲突为空；有冲突时包含全部两两冲突，顺序与
  `InconsistencyError` 中的冲突行一致。

每条诊断是字段固定为 `kind`、`constraintId`、`subject`、`evidence`、`message`
的字典：

- `kind` 为 `"disjointClassMembership"` 或 `"functionalPropertyValue"`；
  前者 `evidence` 恰含 `class` 与 `source` 两项且类名按字典序排列，
  后者恰含 `object` 与 `source` 两项且取值按字典序排列。
- `constraintId` 保留原始字符串或整数。
- `source` 为 `{"kind": "explicit"}`（显式事实）或
  `{"kind": "derived", "ruleId": ...}`（推理事实，`ruleId` 为原始规则 id）。
- `message` 与 `parse` 抛出的冲突行逐字一致，重复诊断同一输入得到相同报告。

