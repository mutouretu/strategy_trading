# 第 1 部分：仿真执行系统 v1.0 规格清单

## 1. 文档目的

本文用于指导 Codex 对 `market_simulator` 与 `grid_trading` 中的“仿真执行系统”进行分阶段完善。

当前系统已经完成最小闭环：

```text
MarketSource
    ↓
SimulationRunner
    ↕
SimulationTradePort
    ↓ TradeInstruction
SimFill
    ↓
Ledger
    ↓
SimulationResult / Viewer JSON
```

现阶段目标不是复制完整交易所，而是在不引入过度复杂度的前提下，使仿真结果具备基本的经济可信度。

Codex 应严格按照本文分批实施，每一批完成后运行测试并保持现有示例可运行。不得一次性重写整个仿真框架。

---

## 2. 当前实现基线

当前已具备：

- `MarketFrame` OHLC 数据结构；
- `SimulationTradePort`；
- `TradeInstruction`；
- `SimFill`；
- `IntentRecord`；
- `SimulationRunner`；
- `SimulationTracePort`；
- `LinearLedger`；
- 可注入的产品专属 `SimulationLedger`；
- COIN-M 反向合约账本基础实现；
- 可注入的 Maker/Taker `FeeModel` 与 COIN-M 手续费适配；
- 意图等待、撤销、成交生命周期；
- 新意图不能回看当前 Bar；
- 被动意图由 Adapter 按 Bar 区间覆盖解析；
- 主动意图由 Adapter 在信号后的下一根 Bar open 解析；
- 显式指令按当前 frame sequence 和明确 price 成交；
- `reduce_only` 防御校验；
- 全量 `SimulationRun` JSON；
- Viewer；
- 确定性执行测试。

当前明确未实现：

- 滑点；
- 资金费；
- 部分成交；
- 保证金；
- 破产判定；
- 强平；
- 跳空限价单价格改善；
- 运行终止原因；
- 保证金与强平事件记录。

---

## 3. 设计原则

### 3.1 保持通用运行时与策略解耦

`simulation_runtime` 不得依赖：

- 网格 Cell；
- 网格层级；
- 网格复位规则；
- `grid_trading` 具体策略类；
- Binance；
- SQLite；
- FastAPI；
- Streamlit。

网格策略通过 `SimulationTradePort` 接入。

### 3.2 保持市场生成与执行解耦

`market_simulator` 只生成 `MarketFrame`，不得处理：

- 订单；
- 成交；
- 仓位；
- 手续费；
- 保证金；
- 强平。

### 3.3 不模拟完整交易所

平台服务于策略研究，优先保证收益、风险、资金占用和策略行为等数值结果正确。
凡是不影响这些结果、只用于还原交易所协议和交互体验的状态与反馈，原则上不进入
仿真运行时。策略输出或账本状态违反执行不变量时，应以明确异常快速失败，而不是
继续模拟交易所的拒单、重试和回调流程。

v1.0 不处理：

- 订单簿；
- 排队优先级；
- 成交量约束；
- 撮合对手方；
- 网络异常；
- 订单查询；
- 部分成交；
- 自动联网同步交易所保证金档位；
- 多资产组合保证金规则。

### 3.4 所有新行为必须可配置、可测试、可复现

同一：

```text
市场路径
+ 策略参数
+ 执行配置
+ 随机种子
```

必须得到相同结果。

### 3.5 不破坏已有示例

以下示例必须持续可运行：

- deterministic probe；
- geometric ladder；
- single following grid；
- layered following grid；
- Viewer JSON 导出。

---

# 4. 仿真执行系统总体规格

```text
1. 仿真执行
│
├── 1.1 意图与指令语义
├── 1.2 成交模型
├── 1.3 仓位与合约账本
├── 1.4 交易成本
├── 1.5 保证金与强平
├── 1.6 运行终止
└── 1.7 执行记录与测试
```

---

# 5. 第一批：交易指令与平仓语义

## 5.1 目标

防止策略提交的平仓指令意外穿过零点并反向开仓。

这是进入正式实验前必须完成的第一项。

## 5.2 TradeInstruction 携带 reduce_only

通用交易指令包含：

```python
reduce_only: bool = False
```

示意：

```python
@dataclass(frozen=True, slots=True)
class TradeInstruction:
    instruction_key: str
    source_intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    frame_sequence: int
    intent_mode: TradeIntentMode
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)
```

要求：

- 建仓指令默认 `reduce_only=False`；
- 网格平仓指令必须设置 `reduce_only=True`；
- `reduce_only` 属于通用执行语义，不应只放在 tags 中；
- 主动和被动意图生成的指令都可以设置 `reduce_only`；
- 网格适配器根据意图角色映射：ENTRY 为 `False`，EXIT 为 `True`；
- `reduce_only` 只约束指令不得增加仓位或穿过零点反向开仓，不表示部分成交；
- v1.0 中指令只有“按明确数量全部成交”；非法指令直接导致 Run 快速失败。

## 5.3 reduce_only 校验规则

### 持仓范围

v1.0 按账本中同一 instrument 的账户净持仓判断：

```text
position[instrument] > 0
    → 多仓

position[instrument] < 0
    → 空仓

position[instrument] == 0
    → 无仓位
```

不按网格、Cell、策略或意图分别建立执行层仓位。网格规则可以在自身状态中保留
Cell 归属，但通用执行系统只校验账户聚合净持仓。

v1.0 采用单向净持仓模式，不模拟交易所 Hedge Mode 中同一 instrument 同时存在
独立 LONG 和 SHORT 仓位。

### 校验时机

`reduce_only` 应在 Runtime 接收当前帧的 `TradeInstruction`、准备生成最终 Fill 时
校验。意图等待期间，账户持仓可能已经被其他成交改变。

### 多仓

当前持仓：

```text
position > 0
```

允许：

```text
SELL + reduce_only=True
```

禁止：

```text
BUY + reduce_only=True
```

### 空仓

当前持仓：

```text
position < 0
```

允许：

```text
BUY + reduce_only=True
```

禁止：

```text
SELL + reduce_only=True
```

### 无仓位

当前持仓：

```text
position == 0
```

任何 `reduce_only=True` 指令不得成交。

## 5.4 超额平仓规则

v1.0 采用简单且严格的规则：

```text
reduce_only 数量 <= 当前可平仓数量
    → 整条指令全部成交

reduce_only 数量 > 当前可平仓数量
    → 判定为非法指令
    → 本次 Run 快速失败
```

暂不实现：

```text
自动缩量成交
部分 Fill
剩余数量继续挂单
```

后续需要时再扩展。

这里允许指令完整成交后只关闭账户总仓位的一部分。例如账户共有 9 张多仓，
某个 Cell 的 3 张 EXIT 完整成交后，账户剩余 6 张。这是“指令全量成交后减少
部分聚合仓位”，不是部分成交。

### 同一 Bar 多条 reduce_only 指令

同一 Bar 可能同时产生多条 `reduce_only` 指令。v1.0 不推测 Bar 内真实价格路径，
采用确定性逐条处理：

1. 当前帧指令按稳定的 `instruction_key` 顺序排列；
2. 每条指令按处理到它时的当前可平仓数量校验；
3. 合法指令完整应用到 Ledger，并立即更新可平仓数量；
4. 后续指令使用更新后的仓位继续校验；
5. 数量超过剩余可平仓数量时判定为非法指令并快速失败，不自动缩量。

例如当前有 5 张多仓，同一 Bar 依次处理两张 3 张的 reduce-only SELL：

```text
第一张 SELL 3
    → 完整成交
    → 剩余多仓 2

第二张 SELL 3
    → 数量超过剩余可平仓数量
    → 判定为非法订单
    → 本次 Run 失败并停止
```

该顺序只是可复现的仿真约定，不表示还原了 Bar 内真实成交顺序。正确构造的网格
EXIT 数量总和应与实际仓位一致；出现超额平仓指令通常意味着策略状态或账本状态
需要检查。

## 5.5 非法 reduce_only 指令快速失败

本平台的目标是计算策略收益、风险、资金占用和行为结果，不模拟交易平台的完整
订单拒绝与反馈机制。

`reduce_only` 校验失败表示策略输出、策略状态或账本状态违反了执行不变量，不属于
正常市场事件。v1.0 采用快速失败：

```text
reduce_only 校验失败
    → 不生成 SimFill
    → 不将违规指令应用到 Ledger
    → 抛出包含必要上下文的明确异常
    → 本次 Run 失败并立即停止
```

异常信息至少应包含：

- `instruction_key`；
- `source_intent_key`；
- instrument；
- 当前净持仓；
- 订单方向；
- 请求数量。

v1.0 不实现：

- `ORDER_REJECTED` 事件；
- `OrderStatus.REJECTED`；
- 拒绝原因枚举和交易所错误码；
- 将拒绝结果回调给策略后继续运行；
- 被拒指令重试；
- Viewer 中的拒单展示。

后续实验系统负责捕获运行异常，并在实验记录中保存：

```text
run status = FAILED
error_type
error_message
```

如果未来需要研究保证金不足、交易所限额等正常拒单行为，再单独设计交易拒绝和
策略反馈协议，不在当前 v1.0 中提前实现。

## 5.6 账本接口调整

Runner 在把当前 `TradeInstruction` 转成最终 `SimFill` 前读取账本当前仓位并做
合法性校验。

推荐职责：

```text
Runner / Account-Constraint-or-Validation Layer
    判断指令是否允许成交

Ledger
    只应用合法 Fill
```

意图是否触发由 Adapter 在生成指令前决定；Ledger 不反向依赖 Adapter 或具体策略。

## 5.7 第一批测试

### simulation_runtime 单元测试

必须增加：

1. 多仓 reduce-only SELL 正常减仓；
2. 多仓 reduce-only BUY 快速失败，且不生成 Fill、不改变 Ledger；
3. 空仓 reduce-only BUY 正常减仓；
4. 空仓 reduce-only SELL 快速失败，且不生成 Fill、不改变 Ledger；
5. 无仓位 reduce-only 指令快速失败，且不生成 Fill、不改变 Ledger；
6. reduce-only 数量等于持仓，完全平仓；
7. reduce-only 数量小于持仓时，指令全量成交，仓位减少但不归零；
8. reduce-only 数量大于持仓时不生成 Fill、不改变 Ledger，并快速失败；
9. reduce-only 校验不得改变普通指令原有的净仓位记账行为；
10. 同一 Bar 多条 reduce-only 指令按稳定顺序逐条占用可平仓数量；
11. 同一 Bar 后续指令超过剩余仓位时快速失败，不自动缩量；
12. 快速失败的异常包含 `instruction_key`、`source_intent_key`、instrument、当前净
    持仓、方向和请求数量；
13. 非法指令不得触发策略的 `on_fills()`。

第 9 项是通用账本的回归测试，目的是确认新增校验只约束
`reduce_only=True` 的指令，不表示当前网格策略会发出穿零反向开仓指令，也不表示
运行时支持同时持有独立的多仓和空仓。

### 网格适配器测试

以下行为属于网格适配器，不应放入 `simulation_runtime` 的单元测试：

1. 网格适配器生成的 ENTRY 为 `reduce_only=False`；
2. 网格适配器生成的 EXIT 为 `reduce_only=True`。

