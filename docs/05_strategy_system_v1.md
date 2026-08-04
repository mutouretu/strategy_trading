# 第 5 部分：策略体系 v1.0 方案

## 1. 文档状态

本文定义策略仿真研究平台第五部分“策略体系”的 v1.0 边界、代码归属、
首批策略和实施顺序。

当前状态：5A—5G 已完成实现与验收，详见
《第 5 部分：策略体系 v1.0 验收记录》；尚未进行版本标记。

本文承接：

- 《策略仿真研究平台总体规划》；
- 《第 1 部分：仿真执行系统 v1.0》；
- 《第 2 部分：实验系统 v1.0》；
- 《第 3 部分：评价指标系统 v1.0》；
- 已有 `grid_rule`、单组跟随网格和分层跟随网格实现。

第四部分“市场环境”与第五部分相互独立又可交叉推进。第五部分开发期间继续使用
现有 Anchored GBM 和固定行情；第四部分以后增加市场源时，不改变策略接口。

---

## 2. 模块目标

策略体系回答：

> 策略看到市场、成交和自身状态后，希望建立什么仓位、保留什么交易意图，以及在
> 什么条件下退出？

v1.0 的目标是：

1. 将具体策略放在 simulator 外围；
2. 形成可同时接入仿真与未来实盘的纯策略核心；
3. 建立最小但可解释的基准策略集合；
4. 首先实现“目标强平价建仓 + 阶梯止盈”策略；
5. 复用现有实验、指标、COIN-M 账本和强平模型；
6. 为后续网格资本分配策略提供清晰边界。

v1.0 不以策略收益最大化为目标，也不进行自动参数优化。

---

## 3. 术语和语义边界

### 3.1 Rule

Rule 是给定状态和参数后的机械交易规则。

例如：

- 根据锚点、间距和格数生成 Grid Cell；
- Entry 成交后生成相邻 Exit；
- K 线覆盖被动价格时形成显式成交指令；
- COIN-M 合约数量和价格步长取整。

`grid_rule` 属于这一层。

### 3.2 Strategy

Strategy 决定：

- 什么时候建仓；
- 建多少仓位；
- 使用什么 Rule 或订单结构；
- 什么时候加仓、暂停或退出；
- 如何在多个子规则之间分配资本。

“目标强平价建仓 + 阶梯止盈”同时决定建仓时机、仓位规模和退出计划，属于
Strategy，而不是 Grid Rule。

### 3.3 Risk control

策略风险控制是策略决策约束，例如最大仓位、暂停开仓和风险预算。

平台强平不是策略风控，而是账户执行事实：达到交易平台强平条件时，
`simulation_runtime` 必须终止运行。

### 3.4 Adapter

Adapter 将策略领域输出转换为目标运行环境的协议。

- Simulation Adapter 实现 `SimulationTradePort`；
- Live Adapter 将来对接实盘账户、行情和订单服务；
- 策略核心不直接生成 `SimFill`，也不调用 `SimulationRunner`。

---

## 4. 总体代码位置

### 4.1 顶层结构

新增与两个现有工程并列的独立策略工程：

```text
strategy_trading/
├── market_simulator/
├── grid_trading/
├── strategies_system/
└── docs/
```

三个工程的职责为：

```text
market_simulator
    通用市场、执行、实验、指标和结果展示

grid_trading
    网格规则、COIN-M 交易基础设施和实盘 Server

strategies_system
    具体高层策略、仿真适配、实验接入和后续研究代码
```

工程目录名使用 `strategies_system`。它是策略代码的单一所有边界，同时容纳
纯策略、仿真接入和以后的策略优化研究；不再为 `strategy_research` 单独建立仓库。
仓库名称是物理边界，Python 包名称继续表达不同的运行时责任。

### 4.2 `strategies_system` 建议目录

