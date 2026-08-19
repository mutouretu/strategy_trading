# 第 5 部分：交易规则与策略应用层 v2.0 方案

## 1. 文档状态

本文重新定义策略体系 v2.0 的核心语义：

```text
TradingRule × n（n ≥ 1）       Strategy Parameters
    可复用的底层交易规则          外部策略参数
              \                 /
               StrategyDefinition
                  validate / resolve / compose
                          ↓
StrategySpec
    参数已解析的 n 条 Rule 组合
        ↓ instantiate
StrategyInstance
    一套策略的一次运行
        ↓ host
StrategyApplication
    同一账户中运行一个或多个策略实例
```

当前状态：5V2-A Rule 领域契约、5V2-B StrategySpec/Ladder 单实例迁移、5V2-C
同策略多实例、5V2-D 多策略独立并行、5V2-E 虚拟仓位与财务归因，以及 5V2-F
实验与前端接线均已完成。
现有 COIN-M 阶梯止盈仿真兼容入口不变，内部已经通过两条 TradingRule 运行。

5V2-A 至 5V2-F 已完成以下公共领域契约、首个策略迁移、多实例运行、实验结果和前端
研究入口：

- 底层行为抽象统一为 `TradingRule`；
- `TradingRuleSpec` 保存 Strategy 解析后分配给单条 Rule 的内部配置；
- `TradingRule` 使用独立的 RuleConfig、Input、State 和 Context 类型参数；
- TradingEvent、RuleIntentProposal 和 RuleTransition 使用 Rule 领域命名；
- 旧的单 Rule StrategySpec 已删除，新的 `StrategySpec` 专门组合 n 条 Rule（n ≥ 1）；
- 已建立 `StrategyDefinition`、RuleSlot、Coordination Policy、TradingRuleInstance 和
  StrategyInstance；
- 已建立最小 StrategyApplication，同一 StrategySpec 可以启动多个拥有独立 Allocation、
  Position Owner、Rule State 和 Intent 空间的 StrategyInstance；
- StrategyApplication 已可装载不同 StrategySpec，对同一上游事件进行跨策略
  原子预演、Application 批准和稳定顺序提交；
- 已建立 Virtual Position Book，按 Strategy Owner 维护仓位、已实现/未实现盈亏、
  手续费、资金费和账户结果对账残差；
- Ladder 已拆分为 `InitialEntryRule` 与 `LadderTakeProfitRule`，并由兼容门面接入原有
  仿真 Plugin/Adapter；
- HODL 与 Grid 仍使用 v1.0 运行路径；实验模型和前端已经兼容新旧两类结果。

本文承接：

- 《策略仿真研究平台总体规划》；
- 《第 5 部分：策略体系 v1.0 方案》；
- 《第 5 部分：策略体系 v1.0 验收记录》；
- 已有 Ladder、HODL、固定网格、单组跟随网格和分层跟随网格实现。

5V2-B 不修改历史数据库；通过兼容门面保持现有 Ladder Plugin、Adapter 与策略 Summary
行为，同时将内部运行链迁移到新契约。

---

## 2. 核心定义

### 2.1 Trading Rule

`TradingRule` 是策略中可复用的底层交易规则。运行时，它接收 Strategy 已解析并分配的
RuleConfig、本次具体 Rule Input、自身状态和只读上下文，输出下一状态与交易意图。

统一使用以下状态转换表达式：

```text
(sₜ₊₁, yₜ) = T_rule(sₜ, xₜ; θ)

xₜ ∈ X_rule
X_rule = InputType₁ | InputType₂ | ... | InputTypeₘ,  m ≥ 1
```

其中：

- `T_rule`：具体 TradingRule 的状态转换函数；
- `sₜ`：当前 Rule State；
- `xₜ`：本次转换消费的一种具体 Rule Input；
- `X_rule`：该 Rule 接受的输入类型集合；
- `θ`：Strategy 解析后分配的固定 RuleConfig；
- `yₜ`：本次转换产生的 Rule Output，例如交易意图、撤销请求、生命周期请求或领域观察；
- `sₜ₊₁`：转换后的 Rule State。

`xₜ` 不是所有市场、账户和成交字段组成的默认聚合快照。首版使用不可变 dataclass 组成
联合输入类型；一次 Rule 转换只消费一个具体输入，不接受通用 `dict` 或组合输入。同一个
上游领域事件可以被 StrategyInstance 映射为多条 Rule 各自的一个输入，并作为一个原子
提案批次预演、校验和提交。Rule 处理结果不依赖 RuleSlot 注册顺序。

典型 Rule 包括：

- 初始建仓规则；
- 阶梯止盈规则；
- RSI 信号规则；
- 马丁式补仓规则；
- 网格 Cell 循环规则；
- 网格跟随或部署规则；
- 趋势退出规则。

Rule 只表达一个清晰、可复用的状态转换机制，不代表一套完整策略。

### 2.2 Strategy

Strategy 是 n 条 Rule（n ≥ 1）的可运行组合。Strategy 接收外部策略参数，完成校验、
转换和分配，并定义各 Rule 的职责与协作关系。

建议使用以下形式化表达：

```text
StrategyParameters = external_parameters

ResolvedRuleConfigᵢ = resolveᵢ(StrategyParameters)

RuleSpecᵢ = TradingRuleSpec(
    TradingRuleᵢ,
    ResolvedRuleConfigᵢ
)

i ∈ {1, 2, ..., n}, n ≥ 1

StrategySpec = compose(
    RuleSpec₁,
    RuleSpec₂,
    ...,
    RuleSpecₙ,
    binding,
    sizing policies,
    coordination policy
)

StrategyInstance = instantiate(StrategySpec)
```

因此：

> Rule 与 Strategy 不是继承关系：Rule 是可组合的底层构件，Strategy 通过组合 n 条
> Rule（n ≥ 1）形成。

### 2.3 Environment

市场环境不属于 Strategy 定义。完整实验运行是：

```text
ExperimentRun
    = StrategyApplication
    × MarketEnvironment
    × Account
    × Execution
    × Seed
```

同一个 StrategySpec 可以进入牛市、熊市、横盘和不同随机路径。市场不同不会生成新的
Strategy 类型，否则前端和研究对象会再次爆炸。

### 2.4 Strategy Parameter、Signal 与 Rule 变量

在前端、实验配置和实盘配置的策略配置部分，对外暴露的交易行为参数统一属于
Strategy，例如：

- BTC 或 ETH；
- LONG 或 SHORT；
- COIN-M、USDT-M 或 SPOT；
- 有效杠杆率或目标强平价；
- 网格间距和数量；
- 阶梯档位和价格比例；
- 每笔下单数量。

领域语义上由 Strategy 首先接收这些参数；实现上由 Strategy 的静态入口
StrategyDefinition 承担。它根据策略结构完成：

1. 参数模式校验；
2. 默认值补全和单位归一化；
3. Binding、Sizing 与 Coordination 配置解析；
4. 向每个 Rule Slot 生成内部 `RuleConfig`；
5. 构造不可变的 TradingRuleSpec 和 StrategySpec。

Rule 可以收到与 Strategy 参数同名、同值的字段，也可以收到经过换算、拆分或派生后的
字段；即使是原样透传，Rule 也不是外部参数的第一接收者。Rule Definition 只声明自身
执行所需的内部 `RuleConfig` 契约。

参数值变化不形成新的 Rule Type。只有事件、状态与交易意图之间的转换机制发生变化时，
才新增 Rule Type。

Strategy 层和 Rule 层对同一信息使用不同抽象。设：

```text
P                  = Strategy Parameters
θᵢ = Cᵢ(P)         = Strategy 为第 i 条 Rule 解析出的 RuleConfig
Oₜ                 = 当前市场、账户、成交和 Strategy 协调事实
xᵢ,ₜ = Rᵢ(Oₜ)      = Strategy 路由给第 i 条 Rule 的具体 Rule Input

(sᵢ,ₜ₊₁, yᵢ,ₜ) = Tᵢ(sᵢ,ₜ, xᵢ,ₜ; θᵢ)
```