这些测试应放在网格适配器所在项目中，避免通用仿真框架依赖 ENTRY、EXIT 等网格
概念。

## 5.8 第一批验收标准

- 网格平仓单不可能产生反向仓位；
- 现有单组和分层网格样例仍可运行；
- 订单 JSON 中可以看到 `reduce_only`；
- 非法 reduce-only 订单不会生成 Fill 或改变 Ledger，并以明确异常终止 Run；
- 所有新增测试通过。

---

# 6. 第二批：主动/被动交易意图与显式成交指令

## 6.1 目标

本批不继续扩展 LIMIT、MARKET、跳空价格改善等交易所订单语义，而是重新划分：

```text
Rule Core
    决定交易意图及其产生时机

Strategy Simulation Adapter
    结合仿真假设和当前 MarketFrame
    把策略意图转换为当前 Bar 的明确交易指令

Simulation Runtime
    校验明确交易指令
    生成 SimFill
    应用 Ledger
    计算收益、风险和账户结果
```

最终目标是让 `simulation_runtime` 不再判断：

- LIMIT 或 MARKET；
- BUY/SELL 限价跳空；
- 价格改善；
- 订单簿和撮合；
- 某个策略为什么选择当前 Bar 或下一根 Bar。

Runtime 只接收已经包含明确成交价格的当前交易指令。

## 6.2 非目标

本批不实现：

- 真实交易所限价单；
- 市价单盘口成交；
- 跳空后的价格改善；
- 排队和成交量约束；
- 部分成交；
- 买卖价差；
- 滑点；
- 流动性不足；
- Bar 内路径推断。

尤其不采用原第 6 节的以下推断：

```text
BUY 目标价被向下跳过
    → 按 open 获得价格改善

SELL 目标价被向上跳过
    → 按 open 获得价格改善
```

如果整根 K 线没有覆盖被动意图的目标价，v1.0 就认为没有证据证明价格到达过，
不产生交易。

## 6.3 两类交易意图

主动和被动是 Rule Core 输出的策略语义，不是 Runtime 的撮合类型。

### 被动交易意图

被动意图表示：

> 策略提前声明一个目标价格，并持续等待市场价格覆盖它。

概念结构：

```python
@dataclass(frozen=True, slots=True)
class PassiveTradeIntent:
    intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    target_price: Decimal
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)
```

适用于：

- 网格建仓；
- 网格平仓；
- 天地单；
- 固定价格止盈或止损；
- 其他提前声明目标成交价的规则。

被动意图具有生命周期：

```text
创建
    → 跨 Bar 持续存在
    → 成交、规则撤销或被新意图替换后结束
```

### 主动交易意图

主动意图表示：

> 策略已经根据指标或状态决定交易，不要求市场先到达某个预设目标价。

概念结构：

```python
@dataclass(frozen=True, slots=True)
class ActiveTradeIntent:
    intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)
```

适用于：

- RSI 信号；
- 均线交叉；
- 趋势反转；
- 定时调仓；
- 风险条件触发的主动退出。

主动意图是一次性事件，不作为跨 Bar 挂单反复提交。

### 类型归属

`PassiveTradeIntent` 和 `ActiveTradeIntent` 是跨策略使用的概念词汇，但具体 Rule Core
可以保留自己的领域模型。`simulation_runtime` 不得反向依赖任一策略包中的意图类型。

例如当前：

```text
GridOrderIntent
    在网格语义中等价于 PassiveTradeIntent
```

不要求为了仿真立刻把 `GridOrderIntent` 改成通用类型。

## 6.4 Rule Core 的职责

Rule Core 负责：

- 是否产生交易意图；
- 意图产生和撤销的时机；
- 主动或被动分类；
- side；
- quantity；
- 被动意图的 target_price；
- ENTRY/EXIT 等策略内部角色；
- 提供足以判断 reduce-only 的策略角色语义。

具体 Core 不一定需要直接包含 `reduce_only` 字段。例如当前网格 Core 已通过
ENTRY/EXIT 表达开平仓角色，由 Adapter 映射成最终指令的 `reduce_only`。

Rule Core 不负责：

- 根据 OHLC 宣布被动意图已经成交；
- 为未来 K 线填写尚未出现的 open；
- 生成 `SimFill`；
- 修改账户仓位；
- 选择仿真专属滑点、费用和资金费算法。

这个边界保证同一个 Core 可以接入：

```text
PassiveTradeIntent
    ├─ 实盘适配器：提交交易所价格订单
    └─ 仿真适配器：按 OHLC 覆盖规则产生交易指令

ActiveTradeIntent
    ├─ 实盘适配器：立即提交交易
    └─ 仿真适配器：在下一可执行 Bar 产生交易指令
```

## 6.5 Strategy Simulation Adapter 的职责

仿真适配器是 Rule Core 和通用 Runtime 之间的执行语义边界。

它负责：

- 保存尚未结束的被动意图；
- 保存等待下一可执行 Bar 的主动意图；
- 只使用进入当前 Bar 以前已经存在的意图；
- 根据当前 MarketFrame 生成明确交易指令；
- 将 `SimFill` 转换回 Rule Core 能理解的 Fill；
- 将规则在 Fill 或 MarketFrame 后产生的新意图留到下一根 Bar；
- 为 Viewer 提供可选的意图生命周期信息。

### 被动意图解析

给定目标价 `P`：

```text
current.low <= P <= current.high
    → 当前 Bar 产生交易指令
    → instruction.price = P
```

否则：

```text
不产生交易指令
意图继续等待
```

BUY 和 SELL 使用完全相同的覆盖判断。v1.0 不推测跳空，不做价格改善。

### 主动意图解析

主动意图在信号产生后的下一可执行 Bar 使用 open：

```text
第 t-1 根 Bar 结束
    → Rule Core 产生 ActiveTradeIntent

第 t 根 Bar 到来
    → Adapter 产生当前交易指令
    → instruction.price = frame.open
```

如果主动意图是根据第 t 根完整 Bar 才产生的，就不能回到第 t 根 open 成交。

## 6.6 Runtime 的唯一成交输入

`simulation_runtime` 新增明确交易指令：

```python
class TradeIntentMode(StrEnum):
    PASSIVE = "PASSIVE"
    ACTIVE = "ACTIVE"


@dataclass(frozen=True, slots=True)
class TradeInstruction:
    instruction_key: str
    source_intent_key: str
    instrument: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    frame_sequence: int
    intent_mode: TradeIntentMode
    reduce_only: bool = False
    tags: Mapping[str, str] = field(default_factory=dict)
```

字段语义：

- `instruction_key`：当前交易指令唯一键；
- `source_intent_key`：产生它的策略意图键；
- `price`：当前 Bar 已经明确的待记账价格；
- `frame_sequence`：防止适配器提交过期或未来指令；
- `intent_mode`：只记录来源和支持后续费用分析，Runtime 不据此判断是否触发；
- `reduce_only`：沿用第 5 节的防御规则。

Runtime 对 `TradeInstruction` 只做：

1. 基础字段校验；
2. `frame_sequence == current.sequence` 校验；
3. 稳定排序和重复键校验；
4. `reduce_only` 校验；
5. 生成 `SimFill`；
6. `Ledger.apply(fill)`；
7. 记录结果并回调 Fill。

Runtime 不再检查：

```text
low <= instruction.price <= high
instruction.price == open
```

这些是 Adapter 产生指令以前必须保证的仿真假设。

## 6.7 新的端口与每根 Bar 调用顺序

当前 `SimulationDecisionPort` 返回完整 `desired_orders`，Runner 保存活动订单并调用
`BarTouchExecutionModel`。目标结构改为由 Adapter 保存策略意图，并向 Runtime 提供
当前 Bar 的明确指令。

概念接口：

```python
class SimulationTradePort(Protocol):
    def initialize(self, frame: MarketFrame) -> None:
        ...

    def instructions_for(
        self,
        frame: MarketFrame,
    ) -> tuple[TradeInstruction, ...]:
        ...

    def on_fills(
        self,
        fills: Sequence[SimFill],
    ) -> None:
        ...

    def on_market(self, frame: MarketFrame) -> None:
        ...
```

每根 Bar 的顺序固定为：

```text
1. MarketSource.next() 得到 current
2. port.instructions_for(current)
   - 只能解析进入 current 以前已经存在的意图
3. Runtime 按 instruction_key 稳定排序
4. 逐条执行：
   4.1 frame_sequence 校验
   4.2 reduce_only 校验
   4.3 生成 SimFill
   4.4 Ledger.apply(fill)
5. port.on_fills(fills)
6. port.on_market(current)
7. Adapter 保存新意图，但不得再次解析 current
8. 保存 EquitySnapshot
```

初始化时：

```text
first = MarketSource.reset()
port.initialize(first)
```

初始化产生的意图最早从下一根 MarketFrame 生效，不能回看 first。

## 6.8 同一 Bar 的确定性规则

v1.0 不推测 Bar 内路径：

- 所有在 Bar 开始前已经存在、且目标价被当前 OHLC 覆盖的被动意图都可以产生指令；
- 所有等待当前 Bar 的主动意图都按当前 open 产生指令；
- 指令按稳定的 `instruction_key` 顺序执行；
- 每条指令执行后立即更新 Ledger；
- 后续 reduce-only 指令使用更新后的当前仓位；
- Fill 后新产生的被动或主动意图不得参与当前 Bar；
- 同一 Bar 同时覆盖买入价和卖出价时，仍按稳定顺序全部执行，不声称还原真实路径。

## 6.9 成交价格与结果记录

`TradeInstruction.price` 是 Adapter 根据策略仿真假设确定的当前执行价格。

`SimFill.price` 是 Runtime 接受并最终应用的成交价格。在尚未引入滑点时：

```text
SimFill.price = TradeInstruction.price
```

为了支持回放和审计，`SimFill` 或其 tags 必须能追踪：

- `source_intent_key`；
- `intent_mode`；
- `instruction_key`。

后续加入滑点后再区分：

```text
reference_price = TradeInstruction.price
effective_price = SimFill.price
```

## 6.10 意图生命周期与 Viewer

Runtime 不再执行或拥有策略意图，但 Viewer 仍需要显示网格等待价格和生命周期。
因此意图展示应通过独立、只读的报告接口提供，不能重新参与成交判断。

概念结构：

```python
class SimulationTracePort(Protocol):
    def visible_intents(self) -> tuple[IntentSnapshot, ...]:
        ...
```

`IntentSnapshot` 最少包含：

- intent_key；
- PASSIVE/ACTIVE；
- instrument；
- side；
- quantity；
- target_price，可为空；
- reduce_only；
- strategy tags；
- 当前状态。

Runtime 可以记录这些快照或生命周期事件，但不得依据它们生成 Fill。

最终 JSON 建议升级 schema：

```text
schema_version = 2
intents
instructions
fills
equity
summary
```

Runtime 只输出 v2。Viewer 在读取历史 v1 文件时，可将旧 `orders` 投影为展示意图；
该兼容逻辑不进入 Runtime。

## 6.11 当前代码耦合分析

### 不需要修改的部分

