# 第 3 部分：评价指标系统 v1.0 方案

状态：v1.0 已实现并通过自动化与端到端验收（2026-08-01）。

> 2026-08 架构迁移说明：本文保留第三阶段实施时的目录名称作为历史记录。
> 网格策略及其专属指标现由 `strategies_system` 持有，具体实现位于
> `strategy_simulation.metrics`；`grid_trading/grid_metrics` 已删除。
> `market_simulator` 仍只提供通用指标协议、计算框架和结果存储。

本文对应《平台总体规划》中的“3. 评价指标”和“阶段 D：指标体系 v1.0”。

---

## 1. 背景

第一部分已经形成一台只负责成交、记账、费用、保证金和强平的仿真执行机器；
第二部分已经可以组合市场、策略、执行和账户组件，运行多场景、多 Seed、参数批量实验，
并将 Summary、Trace 和市场路径保存下来。

目前实验系统保存的是原始事实，还不能用统一口径回答：

- 一个 Run 到底赚了多少；
- 收益承担了多大回撤、仓位和保证金压力；
- 多个 Seed 下结果是否稳定；
- 网格循环、移动和复位对结果有什么影响；
- 两组实验结果能否在相同单位和相同口径下比较。

第三部分的任务，是在不修改仿真事实的前提下，把这些事实转换为可复算、可解释、
可聚合的评价指标。

---

## 2. 模块目标

评价指标系统 v1.0 需要提供：

1. 稳定的指标定义、注册和计算接口；
2. 单 Run 通用指标；
3. 多 Run、多 Seed 聚合统计；
4. 网格策略专属指标扩展；
5. 指标版本、输入指纹和重算能力；
6. 指标结果的 SQLite 持久化、查询、筛选和只读展示；
7. 可供后续策略优化模块使用的结构化指标事实。

它最终回答的是：

> 在明确的计价资产、时间范围和公式版本下，这个实验结果表现如何？

---

## 3. 设计原则

### 3.1 原始事实与派生指标分离

数据链路保持为：

```text
SimulationResult
    ↓
Experiment Summary / Trace / Market Reference
    ↓
MetricInput
    ↓
MetricEvaluator
    ↓
MetricResult
```

评价指标系统只能读取仿真结果，不能反向修改：

- 成交；
- 账本；
- 权益；
- 保证金；
- 强平状态；
- 策略行为。

手续费和资金费已经由仿真账本入账。指标系统可以单独展示它们，但不得再次从权益中扣除。

### 3.2 重计算，轻判断

v1.0 负责准确计算数值和分布，不负责替用户定义唯一的“最佳策略”。

因此首版不提供：

- 自动总分；
- 自动策略排名；
- BTC 收益与 USDT 收益的主观加权；
- 收益、回撤和强平概率之间的默认权重；
- 自动生成投资结论；
- 自动修改参数。

排序和筛选只是对已选指标的机械操作，不代表系统给出的策略结论。

### 3.3 单位先于数值

每个指标必须同时携带：

- 指标标识；
- 数值类型；
- 单位；
- 计价资产；
- 适用对象或维度；
- 公式版本；
- 输入完整性状态。

BTC、USDT、USD、合约张数和基础资产数量不能静默相加或比较。

### 3.4 缺失不等于零

没有 Trace、没有保证金模型、没有仓位序列或没有基准时，相关指标必须返回
`UNAVAILABLE` 及原因，不能返回 `0`。

### 3.5 强平路径不能从样本中消失

仿真正常执行并触发强平的 Run，仍然是一次成功保存的实验结果。聚合时必须保留：

- 强平后的期末权益；
- `liquidated`；
- `bankrupt`；
- 终止时间；
- 终止前的完整可用路径。

不能因为它没有跑满全部时间而将其从均值或概率统计中静默删除。

### 3.6 期末状态按原样评价

仿真结束时不强制平仓。指标以最后一个账户快照的市值权益和未平仓仓位为准。

评价指标系统不创建一笔虚拟平仓，也不追加手续费、滑点或资金费。

---

## 4. 系统边界与代码位置

### 4.1 通用指标框架

建议在 `market_simulator` 中新增：

```text
market_simulator/packages/metric_system/
```

它包含：

- 通用指标协议；
- MetricInput 标准化；
- 通用收益、风险、仓位、资金和执行成本指标；
- 多 Run 聚合；
- 指标定义版本和输入指纹；
- 通用评价服务与 CLI。

`metric_system` 不包含网格、RSI 或任何具体策略术语。

