# Market Simulator

`market_simulator` 是独立于任何具体交易策略的轻量市场与交易仿真框架。

当前阶段采用 simulator-first：先稳定市场数据、成交、账本和运行器边界，再让各应用中的
策略或规则引擎通过适配器接入。这里不会包含网格或其他具体交易规则，也不会
依赖 Binance、FastAPI 或 Streamlit。市场与运行时核心不依赖持久化；
独立的 `experiment_system` 使用 Python SQLite 和 PyArrow 保存实验结果。

## 包边界

```text
packages/
├── market_protocol/   # MarketFrame 与 MarketSource 公共协议
├── market_simulator/   # 固定数据源与锚点约束随机日线
├── simulation_runtime/ # 通用交易端口、显式成交、费用、账本、保证金端口和运行器
├── experiment_system/  # 通用实验计划、单次执行、结果与中性指标存储
└── metric_system/      # 策略无关的收益、风险、仓位、资金和多 Run 聚合指标
```

项目还包含 `viewer/`：它是策略无关的仿真回放页面，读取标准
`SimulationRun` JSON，展示 TradingView 风格 K 线、等待意图、成交标记、账户状态和
权益曲线。

依赖方向：

```text
market_protocol
      ↑
      ├── market_simulator
      └── simulation_runtime
                ↑
                └── experiment_system
                            ↑
                            └── metric_system
```

`simulation_runtime` 的唯一策略入口是 `SimulationTradePort`。适配器向当前
`MarketFrame` 提供已经确定价格的 `TradeInstruction`。

具体策略、规则引擎及其仿真适配器归策略应用所有。例如 `grid_rule` 位于
`grid_trading`；同一仓库中的薄适配器把规则状态、订单意图和成交事件映射到
`SimulationTradePort`。规则引擎不能依赖市场生成模型或 `simulation_runtime`。

## 长期市场环境

`market_environment` 将宏观假设和具体路径分开：Scenario 只声明绝对价格 Anchor、
分段波动率和时间范围，`anchored-regime-bridge/v1` 再按固定 Seed 生成连续的小时
OHLC。策略只能逐 Bar 读取 `MarketFrame`，不会获得未来 Anchor 或场景阶段。

首版 `btc-three-year-market-baseline-v1` 已锁定 6 类三年 BTC 场景，并为每类场景
生成 8 个 TRAIN、4 个 VALIDATION、4 个 HOLDOUT Seed，共 96 条路径。正式定义和
内容锁位于 `market_environments/`，可复现 Parquet 位于被 Git 忽略的 `generated/`。
生成方式与目录边界见 [market_environments/README.md](market_environments/README.md)。

## 当前执行语义

- `MarketFrame` 是经过校验的 OHLC bar，`price` 是 `close` 的兼容别名。
- 策略适配器只为当前 Bar 提供明确价格的交易指令。
- 被动意图由策略适配器在 OHLC 覆盖目标价格时转成指令。
- 主动意图在信号产生后的下一根 Bar 按 open 转成指令。
- 新意图不能回看产生它的当前 Bar。
- bar 内不假设价格路径；同一批成交产生的新意图最早从下一根 bar 生效。
- `reduce_only` 指令只允许按当前净仓位方向全量减仓；方向错误、空仓或超额减仓会
  在应用账本前终止本次运行。
- 默认线性账本记录现金、持仓均价、毛/净已实现盈亏、手续费、资金费净入账和逐 bar
  盯市权益。
- Runtime 默认使用零费率；调用方可注入固定 Maker/Taker 费率或产品专属 FeeModel。
- Runtime 默认使用 `ZeroFundingModel`；调用方可注入固定周期、固定费率的线性或
  产品专属 FundingModel。资金费以有符号 `wallet_delta` 入账，正数为收到、负数为
  支付，并在最终保证金与强平检查以前修改钱包。
- 运行器支持由调用方注入产品专属账本，并将其多币种账户指标写入标准 run JSON。
- 已提供通用 `MarginModel`、`MarginSnapshot`、`FlatMaintenanceMarginSchedule`、
  `TieredMaintenanceMarginSchedule` 和 `NoMarginModel` 计算接口。