```text
strategies_system/
├── pyproject.toml
├── README.md
├── src/
│   ├── trading_strategies/
│   │   ├── __init__.py
│   │   ├── baselines/
│   │   │   └── hold_btc.py
│   │   ├── btc_accumulation/
│   │       ├── models.py
│   │       ├── ports.py
│   │       ├── target_liquidation_ladder.py
│   │       └── take_profit_schedule.py
│   │   └── grid_following/
│   │       ├── ports.py
│   │       ├── single.py
│   │       └── layered.py
│   └── strategy_simulation/
│       ├── __init__.py
│       ├── contracts.py
│       ├── registry.py
│       ├── components/
│       │   ├── accounts.py
│       │   ├── executions.py
│       │   └── markets.py
│       ├── adapters/
│       │   ├── hold_btc.py
│       │   ├── target_liquidation_ladder.py
│       │   ├── coinm_position_sizer.py
│       │   ├── grid_rule_engine.py
│       │   ├── single_following_grid.py
│       │   └── layered_following_grid.py
│       ├── plugins/
│       │   ├── hold_btc.py
│       │   ├── target_liquidation_ladder.py
│       │   ├── single_following_grid.py
│       │   └── layered_following_grid.py
│       ├── experiment_provider/
│       │   ├── provider.py
│       │   └── descriptors.py
│       └── metrics/
├── experiments/                  # 可复现的基线和验收配置
├── research/                     # 第 6 部分开始后再填充
│   ├── parameter_search/
│   ├── scenario_studies/
│   ├── optimization/
│   ├── model_training/
│   └── reports/
└── tests/
```

两个 Python 包表示两个运行时责任：

```text
trading_strategies.baselines
trading_strategies.btc_accumulation
    纯策略核心

strategy_simulation.adapters
    策略与 simulation_runtime 之间的适配层

strategy_simulation.experiment_provider
    具体策略与通用实验系统之间的组装层

strategy_simulation.components
    策略实验所需的市场、执行与账户组件定义

strategy_simulation.registry / plugins
    策略仿真插件的显式注册和查找层
```

`trading_strategies` 不得导入 `strategy_simulation`。实盘工程将来可以只依赖
纯策略包，而不引入 simulator 和实验系统。`research/` 不是 v1.0 运行时依赖，
本部分只确立边界，不提前实现调参、训练或优化工具。

### 4.3 不应放置的位置

本部分代码不得放入：

- `market_simulator/packages/simulation_runtime`；
- `market_simulator/packages/market_simulator`；
- `market_simulator/examples`；
- `grid_trading/grid_rule`；
- Viewer 前端。

### 4.4 现有网格策略

单组与分层跟随网格均已迁入 `trading_strategies.grid_following`，对应的
Simulation Adapter、Plugin、指标和实验位于 `strategy_simulation`。策略核心只依赖
策略侧 `GridRulePort` 和 `grid_rule` 公共 DTO，不创建具体 `GridRuleEngine`；默认
Engine 包装由 `strategy_simulation.adapters.GridRuleEnginePort` 提供。

---

## 5. 依赖方向和运行时调用

### 5.1 代码依赖

```text
strategies_system: trading_strategies
        不依赖 market_simulator、simulation_runtime 和 grid_server
        网格策略依赖策略侧 GridRulePort 和 grid_rule 公共 DTO
        不直接依赖 GridRuleEngine
        非网格策略不依赖 grid_rule

strategies_system: strategy_simulation.adapters
        → trading_strategies
        → market_protocol
        → simulation_runtime

strategies_system: strategy_simulation.experiment_provider
        → trading_strategies
        → strategy_simulation.adapters
        → experiment_system
        → market_simulator
        → simulation_runtime
        → 已验证的 COIN-M 账户与执行 adapter
```

通过架构测试禁止策略核心导入 simulator、实验系统、Web Server 或交易所 SDK。

### 5.2 运行时调用

```text
Experiment Provider
    → 构造 MarketSource
    → 构造 Strategy Core
    → 构造 Simulation Adapter
    → 构造 Account / Execution / Margin Model
    → 注入 SimulationRunner

SimulationRunner
    → initialize(frame)
    → instructions_for(frame)
    → 应用成交和账本
    → on_fills(fills)
    → on_market(frame)
```

`SimulationRunner` 是运行时发起者，但不导入、创建或识别任何具体策略。

