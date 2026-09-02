# Rule、Strategy 与 Experiment 关系架构及迁移方案

## 1. 文档状态

- 状态：A–D 已实现并通过测试；E 留作后续迁移
- 适用范围：`strategies_system` 与 `market_simulator`
- 文档目的：独立定义从 Trading Rule 到 Strategy，再到 Experiment 和
  Simulation Runtime 的逻辑关系、依赖方向、运行调用链与迁移顺序。
- 非目标：本文不定义任何具体策略的优化参数，不修改成交、记账、保证金、
  强平或指标公式。

---

## 2. 核心结论

本方案同时采用以下三个方向，它们并不矛盾。

### 2.1 逻辑归属

Experiment 是对某个 Strategy 的研究，因此在产品语义和前端信息架构中，
Experiment 从属于 Strategy。

```text
StrategyDefinition
├── Baseline Experiment
├── Parameter Study Experiment
├── Market Validation Experiment
└── Robustness Experiment
```

### 2.2 数据依赖

`ExperimentSpec` 显式引用 `StrategyDefinition`，而不是让
`StrategyDefinition` 保存 Experiment 列表。

```text
ExperimentSpec
    → strategy_definition_type
    → StrategyDefinition
```

前端和研究目录可以基于这个显式引用建立反向索引：

```text
strategy_definition_type → Experiment[]
```

### 2.3 运行控制

Experiment System 是运行编排者，它通过通用 Provider 协议调用策略侧的仿真
适配器，由适配器创建 `StrategyInstance` 并接入 `SimulationRuntime`。

```text
ExperimentSystem
    → StrategiesSimulationProvider
    → StrategySimulationAdapter
    → StrategyInstance
    → SimulationRuntime
```

`market_simulator` 不导入任何具体 Strategy 或 Rule。

---

## 3. 领域对象和边界

### 3.1 TradingRuleDefinition

Trading Rule 是最小可复用交易行为单元，定义：

- `rule_type` 和版本；
- RuleConfig 字段契约；
- 接受的 Rule Input 类型；
- 状态转移与生命周期；
- 输出的 Rule Intent 或其他领域结果；
- 方向、产品和行为约束。

Rule 不知道：

- 自己会被哪个 Strategy 使用；
- 自己会运行在哪个 Experiment 中；
- 市场路径、Seed 或整体收益结果。

当前示例：

```text
InitialEntryRule
    启动后生成一次建仓意图

LadderTakeProfitRule
    建仓后生成阶梯式 reduce-only 退出意图
```

### 3.2 StrategyDefinition

Strategy 是 n 条 Rule（n ≥ 1）完成参数绑定、职责分配和协作关系定义后的
可运行组合。

StrategyDefinition 定义：

- n 个 Rule Slot 及稳定顺序；
- 每个 Rule 的职责、权限和输入订阅；
- Strategy 外部参数；
- Strategy Parameters 到 RuleConfig 的转换；
- 意图冲突解决和退出控制权；
- Strategy 级生命周期。

当前策略定义：

```text
初始建仓&阶梯止盈
├── entry: InitialEntryRule
└── take-profit: LadderTakeProfitRule
```

只改变 RuleConfig 参数不会创建新的 StrategyDefinition。增加、删除或替换 Rule，
或改变 Rule 之间的核心协调关系，将产生新的 StrategyDefinition。

### 3.3 StrategySpec 和 StrategyInstance

`StrategyDefinition.bind(parameters)` 完成参数校验与转换，生成不可变
`StrategySpec`。

```text
StrategySpec
├── strategy_type
├── instrument / direction / product binding
├── capital allocation
├── RuleSpec[]
│   ├── rule_type
│   ├── resolved RuleConfig
│   └── capability references
└── coordination policy
```

`StrategyInstance` 是 `StrategySpec` 的一次运行时实例，持有 Rule State、Intent 空间、
虚拟仓位归属和生命周期。

### 3.4 ExperimentSpec

Experiment 不是 Strategy 内部对象，而是以 Strategy 为研究对象的配置容器。

```text
ExperimentSpec
├── Market candidates
├── Strategy candidates
├── Execution candidates
├── Account candidates
├── ParameterAxes
├── Seeds
├── Output / retention
└── Controls
```

Strategy 是 Experiment 配置中的核心组件，但不是整个 Experiment。

### 3.5 SimulationRuntime

Simulation Runtime 是冰冷的执行机器，负责：

- 按时序推进市场帧；
- 处理主动与被动交易意图；
- 成交、手续费和资金费入账；
- 仓位、保证金、权益和强平计算；
- 产生通用 SimulationResult。