| 组件 | 原因 |
| --- | --- |
| `market_protocol.MarketFrame` | OHLC 和 sequence 已足够 |
| `MarketSource` 及 GBM/Fixed 数据源 | 只负责提供行情 |
| `LinearLedger` | 仍然只应用合法 Fill |
| `InverseContractLedger` | 仍然只应用合法 Fill |
| `grid_rule.GridRuleEngine` | 已输出 side、quantity、price、role，并从外部接收 Fill |
| 单组和分层网格 Strategy Core | 高层建网格和复位逻辑不依赖 Runtime 订单类型 |

### 直接需要修改的部分

| 组件 | 当前职责 | 目标调整 |
| --- | --- | --- |
| `SimOrder` / `OrderType` | 表达 LIMIT/MARKET | 由 `TradeInstruction` 取代 Runtime 成交输入 |
| `ActiveOrder` | Runtime 保存待触发订单 | 待解析意图迁入 Strategy Simulation Adapter |
| `SimulationDecision.desired_orders` | Core Adapter 返回完整订单集 | 改由新 Port 返回当前 `TradeInstruction` |
| `SimulationDecisionPort` | 订单同步和 Fill/Market 回调 | 迁移为 `SimulationTradePort` 的分阶段接口 |
| `BarTouchExecutionModel` | Runtime 判断 OHLC 触价 | 从 Runtime 删除；覆盖判断迁入被动策略 Adapter 或无状态辅助函数 |
| `SimulationRunner._synchronize` | 创建、撤销和退休订单 | 意图生命周期迁出；Runner 只校验和应用当前指令 |
| `OrderRecord` | Runtime 记录订单生命周期 | 改为 Adapter 提供的 `IntentSnapshot`/事件，或迁移期兼容投影 |
| `reporting.py` | 输出 order_type、limit_price | 输出 intents、instructions 和 fills |
| Viewer schema / `app.js` | 校验和显示 LIMIT/MARKET 活动订单 | 支持 v2 意图、指令和 v1 兼容读取 |
| deterministic/ladder probes | 直接创建 `SimOrder` | 改用最小测试 Adapter 产生主动/被动意图 |

### `grid_trading` 适配器影响

迁移前，三个仿真适配器只是把 `GridOrderIntent` 机械转换成 LIMIT `SimOrder`；
现在它们是有状态的执行语义适配器：

```text
GridRuleSimulationAdapter
SingleFollowingGridSimulationAdapter
LayeredFollowingGridSimulationAdapter
```

共同需要：

1. 保存上一阶段完整 `GridOrderIntent` 集合；
2. 记录每个意图从哪个 sequence 开始有效；
3. 根据当前 OHLC 覆盖目标价；
4. 只为当前 Bar 以前已经有效的意图生成 `TradeInstruction`；
5. 将 Fill 回传给 Grid Rule；
6. 在 Fill 和 on_market 后刷新下一阶段意图；
7. 保证关闭后的 intent key 不重复执行；
8. 提供 Viewer 所需的意图生命周期快照。

价格覆盖逻辑可以抽成通用、无状态函数复用：

```python
def bar_covers_price(frame: MarketFrame, price: Decimal) -> bool:
    return frame.low <= price <= frame.high
```

该函数只回答行情数据是否覆盖价格，不维护订单，也不模拟交易所。

### 第 5 节 reduce-only 影响

第 5 节的业务语义不变：

- ENTRY 通常为 `reduce_only=False`；
- EXIT 通常为 `reduce_only=True`；
- Runtime 在应用 Ledger 前校验；
- 非法指令快速失败；
- 同一 Bar 按稳定顺序使用最新净仓位。

当前字段载体为：

```text
TradeInstruction.reduce_only
```

网格 Adapter 必须把 `GridOrderRole.EXIT` 映射到最终指令的 `reduce_only=True`。

### 后续章节耦合

以下章节已经按新模型调整：

| 章节 | 现有耦合 | 后续调整方向 |
| --- | --- | --- |
| 第 7 节期末快照 | Runtime 不生成期末交易，直接保存最后的 Ledger 与意图状态 |
| 第 8 节手续费 | 使用 `intent_mode` 的默认费用角色，允许配置覆盖 |
| 第 10 节滑点 | 主动/被动只是默认提示，最终由 SlippageModel 配置 |
| 第 13 节 Runner 顺序 | Port 先提供当前 `TradeInstruction` |
| 第 17 节验收 | 验收主动/被动意图解析和显式指令 |

资金费、保证金、强平检查和账本公式主要消费 Fill、仓位和 mark，不依赖 LIMIT/MARKET，
原则上不受本次边界调整影响。

## 6.12 迁移顺序

这是跨 `market_simulator` 和 `grid_trading` 的接口迁移，不能按一次删除全部旧接口的
方式实施。推荐：

1. 在 `simulation_runtime` 增加 `TradeInstruction` 和新 Port，暂时保留旧接口；
2. 增加 Runtime 对显式指令的执行与 reduce-only 测试；
3. 抽出无状态 `bar_covers_price()`；
4. 迁移 `GridRuleSimulationAdapter`；
5. 迁移单组和分层 Strategy Adapter；
6. 对同一固定市场路径并行比较新旧 Fill、Ledger 和权益结果；
7. 迁移 deterministic probe 和 geometric ladder probe；
8. 增加主动意图的最小 RSI 风格测试 Adapter；
9. 发布 JSON schema v2，并让 Viewer 同时支持 v1/v2；
10. 删除旧 `OrderType`、`SimOrder`、`ActiveOrder` 和 `BarTouchExecutionModel`；
11. 更新第 7、8、11、14、18 节中的旧术语；
12. 最后重新生成标准 Viewer 样例。

当前 6A 已完成第 1、2 步的兼容实现：

- 已新增 `TradeInstruction`、`TradeIntentMode` 和 `SimulationTradePort`；
- `SimulationRunner` 已可通过独立 `trade_port` 执行显式指令；
- 已实现 sequence、instrument、重复键、稳定顺序和 reduce-only 校验；
- 旧 `SimulationDecisionPort`、`SimOrder` 和 `BarTouchExecutionModel` 仍完整保留；
- 该阶段尚未迁移 Probe、JSON schema 和 Viewer。

当前 6B 已完成第 3～6 步：

- `grid_rule.adapters` 已新增无状态 `bar_covers_price()`；
- 已新增有状态 `PassiveGridIntentBook`，由适配层保存意图的生效 sequence、撤销、
  退役和单次执行状态；
- `GridRuleSimulationAdapter`、`SingleFollowingGridSimulationAdapter` 和
  `LayeredFollowingGridSimulationAdapter` 均已实现 `instructions_for(frame)`；
- 三个适配器只把进入当前 Bar 以前已经存在、且价格被当前 OHLC 覆盖的网格意图转换成
  `TradeInstruction`；
- Fill 或 `on_market` 新产生的意图统一从下一根 Bar 开始有效；
- ENTRY 映射为 `reduce_only=False`，EXIT 映射为 `reduce_only=True`；
- 迁移期暂时保留三个适配器原有的 `SimulationDecision` 返回值，用于同一适配器的
  新旧执行路径对照；新路径忽略这些返回值；
- 单 Rule、单组跟随和分层跟随的固定行情对照中，迁移前后的 Fill、Ledger 和权益
  完全一致；
- 三年 seed 42 的单组与分层路径也已完成对照，分别保持 155 和 736 笔 Fill，最终
  权益不变。

6B 不修改 `grid_rule` 或高层 Strategy Core；该阶段也没有切换标准 Probe 和 Viewer
样例。

当前 6C 已完成第 7、8 步：

- deterministic probe 已改为由适配器保存被动与主动意图，并通过
  `SimulationRunner(..., trade_port=...)` 执行；
- 原 probe 的撤销、替换、被动止盈和主动退出时序保持不变，仍为 3 笔 Fill、最终
  权益 1006；
- geometric ladder probe 已从 `SimOrder` 全量同步迁移为被动意图解析，三年 seed 42
  仍为 84 笔 Fill、已实现收益 972.8853、最终权益 10972.8853；
- `examples/intent_adapter_support.py` 提供仅供示例适配器复用的主动/被动意图状态簿，
  该代码不进入 `simulation_runtime`；
- 已增加最小 `RsiSignalRule` 与 `RsiSignalSimulationAdapter`：Rule 只在完整 Bar
  收盘后产生信号，Adapter 将信号保存为一次性主动意图，下一根 Bar 才按 open 产生
  指令；
- RSI 固定路径中，sequence 2 的买入信号在 sequence 3 按 open 81 成交，sequence 3
  的退出信号在 sequence 4 按 open 119 成交，最终平仓收益为 38；
- 主动意图已覆盖不可回看当前 Bar、下一 open、单次执行、多意图稳定顺序和
  reduce-only 退出测试。

当前 6D 已完成第 9 步：

- 新增只读 `SimulationTracePort`、`IntentSnapshot` 和 `IntentRecord`，由适配器暴露
  当前可见意图，Runtime 只记录生命周期和校验指令来源，不依据快照生成 Fill；
- 显式路径统一输出 schema v2，包含 `intents`、`instructions`、`fills`、`equity`
  和 `summary`，不再输出空的 `orders`；
- v2 生命周期区分 `WAITING`、`FILLED` 和 `CANCELLED`，主动意图允许
  `target_price=null`；
- Viewer 将 v1 `orders` 兼容投影为展示意图，并原生读取 v2 意图与指令；图表、账户
  指标、成交明细和意图生命周期表均使用统一模型；
- 网格三个适配器、确定性 Probe、三年几何挂单 Probe 以及标准单组/分层生成脚本均已
  接入 v2 报告；
- 固定 seed 的成交与权益保持不变：几何挂单 84 笔、单组跟随 155 笔、分层跟随
  736 笔。

当前 6E 已完成第 10～12 步：

- 删除 `SimulationDecisionPort`、`SimulationDecision`、`SimOrder`、`OrderType`、
  `ActiveOrder`、`OrderRecord`、`OrderStatus` 和 `BarTouchExecutionModel`；
- 删除 Runtime 的 desired-order 同步、订单触价和 v1 报告生成分支；
- `SimulationRunner` 只接受 `SimulationTradePort`，三个网格适配器不再返回兼容
  `SimulationDecision`；
- `SimFill` 直接携带 `instruction_key`、`source_intent_key` 和 `intent_mode`，
  不再使用通用 `order_key`；网格域自己的 `GridOrderIntent.order_key` 由适配器映射；
- Viewer 继续读取历史 schema v1，但 Runtime 只生成 schema v2；
- 第 7、8、11、13、14、15、18 节及 README 已统一为意图、指令和 Fill 语义；
- 标准 Viewer 样例已重新生成，固定 seed 的成交数量和最终权益保持不变。

迁移期间禁止修改 `grid_rule` 的网格价格、Cell 状态转换和复位算法，确保结果变化只
来自接口边界迁移。

## 6.13 第二批测试

### simulation_runtime

必须增加：

1. Runtime 按 `TradeInstruction.price` 生成 Fill；
2. Runtime 不根据 OHLC 重新判断 instruction.price；
3. `frame_sequence` 不是当前 Bar 时快速失败；
4. 同一 Bar 多指令按 instruction_key 稳定排序；
5. 重复 instruction_key 快速失败；
6. reduce-only 合法指令正常记账；
7. reduce-only 非法指令不应用 Ledger、不回调 Fill；
8. 普通指令仍保持现有净仓位记账能力。

### 被动意图 Adapter

必须增加：