### 5.3 通用端口

现有 `simulation_runtime.SimulationTradePort` 继续作为仿真接入边界：

```python
class SimulationTradePort(Protocol):
    def initialize(self, frame): ...
    def instructions_for(self, frame): ...
    def on_fills(self, fills): ...
    def on_market(self, frame): ...
```

v1.0 不再新增名为 `SimulationStrategy` 的空泛接口，也不要求所有策略核心继承同一
基类。不同策略通过各自的 Simulation Adapter 实现统一仿真端口。

### 5.4 通用策略注册

`strategies_system` 提供显式的 `SimulationStrategyRegistry`。共同接口位于
Simulation Plugin 层，不位于纯策略核心层：

```python
class SimulationStrategyPlugin(Protocol):
    strategy_type: str

    def descriptor(self) -> StrategyDescriptor: ...
    def resolve(self, component: ComponentSpec) -> ComponentSpec: ...
    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding: ...
```

`SimulationStrategyBinding` 至少包含：

- 实现 `SimulationTradePort` 的 `trade_port`；
- 用于 Provider Summary 的策略事实读取器；
- 策略类型、版本和展示描述。

`SimulationStrategyBuildContext` 只提供 Provider 已装配的只读环境能力，例如
instrument Profile 和可选的仓位计算端口。Plugin 不得通过 Context 操作
Runner 或直接修改账本。

注册是显式的，实验 JSON 不得导入任意 Python 类：

```python
registry.register(HoldBtcSimulationPlugin())
registry.register(RsiLongOnlySimulationPlugin())
registry.register(LayeredFollowingGridSimulationPlugin())
```

Registry 必须拒绝重复 `strategy_type`，并对未注册类型给出明确错误。
新增 RSI、Grid 或其他策略时，只新增策略核心、Adapter 和 Plugin，
不修改通用 Provider 的类型分支。

---

## 6. COIN-M 基础设施复用边界

### 6.1 原则

COIN-M 的以下能力不得复制进新策略工程：

- 反向合约盈亏公式；
- 平均开仓价和已实现盈亏；
- 手续费和资金费入账；
- 初始保证金和维持保证金；
- 预计强平价；
- 强平判定。

策略只表达目标，产品 adapter 负责计算。

### 6.2 过渡依赖

现有 COIN-M 账本和保证金实现位于 `grid_trading`。v1.0 的
`strategies_system` 可以在 `strategy_simulation.experiment_provider` 和
`strategy_simulation.adapters` 层复用这些公开对象，
但 `trading_strategies` 不得依赖这些产品执行组件。网格策略依赖
`grid_rule` 公共核心属于 Rule 依赖，不在此限制之内。

当前所指的 COIN-M 产品执行组件是：

- `InverseContractLedger`；
- `InverseContractMarginModel`；
- `InverseContractFeeModel`；
- `FixedRateInverseContractFundingModel`。

它们与 `GridRuleEngine` 是两类不同依赖。禁止复制上述组件、相关公式或实验
账户工厂。

后续是否将 COIN-M 产品 adapter 从 `grid_rule.adapters` 迁到语义更准确的包，属于
独立基础设施整理任务，不与第一个策略同时进行。

### 6.3 仓位计算端口

策略核心通过产品无关端口请求仓位计划：

```python
class TargetLiquidationPositionSizer(Protocol):
    def size_long(
        self,
        *,
        entry_price: Decimal,
        target_liquidation_price: Decimal,
    ) -> PositionPlan: ...
```

`PositionPlan` 至少包含：

- 数量；
- 数量单位；
- 预计强平价；
- 初始保证金；
- 维持保证金；
- 预计保证金缓冲；
- 计算模型版本。

Simulation Adapter 注入 COIN-M 实现；未来 Live Adapter 可以根据真实账户快照提供
另一实现。

---

## 7. v1.0 基准策略集合

### 7.1 BTC 持有基准

类型：`hold-btc/v1`

行为：

- 不生成任何交易指令；
- BTC 权益不因策略交易变化；
- USDT 权益随 BTC 市场价格变化；
- 用于解释其他策略是否增加 BTC 数量。