Simulation Runtime 不理解 Rule 的业务语义，不决定哪些参数应该被优化。

---

## 4. 参数链路

参数必须按以下链路进入 Rule：

```text
Experiment ParameterAxis
    → StrategyParameters
    → StrategyDefinition.bind()
    → StrategySpec
    → RuleSpec.resolved_config
    → RuleInstance
```

例如：

```text
Experiment:
    exit_level_count = 20

Strategy parameter mapping:
    exit_level_count → take-profit.level_count

Resolved RuleConfig:
    LadderTakeProfitRuleConfig.level_count = 20
```

Experiment 不应绕过 StrategyDefinition，直接修改运行中的 RuleInstance 或 Rule State。

### 4.1 配置参数的展示归属

- Rule 详情页使用 `TradingRuleDefinition.config_fields` 展示 RuleConfig 契约；
- Strategy 详情页展示外部 Strategy Parameters 和到 RuleConfig 的映射；
- Strategy 运行页按 Rule Slot 展示每条 Rule 最终收到的已解析 RuleConfig；
- 参数研究页按 Rule 归组参数轴，比较多个候选配置的结果。

---

## 5. Strategy 与 Experiment 的分组规则

### 5.1 同一 StrategyDefinition

以下变化不会创建新 StrategyDefinition：

- RuleConfig 值变化；
- 标的、方向和产品绑定变化；
- 资金配置和账户模型变化；
- Market Path、Market Role 或 Seed 变化；
- Execution 和费用参数变化。

这些 Experiment 在前端中应归属到同一 StrategyDefinition，但可以形成不同的实验。

```text
初始建仓&阶梯止盈
├── btc-hold-coinm-ladder-baseline-v1
├── btc-coinm-ladder-parameter-study-v1
└── btc-coinm-ladder-market-validation-v1
```

### 5.2 新的 StrategyDefinition

以下变化应生成新的 StrategyDefinition 及新的实验分组：

- 增加或删除 Rule；
- 替换 Rule Type；
- 改变核心 Rule 职责；
- 改变会导致行为语义不同的协调与冲突规则。

```text
初始建仓&阶梯止盈
    → StrategyDefinition A

初始建仓&阶梯止盈&止损
    → StrategyDefinition B
```

### 5.3 Experiment 与 Run

Experiment 是一组 Runs 的配置容器，不限制为一组参数。

```text
Run count =
    markets
    × strategies
    × executions
    × accounts
    × parameter-axis combinations
    × seeds
```

基线 Experiment 建议保持一组固定策略参数，可以通过多市场或多 Seed 验证
稳定性。参数扫描使用独立 Experiment ID，并在同一 Experiment 中生成多个
参数候选和 Runs。

---

## 6. 注册与运行调用链

### 6.1 注册对象

`StrategyDefinition` 不主动注册到 `market_simulator`。由 `strategies_system` 组装：

```text
strategies_system
├── TradingRuleRegistry
├── StrategyDefinitionRegistry
├── StrategySimulationAdapter Registry
└── StrategiesSimulationProvider
```

`StrategiesSimulationProvider` 实现 Experiment System 定义的通用 Provider 协议。

### 6.2 规划阶段

```text
ExperimentSpec
    → ExperimentSystem.expand_scenarios()
    → StrategiesSimulationProvider.resolve()/validate()
    → StrategyDefinitionRegistry.get(strategy_definition_type)
    → StrategyDefinition.bind(strategy_parameters)
    → StrategySpec
```

### 6.3 运行阶段

```text
RunSpec
    → StrategiesSimulationProvider.prepare()
    → StrategySimulationAdapter
    → StrategyApplication / StrategyInstance
    → SimulationRunner
    → Rule Input
    → Rule Transition / Rule Intent
    → Execution / Ledger / Margin / Liquidation
    → SimulationResult
```

### 6.4 结果阶段

```text
SimulationResult
    → Provider application summary
    → Strategy / Rule attribution
    → Metric evaluation
    → SQLite experiment database
    → Read API
    → Viewer
```

Provider Summary 保存实际运行的 StrategyInstance、RuleInstance、已解析 RuleConfig
和生命周期，但它是运行结果，不应作为 Experiment 归属 Strategy 的唯一依据。

---

## 7. 策略实验关联契约

### 7.1 目标形式

Strategy Component 的配置应显式声明它实例化的 StrategyDefinition。