1. `low <= target_price <= high` 时按 target_price 产生指令；
2. 整根 Bar 未覆盖 target_price 时不产生指令；
3. 跳空越过目标价但 OHLC 未覆盖时不产生指令；
4. BUY/SELL 使用相同覆盖规则；
5. 当前 Bar 新产生的被动意图不能使用当前 Bar；
6. 被动意图可跨多个 Bar 等待；
7. 规则撤销后不再产生指令；
8. Fill 后新产生的对偶意图最早下一根 Bar 生效；
9. 多意图同 Bar 结果稳定；
10. ENTRY/EXIT 正确映射 reduce-only。

### 主动意图 Adapter

必须增加：

1. 第 t-1 根结束后产生的主动意图按第 t 根 open 产生指令；
2. 第 t 根完整 Bar 产生的主动意图不得按第 t 根 open 成交；
3. 主动意图只执行一次；
4. 多个主动意图同 Bar 结果稳定；
5. 主动退出意图可以携带 reduce-only。

### 集成与回归

必须验证：

1. 确定性 probe；
2. 几何挂单 probe；
3. 单组做多和做空网格；
4. COIN-M 账本；
5. 三年单组跟随网格；
6. 三年分层跟随网格；
7. JSON v2；
8. Viewer v1/v2 兼容读取。

## 6.14 第二批验收标准

- `simulation_runtime` 的公开成交输入中不再出现 LIMIT/MARKET；
- Runtime 不再拥有策略挂单和触价判断；
- Rule Core 决定主动/被动意图及其产生时机；
- Strategy Simulation Adapter 将意图转换为当前 Bar 的明确指令；
- 被动意图只在 OHLC 覆盖目标价时按目标价成交；
- 主动意图在信号后的下一可执行 Bar 按 open 成交；
- 新意图不能回看当前 Bar；
- `reduce_only` 防御保持有效；
- `grid_rule` 和高层网格 Strategy Core 无需修改；
- 固定市场路径上的迁移前后 Fill、Ledger 和权益结果一致；
- Viewer 能同时读取迁移期 v1 和最终 v2 数据；
- 所有新增与现有测试通过。

---

# 7. 第三批：期末状态快照

## 7.1 原则

仿真数据结束只表示统计区间结束，不构成交易事件。Runtime 不替策略平仓、撤销意图或
生成额外成交，只保存最后一根 Bar 结束后的真实状态。

本节不引入 `EndPolicy`，也不支持期末强制平仓和“必须空仓”规则。

## 7.2 固定行为

最后一根 Bar 处理完成后：

- 使用当时最新市场价格作为 mark；日线仿真中即最后一根 Bar 的 close；
- 保留 Ledger 中的现金、仓位、平均成本和已实现盈亏；
- 按最新 mark 计算最终权益，最终权益包含未实现盈亏；
- 保留策略仍然可见但尚未成交的意图，其状态继续为 `WAITING`；
- 不产生额外 `TradeInstruction` 或 `SimFill`；
- 不修改策略和 Adapter 的内部状态。

`WAITING` 在最终结果中的含义是“截至样本结束仍在等待”，不是跨越仿真边界继续执行。

## 7.3 不属于 Runtime 的行为

以下需求不放入通用 Runtime：

- 策略要求在某个日期平仓：由策略在可执行 Bar 主动产生 `reduce_only` 指令；
- 要求策略最终空仓：由实验评价层检查 `SimulationResult.final_positions`；
- 假设立即卖出全部仓位：由评价层基于最终状态计算假设清算权益，不写入 Fill；
- 合约到期交割、保证金强平和破产停止：分别由产品账本或 MarginModel 定义，不属于数据
  截止时的默认行为。

## 7.4 结果字段

本节不增加新的结果字段。继续使用现有：

- `final_cash`
- `final_positions`
- `final_average_costs`
- `realized_pnl`
- `final_equity`
- `final_account_metrics`
- 最后一项 `equity_curve`

其中产品专属的 BTC、USDT 等权益继续通过 Ledger 和 `final_account_metrics` 提供。

## 7.5 第三批测试

只需要固定以下计算契约：

1. 期末有仓位时不产生额外 Fill；
2. 期末现金、仓位和平均成本保持不变；
3. 最终权益按最后一根 Bar 的最新 mark 计算；
4. 等待中的意图保持 `WAITING`；
5. 产品专属 Ledger 的期末账户指标保持正确。

## 7.6 第三批验收标准

- 仿真结束不会触发交易；
- 最终状态与最后一根 Bar 处理完成后的 Ledger 状态一致；
- 最终权益包含按当时市价计算的未实现盈亏；
- Viewer 将期末 `WAITING` 显示为截至样本结束仍未成交；
- Runtime 不包含期末交易策略。

当前 `SimulationRunner` 已满足以上契约，并由期末持仓估值和等待意图状态测试固定该
行为，因此本批不需要新增运行时接口。

---

# 8. 第四批：手续费模型

## 8.1 目标

让高频网格结果能够反映基础交易成本。

## 8.2 FeeModel

新增通用接口：

```python
class FeeModel(Protocol):
    def calculate(
        self,
        instruction: TradeInstruction,
        fill: SimFill,
    ) -> FeeResult:
        ...
```

建议默认实现：

```python
class FixedRateFeeModel:
    maker_fee_rate: Decimal
    taker_fee_rate: Decimal
```

Runtime 未传入 FeeModel 时使用 `ZeroFeeModel`，保证零费率结果与此前版本一致。

## 8.3 流动性角色

新增：

```python
class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"
```

v1.0 简化规则：

```text
PASSIVE → 默认 MAKER
ACTIVE → 默认 TAKER
```

说明：主动/被动只提供默认费用角色，不等于真实交易所最终 Maker/Taker 结果。
需要更精确时应由 Adapter 或费用配置明确提供 `liquidity_role`。

## 8.4 FeeResult

建议结构：

```python
@dataclass(frozen=True, slots=True)
class FeeResult:
    liquidity_role: LiquidityRole
    fee_rate: Decimal
    fee_amount: Decimal
    fee_asset: str
```

## 8.5 线性合约手续费

U 本位或线性账本：

```text
fee_amount
=
fill_price × fill_quantity × fee_rate
```

手续费资产：

```text
USDT 或账本 equity_asset
```

## 8.6 COIN-M 手续费

币本位反向合约手续费由 COIN-M 账本或产品专属费用适配器计算。

通用运行时不得硬编码 COIN-M 公式。

要求：

- 通用 `FeeModel` 可以由调用方替换；
- COIN-M 示例使用产品专属 FeeModel；
- Viewer 只读取结果字段，不依赖具体公式。

## 8.7 账本处理顺序

建议：

```text
1. 验证指令是否允许成交
2. 生成成交价格与数量
3. 计算手续费
4. 账本应用交易
5. 账本扣除手续费
6. 记录净账户状态
```

## 8.8 Fill 扩展字段

`SimFill` 增加：

- `liquidity_role`
- `fee_rate`
- `fee_amount`
- `fee_asset`
- `reduce_only`

## 8.9 Ledger 扩展

`SimulationLedger` 增加：

```python
@property
def total_fees(self) -> Decimal:
    ...
```

费用字段随最终 Fill 一起传入：

```python
def apply(self, fill: SimFill) -> None:
    ...
```

具体 Ledger 在应用成交时扣除 `fill.fee_amount` 并累计 `total_fees`，不提供第二条
`apply_fee()` 路径，避免重复扣费。

要求：

- `realized_pnl` 的语义必须明确；
- 建议区分毛收益和净收益；
- 不允许费用既计入 cash 又重复计入 realized_pnl。

## 8.10 结果字段

`SimulationResult` 与 summary 增加：

- `gross_realized_pnl`
- `total_fees`
- `net_realized_pnl`

如现有 `realized_pnl` 保留，应明确它代表：

```text
net_realized_pnl
```

或者保持毛收益并新增净收益，但必须在文档和测试中固定。

推荐：

```text
realized_pnl = net_realized_pnl
```

同时保留：

```text
gross_realized_pnl
total_fees
```

## 8.11 第四批测试

必须增加：

1. PASSIVE 指令按默认 Maker 费率；
2. ACTIVE 指令按默认 Taker 费率；
3. 一次完整线性 round trip 的毛收益、手续费和净收益正确；
4. 多次加减仓费用累计正确；
5. COIN-M 手续费资产和金额正确；
6. 零费率时结果与旧实现一致；
7. JSON 中费用字段正确导出。

## 8.12 第四批验收标准

- 每笔 Fill 均可追踪手续费；
- 结果可区分毛收益、总费用、净收益；
- 单组和分层网格结果能够显示手续费影响；
- 零费率模式保持向后兼容。

当前第四批已经完成：

- 新增 `FeeModel`、`FeeResult`、`ZeroFeeModel`、线性 `FixedRateFeeModel` 和
  `LiquidityRole`；
- `PASSIVE` 默认映射为 Maker，`ACTIVE` 默认映射为 Taker；
- Runtime 在 Ledger 记账前计算费用，最终 `SimFill` 完整记录角色、费率、金额和资产；
- `LinearLedger` 区分毛已实现收益、累计手续费和净已实现收益；
- `grid_rule.adapters` 提供 COIN-M 专属 `InverseContractFeeModel`，费用按
  `张数 × 合约面值 ÷ 成交价 × 费率` 以 BTC 结算；
- schema v2、Viewer 和账户快照已经支持逐笔及累计手续费；
- 标准单组和分层样例显式使用 Maker `0.0002`、Taker `0.0005` 的可配置研究假设，
  费率写入 manifest，不作为 Runtime 默认值；
- 零费率回归保持原成交和权益结果。

---

# 9. 第五批：合约账户、杠杆、保证金与强平计算

## 9.1 目标与原则

本批把原“破产停止”和“简化保证金与强平”合并为一套完整、可校准的合约账户计算。

Runtime 在这里是一台不带主观判断的执行机器。它只回答：

> 按当前钱包、仓位、杠杆、费用和市场价格，账户数值是什么，平台是否已经达到强平条件？

只要产品账户规则已经触发强平，仿真就必须终止，不能继续等待行情恢复。

本批不包含策略风控。以下行为均不属于 Runtime：

- 设置策略最大张数或最大名义敞口；
- 根据保证金利用率主动减仓；
- 暂停或恢复开仓；
- 设置策略风险预算；
- 在接近强平以前主动保护账户；
- 判断某种仓位或杠杆是否“合理”。

这些行为以后由策略体系决定。Runtime 只计算并执行客观账户约束。

## 9.2 第一阶段产品与账户范围

第一阶段先把当前实际使用的 COIN-M 反向永续合约做准确：

- 单一 instrument；
- 单向净持仓；
- 合约数量以“张”为单位；
- 每张合约具有固定 USD 面值；
- BTC 作为保证金和盈亏结算资产；
- 一个仿真 Run 使用一个专用合约钱包；
- 合约钱包内部采用单资产 Cross 语义；
- 现货 BTC 只参与总资产估值，不补充合约保证金；
- 不支持同一 Run 内多策略、多品种共享保证金；
- 不支持 Hedge Mode 下独立 LONG/SHORT 双向仓位；
- 不支持自动追加保证金。

这里的“专用合约钱包”表示本次实验分配给策略的全部合约资金；它与长期持有的现货
BTC 完全分离。

