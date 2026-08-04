# Strategy Trading

Strategy Trading 是一套面向交易策略研究、市场仿真和实盘演进的平台。

它的目标不是复刻交易所的完整撮合系统，而是在明确的市场假设和执行语义下，准确计算
策略产生的成交、仓位、手续费、资金费率、杠杆、保证金、盈亏、强平和多计价权益，再用
可复现的批量实验回答三个问题：

1. 策略在不同市场环境中能否持续成立；
2. 收益来自策略本身，还是特定行情、参数或随机路径；
3. 经过样本外验证的策略，如何以尽量相同的决策语义接入实盘。

平台遵循“重计算、轻规则”的原则：仿真器是一台冰冷的执行和记账机器；市场环境、策略
决策和实验研究都位于它的外围。

## 核心研究闭环

```text
市场假设 / 历史行情
        │
        ▼
   MarketFrame ───────────────┐
                              │
纯策略 ── 可选 Rule Port ──► 交易意图
                              │
Study ──► ExperimentSpec ─────┤
                              ▼
                    Simulation Runtime
                              │
                 成交 → 仓位 → 账户 → 强平
                              │
                              ▼
                 Summary / Trace / Metrics
                              │
                              ▼
                    SQLite / Viewer / 比较
```

一次研究由市场、策略、执行、账户、参数轴和随机种子共同定义。实验系统负责展开组合、运行
和保存结果；指标系统只读取结果并评价，不反过来改变成交与记账。

## 架构边界

平台把容易混淆的概念拆成几条稳定边界：

- **Market**：提供历史或生成的 K 线及市场元数据，不包含策略结论；
- **Rule**：实现给定参数后的机械交易规则，例如网格 Cell、建仓价和平仓价转换；
- **Strategy**：决定何时启用什么规则、投入多少资金、如何加减仓、复位或整体退出；
- **Adapter / Plugin**：把纯策略和 Rule 的输出翻译为仿真器或未来实盘端理解的意图；
- **Simulation Runtime**：执行主动或被动交易意图，完成成交、账户和强平计算；
- **Experiment / Study**：组织场景、Seed、参数和优化过程，不参与撮合；
- **Grid Server**：当前生产实盘服务，保持独立部署和独立验收。

纯策略不得依赖 simulator、数据库、Web 或交易所 SDK。仿真和实盘可以复用策略与 Rule
语义，但必须通过不同 Adapter 接入。当前 `grid_server` 仍运行已经过实盘验证的原有实现，
尚未切换到新的 `trading_strategies` 与 `grid_rule` 调用链。

## 仓库结构

```text
strategy_trading/
├── market_simulator/   # 市场协议、仿真执行、实验、指标与只读 Viewer
├── strategies_system/  # 纯策略、仿真适配、实验 Provider 与策略优化
├── grid_trading/       # 网格 Rule 和生产实盘前后端服务
├── docs/               # 平台规格、阶段方案与架构文档
└── scripts/            # 工作区级开发和验收命令
```

| 模块 | 主要职责 | 不负责 |
| --- | --- | --- |
| `market_simulator` | MarketFrame、价格路径、成交、账本、实验、指标、结果页面 | 具体交易策略和实盘交易所接入 |
| `strategies_system` | 纯策略、Simulation Adapter、Plugin Registry、Provider、Study 和优化协议 | 重复实现账户或强平公式 |
| `grid_trading/grid_rule` | 可被仿真和未来实盘复用的网格机械规则 | 建仓时机、资金分配和组合退出 |
| `grid_trading/grid_server` | FastAPI、Streamlit、SQLite、调度器和 Binance 实盘适配 | 承担策略研究与批量实验 |

三个工程位于同一 Git 版本中，便于锁定一次实验所使用的完整代码；这不表示它们被合成了
一个 Python 包，也不取消上述依赖边界。

## 当前能力

### 市场与路径

- 固定节点、历史 K 线和锚点几何布朗运动等市场源；
- 三年长期市场环境及牛市、熊市、延迟上涨、区间震荡和判断失效等场景；
- 场景 × 多随机种子的内容寻址 Parquet 路径集及 Manifest 校验；
- Binance 永续历史数据导入、完整性检查和训练/验证/HOLDOUT 边界。

### 仿真执行与账户

