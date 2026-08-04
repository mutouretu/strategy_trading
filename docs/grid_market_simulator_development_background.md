# Grid Trading 与 Market Simulator 项目开发背景及架构规划

## 1. 文档目的

本文用于说明 `grid_trading` 与拟新建的 `market_simulator` 两个项目的开发背景、问题来源、架构边界、模块划分和阶段性实施目标。

本文暂不展开具体市场价格模型、交易策略参数、强化学习算法和收益评价方法，重点是先明确：

1. 为什么需要从现有网格交易系统中提取纯网格规则引擎；
2. 为什么需要独立建设市场模拟器；
3. 两个项目之间通过什么协议交互；
4. 哪些能力属于实盘 Web 系统，哪些能力属于市场模拟与策略实验；
5. 第一阶段最小可运行系统应实现到什么程度。

后续 Codex 的代码重构和新项目开发，应以本文作为整体设计约束。

> 2026-08 架构更新：本文保留早期演进过程，出现的 `grid_strategies`、
> `grid_metrics` 和 Grid 实验 Provider 路径均已被后续设计替代。当前高层策略归属
> `strategies_system/trading_strategies`，仿真适配、插件和策略专属指标归属
> `strategies_system/strategy_simulation`；`grid_trading/grid_rule` 继续作为既有规则实现，
> 通过策略侧 `GridRulePort` 接入，规则源码不因本次迁移而改变。

> 当前接口说明：本文记录了架构形成过程，前文出现的
> `SimulationDecisionPort` 属于已经删除的过渡设计。当前 Runtime 唯一策略入口为
> `SimulationTradePort`，Adapter 输出当前帧明确价格的 `TradeInstruction`；
> `SimulationTracePort` 只提供意图生命周期报告。

---

## 2. 现有项目背景

现有项目为：

* 远端仓库（尚未改名）：`mutouretu/grid_trading_web`
* 本地工程目录：`grid_trading`
* 定位：面向 Web 服务的 U 本位与币本位永续网格交易系统
* 主要技术栈：Python、FastAPI、Streamlit、SQLite、Binance Futures API

系统已经从原始命令行版本重构为前后端架构，并形成了较清晰的分层：

* `domain`：策略、Cell、订单模型和网格计算；
* `ports`：交易所等外部能力抽象；
* `application`：交易触发、开平仓、移动窗口和持仓协调；
* `infrastructure`：Binance、SQLite 和快照缓存；
* `runtime`：共享调度器和进程监管；
* `interfaces`：FastAPI 和 Web 客户端。

现有系统还完成了较多实盘可靠性建设，包括：

* 同币对多策略隔离；
* U 本位与币本位产品隔离；
* 币本位合约张数换算；
* 持仓资源池协调；
* 部分成交处理；
* 未知订单防重复建仓；
* 订单缺失转人工复核；
* 进程重启后的订单和保护单恢复；
* 外部手工订单与网格持仓协调；
* SQLite WAL、共享调度和性能优化。

现有 Web 系统的重点已经不是单纯的网格公式，而是如何在交易所接口不稳定、订单状态短暂不一致、进程可能中断的情况下，安全地维持真实仓位和真实订单。

---

## 3. 新需求来源

当前计划在 BTC 可能处于熊市后期的背景下，对币本位 BTCUSD 多头网格进行系统研究。

研究目标不是简单进行一次历史回测，而是逐步形成一个可以支持以下工作的实验平台：

* 根据随机过程生成不同形态的价格序列；
* 根据历史数据拟合特定标的的行为特征；
* 生成 BTC、NVDA、贵州茅台等不同资产风格的市场路径；
* 对同一策略运行多年级别的批量仿真；
* 比较网格参数、仓位参数和窗口移动规则；
* 进行 Monte Carlo 实验；
* 后续接入强化学习，对网格参数或策略行为进行动态调优；
* 将来支持网格之外的其他策略。

这些实验具有以下特点：

1. 运行步数多；
2. 需要大量重复实验；
3. 需要确定性随机种子和结果复现；
4. 不需要模拟真实交易所的网络异常；
5. 不需要查询挂单、恢复订单或进行持仓一致性修复；
6. 对执行效率的要求远高于对交易所协议还原程度的要求。

