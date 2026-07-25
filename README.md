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
└── simulation_runtime/ # 通用策略端口、bar 成交、可插拔账本和运行器
```

项目还包含 `viewer/`：它是策略无关的仿真回放页面，读取标准
`SimulationRun` JSON，展示 TradingView 风格 K 线、活动订单、成交标记、账户状态和
权益曲线。

依赖方向：

```text
market_protocol
      ↑
      ├── market_simulator
      └── simulation_runtime
```

`simulation_runtime` 通过 `SimulationDecisionPort` 协议接收决策组件。协议是通用仿真宿主的
适配端口，不规定接入方是完整策略还是用于基准验证的规则引擎。

具体策略、规则引擎及其仿真适配器归策略应用所有。例如 `grid_rule` 位于
`grid_trading`；同一仓库中的薄适配器把规则状态、订单意图和成交事件映射到
`SimulationDecisionPort`。规则引擎不能依赖市场生成模型或 `simulation_runtime`。

## 当前执行语义

- 决策组件每次返回“当前完整目标订单集合”，不是增量下单命令。
- `MarketFrame` 是经过校验的 OHLC bar，`price` 是 `close` 的兼容别名。
- 新订单从其出现的 MarketFrame 之后开始参与成交判断，不能回看当前 bar。
- LIMIT 订单在 `low <= limit_price <= high` 时按限价全部成交，和 BUY/SELL 无关。
- MARKET 订单在下一根 bar 的 `open` 全部成交。
- bar 内不假设价格路径；同一批成交产生的新订单最早从下一根 bar 生效。
- 订单生命周期记录创建、成交和撤销；关闭后的逻辑订单 key 不能复用。
- 默认线性账本记录现金、持仓均价、已实现盈亏和逐 bar 盯市权益。
- 运行器支持由调用方注入产品专属账本，并将其多币种账户指标写入标准 run JSON。
- 暂不处理手续费、滑点、部分成交、盘口、保证金、资金费和强平。

币本位反向合约公式不属于通用框架；当前实现位于相邻 `grid_trading` 的
`grid_rule.adapters.InverseContractLedger`。Viewer 只读取通用
`account_metrics`，不依赖网格或 COIN-M 代码。

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
总权益和按每日收盘价折算的 USDT 总权益。原来的单组跟随网格结果仍可手动载入对照。

启动本地播放器：

```bash
python3 -m http.server 8088 --directory viewer
```

访问 `http://127.0.0.1:8088/`。播放器也可以打开其他符合
`viewer/simulation-run.schema.json` 的 run JSON。

默认演示包含 2026-01-01 至 2029-01-01 共 1,097 根日线。锚点路径多次上下穿越，并
经过 40,000 美元和 200,000 美元；市场源使用反射边界，保证全部 OHLC 都位于该价格
区间。

网格规则引擎从首日收盘价下方按 4% 等比间隔挂 5 档买单，每档 0.01 BTC。某档买入后，在该档
上方一个等比间隔挂出对应卖单；卖出后重新恢复该档买单。它只使用限价挂单，不允许
裸卖空。

用于逐笔核对撤单、限价单和市价单语义的 6 日确定性演示仍可生成：

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

1. 增加固定 seed 的随机订单属性测试，验证资产与订单状态不变量。
2. 在 `grid_trading` 内建立网格仿真适配器并做行为对照。
3. 从 Web 引擎逐步抽出纯网格状态与意图，通过该适配器实现 `SimulationDecisionPort`。
4. 用同一人工价格序列对比旧引擎和新规则引擎。
5. 闭环一致后增加批量实验和策略专属指标。
