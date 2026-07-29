# Simulation Viewer

通用仿真运行结果播放器。页面使用 TradingView 风格的深色配色，支持：

- 标准 OHLC 日线 K 线；
- 播放、暂停、逐日推进和时间轴跳转；
- 悬停十字线与精确 OHLC；
- 等待中的被动意图价格水平线；
- 绿色买单/B 成交标记与红色卖单/S 成交标记；
- 当前现金、持仓、持仓均价、净已实现盈亏、累计手续费、资金费净入账和总权益；
- 成交参考价、最终有效价和有符号滑点 bps；旧结果缺少滑点字段时按零滑点显示；
- 随时间推进的账户权益曲线；
- 截至当前回放日期的成交明细、Maker/Taker 角色和逐笔手续费；
- 主动/被动意图的等待、成交和撤销生命周期；
- 正常完成、强平终止和穿越破产线的 Run 状态；
- 每日保证金余额、维持保证金、可用余额和预估强平价；
- K 线上的强平标记及触发时完整账户事实；
- 打开其他 `SimulationRun` JSON。

启动：

```bash
cd market_simulator
python3 -m http.server 8088 --directory viewer
```

访问 `http://127.0.0.1:8088/`。

重新生成默认数据：

```bash
python3 scripts/generate_ladder_run.py
```

当前默认文件为
`viewer/data/layered-following-grid-coinm-long-3y-seed-42.json`。该文件由相邻
`grid_trading` 工程按 `SimulationRunner → StrategyAdapter → Strategy → GridRuleEngine`
调用链生成，包含三年、40,000 至 200,000 美元边界内的固定 seed 随机日线、LONG
多层向上跟随窗口、每跌 5,000 美元新建一层、层间碰撞复位、币本位账本、可配置
Maker/Taker 手续费和真实规则状态转换。页面不会修改源数据。

重新生成这份数据：

```bash
cd ../grid_trading
.venv/bin/python scripts/run_layered_following_grid_simulation.py
```

单组跟随网格仍可通过
`.venv/bin/python scripts/run_single_following_grid_simulation.py` 生成后手动载入。

COIN-M 强平展示样例位于
`viewer/data/coinm-liquidation-adverse-extreme-v1.json`。它使用 5 倍杠杆、10 张
BTCUSD 永续合约和 `ADVERSE_EXTREME` 日线采样，在第 4 根 K 线的 77,000 USD
盘中低点触发强平并终止；没有生成虚构的强平平仓成交。重新生成并查看：

```bash
cd ../grid_trading
python3 scripts/run_coinm_liquidation_demo.py
```

随后在 Viewer 右上角选择“打开 run JSON”，载入上述文件。

6 日逐笔核对数据可通过 `python3 scripts/generate_probe_run.py` 生成，并从页面右上角
手动载入。
数据约束见 `viewer/simulation-run.schema.json`。

Viewer 同时支持两种文档：schema v1 的 `orders` 会在页面内投影为展示意图；
schema v2 直接读取 `intents` 和 `instructions`。兼容投影只影响显示，不改变原始
run 数据或成交结果。没有 9F 状态字段的历史 v1/v2 文档按“正常完成、无保证金
快照”读取；新的强平文档必须同时包含 `run_status`、`margin` 和
`account_events`。

资金费文档使用顶层 `funding_events` 保存强类型结算事件，并在 manifest 中明确
`funding_enabled`、`funding_source` 和 `funding_market_conditioned`。旧文档缺少
这些字段时按零资金费读取；`total_funding` 正数表示账户收到，负数表示账户支付。

新生成的 Fill 使用 `reference_price`、`slippage_amount` 和 `slippage_bps` 记录滑点。
`price` 始终表示账本、费用和保证金实际使用的最终有效成交价。旧文档缺少滑点字段时
Viewer 使用 `reference_price = price`、滑点为零。