因此，不能直接让当前完整的实盘 `TradingEngine` 逐步跑多年数据。实盘引擎中的 HTTP、SQLite、轮询、订单查询、重试、恢复和审计逻辑会成为仿真实验的无效负担。

---

## 4. 核心问题

当前系统中，“网格策略”和“实盘执行”仍存在一定程度的耦合。

例如一次实盘 tick 可能同时承担：

* 获取最新价格；
* 判断 Cell 是否触发；
* 提交建仓单；
* 查询订单状态；
* 识别部分成交；
* 创建保护性平仓单；
* 移动网格窗口；
* 撤销边界订单；
* 处理未知执行结果；
* 保存数据库；
* 更新心跳和运行状态。

这些职责需要重新划分。

真正的网格策略只应关心：

* 如何生成网格；
* 在当前市场状态下应该有什么交易意图；
* 成交后 Cell 如何变化；
* 当前应该持有什么仓位；
* 是否应该移动网格；
* 策略参数如何影响状态转移。

实盘 Web 系统则应关心：

* 如何把策略意图转化为 Binance 订单；
* 如何确认订单是否真正提交；
* 如何处理部分成交、撤单竞争和未知结果；
* 如何将真实仓位分配给不同策略和 Cell；
* 如何在重启后恢复；
* 如何记录、告警和人工复核。

市场模拟器则只应关心：

* 市场下一步如何变化；
* 输出哪些市场观测；
* 如何根据模型、资产配置和随机种子生成价格或量价序列。

---

## 5. 总体架构决策

当前阶段不拆分成大量 Git 仓库，而是控制为两个工程：

```text
workspace/
├── grid_trading/
└── market_simulator/
```

工程边界保持简单，模块边界通过 Python package 和依赖规则保证。

---

## 6. 工程一：grid_trading

`grid_trading` 继续作为网格策略与网格实盘 Web 系统的一体化工程。

内部划分为三个主要业务命名空间：

```text
grid_trading/
├── grid_rule/
├── grid_strategies/
└── grid_server/
```

实际目录可以根据当前仓库结构逐步迁移，不要求第一阶段立即完成所有物理目录调整。

### 6.1 grid_rule

`grid_rule` 是确定性的单组网格规则引擎。它负责“给定一组网格后如何挂单和流转”，
不负责决定何时建立网格、投入多少资金、何时加仓或退出；后者属于更高层的策略。

职责包括：

* 网格价格计算；
* Cell 规则状态；
* 网格触发规则；
* 目标价格和目标数量计算；
* 建仓与平仓的规则状态转换；
* 移动网格的规划规则；
* 网格规则事件输出；
* 网格规则配置校验。

不得包含：

* FastAPI；
* Streamlit；
* SQLite；
* Binance API；
* HTTP 请求；
* 进程管理；
* 真实订单 ID；
* 客户端订单 ID；
* 网络重试；
* 持仓一致性修复；
* `manual_review` 等实盘异常状态。

### 6.2 grid_strategies

`grid_strategies` 存放组合一组或多组网格规则引擎的高层策略。

职责包括：

* 决定什么时候创建、调整或停止网格；
* 在多组网格之间分配资本；
* 根据市场状态加仓、减仓或整体退出；
* 实现 BTC 积累、天地单和熊市项目做空等不同策略。

策略核心可以依赖 `grid_rule`，但不能依赖交易所、SQLite、`grid_server` 或仿真运行时。
对仿真协议的转换放在 `grid_strategies.adapters`。

当前第一个最小实现是 `SingleFollowingGridStrategy`：启动时部署一组跟随网格并持续维护，
用于验证 `SimulationRunner → StrategyAdapter → Strategy → GridRuleEngine` 的完整调用链。
它不包含动态资本配置或整体退出逻辑，也不代表最终的 BTC 积累策略。

### 6.3 grid_server

`grid_server` 是网格实盘宿主。

职责包括：

* Web 页面；
* FastAPI；
* SQLite 持久化；
* Binance 及未来其他交易平台适配；
* 调度器；
* 订单查询；
* 订单状态同步；
* 部分成交处理；
* 未知执行结果处理；
* 重试和恢复；
* 持仓资源池；
* 实盘一致性校验；
* 审计、告警和人工复核。

目标上它调用 `grid_strategies` 和 `grid_rule` 获取订单意图，并负责将意图可靠地落实到
真实交易所。当前实盘实现尚未切换到新规则和策略包，需等待仿真验证完成后单独迁移。

