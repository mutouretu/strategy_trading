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

相邻 `strategies_system` 中的单组和分层 Strategy Adapter 使用相同的被动意图解析机制。Fill 或当日
`on_market` 新产生的意图最早从下一根日线生效；ENTRY/EXIT 分别映射为普通指令和
`reduce_only` 指令。适配器还通过只读 `visible_intents()` 提供报告快照。
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

单组三年示例已经迁入 `strategies_system`，调用顺序是：

```text
SimulationRunner
    → SingleFollowingGridSimulationAdapter
    → SingleFollowingGridStrategy
    → GridRulePort
    → GridRuleEnginePort
    → GridRuleEngine
```

该策略只在启动时部署一组跟随网格，并始终维护这一组网格。直接调用
`GridRuleSimulationAdapter` 的静态规则行为由单元测试保留，不再维护另一套三年
演示脚本。

改进版多层策略使用相同边界：

```text
SimulationRunner
    → LayeredFollowingGridSimulationAdapter
    → LayeredFollowingGridStrategy
    → GridRulePort
    → GridRuleEnginePort（每层、每一代一个实例）
    → GridRuleEngine
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

三年 COIN-M 单组和分层示例均由 `strategies_system` 维护：

```bash
../strategies_system/experiments/layered_following_grid_baseline.json
../strategies_system/experiments/single_following_grid_baseline.json
```

统一运行入口：

```bash
cd ../strategies_system
PYTHONPATH=src python3 -m strategy_simulation plan \
  experiments/single_following_grid_baseline.json
PYTHONPATH=src python3 -m strategy_simulation run \
  experiments/single_following_grid_baseline.json
PYTHONPATH=src python3 -m strategy_simulation plan \
  experiments/layered_following_grid_baseline.json
PYTHONPATH=src python3 -m strategy_simulation run \
  experiments/layered_following_grid_baseline.json
```

配置、状态、Summary 和压缩 Trace 保存到每实验一个 SQLite；K 线按内容寻址保存为
Parquet。策略参数、账户余额、费率和 Seed 统一从实验 JSON 读取，不再散落在 Python
脚本参数中。

Viewer 数据由 `strategies_system` 的通用 CLI 通过 `--export-viewer` 显式导出，
不再维护 `grid_trading/scripts` 下的策略脚本。

```text
../market_simulator/viewer/data/single-following-grid-coinm-long-3y-seed-42.json
```

单组配置使用 `1 BTC` 现货底仓、`0.1 BTC` 合约钱包和每格 `0.01 BTC`；分层配置
使用 `1 BTC` 现货底仓、`0.2 BTC` 合约钱包和每格 `0.003 BTC`。两者均使用
`100 USD` 合约面值、Maker `0.0002` 与 Taker `0.0005` 的研究假设。需要修改时应
复制实验 JSON，并先运行 `plan` 检查展开结果。Viewer 默认载入分层结果；单组结果
保留作对照。

测试：

```bash
PYTHONPATH=../market_simulator/packages/market_protocol/src:\
../market_simulator/packages/market_simulator/src:\
../market_simulator/packages/simulation_runtime/src \
.venv/bin/python -m unittest \
  tests.test_grid_rule_engine \
  tests.test_grid_rule_simulation \
  tests.test_inverse_contract_ledger \
  tests.test_inverse_contract_margin \
  tests.test_inverse_margin_execution \
  tests.test_inverse_liquidation_runtime -v
```