当前 `LinearLedger` 是全额现金/现货式线性账本，不是 U 本位合约账本。不得直接给它
增加 leverage 字段并宣称支持 U 本位强平。未来需要 U 本位时，应新增独立的
`LinearContractLedger` 和对应 MarginModel。

## 9.3 术语、符号与单位

COIN-M 账户使用：

```text
Q = 有符号合约张数；多仓为正，空仓为负
C = 每张合约面值，单位 USD/contract
N = abs(Q) × C，仓位名义价值，单位 USD
E = 反向合约平均开仓价，单位 USD/BTC
M = 当前标记价格，单位 USD/BTC
L = 初始杠杆倍数
D = 仓位方向；多仓为 1，空仓为 -1
W = 合约钱包余额，单位 BTC
```

所有金额字段必须在名称或类型中明确资产单位，不能使用含糊的 `equity`、
`margin` 或 `margin_ratio` 表示多个概念。

计算过程统一使用 `Decimal`。中间值不得提前按展示精度舍入；只允许在合约数量、
交易所规定的资产精度边界或最终序列化时量化。

## 9.4 账户与盈亏公式

仓位名义价值：

```text
position_notional_usd = abs(Q) × C
```

COIN-M 未实现盈亏：

```text
unrealized_pnl_btc
    = D × N × (1 / E - 1 / M)
```

钱包余额只包含已经结算的项目：

```text
futures_wallet_btc
    = initial_futures_wallet_btc
    + gross_realized_pnl_btc
    - trading_fees_btc
    + total_funding_btc
```

`total_funding_btc` 使用钱包变动符号：收到为正，支付为负。默认
`ZeroFundingModel` 下：

```text
total_funding_btc = 0
```

manifest 和结果明确记录 `funding_enabled=false`；启用固定资金费模型时则记录
`funding_enabled=true` 和 `funding_source=FIXED`。

保证金余额：

```text
margin_balance_btc
    = futures_wallet_btc + unrealized_pnl_btc
```

账户总资产继续单独计算：

```text
total_equity_btc
    = spot_btc + margin_balance_btc

total_equity_usdt
    = total_equity_btc × M
```

`total_equity_btc` 和 `total_equity_usdt` 只用于资产评价，不得参与合约强平判断。

## 9.5 杠杆与初始保证金

配置只保存一个权威杠杆值：

```python
@dataclass(frozen=True, slots=True)
class MarginConfig:
    leverage: Decimal
    maintenance_schedule: MaintenanceMarginSchedule
    mark_price_sampling: MarkPriceSampling
```

不得同时接受可相互冲突的 `leverage` 和 `initial_margin_rate`。初始保证金率固定推导为：

```text
initial_margin_rate = 1 / leverage
```

COIN-M 仓位初始保证金：

```text
position_initial_margin_btc
    = position_notional_usd / M / leverage

available_balance_btc
    = margin_balance_btc - position_initial_margin_btc
```

第一阶段尚未冻结未成交挂单保证金，因此这里不扣除 `open_order_initial_margin_btc`；
该限制见 9.8。

杠杆只影响初始保证金、资金占用和可开仓容量，不改变：

- 合约名义价值；
- 已实现盈亏；
- 未实现盈亏；
- 维持保证金率；
- 在 Cross 钱包和既定仓位固定时的账户损失路径。

## 9.6 维持保证金档位

维持保证金不得硬编码在 Ledger 中。通用 Runtime 只依赖：

```python
class MaintenanceMarginSchedule(Protocol):
    def requirement(
        self,
        *,
        position_notional: Decimal,
    ) -> Decimal:
        """Return maintenance requirement in the notional asset."""
```

提供两种实现：

```text
FlatMaintenanceMarginSchedule
TieredMaintenanceMarginSchedule
```

固定费率版本用于确定性测试和明确的研究假设：

```text
maintenance_requirement_usd
    = position_notional_usd
    × maintenance_margin_rate

maintenance_margin_btc
    = maintenance_requirement_usd
    / M
```

档位表只负责在名义资产中计算要求，产品 MarginModel 负责换算到结算资产。这样档位
接口不内置 COIN-M 的 `/ M`，以后 U 本位模型可以复用同一类分档结构。

分档版本用于和目标交易平台校准。每份分档配置必须记录：

- product / instrument；
- notional 下限与上限；
- maintenance margin rate；
- 速算扣除额或平台定义的等价字段；
- 数据来源；
- 生效或抓取日期；
- 配置版本和内容哈希。

分档边界、扣除额及换档连续性必须通过独立测试。v1.0 不自动联网同步交易所最新档位，
但任何声称“平台校准”的实验都必须显式使用一份版本化档位快照。

当前分档结构为：

```python
@dataclass(frozen=True, slots=True)
class MaintenanceMarginTier:
    notional_floor: Decimal
    notional_cap: Decimal | None
    maintenance_margin_rate: Decimal
    maintenance_amount_deduction: Decimal
```

正名义价值采用 `(notional_floor, notional_cap]`：边界值归入较低档，首档下限必须为
0，相邻档位必须共用边界，只有末档可以使用 `None` 表示无上限。由于档位必须连续，
边界归属不会改变边界处的维持保证金数值。

每档要求为：

```text
maintenance_requirement_usd
    = position_notional_usd
    × maintenance_margin_rate
    - maintenance_amount_deduction
```

`TieredMaintenanceMarginSchedule` 在构造时拒绝：

- 空档位表、非零首档下限和中间无上限档；
- 档位空隙、重叠或费率下降；
- 负费率、负扣除额和非有限数值；
- 相邻档位在共用边界处不连续；
- 缺少 product、instrument、source、effective_at、version 或 content_hash。

末档具有有限上限时，超过上限的仓位名义价值属于不可计算输入，不能静默套用最后一档。

## 9.7 MarginModel 与 MarginSnapshot

通用 Runtime 新增账户保证金计算端口，不使用容易与策略风控混淆的 `RiskModel`：

```python
class MarginModel(Protocol):
    def snapshot(
        self,
        ledger: SimulationLedger,
        *,
        mark_price: Decimal,
        frame: MarketFrame,
    ) -> MarginSnapshot:
        ...

    def projected_snapshot(
        self,
        ledger: SimulationLedger,
        *,
        fill: SimFill,
        mark_price: Decimal,
        frame: MarketFrame,
    ) -> MarginSnapshot | None:
        ...
```

默认实现：

```text
NoMarginModel
```

COIN-M 产品实现：

```text
InverseContractMarginModel
```

`MarginSnapshot` 至少包含：

```text
sequence
timestamp
instrument
settlement_asset
notional_asset
mark_price
mark_price_source
leverage
position_quantity
position_unit
average_entry_price
position_notional
wallet_balance
unrealized_pnl
margin_balance
position_initial_margin
maintenance_margin
available_balance
margin_buffer
initial_margin_utilization
maintenance_margin_utilization
effective_leverage
estimated_liquidation_price
liquidation_triggered
bankrupt
```

通用字段的单位由 `settlement_asset` 和 `notional_asset` 明确。对于当前 COIN-M 模型，
保证金和盈亏字段均为 BTC，`position_notional` 为 USD 名义金额；产品专属
`account_metrics` 和最终 JSON 可以继续使用 `_btc`、`_usdt` 后缀增强可读性。

其中：

```text
margin_buffer_btc
    = margin_balance_btc - maintenance_margin_btc

initial_margin_utilization
    = position_initial_margin_btc / margin_balance_btc

maintenance_margin_utilization
    = maintenance_margin_btc / margin_balance_btc

effective_leverage
    = position_notional_usd / (margin_balance_btc × M)
```

无仓位或分母非正时，相应比例和强平价格使用 `None`，不得用零伪装为有效数值。

MarginModel 只读取 Ledger 的账户事实并进行产品公式计算；Ledger 继续只负责应用合法
Fill 和维护会计状态。

纯计算实现使用局部高精度 Decimal context，不修改进程全局精度。

## 9.8 新开仓保证金可执行性

在把增加敞口的 `TradeInstruction` 应用为 Fill 以前，Runtime 必须计算成交和手续费
发生后的预计账户状态：

```text
projected_margin_balance_btc
projected_position_initial_margin_btc
projected_available_balance_btc
```

如果：

```text
projected_available_balance_btc < 0
```

则该指令在当前账户状态下不可执行。

校验点固定在：

```text
TradeInstruction
    → 生成包含最终手续费的候选 SimFill
    → MarginModel.projected_snapshot(...)
    → 校验 projected_available_balance
    → 真实 Ledger.apply(fill)
```

产品 MarginModel 必须在独立账本副本上应用候选 Fill；Runtime 不复制或解释产品账本。
当前 COIN-M 实现使用 `InverseContractLedger.clone()`，因此投影中的已实现盈亏、
手续费、反向平均开仓价和仓位都与真实记账公式一致，而校验失败不会污染真实账本。

开仓校验使用指令成交价作为 `mark_price_at_fill_proxy`，并在投影快照中记录
`mark_price_source="fill_price_proxy"`。处理规则为：

- 从空仓开仓、同方向加仓需要校验；
- 穿过零点并在反方向留下新仓位需要校验；
- 普通指令或 `reduce_only` 的纯减仓、完全平仓不做初始保证金拦截；
- 手续费在校验以前进入候选 Fill，必须影响预计钱包和可用余额；
- `projected_available_balance == 0` 可以成交，只有严格小于 0 才失败；
- 同一 Bar 的多条指令继续按稳定键顺序逐条计算，前一条已确认成交会成为后一条的
  当前账户状态。

v1.0 不恢复第 5 节已经删除的交易所拒单生命周期。处理方式固定为：

```text
不生成 SimFill
不修改 Ledger
抛出 InsufficientMarginError
本次 Run 以 FAILED 结束
```

这不是爆仓，也不是策略风控；它只是说明输入的交易指令超出了账户可执行能力。
异常携带完整的预计 `MarginSnapshot`，但 Runtime 不生成伪拒单、拒绝事件或失败 Fill。
这里的“不修改 Ledger”指失败指令本身具有原子性；同一 Bar 中排在它以前且已经确认的
合法成交不会回滚。

网格被动意图的未成交挂单保证金会影响真实平台的可挂单容量，但不直接改变已持仓的
维持保证金和强平线。由于 v1.0 的 Runtime 不拥有交易所订单，本批先在实际产生
`TradeInstruction` 时校验保证金，不模拟挂单创建时的保证金冻结。该限制必须写入
manifest。未来如需研究挂单资金占用，应新增通用保证金预留端口，不得让 Runtime
反向依赖网格意图。

## 9.9 强平与破产判定

正常强平触发条件：

```text
margin_balance_btc <= maintenance_margin_btc
    → LIQUIDATION
```

强平触发早于权益归零。以下条件只作为更严重的账户状态诊断：

```text
margin_balance_btc <= 0
    → BANKRUPTCY
```

正常路径可能先触发 `LIQUIDATION`；只有价格跳空、离散采样或后续强平执行损失使账户
直接越过零点时，才同时记录 `BANKRUPTCY`。

预估强平价格不是独立配置值，而是当前账户状态下方程的解：

```text
margin_balance_btc(P)
    = maintenance_margin_btc(P)
```

