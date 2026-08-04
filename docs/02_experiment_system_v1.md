# 第 2 部分：实验系统 v1.0 实现规格

> 2026-08 架构迁移说明：本文保留第二阶段落地过程中的路径和名称作为历史记录。
> 当前网格策略、策略插件和实验入口均归属 `strategies_system`；
> 策略实验使用的市场、执行和账户组件定义也已迁至
> `strategies_system/strategy_simulation/components`。`grid_trading` 仅保留
> `grid_rule` 和实盘服务，不再作为策略实验宿主。

## 1. 文档目的

本文用于规划策略仿真研究平台的第二个模块：实验系统。

当前 `market_simulator` 已经能够完成一条市场路径上的单次仿真，
`grid_trading` 也已经有单组跟随网格、分层跟随网格等可运行脚本。
但这些脚本分别承担了配置、对象构造、执行、结果补充和文件写入职责，
还不能稳定地回答下面这些研究问题：

```text
这次实验用了什么配置？
同一配置在多个随机种子下表现如何？
哪些市场、策略和参数被组合过？
某个结果能否按原配置和代码版本复现？
多个 Run 的结果如何集中查看和比较？
```

实验系统的目标是把这些工作标准化。

本文最初用于确认设计边界；第 18 节现在同时记录各批次的实际落地结果。
实验系统 v1.0 已于 2026-07-31 完成，后续变更应继续遵守本文的模块边界。

---

## 2. 当前实现基线

### 2.1 已具备

`market_simulator` 当前已经具备：

- `MarketSource` 协议；
- 固定行情与 Anchored GBM 行情；
- `SimulationRunner`；
- 主动与被动交易意图；
- 成交、手续费、资金费、账本、保证金、强平与终止；
- 完整 `SimulationRun` 结果；
- Viewer JSON 与可视化回放；
- 确定性执行测试。

`grid_trading` 当前已经具备：

- 网格规则；
- 单组跟随网格策略；
- 分层跟随网格策略；
- 网格策略到 `simulation_runtime` 的适配器；
- COIN-M 账本、手续费、资金费和保证金适配器；
- 若干可独立执行的仿真脚本。

### 2.2 当前不足

现有仿真脚本普遍同时负责：

```text
读取或声明参数
    ↓
构造 MarketSource
    ↓
构造策略和产品适配器
    ↓
构造 SimulationRunner
    ↓
执行单次 Run
    ↓
补充策略专用汇总字段
    ↓
直接写入 Viewer JSON
```

因此目前存在以下问题：

- 配置分散在 Python 脚本中；
- 每增加一类实验都容易复制一份脚本；
- 多 Seed、参数组合和场景组合没有统一入口；
- 输出文件名不能完整代表实验配置；
- 缺少统一的运行状态、失败记录和结果索引；
- 很难判断两个结果是否来自相同配置和相同代码；
- Viewer 数据目录同时承担示例数据和实验结果目录职责；
- 比较多个结果仍需手工读取 JSON。

---

## 3. 模块目标

实验系统负责：

> 规范地定义、展开、执行、记录和比较仿真实验。

它回答的是：

```text
在什么配置下，
运行了哪些市场、策略、执行模型和账户模型，
使用了哪些随机种子，
每个 Run 是否成功，
产生了哪些原始汇总结果。
```

v1.0 必须支持：

- 单次运行；
- 市场、策略、执行和账户场景组合；
- 多随机种子；
- 显式参数列表和参数网格；
- 运行前展开与数量预览；
- 运行状态和错误记录；
- Summary 与非市场 Trace 统一入库；
- Trace 保留、归档和清理状态；
- SQLite 结果索引；
- 按需导出的 CSV 基础比较表；
- 按配置和代码版本复现运行。

---

## 4. 核心设计原则

### 4.1 实验系统只负责编排

实验系统不得实现：

- 具体市场路径生成算法；
- 具体交易策略；
- 网格规则；
- 成交公式；
- 手续费、资金费、保证金或强平公式；
- 收益和风险指标语义；
- 参数优劣判断；
- 自动参数优化。

这些能力分别属于市场环境、策略体系、仿真执行、评价指标和策略优化模块。

### 4.2 不复制仿真执行能力

实验系统不新增第二套成交或账本引擎。

每个 Run 最终仍应调用：

```text
MarketSource
    ↓
SimulationRunner
    ↕
策略 TradePort / TracePort
    ↓
Ledger / Fee / Funding / Margin
```

单次运行不是独立于批量系统的另一套实现。
它只是展开后仅包含一个 `RunSpec` 的实验。

### 4.3 通用层不得依赖网格代码

建议将通用实验系统放入 `market_simulator`：

```text
market_simulator
├── market_protocol
├── market_simulator
├── simulation_runtime
├── experiment_system
└── viewer
```

依赖方向必须保持为：

```text
experiment_system
    ├── 使用 market_protocol
    └── 使用 simulation_runtime

grid_trading
    ├── 实现网格市场/策略/账户构造器
    └── 将这些构造器注册给 experiment_system
```

禁止：

```text
market_simulator/experiment_system
    → import grid_trading
```

这样以后 RSI、趋势、套利或其他策略应用都能使用同一套实验编排能力。

### 4.4 可复现优先于吞吐量

同一组：

```text
已解析配置
+ Seed
+ 参与运行的代码版本
```

必须能够得到相同结果。

v1.0 先使用单进程顺序执行。并行执行会引入日志交错、资源竞争、
中断恢复和确定性等额外问题，等顺序批量运行稳定后再增加。

### 4.5 原始事实与评价结论分离

实验系统可以保存和排列已有结果字段，但不定义：

- 最大回撤如何计算；
- Sharpe、Sortino 等指标如何计算；
- BTC 本位和 USDT 本位结果如何加权；
- 哪个策略“最好”；
- 多目标评分如何形成。

这些属于第三部分“评价指标”。

---

## 5. 核心对象

### 5.1 ExperimentSpec

`ExperimentSpec` 是用户提交的实验定义，描述：

- 实验名称和说明；
- 一个或多个场景组；
- 随机种子；
- 结果数据库位置；
- 默认 Trace 保留级别；
- 批次安全限制。

每个场景组可以包含候选市场、策略、执行配置、账户配置和参数轴，
因此一个 `ExperimentSpec` 不一定只对应单次运行。

### 5.2 Scenario

`Scenario` 是一个不含随机种子的完整场景组合：

```text
一个市场配置
+ 一个策略配置
+ 一个执行配置
+ 一个账户配置
+ 一组已确定的参数值
```

同一 `Scenario` 可以在多个 Seed 下运行。

`scenario_id` 应由不含 Seed 的已解析配置确定，用于对多 Seed 结果分组。

### 5.3 RunSpec

`RunSpec` 是已经完全展开、可以直接执行的一次运行配置：

```text
Scenario
+ 一个 Seed
```

`RunSpec` 中不得再存在：

- 参数范围；
- 参数列表；
- 随机选择；
- 未解析默认值；
- 对其他配置片段的引用。

### 5.4 RunRecord

`RunRecord` 记录一次运行的生命周期：

