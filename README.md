# Strategy Trading Platform

这是策略研究、市场仿真和网格实盘交易的单体仓库。Git 历史由原来的
`market_simulator`、`strategies_system` 和 `grid_trading` 三个仓库完整合并而来；
单体仓库统一版本、标签和实验 provenance，但不取消各模块的软件边界。

## 目录

```text
strategy_trading/
├── market_simulator/   # 市场、执行、实验、指标与只读 Viewer
├── strategies_system/  # 纯策略、仿真插件、Study 与优化研究
├── grid_trading/       # 网格 Rule 与实盘前后端服务
├── docs/               # 平台规格、开发计划与架构文档
└── scripts/            # 单体仓库级开发命令
```

运行时依赖方向保持为：

```text
market_protocol
      ↑
      ├── market_simulator
      └── simulation_runtime
                ↑
                └── experiment_system ← metric_system
                              ↑
                    strategy_simulation
                              ↑
                     trading_strategies
                              ↓
                         grid_rule
```

`grid_server` 仍是独立的实盘部署单元；合并 Git 仓库不表示把实盘服务、策略核心和
仿真框架合成一个 Python 包。

## 开发与测试

各子项目继续保留自己的依赖和启动方式。运行整个工作区的回归测试：

```bash
./scripts/test_all.sh
```

脚本优先使用 `grid_trading/.venv/bin/python` 运行实盘服务测试；不存在时使用
顶层 `.venv` 或 `python3`。也可以通过 `STRATEGY_TRADING_PYTHON` 显式指定解释器；
所选解释器必须已经安装 PyArrow 等项目依赖。真实 Binance 测试网下单用例默认关闭，
只有显式设置原有测试开关时才会运行。

长期市场环境、Experiment 和 Study 的使用方式分别见：

- `market_simulator/market_environments/README.md`；
- `market_simulator/README.md`；
- `strategies_system/README.md`；
- `docs/platform-plan.md`。

## 密钥与本地数据

以下内容不得提交：

- 任意 `.env`、`.env.*` 和 `*.env`，包括模拟交易使用的 `test.env`；
- API Key、私钥和证书；
- `.venv`、SQLite、Parquet、实验结果、运行输出和缓存；
- TeX 编译产物。

只有不含凭证的 `.env.example` / `*.env.example` 可以进入仓库。实盘和模拟交易凭证
继续保存在本地被忽略的环境文件中。

## 版本与可复现性

新实验只记录单体仓库的一个 `strategy_trading` commit。旧 SQLite 结果中的三个仓库
commit 仍可读取，不做数据迁移。新版本使用平台级标签，旧仓库继续作为历史只读来源。