其中未实现盈亏和维持保证金都必须使用同一个候选标记价格 `P`。固定费率档位可以使用
闭式公式作为测试基准；分档保证金必须在正确档位中求解，并验证解仍落在该档位的有效
名义区间。不存在正价格解或没有仓位时返回 `None`。

对于当前 COIN-M 反向合约，`N = abs(Q) × C` 是固定 USD 名义价值，不随候选价格
变化。因此先按 `N` 选择档位并得到维持保证金要求 `R`（USD），再直接求解：

```text
estimated_liquidation_price
    = (D × N + R)
    / (W + D × N / E)
```

分母为零、结果非有限或结果不为正数时返回 `None`。例如，某些抵押充分的反向合约
空仓即使价格趋于无穷，其最大 BTC 损失仍小于钱包余额，此时不存在有限正强平价格。
杠杆不直接进入该方程；在钱包、仓位和开仓均价固定时，改变杠杆只改变初始保证金，
不改变强平价格。

本批不允许策略配置关闭平台强平规则。需要运行无杠杆、无强平的现货或数学 probe 时，
显式使用 `NoMarginModel`；一旦选择合约 MarginModel，就必须执行其强平条件。

## 9.10 标记价格与日线采样

未实现盈亏、保证金余额和强平必须使用标记价格语义，不能静默使用成交价。

市场数据应区分：

```text
execution_price
mark_price
```

当前生成行情只有一套 OHLC 时，允许把该 OHLC 作为 mark price proxy，但必须在
manifest 中记录：

```text
mark_price_source = "market_ohlc_proxy"
```

历史平台校准实验应使用目标平台的 mark price K 线。

新开仓保证金预检查同样需要“成交时刻的标记价格”。如果日线数据没有该时刻的独立
mark，只能把当前指令价格作为 `mark_price_at_fill_proxy`；manifest 必须记录这一
近似。提供更细粒度 mark path 后，应使用同一时刻的真实 mark，不再使用成交价代理。

日线 OHLC 无法还原 Bar 内事件顺序，因此提供两种明确的采样策略：

```text
CLOSE_ONLY
    只按 close 判断日终账户状态；确定性强，但可能漏掉盘中强平。

ADVERSE_EXTREME
    多仓使用 low、空仓使用 high 检查盘中是否触及强平线；更保守。
```

如果仓位在整根 Bar 内保持不变，最不利价格跨越强平线即可确认该 Bar 内曾触发强平。
如果同一 Bar 内既有改变仓位的 Fill，又覆盖强平线，而现有 OHLC 无法判断先后，则记录：

```text
INTRABAR_ORDERING_AMBIGUOUS
```

配置可以选择保守终止，但结果必须保留该标志，不得声称获得了精确强平时刻。需要更高
时间精度时，必须提供小时或分钟级 mark price 路径；这不会改变策略可以按日线决策的
事实。

## 9.11 Runtime 协调顺序

每根 Bar 固定为：

```text
1. 获取 MarketFrame 和本 Bar 的 mark price 信息
2. 对进入本 Bar 前已经存在的仓位检查开盘跳空
3. trade_port.instructions_for(current)
4. 按 instruction_key 稳定处理每条指令：
   4.1 reduce_only 校验
   4.2 计算手续费
   4.3 预计成交后的仓位、钱包和初始保证金
   4.4 保证金不足则快速失败
   4.5 生成 SimFill 并 Ledger.apply(fill)
   4.6 重新计算 MarginSnapshot
   4.7 如已触发强平则立即停止处理后续指令
5. 结算本 Bar 应发生的资金费
6. 按配置的 mark price sampling 做 Bar 级强平检查
7. 如触发强平：
   7.1 保存 LiquidationEvent
   7.2 保存最终 EquitySnapshot 和 MarginSnapshot
   7.3 不再调用策略回调
   7.4 终止 Run
8. 未触发强平时调用 trade_port.on_fills()
9. 同步只读意图生命周期
10. 调用 trade_port.on_market()
11. 再次同步只读意图生命周期
12. 保存本 Bar 最终快照
```

资金费必须在最终强平检查以前进入钱包；默认 `ZeroFundingModel` 使该步骤保持零影响。

9E 已同时实现 `CLOSE_ONLY` 和 `ADVERSE_EXTREME`。后一种模式先对进入本 Bar
以前已经存在的仓位使用 open 检查跳空；未触发时，多仓使用 low、空仓使用 high
检查盘中最不利价格。正常完成的 Bar 仍保存 close 快照，只有触发强平时最终快照才
保存实际采用的 open、low 或 high。

当前日线模式对顺序歧义采用固定的保守终止：

- 原仓位在盘中极值触发，同时本 Bar 存在交易指令时，假设极值先于成交，不应用这些
  指令，并标记 `intrabar_ordering_ambiguous=true`；
- 原仓位在极值处安全，但某笔已确认 Fill 形成的新仓位在同一 Bar 极值处触发时，
  保留该 Fill，立即终止后续指令，并标记相同的 ambiguity；
- 开盘本身已经触发时，强平检查先于当日指令，属于确定的开盘跳空，不标记 ambiguity；
- close 触发属于确定的日终状态，同样不标记 ambiguity。

这只是 OHLC 信息下的保守边界，不声称获得了精确盘中路径。若需要判断低点和高点、
多个被动成交以及强平之间的真实顺序，必须提供小时或分钟级 mark price 路径。

## 9.12 强平终止与账户状态

触发强平时：

```text
termination_reason = "LIQUIDATION"
completed = false
liquidated = true
bankrupt = margin_balance_btc <= 0
```

Runtime 必须：

- 立即停止运行；
- 不再向策略发送 `on_fills` 或 `on_market`；
- 不再接受后续交易指令；
- 保存触发强平时的仓位、钱包、盈亏、保证金和 mark；
- 将仍然可见的意图保留为仿真截止时状态，不伪造策略撤单；
- 保证强平结果不能被当作正常到期结果。

第一阶段只要求强平触发点和触发时账户数值准确。日线数据无法给出真实强平订单的最终
成交价，因此第一阶段不虚构普通 `TradeInstruction`、Taker Fill、强平手续费或
“强平后仓位归零”的账本结果。最终仓位字段表示 `position_at_liquidation`，最终权益
表示触发状态下的 mark-to-market 权益，而不是交易所接管完成后的可提现余额。

未来只有在引入独立的 `LiquidationExecutionModel` 并明确执行价格、滑点、交易费用和
强平费用以后，才生成强平结算结果。它不得反向改变本节的强平触发判定。

## 9.13 输出与事件

新增：

```text
MarginSnapshot
LiquidationEvent
```

`LiquidationEvent` 至少记录：

- sequence、timestamp；
- instrument；
- mark price、mark price source 和采样策略；
- position、average entry price；
- position notional；
- wallet balance；
- unrealized PnL；
- margin balance；
- initial margin；
- maintenance margin；
- available balance；
- margin buffer；
- leverage；
- maintenance schedule version；
- 是否同时 bankrupt；
- 是否存在 intrabar ordering ambiguity。

`SimulationResult` 增加：

```text
completed
liquidated
bankrupt
termination_reason
termination_sequence
margin_snapshots
account_events
```

这些都是原始账户事实。强平率、生存率、仓位是否过大以及策略是否安全，均由后续实验
和评价模块计算，不在 Runtime 中给出判断。

9D 先完成 Runtime 内部结果：`SimulationResult` 已保存终止状态、
`margin_snapshots` 和 `account_events`。schema v2、标准 JSON 和 Viewer 尚未增加这些
字段；为避免把强平结果误导性地序列化成普通完成结果，9F 完成以前
`simulation_result_to_document()` 会拒绝强平结果。

## 9.14 数值验证

测试必须分层：

### 公式测试

1. 多仓和空仓 COIN-M 未实现盈亏；
2. 加仓后的反向合约平均成本；
3. 减仓、完全平仓和已实现盈亏；
4. 手续费进入钱包后的保证金余额；
5. 不同杠杆下的初始保证金；
6. 维持保证金固定费率公式；
7. 分档边界、速算扣除额和换档连续性；
8. 强平价格等式两侧相等。

### 不变量测试

1. 杠杆变化不改变盈亏；
2. 现货 BTC 变化不改变合约强平条件；
3. 增加合约钱包余额不能使同一仓位更早强平；
4. 增加绝对仓位不能降低维持保证金；
5. 有利价格变化不能恶化同一仓位的未实现盈亏；
6. 同一输入、配置和市场路径必须得到完全相同结果。

### Runtime 测试

1. 维持保证金之上不终止；
2. 等于维持保证金时触发强平；
3. 低于维持保证金时触发强平；
4. 跳空越过强平价时同时保留破产诊断；
5. 强平后不再执行指令或调用策略；
6. 保证金不足的新开仓快速失败且不生成 Fill；
7. CLOSE_ONLY 和 ADVERSE_EXTREME 行为明确；
8. Bar 内顺序不确定时记录 ambiguity；
9. `NoMarginModel` 保持现有非合约 probe 兼容；
10. JSON、Viewer 和最终状态正确显示强平。

### 外部校准

建立脱敏、版本固定的目标平台 COIN-M 账户样例，比较：

- position notional；
- unrealized PnL；
- margin balance；
- position initial margin；
- maintenance margin；
- available balance；
- estimated liquidation price。

误差阈值按 BTC 结算精度和价格 tick 定义，不使用随意的百分比容差。外部平台规则或
档位变化时新增版本，不覆盖旧实验使用的配置。

## 9.15 第五批验收标准

- COIN-M 多空仓位、盈亏和钱包余额可以独立手算复核；
- 杠杆、初始保证金和可执行容量语义明确；
- 维持保证金由可版本化档位配置计算；
- 现货 BTC 不参与合约强平；
- 平台强平条件满足时 Run 必须终止；
- 强平后不能继续交易或等待行情恢复；
- 保证金不足与强平被明确区分；
- 日线强平采样假设和盘中顺序不确定性可追踪；
- 结果保存触发强平时的完整账户状态；
- Runtime 不包含任何策略主动风控规则；
- 单组和分层跟随网格可以在同一账户配置下运行并得到确定结果；
- 与版本固定的外部账户样例在规定精度内一致。

## 9.16 当前实施进度

9A 已完成纯计算基础：

- 通用 `MarginConfig`、`MarginSnapshot`、`MarginModel` 和 `NoMarginModel`；
- `FlatMaintenanceMarginSchedule`；
- COIN-M `InverseContractMarginModel`；
- `InverseContractLedger` 的只读钱包、仓位和平均开仓价属性；
- 多空盈亏、费用、杠杆、现货隔离和不变量测试；
- 尚未接入 Runner，现有仿真成交和结果保持不变。

9B 已完成分档保证金和强平价格：

- 通用 `MaintenanceMarginTier` 和 `TieredMaintenanceMarginSchedule`；
- 档位边界、速算扣除额、连续性、有限上限和版本来源校验；
- COIN-M 多空仓位强平价格闭式反解；
- 无仓位、无正价格解和抵押资金变化等边界测试；
- 强平价格目前只是 `MarginSnapshot` 中的只读事实，尚不触发 Runtime 行为。

9C 已完成成交前保证金可执行性：