```text
run_id
experiment_id
scenario_id
configuration_hash
seed
status
started_at
finished_at
duration
code_revisions
trace_state
retention_class
error
```

它描述“运行发生了什么”。完整非市场 Trace 通过 `run_id`
关联到同一 SQLite 数据库中的 Payload 记录。

### 5.5 ExperimentManifest

`ExperimentManifest` 是整个实验的固定快照，包括：

- 原始 `ExperimentSpec`；
- 展开的 Run 数量；
- 实验创建时间；
- 实验系统 schema 版本；
- 参与运行的代码版本；
- 运行顺序；
- 汇总状态。

开始运行后不得原地修改原始配置快照。

---

## 6. 标准实验配置

### 6.1 v1.0 使用 JSON

v1.0 建议只接受标准 JSON：

- 与现有仿真结果格式一致；
- Python 标准库即可读取；
- 不新增 YAML 依赖；
- 便于规范化、哈希和精确保存；
- 避免 YAML 隐式类型转换。

以后可以增加 YAML 输入适配器，但内部仍先转为同一个 `ExperimentSpec`。

所有需要保持十进制精度的数值使用字符串：

```json
{
  "initial_price": "60000",
  "maker_fee_rate": "0.0002",
  "order_coin_quantity": "0.001"
}
```

Seed、数量上限、步数等严格整数仍使用 JSON integer。

### 6.2 配置结构草案

```json
{
  "schema_version": "experiment-spec/v1",
  "experiment_id": "single-following-grid-baseline",
  "description": "单组跟随网格在三年 Anchored GBM 行情上的基线实验",
  "scenario_groups": [
    {
      "key": "coinm-following-grid",
      "run_provider": "grid-simulation/v1",
      "markets": [
        {
          "key": "btc-3y-base",
          "type": "anchored-gbm/v1",
          "parameters": {
            "instrument": "BTCUSD_PERP",
            "anchors": [
              {"date": "2026-01-01", "price": "65000"},
              {"date": "2026-07-01", "price": "40000"},
              {"date": "2027-01-01", "price": "115000"},
              {"date": "2027-07-01", "price": "55000"},
              {"date": "2028-01-01", "price": "200000"},
              {"date": "2028-07-01", "price": "45000"},
              {"date": "2029-01-01", "price": "160000"}
            ],
            "annual_volatility": "0.60",
            "price_floor": "40000",
            "price_ceiling": "200000",
            "interval": "1d"
          }
        }
      ],
      "strategies": [
        {
          "key": "single-following-base",
          "type": "single-following-grid/v1",
          "parameters": {
            "strategy_id": "single-following-grid-coinm-long-3y",
            "grid_id": "grid-rule-coinm-long-3y",
            "instrument": "BTCUSD_PERP",
            "mode": "long",
            "anchor_price": "65000",
            "grid_ratio": "0.04",
            "grid_count": 5,
            "move_grid": true,
            "market_type": "coinm",
            "order_coin_quantity": "0.01",
            "contract_size": "100"
          }
        }
      ],
      "executions": [
        {
          "key": "daily-passive",
          "type": "daily-bar-execution/v1",
          "parameters": {
            "maker_fee_rate": "0.0002",
            "taker_fee_rate": "0.0005",
            "fee_asset": "BTC",
            "funding_model": "none"
          }
        }
      ],
      "accounts": [
        {
          "key": "coinm-long",
          "type": "coinm-inverse/v1",
          "parameters": {
            "instrument": "BTCUSD_PERP",
            "contract_size": "100",
            "spot_btc": "1",
            "futures_wallet_btc": "0.1",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "notional_asset": "USD",
            "margin_model": "none"
          }
        }
      ],
      "parameter_axes": [
        {
          "path": "/strategy/parameters/order_coin_quantity",
          "values": ["0.001", "0.002"]
        }
      ]
    }
  ],
  "seeds": [42, 43, 44],
  "output": {
    "root": "experiment_results",
    "default_retention_class": "standard"
  },
  "controls": {
    "max_runs": 100,
    "continue_on_error": true
  },
  "metadata": {
    "owner": "research",
    "purpose": "baseline"
  }
}
```

以上字段名已经由 2C Provider 落地。单 Run 的固定等价样例见
`grid_trading/experiments/single_following_grid_baseline.json`；这里保留参数轴和
多 Seed，用于说明后续批量展开结构。

### 6.3 场景组内的组件候选列表

一个实验可以包含多个 `scenario_groups`。

每个场景组内部的 `markets`、`strategies`、`executions`、`accounts`
都是候选列表，只在当前场景组内做笛卡尔积：

```text
场景组 A
    markets
    × strategies
    × executions
    × accounts
    × parameter_axes

场景组 B
    markets
    × strategies
    × executions
    × accounts
    × parameter_axes
```

不同场景组之间不互相组合，其展开结果只做合并：

```text
Experiment Runs
    = 场景组 A 的 Runs
    + 场景组 B 的 Runs
    + ...
```

例如可以把 COIN-M 网格放在场景组 A，把 U 本位 RSI 放在场景组 B。
两组各自组合市场和策略，不会错误地产生“COIN-M 策略 × U 本位账户”。

如果一个实验只有一个场景组，每类组件都只有一个候选项，
且没有参数轴和多个 Seed，就得到单次运行。

Provider 必须在运行前继续校验同一场景组内展开出的组件是否兼容。

### 6.4 参数路径

参数轴使用 JSON Pointer 路径，例如：

```text
/strategy/parameters/order_coin_quantity
/market/parameters/annual_volatility
/account/parameters/leverage
```

不使用点分路径，避免配置键本身包含 `.` 时产生歧义。

v1.0 参数轴只接受显式 `values`，所有轴做笛卡尔积。

暂不在实验系统内实现：

- 连续分布随机采样；
- 贝叶斯优化；
- 遗传算法；
- 强化学习动作；
- 根据前一批结果自动生成下一批参数。

这些参数仍可由外部工具生成，再提交为显式 `ExperimentSpec`。

### 6.5 显式默认值

用户配置可以省略由 Provider 声明的默认值，但进入 `RunSpec` 前必须全部展开。

配置哈希基于包含默认值的已解析配置计算，防止 Provider 默认值变化后，
旧配置看似相同但实际行为不同。

### 6.6 预检

任何 Run 开始前，实验系统必须完成：

1. schema 校验；
2. Provider 是否存在；
3. 组件类型是否存在；
4. 参数类型与必填项校验；
5. 市场、策略、执行和账户兼容性校验；
6. 参数轴路径校验；
7. 展开数量计算；
8. 重复配置检测；
9. `max_runs` 上限校验；
10. 输出目录可写性校验。

v1.0 采用“整体预检”：

只要任何展开后的 `RunSpec` 无效，就不启动整个实验。
这样不会在批次执行到一半后才发现配置组合错误。

---

## 7. Provider 与注册机制

### 7.1 目的

实验 JSON 只能描述“使用什么”，不能直接序列化 Python 对象。
因此需要由宿主应用把稳定的类型名称映射为实际构造器。

例如：

