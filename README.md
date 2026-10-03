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

`OntologyModel.describe` 接受 SPARQL 风格的 DESCRIBE 查询：

```
DESCRIBE (?变量 ... | *) WHERE { 模式 ('.' 模式)* '.'? }
```

投影位置只接受一个或多个互不重复的查询变量，或单独一个 `*`；WHERE 模式体
与 `query`/`ask` 完全同语法、同语义（三元组模式、OPTIONAL、FILTER、UNION
与谓语位置的属性路径），同样在显式与推理三元组的并集上求值。`*` 表示描述
每个最终绑定中当前已绑定的全部变量值；指定变量时分别取其绑定值，未绑定
变量（如 OPTIONAL 未命中）在本次解中忽略。对每个待描述名称 N，结果包含
显式与推理三元组并集中主语或宾语为 N 的全部三元组，不因三元组来自显式事实
还是某条规则而改变；所有解产生的描述合并后按主语、谓语、宾语的字典序去重，
以只读 `Triple` 元组返回。无匹配绑定、变量均未绑定或描述集合为空时返回空
元组。同一模型重复执行同一文本结果相同，且不修改模型数据或已有证明。
输入不是字符串、查询为空、关键字不是大写、投影为空或含常量、变量名重复、
花括号不配对、WHERE 后有后缀、模式体出现子查询或不支持形式、属性路径引用
未声明属性时抛出 `OntologyError`；合法查询仅有数据不匹配时不视为异常。

## 一致性诊断

`OntologyEngine.parse` 在结构校验与不动点推理完成后检查 `consistency` 约束，
发现互斥类成员、函数型属性多值或非对称属性反向三元组冲突时抛出
`InconsistencyError`，不返回模型。`consistency` 除原有
`classMembershipPredicate`、`disjointClasses`、`functionalProperties` 字段外，
另含可选字段 `asymmetricProperties`：省略或为空数组表示不检查非对称属性；
每项为 `{"id": ..., "property": ...}`，`id` 为非空字符串或整数（`bool` 不算）
且同一 `consistency` 内各约束数组间不重复，`property` 必须是已声明属性。
未知字段、类型错误、重复 `id` 或未声明属性均为结构错误，`parse` 与
`diagnose` 都抛出带数组元素与字段定位的 `OntologyError`。

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

- `kind` 为 `"disjointClassMembership"`、`"functionalPropertyValue"` 或
  `"asymmetricPropertyPair"`；前两者的 `evidence` 各恰含两项：互斥类为
  `class` 与 `source` 且类名按字典序排列，函数型属性为 `object` 与 `source`
  且取值按字典序排列。非对称属性的 `evidence` 恰含两项，每项固定为
  `subject`、`object`、`source`：在显式与推理三元组并集上，约束属性 `p`
  同时存在 `(a, p, b)` 与 `(b, p, a)` 时违反一次，两项按无序节点对
  `{a, b}` 的字典序排列（第一项 subject 较小）；`a` 等于 `b` 时 `(a, p, a)`
  单独构成一次自反违反，两项指向同一事实。诊断按无序节点对去重，整体排在
  互斥类与函数型属性冲突之后，新约束内按属性声明顺序、节点字典序与来源
  稳定排序。
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
  事实 `(subject, property, object)`；
- `asymmetricPropertyPair`：两项与 `evidence` 顺序一致，分别证明
  `(first, property, second)` 与 `(second, property, first)` 两条反向事实；
  自反违反时两项都证明同一事实 `(subject, property, subject)`。

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

