# Market Simulator

`market_simulator` 是独立于任何具体交易策略的轻量市场与交易仿真框架。

当前阶段采用 simulator-first：先稳定市场数据、成交、账本和运行器边界，再让各应用中的
策略或规则引擎通过适配器接入。这里不会包含网格或其他具体交易规则，也不会
依赖 Binance、SQLite、FastAPI 或 Streamlit。

## 包边界

```text
packages/
├── market_protocol/   # MarketFrame 与 MarketSource 公共协议
├── market_simulator/   # 固定数据源与锚点约束随机日线
└── simulation_runtime/ # 通用交易端口、显式成交、费用、账本、保证金端口和运行器
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
```

`simulation_runtime` 的唯一策略入口是 `SimulationTradePort`。适配器向当前
`MarketFrame` 提供已经确定价格的 `TradeInstruction`。

具体策略、规则引擎及其仿真适配器归策略应用所有。例如 `grid_rule` 位于
`grid_trading`；同一仓库中的薄适配器把规则状态、订单意图和成交事件映射到
`SimulationTradePort`。规则引擎不能依赖市场生成模型或 `simulation_runtime`。

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

接入后的改进版 COIN-M 多层跟随网格示例由
`grid_trading/scripts/run_layered_following_grid_simulation.py`
生成。Viewer 默认载入
`viewer/data/layered-following-grid-coinm-long-3y-seed-42.json`，并可切换查看 BTC
总权益、按每日收盘价折算的 USDT 总权益、逐笔和累计手续费以及资金费净入账。
原来的单组跟随网格结果仍可手动载入对照。

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

可以将三个包安装到同一虚拟环境：

```bash
python -m pip install -e packages/market_protocol
python -m pip install -e packages/market_simulator
python -m pip install -e packages/simulation_runtime
```

也可以不安装，直接运行测试：

```bash
PYTHONPATH=packages/market_protocol/src:packages/market_simulator/src:packages/simulation_runtime/src \
python -m unittest discover -s tests -v
```

## 后续接入顺序

1. 资金费的历史回放和市场条件化生成留到策略优化精细化阶段。
2. 在稳定执行边界上增加批量实验、评价指标和策略优化。
