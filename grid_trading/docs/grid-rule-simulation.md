# Grid Rule 仿真雏形

## 固定区间等 U 网格与实盘引擎对照（`feat/grid-rule-parity`）

这条分支从 `grid` 的 `0111a36` 创建。现有 Binance/OKX 实盘和触发式测试网进程
仍使用各自原来的工作目录，不读取本分支的改动。

固定网格的价格边界、等 U 预算、下单数量、初始卖侧仓位、触发式买单激活条件和十单窗口选择现已放在
`grid_rule.fixed_equal_usdt`、`grid_rule.fixed_window`；实盘 `FixedGridEngine`
调用同一组纯规则。规则接收单元格状态和价格，返回目标挂单集合；交易所价格带、
下单、撤单、恢复、资金和 SQLite 仍由实盘适配层负责。

`strategies_system` 的 TradingRuleRegistry 现已注册
`fixed-equal-usdt-grid/v1`。其 `TradingRule.transition` 将已确认 Cell 观测转换成
`RuleIntentProposal` 和撤单计划，并与实盘引擎共用上述纯函数。
`fixed-range-equal-usdt-grid/v1` StrategyDefinition 已把价格范围、预算、格数、
启动模式和窗口参数解析为这条 Rule 的配置，并注册到研究页的策略目录。
StrategyInstance 已能在同一次观测中接收该网格的买入与卖出意图。
`fixed-equal-usdt-grid-simulation/v1` Plugin 通过通用
`StrategyApplicationSimulationAdapter` 运行这条 Strategy。网格事件路由器只将
行情与成交变成单元格观测；初始建仓由 `initial-entry/v1` Rule 发出一次建仓意图，
网格挂单意图由 `fixed-equal-usdt-grid/v1` Rule 发出。旧 `fixed-grid/v1` 仿真 Plugin
仍保留早期语义，其旧实验不会自动归属于新策略。

通用适配器只负责市场事件、成交和 Rule 意图之间的通用执行流程，不保存网格单元格，
也不按网格价格选择订单。`StrategyApplicationEventRouter` 定义事件转换接口；
`FixedGridEventRouter` 实现网格专用的单元格状态、批量成交确认和 Rule 输入转换。
其他策略可实现自己的路由器，无需在通用适配器中增加网格分支。

样本实验配置 `strategies_system/experiments/soxl_equal_usdt_grid_15_90_1m_20261103_16_v1.json`
使用下面的 Binance SOXLUSDT 分钟行情，预建仓、15–90、359 个等比单元格、
50,000 U 总预算；约 0.5003% 格距。该结构实验按用户要求将手续费与资金费设为零，
并暂不做自动复投或真实排队成交量模拟；报告分别列出种子仓位卖出收益、后续单元格
配对收益、期末未实现盈亏与账户总盈亏。

这份两周样本的零费用回放完成 3,620 次单元格买卖配对，配对收益 **2,516.8218 U**；
初始卖侧仓位分批卖出 24 次，收益 **195.2864 U**；期末按单元格成本计的持仓
浮亏 **3,598.8795 U**，合计 **-886.7713 U**（相对 50,000 U 为 **-1.7735%**）。
单元格盈亏与账户权益变动精确对账。期间最大持仓名义价值约 **14,558.10 U**，
因此 50,000 U 主要是覆盖下方格子的预算，并非全程投入的仓位。该实验不含手续费、
资金费、复投、盘口排队和真实价格带，不能把收益数字直接当作实盘预测。

`seeded` 模式按现价预建卖侧仓位；`price_triggered` 模式从零仓位启动，价格触及
单元格买价后才激活该买单。两种模式使用同一套纯规则和执行引擎。

## SOXLUSDT 一分钟历史行情

`strategies_system/scripts/fetch_binance_um_daily_klines.py` 从 Binance Vision 获取
USD-M 永续合约的逐日 1 分钟成交价 K 线，逐个核对官方 `.CHECKSUM` 的 SHA-256，
再用现有导入器检查时间戳无重复、无缺分钟，写入仿真使用的内容寻址 Parquet。
原始 ZIP、来源 URL、校验值和 Parquet 引用保存在本地 `experiments/market_data`
目录；该目录按项目约定不提交 Git。

