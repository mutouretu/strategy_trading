# Simulation Viewer

通用仿真运行结果播放器。页面使用 TradingView 风格的深色配色，支持：

- 标准 OHLC 日线 K 线；
- 播放、暂停、逐日推进和时间轴跳转；
- 悬停十字线与精确 OHLC；
- 活动限价订单水平线；
- 绿色买单/B 成交标记与红色卖单/S 成交标记；
- 当前现金、持仓、持仓均价、已实现盈亏和总权益；
- 随时间推进的账户权益曲线；
- 截至当前回放日期的成交明细；
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
多层向上跟随窗口、每跌 5,000 美元新建一层、层间碰撞复位、币本位账本和真实规则
状态转换。页面不会修改源数据。

重新生成这份数据：

```bash
cd ../grid_trading
.venv/bin/python scripts/run_layered_following_grid_simulation.py
```

单组跟随网格仍可通过
`.venv/bin/python scripts/run_single_following_grid_simulation.py` 生成后手动载入。

6 日逐笔核对数据可通过 `python3 scripts/generate_probe_run.py` 生成，并从页面右上角
手动载入。
数据约束见 `viewer/simulation-run.schema.json`。