在当前全 BTC 账户中，“不交易”和“HODL”语义相同，v1.0 不重复实现两个组件。

### 7.2 目标强平价阶梯止盈多头

类型：`target-liquidation-ladder-long/v1`

行为：

1. 在实验开始后的第一个可成交开盘价建立 COIN-M 多仓；
2. 仓位数量由目标强平价反算；
3. 建仓成交后生成多个被动 `reduce_only` 卖出意图；
4. 每个价格档位触发后部分平仓；
5. 最后一个档位处理取整余量并完全退出；
6. 不重新建仓、不追涨、不补仓。

### 7.3 已有网格参与者

单组和分层跟随网格均由 `strategies_system` 注册和提供，并通过同一个
`SimulationStrategyRegistry` 进入实验结果页。

---

## 8. 目标强平价阶梯止盈策略规格

### 8.1 策略配置

建议配置：

```json
{
  "strategy_id": "btc-target-liquidation-ladder",
  "instrument": "BTCUSD_PERP",
  "side": "LONG",
  "entry_timing": "NEXT_OPEN",
  "target_liquidation_price": "20000",
  "first_take_profit_ratio": "1.10",
  "take_profit_end_price": "150000",
  "take_profit_count": 10,
  "take_profit_spacing": "GEOMETRIC",
  "take_profit_quantity_mode": "EQUAL_CONTRACTS",
  "close_all_at_last_level": true
}
```

止盈起点不能使用 20,000。20,000 是下行风险边界；止盈档位必须严格高于实际建仓
成交价。

已确认 v1.0 使用 `first_take_profit_ratio`：第一档止盈价格相对实际建仓
成交价计算，例如 `entry_fill_price × 1.10`。v1.0 不再提供
`first_take_profit_price` 绝对价格口径，避免两套参数优先级。

上述 20,000 和 150,000 只是某个具体实验的示例值，不是策略体系框架的
全局默认值。

### 8.2 建仓时序

当前 Runner 在第一根 Frame 上初始化 Adapter，从下一根 Frame 开始请求显式成交
指令。因此 v1.0 将“立即建仓”精确定义为：

```text
第 t-1 根 Frame
    初始化策略并声明建仓计划

第 t 根 Frame open
    生成 ACTIVE 买入指令并成交
```

不为本策略修改已经稳定的 Runner 首帧语义。

### 8.3 仓位反算

策略输入是目标强平价，不是固定杠杆或固定合约张数。

COIN-M Simulation Adapter 应使用现有账户和保证金模型反算“满足目标强平价的最大
合法合约数量”，并按合约步长向下取整。计算后必须用同一个 Margin Model 再次验证：

```text
estimated_liquidation_price <= target_liquidation_price
```

如果因为钱包余额、价格、维护保证金阶梯或最小合约数量而无法生成合法仓位，
当前 Run 应在预检阶段失败，不得静默改成另一仓位目标。

### 8.4 费用和安全余量

仓位反算能力必须能够考虑建仓手续费对钱包余额的影响。目标强平价和
安全余量都属于具体策略配置，不属于框架的全局风控或默认参数。
需要该能力的策略可以显式声明：

```json
{
  "sizing_safety_buffer_ratio": "0.00"
}
```

当前搭建框架时不固定 20,000，也不设定全局非零 buffer。实现具体策略时，
再由它的配置、仓位公式测试和实验用例确定数值；所有实际使用的值都必须
写入解析后的 RunSpec。

### 8.5 阶梯止盈价格

几何间距定义为：

```text
P_i = P_first × (P_end / P_first)^(i / (n - 1))
```

其中：

- `i = 0 ... n - 1`；
- `P_first > entry_fill_price`；
- `P_end > P_first`；
- `n >= 2`；
- 所有价格按 tick size 取整后必须严格递增。

### 8.6 阶梯止盈数量

`EQUAL_CONTRACTS` 将初始合约数量尽量平均分配到所有止盈档位：