### 6.4 仿真适配器

`grid_rule.adapters` 和未来的 `grid_strategies.adapters` 属于 `grid_trading`，
分别把规则引擎和高层策略适配到通用仿真框架。

职责包括：

* 将 `grid_rule` 适配为通用 `SimulationDecisionPort`；
* 在 `GridOrderIntent`、`GridFill` 与通用仿真订单、成交之间转换；
* 提供 USD-M、COIN-M 等产品特有的数量换算和记账适配；
* 对 COIN-M 分开记录现货底仓、币本位合约钱包以及 BTC/USDT 双计价权益；
* 组装网格配置、市场数据源、成交模型和账本；
* 汇总网格专属指标与实验结果。

适配器可以依赖所属核心和通用仿真框架，但不能把仿真订单、账本或随机市场模型
引入 `grid_rule` 或 `grid_strategies` 的核心。

---

## 7. 工程二：market_simulator

`market_simulator` 是与具体策略无关的市场生成与交易仿真框架。

内部暂时包含四个模块：

```text
market_simulator/
├── market_protocol/
├── market_simulator/
├── simulation_runtime/
└── simulation_viewer/
```

框架不包含网格或其他具体策略的配置、状态和指标。不同策略通过各自应用仓库中的适配器接入。

### 7.1 market_protocol

`market_protocol` 定义市场数据与策略运行器之间的通用协议。

它是两个工程之间的公共边界，作用类似 C++ 项目中的头文件接口。

第一阶段主要定义：

* `MarketFrame`
* `MarketBatch`
* `MarketSimulationSpec`
* `MarketSource`

原则：

* 协议包应尽可能小；
* 不依赖具体市场模型；
* 不依赖 Web；
* 不依赖数据库；
* 不依赖具体策略；
* 第一阶段只覆盖当前策略仿真所需的价格字段；
* 为未来扩展 OHLC、成交量和其他特征保留结构。

### 7.2 market_simulator

该模块负责生成市场走势。

未来可包含：

* 固定人工序列；
* 历史数据回放；
* Bootstrap 重采样；
* 几何布朗运动；
* 跳跃扩散；
* GARCH 或随机波动率；
* Regime Switching；
* 特定资产行为 profile；
* Transformer、Diffusion 等生成模型。

当前锚点 GBM 数据源已支持可选价格下限和上限；同时设置时，越界随机值通过反射边界
回到有效区间，锚点收盘价仍保持精确。

市场模型与资产行为参数应尽量分开。

例如：

```text
模型：
- jump_diffusion
- regime_switching
- generative_model

资产配置：
- btc-v1
- nvda-v1
- moutai-v1
```

市场模拟器只输出市场数据，不关心：

* 网格；
* Cell；
* 策略仓位；
* 买卖动作；
* 账户收益；
* 交易所订单。

### 7.3 simulation_runtime

`simulation_runtime` 是策略无关的仿真运行层。

它负责：

* 调用 `MarketSource`；
* 通过 `SimulationDecisionPort` 将市场数据交给注入的决策适配器；
* 维护通用逻辑限价订单；
* 使用最小成交规则生成成交反馈；
* 维护轻量账本；
* 汇总仿真结果；
* 批量运行实验；
* 后续封装 RL 环境。

它只依赖 `market_protocol`。具体 `MarketSource` 由最外层实验程序注入，运行层不依赖任何
市场生成模型实现。

不得包含或依赖任何具体策略或规则引擎，也不得出现 Cell、网格方向等具体领域概念。

### 7.4 simulation_viewer

`simulation_viewer` 是策略无关的仿真结果回放页面。

它读取标准 `SimulationRun` 数据，展示：

* OHLC K 线；
* 时间推进与逐 bar 回放；
* 活动逻辑订单；
* 成交标记；
* 现金、仓位、持仓均价和已实现盈亏；
* 逐 bar 盯市权益曲线与成交明细。

页面不生成交易决策，也不直接调用任何具体策略或规则引擎。调用方只需把订单与成交写入通用
`SimulationRun`，即可复用同一播放器。

---

## 8. 两个工程的依赖关系

目标依赖方向如下：