### 4.2 网格专属指标

建议在 `grid_trading` 中新增：

```text
grid_trading/grid_metrics/
```

它包含：

- 网格 Provider Summary 的标准化；
- 单组跟随网格指标；
- 分层跟随网格指标；
- 基于 fill tags 的层、cell、cycle 维度统计；
- 网格指标注册入口。

它依赖通用 `metric_system`，通用框架不能反向依赖 `grid_trading`。

### 4.3 实验系统的职责

`experiment_system` 仍然不包含任何指标公式，只增加中性的存储和读取能力：

- 保存 MetricSet 定义；
- 保存单 Run 指标结果；
- 保存聚合指标结果；
- 按指标查询和筛选；
- 向只读前端返回已经计算好的结果。

推荐依赖方向为：

```text
simulation_runtime
        ↑
experiment_system ← metric_system ← strategy_simulation.metrics
```

这里的箭头表示右侧使用左侧公开能力。`experiment_system` 不 import
`metric_system`，从而避免实验编排层重新承担指标语义。

### 4.4 实施前耦合和迁移点

第三阶段开始前，代码已经保存了大部分评价事实，但仍有四个需要处理的边界：

1. 当时 `grid_experiments` 的 Provider Summary 计算了最低合约权益、最低日期和权益是否
   非正。这些属于派生指标，随后迁移到了指标系统；已有旧
   Summary 继续可读，不要求回写历史数据库；
2. 主权益有明确的 `initial_equity`，但 `account_metrics` 中的 USDT 等额外计价权益
   没有独立保存仿真开始值。3A 需要以向后兼容的方式补充
   `initial_account_metrics` 或等价输入，不能把第一根 K 线结束后的权益冒充初始权益；
3. 现有 Trace 已足够计算通用路径指标，但没有完整的策略创建、移动、复位和暂停事件，
   因而网格高级解释指标需要后续策略侧观测能力；
4. 当前 Experiment SQLite 只有 Summary 和 Trace 存储，没有指标定义、输入指纹和
   指标值表，需要在 3D 做 schema migration。

这些都是加法式改动，不改变既有仿真成交、账本和策略调用关系。

---

## 5. 评价对象和输入层级

### 5.1 Run 级评价

最小评价对象是一个已经成功保存的 Run。

Run 级输入分为三档：

| 输入级别 | 数据来源 | 可计算内容 |
| --- | --- | --- |
| `SUMMARY` | 永久 Summary | 期末收益、费用、资金费、最终仓位、强平状态、Provider 汇总计数 |
| `TRACE` | Summary + Trace | 回撤、波动、水下时间、仓位路径、保证金路径、成交结构 |
| `MARKET` | Summary + Trace + Parquet 市场路径 | 需要市场基准或价格路径的扩展指标 |

每个指标声明自己的最低输入级别。

### 5.2 场景级评价

同一个 `scenario_id` 表示相同的完整组件配置，不包含 Seed 差异。

因此多 Seed 聚合的默认分组键是：

```text
scenario_id
```

这可以回答“同一策略和参数在多个随机路径上的分布如何”。

### 5.3 实验级评价

实验级比较可以并排展示不同 Scenario 的聚合结果，但默认不把不同 Scenario 的样本
混成一个总体均值。

如果未来需要跨市场或跨参数汇总，必须显式提供 `AggregationSpec` 和分组字段。

---

## 6. MetricInput

### 6.1 标准输入结构

`MetricInput` 是只读、与策略无关的评价事实，建议至少包含：

```text
RunIdentity
RunConfiguration
RunSummary
EquitySeries[]
PositionSeries[]
FillFacts[]
MarginSeries[]
FundingFacts[]
TerminationFact
ProviderFacts
MarketReference
InputAvailability
```

所有金额和比例在 Python 侧使用 `Decimal`，时间使用 UTC 毫秒时间戳。

### 6.2 权益序列

每条权益序列必须具有独立标识和计价资产，例如：

```text
account.total_equity / BTC
account.total_equity / USDT
account.futures_equity / BTC
```

当前 `SimulationResult.equity_curve` 提供主权益序列；账户模型放在
`account_metrics` 中的额外权益，由参与运行的 Provider 通过输入贡献接口声明。

通用框架不得通过猜测 `total_equity_btc` 等字符串来推断字段语义。

每条用于收益和回撤计算的权益序列都必须具有明确的仿真开始点。主权益开始值来自
`initial_equity`；额外计价权益必须提供独立的开始值。日线 Trace 中保存的是每根 K 线
结束后的状态，不能把第一根结束值当作初始值。