| Strategy 视角 | Rule 状态机视角 | 生命周期 |
| --- | --- | --- |
| 标的、方向、仓位方式、网格间距、阶梯档位等 Strategy Parameters | 解析后的控制变量 `θᵢ` / RuleConfig | StrategySpec 生命周期内固定 |
| 市场变化、指标信号、成交、仓位变化和协调事件 | 输入集合 `X_rule` 中的一个具体 `xᵢ,ₜ` | 随运行事件变化 |
| Rule 的历史执行结果 | 当前状态 `sᵢ,ₜ` | 跨转换保留 |
| Rule 提出的交易动作或领域结果 | 输出 `yᵢ,ₜ` | 每次转换产生 |

因此，Strategy 参数在上层表达交易意图、市场假设和配置选择；进入具体 Rule 后，它们只
表现为状态转换所需的固定控制变量 `θᵢ`。Strategy 识别的市场或交易信号进入 Rule 后，则
表现为输入集合中的具体变量 `xᵢ,ₜ`。Rule 不需要知道原始参数来自前端、实验还是实盘，
也不需要知道某个输入在 Strategy 层是如何计算或路由出来的。

---

## 3. 为什么以 Rule 作为底层构件

策略体系只需要 Rule 和 Strategy 两层领域语义：

- Rule 描述可独立复用的状态转换和交易意图生成机制；
- Strategy 接收策略参数，并描述 n 条 Rule（n ≥ 1）的配置映射、职责分配与协作关系。

对象层次如下：

```text
TradingRule
    策略的组成零件

StrategyDefinition
    策略参数契约与 RuleConfig 映射

StrategySpec
    参数已解析的 n 条 Rule 组合

StrategyInstance
    规则组合的一次运行
```

这也与现有 `grid_rule` 的历史语义一致：`GridRuleEngine` 本身就是一类具体的机械交易
规则，只是它当前同时保存了配置和运行状态。Grid 本轮仍只评估，不迁移。

代码中推荐使用 `TradingRule`，而不是过于宽泛的 `Rule`，避免与参数校验规则、风控规则
和交易所执行规则混淆。

前端分别使用“策略”和“交易规则”一级目录：前者展示 Rule 的可运行组合，后者展示
单条底层状态机，不再用实验 Component Descriptor 代替 StrategyDefinition。

---

## 4. 完整对象模型

### 4.1 TradingRuleDefinition

Rule 的静态定义，用于注册、文档和前端展示，至少包含：

- `rule_type` 与版本；
- 展示名称和说明；
- 内部 `RuleConfig` 契约；
- 接受的 Rule Input 类型集合与 Rule Output 契约；
- 状态和生命周期；
- 支持的方向与产品能力；
- 数学公式和约束；
- 历史兼容别名。

Definition 不保存具体参数和运行状态。

### 4.2 TradingRuleSpec

TradingRuleSpec 是 Strategy 完成参数解析后，为一条 Rule 生成的不可变内部配置：

```text
rule_type
config（resolved RuleConfig）
capability_references（optional）
```

TradingRuleSpec 不直接接收前端、实验文件或实盘 Server 的原始参数，也不包含市场场景或
随机 Seed。

### 4.3 Rule Slot

StrategySpec 通过稳定的 Rule Slot 引用 RuleSpec：

```text
rule_key
rule_spec
permissions
event subscriptions
```

`rule_key` 在一套 StrategySpec 内唯一，例如：

```text
entry
take-profit
dip-buy
grid-cycle
grid-follow
```

运行时的 `rule_instance_id` 由 `strategy_instance_id + rule_key` 生成，不依赖数组下标。

### 4.4 TradingRuleInstance

一条 Rule 在某个 StrategyInstance 中的运行对象，包含：

- Rule Instance ID；
- RuleSpec；
- Rule State；
- 当前意图；
- 已处理事件序号；
- 生命周期状态。

不同 Rule Instance 不直接修改对方状态。

### 4.5 StrategyDefinition

StrategyDefinition 描述一种可复用策略模板，至少包含：

```text
strategy_type
display_name
strategy parameter schema
rule slot blueprint
strategy-parameter → RuleConfig mapping
default sizing and coordination policies
```

它是外部策略参数的领域入口。StrategyDefinition 绑定一组 Strategy Parameters 后生成
StrategySpec。当前已经建立独立的 `StrategyDefinitionRegistry`，其目录与实验数据库无关。
首个登记类型为 `entry-then-ladder-exit/v1`，由 InitialEntryRule 与
LadderTakeProfitRule 组成；COIN-M、多头和 BTC 均只是兼容仿真实例的绑定参数。

### 4.6 StrategySpec

StrategySpec 定义一套规则如何组合，至少包含：

```text
strategy_spec_id
strategy_type
display_name
validated strategy parameters
instrument binding
rule slots × n（n ≥ 1）
sizing policies
coordination policy
default allocation reference
```

构造链路为：

```text
External Strategy Parameters
        ↓ StrategyDefinition.validate / resolve
Validated Strategy Parameters
        ├── Binding / Sizing / Coordination
        └── ResolvedRuleConfig₁ ... ResolvedRuleConfigₙ
                    ↓
            TradingRuleSpec₁ ... TradingRuleSpecₙ
                    ↓ compose
                StrategySpec
```

相同 StrategySpec 可以被重复实例化，也可以在不同市场环境中重复实验。

### 4.7 StrategyInstance

StrategyInstance 是资金、虚拟仓位、运行状态和绩效归因的最小策略单位，包含：

- `strategy_instance_id`；
- 一个不可变 StrategySpec；
- n 个 Rule Instance（n ≥ 1）；
- 一个默认 Position Owner；
- 一个 Capital Allocation；
- Strategy 生命周期；
- Strategy 级协调状态；
- Strategy 级指标事实。

同一 StrategyInstance 内的 Rule 默认共同管理该 Strategy 的虚拟仓位。

### 4.8 StrategyApplication

StrategyApplication 在同一账户上下文中运行一个或多个 StrategyInstance，负责：

- 创建、启动和停止 StrategyInstance；
- 广播市场和账户事件；
- 汇总各 Strategy 的交易提案；
- 处理跨 Strategy 的资金和方向冲突；
- 路由成交；
- 管理重复启动；
- 汇总账户级和 Strategy 级结果。

### 4.9 ExperimentRun

ExperimentRun 将 StrategyApplication 放入具体 Market、Account、Execution 和 Seed。它是
研究结果的最小运行记录，不反向改变 Strategy 或 Rule 的定义。

---

## 5. 分层关系

```text
TradingRuleRegistry
        │
        ▼
TradingRuleDefinition
        ▲
        │ references
Strategy Parameters ──► StrategyDefinition
                              │ validate / resolve
                              ▼
StrategySpec
├── RuleSlot × n（n ≥ 1）
│   └── TradingRuleSpec（ResolvedRuleConfig）
└── CoordinationPolicy
        │ instantiate
        ▼
StrategyInstance
├── RuleInstance: entry
├── RuleInstance: take-profit
├── Virtual Position
└── Capital Allocation
        │
        ▼
StrategyApplication
├── StrategyInstance A
├── StrategyInstance B
└── Cross-strategy coordination
        │
        ├── Simulation Adapter
        └── Live Adapter
```

### 5.1 依赖方向

```text
trading_strategies.rules
        ▲
        │
trading_strategies.strategy
        ▲
        │
strategy_application
        ▲
        │
strategy_simulation ──► simulation_runtime
```

禁止：

```text
TradingRule ──► StrategyApplication
TradingRule ──► simulation_runtime
StrategyInstance ──► simulation_runtime
market_simulator ──► concrete TradingRule
```