```json
{
  "key": "btc-ladder",
  "type": "strategy-application/v1",
  "parameters": {
    "strategy_definition_type": "entry-then-ladder-exit/v1",
    "strategy_parameters": {
      "instrument": "BTCUSD_PERP",
      "direction": "LONG",
      "product_type": "INVERSE_PERPETUAL",
      "first_exit_ratio": "1.10",
      "exit_level_count": 10
    }
  }
}
```

Experiment ParameterAxis 继续作用于 Strategy 外部参数：

```json
{
  "path": "/strategy/parameters/strategy_parameters/exit_level_count",
  "values": [5, 10, 20]
}
```

StrategyDefinition 已有参数映射负责把 `exit_level_count` 转换为
`take-profit.level_count`。

### 7.2 近期兼容形式

当前仿真配置仍使用专用 Plugin Type：

```text
coinm-long-take-profit-ladder/v1
```

近期可以保留该 Plugin，但在其参数中增加：

```json
{
  "strategy_definition_type": "entry-then-ladder-exit/v1"
}
```

`strategies_system` 运行前必须校验：

- `strategy_definition_type` 已注册；
- Strategy Parameters 可以成功绑定；
- ParameterAxis 指向合法的 Strategy 参数；
- 解析后的 RuleConfig 通过每条 Rule 的校验。

### 7.3 通用适配器目标

在专用 Plugin 兼容路径稳定后，再独立迁移为：

```text
strategy-application/v1
```

通用适配器根据 `strategy_definition_type` 找到 StrategyDefinition，根据产品与账户绑定
注入 PositionSizer 等 Capability。这个迁移不与参数研究前端强制绑定在同一批次。

### 7.4 Experiment Kind

`ExperimentSpec.metadata` 可以保存不影响仿真运行的研究语义：

```json
{
  "experiment_kind": "PARAMETER_STUDY"
}
```

建议首版值：

- `BASELINE`；
- `PARAMETER_STUDY`；
- `MARKET_VALIDATION`；
- `ROBUSTNESS`。

`experiment_kind` 由 `strategies_system` 约定和校验；通用 Experiment System 只把它当作
不透明 metadata 保存。

---

## 8. 前端信息架构

### 8.1 Strategy 运行

Strategy 运行页展示某个 StrategyDefinition 下已运行的实验和 Runs：

```text
实验 ID
+ 标的 / 方向 / 产品 / 资金 / 账户
+ Market / Seed
+ 每条 Rule 的已解析 RuleConfig
+ 收益 / 仓位 / 强平 / 成交 / 状态
```

该页不展开全部 Strategy Parameters，不承担参数优化比较。

### 8.2 参数研究

参数研究从属于 StrategyDefinition，按以下层级展示：

```text
StrategyDefinition
    → PARAMETER_STUDY Experiment[]
        → Rule-grouped parameter axes
            → Candidate configurations
                → Runs and reports
```

页面规则：

- 一级分组使用 StrategyDefinition，不使用历史仿真 Plugin Type；
- 二级列表使用 Experiment ID；
- 只展示 `experiment_kind=PARAMETER_STUDY` 或含有 `parameter_axes` 的 Experiment；
- 参数轴通过 Strategy 参数映射归属到具体 Rule Slot；
- 报表只比较当前 Experiment 中的候选配置；
- 基线 Experiment 在 Strategy 运行页展示，可被参数研究引用为对照，但不单独
  冒充参数研究。

### 8.3 Run 详情

Run 详情保留完整 Experiment Configuration、Strategy Parameters、RuleConfig、指标和
Trace，用于追溯，不作为参数研究的主要浏览页。

---

## 9. 当前实现与目标差异

### 9.1 已有能力

- Experiment System 已支持 Market、Strategy、Execution、Account、ParameterAxis 和 Seed
  的笛卡尔积；
- `strategies_system` 已有 TradingRuleRegistry 和 StrategyDefinitionRegistry；
- Ladder 已解析为 `InitialEntryRule + LadderTakeProfitRule`；
- Provider Summary 已保存 StrategyInstance、RuleInstance 和最终 RuleConfig；
- SQLite 已保存 ExperimentSpec、Run、Metric 和 Trace；
- 前端已有参数候选表和比较报表。

### 9.2 实施前缺口（A–D 已闭环）

- 有效 Ladder 实验已经在策略组件参数中显式声明
  `strategy_definition_type`；
- 两个首批实验已经使用 `strategy_parameters` 作为外部参数入口；
- 前端从持久化 ExperimentSpec 建立 StrategyExperimentIndex，只有旧结果才从
  Provider Summary 回退；