首版根据已声明的 K 线 interval 建立评价时间边界：开始点位于第一根 K 线区间开始，
最后点位于最后一根 K 线区间结束。日收益序列包含“初始权益到第一根结束权益”这一期。
如果 interval 不明确、时间戳不规则且又没有显式区间边界，年化、波动和水下时长等
时间相关指标返回 `UNAVAILABLE/AMBIGUOUS_TIME_AXIS`，不猜测频率。

### 6.3 双本位原则

BTC 本位和 USDT 本位分别产生完整指标集：

```text
total_return{valuation_asset=BTC}
total_return{valuation_asset=USDT}
max_drawdown_rate{valuation_asset=BTC}
max_drawdown_rate{valuation_asset=USDT}
```

两者并列展示，不做自动合成。

例如持有 BTC 的过程中，BTC 本位收益可能接近不变，而 USDT 本位权益可能因为
BTC 市价上涨而明显增加。这是两个不同问题的答案，不是矛盾数据。

### 6.4 输入校验

计算前至少检查：

- 时间戳单调递增；
- 同一序列的单位和计价资产不变；
- Decimal 有限；
- 初始权益与 Summary 一致；
- 最后权益点与 Summary 一致；
- 强平状态与终止事件一致；
- Trace 中的 fill、margin 和 funding 数量与 Summary 计数一致；
- 市场引用内容哈希正确。

校验失败时不得继续输出看似正常的指标值。

---

## 7. 指标定义模型

### 7.1 MetricDefinition

每个指标定义建议包含：

```text
metric_key
display_name
category
description
value_type
unit_kind
required_input_level
dimensions
adverse_direction
formula_version
```

其中：

- `metric_key` 是稳定机器标识；
- `dimensions` 用于区分计价资产、instrument、layer 等；
- `adverse_direction` 只描述哪一侧是风险尾部，取
  `HIGHER`、`LOWER` 或 `NONE`，不生成综合评分；
- `formula_version` 发生语义变化时必须升级。

### 7.2 MetricSet

MetricSet 是一次评价使用的指标定义集合，例如：

```text
core/v1
grid/v1
```

MetricSet 必须有规范 JSON 文档和 SHA-256 `definition_hash`。同名同版本的定义哈希
不一致时拒绝覆盖，必须升级版本。

### 7.3 MetricValue

指标值支持：

- Decimal；
- integer；
- boolean；
- timestamp；
- text enum。

Decimal 持久化为规范十进制字符串。比率统一保存为小数，例如 `0.15` 表示 15%。

每个结果还包含：

```text
status = AVAILABLE | UNAVAILABLE | INVALID
reason_code
source_level
dimensions
unit
```

---

## 8. 通用单 Run 指标

### 8.1 收益指标

对于同一计价资产的初始权益 `E0` 和期末权益 `ET`：

```text
absolute_return = ET - E0
total_return_rate = ET / E0 - 1
```

`total_return_rate` 只在 `E0 > 0` 时可用。

v1.0 建议实现：

| metric_key | 含义 | 输入 |
| --- | --- | --- |
| `return.initial_equity` | 初始权益 | Summary |
| `return.final_equity` | 期末权益 | Summary |
| `return.absolute` | 绝对收益 | Summary |
| `return.total_rate` | 总收益率 | Summary |
| `return.annualized_rate` | 按实际首尾时间年化的收益率 | Trace |
| `pnl.gross_realized` | 毛已实现盈亏 | Summary |
| `cost.total_fees` | 累计手续费 | Summary |
| `pnl.net_realized` | 扣手续费后的已实现盈亏 | Summary |
| `funding.net_wallet_delta` | 累计资金费钱包变动 | Summary |
| `pnl.net_after_fees_funding` | 扣手续费和计入资金费后的已实现盈亏 | Summary |

年化收益公式为：

```text
annualized_rate = (ET / E0) ^ (365 days / elapsed_time) - 1
```

只有 `E0 > 0`、`ET > 0` 且时间跨度大于零时计算。首版以 365 天作为加密市场年基数。

### 8.2 回撤指标

对权益序列 `E(t)`：

```text
peak(t) = max(E(0)...E(t))
drawdown_amount(t) = peak(t) - E(t)
drawdown_rate(t) = drawdown_amount(t) / peak(t)
```

v1.0 建议实现：

- 最大回撤金额；
- 最大回撤率；
- 最大回撤峰值时间；
- 最大回撤谷底时间；
- 最低权益及发生时间；
- 最长水下时长；
- 期末是否仍在水下；
- 总水下时间占比。