```text
anchored-gbm/v1
    → AnchoredGBMMarketSource 构造器

single-following-grid/v1
    → SingleFollowingGridSimulationAdapter 构造器

coinm-inverse/v1
    → COIN-M Ledger/Fee/Funding/Margin 构造器
```

### 7.2 禁止任意 import 路径

配置中不得接受：

```json
{
  "class": "some.module.ArbitraryClass"
}
```

也不得执行配置中提供的 Python 表达式。

v1.0 只允许使用代码显式注册的稳定类型名称。
这样能避免任意代码执行，也能在类型重命名时保留版本兼容层。

### 7.3 建议协议

通用层建议定义：

```python
class ExperimentRunProvider(Protocol):
    provider_id: str

    def resolve(self, run_spec: RunSpec) -> RunSpec:
        ...

    def validate(self, run_spec: RunSpec) -> None:
        ...

    def prepare(self, run_spec: RunSpec) -> PreparedRun:
        ...
```

`PreparedRun` 对实验系统暴露：

```python
class PreparedRun(Protocol):
    def execute(self) -> SimulationRun:
        ...

    def summarize(self, result: SimulationRun) -> Mapping[str, object]:
        ...
```

实验系统负责生命周期和持久化；
Provider 负责把配置组装为现有 `SimulationRunner` 和适配器。

### 7.4 Provider 的仓库归属

通用协议和注册表：

```text
market_simulator/packages/experiment_system
```

通用市场构造器：

```text
market_simulator/packages/experiment_system
或对应 market_simulator 包中的适配模块
```

策略专用 Provider 与具体实验组件：

```text
strategies_system/src/strategy_simulation/
├── experiment_provider/
└── components/
```

策略 Provider 可以通过 Adapter 依赖 `grid_rule` 和 `simulation_runtime`，
但通用实验系统不能反向依赖任何具体策略工程。

### 7.5 策略专用汇总

Provider 可以补充策略专用的原始事实，例如：

- 网格创建次数；
- 网格复位次数；
- 各层成交次数；
- 最终网格状态。

这些字段必须放入命名空间：

```json
{
  "provider_summary": {
    "grid-simulation/v1": {
      "reset_count": 12
    }
  }
}
```

Provider 不得覆盖通用运行结果字段，也不得在此处输出“策略得分”。

---

## 8. 场景与批量展开

### 8.1 确定性顺序

展开顺序必须稳定：

```text
scenario_group 声明顺序
→ market 声明顺序
→ strategy 声明顺序
→ execution 声明顺序
→ account 声明顺序
→ parameter_axes 声明顺序
→ seed 声明顺序
```

同一配置文件每次预览和运行都应得到相同的 Run 顺序。

### 8.2 多随机种子

Seed 是 `RunSpec` 的一部分，并传给实际使用随机性的组件。

Provider 必须保证：

- 同一 Seed 产生同一市场路径；
- 策略若使用随机数，也从该 Run 的确定性随机上下文派生；
- 不使用模块级共享随机状态；
- 不依赖其他 Run 的执行顺序。

如未来一个 Run 需要多个独立随机流，应由根 Seed 派生具名子 Seed，
例如 `market`、`strategy` 和 `execution`，而不是各组件随意取系统时间。

### 8.3 重复配置

展开后若两个 `RunSpec` 的规范配置完全相同，应在预检阶段报错。

不静默重复执行，也不静默去重，因为重复通常表示实验配置写错。

### 8.4 数量保护

运行前必须显示：

```text
场景数量
Seed 数量
Run 总数
默认 Trace 保留级别
SQLite 输出位置
```

当总数超过 `controls.max_runs` 时拒绝执行。

`max_runs` 是防止场景组内部无意生成巨大笛卡尔积的硬限制，
不是优化器的预算管理功能。

---

## 9. 标识、哈希与代码版本

### 9.1 experiment_id

`experiment_id` 由用户提供，要求：

- 在同一输出根目录中唯一；
- 只使用小写字母、数字、`-` 和 `_`；
- 用于人类识别，不参与结果正确性判断。

### 9.2 configuration_hash

配置哈希使用：

```text
已解析 RunSpec
→ 规范 JSON
→ UTF-8
→ SHA-256
```

规范 JSON 至少要求：

- 对象键排序；
- 无无意义空白；
- Decimal 保留为规范字符串；
- 列表顺序保留；
- 不包含运行时间和输出绝对路径。

以下内容进入哈希：

- 市场、策略、执行、账户的类型与完整参数；
- Seed；
- 影响结果的运行选项；
- Provider schema/version。

以下内容不进入哈希：

- 描述；
- owner；
- 备注；
- 输出目录；
- 创建时间；
- `experiment_id`。

`retention_class`、是否归档和是否已经清理 Trace 也不进入配置哈希，
因为它们只影响结果保存周期，不影响市场、策略和账户计算。

### 9.3 scenario_id 与 run_id

建议：

```text
scenario_id = 不含 Seed 的配置哈希前 16 位
configuration_hash = 含 Seed 的规范配置 SHA-256
run_fingerprint    = configuration_hash + code revisions
run_id             = run_fingerprint 的 SHA-256 前 20 位
```

这样可以区分：

```text
配置变化
    → configuration_hash 变化

配置相同但代码版本变化
    → configuration_hash 不变
    → run_fingerprint 和 run_id 变化
```

所有完整 SHA-256 仍保存在记录中，短 ID 仅用于目录和显示。

### 9.4 代码版本

本平台最初涉及多个 Git 仓库，因此 `code_revisions` 保持映射结构，以便继续读取旧结果。
自 2026-08-04 合并为单体仓库后，新实验只保存一个平台级版本：

建议保存：

```json
{
  "code_revisions": {
    "strategy_trading": {
      "commit": "完整 commit SHA",
      "dirty": false
    }
  }
}
```

旧 SQLite 中的 `market_simulator`、`grid_trading` 和 `strategies_system` 键继续按原样读取，
不需要迁移历史实验。

正式基线实验默认要求单体仓库 worktree 为 clean。

可以通过显式开发选项允许 dirty worktree 做探索性运行，但必须：

- 记录 `dirty: true`；
- 标记 `reproducible: false`；
- 禁用已有成功结果的自动命中和断点续跑；
- 不把该结果作为正式可复现基线。

v1.0 不尝试完整打包 dirty worktree 和未跟踪文件。
如果需要保留结果，应先提交为明确版本后重新运行。

---

## 10. 单次运行生命周期

标准生命周期：

```text
读取 ExperimentSpec
    ↓
注册 Provider
    ↓
解析默认值与预检
    ↓
展开 RunSpec
    ↓
计算 ID、哈希和代码版本
    ↓
写入 ExperimentManifest
    ↓
Run 状态置为 RUNNING
    ↓
Provider.prepare()
    ↓
PreparedRun.execute()
    ↓
在同一事务中保存 Summary 与非市场 Trace
    ↓
Run 状态置为 SUCCEEDED
    ↓
提交 SQLite 事务
```

运行状态建议为：

```text
PLANNED
RUNNING
SUCCEEDED
FAILED
```