- 即使 Run 失败或尚未产生 Provider Summary，实验仍可归属到 StrategyDefinition；
- 参数研究页已经按 StrategyDefinition 和 PARAMETER_STUDY Experiment 组织，
  单 Run 基线只留在策略运行页。

---

## 10. 实施计划

### 10.1 A：建立 Experiment 到 Strategy 的显式关联（已完成）

1. 在策略仿真组件参数中增加 `strategy_definition_type`。
2. 在 `strategies_system` Provider 的 `resolve/validate` 阶段校验该定义已注册。
3. 校验 Strategy Parameters 可以解析为合法 StrategySpec 和 RuleConfig。
4. `market_simulator` 中的通用 `ExperimentSpec` 模型不增加 Strategy 领域依赖。

### 10.2 B：建立 StrategyExperimentIndex（已完成）

1. 从已保存的 ExperimentSpec 读取 `strategy_definition_type`。
2. 以 `strategy_definition_type` 建立 Experiment 反向索引。
3. 已启动但 Run 失败的 Experiment 仍可归属到策略。
4. 对旧数据暂时回退使用 Provider Summary 中的 canonical `strategy_type`。
5. 回退逻辑只用于读取兼容，新 Experiment 必须具有显式关联。

### 10.3 C：重构参数研究页（已完成）

1. 使用 StrategyDefinition 作为一级分组。
2. 使用 Parameter Study Experiment 作为二级列表。
3. 参数轴通过 Strategy Parameter Mapping 归属到 Rule Slot。
4. 保留现有候选对比表、收益/风险图、成交图和关键绩效表。
5. 删除以历史 Simulation Plugin 和 `researchFocusGroups(state.strategies)` 为主索引
   的页面组织方式。
6. 不含参数轴的基线只进入 Strategy 运行页。

### 10.4 D：迁移当前有效实验（已完成）

首批迁移：

```text
btc-hold-coinm-ladder-baseline-v1
btc-coinm-effective-leverage-scan-v1
```

二者均显式引用：

```text
entry-then-ladder-exit/v1
```

旧 Grid Experiment 等相应 Grid StrategyDefinition 建立后再迁移，不使用临时字符串
归类。

### 10.5 E：通用 Strategy Application 适配器（待后续）

本批次只预留目标，不作为 A–D 的前置条件。

1. 建立 `strategy-application/v1` 通用仿真组件。
2. 根据 StrategyDefinition、产品绑定和 Capability Registry 创建 StrategySpec。
3. 将 `coinm-long-take-profit-ladder/v1` 降级为兼容别名或迁移工具。
4. 在兼容路径验收后删除专用 Plugin 中的重复 Strategy 组装逻辑。

---

## 11. 代码归属

### 11.1 `strategies_system/src/trading_strategies`

保存：

- TradingRuleDefinition 和 Rule 实现；
- StrategyDefinition 和 Strategy Parameter Mapping；
- StrategySpec / StrategyInstance 领域契约；
- Rule 和 Strategy Registry。

不保存：

- ExperimentSpec；
- 市场路径；
- SQLite 结果；
- 参数对比报表。

### 11.2 `strategies_system/src/strategy_simulation`

保存：

- Experiment Provider；
- Strategy 仿真适配器；
- Capability 注入；
- Strategy 实验关联校验；
- Strategy/Rule 结果归因摘要。

### 11.3 `strategies_system/experiments`

保存：

- 具体 ExperimentSpec JSON；
- StrategyDefinition 显式引用；
- ParameterAxes；
- 研究类型 metadata；
- 本地实验结果数据库（不作为源码版本一部分）。

### 11.4 `market_simulator/packages/experiment_system`

保存：

- 通用 ExperimentSpec 契约；
- 笛卡尔积展开；
- Run 计划和执行编排；
- SQLite 仓储；
- Metric 存储和通用比较；
- 只读 API。

不引入 StrategyDefinition、RuleType 或具体交易产品概念。

### 11.5 `market_simulator/viewer`

保存：

- Strategy 到 Experiment 的反向展示索引；
- Strategy 运行概览；
- 参数研究的 Rule 分组、候选比较与报表；
- Run 详情和 K 线播放。

前端不根据 Experiment ID、description 或中文名称猜测 Strategy 归属。

---

## 12. 兼容与非目标

### 12.1 兼容原则

- 旧 SQLite 结果可以通过 Provider Summary 回退读取；
- 新 Experiment 不再依赖回退逻辑；
- A–D 不强制删除专用 Ladder Simulation Plugin；
- 不修改旧 Grid Rule 的交易行为；
- 不为尚未建立的 Grid StrategyDefinition 伪造归属。