水下区间从权益跌破历史峰值开始，到权益恢复到该峰值或更高时结束；未恢复的区间在
Run 最后一个时间戳截断。持续时间按时间戳计算，不按 K 线根数近似。

如果权益降到零以下，回撤率允许达到或超过 `1`，不截断成 100%。

### 8.3 波动和尾部指标

首版针对当前日线实验定义：

```text
r(t) = E(t) / E(t-1) - 1
```

权益分母必须大于零，否则从该点开始，依赖收益率序列的指标标记为
`UNAVAILABLE/NONPOSITIVE_EQUITY`。

建议实现：

- 日收益均值；
- 日收益样本标准差；
- 年化波动率；
- Sharpe；
- Sortino；
- 5% 日收益分位数；
- 5% Expected Shortfall。

首版统一采用：

- 日频年化系数 `sqrt(365)`；
- 无风险收益率 `0`；
- Sharpe 使用算术日收益和样本标准差；
- Sortino 的最低可接受收益为 `0`；
- 下行偏差为 `sqrt(mean(min(r, 0)^2))`；
- 少于两个有效日收益或分母为零时，相关比率为 `UNAVAILABLE`。

这组口径必须随 MetricSet 保存，未来修改不能静默影响旧结果。

### 8.4 强平和生存指标

建议实现：

- 是否完整运行；
- 是否强平；
- 是否破产；
- 终止原因；
- 终止 sequence；
- 终止时间；
- 实际处理 K 线数量。

这些是执行事实的标准化，不由指标系统重新判断是否应当强平。

### 8.5 保证金和杠杆指标

只有存在 Margin Trace 时计算：

- 最大仓位名义价值；
- 平均仓位名义价值；
- 最大初始保证金；
- 平均初始保证金；
- 最大维持保证金；
- 最低保证金缓冲；
- 最大初始保证金使用率；
- 最大维持保证金使用率；
- 最大有效杠杆；
- 平均有效杠杆。

平均值使用按相邻时间戳间隔加权的时间平均，不简单按快照个数平均。

没有启用保证金模型时，保证金指标为 `UNAVAILABLE/NO_MARGIN_MODEL`，而不是零风险。

### 8.6 仓位和库存指标

按 instrument 和仓位单位分别计算：

- 最终净仓位；
- 最大绝对仓位；
- 平均绝对仓位；
- 最大多头仓位；
- 最大空头仓位；
- 有仓位时间占比；
- 最长连续持仓时长；
- 方向切换次数。

平均仓位和持仓时间使用 Equity Trace。不同 instrument 或不同 quantity unit 不合并。

“高位库存”“低位资金闲置”“每层仓位分布”等带有策略含义的指标，不放入通用层。

### 8.7 成交和成本解释指标

v1.0 通用层建议实现：

- fill 总数；
- BUY / SELL fill 数；
- PASSIVE / ACTIVE fill 数；
- MAKER / TAKER fill 数；
- reduce-only fill 数；
- 有成交的 K 线数量；
- 手续费总额；
- 资金费总额；
- 资金费结算次数。

不同合约的 quantity 和 notional 公式不同。首版不在通用层通过
`price × quantity` 猜测成交额，也不据此计算换手率和滑点成本。

需要成交额的账户或产品，由对应输入贡献者提供统一计价后的 notional 序列，再启用：

- 累计成交额；
- 收益 / 成交额；
- 手续费 / 毛收益；
- 成交额 / 净收益；
- 方向修正后的滑点成本。

### 8.8 资金效率指标

首版优先使用无单位或单位明确的指标：

- 总收益率；
- 最大初始保证金使用率；
- 平均初始保证金使用率；
- 最大有效杠杆；
- 平均有效杠杆；
- 已实现净盈亏 / 最大初始保证金；
- 已实现净盈亏 / 平均初始保证金。

后两项的分子使用 `net_pnl_after_fees_and_funding`。只有分子和保证金属于同一结算资产
时才计算；它们解释合约已实现收益对保证金的使用效率，不包含现货 BTC 市价变化，也
不能代替总账户收益率。

“单位风险收益”没有唯一公式，不能在 v1.0 中用一个模糊名称代替。后续可以明确增加
Calmar、Sharpe 或其他具名指标。

---

## 9. 网格专属指标 v1

### 9.1 当前可直接计算的指标

根据现有 Provider Summary 和 fill tags，首版可以提供：