- 每档数量按 quantity step 向下取整；
- 前 `n-1` 档使用标准数量；
- 最后一档接收全部取整余量；
- 所有退出指令均为 `reduce_only=True`；
- 任意时点累计退出量不得超过已成交建仓量。

### 8.7 状态机

```text
WAITING_ENTRY
    → ENTRY_PENDING
    → POSITION_OPEN
    → PARTIALLY_EXITED
    → COMPLETED

任意持仓状态
    → LIQUIDATED（由 runtime 终止，不由策略伪造）
```

策略不自行修改账本。状态变化必须来自真实 `SimFill`。

---

## 9. 策略行为记录和指标

### 9.1 v1.0 记录方式

v1.0 不新增通用策略事件存储系统。通过以下现有事实表达行为：

- `IntentSnapshot.tags`；
- `TradeInstruction.tags`；
- `SimFill.tags`；
- Provider Summary；
- 策略专属 MetricSet。

### 9.2 意图标签

至少记录：

- `strategy_type`；
- `strategy_id`；
- `role=entry|take_profit`；
- `take_profit_level`；
- `target_liquidation_price`；
- `quantity_unit=contracts`；
- `contract_size`。

### 9.3 Provider Summary

至少记录：

- 计划和实际建仓价；
- 建仓合约数量；
- 建仓后的预计强平价；
- 目标强平价偏差；
- 止盈档位总数和已完成档位数；
- 已退出数量和剩余数量；
- 策略是否完成；
- 最后触发的止盈档位。

### 9.4 策略专属指标

建议 MetricSet：`btc-accumulation/v1`

首批指标：

- `strategy.entry_contracts`；
- `strategy.estimated_liquidation_price_after_entry`；
- `strategy.liquidation_target_deviation_rate`；
- `strategy.take_profit_level_count`；
- `strategy.completed_take_profit_level_count`；
- `strategy.take_profit_completion_rate`；
- `strategy.exited_contracts`；
- `strategy.remaining_contracts`；
- `strategy.completed`。

收益、回撤、手续费、资金费、仓位和实际强平继续使用 `core/v1`，不得在策略指标中
重复计算。

---

## 10. 实验系统接入

### 10.1 Strategy Component

实验系统中的策略候选仍使用通用 `ComponentSpec`：

```json
{
  "key": "btc-ladder-baseline",
  "type": "target-liquidation-ladder-long/v1",
  "parameters": {}
}
```

### 10.2 Provider 归属

通用策略仿真 Provider 位于 `strategies_system` 工程的
`strategy_simulation.experiment_provider` 包，不将具体策略代码写入
`market_simulator`。宿主组装入口将该 Provider 显式注册进通用
`experiment_system.ProviderRegistry`。

两级注册边界为：

```text
experiment_system.ProviderRegistry
    └── strategies-simulation/v1
            └── SimulationStrategyRegistry
                    ├── hold-btc/v1
                    ├── target-liquidation-ladder-long/v1
                    ├── single-following-grid/v1
                    ├── layered-following-grid/v1
                    └── rsi-long-only/v1（后续）
```

Provider 负责：

- 通过 `SimulationStrategyRegistry` 解析策略类型；
- 将策略配置解析和 Adapter 构造委托给已注册 Plugin；
- 复用市场、执行和账户组件；
- 进行跨组件一致性校验；
- 输出策略 Provider Summary 和展示描述。

Provider 不得使用 `if strategy_type == ...` 或对具体 Adapter 进行
`isinstance` 分支。策略专属的默认值、校验、构造和 Summary 事实由
Plugin 所有。

### 10.3 市场和账户

首个验收实验复用当前配置：

- Anchored GBM 三年日线；
- Seed 42、43；
- 1.1 BTC 全部进入 COIN-M 全仓钱包；
- 5 倍配置杠杆；
- 0.5% 固定维持保证金率；
- `ADVERSE_EXTREME` 强平采样；
- Maker/Taker 手续费；
- 日资金费模型继续按实验配置选择。

### 10.4 首批对照实验

```text
相同 Market × 相同 Seed × 相同 Account × 相同 Execution

hold-btc/v1
target-liquidation-ladder-long/v1
single-following-grid/v1
```