2026-11-18 获取的一份 SOXLUSDT 样本覆盖 **2026-11-03 至 2026-11-16** 共 14 个
完整 UTC 日、20,160 根 1 分钟 K 线。获取时 11 月 17 日的日档案尚未发布，
因此样本没有拼入其他交易所或其他产品的行情。复现命令：

```bash
cd strategies_system
PYTHONPATH=src python scripts/fetch_binance_um_daily_klines.py \
  --symbol SOXLUSDT --start-date 2026-11-03 --end-date 2026-11-16 \
  --output-root experiments/market_data
```

这份数据是合约成交价 OHLC，不是标记价、盘口或逐笔成交；它可以驱动分钟级订单
行为回放，但无法仅凭 K 线还原同一分钟内的成交先后、排队成交量或资金费。

`grid_server.simulation.FixedGridReplay` 接受 `market_simulator` 的
`MarketSource`（包括 `FixedBarMarketSource` 和历史行情源），在临时数据库和内存交易所
中执行同一个 `FixedGridEngine`。第一根 K 线用收盘价启动策略（预建仓模式同时建仓）；之后只让**前一根 K 线
已经存在**的限价单按 OHLC 范围成交，再以本根收盘价运行下一次策略决策。
同一根 K 线内新挂的限价单不会回填成交，避免用未知的盘中路径制造交易。
回放结果逐根给出买卖挂单数、全量或部分成交订单、逻辑与交易所仓位、完成周期数。
默认触价即全量成交；可注入 `fill_quantity(frame, order)`，返回这一根 K 线的
增量成交量，以验证部分成交、撤余量及后续配对卖单。

该回放是**订单行为对照**，还不能用于净收益结论：部分成交量须由调用方提供；
它没有模拟盘口深度、真实价格带变化、手续费、资金费、保证金和经费用核实后的自动复投。旧的
`FixedGridSimulationAdapter` 仍对应早期 `GridRuleEngine` 的固定网格语义，不能用它
代表当前等 U 天地网格。后续应把成交状态转换、费用账本和策略组合逐步迁入共享
规则/运行时，并以同一批行情比较实盘适配层与仿真适配层的事件序列。

只读结果页使用 `grid_trading/simulation_app.py`，可查看内置固定行情样例，或上传
包含 `open,high,low,close` 四列的 CSV（最多 300 根）。它使用临时数据库和内存
交易所，不读取交易所 API，也不操作实盘策略。启动示例：

```bash
cd grid_trading
python -m streamlit run simulation_app.py --server.address 127.0.0.1 --server.port 8515
```

页面的 `SOXLUSDT` 样例是人工构造的震荡路径，用于观察订单状态，不是历史行情或
收益回测。页面合约过滤器采用 SOXL 的示例精度（价格 0.01、数量 0.01、最小名义
价值 5 U）；上传其他合约数据时也沿用这些示例过滤器。

原有 `market_simulator/viewer` 也能播放这份回放。运行
`grid_trading/scripts/export_fixed_grid_replay_viewer.py`，会在
`market_simulator/viewer/data` 生成触发式与预建仓两份 schema v1 run JSON。
从研究工作台 `experiments.html` 侧栏的「固定网格 · 人工行情」进入，或直接打开
`index.html?run_api=./data/grid-rule-parity-soxl-price_triggered-sample.json`。
导出器保存 K 线、订单生命周期和逐笔成交；`equity` 为空，并在页面明确标注没有
费用或收益账本。这两份样例不会作为研究实验写入 `experiment_results`。

最小回放示例：

```python
from decimal import Decimal
from market_simulator import FixedBarMarketSource
from grid_server.domain import SymbolFilters
from grid_server.simulation import FixedGridReplay

source = FixedBarMarketSource("SOXLUSDT", [
    ("50.45", "50.45", "50.45", "50.45"),
    ("50.45", "50.45", "50.25", "50.25"),
])
result = FixedGridReplay(
    source, symbol="SOXLUSDT", lower_price=Decimal("40"),
    upper_price=Decimal("60"), grid_count=135,
    grid_budget_usdt=Decimal("2000"),
    filters=SymbolFilters(
        tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        contract_type="TRADIFI_PERPETUAL",
    ),
).run()
```

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