- 调用方注入 MarginModel 后，Runner 会在新增敞口成交前计算含手续费的预计账户；
  可用余额小于零时抛出 `InsufficientMarginError`，不生成 Fill、不修改真实账本。
- Runner 可选择 `CLOSE_ONLY` 或 `ADVERSE_EXTREME` 强平采样。后者先检查开盘
  跳空，再以多仓 low、空仓 high 检查盘中触线；触发后保留账户状态并立即终止。
- 同一 Bar 同时存在成交和盘中强平触线时采用保守终止，并在
  `LiquidationEvent` 中记录 `intrabar_ordering_ambiguous=true`。
- Runner 暂不处理分钟级盘中路径重放、部分成交和盘口，也不预测或按市场环境生成
  资金费率。
- 默认 `NoSlippageModel` 保持历史结果；可注入 `FixedBpsSlippageModel` 对主动成交
  施加固定不利 bps。被动触价网格保持指定价格，手续费按滑点后的有效成交价计算。
- v1 资金费按日线使用日级汇总假设，每根日线至多结算一次；不生成盘中 8 小时
  资金费事件。

### 显式交易指令路径

调用方通过 `SimulationTradePort` 为当前 `MarketFrame` 提供已经包含明确价格的交易
指令，Runtime 只校验 sequence、instrument、重复键和 `reduce_only`，随后生成 Fill
并记账；它不会再根据 OHLC 判断该指令是否应该成交。

```text
SimulationTradePort.instructions_for(current)
    → TradeInstruction(price, quantity, side, frame_sequence)
    → SimulationRunner
    → SimFill
    → Ledger
```

`SimFill` 直接携带 `instruction_key`、`source_intent_key`、`intent_mode`、
`liquidity_role`、费率、费用金额和费用资产。`PASSIVE` 默认按 Maker、`ACTIVE`
默认按 Taker 计费；自定义 FeeModel 可以覆盖这一计算。
相邻 `grid_trading` 中的三个网格适配器通过 `SimulationTradePort` 解析被动意图；
`examples/rsi_signal_probe.py` 展示了收盘产生 RSI 信号、下一根开盘执行的主动意图
时序。只读 `SimulationTracePort` 记录意图生命周期，并输出包含 `intents`、
`instructions`、`fills` 和 `equity` 的 JSON schema v2；这些快照只用于报告和
一致性校验，不参与成交判断。

币本位反向合约公式不属于通用框架；当前实现位于相邻 `grid_trading` 的
`grid_rule.adapters.InverseContractLedger`、`InverseContractFeeModel`、
`FixedRateInverseContractFundingModel` 和 `InverseContractMarginModel`。保证金
模型已经可以纯计算仓位名义价值、钱包、
未实现盈亏、初始/维持保证金、资金占用和预估强平价格；维持保证金支持固定费率与
带版本来源的连续分档表。COIN-M 模型已经接入 Runner 的成交前保证金校验，但当前
示例脚本尚未启用；Runtime 已支持按 close 强平终止，但结果的 JSON 和 Viewer 展示
留在后续批次。Viewer 只读取通用 Fill、权益与 `account_metrics`，不依赖网格或
COIN-M 代码。

## 实验系统 v1.0

`experiment_system` 当前实现配置规划、单次运行和确定性批量运行闭环：

- 严格的 `experiment-spec/v1` JSON 配置；
- 一个实验内的多个场景组；
- 场景组内 market × strategy × execution × account 组合；
- 显式参数轴与多 Seed 展开；
- `max_runs` 数量保护；
- Provider 显式注册、默认值解析和兼容性预检；
- 规范 JSON、scenario_id、configuration_hash 和 run_id；
- `validate_experiment()`、`plan_experiment()` 与紧凑 plan 文档；
- `ExperimentManifest` 和 `PLANNED → RUNNING → SUCCEEDED/FAILED`
  生命周期；