第一批只用于证明调用和评价关系正确，不据此宣布策略优劣。

---

## 11. 实盘接入边界

v1.0 只实现 Simulation Adapter，不修改实盘 Server。

未来 Live Adapter 必须：

- 从真实账户读取钱包、持仓和保证金档位；
- 在提交建仓单前重新计算目标强平价；
- 处理实际成交均价和部分成交；
- 使用交易所 `reduce_only` 止盈单；
- 重启后恢复策略状态；
- 将实际持仓与策略计划持续对账。

仿真策略经过场景、样本外和实盘适用性验证前，不得直接连接真实交易入口。

---

## 12. 实施批次

### 12.1 5A：独立工程和架构边界

- 新建 `strategies_system` 工程；
- 建立 `src` 包结构和测试入口；
- 建立 `trading_strategies` 和 `strategy_simulation` 两个 Python 包；
- 实现 `SimulationStrategyPlugin`、`SimulationStrategyRegistry` 和 Binding 边界；
- 实现不包含具体策略分支的通用 Strategy Experiment Provider；
- 建立策略核心禁止依赖 simulator/Web/SDK 的架构测试；
- 禁止 `trading_strategies` 反向导入 `strategy_simulation`；
- 建立 `experiments/` 与 `research/` 的边界，但暂不实现策略优化；
- 接通相邻工程的本地开发依赖；
- 只建立骨架，不实现策略行为。

### 12.2 5B：BTC 持有基准

- 实现无交易的策略核心/Adapter；
- 注册 `hold-btc/v1`；
- 跑通单 Run 和双 Seed 实验；
- 验证 BTC 收益为 0、USDT 收益随行情变化；
- 在结果页显示策略名称和说明。

### 12.3 5C：仓位反算和阶梯计划

- 实现 `TargetLiquidationPositionSizer` 端口；
- 实现 COIN-M 仿真仓位反算 Adapter；
- 实现几何止盈价格和等合约数量分配；
- 对取整、费用、维护保证金率和边界值进行手算测试；
- 用现有 Margin Model 反向校验预计强平价。

### 12.4 5D：目标强平价阶梯策略

- 实现策略状态机；
- 实现主动建仓和被动 `reduce_only` 退出；
- 实现 Intent/Fill tags；
- 实现 Provider Summary；
- 验证全部退出、部分退出、未触及、强平四类路径。

### 12.5 5E：策略专属指标

- 注册 `btc-accumulation/v1`；
- 计算目标强平价偏差、止盈进度和剩余仓位；
- 接入单 Run 与 Scenario 聚合；
- 在全部指标中展示，关键指标只保留必要项。

### 12.6 5F：基线对照实验

- 建立统一的三策略对照 ExperimentSpec；
- 注册策略工程自有的 `single-following-grid/v1`；
- 使用相同 Market、Seed、Account 和 Execution；
- 生成并保存结果；
- 在实验总览中按策略分组；
- 验证回放中的 Entry 和各级止盈标记。

### 12.7 5G：验收和版本

- 运行三个参与工程的测试；
- 验证通用 simulator 不依赖具体策略；
- 验证策略核心不依赖 simulator 和实盘基础设施；
- 固定策略类型、配置和指标版本；
- 完成第五部分 v1.0 验收记录。

---

## 13. 测试计划

### 13.1 策略核心单元测试

- 配置字段和数值边界；
- 状态机合法迁移；
- 重复初始化；
- 未建仓时禁止止盈；
- 重复 Fill 幂等保护；
- 最后一档完全退出。

### 13.2 仓位反算测试

- 手算固定维持保证金率；
- 目标强平价必须低于建仓价；
- 合约张数向下取整；
- 取整后预计强平价不高于目标；
- 钱包不足和最小合约限制；
- 费用后强平价校验；
- 维护保证金档位边界。

### 13.3 止盈计划测试

- 几何价格严格递增；
- tick size 取整后不重复；
- 合约数量总和等于建仓成交量；
- 每个退出意图均为 reduce-only；
- K 线覆盖多个档位时全部合法成交；
- 跳空穿越多个档位仍按各自目标价成交。