- `MarginModel.projected_snapshot(...)` 产品投影端口；
- Runner 在手续费确定后、真实记账前检查新增敞口；
- COIN-M 账本独立副本投影；
- `InsufficientMarginError` 携带预计保证金快照；
- 保证金不足不生成 Fill、不调用 `on_fills`、不修改真实账本；
- 可用余额等于零、手续费影响、低保证金减仓和穿零反向开仓测试；
- 爆仓计算测试确认 `margin_balance == maintenance_margin` 时触发强平且早于破产；
- 三年示例尚未注入 MarginModel，原有结果保持不变。

9D 已完成 Runner 强平终止闭环：

- 每根 Bar 按 close 和 `market_ohlc_proxy` 保存最终 `MarginSnapshot`；
- 每笔确认 Fill 后立即复算，触发强平后停止同 Bar 后续指令；
- 首帧已经强平时不调用策略初始化；
- 强平 Bar 不调用 `on_fills`、`on_market` 或意图同步；
- 已确认 Fill、触发时仓位、钱包、权益和可见意图截止状态均被保留；
- `SimulationResult` 记录 completed、liquidated、bankrupt、终止原因和 sequence；
- `LiquidationEvent` 记录采样方式、档位版本和完整触发快照；
- 跳空越过零权益时同时保留 bankruptcy 诊断，不虚构强平平仓 Fill；
- JSON/schema/Viewer 输出留给 9F，当前拒绝序列化强平结果；
- 三年示例尚未注入 MarginModel，因此基线结果不变。

9E 已完成日线盘中强平采样：

- Runner 构造参数可选择 `CLOSE_ONLY` 或 `ADVERSE_EXTREME`，默认继续保持
  `CLOSE_ONLY` 兼容；
- 已有仓位先按 open 检查跳空，触发后不再请求当前 Bar 的交易指令；
- 多仓使用 low、空仓使用 high 检查盘中最不利价格；
- 首根 Bar 中预先注入的仓位同样执行开盘和盘中极值检查；
- 收盘恢复不能撤销已经由盘中极值确认的强平；
- 同一 Bar 的成交与极值无法排序时采用保守终止，并保存
  `intrabar_ordering_ambiguous`；
- 强平时最终 EquitySnapshot、MarginSnapshot 和账户指标统一使用实际触发采样价；
- 多仓、空仓、首帧、开盘跳空、收盘恢复和成交冲突均有确定性测试。

9F 当前状态：

9F 已完成可在仓库内实现的输出与校准闭环：

- schema v2 新增 `run_status`、`margin` 和 `account_events`，强平结果不再被伪装为
  普通完成结果；
- 新文档始终输出终止状态和保证金数组，历史 v1/v2 文档由 Viewer 兼容为正常完成、
  无保证金快照；
- Viewer 显示正常/强平/破产状态、每日保证金数值、预估强平价、K 线强平标记和
  触发时完整账户事实；
- 确定性 COIN-M 样例在日线盘中低点触发强平，Run 立即终止且不生成虚构平仓；
- 官方文档空仓样例固定为版本化 fixture，逐字段验证平台字段映射和零值边界；
- 提供需要显式确认的只读采集脚本，只接受非零单仓、全仓、无挂单、无同币种其他
  仓位的账户状态，并使用字段白名单脱敏；
- 合约 USD 名义价值单位与 USDT 权益折算单位已经拆开，避免在校准报告中混用。

非零外部数据验收已经使用 Binance COIN-M Demo 的 AAVEUSD_PERP 全仓多仓和空仓
分别完成。两份脱敏 fixture 均保存 1 张、10 USD 合约面值、10 倍杠杆和第一档 2.5%
维持保证金率；离线测试逐项比较仓位名义价值、未实现盈亏、保证金余额、初始保证金、
维持保证金、可用余额和预估强平价，全部通过按结算币精度和价格 tick 定义的绝对
误差。空仓样例同时确认：当抵押资金大于反向合约最大可能亏损时，Binance 的强平价
`0` 与 Runtime 的 `None/null` 表达同一个“无有限正数强平价”事实。采集器还会拒绝
平台字段在顺序请求期间刷新错位的快照。

因此 9F 与第五批最后一条“非零平台数值校准通过”均已验收。正式接入生产 Binance
以前仍建议再保存一份生产端版本化快照，用于识别 Demo 与生产档位配置差异；这属于
上线前平台版本复核，不再是当前 Runtime 公式实现的缺口。

---

# 10. 第六批：滑点模型

## 10.1 目标

为主动成交提供可配置的确定性价格偏差模型。当前仍不虚构强平平仓 Fill，也不为
被动网格引入成交概率、负向选择或盘口冲击。

## 10.2 SlippageModel

建议接口：

```python
class SlippageModel(Protocol):
    @property
    def enabled(self) -> bool:
        ...

    @property
    def source(self) -> str:
        ...

    def apply(
        self,
        instruction: TradeInstruction,
        reference_price: Decimal,
        frame: MarketFrame,
    ) -> Decimal:
        ...
```

## 10.3 第一版实现

提供：

```text
NoSlippageModel
FixedBpsSlippageModel
```

`NoSlippageModel` 是默认模型：

```text
slippage_enabled = false
slippage_source = ZERO
```

`FixedBpsSlippageModel` 接受非负且小于 10,000 的 `slippage_bps`，只对 ACTIVE
指令施加不利方向偏差：

BUY：

```text
effective_price
=
reference_price × (1 + bps / 10000)
```

SELL：

```text
effective_price
=
reference_price × (1 - bps / 10000)
```

## 10.4 应用范围

默认：

```text
NoSlippageModel → ACTIVE / PASSIVE 都保持参考价
FixedBpsSlippageModel → ACTIVE 使用固定 bps
FixedBpsSlippageModel → PASSIVE 保持指定触价
```

Runtime 的固定顺序是：

```text
TradeInstruction.price 作为 reference_price
→ SlippageModel 得到有效 price
→ FeeModel 按有效 price 计算手续费
→ 保证金预检查
→ Ledger 入账
```

后续才考虑被动成交的价格改善、负向选择以及强平执行价格。

## 10.5 Fill 字段

增加：

- `reference_price`
- `slippage_amount`
- `slippage_bps`
- `price` 继续表示最终有效成交价。

其中：

```text
slippage_amount = price - reference_price
slippage_bps = slippage_amount / reference_price × 10000
```

因此 BUY 的不利滑点为正，SELL 的不利滑点为负。旧 Fill 和旧 JSON 缺少这些字段时，
按 `reference_price = price`、滑点为零兼容。

## 10.6 第六批测试

必须增加：

1. BUY 主动指令正向加价；
2. SELL 主动指令负向减价；
3. PASSIVE 默认无滑点；
4. 零滑点与旧结果一致；
5. 手续费按最终有效成交价计算；
6. JSON、schema 和 Viewer 能读取新字段及旧零滑点文档；
7. 网格样例在默认模型下保持原结果。

## 10.7 第六批验收标准

- 滑点可配置；
- 成交参考价与有效价可追踪；
- 费用计算基于有效价；
- 网格策略默认限价单结果不受影响。

当前状态：以上最小滑点范围已实现。盘口冲击、随机滑点、被动成交价格改善和强平
执行模型不属于 v1。

---

# 11. 第七批：资金费

## 11.1 目标与本批边界

本批只实现永续合约资金费的确定性结算和入账，使长期仿真的钱包、权益、保证金与
强平结果包含资金费影响。

本批不负责：

- 预测未来资金费率；
- 根据牛熊环境生成资金费率；
- 模拟多空比、持仓量或溢价指数；
- 复刻交易所资金费率形成公式；
- 在日线 OHLC 内猜测不可见的盘中结算与成交顺序。

市场条件化资金费生成属于后续策略精细化优化。当前先保证：

> 给定结算时间、最终费率、仓位和 mark 后，资金费必须算对并进入正确的钱包。

## 11.2 资金费符号

统一使用 `wallet_delta` 表示资金费对结算钱包的有符号影响：

```text
wallet_delta > 0
    账户收到资金费

wallet_delta < 0
    账户支付资金费
```

正资金费率下：

- 多头支付；
- 空头收取。

负资金费率下方向相反。没有仓位或费率为零时不生成资金费事件。

## 11.3 FundingModel 与 FundingSettlement

Runtime 使用只计算、不修改账本的端口：

```python
class FundingModel(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def source(self) -> str: ...

    @property
    def market_conditioned(self) -> bool: ...

    def settle(
        self,
        frame: MarketFrame,
        ledger: SimulationLedger,
        marks: Mapping[str, Decimal],
    ) -> FundingSettlement | None:
        ...
```

`FundingSettlement` 至少记录：

- `settlement_id`；
- `sequence`、`timestamp`；
- `instrument`、`source`；
- `funding_rate`；
- 结算时带方向仓位 `position_quantity`；
- `mark_price` 和 `mark_price_source`；
- `position_notional` 和 `notional_asset`；
- 以结算币表示的 `position_value`；
- `settlement_asset`；
- 有符号 `wallet_delta`。

模型返回事件后，由 Runtime 调用：

```text
ledger.apply_funding(settlement)
```

从而保持“费率与产品公式计算”和“钱包记账”职责分离。

## 11.4 第一阶段模型

提供：

```text
ZeroFundingModel
FixedRateFundingModel
FixedRateInverseContractFundingModel
```

`ZeroFundingModel` 是默认模型，保持既有结果兼容：

```text
funding_enabled = false
funding_source = ZERO
funding_market_conditioned = false
```

固定费率模型用于确定性测试和敏感性实验：

- `funding_interval_seconds`；
- `settlement_offset_seconds`；
- `funding_rate`；
- 线性产品可选 `funding_asset`。

固定周期只在 Runtime 已经看见的 `MarketFrame.timestamp` 上判断是否结算，不会为两个
日线 Bar 之间虚构盘中结算点。v1 明确采用日级汇总资金费假设，每根日线至多结算一次；
8 小时资金费事件不属于当前阶段的待办。未来只有在研究精度确实需要时，才通过更细的
账户执行帧扩展，而不改变策略仍可按日线决策的边界。

## 11.5 线性合约公式

设：

```text
Q = 带方向仓位数量，多头为正，空头为负
M = 结算 mark
R = 最终资金费率
D = sign(Q)
```

则：

```text
position_notional_quote = abs(Q) × M

wallet_delta_quote
    = -D × position_notional_quote × R
```

## 11.6 COIN-M 反向合约公式

设：

```text
Q = 带方向合约张数
C = 每张合约固定 USD 面值
M = 资金费结算 mark，单位 USD / base
R = 最终资金费率
D = sign(Q)
```

则：

```text
position_notional_usd
    = abs(Q) × C

position_value_base
    = position_notional_usd / M

wallet_delta_base
    = -D × position_value_base × R
```

资金费只修改合约钱包，不修改长期现货底仓。

## 11.7 账本与盈亏字段

`SimulationLedger` 增加：

```text
total_funding
net_pnl_after_fees_and_funding
apply_funding(settlement)
```

其中：

```text
total_funding
    = sum(funding_event.wallet_delta)

net_realized_pnl
    = gross_realized_pnl - total_fees

net_pnl_after_fees_and_funding
    = net_realized_pnl + total_funding
```

既有 `net_realized_pnl` 和 `realized_pnl` 继续表示交易盈亏扣除手续费，不把资金费
悄悄混入旧字段。资金费通过新增字段单独呈现。