- 网格完成循环数；
- cell 新增数；
- cell 回收数；
- 最终 cell 数；
- 最终 layer 数；
- layer 创建数量；
- 分层复位次数；
- retiring grid 数量；
- 每 layer 完成循环数；
- 每 layer 最终仓位；
- 每 generation 成交数；
- ENTRY / EXIT 成交数；
- 每个已完成循环的平均净收益；
- 每个已完成循环的平均手续费；
- 最终未完成 ENTRY 数量。

最后三项必须能够通过 cycle、cell、layer、generation 标签完整关联；无法完整配对时返回
`UNAVAILABLE/INCOMPLETE_GRID_LIFECYCLE`，不能用估算值补齐。

### 9.2 暂不能可靠计算的指标

现有 Trace 没有完整记录每次网格创建、移动、复位和暂停的结构化事件，因此以下指标
暂不进入 v1 强制范围：

- 每次网格移动前后的风险变化；
- 每次复位前后的风险变化；
- 策略暂停时长；
- 每层独立权益曲线；
- 每层独立保证金占用；
- 高位库存形成与释放过程。

如果后续确实需要，应在策略侧增加只读 `provider_events`，而不是让指标系统反推策略
内部状态。

### 9.3 边界

网格指标只解释策略发生了什么，不决定：

- 何时建立网格；
- 建多少层；
- 是否加仓；
- 是否平仓；
- 是否暂停；
- 哪个网格参数最好。

这些仍然属于策略体系和策略优化。

---

## 10. 多 Run 和多 Seed 聚合

### 10.1 默认分组

默认按 `scenario_id` 聚合。这样每组只有 Seed 不同，组件和参数完全一致。

聚合结果至少记录：

- 总 Run 数；
- 可用指标数；
- 缺失指标数；
- 非法指标数；
- 正常完成数；
- 强平数；
- 破产数。

### 10.2 数值聚合

对同一 metric_key、相同 dimensions 和相同 unit 的 Decimal 指标计算：

- 均值；
- 中位数；
- 样本标准差；
- 最小值；
- 最大值；
- P05；
- P25；
- P75；
- P95；
- adverse worst value。

分位数采用确定性的 Hyndman-Fan Type 7 线性插值。`adverse worst value` 根据指标
定义中的 `adverse_direction` 选择最小值或最大值；方向为 `NONE` 时不生成“最坏值”。

### 10.3 事件概率

聚合时计算：

```text
liquidation_rate = liquidated_runs / evaluated_runs
bankruptcy_rate = bankrupt_runs / evaluated_runs
completion_rate = completed_runs / evaluated_runs
```

失败于配置、代码异常或数据损坏的实验 Run 不进入收益分布，但必须单独计入
`execution_failed_count`，不能与强平混为一类。

### 10.4 达标概率

“达标”必须由调用者显式给出阈值，例如：

```text
USDT total_return >= 0.20
AND max_drawdown_rate <= 0.15
AND liquidated = false
```

v1.0 可以预留 `ThresholdSpec`，但不内置任何默认达标条件。默认目标和多目标权重属于
后续策略优化模块。

### 10.5 禁止静默混合

以下数据不得放入同一数值聚合：

- 不同 metric formula version；
- 不同计价资产；
- 不同单位；
- 不同 dimensions；
- 不同输入语义；
- 一部分含 Trace、另一部分使用替代估算的结果。

---

## 11. 基准指标

“相对 HODL”和“相对基准策略”只有在基准定义明确时才有意义。

例如 HODL 至少需要明确：

- 初始资金是什么资产；
- 是否在第一根 open 买入；
- 是否收取买入手续费；
- 期末是否卖出；
- 使用 BTC 还是 USDT 计价；
- 是否包含现货 BTC 与合约钱包的原始持仓。

因此 v1.0 不根据市场首尾价格自动猜测 HODL 收益。建议预留 `BenchmarkResultPort`：

```text
显式基准 Run / 显式基准权益序列
        ↓
相同计价资产和相同时间范围校验
        ↓
relative_return
relative_drawdown
```

真正的 HODL 和基准策略在“阶段 E：策略 × 市场研究”中作为正式组件补充。

---

## 12. 指标持久化

### 12.1 存储原则

指标是可重算的派生结果，但计算可能依赖之后会被清理的 Trace。因此：

- 指标结果统一保存进 Experiment SQLite；
- 不改写原始 Summary；
- 不把指标写回 Trace；
- 每次评价记录定义哈希和输入指纹；
- Trace 清理不级联删除已经保存的指标；
- Trace 被清理后，已有指标仍可查看，但重算能力可能消失。