`SKIPPED` 是一次调度决定，不是已执行 Run 的最终状态。
跳过已有成功结果时，原 Run 仍保持 `SUCCEEDED`。

---

## 11. 批量运行与失败处理

### 11.1 v1.0 顺序执行

所有 Run 按确定性顺序逐个执行。

暂不引入：

- 多进程池；
- 分布式队列；
- 远程 Worker；
- GPU 调度；
- 集群资源管理。

### 11.2 单 Run 失败隔离

一个 Run 失败时必须：

1. 将该 Run 标记为 `FAILED`；
2. 记录异常类型和消息；
3. 可选记录本地调试栈；
4. 不生成伪造 Summary；
5. 释放当前 Run 资源；
6. 根据 `continue_on_error` 决定继续或停止。

错误记录不得包含 `.env`、API Key 或其他密钥内容。

### 11.3 中断恢复

再次执行相同 `experiment_id` 时：

- 已有相同 hash 的 `SUCCEEDED` Run 默认跳过；
- `FAILED` Run 可通过显式 `--rerun-failed` 重跑；
- 遗留 `RUNNING` 状态视为上次异常中断，需显式恢复；
- 配置 hash 不同但 `experiment_id` 相同，拒绝覆盖；
- 不使用 `--force` 原地覆盖成功结果。

如果确实要运行不同配置，应使用新的 `experiment_id`。

---

## 12. 结果保存

### 12.1 存储边界

实验结果不直接写入 `viewer/data`。

v1.0 采用：

```text
market_data/
└── <market_path_id>.parquet       # 已物化的 K 线，可被多个 Run 共享

experiment_results/
└── <experiment_id>.sqlite3        # 配置、状态、Summary 和非市场 Trace

exports/                            # 只有用户显式导出时才出现
├── comparison.csv
└── viewer-run.json
```

职责边界：

- K 线不存入 SQLite；
- 已物化的市场路径使用 Parquet，并由 `market_path_id` 和内容哈希引用；
- 配置、状态、错误、Summary 和非市场 Trace 统一存入 SQLite；
- 不生成外部 Trace JSON 作为正式结果；
- CSV 和 Viewer JSON 都是可删除、可重新生成的导出物；
- 导出物不进入数据库一致性判断。

Parquet 市场数据也必须通过统一的市场数据接口创建、校验和清理。
历史或不可重新生成的数据按不可变数据集管理；随机生成路径若只是缓存，
可以依据生成配置、Seed 和代码版本重新物化。直接在文件系统中手工删除
正式市场数据不属于受支持的清理方式。

`experiment_results/` 应作为默认本地目录并加入相关仓库 `.gitignore`。
需要作为固定回归样例的数据，再显式复制到 `viewer/data` 或测试 fixture。

相对 `output.root` 统一按实验配置文件所在目录解析，不按命令执行时的当前目录解析。
这样从不同工作目录调用同一份配置时，输出位置不会变化。

### 12.2 SQLite 结构

每个实验使用一个 SQLite 文件，建议至少包含：

```text
schema_migrations
    数据库 schema 版本

experiments
    ExperimentSpec、代码版本、创建时间、汇总状态

runs
    RunSpec、状态、Summary、错误、trace_state、retention_class

run_payloads
    run_id、payload_type、compression、payload_blob

market_references
    market_path_id、生成配置或 Parquet 引用、内容哈希
```

RunSpec、Summary 和错误等小型结构可保存为 JSON TEXT。
非市场 Trace 先序列化为 JSON，再压缩为 BLOB：

```text
非市场 Trace
    → JSON
    → zlib/gzip 压缩
    → SQLite BLOB
```

这只是把完整 Trace 作为一个 Payload 保存，不把逐意图、逐成交和逐权益记录
正规化为大量数据库行。

### 12.3 Trace 的范围

Trace 表示一次 Run 的过程明细。SQLite 中保存：

- 意图；
- 指令；
- 成交；
- 权益；
- 保证金；
- 资金费；
- 账户事件；
- 终止信息；
- Provider 专用过程事件。

K 线不在 Trace BLOB 中重复保存。Trace 只记录对应的 `market_path_id`，
Viewer 或分析工具读取时再与 Parquet K 线组合。

所有成功 Run 默认同时保存 Summary 和非市场 Trace。
查询实验列表或进行基础比较时只读取 Summary，不加载和解压 Trace BLOB。

如果 `SimulationRunner` 当前仍在内存中形成完整结果，本阶段不为减少内存而重写它；
流式统计和超长路径内存优化另行设计。

### 12.4 保留与归档

每个 Run 包含两个相互独立的字段：

```text
trace_state
    STORED      Trace 当前存在于数据库
    PURGED      Trace 已按统一接口清理，Summary 仍保留

retention_class
    STANDARD    普通结果，允许后续批量清理 Trace
    ARCHIVED    有研究价值的结果，禁止普通清理流程删除 Trace
```

默认 `retention_class=STANDARD`。

用户确认某个 Run 有保留价值时，通过统一接口将其标记为 `ARCHIVED`，并可记录：

- `archived_at`；
- `archive_reason`；
- 自定义 tags。

只有 `trace_state=STORED` 的 Run 才能归档完整 Trace。
如果 Trace 已经是 `PURGED`，必须先按原配置和原代码版本重新运行并恢复 Trace，
不能仅修改归档字段来伪装已经恢复。

归档标记表示“受清理策略保护”，不表示已经完成异地备份。
需要防止数据库文件整体损坏或丢失时，仍应备份整个 SQLite 文件。

v1.0 不做静默自动清理。清理命令必须先显示预计清理数量和释放空间，
再由用户显式执行；是否增加自动保留期限以后单独设计。

清理普通 Trace 时必须通过实验系统接口执行：

```text
校验 retention_class != ARCHIVED
    ↓
删除 run_payloads 中的 Trace BLOB
    ↓
将 trace_state 更新为 PURGED
    ↓
提交同一个 SQLite 事务
```

不得绕过接口直接修改数据库。删除按需导出的 CSV 或 Viewer JSON
不需要修改数据库，因为这些文件从来不是正式结果。

### 12.5 事务一致性

一次成功 Run 的写入顺序为：

```text
BEGIN
    保存 RunSpec
    保存 Summary
    保存压缩 Trace BLOB
    设置 trace_state=STORED
    设置 status=SUCCEEDED
COMMIT
```

如果 Trace 写入失败，不得提交一个声称 Trace 已保存的成功状态。
SQLite 事务用于保证状态、Summary 和 Trace 不出现半写入。

---

## 13. 结果索引与基础比较

### 13.1 SQLite 结果索引

`runs` 表是正式结果索引，至少保存：

```text
run_id
scenario_id
seed
status
market.key
strategy.key
execution.key
account.key
参数轴取值
configuration_hash
code_revisions
trace_state
retention_class
```

RunSpec、Summary 和 Provider 原始字段可以 JSON TEXT 形式保存，
同时将经常筛选的标量提升为普通列或由比较工具解析。

### 13.2 按需导出 comparison.csv

基础比较工具从 SQLite 的 Run 索引和 Summary 中读取标量字段，
只在用户显式执行 `compare` 或 `export` 时生成 CSV。