---

## 6. 当前代码位置

```text
strategies_system/src/
├── trading_strategies/
│   ├── kernel/
│   │   ├── definition.py
│   │   ├── events.py
│   │   ├── intents.py
│   │   ├── registry.py
│   │   ├── rule.py
│   │   ├── spec.py
│   │   └── transition.py
│   ├── rules/
│   │   ├── initial_entry.py
│   │   ├── ladder_take_profit.py
│   │   └── catalog.py
│   ├── btc_accumulation/
│   │   └── long_take_profit_ladder.py  # StrategyDefinition
│   └── strategy/
│       ├── definition.py
│       ├── spec.py
│       └── values.py
├── strategy_application/
│   ├── allocation.py
│   ├── accounting.py
│   ├── application.py
│   ├── rule_instance.py
│   ├── spec.py
│   ├── strategy_instance.py
│   └── coinm_long_take_profit_ladder.py  # v1 兼容门面
└── strategy_simulation/
    ├── adapters/
    ├── plugins/
    └── experiment_provider/
```

依赖方向严格为 `strategy_application → trading_strategies`；纯策略包不反向引用应用层。
两者均不依赖 simulator。未来 Live Adapter 可以直接复用相同 Rule、StrategySpec 和
StrategyInstance。

---

## 7. TradingRule 虚基类

### 7.1 接口

```python
class TradingRule(ABC, Generic[RuleConfigT, InputT, StateT, ContextT]):
    rule_type: ClassVar[str]

    @classmethod
    @abstractmethod
    def definition(cls) -> TradingRuleDefinition:
        ...

    @abstractmethod
    def initial_state(
        self,
        rule_config: RuleConfigT,
        context: ContextT,
    ) -> StateT:
        ...

    @abstractmethod
    def transition(
        self,
        rule_config: RuleConfigT,
        state: StateT,
        input: InputT,
        context: ContextT,
    ) -> RuleTransition[StateT]:
        ...
```

### 7.2 无运行状态

TradingRule 对象只保存无状态算法或纯计算依赖。当前仓位、已成交档位、挂单、Fill ID、
事件序号和 Instance ID 都属于 Rule Instance。

接口中的 `RuleConfigT` 是 Strategy 已解析的内部配置，不是外部 Strategy Parameters；
`InputT` 是具体 Rule 接受的输入类型集合，不要求所有 Rule 共用同一种聚合输入模型。

同一个 Rule 对象可以被多个 Rule Instance 共享：

```text
LadderTakeProfitRule
├── strategy-a:take-profit state
├── strategy-b:take-profit state
└── strategy-c:take-profit state
```

### 7.3 输入与输出

上游首批 TradingEvent 可以包括：

```text
START
MARKET_UPDATED
SIGNAL_RECEIVED
FILL_RECEIVED
POSITION_CHANGED
STOP_REQUESTED
```

这些 TradingEvent 是 StrategyApplication 可以观察和路由的领域事实，不等于每条 Rule
必须直接接收的 InputT。StrategyDefinition 和 StrategyInstance 根据 Rule Slot、订阅关系
与映射规则，将上游事实转换成该 Rule 输入集合中的具体成员 `xₜ`。

首批 Ladder 的具体输入集合为：

```text
InitialEntryRule
    InitialEntryStartInput | InitialEntryFillInput

LadderTakeProfitRule
    LadderPositionOpenedInput | LadderFillInput | LadderStopInput
```

每次 Rule 转换只接收其中一种具体输入。建仓 Fill 这一上游事件同时映射为 entry Rule 的
Fill Input 和 take-profit Rule 的 PositionOpened Input，两个转换在 StrategyInstance 中
原子提交。

RuleTransition 至少包含：

```text
next_state
intent proposals
intent cancellations
lifecycle request
domain observations
```

Rule 只能提出交易意图，不能直接修改账户账本、伪造成交、伪造强平、调用 Runner 或提交
交易所订单。

### 7.4 Rule Intent

RuleIntentProposal 至少表达：

- Rule 内部 Intent Key；
- 主动或被动；
- 开仓、增加、减少或关闭；
- 买卖方向；
- 明确数量及数量单位；
- 可选目标价格；
- `reduce_only`；
- Rule role 和标签。

StrategyInstance 补充：

```text
strategy_instance_id
rule_instance_id
allocation_id
position_owner_id
```

Simulation Adapter 最终转换为 `TradeInstruction`。

---

## 8. 什么应该成为 Rule

### 8.1 判定标准

一种机制同时满足以下大部分条件时，才建立独立 Rule：

- 有明确事件输入；
- 有自己的状态转换；
- 能产生、取消或调整交易意图；
- 能在多套 Strategy 中复用；
- 不需要读取另一个 Rule 的私有状态；
- 可以独立写出状态机和单元测试。

### 8.2 不属于 Rule 的对象

| 对象 | 应放位置 |
| --- | --- |
| 有效杠杆率定量 | Sizing Policy |
| 目标强平价反算 | Sizing Policy |
| 手续费、资金费 | Execution / Account Model |
| 保证金与强平 | Runtime Account Model |
| 完成后再次启动 | Lifecycle Policy |
| BTC/ETH、LONG/SHORT | Strategy Binding |
| 牛市、熊市、随机路径 | Market Environment |
| 最大账户仓位限制 | Application Constraint |

### 8.3 防止 Rule 数量爆炸

不是每个 `if`、公式或参数都建立一个 Rule。参数变化不新增 Rule Type；只有可复用的事件
状态转换机制发生变化才新增。

---

## 9. StrategySpec 的组合语义

外部调用者只提交 Strategy Parameters，不直接构造 Rule 的内部配置。StrategyDefinition
解析参数并为每个 Rule Slot 生成 TradingRuleSpec；StrategySpec 保存解析结果，运行期间
不再重新解释原始参数。

### 9.1 Rule Slot

建议结构：

```python
class StrategyRuleSlotSpec:
    rule_key: str
    rule: TradingRuleSpec
    permissions: tuple[RulePermission, ...]
    subscriptions: tuple[str, ...]  # 具体 Rule Input 类型名
```

### 9.2 Rule 权限

首批可以表达：

```text
OPEN
INCREASE
REDUCE
CLOSE
MANAGE_EXITS
```

读取仓位、市场和成交事实不设置独立 Permission，而由显式 Input 和只读 Context 决定。
权限属于 Strategy 内部协调，不代表交易所权限。同一 Strategy 内只有一个规则可被指定为
整个仓位的 `MANAGE_EXITS` 控制者。

### 9.3 Strategy Coordination Policy

首版精确结构为：

```json
{
  "coordination_policy": {
    "proposal_batch": "ATOMIC_PER_EVENT",
    "conflict_resolution": "FAIL_FAST",
    "exit_controller_rule_key": "take-profit"
  }
}
```

它负责处理同一 Strategy 内 n 条 Rule（n ≥ 1）的提案：

- 是否允许同一事件同时加仓和减仓；
- 多条 Rule 同时提出退出时如何处理；
- Position Changed 后哪些 Rule 需要重算；
- 资金和数量是否足够；
- 冲突时拒绝、排序还是合并。

首版使用事件级原子提案批次与 fail-fast，不合并冲突，也不按 Rule 注册顺序隐式决定
优先级。同一个批次不能同时增加和减少敞口。

### 9.4 Rule 之间的通信

Rule 不直接调用另一条 Rule。协作通过 StrategyInstance 发送公开事件：

```text
FILL_RECEIVED
POSITION_CHANGED
SIGNAL_RECEIVED
```

这样能够单独测试每条 Rule，也能替换其中一条实现。

---

## 10. 首批策略分解示例

### 10.1 阶梯止盈

目标 StrategySpec：