### 12.2 建议表结构

实验数据库升级 schema，增加中性表：

```text
metric_sets
run_metric_evaluations
run_metric_values
aggregate_metric_evaluations
aggregate_metric_values
```

`metric_sets` 保存：

- metric_set_id；
- version；
- definition_hash；
- definition_json；
- evaluator code revisions；
- 创建时间。

`run_metric_evaluations` 保存：

- run_id；
- metric_set；
- input_fingerprint；
- 实际使用的输入级别；
- Summary、Trace 和 Market 各自的输入哈希；
- 计算状态；
- 是否仍可重算；
- 计算时间；
- 问题列表。

`run_metric_values` 按指标和 dimensions 保存精确值，避免将全部指标塞进一个无法查询的
JSON BLOB。

### 12.3 输入指纹

`input_fingerprint` 至少覆盖：

- canonical Summary hash；
- Trace payload hash 或 `PURGED` 状态；
- Market content hash；
- Provider input contributor 版本；
- MetricSet definition hash。

同一输入和同一 MetricSet 重复执行时应幂等跳过。

### 12.4 Trace 清理后的行为

分为三种情况：

1. Trace 尚在：全部满足条件的指标可计算和重算；
2. Trace 已清理，但此前已经计算：保留指标及原输入指纹，标记
   `recomputable=false`；
3. Trace 已清理且此前未计算：只计算 Summary 级指标，Trace 级指标记录
   `UNAVAILABLE/TRACE_PURGED`。

有研究价值、需要长期复核公式的 Run 应先标记 `ARCHIVED`，保留 Trace。

---

## 13. 计算和调用接口

### 13.1 Python 接口

建议核心接口为：

```text
MetricRegistry
MetricInputBuilder
MetricEvaluator.evaluate_run(...)
MetricAggregator.aggregate(...)
MetricEvaluationService.evaluate_experiment(...)
```

策略应用通过注册贡献者接入，不允许配置任意 Python import 路径。

### 13.2 CLI

通用 CLI 建议：

```text
python -m metric_system evaluate-run EXPERIMENT_DB RUN_ID
python -m metric_system evaluate-experiment EXPERIMENT_DB
python -m metric_system aggregate EXPERIMENT_DB
python -m metric_system list-sets EXPERIMENT_DB
```

网格应用入口负责注册 `grid/v1`：

```text
python -m grid_metrics evaluate-experiment EXPERIMENT_DB
```

所有命令默认幂等；只有显式 `--recompute` 才尝试重新计算同版本结果，输入不足时给出清晰
错误，不覆盖旧的可用结果。

### 13.3 运行时机

首版采用实验完成后的独立评价：

```text
run experiment
    ↓
persist Summary / Trace / Market
    ↓
evaluate metrics
    ↓
persist MetricResult
```

不把指标计算放进每根 K 线的仿真循环，也不因为指标失败而回滚已经成功的 Run。

---

## 14. 只读结果展示

现有实验结果页面增加：

- MetricSet 和公式版本选择；
- Run 级核心指标卡片；
- BTC / USDT 计价切换或并列展示；
- 动态指标列；
- 按单一指标排序和筛选；
- Scenario 多 Seed 聚合表；
- P05 / 中位数 / P95 分布展示；
- 强平率和破产率；
- 指标输入级别、缺失原因和是否可重算；
- 单 Run 权益、回撤、仓位和保证金曲线。

前端只展示服务端已经计算并保存的指标，不在 JavaScript 中复制公式。

首版不做综合排行榜。用户可以选择一个具体指标排序，但页面必须显示计价资产、单位和
MetricSet 版本。

---

## 15. 测试和数值验证

### 15.1 手算金样本

为每类公式建立小型固定序列和手工答案，例如：

```text
equity = [100, 120, 90, 110]
max_drawdown_rate = 25%
```

金样本不依赖具体策略，直接验证公式。

### 15.2 核心边界用例

至少覆盖：

- 零成交、权益恒定；
- 单调上涨；
- 单调下跌；
- 多次创新高和回撤；
- 期末仍在水下；
- 未平仓结束；
- 手续费和资金费同时存在；
- 正常强平；
- 破产强平；
- 权益为零或负数；
- 无保证金模型；
- Trace 已清理；
- Trace 已归档；
- 不规则时间间隔；
- BTC 与 USDT 双权益；
- 多 instrument；
- 不同单位禁止聚合；
- 不完整网格生命周期。