COIN-M 合约钱包：

```text
futures_wallet_base
    = initial_futures_wallet_base
    + gross_realized_pnl_base
    - total_fees_base
    + total_funding_base
```

## 11.8 Runtime 顺序

对进入 Bar 前已经存在的仓位，先执行现有开盘跳空和盘中最不利价格检查。未触发强平
时，本 Bar 后续顺序为：

```text
交易指令
→ 手续费
→ Fill 入账
→ 资金费结算
→ 使用结算后钱包重新计算 MarginSnapshot
→ 强平/破产检查
→ 策略回调
→ EquitySnapshot
```

如果资金费导致 `margin_balance <= maintenance_margin`，Runtime 立即记录
`LiquidationEvent` 并终止，不调用成交通知和后续策略回调。

首根 Bar 如果由调用方传入已有仓位，也会在通过初始强平检查后按资金费模型结算，
随后重新检查保证金。

## 11.9 输出

`SimulationResult`、逐 Bar `EquitySnapshot` 和 JSON `summary` 增加：

- `total_funding`；
- `net_pnl_after_fees_and_funding`。

JSON 增加：

- `manifest.funding_enabled`；
- `manifest.funding_source`；
- `manifest.funding_market_conditioned`；
- 顶层 `funding_events`；
- `summary.funding_event_count`。

Viewer 将 `total_funding` 按“资金费净入账”展示：正数为收到，负数为支付。

## 11.10 第七批测试与验收

必须验证：

1. 线性多头支付正资金费；
2. 线性空头收到正资金费；
3. 负资金费方向相反；
4. 无仓位、零费率和非结算时间不生成事件；
5. COIN-M 多空方向、USD 面值和 BTC 结算单位正确；
6. 资金费只进入合约钱包，不改变现货底仓；
7. 资金费进入钱包后可以触发强平或破产诊断；
8. JSON 和 schema 正确导出完整事件；
9. 默认零资金费与旧结果一致。

当前状态：以上资金费入账范围已实现。真实历史资金费回放、条件抽样和市场状态模型
明确留到策略优化精细化阶段。

---

# 12. 执行记录规格

## 12.1 SimulationRun 顶层

建议扩展：

```text
schema_version
manifest
market
intents
instructions
fills
events
equity
summary
```

## 12.2 IntentRecord

至少包含：

- intent_key
- instrument
- intent_mode
- side
- quantity
- target_price
- reduce_only
- active_from_sequence
- active_to_sequence
- status
- tags

## 12.3 Fill

至少包含：

- fill_id
- instruction_key
- source_intent_key
- intent_mode
- instrument
- sequence
- timestamp
- side
- quantity
- reference_price
- price
- slippage_amount
- slippage_bps
- liquidity_role
- fee_rate
- fee_amount
- fee_asset
- reduce_only
- tags

## 12.4 Event

当前输出按不同会计语义分开：

- `account_events` 保存 `LiquidationEvent`；
- `funding_events` 保存 `FundingSettlement`。

不使用只有 `message` 和通用 `metrics` 的弱类型事件代替强平快照或资金费单位字段。
未来增加统一事件流时，可以在保持这两类强类型载荷的前提下增加事件 envelope。

## 12.5 EquitySnapshot

建议包含：

- cash
- positions
- average_costs
- marks
- gross_realized_pnl
- total_fees
- total_funding
- net_realized_pnl
- net_pnl_after_fees_and_funding
- unrealized_pnl
- equity
- wallet_balance
- position_notional
- initial_margin
- maintenance_margin
- available_balance
- margin_buffer
- initial_margin_utilization
- maintenance_margin_utilization
- effective_leverage
- account_metrics

通用 Schema 允许产品账本通过 `account_metrics` 补充字段。

## 12.6 Summary

建议包含：

- initial_equity
- final_cash
- final_positions
- final_average_costs
- gross_realized_pnl
- total_fees
- total_funding
- net_realized_pnl
- net_pnl_after_fees_and_funding
- unrealized_pnl
- final_equity
- completed
- bankrupt
- liquidated
- termination_reason
- termination_sequence
- final_account_metrics

---

# 13. SimulationRunner 推荐协调顺序

完成全部 v1.0 能力后，每根 Bar 推荐执行顺序如下：

```text
1. MarketSource.next()
2. 更新当前 mark
3. 调用 `trade_port.instructions_for(current)` 获取当前帧显式指令
4. 按 `instruction_key` 稳定处理每条指令：
   4.1 reduce_only 合法性检查
   4.2 计算 reference price
   4.3 应用 SlippageModel
   4.4 计算 FeeModel
   4.5 MarginModel 预计成交后的账户保证金
   4.6 保证金不足则快速失败
   4.7 生成 SimFill
   4.8 Ledger.apply(fill)
   4.9 重新计算 MarginSnapshot
5. FundingModel 结算
6. MarginModel 计算账户状态并检查强平条件
7. 若触发 BANKRUPTCY / LIQUIDATION：
   7.1 保存 LiquidationEvent
   7.2 保留截至终止时的意图生命周期状态
   7.3 保存账户与保证金快照
   7.4 保存最终 Snapshot
   7.5 终止运行
8. 调用 `trade_port.on_fills()`
9. 读取只读意图快照并更新生命周期
10. 调用 `trade_port.on_market()`
11. 再次读取只读意图快照
12. 保存 EquitySnapshot
```

注意：

- Fill 后生成的新意图不得参与当前 Bar；
- 强平事件发生后不得继续调用策略；
- 资金费必须先于最终保证金与强平检查；
- 期末处理在所有 MarketFrame 完成后执行。

---

# 14. 模块边界建议

建议新增或演进为：

```text
simulation_runtime/
├── models.py
├── trade.py
├── trace.py
├── ledger.py
├── fees.py
├── slippage.py
├── funding.py
├── margin.py
├── ending.py
├── events.py
├── runner.py
└── reporting.py
```

职责：

```text
trade.py
    定义 Runtime 唯一策略交易端口

trace.py
    定义只读意图报告端口

fees.py
    只处理手续费

slippage.py
    只处理成交价格偏差

funding.py
    只处理资金费

margin.py
    只处理杠杆、保证金、账户约束和强平判断

ending.py
    只处理期末策略

ledger.py
    只处理账户会计

runner.py
    只负责编排
```

不得把全部逻辑堆入 `SimulationRunner.run()`。

---

# 15. 不在 v1.0 范围内

Codex 不得自行扩展以下内容：

- 盘口深度；
- Level 2 order book；
- 撮合队列；
- 订单成交概率；
- 成交量限制；
- 部分成交；
- 冰山单；
- 止损单；
- 止盈市价单；
- 自动联网同步 Binance 保证金阶梯；
- 多资产组合保证金和 Portfolio Margin；
- 保险基金；
- ADL；
- 网络延迟；
- API 错误；
- SQLite 持久化；
- Web 管理页面；
- RL；
- 参数优化；
- 批量实验框架；
- 指标系统。

这些属于后续阶段。

---

# 16. 分批执行顺序

Codex 应按以下顺序逐批执行：

```text
批次 1：reduce_only 与平仓合法性
批次 2：主动/被动意图与显式交易指令
批次 3：期末状态快照契约
批次 4：手续费
批次 5：合约账户、杠杆、保证金与强平
批次 6：滑点
批次 7：资金费
```

每个批次都必须：

1. 先检查当前代码；
2. 保持公开 API 尽量兼容；
3. 增加单元测试；
4. 更新 README 或规格文档；
5. 运行完整测试；
6. 运行确定性 probe；
7. 运行单组跟随网格样例；
8. 运行分层跟随网格样例；
9. 汇报改动文件；
10. 汇报仍未实现的内容。

---

# 17. v1.0 最终验收标准

当以下条件全部满足时，“1. 仿真执行”可以进入 v1.0 完成状态：

## 17.1 意图与指令

- 支持 PASSIVE 与 ACTIVE 意图解析；
- 支持 reduce_only；
- 平仓不会意外反向开仓；
- 非法平仓意图会在生成 Fill 前明确失败；
- 意图结束状态完整。

## 17.2 成交

- 新意图不能回看当前 Bar；
- PASSIVE 覆盖触发正确；
- PASSIVE 跳空不覆盖时不成交；
- ACTIVE 在约定的下一根 open 成交；
- 成交顺序确定；
- 滑点可配置。

## 17.3 账本

- 加仓正确；
- 减仓正确；
- 完全平仓正确；
- 反向开仓规则明确；
- LinearLedger 与 COIN-M 账本各自的会计语义正确；
- LinearLedger 不被误当作 U 本位合约账本；
- 已实现与未实现盈亏正确。

## 17.4 成本

- Maker/Taker 手续费正确；
- 手续费资产明确；
- 资金费可配置；
- 毛收益、费用、净收益可区分。

## 17.5 保证金与强平

- COIN-M 杠杆、初始保证金和维持保证金计算正确；
- 现货 BTC 不参与合约强平；
- 保证金指标和档位版本可追踪；
- MarginModel 启用时强平规则不可由策略关闭；
- 满足强平条件后立即停止；
- 强平后不继续交易；
- 保证金不足与强平明确区分；
- 终止原因明确。

## 17.6 期末

- 仿真结束不生成额外指令或 Fill；
- 保留最后的现金、仓位、成本和意图状态；
- 等待中的意图保持 WAITING；
- 按最后的市场 mark 计算最终权益；
- 最终权益语义明确。

## 17.7 输出

- Intent、Instruction、Fill、Event、Equity、Summary 字段完整；
- Viewer 可读取；
- 旧 JSON 可通过兼容逻辑读取，或明确升级 schema_version；
- 所有配置写入 manifest。

## 17.8 测试

- 所有单元测试通过；
- 确定性 probe 通过；
- 单组跟随网格样例通过；
- 分层跟随网格样例通过；
- 零费用、零滑点、NoMarginModel 下，结果尽量与旧实现一致；
- 强平样例必须在首次满足强平条件的位置停止。

## 17.9 当前状态

本文定义的“1. 仿真执行”v1.0 范围已经完成。批次 1–7 的公开接口、账本顺序、
结构化输出和确定性测试均已落地；确定性 Probe、单组跟随网格、分层跟随网格以及
COIN-M 强平和外部数值校准均已通过。

这里的完成只针对本文已经收敛的 v1 边界。盘口深度、部分成交、随机或市场条件化
滑点、强平平仓执行、8 小时资金费事件、U 本位合约保证金和第 15 节列出的其他项目，
仍明确属于后续扩展，不构成模块一 v1.0 的未完成项。

---

# 18. Codex 每批任务输出格式

Codex 完成每一批后，应按以下格式汇报：

```text
## 本批完成内容

## 修改文件

## 核心设计决定

## 新增或修改的公开接口

## 新增测试

## 测试结果

## 示例运行结果

## 向后兼容性

## 当前仍未实现

## 下一批建议
```

---

# 19. 最终说明

本文只定义“1. 仿真执行”。

以下模块暂不在本文范围内：

```text
2. 实验系统
3. 评价指标
4. 市场环境
5. 策略体系
6. 策略优化
7. 结果验证
```

完成本文 v1.0 后，再开始建设实验系统，避免在交易成本、平仓、保证金、强平和期末处理
仍不明确时进行大规模策略实验。