```text
Strategy：初始建仓并阶梯退出
├── RuleSlot entry
│   └── InitialEntryRule
├── RuleSlot take-profit
│   └── LadderTakeProfitRule
├── SizingPolicy
│   └── EFFECTIVE_LEVERAGE 或 TARGET_LIQUIDATION_PRICE
└── CoordinationPolicy
    └── 建仓 Fill 后激活阶梯退出
```

`LadderTakeProfitRule` 只负责根据真实仓位和成交价生成阶梯、处理各档 Fill 并判断退出
完成。BTC、COIN-M、LONG 和仓位公式都不写死在 Rule 中。

初始建仓已经采用独立 `InitialEntryRule`，因为它具有明确事件、意图和可复用边界。

### 10.2 马丁补仓与阶梯退出

```text
Strategy：下跌补仓并阶梯退出
├── MartingaleAddRule
└── LadderTakeProfitRule
```

两条 Rule 位于同一个 StrategyInstance，因此天然共享 Strategy 的 Position Owner。
Martingale Fill 触发 Position Changed，Ladder 决定增补还是重建退出计划。

这比把两条机制建成两个独立 Strategy 后再共享 Position Pool 更简单，也更符合“它们共同
构成一套策略”的语义。

### 10.3 RSI

```text
Strategy：RSI 入场并退出
├── RsiSignalRule
└── ExitRule
```

RSI Rule 只接收已完成 K 线或指标事实并产生信号/入场意图。下一根 Open 等执行时序由
Rule Intent 与 Adapter 共同表达。

### 10.4 网格

```text
Strategy：单组跟随网格
├── GridCycleRule
└── GridFollowRule

Strategy：分层跟随网格
├── GridCycleRule × N
└── LayerDeploymentRule
```

现有 `grid_rule` 可以作为 `GridCycleRule` 的具体实现或兼容对象。Ladder 完成新模型验证
前，不修改 GridRuleEngine。

---

## 11. StrategyInstance

### 11.1 运行身份

StrategyInstance 是运行时对象：

```python
strategy = StrategyInstance(
    strategy_instance_id="btc-ladder-01",
    spec=strategy_spec,
    rule_instances=rule_instances,
)
```

同一个 StrategySpec 可以创建多个 Instance：

```text
btc-ladder-01
btc-ladder-02
btc-ladder-03
```

它们共享规则定义，但状态、仓位、资金和绩效互相独立。

### 11.2 生命周期

5V2-C 当前同步运行链为：

```text
CREATED → ACTIVE → COMPLETED
ACTIVE → STOPPED
CREATED / ACTIVE → FAILED
```

异步启动和停止所需的 `STARTING / STOPPING` 留给实盘 Lifecycle Adapter，不在
当前纯同步应用层中制造无实际过程的中间状态。

完成后再次建仓由 Lifecycle Policy 创建新的 StrategyInstance，不清空旧 Instance 状态后
复用同一个 ID。

### 11.3 仓位所有权

默认一个 StrategyInstance 对应一个 `position_owner_id`。内部 n 条 Rule（n ≥ 1）共同操作这个
Strategy 虚拟仓位，并受 Rule Permission 和 Coordination Policy 约束。

### 11.4 绩效归因

StrategyInstance 是实例级收益、手续费、资金费、成交数、持仓和风险贡献的归因单位。
Rule 级记录用于解释行为，不将每条 Rule 伪装成独立账户收益。

---

## 12. 同策略多实例与多策略

### 12.1 同策略多实例

```text
StrategyApplication
├── StrategyInstance ladder-01 → StrategySpec A
├── StrategyInstance ladder-02 → StrategySpec A
└── StrategyInstance ladder-03 → StrategySpec A
```

每个 Instance 有独立 Position Owner 和 Allocation。交易所可能只显示一个产品净仓位，
系统内部仍保留三份虚拟归属。

### 12.2 多策略

```text
StrategyApplication
├── StrategyInstance accumulation-01 → StrategySpec A
├── StrategyInstance grid-01         → StrategySpec B
└── StrategyInstance hedge-01        → StrategySpec C
```

Application 不理解各 Rule 的私有状态，只处理 Strategy 级意图、资金、方向、Owner 和
生命周期。

### 12.3 区分规则组合和策略组合

判断标准：

- n 个机制（n ≥ 1）共同管理同一策略仓位和目标：组成一个 StrategySpec 的 n 条 Rule；
- 多套策略拥有独立资金、仓位和绩效：组成 Application 的多个 StrategyInstance。

例如“马丁加仓 + 阶梯退出”通常是一套 Strategy 的两条 Rule；“BTC 长期持有 + 合约网格”
通常是两个 StrategyInstance。

---

## 13. StrategyApplication

### 13.1 Application Spec

```python
class StrategyLaunchSpec:
    strategy_instance_id: str
    strategy_spec: StrategySpec
    capital_allocation: CapitalAllocationSpec | None

class StrategyApplicationSpec:
    application_id: str
    strategy_launches: tuple[StrategyLaunchSpec, ...]
    coordination_policy: ApplicationCoordinationPolicy
```

5V2-D 允许各 Launch 绑定不同 `StrategySpec`，但每个 Launch 仍必须能解析出明确的
结算资产额度。首版 Application Coordination Policy 精确为：

```json
{
  "proposal_batch": "ATOMIC_PER_EVENT",
  "conflict_resolution": "FAIL_FAST",
  "reject_opposing_open_intents": true
}
```

该策略只解决可在账本前确定的跨策略冲突。Lifecycle Policy 和资金额度释放策略仍在
后续批次引入。

最小单策略也包装成 Application：

```text
StrategyApplication
└── one StrategyInstance
```

### 13.2 Application State

5V2-C/5V2-D/5V2-E 已包含：

- 活跃、完成和失败的 StrategyInstance；
- Allocation Book；
- Intent Owner Index；
- Lifecycle State；
- Application Event Sequence；
- 按 StrategyInstance 隔离的事件、Intent 和 Fill 活动计数。
- 多 StrategySpec 原子事件批次和应用级冲突策略。
- Virtual Position Book；
- Owner 级仓位与减仓约束；
- Strategy 金融绩效归因与账户对账状态。

### 13.3 默认失败策略

首版 fail-fast：未知 Fill、重复全局 Intent Key、越权减仓、无法解决的资金冲突或 Rule
异常使当前 Run 明确失败。不得静默跳过一个策略后继续计算整体收益。

强平仍由账户模型判定，并终止整个账户 Run。

---

## 14. 确定性事件处理

### 14.1 Strategy 内两阶段处理

```text
阶段 1：Rule 提案
    同一 Strategy 的所有订阅 Rule 读取同一只读 Context

阶段 2：Strategy 批准
    Coordination Policy 检查权限、数量和冲突
```

一条 Rule 不能先修改仓位，再让另一条 Rule 在同一事件中读取修改后的状态。

### 14.2 Application 两阶段处理

5V2-D 已实现该过程：调用者显式提供本次事件的 `inputs_by_strategy`，Application
按 Strategy Instance ID 排序后先完成所有预演，然后统一批准和提交。应用层不猜测
哪个 Strategy 订阅了上游市场事件；事件到 Rule Input 的映射仍由 Adapter 负责。

```text
阶段 1：Strategy 提案
    所有 ACTIVE StrategyInstance 读取同一账户快照

阶段 2：Application 批准
    检查全局 Intent 唯一性、Strategy 身份和单向账户的反向建仓冲突
```

当前可允许不同产品或同产品同向策略并行；同一产品同时出现 LONG 和 SHORT 的
OPEN/INCREASE 意图时 fail-fast。Owner 减仓由 Virtual Position Book 校验；真实账户
可用资金、保证金和强平冲突仍由 Runtime Account Model 判定。

### 14.3 稳定排序

稳定排序键建议为：

```text
event_sequence
strategy_instance_id
rule_instance_id
local_intent_sequence
```

