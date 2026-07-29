# Grid Rule 仿真雏形

当前分支将网格规则组织为一个独立 Python 命名空间：

```text
grid_rule/
├── engine.py             # 单组网格状态转换
├── grid.py               # Cell 与价格计算
├── models.py             # 规则输入、状态与目标订单
└── adapters/
    ├── simulation.py      # 通用 simulator 适配
    ├── inverse_fee.py     # 币本位反向合约手续费
    ├── inverse_funding.py # 币本位反向合约资金费结算
    ├── inverse_ledger.py  # 币本位反向合约账本
    └── inverse_margin.py  # 币本位杠杆与保证金纯计算
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

Rule Core 明确不包含手续费和资金费；费率属于仿真配置，由 Runtime 的 FeeModel、
FundingModel 和产品适配器计算。当前已经提供不修改账本的 COIN-M 保证金模型，
可以计算钱包、未实现盈亏、
仓位名义价值、初始保证金、固定或连续分档维持保证金、资金占用、预估强平价格和
当前强平状态。分档表显式保存产品、instrument、来源、生效日期、版本和内容哈希。
它已经接入 Runner 的成交前保证金校验：产品适配器在独立账本副本上应用含手续费的
候选 Fill，新增敞口导致可用余额小于零时不成交。Runtime 已支持
`CLOSE_ONLY` 和 `ADVERSE_EXTREME` 两种日线强平采样：后一种先检查开盘跳空，
再用多仓 low 或空仓 high 检查盘中触线；当前三年脚本尚未注入 MarginModel，所以
基线结果保持不变。

`grid_rule.adapters.GridRuleSimulationAdapter` 实现
`SimulationTradePort`。适配器保存 `GridOrderIntent` 生命周期，在意图进入当前日线
以前已经生效且目标价格被 OHLC 覆盖时，生成明确价格的 `TradeInstruction`。Runtime
不再替网格判断是否触价。

单组和分层 Strategy Adapter 使用相同的被动意图解析机制。Fill 或当日
`on_market` 新产生的意图最早从下一根日线生效；ENTRY/EXIT 分别映射为普通指令和
`reduce_only` 指令。三个适配器还通过只读 `visible_intents()` 提供报告快照。
`InverseContractLedger` 继续只负责币本位反向合约记账；
`FixedRateInverseContractFundingModel` 按 `张数 × contractSize ÷ mark × rate`
生成 BTC 钱包变动；
`InverseContractMarginModel` 读取真实账本计算保证金事实，或者读取账本副本计算
候选成交后的预计保证金；投影失败不会修改真实账本。

新执行路径的调用方式为：

```python
SimulationRunner(
    source,
    trade_port=adapter,
    ledger_factory=...,
    fee_model=...,
    funding_model=...,
)
```

零费率下，三个网格适配器在相同固定行情上的 Fill、账本和权益保持原结果；三年
seed 42 的单组与分层结果仍分别为 155 和 736 笔 Fill。标准样例显式使用
Maker `0.0002`、Taker `0.0005` 的研究假设，并将费率写入 manifest。Viewer 可逐日
显示等待、成交和撤销的网格意图以及逐笔、累计手续费。

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
Fee(BTC)       = 张数 × contractSize / 成交价 × feeRate
Funding(BTC)   = -仓位方向 × 张数 × contractSize / mark × fundingRate
```

现货 BTC 底仓和合约钱包分开保存。网格成交只改变合约张数和合约钱包的 BTC 盈亏，
资金费也只改变合约钱包，不会卖出或扣减现货底仓。每根日线同时输出：

- 现货 BTC；
- 合约钱包、合约浮盈和合约权益（BTC）；
- 总权益 BTC；
- 按当日 BTC 收盘价折算的总权益 USDT。

`grid_rule` 输出的被动网格意图由 Runtime 归类为 PASSIVE。即使仿真启用
`FixedBpsSlippageModel`，这类触价成交仍保持网格指定价格；固定 bps 只作用于
ACTIVE 指令。最终 Fill 同时记录参考价、有效价和滑点，COIN-M 手续费按有效价计算。

运行三年 COIN-M 示例：

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py
```

默认运行跟随网格，结果写入相邻 `market_simulator` 工程的：

```text
viewer/data/single-following-grid-coinm-long-3y-seed-42.json
```

示例初始账户为 `1 BTC` 长期现货底仓和 `0.1 BTC` 合约钱包，每格配置
`0.01 BTC`、每张合约面值 `100 USD`，并显式假设 Maker 费率 `0.0002`、Taker
费率 `0.0005`。这些参数只用于当前可复现实验，不代表账户实际费率，均可在脚本中调整。

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py \
  --spot-btc 1 \
  --futures-wallet-btc 0.2 \
  --order-coin-qty 0.005 \
  --maker-fee-rate 0.0002 \
  --taker-fee-rate 0.0005
```

使用之前的 USD-M 线性账本：

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py --linear
```

静态网格仍可通过 `--static-grid` 生成对照；该参数可和 `--linear` 组合。

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py --static-grid
```

Viewer 默认载入 COIN-M 跟随网格结果。账户区分开显示 BTC 与 USDT 总权益、净已实现
盈亏和累计手续费，权益曲线可在 BTC 和 USDT 计价之间切换。run 同时记录合约子账户
最低权益；如果该权益不大于零，Viewer 会显示风险提示。由于尚未将逐 Bar 保证金快照
和强平事件接入当前示例及 run JSON，这个提示只说明账户已经不可能继续按当前仿真
成交。Runtime 内部已经可以按 close 或盘中最不利价格终止，但 Viewer 尚未显示该
终止状态。

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
  tests.test_inverse_contract_ledger \
  tests.test_inverse_contract_margin \
  tests.test_inverse_margin_execution \
  tests.test_inverse_liquidation_runtime -v
```