### 15.3 会计一致性

至少验证：

```text
gross_realized_pnl - total_fees = net_realized_pnl
net_realized_pnl + total_funding = net_pnl_after_fees_and_funding
last_equity_snapshot = final_equity
```

指标系统发现不一致时输出 `INVALID/ACCOUNTING_MISMATCH`，不能自行修正原结果。

### 15.4 性质测试

建议增加：

- 金额整体乘常数后，金额指标同比变化、比例指标不变；
- 相同输入和版本的结果字节级稳定；
- Run 顺序变化不影响聚合结果；
- 重复 Seed 不会被重复计入；
- 强平样本始终保留在事件概率分母中；
- 分位数结果与固定 Type 7 参考值一致。

### 15.5 跨仓库验收

使用第二阶段正式的 8-Run 跟随网格矩阵验证：

- 8 个 Run 全部生成通用指标；
- 网格 Run 全部生成 `grid/v1` 指标；
- 同一 Scenario 的多个 Seed 正确聚合；
- 重复评价幂等跳过；
- BTC 和 USDT 指标独立保存；
- 页面可以查看 Run 和聚合结果；
- 现有 K 线回放保持不变。

再使用现有 COIN-M 强平固定样本验证强平率、破产状态、终止时间和最大回撤。

---

## 16. 开发批次

### 3A：指标契约与输入标准化

状态：已完成。

- 新建 `metric_system`；
- 定义 MetricDefinition、MetricSet、MetricValue；
- 定义 MetricInput 和输入级别；
- 从 Experiment Summary / Trace 构建通用输入；
- 补充额外计价权益的明确初始值和时间边界；
- 迁移 Grid Provider 中现有的临时最低权益指标；
- 建立 Decimal、单位、dimensions 和校验规则；
- 建立手算金样本。

### 3B：收益和风险指标

状态：已完成。

- 期末收益；
- 年化收益；
- 回撤；
- 水下时间；
- 波动；
- Sharpe、Sortino；
- 尾部收益；
- 强平和破产事实标准化。

### 3C：资金、仓位和成本指标

状态：已完成。

- 保证金；
- 有效杠杆；
- 仓位路径；
- 成交结构；
- 手续费；
- 资金费；
- 资金效率。

### 3D：持久化和重算

状态：已完成。

- Experiment SQLite schema migration；
- MetricSet 和 MetricResult 表；
- 输入指纹；
- 幂等计算；
- Trace 清理后的可用性语义；
- CLI。

### 3E：多运行聚合

状态：已完成。

- Scenario / Seed 分组；
- 均值、中位数、标准差和分位数；
- 强平率和破产率；
- 聚合持久化；
- 不同单位和版本隔离。

### 3F：网格专属指标

状态：已完成。

- 新建 `grid_metrics`；
- 注册网格输入贡献者；
- 单组和分层跟随网格指标；
- fill tag 生命周期配对；
- 网格跨仓库集成测试。

### 3G：结果展示与正式验收

状态：已完成；自动化和只读 API 已验收，当前开发会话没有可用浏览器实例，视觉点击
保留为人工复核项，不影响指标计算与页面数据契约验收。

- 现有只读页面增加指标视图；
- Run 指标和 Scenario 聚合；
- BTC / USDT 并列展示；
- 指标筛选和单指标排序；
- 8-Run 正式基线；
- 强平基线；
- 使用说明和最终验收记录。

---

## 17. v1.0 验收标准

全部满足以下条件，第三部分才算完成：

1. 指标公式不在 `experiment_system` 和前端中重复实现；
2. 通用 `metric_system` 不包含网格术语；
3. BTC 和 USDT 指标分别计算、分别存储；
4. Summary、Trace 和 Market 输入缺失状态明确；
5. Trace 清理前后已有指标保持一致，重算能力状态准确；
6. 强平和破产 Run 不被静默排除；
7. 单 Run 指标通过手算金样本；
8. 多 Seed 聚合结果通过固定参考值；
9. 不同单位、计价资产和公式版本不能误聚合；
10. 网格专属指标由 `grid_trading` 注册；
11. 8-Run 正式矩阵和 COIN-M 强平样本通过；
12. 重复评价不会产生重复结果；
13. 页面只读展示已保存指标，不在浏览器重新计算；
14. 第一、第二阶段原有测试继续通过。

---

## 18. v1.0 明确不做