```text
grid_server ───────► grid_strategies ───────► grid_rule
                              ▲
                              │
grid_strategies.adapters ─────┘
             │
             └────────► simulation_runtime ◄──────── grid_rule.adapters
                              │
                              ▼
                        market_protocol
```

更明确地说：

```text
grid_trading
├── grid_rule
│   └── adapters
│       └── simulation_runtime
├── grid_strategies
│   ├── grid_rule
│   └── adapters
│       └── simulation_runtime
└── grid_server
│   ├── grid_strategies
│   └── grid_rule

market_simulator
├── market_protocol
├── market_simulator
│   └── market_protocol
└── simulation_runtime
    └── market_protocol
```

`simulation_viewer` 只消费 `simulation_runtime` 输出的持久化 `SimulationRun`，不依赖
任何具体策略包。

禁止以下依赖：

```text
grid_rule → grid_server
grid_strategies → grid_server
grid_strategies 核心 → simulation_runtime
grid_rule → market_simulator 具体模型
market_simulator   → grid_rule
market_simulator   → grid_strategies
market_simulator   → grid_server
simulation_runtime → 任意具体策略或规则引擎
market_protocol    → 任意具体策略或市场模型
```

---

## 9. 市场数据交互协议

网格规则引擎不应主动调用市场模拟器。

正确的数据流是：

```text
MarketSource
    ↓
SimulationRunner / LiveRuntime
    ↓
GridRuleEngine
```

运行器从数据源获取市场数据，再传给策略。

### 9.1 MarketFrame

第一阶段建议采用简单结构：

```python
@dataclass(frozen=True, slots=True)
class MarketFrame:
    sequence: int
    timestamp: int
    instrument: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    features: Mapping[str, Decimal]
```

只需要单点价格的策略可以读取：

```python
frame.close
```

`frame.price` 暂时作为 `frame.close` 的兼容别名。OHLC 是日线成交判断依赖的一级字段，
成交量和其他扩展特征继续放在：

```python
frame.features["volume"]
```

### 9.2 MarketSource

市场数据源协议示意：

```python
class MarketSource(Protocol):
    def reset(self, seed: int | None = None) -> MarketFrame:
        ...

    def next(self) -> MarketFrame:
        ...

    def next_batch(self, count: int) -> Sequence[MarketFrame]:
        ...

    @property
    def done(self) -> bool:
        ...
```

可形成多种实现：

* `SyntheticMarketSource`
* `HistoricalMarketSource`
* `FixedSequenceMarketSource`
* `LiveMarketSource`

但网格规则引擎只依赖 `MarketFrame`，不直接依赖 `MarketSource`。

---

## 10. 网格规则引擎与运行器接口

Web 实盘与仿真应调用同一套网格规则引擎接口。

建议核心接口包括：

```python
class GridRuleEngine:
    def initialize(
        self,
        config: GridRuleConfig,
        instrument: InstrumentSpec,
    ) -> GridState:
        ...

    def on_market(
        self,
        state: GridState,
        frame: MarketFrame,
    ) -> GridRuleDecision:
        ...

    def on_fills(
        self,
        state: GridState,
        fills: Sequence[FillEvent],
    ) -> GridRuleDecision:
        ...
```

返回结果：

```python
@dataclass(frozen=True, slots=True)
class GridRuleDecision:
    state: GridState
    order_intents: tuple[GridOrderIntent, ...]
    events: tuple[GridEvent, ...]
```

其中 `GridOrderIntent` 只表达网格规则产生的目标订单，例如：

* 标的；
* 买卖方向；
* 开仓或平仓角色；
* 目标价格；
* 目标数量；
* 对应 Cell。

它不包含真实交易所订单 ID。

---

## 11. 实盘与仿真的不同执行方式

### 11.1 实盘 Web

数据流：

```text
Binance 行情
    ↓
MarketFrame
    ↓
grid_rule
    ↓
GridOrderIntent
    ↓
实盘订单协调与一致性系统
    ↓
Binance 订单
```

真实订单成交后：

```text
Binance 成交状态
    ↓
GridFill
    ↓
grid_rule
```

实盘系统负责处理所有交易所复杂性。

### 11.2 仿真

数据流：

```text
market_simulator
    ↓
MarketFrame
    ↓
grid_rule
    ↓
GridOrderIntent
    ↓
最小成交模型
    ↓
GridFill
    ↓
grid_rule
```

第一阶段最小成交规则：