首版可包括：

- Run 和 Scenario 标识；
- Seed；
- 状态；
- 各组件 key；
- 参数轴值；
- 最终权益；
- 已实现和未实现盈亏；
- 手续费；
- 资金费；
- 成交数量；
- 强平和破产状态；
- 终止原因；
- Provider 暴露的标量原始事实。

CSV 的职责是“把事实并排放置”，不是计算策略排名。
删除 CSV 不影响实验完整性，之后可以从 SQLite 重新生成。

### 13.3 按需导出 Viewer JSON

Viewer 通过统一读取接口：

```text
SQLite 中的 Trace BLOB
+ market_path_id 对应的 Parquet K 线
    → 在内存中组合
    → 通过本地 HTTP 接口返回或显式导出 Viewer JSON
```

正常动态查看不要求产生临时 JSON 文件。
只有用户明确选择离线导出时才写入 `viewer-run.json`。

### 13.4 暂不支持

v1.0 暂不增加：

- PostgreSQL 或其他数据库服务；
- 将逐帧、逐意图、逐成交和逐权益 Trace 正规化为数据库表；
- 将 Parquet 用作实验状态或结果索引；
- Web 实验管理后台；
- 自动图表报告；
- 自动排名；
- Pareto 前沿；
- 统计显著性检验；
- 跨实验数据仓库。

---

## 14. 命令行与 Python API

### 14.1 通用命令

建议提供：

```bash
python -m experiment_system validate <experiment.json>
python -m experiment_system plan <experiment.json>
python -m experiment_system run <experiment.json>
python -m experiment_system run <single-run-experiment.json> \
  --export-viewer <viewer-run.json>
python -m experiment_system compare <experiment.sqlite3> --output <comparison.csv>
python -m experiment_system export-run <experiment.sqlite3> \
  --run-id <run-id> --output <viewer-run.json>
python -m experiment_system serve-results <result-directory>
python -m experiment_system archive-run <experiment.sqlite3> --run-id <run-id>
python -m experiment_system purge-traces <experiment.sqlite3>
```

其中：

- `validate`：只校验，不展开执行；
- `plan`：展示场景、Run 数量、顺序和输出位置；
- `run`：执行；
- `run --export-viewer`：仅对恰好一个 Run 的实验，在成功后显式导出 Viewer JSON；
- `compare`：查询 SQLite 并按需生成比较 CSV；
- `export-run`：组合 SQLite Trace 与 Parquet K 线，按需导出 Viewer JSON；
- `serve-results`：启动本机只读 API 和实验结果页面；
- `archive-run`：把有价值的 Run 标记为归档；
- `purge-traces`：只清理符合条件的普通 Trace，并同步更新 `trace_state`。

### 14.2 宿主应用入口

由于通用 CLI 不知道网格 Provider，`grid_trading` 应提供薄入口：

```bash
python -m grid_experiments validate <experiment.json>
python -m grid_experiments plan <experiment.json>
python -m grid_experiments run <experiment.json>
```

这个入口只负责：

1. 创建通用 Registry；
2. 注册网格 Provider；
3. 调用 `experiment_system` 的同一套命令实现。

不得在薄入口中再复制实验展开、状态和持久化代码。

### 14.3 Python API

命令行应建立在 Python API 之上，便于未来：

- Notebook 调用；
- Web 服务调用；
- 优化器提交实验；
- 自动测试。

建议最小入口：

```python
plan = experiment_service.plan(spec, registry)
result = experiment_service.run(plan, repository)
comparison = experiment_service.compare(experiment_database)
repository.archive_run(run_id, reason="baseline")
```

### 14.4 首版结果展示界面

v1.0 增加只读的本地实验结果界面，但不在前端创建、修改或启动实验。

前端通过只读 HTTP API 获取数据库内容：

```text
GET /api/experiments
GET /api/experiments/{experiment_id}
GET /api/experiments/{experiment_id}/runs
GET /api/experiments/{experiment_id}/runs/{run_id}
GET /api/experiments/{experiment_id}/runs/{run_id}/viewer
```

首版页面包括：

- 实验列表；
- 实验配置只读查看；
- Run 列表、筛选和排序；
- Summary 标量并排比较；
- Trace 状态和归档状态展示；
- 单 Run K 线、买卖点、权益、保证金和事件回放；
- 按需 CSV 或 Viewer JSON 导出。

实验配置仍通过 JSON、CLI 和 Provider 管理。
前端只渲染数据库中已保存的 ExperimentSpec 和 RunSpec，
不得写死网格策略或其他具体策略参数。

首版前端不提供：

- 新建或编辑 ExperimentSpec；
- 启动、停止或重跑实验；
- 修改策略参数；
- 归档或清理 Trace；
- 自动评价和策略排名。

---

## 15. 与其他模块的边界

### 15.1 与仿真执行

实验系统：

- 选择执行配置；
- 调用 `SimulationRunner`；
- 保存执行结果。

仿真执行：

- 解释交易指令；
- 计算成交、账本、费用、保证金和强平；
- 产生单 Run 原始结果。

实验系统不得在保存结果时重新计算账户结果。

### 15.2 与评价指标

实验系统首版只收集现有 Summary 和 Provider 原始字段。

第三部分完成后，可以通过稳定接口：

```text
SimulationRun / Summary
    ↓
MetricEvaluator
    ↓
MetricSet
    ↓
实验索引和比较工具
```

在评价指标定义前，不把临时指标公式固化到实验系统中。

### 15.3 与市场环境

实验系统只选择：

- 市场类型；
- 参数；
- Seed；
- 市场数据引用。

市场环境模块决定如何生成或读取 `MarketFrame`。

### 15.4 与策略体系

实验系统只选择策略及其配置，并调用 Provider。

策略模块决定：

- 何时建仓；
- 如何布网；
- 何时加仓、复位和平仓；
- 输出什么交易意图。

### 15.5 与策略优化

实验系统提供“给定参数，批量运行并返回结果”的能力。

优化模块决定：

- 下一组参数是什么；
- 如何搜索；
- 目标函数是什么；
- 何时停止。

显式参数网格属于实验编排；
根据结果自动产生下一组参数属于策略优化。

---

## 16. 建议代码结构

### 16.1 market_simulator

```text
market_simulator/
└── packages/
    └── experiment_system/
        ├── pyproject.toml
        └── src/experiment_system/
            ├── __init__.py
            ├── models.py
            ├── schema.py
            ├── registry.py
            ├── expansion.py
            ├── hashing.py
            ├── provenance.py
            ├── service.py
            ├── repository.py
            ├── sqlite_repository.py
            ├── payloads.py
            ├── market_references.py
            ├── comparison.py
            ├── exports.py
            ├── read_api.py
            └── cli.py
```

职责建议：

