# Grid Rule 仿真雏形

当前分支将网格规则组织为一个独立 Python 命名空间：

```text
grid_rule/
├── engine.py             # 单组网格状态转换
├── grid.py               # Cell 与价格计算
├── models.py             # 规则输入、状态与目标订单
└── adapters/
    ├── simulation.py     # 通用 simulator 适配
    └── inverse_ledger.py # 币本位反向合约账本
```

`grid_rule` 顶层核心不依赖 Web、SQLite、Binance、`market_protocol` 或
`simulation_runtime`；只有 `grid_rule.adapters` 可以依赖外部运行框架。第一版只实现：

- 与现有 Web 相同的 LONG/SHORT 锚点和等比 Cell 生成；
- 与现有引擎相同的初次挂单触发条件；
- 全量成交后的 entry → exit → 新周期状态转换；
- LONG 向上、SHORT 向下的单向跟随窗口；
- 只回收无持仓 Cell，持仓阻塞时保留临时超额窗口；
- USD-M 每格报价名义金额到基础币数量的向下取整；
- COIN-M 按 `order_coin_qty × Cell 价格 ÷ contractSize` 四舍五入为有效合约张数；
- 完整目标订单集合。

第一版明确不包含部分成交、手续费、滑点、资金费、保证金占用、强平和实盘异常恢复。
这些能力会在基本仿真闭环验证后逐项对齐。因此当前 COIN-M 结果用于核对规则与盈亏公式，
不能用于判断真实杠杆下是否会被强平。

`grid_rule.adapters.GridRuleSimulationAdapter` 实现通用
`SimulationDecisionPort`，只负责 `MarketFrame`、`SimOrder`、`SimFill` 与规则引擎
模型之间的转换。它不是完整交易策略。`InverseContractLedger` 负责币本位反向合约记账：

默认三年示例已经接入最小高层策略，调用顺序是：

```text
SimulationRunner
    → SingleFollowingGridSimulationAdapter
    → SingleFollowingGridStrategy
    → GridRuleEngine
```

该策略只在启动时部署一组跟随网格，并始终维护这一组网格。`--static-grid` 不走策略层，
保留为直接调用 `GridRuleSimulationAdapter` 的规则基线。

改进版多层策略的调用顺序保持同一边界：

```text
SimulationRunner
    → LayeredFollowingGridSimulationAdapter
    → LayeredFollowingGridStrategy
    → GridRuleEngine（每层、每一代一个实例）
```

它从 65,000 美元开始，收盘价每向下跨过 5,000 美元部署一层。下层上沿触及上层
下沿时，下层回到自己的初始锚点。复位只撤销旧层尚未成交的建仓单；已有仓位的
平仓单继续由退役规则实例维护，直到完成平仓，避免复位造成孤儿仓位。

```text
LONG PnL(BTC)  = 张数 × contractSize × (1 / 建仓价 - 1 / 平仓价)
SHORT PnL(BTC) = 张数 × contractSize × (1 / 平仓价 - 1 / 建仓价)
```

现货 BTC 底仓和合约钱包分开保存。网格成交只改变合约张数和合约钱包的 BTC 盈亏，
不会卖出或扣减现货底仓。每根日线同时输出：

- 现货 BTC；
- 合约钱包、合约浮盈和合约权益（BTC）；
- 总权益 BTC；
- 按当日 BTC 收盘价折算的总权益 USDT。

运行三年 COIN-M 示例：

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py
```

默认运行跟随网格，结果写入相邻 `market_simulator` 工程的：

```text
viewer/data/single-following-grid-coinm-long-3y-seed-42.json
```

示例初始账户为 `1 BTC` 长期现货底仓和 `0.1 BTC` 合约钱包，每格配置
`0.01 BTC`、每张合约面值 `100 USD`。这几个参数只用于当前可复现实验，可在脚本中调整。

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py \
  --spot-btc 1 \
  --futures-wallet-btc 0.2 \
  --order-coin-qty 0.005
```

使用之前的 USD-M 线性账本：

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py --linear
```

静态网格仍可通过 `--static-grid` 生成对照；该参数可和 `--linear` 组合。

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py --static-grid
```

Viewer 默认载入 COIN-M 跟随网格结果。账户区分开显示 BTC 与 USDT 总权益，权益曲线可在
BTC 和 USDT 计价之间切换。run 同时记录合约子账户最低权益；如果该权益不大于零，
Viewer 会显示风险提示。由于尚未实现维持保证金和强平，这个提示只说明账户已经不可能
继续按当前仿真成交，真实交易所通常会更早强平。

运行改进版多层跟随网格：

```bash
.venv/bin/python scripts/run_layered_following_grid_simulation.py
```

结果写入：

```text
viewer/data/layered-following-grid-coinm-long-3y-seed-42.json
```

Viewer 默认载入这份改进版结果。原来的单组跟随网格脚本和结果仍保留作对照。

测试：

```bash
PYTHONPATH=../market_simulator/packages/market_protocol/src:\
../market_simulator/packages/market_simulator/src:\
../market_simulator/packages/simulation_runtime/src \
.venv/bin/python -m unittest \
  tests.test_grid_rule_engine \
  tests.test_grid_rule_simulation \
  tests.test_layered_following_grid_strategy \
  tests.test_layered_following_grid_simulation \
  tests.test_single_following_grid_strategy \
  tests.test_inverse_contract_ledger -v
```