### 12.2 尚未启动的配置

结果 Viewer 的主数据源是实验结果数据库。一个从未启动、尚未持久化到数据库
的 JSON 配置，不默认出现在结果 Viewer。如果未来需要查看“待运行实验”，应增加
独立的配置目录 API，不与本次结果索引混合。

---

## 13. 验收标准

### 13.1 领域边界

- `TradingRuleDefinition` 不依赖 Strategy 或 Experiment。
- `StrategyDefinition` 不包含 Experiment 列表，不导入 Experiment System。
- `market_simulator` 不导入任何具体 Strategy 或 Rule。
- Strategy 仿真关联和适配只存在于 `strategies_system` 侧。

### 13.2 数据关联

- 新 Experiment 显式指定 `strategy_definition_type`。
- 不通过 Experiment ID、description 或策略中文名猜测归属。
- ExperimentSpec 一旦持久化，即使 Run 失败，仍能建立 Strategy 归属。
- 旧数据的 Provider Summary 回退路径有独立测试。

### 13.3 实验分组

- 只改变 RuleConfig，Experiment 仍归属原 StrategyDefinition。
- 增加或删除 Rule，Experiment 归属新 StrategyDefinition。
- 改变 Market、Account、Execution 或 Seed，不改变 StrategyDefinition 归属。
- 基线 Experiment 不出现在参数研究主列表。
- Parameter Study 中的参数轴按 Strategy Parameter Mapping 显示在对应 Rule 下。

### 13.4 运行链路

- Experiment System 只通过 Provider 协议调用策略侧。
- StrategyDefinition 在运行前完成参数绑定和 RuleConfig 校验。
- SimulationRuntime 只消费通用交易意图，不识别具体 Rule Type。
- Run 结果可追溯到 Experiment、StrategyInstance、RuleInstance 和已解析 RuleConfig。

---

## 14. 一句话架构定义

```text
逻辑上，Experiment 从属于 Strategy；
数据上，ExperimentSpec 引用 StrategyDefinition；
运行时，ExperimentSystem 通过 strategies_system 适配器调用 StrategyInstance；
代码上，StrategyDefinition 永远不依赖 Simulator。
```

---

## 15. A–D 实现记录

### 15.1 显式关联和参数链路

`coinm-long-take-profit-ladder/v1` 兼容 Plugin 现在要求显式提供已注册的
`strategy_definition_type`。首批有效实验把外部参数放入 `strategy_parameters`，参数轴
直接修改该对象，Plugin 只负责将其转换为已经验证过的 COIN-M 运行配置。

Provider 在规划阶段完成以下校验：

- StrategyDefinition 已注册并规范化为 canonical type；
- 嵌套 Strategy 参数字段无缺失和未知项；
- StrategyDefinition 最终生成的 StrategySpec 与声明类型一致；
- `experiment_kind` 属于约定枚举；
- PARAMETER_STUDY 至少包含一个参数轴。

Experiment System 只增加了可选的 Provider 级 ExperimentSpec 校验钩子，不解释
`experiment_kind`、Strategy 或 Rule。

### 15.2 读取索引和旧数据

Viewer 研究模型优先从持久化 ExperimentSpec 的 Strategy Component 参数读取
`strategy_definition_type`，建立 `StrategyDefinition → Experiment[]` 反向索引。该索引
不依赖成功 Run。旧 SQLite 若没有显式字段，则从 Provider Summary 中的 canonical
Strategy type 回退，且为关联记录标明来源。

### 15.3 参数研究页面

参数研究页面当前层级为：

```text
StrategyDefinition
    → PARAMETER_STUDY Experiment
        → Strategy/Rule 分组的 ParameterAxis
            → Candidate configuration
                → Run / report
```

不同 Experiment ID 不再因为市场、账户和成本上下文相同而自动合并。一个参数扫描
Experiment 内部的候选值仍在同一报表中比较；BASELINE 不进入参数研究主列表。

### 15.4 首批迁移配置

- `btc-hold-coinm-ladder-baseline-v1`：`experiment_kind=BASELINE`；
- `btc-coinm-effective-leverage-scan-v1`：
  `experiment_kind=PARAMETER_STUDY`，有效杠杆参数轴进入
  `strategy_parameters.entry_sizing_parameters`。

旧 Grid 实验没有伪造 StrategyDefinition 归属，等待对应策略定义建立后再迁移。