业务结果不得依赖字典、Plugin 注册或数据库返回顺序。

### 14.4 Fill 路由

```text
global_intent_key
    → strategy_instance_id
    → rule_instance_id
    → local_intent_key
```

Fill 先更新真实账本与 Strategy 虚拟仓位，再路由给产生 Intent 的 Rule。未知 Intent Fill
必须失败，不广播给所有 Rule 猜测归属。

---

## 15. 资金与虚拟仓位

### 15.1 Capital Allocation

Allocation 是 StrategyInstance 的研究期初资本和归因基准，不复制成多个真实账户。
5V2-C 固定表达：

- Allocation ID；
- Strategy Instance ID；
- 结算资产；
- 正的固定额度。

当前 Allocation 作为 Strategy 绩效归因的期初资本，用于计算 attributed equity。它不是
保证金子账户，也不用于单独判定强平。最大初始保证金、最大新增名义敞口和
额度释放策略继续后置，需要具体资金策略需求后再引入。

### 15.2 全仓账户

5V2-E 不使用 Application Allocation 拒绝交易提案。真实可用资金、保证金、权益和强平
继续由 Runtime Account Model 按整个账户计算；虚拟额度不得替代交易平台的全仓计算。
如果未来需要实例级资金上限，应新增显式 Application Constraint，而不是把归因额度
隐式解释成保证金子账户。

### 15.3 Virtual Position Book

交易所可能只提供：

```text
BTCUSD_PERP LONG = 1,000 contracts
```

Application 内部保留：

```text
accumulation-01  400 contracts
grid-01          300 contracts
ladder-02        300 contracts
```

Strategy 内的 Rule 共用其 Strategy Owner；不同 Strategy 默认使用不同 Owner。

5V2-E 已实现 Owner + Instrument 维度的 Virtual Position，保留产品类型、结算资产、
原生数量单位、合约面值、签名数量和平均建仓价。

### 15.4 Owner 级 reduce-only

```text
requested_reduce_quantity
    <= strategy_owner_virtual_position_quantity
```

越界时首版直接失败，不自动裁剪。Runtime 的账户级 `reduce_only` 仍是第二道防御。

Application 在成交预演时先计算成交后 Owner 仓位，再检查本次新建与已存的所有
减仓意图总量。任一一侧失败，Strategy State 和 Virtual Position 均不提交。

### 15.5 对账

无外部交易的仿真中：

```text
sum(strategy virtual positions by product and side)
    = actual account position
```

未来实盘的无法归属仓位记入 `UNATTRIBUTED_POSITION`。

5V2-E 已实现 `AttributionReconciliation`，显式返回：

- 各产品的仓位残差；
- 账户初始权益与 Allocation 合计残差；
- 已实现盈亏、手续费、资金费和未实现盈亏残差。

任一残差非零时 `balanced=false`，不会为了账面平衡而修改 Runtime 或 Strategy 数值。

---

## 16. 跨 Strategy 共享仓位

大部分共同管理一个仓位的机制应组合成同一 Strategy 的 n 条 Rule（n ≥ 1），因此跨 Strategy 共享
仓位不再是基础路径。

只有两套独立 StrategyInstance 必须共享一个业务仓位时，才使用显式 Position Pool。

Position Pool 至少需要：

```text
READ
OPEN
INCREASE
REDUCE
CLOSE
MANAGE_EXITS
```

一个 Pool 同时最多只有一个 `MANAGE_EXITS` 控制者。共享池实现继续后置，不属于 v2
基础验收。

---

## 17. 绩效归因

### 17.1 权威结果

账户收益、保证金、强平和权益继续以 Simulation Runtime 账本为权威。

### 17.2 Strategy 级归因

- 手续费：归属产生 Fill 的 Strategy；
- 已实现盈亏：按 Strategy Owner 开平仓 Fill 计算；
- 未实现盈亏：按 Strategy Owner 数量和均价计算；
- 交易次数：按 Strategy 的 Intent/Fill 统计；
- 资金费：按结算时各 Strategy 同方向名义敞口比例分摊；
- 强平：记录为 Application/Account 级事件；
- 归因残差：单独记录。

5V2-E 公式边界：

```text
Linear / Spot:
realized = direction × closed_quantity × contract_size
           × (exit_price - average_entry_price)

Inverse COIN-M:
realized = direction × closed_contracts × contract_size
           × (1 / average_entry_price - 1 / exit_price)

net_realized = gross_realized - fees
net_after_funding = net_realized + funding
attributed_equity = allocated_capital + net_after_funding + unrealized
```

资金费只在账户 Funding Settlement 产生后归因，按该产品、同方向 Owner 在结算价的
绝对名义敞口比例分摊，最后一个 Owner 承担 Decimal 除法的尾差，保证分摊合计精确
等于 Runtime `wallet_delta`。

### 17.3 Rule 级事实

Rule 级记录用于回答：

- 哪条 Rule 发起交易；
- 哪条 Rule 增加或减少仓位；
- 哪个档位或信号触发；
- Rule 的状态和完成进度。

Rule 不单独计算一份账户权益，避免重复计算共享仓位收益。

---

## 18. 仿真与实盘适配

### 18.1 仿真调用链

```text
SimulationRunner
        ↓ SimulationTradePort
StrategyApplicationSimulationAdapter
        ↓
StrategyApplication
        ↓
StrategyInstance(s)
        ↓
TradingRuleInstance(s)
```

SimulationRunner 仍是仿真发起者，不识别具体 Rule 或 Strategy。

### 18.2 Simulation Adapter

负责：

- 将 MarketFrame 转为 TradingEvent；
- 将批准 Intent 转为 TradeInstruction；
- 处理主动/被动执行时序；
- 将 SimFill 路由回 Application；
- 暴露 Intent Snapshot；
- 写入 Strategy 和 Rule 身份标签。

### 18.3 Live Adapter

未来负责订单提交、部分成交、client order ID、账户对账、状态恢复、外部交易识别和网络
幂等。Rule 与 StrategyApplication 都不导入交易所 SDK。

---

## 19. 实验系统

### 19.1 环境边界

StrategySpec 不保存 Market Path、Scenario 或 Seed。实验系统在 Run 中组合：

```text
StrategyApplicationSpec
Market Component
Account Component
Execution Component
Seed
```

### 19.2 单策略兼容

当前单 Strategy Component 自动包装成一个 StrategyApplication，不要求历史实验一次性
改写。

### 19.3 目标配置

```json
{
  "application_id": "btc-research-app",
  "strategies": [
    {
      "strategy_key": "ladder-main",
      "strategy_type": "initial-entry-ladder-exit/v1",
      "parameters": {
        "instrument": "BTCUSD_PERP",
        "direction": "LONG",
        "product_type": "INVERSE_PERPETUAL",
        "position_sizing": {
          "mode": "EFFECTIVE_LEVERAGE",
          "effective_leverage": 0.6
        },
        "take_profit": {
          "level_count": 10,
          "start_price_ratio": 1.2,
          "end_price_ratio": 2.5
        }
      }
    }
  ]
}
```

### 19.4 参数轴

参数轴通过稳定 Strategy Key 和 Strategy Parameter 路径定位：

```text
/strategy/parameters/strategies/ladder-main/parameters/take_profit/level_count
/strategy/parameters/strategies/ladder-main/parameters/position_sizing/effective_leverage
```

不得使用数组下标作为永久身份。Rule Key 仍用于运行身份、事件路由和研究焦点，但不是
外部参数的所有权边界。

### 19.5 结果身份

新结果逐步增加：

```text
application_id
strategy_instance_id
strategy_spec_id
rule_instance_id
rule_type
allocation_id
position_owner_id
```

历史 `strategy_type` 保留读取，不重写数据库。

---

## 20. 前端信息模型

### 20.1 策略库