- 每个实验一个 SQLite 结果库；
- Summary 与 zlib 压缩的非市场 Trace BLOB；
- 精确 Decimal OHLC 的内容寻址 Parquet 市场路径；
- Git commit、tag、dirty worktree 内容指纹与可复现性标记；
- 可由具体策略应用注入 Registry 的 `validate`、`plan`、`run` CLI 骨架；
- `execute_experiment()` 按完整 plan 顺序执行全部 Run；
- `execute_single_run()` 只是单 Run plan 的同一路径包装，不维护第二套执行逻辑。
- `continue_on_error` 控制当前批次遇到失败后是否继续；
- clean 实验可从原 SQLite 恢复，成功 Run 自动跳过，失败和中断 Run 必须显式处理；
- `STANDARD` Trace 可预览后清理，`ARCHIVED` Trace 受普通清理保护。
- 从 SQLite 只读查询实验、Run 和原始 Summary 标量，并支持筛选、排序和并排比较；
- 本地只读结果页复用现有 K 线 Viewer，动态读取 SQLite Trace 与 Parquet，
  正常浏览不生成临时 JSON；
- CSV 和 Viewer JSON 只在用户显式执行导出时创建。

不同场景组之间不会交叉组合。实验配置不能加载任意 Python import 路径；
具体策略应用必须通过 `ProviderRegistry` 显式注册 Provider。

正式执行默认拒绝 dirty 单体仓库；探索性运行必须显式使用
`allow_dirty=True` 或 CLI 的 `--allow-dirty`，并在 Manifest、Summary 和
RunRecord 中标记为不可复现。

结果边界为：

```text
market_data/<market_path_id>.parquet
experiment_results/<experiment_id>.sqlite3
```

SQLite 保存配置、状态、Summary 和 Trace，但不重复保存 K 线。查询 Summary
不会读取或解压 Trace；成功状态、Summary、Trace 和市场引用在同一个事务中提交。
同一实验中的全部 Run 共用一个 SQLite；相同市场路径通过内容寻址复用 Parquet。
恢复时会校验完整 ExperimentSpec、Run plan 和代码指纹；配置不同但
`experiment_id` 相同会拒绝覆盖。dirty 探索性实验不启用成功命中或断点恢复。

批次恢复和 Trace 生命周期命令：

```bash
python -m experiment_system run experiment.json --rerun-failed
python -m experiment_system run experiment.json --resume-interrupted
python -m experiment_system archive-run experiment.sqlite3 \
  --run-id <run-id> --reason "研究基线"
python -m experiment_system purge-traces experiment.sqlite3
python -m experiment_system purge-traces experiment.sqlite3 --confirm
```

`purge-traces` 默认只预览预计清理的 Run 和压缩 Payload 字节数；必须显式增加
`--confirm` 才会在同一事务中删除 Trace BLOB 并把 `trace_state` 改为 `PURGED`。

只读浏览一个结果目录：

```bash
python -m experiment_system serve-results experiment_results \
  --viewer-root viewer --port 8088
```

访问 `http://127.0.0.1:8088/`。结果页可以查看 ExperimentSpec、代码版本、
RunSpec、状态、参数、Trace/归档状态，并从数据库动态展开任意 Provider 的原始
Summary 标量和已经持久化的指标。页面自身不实现指标公式，也不提供创建、运行、
重跑、归档或清理操作。

显式导出比较表或某个 Run 的标准 Viewer JSON：

```bash
python -m experiment_system compare experiment.sqlite3 \
  --output exports/comparison.csv
python -m experiment_system export-run experiment.sqlite3 \
  --run-id <run-id> --output exports/run.json
```

恰好只有一个 Run 的实验也可以在执行命令中显式增加
`--export-viewer exports/run.json`。批量实验使用该参数会在任何 Run 开始前被拒绝，
避免“应当导出哪一个 Run”产生隐式规则。

## 评价指标系统 v1.0

`metric_system` 从已保存的 Summary / Trace 构建只读 `MetricInput`，计算并保存：