* 只有当前 bar 之前已经激活的订单才能参与当前 bar 成交；
* LIMIT 订单满足 `low <= limit_price <= high` 时视为成交，BUY/SELL 不影响成交资格；
* LIMIT 订单按限价成交；
* MARKET 订单在下一根 bar 的 open 成交；
* 全部成交；
* 不假设 bar 内的价格路径；
* 当前 bar 成交后新生成的订单最早从下一根 bar 生效；
* 不模拟挂单队列；
* 不模拟部分成交；
* 不模拟交易所网络和订单恢复。

---

## 12. 为什么规则引擎仍然输出 GridOrderIntent

即使仿真不模拟真实交易所，规则引擎也不应直接修改账户账本。

规则引擎输出 `GridOrderIntent` 的好处包括：

1. Web 和仿真使用相同规则接口；
2. 规则引擎只表达目标，不关心执行方式；
3. 仿真可以使用极简 bar 覆盖成交；
4. 实盘可以使用完整的一致性系统；
5. 后续可以替换手续费、滑点和成交模型；
6. 高层策略或 RL 可以调节网格参数，但不需要改变规则—执行边界。

---

## 13. Cell 状态拆分

当前实盘 Cell 中的以下字段不应进入纯规则引擎：

* `entry_order_id`
* `exit_order_id`
* `entry_client_id`
* `exit_client_id`
* 订单重试信息
* `manual_review`
* 交易所订单同步状态

规则 Cell 可以只保留：

```text
cell_id
index
buy_price
sell_price
position_state
open_quantity
entry_price
cycle_count
```

规则状态可简化为：

```text
FLAT
  ↓ entry fill
OPEN
  ↓ exit fill
FLAT
```

实盘订单状态由 `grid_server` 中的独立对象维护，例如：

```text
LiveOrderBinding
LiveRuntimeState
RuntimeReviewStatus
```

---

## 14. 配置拆分

现有配置中同时包含网格规则参数和实盘运行参数，需要逐步拆分。

### 14.1 GridRuleConfig

属于 `grid_rule`：

```text
instrument
grid_id
mode
anchor_price
grid_ratio
grid_count
size_value
size_unit
move_policy
```

### 14.2 LiveRuntimeConfig

属于 `grid_server`：

```text
strategy_id
exchange_profile
market_type
leverage
poll_interval
运行状态
```

### 14.3 SimulationConfig

属于 `simulation_runtime` 或具体策略的实验组合层：

```text
seed
max_steps
execution_model
fee_rate
initial_equity
结果采样频率
```

---

## 15. Python 包组织方式

两个 Git 工程内部可以包含多个 Python package。`grid_trading` 当前采用直接位于工程根目录
的命名空间：

```text
grid_trading/
├── grid_rule/
│   └── adapters/
├── grid_strategies/
│   └── adapters/
└── grid_server/
```

```text
market_simulator/
├── packages/
│   ├── market_protocol/
│   ├── market_simulator/
│   └── simulation_runtime/
└── viewer/
```

这些命名空间从工程根目录直接导入。后续只有在出现真实的跨仓库消费者时，才考虑拆分
为独立 wheel 或 editable package。无论是否拆包，都应保持：

* 修改源码立即生效；
* 上层只通过包公开接口引用；
* 如果出现明确性能瓶颈，再用 Cython、pybind11 或 Rust 编译热点模块。

---

## 16. 第一阶段最小开发范围

第一阶段重点不是市场模型，而是完成架构闭环。

### 16.1 market_simulator

完成：

1. 建立 `market_protocol`；
2. 建立最小 `MarketFrame` 和 `MarketSource`；
3. 实现固定人工价格序列；
4. 建立策略无关的 `simulation_runtime`；
5. 定义 `SimulationDecisionPort` 决策端口；
6. 实现 OHLC bar 覆盖成交和下一根 bar 开盘市价成交；
7. 实现简单线性账本；
8. 使用脚本决策组件验证运行器闭环；
9. 实现锚点约束的日频随机 OHLC；
10. 持久化 `SimulationRun` 并通过通用 K 线页面回放。

暂不要求：

* 任何具体策略实现；
* 网格 Cell、开平仓角色或网格指标；
* `grid_rule` 的临时副本。

### 16.2 grid_trading

完成：