- 主动交易意图和等待 K 线覆盖价格的被动交易意图；
- 手续费、按日资金费率、期末按市价计价；
- U 本位线性合约和币本位反向合约的仓位、保证金、杠杆、盈亏及权益计算；
- 全仓强平检查、强平事件和强平距离等数值结果；
- BTC 与 USDT 两种权益视角。

### 实验与评价

- 市场、策略、执行、账户和参数轴的场景组合；
- 多随机种子、断点恢复、失败重跑和代码/数据 provenance；
- SQLite 保存 Experiment、Run、Summary、指标和可选压缩 Trace；
- 收益、回撤、风险、成交、费用、强平和策略专属指标；
- 策略目录、实验总览、实验详情、指标比较和 K 线回放页面。

### 已注册策略

- BTC HODL 基线；
- 目标强平价约束下的分批止盈阶梯；
- 固定网格；
- 单组跟随网格；
- 分层跟随网格。

这些实现用于验证架构和建立研究基线，不代表已经完成参数优化或具备直接上线条件。

## 策略研究方式

一个新策略通常按以下顺序进入平台：

1. 在 `trading_strategies` 中实现与基础设施无关的决策核心；
2. 如果需要机械规则，通过 Port 调用 `grid_rule` 或未来的其他 Rule；
3. 在 `strategy_simulation` 中注册 Adapter、Plugin、Provider 和策略描述；
4. 选择历史行情或生成场景，定义执行、账户、参数轴和 Seed；
5. 运行 Experiment，先检查成交链路和账户数值，再比较收益、风险和交易频率；
6. 通过 Study 做参数扫描、多目标评价和训练/验证；
7. 最终 HOLDOUT 只用于一次正式样本外验收，不参与调参；
8. 验证通过后再开发实盘 Adapter，并单独完成交易所一致性和安全验收。

当前已经完成策略优化框架和真实历史技术基线；下一阶段是把长期市场 PathSet 接入正式
Experiment/Study，然后开展参数扫描和多场景稳健性研究。强化学习属于更后期的可选方法，
不会替代基线、人工规则改进和样本外验证。

## 快速开始

各模块保留独立依赖和运行入口。运行整个工作区的回归测试：

```bash
./scripts/test_all.sh
```

脚本会依次验证仿真平台、策略系统和实盘服务。可以通过
`STRATEGY_TRADING_PYTHON=/path/to/python` 显式指定已安装 PyArrow 等依赖的解释器。
真实 Binance 测试网下单用例默认关闭。

启动策略实验结果页面：

```bash
cd strategies_system
PYTHONPATH=src python3 -m strategy_simulation \
  serve-results experiments/experiment_results \
  --viewer-root ../market_simulator/viewer \
  --port 8088
```

浏览器访问 `http://127.0.0.1:8088/experiments.html`。

实盘服务的安装、后端和前端启动方式见
[`grid_trading/README.md`](grid_trading/README.md)。实验命令和正式基线重建方式见
[`strategies_system/README.md`](strategies_system/README.md)。

## 数据、密钥与可复现性

以下内容只保存在本地，不得提交：

- 任意 `.env`、`.env.*` 和 `*.env`，包括模拟交易使用的 `test.env`；
- API Key、私钥、证书和其他账户凭证；
- `.venv`、SQLite、Parquet、实验输出、缓存和 TeX 编译产物。

只有不含凭证的 `.env.example` / `*.env.example` 可以进入仓库。市场 Parquet 和实验数据库
默认不进入 Git；配置、Manifest、内容哈希和代码版本共同保证它们能够校验或重建。

正式研究要求 clean worktree 和内容锁定的数据集。探索性运行可以显式允许 dirty 状态，
但其结果会被标记为不可复现，不能升级为正式结论。新实验只记录单体仓库的一个
`strategy_trading` commit。

## 设计文档

- [平台总体规划](docs/platform-plan.md)
- [当前架构说明与图](docs/architecture/README.md)
- [1. 仿真执行规格](docs/01_simulation_execution_v1.md)
- [2. 实验系统规格](docs/02_experiment_system_v1.md)
- [3. 指标系统规格](docs/03_metric_system_v1.md)
- [4. 市场环境方案](docs/04_market_environment_v1.md)
- [5. 策略系统方案](docs/05_strategy_system_v1.md)
- [6. 策略优化方案](docs/06_strategy_optimization_v1.md)

README 只描述平台的稳定意图和使用入口；具体公式、字段、验收标准及阶段性决策以对应规格
文档为准。