导航改为：

```text
策略
├── 策略总览
└── 策略详情

交易规则
├── 规则总览
└── 规则详情
```

策略总览每行对应一个 `StrategyDefinition`；策略详情展示 Strategy 外部参数、Rule Slot
组成、职责、权限、订阅、参数映射、协调策略、生命周期与已有运行实例。即使清空全部实验
数据库，已经注册的策略定义仍然可见。

### 20.2 规则库

每行对应一个 TradingRuleDefinition，例如：

- 初始建仓；
- 阶梯止盈；
- 网格循环；
- RSI 信号；
- 马丁补仓。

标的、方向、产品和参数变化不产生新的规则目录项。

### 20.3 规则详情

展示 Rule 描述、状态转换、公式、内部 RuleConfig 契约、事件订阅、意图类型、支持能力
和边界，不展示 Python 源码。用户实际填写的仍是 Strategy Parameters。

### 20.4 参数研究主视角

每个参数研究声明：

```json
{
  "research_focus": {
    "primary_strategy_key": "ladder-main",
    "primary_rule_key": "take-profit"
  }
}
```

大标签页根据该 Rule Slot 的 `rule_type` 划分：

```text
参数研究
├── 阶梯止盈
├── 网格循环
├── RSI 信号
└── 马丁补仓
```

“核心规则”只是研究展示维度，不代表执行优先级、资金优先级或成交顺序。

### 20.5 表格

| 研究/配置 | 标的 | 产品/方向 | 核心规则相关策略参数 | 市场环境 | 配合策略 | Seeds / Runs | 关键结果 | 操作 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |

一行表示：

```text
一套解析后的 StrategyApplication 配置
    × 一个市场环境
    × 一组聚合 Seed Runs
```

Seed 不单独占行。完整账户、执行、Strategy Parameters、解析后的 RuleConfig 和各
Instance 明细放入展开区域。

### 20.6 配合策略

“配合策略”指 Application 中除主 StrategyInstance 外的其他 StrategyInstance：

```text
单策略
    配合策略：—

同 StrategySpec 多实例
    配合策略：同名策略 × 2

多策略
    配合策略：网格策略 × 1、RSI 策略 × 1
```

主 Strategy 内部的其他 Rule 不列入“配合策略”，而是在展开区域显示为“规则组成”：

```text
规则组成：InitialEntryRule + LadderTakeProfitRule
```

这样“规则组合”和“策略组合”不会在前端混为一谈，同时顶层显示对象数量只随核心
Rule Type 增长。

### 20.7 数据来源

策略目录直接来自 StrategyDefinitionRegistry，规则目录直接来自 TradingRuleRegistry，
二者都不依赖实验数据库。清空实验结果后，已注册策略和规则仍然存在；参数研究表只显示
实际存在的 Study/Application 结果。

---

## 21. 注册表

### 21.1 TradingRuleRegistry

```text
TradingRuleRegistry
    rule_type → TradingRule
```

要求：

- Rule Type 全局唯一；
- Definition Type 与 Rule Type 一致；
- 别名不得冲突；
- 未注册 Rule 明确失败；
- 行为语义变化必须升级版本；
- 实验 JSON 不允许导入任意 Python 类。

### 21.2 StrategyDefinitionRegistry

```text
StrategyDefinitionRegistry
    strategy_type → StrategyDefinition
```

Registry 校验 Strategy Type、Descriptor Type 与别名唯一性，并独立发布
`kind = strategy-definition` 的只读目录。具体 StrategyDefinition 显式接收 Strategy
Parameters，完成 Binding、Sizing 引用、RuleConfig 和 Coordination Policy 解析后构造
StrategySpec。

### 21.3 SimulationStrategyRegistry

现有 Simulation Registry 暂时保留，负责兼容实验 Component、注入仿真产品能力并交付
SimulationTradePort。规则名称、公式和内部 RuleConfig 说明以 TradingRuleDefinition 为
权威；用户可配置的 Strategy Parameters 及其映射以 StrategyDefinition 为权威。

---

## 22. 兼容与迁移

### 22.1 5V2-A Rule 领域契约

5V2-A 已实现并导出：

```text
TradingRule
TradingRuleDefinition
TradingRuleRegistry
TradingRuleSpec
TradingEvent
RuleIntentProposal
RuleTransition
```

同时实现 RuleConfigFieldDefinition、TradingEvent 具体事件、Rule Intent 枚举和
RuleLifecycleRequest。旧原型在迁移前尚未被具体策略使用，因此没有设置过渡别名，也没有
保留重复的公共概念。

### 22.2 Ladder

Ladder 是第一套迁移策略。历史仿真类型继续作为兼容入口：

```text
coinm-long-take-profit-ladder/v1
effective-leverage-ladder-long/v1
target-liquidation-ladder-long/v1
```

兼容 Plugin 已将历史参数交给纯策略层的
`CoinMLongTakeProfitLadderStrategyDefinition`，后者转换成产品中立的
`EntryThenLadderExitParameters`，再由 `EntryThenLadderExitStrategyDefinition` 解析为
StrategySpec、两个 TradingRuleSpec、Binding 和 Sizing Context。兼容门面位于
`strategy_application`，继续提供原有状态、EntryPlan、TakeProfitLevel 和 Summary 接口，
历史数据库不重写。

仿真成交标签现已包含：

```text
strategy_instance_id
strategy_spec_id
core_strategy_type
rule_key
rule_type
rule_instance_id
position_owner_id
```

账户收益、保证金和强平仍由 simulation_runtime 计算。

### 22.3 Grid

5V2-B 没有修改 `grid_rule`、GridRuleEngine、固定/单组/分层网格及其 Adapter。Ladder
单实例验收后再单独评估 Grid 的 Rule 接入方式。

### 22.4 Runtime

SimulationRunner 不增加具体 Rule 或 Strategy 分支。所有新结构仍通过
SimulationTradePort 接入。

---

## 23. 开发计划

### 23.1 5V2-A：Rule 领域契约修订

当前状态：已完成并通过全量回归。

- 建立 `TradingRule`、`TradingRuleDefinition` 和 `TradingRuleRegistry`；
- 建立只保存一条 Rule 及其已解析 RuleConfig 的 `TradingRuleSpec`；
- 固定 TradingEvent、RuleIntentProposal 和 RuleTransition；
- 固定 TradingRuleRegistry；
- 更新公共导出和架构测试；
- 不迁移 Ladder 和 Grid。

### 23.2 5V2-B：StrategySpec 与 Ladder 单实例

当前状态：已完成并通过策略核心、仿真兼容和全量回归测试。

- 新增 StrategyDefinition、Strategy Parameters 与 RuleConfig 解析；
- 新增 n 个 RuleSlot（n ≥ 1）、StrategySpec 和 Coordination Policy；
- 新增 TradingRuleInstance 与 StrategyInstance；
- 实现 InitialEntryRule；
- 实现 LadderTakeProfitRule；
- 复用现有 Sizing Port；
- 使用兼容门面保持 Fill、仓位、强平和 Summary 结果一致。

### 23.3 5V2-C：同策略多实例

当前状态：已完成并通过同策略多实例、定向 Fill 路由和失败隔离测试。

- 实现最小 StrategyApplication；
- 同一 StrategySpec 创建多个 StrategyInstance；
- 实现 Strategy/Rule 全局 Intent Key 和 Fill 路由；
- 使用独立 Allocation 和 Position Owner；
- 验证 Rule State、Intent、Event 和 Fill 活动计数互不污染；
- 5V2-C 验收时拒绝重复 Instance ID 和缺失 Allocation，并暂时拒绝混合 StrategySpec；
- 未知 Fill 或 Rule 失败时 fail-fast，Application 和所有未结束 Instance 统一进入
  `FAILED`；
- 当前只保存活动计数，不伪造尚未实现的策略级盈亏与保证金归因。