- BTC、USDT 等不同计价资产相互独立的收益、回撤、水下时间、波动、Sharpe、
  Sortino 和尾部收益；
- 强平、破产、终止、仓位路径、成交结构、手续费、资金费、保证金与有效杠杆；
- 同一 Scenario 多 Seed 的均值、中位数、标准差、P05/P25/P75/P95、最差值和事件率；
- MetricSet 定义、输入指纹、计算版本、缺失原因和 Trace 清理后的可重算状态。

通用框架不包含具体策略术语。应用通过注册输入贡献者和附加 MetricSet 扩展指标；
例如相邻 `strategies_system` 的策略指标注册网格循环、layer 和 cell 指标。

仅计算通用指标：

```bash
python -m metric_system evaluate-experiment experiment.sqlite3
python -m metric_system evaluate-run experiment.sqlite3 <run-id>
python -m metric_system aggregate experiment.sqlite3
```

命令默认幂等；只有显式 `--recompute` 才重算同版本结果。Trace 已清理但从未计算过的
Trace 级指标会保存为 `UNAVAILABLE/TRACE_PURGED`，不会用零值代替。已经计算的指标
继续保留，并标记为不可重算。

## 随机日线与可视化

生成三年固定 seed 的等比挂单演示：

```bash
cd market_simulator
python3 scripts/generate_ladder_run.py
```

默认输出：

```text
viewer/data/btc-geometric-ladder-3y-seed-42.json
```

接入后的改进版 COIN-M 多层跟随网格示例由相邻 `strategies_system` 中的
`experiments/layered_following_grid_baseline.json` 定义，并通过
`strategy_simulation` 实验 CLI 显式导出。
Viewer 默认载入
`viewer/data/layered-following-grid-coinm-long-3y-seed-42.json`，并可切换查看 BTC
总权益、按每日收盘价折算的 USDT 总权益、逐笔和累计手续费以及资金费净入账。
单组跟随网格结果也由同一策略工程生成，可手动载入对照。

启动本地播放器：

```bash
python3 -m http.server 8088 --directory viewer
```

访问 `http://127.0.0.1:8088/`。播放器也可以打开其他符合
`viewer/simulation-run.schema.json` 的 run JSON。

默认演示包含 2026-01-01 至 2029-01-01 共 1,097 根日线。锚点路径多次上下穿越，并
经过 40,000 美元和 200,000 美元；市场源使用反射边界，保证全部 OHLC 都位于该价格
区间。

几何挂单 Probe 从首日收盘价下方按 4% 等比间隔建立 5 档被动买入意图，每档
0.01 BTC。某档买入后，在该档上方一个等比间隔建立对应卖出意图；卖出后重新恢复
该档买入意图，不允许裸卖空。

用于逐笔核对意图撤销、被动触价、主动次日开盘执行和账本结果的 6 日确定性演示：

```bash
python3 scripts/generate_probe_run.py
```

输出为 `viewer/data/deterministic-probe-run.json`。

锚点 GBM 的纯市场路径仍可单独生成：

```bash
python3 scripts/generate_sample_run.py
```

输出为 `viewer/data/btc-anchored-seed-42.json`，可通过 Viewer 的“打开 run JSON”载入。

## 开发运行

可以将五个包安装到同一虚拟环境：

```bash
python -m pip install -e packages/market_protocol
python -m pip install -e packages/market_simulator
python -m pip install -e packages/simulation_runtime
python -m pip install -e packages/experiment_system
python -m pip install -e packages/metric_system
```

也可以不安装，直接运行测试：

```bash
PYTHONPATH=packages/market_protocol/src:packages/market_simulator/src:packages/simulation_runtime/src:packages/experiment_system/src:packages/metric_system/src \
python -m unittest discover -s tests -v
```

## 后续接入顺序

1. 资金费的历史回放和市场条件化生成留到策略优化精细化阶段。
2. 第二阶段实验系统 v1.0 和第三阶段评价指标 v1.0 已完成。
3. 下一阶段进入市场环境扩展，再与策略体系交叉研究。
