# Strategies System

策略体系工程包含三条明确隔离的代码边界：

- `trading_strategies`：纯策略核心，不依赖 simulator、实验系统或实盘 Server；
- `strategy_simulation`：Simulation Adapter、实验组件、显式 Plugin Registry、实验 Provider 和策略指标。
- `strategy_optimization`：第 6 部分的 Study、研究协议、ExperimentSpec 编译和研究状态持久化。

当前注册的仿真策略：

- `hold-btc/v1`；
- `target-liquidation-ladder-long/v1`；
- `single-following-grid/v1`；
- `layered-following-grid/v1`。
- `fixed-grid/v1`（支持 USD-M/COIN-M 与 long/short）。

## 单体仓库布局

本项目位于统一的 `strategy_trading` 单体仓库中：

```text
strategy_trading/
├── market_simulator/
├── grid_trading/
└── strategies_system/
```

开发模式下，`strategy_simulation` 会从同一工作树的相邻目录接入公共包和已验证的
COIN-M 数值实现；策略实验使用的市场、账户和执行组件定义位于本模块的
`strategy_simulation.components`。`trading_strategies.grid_following` 只依赖其中的 `grid_rule`
DTO 和策略侧 `GridRulePort`，不创建具体 `GridRuleEngine`，也不依赖
`grid_server`、simulator 或实验系统。`GridRuleEnginePort` 的具体包装位于
`strategy_simulation.adapters`。

`strategy_optimization` 只在策略研究时运行。它把 Study 编译为现有
`ExperimentSpec`，实际展开、执行、记账、指标和 Trace 仍由既有系统负责；
`trading_strategies` 和未来实盘 Server 均不依赖该包。

## 验证与实验

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v

PYTHONPATH=src python3 -m strategy_simulation \
  validate experiments/strategy_baselines_v1.json

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/strategy_baselines_v1.json \
  --database experiments/experiment_results/strategy-baselines-v1.sqlite3 \
  --market-root experiments/market_data

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/single_following_grid_baseline.json \
  --database experiments/experiment_results/single-following-grid.sqlite3 \
  --market-root experiments/market_data

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/layered_following_grid_baseline.json \
  --database experiments/experiment_results/layered-following-grid.sqlite3 \
  --market-root experiments/market_data

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/usdm_fixed_short_grid_structure_v1.json \
  --database experiments/experiment_results/usdm-fixed-short-grid-structure-v1.sqlite3 \
  --market-root experiments/market_data

PYTHONPATH=src python3 scripts/import_binance_futures_klines.py \
  --archive-root /path/to/binance-vision-zips \
  --output-root experiments/market_data \
  --instrument BTCUSDT \
  --start-date 2026-03-22 \
  --end-date 2026-07-19

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/usdm_fixed_short_grid_pionex_frequency_v1.json \
  --database experiments/experiment_results/usdm-fixed-short-grid-pionex-frequency-v1.sqlite3 \
  --market-root experiments/market_data \
  --allow-dirty
```

`usdm_fixed_short_grid_structure_v1.json` 用 5 分钟可执行路径验证 U 本位固定
空头网格、USDT 账本和强平链路。它只沿用 30,000–70,168 区间；网格数、
本金、杠杆和真实行情尚未按派网订单校准，因此不能与派网显示的 89% 网格年化
直接比较。

`usdm_fixed_short_grid_pionex_frequency_v1.json` 是成交频次校准实验：读取带
SHA-256 身份校验的 Binance USD-M 永续 1 分钟历史 Parquet，使用约 380 个
等比空头网格和固定 USDT 下单。运行期间 Maker/Taker 费率均为零；派网盈利结算时
收取的 20% 服务费不参与该实验，因为它不改变成交次数。10,000 USDT 钱包用于避免
资金约束干扰频次，实验结果不能据此评价真实资金收益率。

基线是三策略 × Seed 42、43，共 6 个 Run。三个策略共用相同市场、账户和执行配置。

## 第 6 部分：Study 与 COIN-M 正式基线

6A development scaffold 同时覆盖 HODL、阶梯止盈、单组跟随网格和分层跟随网格，
并显式排除最终 HOLDOUT 市场：

```bash
PYTHONPATH=src python3 -m strategy_optimization \
  validate research/scenario_studies/coinm_btc_baseline_scaffold_v1.json