### 23.4 5V2-D：多策略独立并行

当前状态：已完成并通过多 StrategySpec、原子批次、稳定顺序和默认冲突拒绝测试。

- Application 装载不同 StrategySpec；
- 实现 Strategy 级两阶段提案和批准；
- 实现稳定顺序和默认冲突拒绝；
- 任一 Strategy 预演或 Application 批准失败时，所有 Strategy 均不提交本次事件；
- 单个 `dispatch` 复用同一批次管道，Fill 继续使用 Intent Owner Index 定向路由；
- 不实现跨 Strategy 共享仓位和财务归因。

### 23.5 5V2-E：资金、虚拟仓位和归因

当前状态：已完成领域实现和 Application 接入，并通过 Linear/Inverse 公式、Owner
减仓、资金费分摊和账户残差对账测试。

- 固定 Capital Allocation；
- 实现 Virtual Position Book；
- 校验 Owner 级 reduce-only；
- 实现手续费、盈亏、资金费归因；
- 建立账户结果与 Strategy 贡献对账。

5V2-F 已通过统一的 `StrategyApplicationSimulationAdapter` 自动构造
`StrategyAccountingFill` 并转发 Funding Settlement。未迁移的旧 Adapter 继续保持原有
兼容行为，历史运行结果不会被重算。

### 23.6 5V2-F：实验与前端

当前状态：已完成。

- 新增 Application/Strategy/Rule 结果身份；
- 保留单 Strategy Component 自动包装；
- 前端建立相互独立的策略总览/详情与交易规则总览/详情；
- 参数研究按核心 Rule Type 分标签；
- “配合策略”按 StrategyInstance 汇总；
- 主 Strategy 内 Rule 在展开区域显示；
- 保持历史数据库可读。

落地说明：

- `StrategyApplicationSimulationAdapter` 是通用仿真桥，Runner 只向其交付 MarketFrame、
  Fill 与 Funding 等框架事实；具体 Rule Input 由策略专属 Event Router 构造；
- 单 Strategy Component 被包装为一条 `StrategyLaunchSpec` 的 Application，首个迁移对象为
  `coinm-long-take-profit-ladder/v1`；
- Runtime 新增可选 `SimulationAccountingPort`，用于按 Fill 后 Funding 的顺序交付权威账户
  事实，同时不改变旧 Adapter 的 `on_fills` 兼容语义；
- Run 结果保留 `application_id / strategy_instance_id / strategy_spec_id /
  rule_instance_id / rule_type / allocation_id / position_owner_id`，并保存 Application
  组成、Strategy 归因和账户残差对账；
- `/api/components` 同时发布仿真 Component Descriptor、独立
  `StrategyDefinitionDescriptor` 与 `TradingRuleDefinition`；策略和规则目录在没有任何
  实验数据库时仍可展示；
- `StrategyDefinitionRegistry` 当前注册产品中立的“建仓后阶梯退出”，页面展示它的两条
  Rule、参数到 RuleConfig 的映射、协调策略和已有运行实例；
- 前端“交易规则”展示 Rule 的说明、状态机、公式、RuleConfig、输入输出和能力边界；参数
  研究按核心 Rule Type 分组，并在 Study 内展示规则组成和配合 Strategy；
- 历史数据库继续通过既有 Strategy Type 分组读取，无需迁移或重写。

### 23.7 5V2-G/H：跨 Strategy Position Pool

- 先形成详细共享仓位规格和冲突用例；
- 基础策略组合优先放入同一 Strategy 的多 Rule；
- 只有确有需求后再实现跨 Strategy 共享池。

---

## 24. 测试计划

### 24.1 TradingRule

- Rule 无运行状态；
- 相同 RuleConfig、State、Input 和 Context 产生相同 Transition；
- Definition 与 Rule Type 一致；
- 每个具体 Rule 显式声明其 Input 类型集合；
- 未声明的 Input 不能静默进入 Rule；
- TradingRuleSpec 中已解析的 RuleConfig 不可变；
- 重复 Fill 幂等；
- Rule 不导入 simulator、Web 或交易所 SDK。

### 24.2 StrategySpec 与 StrategyInstance

- Strategy Parameters 是外部配置的第一接收边界；
- StrategyDefinition 能确定性地将 Strategy Parameters 解析为 RuleConfig；
- Rule 不直接接收原始前端、实验或实盘配置；
- 一个 StrategySpec 可以组合 n 条 Rule（n ≥ 1）；
- Rule Key 唯一；
- Rule State 独立；
- Rule Proposal 经过 Strategy Coordination；
- 同一 Strategy 的 Rule 共享 Strategy Position Owner；
- Strategy Snapshot 可序列化。

### 24.3 多实例

- 同 StrategySpec 创建多个独立 StrategyInstance；
- Fill 精确路由到 Strategy 和 Rule；
- 改变注册顺序不改变结果；
- 一个 Strategy 不能减少另一个 Strategy 的仓位；
- 强平终止整个 Run。

5V2-C 已覆盖前三项，并验证了 Rule State、Intent、Event 和 Fill 活动计数隔离；5V2-E
已经补充 Owner 数量减仓约束。账户强平仍由 Runtime 统一触发并终止整个 Run。

### 24.4 前端

- 一种 StrategyDefinition 只显示一条策略记录；
- 策略定义在无实验数据库时仍可展示；
- 一种 TradingRule 只显示一条规则记录；
- 标的、方向和参数不生成新规则类型；
- 参数研究标签数量不随 Seed 和 Instance 数量增长；
- 同策略多实例显示为“同名策略 × 数量”；
- 多策略按 Strategy 名称汇总；
- 主策略内部 Rule 只进入“规则组成”；
- 历史 Strategy Type 能定位到对应规则和策略说明。

---

## 25. 基础验收标准

1. `TradingRule` 是薄、纯、无运行状态的 ABC；
2. TradingRuleSpec 只保存一条 Rule 及其已解析的内部 RuleConfig；
3. StrategyDefinition 首先接收 Strategy Parameters，并能构造含 n 个 RuleSlot（n ≥ 1）
   的 StrategySpec；
4. TradingRule 的状态转换符合 `(sₜ₊₁, yₜ) = T_rule(sₜ, xₜ; θ)`；
5. 具体 Rule 显式声明不可变联合输入类型，每次转换只接收一个具体输入；
6. Ladder 由可解释的 Rule 组合构成并完成单实例仿真；
7. 产品、方向和 Sizing 不写死在 LadderTakeProfitRule 中；
8. 同一 StrategySpec 可以创建多个独立 StrategyInstance；
9. 一个 Application 可以运行不同 StrategyInstance；
10. 市场事件处理不依赖注册顺序；
11. Fill 可以路由到唯一 Strategy 和 Rule；
12. Owner 级减仓不超过本 Strategy 虚拟仓位；
13. 账户收益、保证金和强平仍由 Runtime 权威计算；
14. Strategy 绩效归因能与账户结果对账；
15. 历史实验和数据库无需重写；
16. 前端分别显示 StrategyDefinition 与 TradingRuleDefinition，不把参数组合当成新类型；
17. 参数研究按核心 Rule Type 分组；
18. 配合策略不产生新的顶层标签；
19. Grid 在 Ladder 首轮迁移中不被修改；
20. market_simulator 不导入具体 TradingRule。

跨 Strategy Position Pool 不属于基础验收条件。

---

## 26. 已确认决定