- 自动综合评分；
- 自动策略排名结论；
- 自动参数优化；
- 默认收益/风险权重；
- BTC 与 USDT 收益自动合成；
- 隐式 HODL 假设；
- 未声明产品语义时猜测成交额；
- 指标系统重新判断或模拟强平；
- 指标系统强制期末平仓；
- 根据指标自动触发风控；
- 在线实时指标流；
- 分布式计算；
- 数据仓库或外部分析数据库。

---

## 19. 已确认的 v1.0 边界

1. 通用包是否确定命名为 `metric_system`，网格扩展命名为 `grid_metrics`；
2. 是否接受 BTC 和 USDT 两套指标始终独立，不提供默认综合权益；
3. 是否接受首版日频 Sharpe / Sortino 使用 365 天、无风险收益率 0；
4. 是否接受已计算指标在 Trace 清理后继续保留，但标记为不可重算；
5. 是否接受 HODL 必须由显式基准定义，首版不根据首尾价自动生成；
6. 是否接受首版只允许单指标排序，不提供综合排行榜；
7. 是否接受网格 v1 先计算现有 Summary 和 tags 能可靠支持的指标，复位风险变化等
   高级解释等策略事件完善后再增加。

以上七项均按本文方案接受并实现：通用包为 `metric_system`，网格扩展为
`grid_metrics`；双本位不合成；日频风险指标使用 365 天和零无风险收益率；已保存指标
在 Trace 清理后保留；首版不隐式构造 HODL；页面不提供综合评分；网格高级事件指标
等待策略侧提供稳定事件事实后再扩展。

---

## 20. v1.0 实现与验收记录

### 20.1 代码产物

`market_simulator`：

- 新增 `packages/metric_system`，包含指标契约、输入构建、注册、通用计算、Scenario
  聚合、评价服务和 CLI；
- `simulation_runtime` 补充只读 `initial_account_metrics`，确保 USDT 等额外计价权益
  使用仿真开始值而不是第一根 K 线结束值；
- Experiment SQLite schema 升级到 v3，新增 MetricSet、Run 指标和聚合指标中性表；
- 结果读取、CSV、只读 API 与 `experiments.html` 支持持久化指标、动态排序和聚合展示。

`grid_trading`：

- 新增 `grid_metrics`，注册 BTC 总权益、USDT 总权益与 BTC 合约权益输入；
- Provider 中原有最低合约权益临时指标已移除；
- 新增网格 Summary、fill role/generation、完整循环、未闭合 entry、每循环净收益与
  手续费指标；
- `python -m grid_metrics` 同时评价 `core/v1` 与 `grid/v1`。

### 20.2 自动化验收

- `market_simulator`：132 项测试通过，1 项按环境跳过；
- `grid_trading`：260 项测试通过，14 项按环境跳过；
- 手算 `[100, 120, 90, 110]` 最大回撤 25%、恒定权益、Type 7 分位数、输入缺失、
  会计一致性、幂等、Trace 清理和多 Seed 聚合均有固定测试；
- 真实 COIN-M 保证金路径验证了 `run.liquidated=true`、`run.completed=false`、
  `termination_reason=LIQUIDATION` 与非正最低保证金缓冲。

### 20.3 8-Run 端到端验收

使用 `single_following_grid_matrix.json` 生成：

- 4 个 Scenario；
- 每个 Scenario 2 个 Seed；
- 共 8 个成功 Run；
- 2 条复用的 Parquet 市场路径；
- `core/v1` 8 个 Run 评价和 4 个 Scenario 聚合；
- `grid/v1` 8 个 Run 评价和 4 个 Scenario 聚合；
- 第二次评价两个 MetricSet 均为 `evaluated_count=0, skipped_count=8`；
- 920 个 Run 指标值，其中 0 个 `INVALID`；基线未启用保证金模型，相关指标按设计为
  `UNAVAILABLE`，没有写成零；
- 只读 `/metrics` API 返回两套 MetricSet 定义和八组聚合结果。

该次历史端到端库在合并前的两个参与仓库含未提交改动时以 `--allow-dirty` 运行，因此明确为
exploratory、`reproducible=false`。代码提交后应再运行同一配置，生成正式 clean 基线；
这不会改变本轮对调用链、数值、持久化和幂等性的验收结论。

### 20.4 保留项

- 当前会话没有可用的可视浏览器实例，结果页的视觉和点击流程需在本机浏览器执行一次
  人工复核；只读 HTTP API、静态页面、前端无公式约束和动态字段逻辑已经由测试覆盖；
- HODL、复位风险变化、策略综合评分和自动优化仍属于明确的后续阶段，不在 v1.0 内。