1. 新建或明确 `grid_rule`；
2. 抽取纯网格配置、Cell 状态和网格生成函数；
3. 实现 `initialize`、`on_market`、`on_fills` 和 `GridOrderIntent`；
4. 在同一工程内建立 `grid_rule.adapters` 适配器；
5. 通过适配器接入 `simulation_runtime`；
6. 建立 `grid_strategies` 高层策略边界；
7. 让现有实盘服务在仿真验证后调用同一策略和规则实现；
8. 保持现有 Web、API 和实盘行为不变。

第一阶段不做：

* GBM；
* GARCH；
* 跳跃扩散；
* Regime Switching；
* 生成模型；
* 资产 profile；
* RL；
* 交易所异常；
* 部分成交；
* 深度和盘口；
* 强平和保证金。

---

## 17. 第一阶段验收场景

使用人工价格序列：

```text
65000
62000
59000
61000
64000
```

验证：

1. 网格初始化正确；
2. 日线 high/low 覆盖买入价格时生成成交；
3. 成交后对应 Cell 转为持仓状态；
4. 规则引擎生成对应平仓意图；
5. 后续日线 high/low 覆盖卖出价格时完成平仓；
6. Cell 循环次数增加；
7. 账本仓位和收益正确；
8. Web 实盘宿主与仿真宿主调用的是同一个网格规则引擎；
9. 市场模拟器不知道网格状态；
10. 网格规则引擎不知道市场数据来自人工序列、随机模型还是实盘行情。

---

## 18. 后续阶段

在最小闭环稳定后，再逐步加入：

### 市场模型

* 历史数据回放；
* Bootstrap；
* GBM；
* GARCH；
* Jump Diffusion；
* Regime Switching；
* BTC、NVDA、贵州茅台 profile；
* 深度学习生成模型。

### 仿真执行

* 手续费；
* 滑点；
* 成交量约束；
* OHLC 内部路径；
* 部分成交；
* 币本位和 U 本位账本；
* 保证金；
* 资金费；
* 强平模型。

### 实验能力

* 参数批量扫描；
* Monte Carlo；
* 多进程或向量化环境；
* ExactMode 与 FastMode；
* Gymnasium 风格 RL 环境；
* 结果可视化；
* Web 实验管理。

---

## 19. 设计原则总结

### 原则一：市场与策略解耦

市场模拟器只回答：

> 市场接下来怎么走。

高层网格策略回答：

> 什么时候建立或撤销网格，投入多少资金，以及何时加仓、减仓或退出。

网格规则引擎只回答：

> 给定一组网格配置和当前状态后，应维护哪些订单，成交后状态如何流转。

### 原则二：规则、策略与实盘执行解耦

网格规则引擎只输出网格订单意图；高层策略负责组合和调整一组或多组规则引擎。

Web 实盘系统负责：

> 如何安全、可靠地将交易意图落实到真实交易所。

### 原则三：两个工程，多个内部包

不为了形式上的解耦建立过多 Git 仓库。

采用：

```text
两个 Git 工程
+ 清晰的 Python package 边界
+ 单向依赖规则
```

### 原则四：先保证闭环，再研究市场模型

第一阶段先跑通：

```text
人工价格
→ 市场协议
→ 网格规则引擎
→ 最小成交
→ 成交反馈
→ 策略状态
→ 账本结果
```

随机过程、生成模型和 RL 都放在闭环之后。

### 原则五：不要让实盘复杂性进入高速仿真

仿真中不处理：

* HTTP；
* SQLite；
* 挂单查询；
* 网络重试；
* 人工复核；
* 订单恢复；
* 持仓一致性修复。

这些继续保留在 Web 实盘系统中。

---

## 20. 最终项目定位

### grid_trading

网格策略及其生产级实盘系统。

内部包括：

```text
grid_rule
grid_strategies
grid_server
```

### market_simulator

策略无关的市场生成、交易仿真运行与后续实验基础框架。

内部包括：

```text
market_protocol
market_simulator
simulation_runtime
simulation_viewer
```

`grid_rule`、`grid_strategies` 及其仿真适配器继续属于 `grid_trading`。
高层网格策略负责组合一组或多组 `GridRuleEngine`。其他类型的交易策略则在各自应用中
拥有自己的决策模块和仿真适配器，并通过 `market_protocol`、`SimulationTradePort`
与纯仿真框架组合。