- `models.py`：ExperimentSpec、Scenario、RunSpec、RunRecord；
- `schema.py`：JSON 解析、类型校验、默认值解析；
- `registry.py`：Provider 注册和查找；
- `expansion.py`：场景、参数轴、Seed 展开；
- `hashing.py`：规范 JSON 与配置哈希；
- `provenance.py`：代码版本记录；
- `service.py`：plan/run 生命周期；
- `repository.py`：统一结果存储接口；
- `sqlite_repository.py`：事务、schema migration、恢复与索引；
- `payloads.py`：Trace JSON 序列化、压缩与解压；
- `market_references.py`：Parquet 市场路径引用与内容哈希；
- `comparison.py`：SQLite 基础比较查询；
- `exports.py`：按需导出 CSV 和 Viewer JSON；
- `read_api.py`：结果展示所需的本地只读 HTTP API；
- `cli.py`：通用命令实现。

### 16.2 策略实验宿主

```text
strategies_system/src/strategy_simulation/
├── components/
│   ├── markets.py
│   ├── executions.py
│   └── accounts.py
├── experiment_provider/
├── plugins/
└── adapters/
```

该结构是第二阶段原 `grid_trading/grid_experiments` 的后续归位结果。
通用命令仍由 `experiment_system` 提供，策略工程只注册具体组件和策略。

### 16.3 结果展示前端

优先扩展现有 `market_simulator/viewer`，不另建网格专用前端：

```text
market_simulator/viewer/
├── experiments.html
├── experiment.html
├── run.html
├── experiment-api.js
└── 现有 K 线回放组件
```

前端只依赖通用只读 API。网格、RSI 或其他策略的配置以保存的结构化数据展示，
不在 JavaScript 中维护各策略的参数 schema。

---

## 17. 测试与验收

### 17.1 单元测试

至少覆盖：

- JSON 配置解析；
- Decimal 字符串保持精度；
- 默认值展开；
- JSON Pointer 参数替换；
- 笛卡尔积数量；
- 确定性展开顺序；
- 多 Seed 展开；
- 重复 Run 检测；
- `max_runs` 保护；
- 规范 JSON 与配置哈希；
- scenario_id 不受 Seed 影响；
- run_id 随 Seed 改变；
- SQLite schema migration；
- Run 事务提交和失败回滚；
- Trace 压缩 BLOB 往返一致；
- Summary 查询不加载 Trace BLOB；
- `trace_state` 状态变化；
- STANDARD Trace 清理；
- ARCHIVED Trace 清理保护；
- Parquet 市场引用与内容哈希校验；
- 失败记录；
- 成功 Run 跳过；
- 失败 Run 重跑；
- CSV 和 Viewer JSON 按需导出；
- 只读 API 不提供写操作；
- 结果前端不硬编码网格参数。

### 17.2 架构测试

至少验证：

- `experiment_system` 不 import `grid_trading`；
- Provider 只能通过 Registry 接入；
- 配置不能加载任意 Python import 路径；
- 通用结果字段不能被 Provider 覆盖；
- 实验系统不包含具体指标公式；
- 实验系统不包含网格术语和规则。

### 17.3 跨仓库集成测试

在 `grid_trading` 中至少建立一条固定基线：

```text
同一配置
+ 同一 Seed
+ 原脚本入口
+ 新实验入口
```

两条路径应得到一致的：

- 市场序列；
- 交易意图和成交；
- 最终持仓；
- 最终权益；
- 手续费与资金费；
- 保证金与终止状态；
- 网格专用原始汇总。

完成等价验证后，再把原脚本改成实验系统的薄封装。

### 17.4 功能验收场景

v1.0 至少通过以下场景：

1. 一个固定行情、一个确定性策略、一个 Seed 的结果完整入库；
2. 一个策略 × 两种市场；
3. 两个策略配置 × 一个市场；
4. 一个 Scenario × 三个 Seed；
5. 两个参数轴的笛卡尔积；
6. 批次中一个 Run 失败，其余 Run 按配置继续；
7. 中断后跳过已成功 Run；
8. 从 SQLite Summary 按需导出 comparison.csv；
9. Viewer 通过统一接口打开 SQLite Trace 与 Parquet K 线；
10. 归档 Run 不会被普通清理删除；
11. 清理普通 Trace 后，数据库状态为 `PURGED` 且 Summary 仍可查询；
12. 相同配置和代码版本重复运行得到相同结果。

---

## 18. 分批开发计划

### 2A：领域模型、配置与确定性标识

状态：已完成（2026-07-30）。

实现：

- `experiment_system` 包骨架；
- ExperimentSpec、Scenario、RunSpec、RunRecord；
- JSON 读取与基本校验；
- Registry 和 Provider 协议；
- 规范 JSON；
- scenario_id、run_id 和 configuration_hash；
- 单次配置展开；
- `validate` 和 `plan` 的最小 Python API。

验收重点：

- 不执行仿真也能稳定解析、展开和展示一次 Run；
- 相同配置始终产生相同 ID 和哈希；
- 通用包不依赖网格代码。

实际落地：

- 新增 `market_simulator/packages/experiment_system` 独立包；
- Provider 的 Git 版本信息在 2A 由调用方显式注入，自动探测留到 2B；
- 新增配置、展开、哈希、Registry 和架构测试；
- 2A 不执行仿真、不写 SQLite、不接入网格 Provider。

### 2B：单次执行与结果存储

状态：已完成（2026-07-30）。

实现：

- 单 Run 生命周期；
- ExperimentManifest；
- RunRecord；
- SQLite schema 与 migration；
- 统一 Repository 接口；
- Summary 与压缩 Trace BLOB 入库；
- Parquet 市场引用；
- Run 事务提交与回滚；
- 失败记录；
- code revisions；
- 通用 CLI 骨架。

验收重点：

- 使用一个通用确定性 probe 完成端到端单次运行；
- 实验只产生一个正式 SQLite 结果文件；
- K 线不进入 SQLite；
- Summary 查询不需要读取 Trace；
- Trace 解压后与原运行结果一致；
- 失败不会留下伪成功结果。

实际落地：

- 新增单 Run 执行服务和可注入 Provider Registry 的通用 CLI 骨架；
- 新增每实验一个 SQLite Repository，包含 schema migration、Manifest、
  Run 生命周期、Summary、压缩 Trace BLOB 和市场引用；
- 成功状态、Summary、Trace 与市场引用在同一事务中提交，写入失败会整体回滚；
- K 线使用内容寻址的 Parquet 文件保存，Decimal OHLC 保持精度，并在复用已有
  文件时重新校验语义内容哈希；
- 正式运行默认要求单体仓库 worktree clean；显式允许 dirty 时记录工作区内容指纹，并把
  Manifest、Summary 和 RunRecord 标记为不可复现；
- deterministic probe 已完成从执行、Parquet、SQLite 到结果读取的端到端验证；
- 新增 15 项 2B 测试；截至本批完成，`market_simulator` 全量测试为
  104 项通过、1 项因本机缺少 Node.js 跳过。

### 2C：网格 Provider 接入

状态：已完成（2026-07-30）。

实现：

- `grid_experiments` 薄入口；
- `grid-simulation/v1` Provider；
- Anchored GBM、单组跟随网格和 COIN-M 账户配置构造；
- 策略专用 Summary 命名空间；
- 原脚本与新入口的一致性测试。

验收重点：

