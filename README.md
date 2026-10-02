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

`OntologyModel.ask` 接受 SPARQL 风格的 ASK 存在性查询：

```
ASK WHERE { 模式 ('.' 模式)* '.'? }
```

WHERE 模式体与 `query` 完全同语法、同语义（三元组模式、OPTIONAL、FILTER、
UNION 与谓语位置的属性路径），同样在显式与推理三元组的并集上求值；
ASK 不做投影，不接受 SELECT、变量列表或 `*`，WHERE 花括号之后不允许
任何后缀成分，关键字只接受大写。至少存在一个满足全部条件的最终绑定时
返回 `True`，否则返回 `False`（合法查询无匹配不是错误）。输入不是字符串、
查询为空、词法或语法非法、出现不支持的形式或关键字、属性路径引用未声明
属性时抛出 `OntologyError`。重复调用结果一致，且不修改模型数据。

`OntologyModel.construct` 接受 SPARQL 风格的 CONSTRUCT 查询，从已有事实与
推理结论中提取并生成新的三元组集合：

```
CONSTRUCT {
  三元组模板 ('.' 三元组模板)* '.'?
} WHERE {
  模式 ('.' 模式)*
}
```

- 模板由一个或多个固定三项（主语、谓语、宾语）的三元组模板组成，模板之间
  用点分隔，末尾点可省略；主语和宾语为变量或常量（词法约定与 WHERE 相同），
  谓语只能写已声明的常量属性名，不接受变量谓语、属性路径，模板内也不接受
  `OPTIONAL`、`FILTER` 或 `UNION`，模板组不得为空。
- WHERE 模式体与 `query`/`ask` 完全同语法、同语义：三元组模式、`OPTIONAL`、
  `FILTER`、相邻花括号 `UNION` 与谓语位置的属性路径，都在显式与推理三元组
  的并集上求值。
- 对每个通过 WHERE 全部条件的最终绑定逐项实例化模板：主语或宾语变量在该
  绑定中未绑定时不生成对应三元组，其他模板继续处理。
- 返回值是按三元组字典序去重排序的 `Triple` 只读元组；合法查询没有匹配时
  返回空元组而不是异常。生成的三元组不写回模型，不影响后续 `query`、`ask`、
  `entails`、`source_rule`、`explain` 或诊断结果；重复执行同一模型和查询
  结果完全一致。关键字只接受大写。
- 输入不是字符串、查询为空、模板为空、缺少 WHERE、花括号不配对、使用小写
  或不支持的关键字、模板出现变量谓语或属性路径、模板谓语属性未声明、WHERE
  部分存在既有查询器不接受的词法或语法形式时，均抛出 `OntologyError`。

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

### 冲突事实的证明追溯

`ValidationReport.explain_diagnostic(index)` 输入诊断在 `diagnostics` 中的
零基下标，返回与该诊断 `evidence` 两项顺序一致的二元组，分别证明参与冲突的
两条事实：

- `disjointClassMembership`：两项分别证明个体经当前
  `classMembershipPredicate` 表达的两项类成员事实
  `(subject, classMembershipPredicate, class)`；
- `functionalPropertyValue`：两项分别证明个体在约束属性上的两个不同取值
  事实 `(subject, property, object)`。

每项是一个 `Proof` 元组：

- 显式事实恰含一个 `kind` 为 `"explicit"`、`ruleId` 为 `None`、
  `premises` 为空的 `Proof`；
- 推理事实为 `OntologyModel.explain` 对该三元组给出的全部最短完整证明，
  保留原始规则 id、规则 `if` 模式顺序与稳定排序；`premises` 逐层指向推导
  前提并到达显式叶子，循环规则下有限结束且不包含依赖循环的证明。

重复调用结果一致，返回的元组、`Proof` 与嵌套 `premises` 均为只读快照，
调用方修改不影响后续结果。下标为非整数（`bool` 不算整数）、负数、越界，
或 `diagnostics` 为空时调用，均抛出 `OntologyError`，消息包含收到的下标
或空诊断说明。

