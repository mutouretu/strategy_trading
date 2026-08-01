# Strategies System

策略体系工程同时包含两条明确隔离的代码边界：

- `trading_strategies`：纯策略核心，不依赖 simulator、实验系统或实盘 Server；
- `strategy_simulation`：Simulation Adapter、显式 Plugin Registry、实验 Provider 和策略指标。

当前注册的仿真策略：

- `hold-btc/v1`；
- `target-liquidation-ladder-long/v1`；
- `single-following-grid/v1`（桥接并复用 `grid_trading` 的已有实现）。

## 本地布局

本项目目前以三个并列 checkout 运行：

```text
strategy_trading/
├── market_simulator/
├── grid_trading/
└── strategies_system/
```

开发模式下，`strategy_simulation` 会从上述相邻目录接入公共包和已验证的
COIN-M 组件；`trading_strategies` 本身没有该依赖。

## 验证与实验

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v

PYTHONPATH=src python3 -m strategy_simulation \
  validate experiments/strategy_baselines_v1.json

PYTHONPATH=src python3 -m strategy_simulation \
  run experiments/strategy_baselines_v1.json \
  --database experiments/experiment_results/strategy-baselines-v1.sqlite3 \
  --market-root experiments/market_data
```

基线是三策略 × Seed 42、43，共 6 个 Run。三个策略共用相同市场、账户和执行配置。

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
  --metric-set grid --version v1
```

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
- 不迁移现有网格策略实现；
- 不实现参数搜索、训练或自动优化；
- 不把具体策略写进 `simulation_runtime`。