- 调用方向为 `grid_trading → experiment_system → simulation_runtime`；
- 新入口结果与当前单组跟随网格脚本一致；
- 未改动网格规则和实盘服务。

实际落地：

- 在 `grid_trading/grid_experiments` 新增薄宿主、组件 factory 和
  `grid-simulation/v1` Provider，依赖方向保持为
  `grid_trading → experiment_system → simulation_runtime`；
- 注册 `anchored-gbm/v1`、`single-following-grid/v1`、
  `daily-bar-execution/v1` 和 `coinm-inverse/v1` 四类稳定组件；
- 新增 `experiments/single_following_grid_baseline.json`，固定三年 Anchored GBM、
  单组 COIN-M 跟随网格、费用、账户和 Seed 42；
- 基线显式使用 `margin_model=none`、`funding_model=none`，先保持与旧入口完全
  等价；新配置可显式启用 `flat-maintenance/v1`，并已验证会按配置触发强平；
- 网格创建、复位、Cell 终态和成交计数仅写入
  `provider_summary["grid-simulation/v1"]`，不覆盖通用 Summary；
- 旧脚本和新 Provider 在 1,097 根 K 线、意图、指令、155 笔成交、权益、
  费用、最终 90 张仓位和 75 次循环上逐项一致；
- 新入口已完成 SQLite Summary/Trace 与外置 Parquet 市场路径的端到端验证；
- `grid_trading` 全量测试为 253 项通过、14 项测试网写入类用例按环境开关跳过；
  `market_simulator` 仍为 104 项通过、1 项因本机缺少 Node.js 跳过；
- 未修改 `grid_rule`、`grid_strategies` 或 `grid_server` 的业务实现。

### 2D：场景组合、多 Seed 与参数网格

状态：已完成（2026-07-30）。

实现：

- 多场景组；
- 场景组内的四类组件候选列表；
- 参数轴；
- JSON Pointer 参数替换；
- 多 Seed；
- 场景组内的确定性笛卡尔积；
- 重复检测；
- `max_runs`；
- 完整 `plan` 输出。

验收重点：

- 运行前能准确看到 Run 总数；
- 展开顺序稳定；
- 不兼容组合在任何 Run 开始前失败；
- 单次和批量共用同一执行路径。

实际落地：

- 2A 已实现的多场景组、四类组件候选列表、参数轴、JSON Pointer、多 Seed、
  重复检测和 `max_runs` 现已接入实际批量执行，不再只停留在 plan；
- 新增 `execute_experiment()`，一个 ExperimentManifest 和一个 SQLite 数据库
  按 plan 的稳定顺序执行全部 Run；
- `execute_single_run()` 改为单 Run plan 对同一批量路径的约束包装；
- SQLite 实验状态按全部 Run 聚合，完成中间 Run 时保持 `RUNNING`，仅在全部
  Run 成功后变为 `SUCCEEDED`；
- 2D 使用 fail-fast：首个 Run 失败时记为 `FAILED`，实验记为 `FAILED`，
  后续 Run 保持 `PLANNED`；继续执行、恢复和失败重跑留给 2E；
- 通用 CLI 的 `run` 已支持多 Run，并输出 Experiment 级数量与逐 Run 状态摘要；
- 新增 `experiments/single_following_grid_matrix.json`，以 2 个策略候选、
  2 个 `grid_count` 参数值和 2 个 Seed 展开为 4 个 Scenario、8 个 Run；
- 8 个网格 Run 已验证按固定顺序落入一个 SQLite；市场路径按 Seed 内容寻址，
  只生成并复用 2 个 Parquet 文件；
- 已验证该矩阵的首个 Run 通过单次入口和批量入口得到相同运行事实、策略原始汇总
  和市场路径；
- 截至本批完成，`market_simulator` 全量测试为 108 项通过、1 项因本机缺少
  Node.js 跳过；`grid_trading` 全量测试为 256 项通过、14 项测试网写入类用例
  按环境开关跳过。

### 2E：批次状态、恢复与错误隔离

状态：已完成（2026-07-30）。

实现：

- 可恢复的顺序批量调度；
- `continue_on_error`；
- SQLite Run 索引；
- 已成功 Run 跳过；
- `--rerun-failed`；
- 异常中断状态识别；
- `retention_class`；
- Trace 归档和清理。

验收重点：

- 单 Run 失败不污染其他结果；
- 中断恢复不覆盖成功结果；
- 所有状态和 Payload 变更保持事务一致；
- ARCHIVED Trace 不会被普通清理删除。

实际落地：

- `execute_experiment()` 现在按 `controls.continue_on_error` 处理当前批次失败：
  `false` 时保留 fail-fast，`true` 时记录失败并继续后续 Run；
- `SKIPPED` 只记录为本次 `ExperimentOutcome` 的调度决定，不新增数据库状态；
  已成功 Run 仍保持 `SUCCEEDED`；
- 再次执行 clean 实验时会校验完整 ExperimentSpec、Run plan 和代码指纹：
  完全一致才允许恢复，不同配置使用同一 `experiment_id` 会在任何新 Run 开始前拒绝；
- 已成功 Run 默认跳过；`FAILED` 只有显式 `rerun_failed=True` 或
  `--rerun-failed` 才重置并重跑；
- 遗留 `RUNNING` 被识别为异常中断，默认拒绝继续；只有显式
  `resume_interrupted=True` 或 `--resume-interrupted` 才恢复为 `PLANNED` 并执行；
- dirty 探索性实验继续禁止成功命中和断点恢复，避免把未固化工作区当成可复现基线；
- `ExperimentOutcome` 增加全量 RunRecord、执行/跳过/恢复/重跑 ID 和各状态计数；
  CLI 对允许继续的失败批次输出完整摘要并返回非零退出码；
- SQLite schema 升级到 v2，增加 `archived_at` 和 `archive_reason`，并验证 v1
  数据库可原地迁移；
- Repository 新增 Trace 归档、清理预览和事务性清理接口；只有成功且
  `trace_state=STORED` 的 Run 可以归档；
- `purge-traces` 默认只报告可清理 Run 和压缩 Payload 字节数，显式
  `--confirm` 后才删除 `STANDARD` Trace BLOB 并同步写入 `PURGED`；
- `ARCHIVED` Trace 已验证不会进入普通清理候选；清理后 Summary 和市场引用仍保留；
- 持久化的异常消息会对常见 API key、secret、token、password、Authorization
  和 Bearer 凭据做脱敏，并限制消息长度；
- 截至本批完成，`market_simulator` 全量测试为 116 项通过、1 项因本机缺少
  Node.js 跳过；`grid_trading` 全量测试为 256 项通过、14 项测试网写入类用例
  按环境开关跳过。

### 2F：基础比较与只读结果展示

状态：已完成（2026-07-30）。

实现：

- SQLite Summary 比较查询；
- 本地只读 HTTP API；
- 实验列表；
- ExperimentSpec 和 RunSpec 只读查看；
- Run 表格、筛选和排序；
- Summary 标量并排展示；
- Trace/归档状态展示；
- 复用现有 Viewer 的单 Run 回放；
- 按需导出 comparison.csv；
- 按需导出 Viewer JSON；
- Provider 原始字段展开；