1. 底层行为抽象统一使用 `TradingRule`；
2. Strategy 是 n 条 Rule（n ≥ 1）的可运行组合；
3. 外部交易行为参数统一归属于 Strategy，并由其静态入口 StrategyDefinition 接收和校验；
4. TradingRuleSpec 只保存 Strategy 解析后分配的内部 RuleConfig；
5. TradingRule 统一采用 `(sₜ₊₁, yₜ) = T_rule(sₜ, xₜ; θ)` 表达状态转换；
6. 每种 Rule 拥有自己的输入类型集合，当前不提前冻结统一聚合输入结构；
7. StrategyInstance 是 StrategySpec 的一次运行；
8. StrategyInstance 是资金、仓位和绩效的归因单位；
9. 同一 Strategy 内的 Rule 默认共享 Strategy 仓位；
10. 多个 StrategyInstance 由 StrategyApplication 管理；
11. 市场环境只属于 ExperimentRun，不属于 Strategy 定义；
12. 参数变化不产生新的 Rule Type；
13. 完成后重新运行由 Lifecycle Policy 创建新 StrategyInstance；
14. 马丁补仓与阶梯退出可以作为同一 Strategy 的不同 Rule；
15. Ladder 作为第一套迁移策略；
16. Grid 只评估，不在首轮迁移；
17. 前端使用独立的“策略/策略总览/策略详情”和“交易规则/规则总览/规则详情”；
18. 参数研究按核心 Rule Type 建立大标签页；
19. “配合策略”表示 Application 中的其他 StrategyInstance；
20. 主 Strategy 内其他 Rule 显示为“规则组成”，不算配合策略；
21. 仿真发起者继续是 SimulationRunner。

---

## 27. 5V2-B 已确认决定

1. 初始建仓采用独立 `InitialEntryRule`；
2. Coordination Policy 首版固定为 `ATOMIC_PER_EVENT + FAIL_FAST +` 单一
   `exit_controller_rule_key`；
3. Rule Intent 只接受产品原生的明确数量和显式 `quantity_unit`，不接受目标仓位；
   COIN-M 使用 `contracts` 合约张数；
4. Rule Permission 最小集合为 `OPEN / INCREASE / REDUCE / CLOSE / MANAGE_EXITS`，
   读取能力由 Input 和只读 Context 提供；
5. Direction 最小枚举为 `LONG / SHORT`，Product 最小枚举为
   `SPOT / LINEAR_PERPETUAL / INVERSE_PERPETUAL`；
6. Capital Allocation 使用结算资产及其金额表达；
7. 单向账户遇到与 Strategy Binding 相反的 OPEN/INCREASE Intent 时 fail-fast；合法的
   REDUCE/CLOSE 使用相反买卖方向；
8. Strategy 级资金费在后续归因批次按结算时绝对名义敞口比例分摊；
9. TradingRuleDefinition 由独立 TradingRuleRegistry 展示，不依赖实验数据库；当前内置
   Registry 已注册 InitialEntryRule 和 LadderTakeProfitRule；
10. StrategyDefinition 由独立 StrategyDefinitionRegistry 展示，不依赖实验数据库；
    当前内置 Registry 已注册 `entry-then-ladder-exit/v1`；
11. 跨 Strategy Position Pool 继续采用单一 `MANAGE_EXITS` 控制者；基础批次仍不实现
    跨 Strategy 共享仓位；
12. Rule InputT 使用不可变 dataclass 的联合类型。单条 Rule 每次只消费一个具体输入；
    同一个上游事件可映射给多条 Rule，并由 StrategyInstance 原子预演和提交，不使用通用
    `dict`、组合输入或隐式注册顺序。

---

## 28. 5V2-C 已确认实现边界

1. 一个 5V2-C `StrategyApplicationSpec` 只装载同一 `StrategySpec` 的 n 个
   `StrategyLaunchSpec`（n ≥ 1）；
2. 每个 Launch 可覆盖 StrategySpec 默认额度，但最终必须解析出结算资产和正的
   固定额度；
3. Allocation ID 和 Position Owner ID 按 Strategy Instance 派生，并在 Application 内
   唯一；
4. Launch 输入顺序不影响运行顺序，Application 按 `strategy_instance_id` 稳定排序；
5. 非 Fill 输入必须定向发送给一个 StrategyInstance；市场事件广播和跨 Strategy
   两阶段批准属于 5V2-D；
6. Fill 通过全局 Intent Owner Index 精确路由，未知 Intent 立即失败，不广播猜测
   归属；
7. 未知 Fill、重复全局 Intent 或 Rule 异常使 Application 和所有未结束 Instance
   fail-fast 进入 `FAILED`；
8. 5V2-C 记录实例级事件、Intent、Fill 和活跃 Intent 计数，不把这些活动统计
   冒充成财务绩效；
9. Virtual Position Book、Owner 级 reduce-only、手续费/盈亏/资金费归因和账户
   对账属于 5V2-E；
10. 5V2-C 不同 StrategySpec 组合的限制已在 5V2-D 解除；单策略 Plugin 自动包装和
    前端展示属于 5V2-F。

---

## 29. 5V2-D 已确认实现边界

1. `StrategyApplicationSpec` 允许 n 个 Launch 分别绑定不同 `StrategySpec`（n ≥ 1）；
2. Application 事件批次由显式 `strategy_instance_id → Rule Inputs` 映射组成，Adapter
   决定本次事件应送达哪些 Strategy；
3. 所有目标 Strategy 先基于各自当前状态预演，Application 在任何状态提交前完成
   全局批准；
4. 任一 Rule/Strategy 预演失败或 Application 冲突校验失败时，所有 Strategy
   都不提交本次转换，Application fail-fast；
5. 预演和提交顺序均按 `strategy_instance_id` 稳定排序，不受 Launch 和调用者
   字典顺序影响；
6. 首版 Application Coordination Policy 为 `ATOMIC_PER_EVENT + FAIL_FAST +`
   `reject_opposing_open_intents=true`；
7. 全局批准检查 Intent Key 唯一性、Strategy 身份，以及同产品相反方向的
   OPEN/INCREASE 冲突；
8. 不同产品、或同产品同方向的多策略可以在同一批次中并行；
9. Fill 仍严格定向路由，不进入市场事件广播批次；
10. Allocation 只完成身份和固定结算资产额度隔离；总资金约束、Virtual Position
    Book、Owner 级 reduce-only 和财务归因属于 5V2-E。

---

## 30. 5V2-E 已确认实现边界

1. `VirtualPositionBook` 位于 `strategy_application`，不导入 simulator、Grid Rule 或交易所
   SDK；
2. 仓位主键为 `position_owner_id + instrument`，Strategy 内 n 条 Rule 共用同一 Owner，
   不同 StrategyInstance 默认不共享；
3. 归因 Fill 必须绑定已注册的全局 Intent，且 Intent Key、Strategy ID、标的、买卖
   方向、数量和数量单位必须一致；
4. OPEN、INCREASE、REDUCE 和 CLOSE 分别校验 Owner 当前仓位，REDUCE 不得超过 Owner
   仓位，CLOSE 必须精确消耗全部 Owner 仓位；
5. Owner 仓位和 Strategy State 分别预演，全部校验通过后才提交；
6. SPOT/LINEAR_PERPETUAL 使用价差盈亏，INVERSE_PERPETUAL 使用倒数价差和
   合约面值，结果均保留在 Strategy Allocation 的结算资产中；
7. 手续费全额归属产生 Fill 的 Strategy；Funding Settlement 显式携带 Product Type，
   资金费按结算时同标的、同产品、同方向 Owner 的绝对名义敞口比例分摊；
8. Strategy Snapshot 输出期初 Allocation、当前仓位、已实现/未实现盈亏、手续费、
   资金费、归因权益、Fill 数和 Funding Event 数；
9. `AttributionReconciliation` 对比 Runtime 权威账户快照，残差只记录、不自动消灭；
10. Runtime 继续唯一负责账户权益、保证金和强平。Application Allocation 和 Virtual
    Position 不得取代 Runtime 账本；
11. 5V2-F 已完成仿真 Fill/Funding 自动转换与结果持久化；未迁移的旧 Adapter 继续保留
    无 Application 财务事实的兼容路径。