PYTHONPATH=src python3 -m strategy_optimization \
  plan research/scenario_studies/coinm_btc_baseline_scaffold_v1.json

PYTHONPATH=src python3 -m strategy_optimization \
  run research/scenario_studies/coinm_btc_baseline_scaffold_v1.json \
  --database experiments/experiment_results/coinm-btc-baseline-scaffold-v1.sqlite3 \
  --market-root experiments/market_data \
  --allow-dirty \
  --allow-development-data
```

最后两个开关只允许当前开发工作树和生成行情做调用链验收。正式研究必须使用 clean
代码版本和 `CONTENT_LOCKED` 数据集，不能通过开关把 development 结果升级为正式结论。

首个目标协议位于
`research/protocols/coinm_btc_accumulation_v1.json`：以 BTC 权益及相对 HODL 的
超额 BTC 为主目标。真实 `BTCUSD_PERP` 1 分钟数据的训练、验证和最终样本外边界已在
`research/protocols/btc_coinm_historical_split_v1.json` 固定并升级为
`CONTENT_LOCKED`。正式数据和基线可用以下命令重建：

```bash
PYTHONPATH=src python3 scripts/prepare_coinm_baseline_data.py \
  --cache-root /tmp/strategy_trading_coinm_6b_archives

PYTHONPATH=src python3 -m strategy_optimization \
  validate research/scenario_studies/coinm_btc_formal_baseline_v1.json

PYTHONPATH=src python3 -m strategy_optimization \
  plan research/scenario_studies/coinm_btc_formal_baseline_v1.json

PYTHONPATH=src python3 -m strategy_optimization \
  run research/scenario_studies/coinm_btc_formal_baseline_v1.json \
  --database experiments/experiment_results/coinm-btc-formal-baseline-v1.sqlite3 \
  --market-root experiments/market_data
```

数据准备器校验 Binance 官方归档校验和、每日分钟完整性、OHLC 和时间边界。官方
K 线归档缺失的 2026-06-29 使用同一 `BTCUSD_PERP` 合约的官方 `aggTrades` 重建，
并在 `research/data_manifests/btc_coinm_historical_split_v1.json` 保留来源与哈希。
不使用其他合约或市场代理。HOLDOUT 只锁定内容，不在该 Study 中运行。

分别计算通用、策略专属和网格指标：

```bash
PYTHONPATH=src python3 -m strategy_simulation.metrics \
  evaluate-experiment experiments/experiment_results/strategy-baselines-v1.sqlite3 \
  --metric-set core --version v1

PYTHONPATH=src python3 -m strategy_simulation.metrics \
  evaluate-experiment experiments/experiment_results/strategy-baselines-v1.sqlite3 \
  --metric-set btc-accumulation --version v1

PYTHONPATH=src python3 -m strategy_simulation.metrics \
  evaluate-experiment experiments/experiment_results/strategy-baselines-v1.sqlite3 \
  --metric-set grid --version v2
```

正式基线将上面三个 MetricSet 的数据库参数改为
`coinm-btc-formal-baseline-v1.sqlite3`。全部指标成功后，生成并持久化相对 HODL 的
不可变 BTC 基线报告：

```bash
PYTHONPATH=src python3 -m strategy_optimization \
  baseline-report research/scenario_studies/coinm_btc_formal_baseline_v1.json \
  --database experiments/experiment_results/coinm-btc-formal-baseline-v1.sqlite3
```

正式研究必须从 clean worktree 运行，确保 provenance 中
`reproducible=true`。`--allow-dirty` 仅用于技术验收，带该标记的结果不能作为正式
可复现结论。基线报告固定使用相同市场、执行、账户与 Seed 的 HODL Run 计算 BTC
超额；USDT 权益仅作为另一计价视角，不等同于策略赚取的 BTC。

通过现有 Viewer 查看策略目录、公式、实验指标与 K 线回放：

```bash
PYTHONPATH=src python3 -m strategy_simulation \
  serve-results experiments/experiment_results \
  --viewer-root ../market_simulator/viewer \
  --port 8088
```

浏览器打开 `http://127.0.0.1:8088/experiments.html`。

## 明确不在 v1 内的内容

- 不修改实盘 `server`；
- 不复制 COIN-M 盈亏、保证金或强平公式；
- 不迁移尚未选定的其他网格策略实现；
- 6A/6B 不实现参数搜索、训练或自动优化；
- 不把具体策略写进 `simulation_runtime`。