验收重点：

- 可以从界面浏览一个结果目录中的全部实验；
- 可以查看和筛选 Run，但不能从前端修改实验；
- 前端不硬编码网格或其他具体策略参数；
- 查看 Run 时不产生临时 JSON；
- 不在界面中引入评价指标和策略排名。

实际落地：

- 新增只读 `ExperimentCatalog` 和 `ExperimentReader`，以 SQLite
  `mode=ro + query_only` 扫描结果目录并读取实验、Run、Summary、Trace 和市场引用；
- Run 查询支持状态、Scenario、Seed、保留类别、Trace 状态和自由文本筛选，以及
  稳定排序和分页；Summary 和 Provider Summary 只展开已有标量叶子，不解释含义；
- 新增通用比较表模型，将组件、参数轴和原始 Summary 标量按动态列并排展开，
  不引入指标公式、评分或排名；
- 新增本地 GET-only HTTP API 和通用结果页，支持实验列表、ExperimentSpec、
  代码版本、RunSpec、Run 表格、筛选、排序和 Trace/归档状态查看；
- 前端只使用通用组件槽位、参数和动态 Summary 字段，没有网格、RSI 或具体账户
  模型的硬编码，也没有运行、重跑、归档、清理等写操作；
- 单 Run 回放由 API 在内存中解压 SQLite Trace、校验市场引用并读取 Parquet，
  直接返回现有 Viewer schema；正常浏览不会生成临时 JSON；
- CLI 新增 `serve-results`、`compare` 和 `export-run`；CSV 与 Viewer JSON
  只有在显式导出命令或下载动作发生时才写出；
- 已用真实的 8-Run 跟随网格矩阵验证通用页面数据链，结果目录可发现实验，
  Run Summary 可以动态展开，K 线回放接口可读取 1,097 根日线；
- 截至本批完成，`market_simulator` 全量测试为 123 项通过、1 项因本机缺少
  Node.js 跳过；`grid_trading` 全量测试为 256 项通过、14 项测试网写入类用例
  按环境开关跳过；
- 本次执行环境未连接图形浏览器，因此没有完成自动化点击截图；HTTP 路由、
  静态资源、只读方法、动态回放、无临时 JSON 和前端通用性均已有自动化测试。

### 2G：现有脚本收口与使用说明

状态：已完成（2026-07-31）。

实现：

- 单组和分层跟随网格实验样例；
- 现有仿真脚本改为薄封装；
- 使用说明和示例配置。

验收重点：

- 能用一条命令运行多场景、多 Seed 和参数网格；
- 能用一条命令生成基础比较表；
- 不在实验系统中引入评价指标和策略排名；
- 现有 Viewer 示例和回归测试继续运行。

实际落地：

- `grid-simulation/v1` 新增 `layered-following-grid/v1` Strategy 组件，与现有
  `single-following-grid/v1` 共用市场、执行、COIN-M 账户、Runner 和持久化路径；
- 新增 `experiments/layered_following_grid_baseline.json`，完整承接原三年分层
  跟随网格的行情、策略、账户、费率和 Seed 42 配置；
- 单组基线、分层基线和 8-Run 参数矩阵均可通过同一个 `grid_experiments`
  入口执行；矩阵继续覆盖多 Scenario、多 Seed 和显式参数轴；
- 通用 `run` 命令新增显式 `--export-viewer`，且只允许用于恰好一个 Run 的实验，
  批量实验会在任何执行开始前拒绝该选项；
- `run_single_following_grid_simulation.py` 和
  `run_layered_following_grid_simulation.py` 已缩减为 30 行薄封装，只选择实验配置、
  输出位置和恢复参数，不再构造 MarketSource、策略、规则、Runner、账本或 Summary；
- 原脚本的 Seed、费率、账户和策略参数入口收口到实验 JSON；需要研究变体时复制
  配置并先执行 `plan`，不再继续扩充 Python 脚本参数；
- 分层 Provider 结果保持 1,097 根日线、736 笔成交、6 层、23 次复位、364 次
  完整循环和最终 41 张合约仓位；显式 Viewer 导出已完成端到端验证；
- 单组 Provider 继续保持 1,097 根日线、155 笔成交、75 次完整循环和最终
  90 张合约仓位；静态规则的基础行为继续由直接规则适配器单元测试覆盖；
- `README.md`、网格仿真说明和 Viewer 说明已统一为实验配置、SQLite/Parquet
  正式结果与按需导出的工作流；
- 最终全量验收为 `market_simulator` 124 项通过、1 项因本机缺少 Node.js 跳过；
  `grid_trading` 258 项通过、14 项测试网写入类用例按环境开关跳过；
- 第二阶段至此完整结束；实验系统仍不包含评价指标、策略评分或自动排名。

---

## 19. v1.0 明确不做

- 并行和分布式执行；
- 随机参数分布采样；
- 自动优化器；
- 在前端创建、修改、启动、停止或重跑实验；
- 在前端执行 Trace 归档和清理；
- 多用户或远程实验管理后台；
- 实时进度推送；
- PostgreSQL 或其他数据库服务；
- 将 Trace 正规化为逐帧、逐意图、逐成交数据库表；
- 将 Parquet 用作实验状态和结果索引；
- 云端对象存储；
- 多用户权限；
- 实验取消和远程 Worker 控制；
- 跨机器缓存；
- 指标公式；
- 自动策略排名；
- 自动生成研究结论；
- 为减少内存而重写 `SimulationRunner` 为流式执行。

---

## 20. 建议先确认的设计结论

编码前建议按顺序确认以下事项：

1. 通用包是否确定放在 `market_simulator/packages/experiment_system`；
2. 网格 Provider 是否确定放在 `grid_trading/grid_experiments`；
3. v1.0 是否只使用 JSON 配置；
4. 是否接受“单次运行就是只有一个 Run 的批次”；
5. 【已确认】场景组内四类组件列表做笛卡尔积，不同场景组之间不交叉组合；
6. 是否接受 v1.0 参数批量只做显式 values；
7. 是否接受 v1.0 只做顺序执行；
8. 【已确认】K 线使用独立 Parquet；配置、状态、Summary 和非市场 Trace
   统一保存到每实验一个 SQLite；CSV 与 Viewer JSON 按需导出；
9. 【已确认】Summary 作为永久保留的紧凑结果，Trace 作为过程明细：
   所有成功 Run 初始保存压缩 Trace BLOB；普通 Run 为 STANDARD，可按统一接口清理，
   有价值的 Run 标记为 ARCHIVED；清理只删除 Trace，并同步更新 `trace_state`；
10. 基础比较是否只并排展示原始字段，不引入评价指标；
11. 正式基线实验是否要求整个单体仓库 worktree 为 clean；
12. 现有脚本是否按“先等价验证、再改薄封装”的顺序迁移；
13. 【已确认】首版前端只做实验结果展示：配置只读、Run 查询比较和单 Run
    回放；实验创建、修改、启动、归档和清理仍由 JSON、CLI 与 Python API 完成。

这些结论确认后，可从 2A 开始实现。