### 13.4 集成测试

- 建仓后从未上涨；
- 逐级上涨至完全退出；
- 一根日线覆盖多个止盈档位；
- 止盈前先触发强平；
- 部分止盈后再大幅下跌；
- 两个 Seed 复现相同结果；
- Trace 清理前后 Summary 指标保持。

### 13.5 架构测试

- `market_simulator` 不出现具体策略类型；
- 策略核心不导入 `simulation_runtime`；
- 只有 Simulation Adapter 创建 TradeInstruction；
- 只有 Provider 组装市场、策略、执行和账户；
- 注册重复策略类型必须失败；
- 未注册策略类型必须给出明确错误；
- 使用测试 Plugin 验证主动信号和被动价格两类策略均可注册；
- 新增测试 Plugin 不得修改通用 Provider；
- 不复制 COIN-M 账本和保证金公式。

---

## 14. 验收标准

第五部分 v1.0 完成必须同时满足：

1. `strategies_system` 是独立工程，内部提供 `trading_strategies` 和
   `strategy_simulation` 两个 Python 包；
2. 通用 Provider 通过 Registry 加载策略，不硬编码具体策略类型；
3. 主动信号与被动价格策略均可通过同一 Registry 注册；
4. `hold-btc/v1` 可完成实验并作为基准；
5. `target-liquidation-ladder-long/v1` 可完成完整仿真；
6. 建仓数量由目标强平价和账户事实反算；
7. 预计强平价经过现有 Margin Model 校验；
8. 所有止盈指令为 reduce-only；
9. 完全退出后仓位为 0；
10. 未完全退出时残余仓位和止盈进度可解释；
11. 实际强平继续由 runtime 判定；
12. 三策略可在相同场景下比较；
13. 通用指标和策略专属指标均可读取；
14. Viewer 可以回放建仓与分批退出；
15. 三个工程的架构和回归测试通过；
16. 不修改或复制已验证的网格规则。

---

## 15. v1.0 暂不支持

- 实盘交易接入；
- 建仓后加仓；
- 止盈后重新开仓；
- 动态上调或下调目标强平价；
- 追踪止盈；
- 止损；
- 多方向或双向持仓；
- 多资产联合策略；
- 多策略共享风险预算；
- 自动参数优化；
- 强化学习；
- 深度生成市场模型。

---

## 16. 已确认设计决定

### 16.1 工程名称

已确认工程目录名使用 `strategies_system`。仓库内部使用
`trading_strategies` 表示纯策略核心，使用 `strategy_simulation` 表示
Simulation Adapter 和 Experiment Provider。后续策略研究也放在该仓库的
`research/` 中，不再单独建立 `strategy_research` 仓库。

### 16.2 止盈起点

已确认选择相对实际建仓成交价，例如第一档为 `entry_fill_price × 1.10`。
绝对第一止盈价不进入 v1.0 配置。

### 16.3 止盈数量

已确认 v1.0 使用等合约张数分配，最后一档吸收取整余量。

### 16.4 仓位安全余量

已确认当前只搭建可装配具体策略的框架，不在框架中固定 20,000 或任何
非零 sizing buffer。这些数值由以后装入的具体策略和实验配置决定。

### 16.5 过渡依赖

已确认 v1.0 允许 `strategies_system.strategy_simulation` 的 Adapter 和
Experiment Provider 复用 `grid_trading` 中已验证的 COIN-M 账本、保证金、
手续费和资金费组件，不复制相关公式。`trading_strategies` 中的纯策略
核心不得依赖这些产品执行组件。网格策略只依赖策略侧 `GridRulePort` 和
`grid_rule` 公共 DTO；`GridRuleEngine` 的具体包装属于外层 Adapter，两类依赖不混同。

### 16.6 通用注册边界

已确认当前不改造 `grid_trading` 实盘 Server。`strategies_system` 先实现
通用 `SimulationStrategyRegistry`；Grid、RSI 和其他策略均通过 Plugin 显式注册，
并最终交付 `SimulationTradePort`。纯策略核心不强制继承通用仿真接口。
