# Grid Trading

网格交易规则、策略研究与生产级实盘服务的一体化工程。当前实盘服务支持 Binance
U 本位与币本位永续触发式移动等比网格，使用 SQLite、共享调度器和 Web/API，
不依赖旧命令行版本的 CSV 恢复或 tmux 管理。

## 架构

```text
Streamlit / other client
        │ HTTP
        ▼
FastAPI service ── SQLite (WAL)
        │
        ▼
StrategySupervisor
        │ one shared subprocess
        ▼
StrategyScheduler ── N lightweight TradingEngine states
        │ shared symbol snapshots
        ▼
Binance USDⓈ-M / COIN-M Futures
```

顶层代码按业务语义分为：

- `grid_rule/`：单组网格的机械交易规则；核心不依赖 Web、交易所或 simulator。
- `grid_strategies/`：高层网格策略；当前包含用于验证完整调用链的单组跟随网格策略。
- `grid_experiments/`：把网格组件注册到通用实验系统的薄 Provider 和 CLI 宿主。
- `grid_metrics/`：向通用指标系统注册币本位双计价输入和网格专属指标。
- `grid_server/`：生产级实盘前后端服务，内部职责如下。

`grid_server` 按职责分层：

- `grid_server/domain/`：策略、Cell、订单模型及纯等比网格计算。
- `grid_server/ports/`：交易所等外部能力的抽象接口。
- `grid_server/application/`：触发、开平仓、移动窗口、策略用例和持仓一致性。
- `grid_server/infrastructure/`：Binance、SQLite 和轮询快照的具体适配器。
- `grid_server/runtime/`：共享调度器、进程监管和 worker 入口。
- `grid_server/interfaces/`：FastAPI 接口和 Streamlit 使用的 HTTP 客户端。
- `grid_server/shared/`：环境配置、价格格式化等无业务状态工具。
- `grid_server/*.py`：旧导入路径的薄兼容层，不再承载业务实现。

当前 `grid_server` 仍运行已经过实盘验证的原有实现，尚未切换到 `grid_rule` 或
`grid_strategies`。规则和策略完成仿真验证后，再单独进行一次实盘迁移和验收。

更完整的依赖边界和文件归属说明见 `docs/architecture.md`。
旧命令行版本的代码谱系和行为测试迁移记录见 `docs/legacy-cli-history.md`。
规则引擎仿真的当前范围和运行方法见 `docs/grid-rule-simulation.md`。这里的规则引擎只处理
给定参数后的 Cell 与订单转换；建网格时机、资本分配和整体退出属于后续高层策略。

## 实验系统

`grid_experiments` 注册 `grid-simulation/v1` Provider，把以下组件组装为
现有 `SimulationRunner`，不会复制实验展开、SQLite 或 Parquet 逻辑：

- `anchored-gbm/v1`；
- `single-following-grid/v1`；
- `layered-following-grid/v1`；
- `daily-bar-execution/v1`；
- `coinm-inverse/v1`。

安装相邻 `market_simulator` 的实验依赖：

```bash
.venv/bin/pip install -r requirements-experiments.txt
```

三年单组跟随网格基线位于
`experiments/single_following_grid_baseline.json`：

```bash
.venv/bin/python -m grid_experiments validate \
  experiments/single_following_grid_baseline.json
.venv/bin/python -m grid_experiments plan \
  experiments/single_following_grid_baseline.json
.venv/bin/python -m grid_experiments run \
  experiments/single_following_grid_baseline.json
```

三年分层跟随网格基线位于
`experiments/layered_following_grid_baseline.json`：

```bash
.venv/bin/python -m grid_experiments validate \
  experiments/layered_following_grid_baseline.json
.venv/bin/python -m grid_experiments plan \
  experiments/layered_following_grid_baseline.json
.venv/bin/python -m grid_experiments run \
  experiments/layered_following_grid_baseline.json
```

批量样例位于 `experiments/single_following_grid_matrix.json`。它展开
2 个策略候选 × 2 个 `grid_count` 参数值 × 2 个 Seed，共 4 个 Scenario、
8 个 Run：

```bash
.venv/bin/python -m grid_experiments validate \
  experiments/single_following_grid_matrix.json
.venv/bin/python -m grid_experiments plan \
  experiments/single_following_grid_matrix.json
.venv/bin/python -m grid_experiments run \
  experiments/single_following_grid_matrix.json
```

8 个 Run 按 plan 的固定顺序写入同一个 SQLite。行情只由市场配置和 Seed 决定，
因此这个样例只产生并复用 2 条内容寻址 Parquet 市场路径。

用于观察网格关键参数敏感性的实验位于
`experiments/single_following_grid_key_parameter_matrix.json`。它显式展开：

- 网格数 `grid_count`：3、5；
- 等比网格间距 `grid_ratio`：2%、4%、6%；
- 每格下单量 `order_coin_quantity`：0.005 BTC、0.01 BTC；
- 随机种子：42、43。

该实验账户启用 `flat-maintenance/v1`：5 倍杠杆、0.5% 固定维持保证金率，
并用当根 K 线对持仓最不利的极值检查强平。结果因此会记录强平状态、最大维持
保证金使用率、最低保证金缓冲和最大有效杠杆；这是研究配置，不改变无保证金的
行为兼容基线。账户的 1.1 BTC 全部放入合约钱包，由单一 COIN-M 账户作为全仓
保证金使用；`spot_btc` 设为 0，避免重复计算总资产。

前三项产生 12 个 Scenario；每个 Scenario 使用 2 个 Seed，共 24 个 Run。结果页的
场景标题会直接显示这三个参数，可据此比较收益、回撤、成交数和完成循环数：

```bash
.venv/bin/python -m grid_experiments run \
  experiments/single_following_grid_key_parameter_matrix.json
```

实验完成后，使用 `grid_metrics` 一次计算通用 `core/v1` 和网格 `grid/v1`：

```bash
.venv/bin/python -m grid_metrics evaluate-experiment \
  experiments/experiment_results/single-following-grid-matrix.sqlite3
```

它会分别保存 BTC / USDT 总权益、BTC 合约权益、收益、回撤、波动、仓位、保证金、
强平等通用指标，以及完成循环数、未闭合 entry、按 role/generation 的成交数、每循环
平均净收益和手续费。重复执行默认按输入指纹幂等跳过；需要用新公式覆盖同版本结果时
必须显式增加 `--recompute`。计算某一个 Run 时使用：

```bash
.venv/bin/python -m grid_metrics evaluate-run <database> <run-id>
```

两个原 Viewer 演示脚本已经收口为实验 CLI 的薄封装，不再自行构造 MarketSource、
策略、Runner、账本或 Summary：

```bash
.venv/bin/python scripts/run_single_following_grid_simulation.py
.venv/bin/python scripts/run_layered_following_grid_simulation.py
```

脚本分别运行上面的单 Run 配置，并通过显式 `--export-viewer` 生成原路径下的
Viewer JSON。研究参数不再通过脚本参数维护；需要调整 Seed、账户或策略参数时，
复制并修改实验 JSON，再使用 `grid_experiments plan/run`。开发期 dirty 仓库可向
脚本增加 `--allow-dirty`；如需隔离探索结果，还可显式指定 `--database`、
`--market-root` 和 `--output`。

`controls.continue_on_error` 决定当前批次遇错后是否继续。再次运行完全相同的 clean
实验时，已成功 Run 会自动跳过；失败和异常中断分别需要显式处理：

```bash
.venv/bin/python -m grid_experiments run \
  experiments/single_following_grid_matrix.json --rerun-failed
.venv/bin/python -m grid_experiments run \
  experiments/single_following_grid_matrix.json --resume-interrupted
```

Trace 归档和清理也通过同一薄 CLI 进入通用实验系统：

```bash
.venv/bin/python -m grid_experiments archive-run \
  experiments/experiment_results/<experiment>.sqlite3 --run-id <run-id>
.venv/bin/python -m grid_experiments purge-traces \
  experiments/experiment_results/<experiment>.sqlite3
.venv/bin/python -m grid_experiments purge-traces \
  experiments/experiment_results/<experiment>.sqlite3 --confirm
```

第一次 `purge-traces` 只预览；`--confirm` 才会清理 `STANDARD` Trace。
`ARCHIVED` Trace 不会被普通清理命令删除。

2F 的通用只读结果页由同一个薄 CLI 启动：

```bash
.venv/bin/python -m grid_experiments serve-results \
  experiments/experiment_results \
  --viewer-root ../market_simulator/viewer \
  --port 8088
```

访问 `http://127.0.0.1:8088/` 可以浏览实验、筛选 Run、并排查看数据库中已有的
Summary 和指标，按指定指标排序，查看每个 Scenario 的 P05 / 中位数 / P95 / 最差值
与强平率，并把带有 `STORED` Trace 的 Run 直接送入 K 线播放器。浏览过程只读，
在内存中组合 SQLite Trace 和 Parquet K 线，不生成临时 JSON，也不在前端重算指标。

需要文件时再显式导出：

```bash
.venv/bin/python -m grid_experiments compare \
  experiments/experiment_results/<experiment>.sqlite3 \
  --output comparison.csv
.venv/bin/python -m grid_experiments export-run \
  experiments/experiment_results/<experiment>.sqlite3 \
  --run-id <run-id> --output run.json
```

正式运行默认要求 `market_simulator` 和 `grid_trading` 都是 clean。开发期确需运行
未提交代码时可以显式增加 `--allow-dirty`，结果会标记为不可复现。

该基线明确使用 `margin_model: "none"` 和 `funding_model: "none"`，目的是先证明
新旧入口在 K 线、成交、账本和权益上完全等价。新的实验配置可以把账户切换为
`flat-maintenance/v1` 并显式设置杠杆、维持保证金率和强平采样，不会隐式改变基线。

## 锚点语义

- 做多：锚点是最高卖出边界，按 `p(i+1) = p(i) / (1+r)` 向下生成。
- 做空：锚点是最低买入边界，按 `p(i+1) = p(i) × (1+r)` 向上生成。
- Cell 永远按价格从低到高编号，`#001` 是最低 Cell。

## 安装

```bash
git clone git@github.com:mutouretu/grid_trading_web.git grid_trading
cd grid_trading
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 启动后端

项目会从仓库根目录的 `.env` 统一加载配置；已经由 shell/export 提供的变量优先，不会被 `.env` 覆盖。可参考 `.env.example`。因此无需再手动导出币安密钥：

```bash
.venv/bin/uvicorn grid_server.interfaces.api:create_app --factory --host 127.0.0.1 --port 8100 --workers 1
```

接口文档：`http://127.0.0.1:8100/docs`

后端默认使用 `grid_trading.sqlite3`。可以用 `GRID_DB_PATH` 指定路径。

首次启动任一策略时，API 会自动启动一个共享调度器；后续策略复用同一个 PID。不要启动多个 Uvicorn worker，否则可能产生多个进程管理入口。

持仓一致性默认每 60 秒执行一次，可以用 `GRID_POSITION_RECONCILE_INTERVAL_SEC` 调整，最小为 5 秒：

```bash
export GRID_POSITION_RECONCILE_INTERVAL_SEC=60
```

建仓成交后，订单接口和持仓接口可能短暂不同步。调度器默认给新成交 15 秒结算宽限；宽限期内资源池可以标记为 `settling`，但不会取消保护性平仓单或释放 Cell。可以用 `GRID_POSITION_SETTLEMENT_GRACE_SEC` 调整：

```bash
export GRID_POSITION_SETTLEMENT_GRACE_SEC=15
```

## 轻量负载目标

当前调度模型按 `50 组 × 每组 5 个 Cell` 设计：

- 不再为每组策略创建 tmux 或 Python 进程。
- 每组保留自己的 `poll_interval_sec`，未到期的策略不会访问交易所。
- 同一轮中同一币对只查询一次价格和一次开放订单列表。
- 仍在开放订单快照中的订单不再逐张查询；只有消失的订单才单独查询成交或撤单结果。
- 交易规则仍按策略组隔离，停止一组不会停止调度器或其他策略。
- USDⓈ-M 与 COIN-M 使用独立 REST 适配器和快照缓存，不会共享订单或持仓资源池。
- 调度器启动、待机间隔、连续请求失败和恢复会聚合写入 SQLite 审计表，不会按每次失败刷日志。

## 合约产品与数量语义

- `market_type=usdm`：使用 `order_usdt` 配置单格 USD 名义金额，交易所数量按 `USD ÷ 价格` 换算为基础币数量。
- `market_type=coinm`：使用 `order_coin_qty` 配置单格标的币数量；下单时按 `币数量 × Cell 价格 ÷ contractSize` 换算为最接近的整数张。
- 币本位订单与仓位资源池内部仍以张数精确对账，API 和页面把订单张数按对应买卖价格换算为币数量展示。
- 资源池主键是 `market_type + symbol + positionSide`，两个产品族不能互相占用持仓资源。

生产和测试入口分别配置，不能用一个 URL 推断另一个产品的写入目标：

```dotenv
BINANCE_BASE_URL=https://fapi.binance.com
BINANCE_COINM_BASE_URL=https://dapi.binance.com
```

COIN-M 测试环境使用 `BINANCE_COINM_BASE_URL=https://testnet.binancefuture.com`。所有真实下单测试还需显式测试开关和域名保护。

## 启动前端

```bash
.venv/bin/streamlit run app.py --server.port 8000
```

访问 `http://127.0.0.1:8000`。前端默认连接 `http://127.0.0.1:8100`，可以在 `.env` 中通过 `GRID_API_URL` 修改。

总览、预览、新增、编辑、启动、停止、刷新价格、归档、删除和 Cell 详情均通过 FastAPI 操作 SQLite/调度器。同一币对允许创建多组网格，页面使用 `strategy_id` 区分。

## 订单一致性规则

运行中的每组网格只按自己的 `strategy_id + cell_id` 识别订单；同一币对、同一方向可以同时运行多组网格。轮询后的处理规则如下：

| 订单情况 | 系统处理 |
| --- | --- |
| 订单仍在挂单 | 保持不变 |
| 建仓单全部成交 | 按实际成交数量创建对偶平仓单 |
| 建仓单明确为 `CANCELED/EXPIRED/REJECTED` 且未成交 | 视为已经触发，立即重新挂建仓单，不重复判断当前价格 |
| 建仓单处于部分成交 | 撤销未成交余量，只为最终实际成交数量创建平仓单 |
| 建仓单完全查不到 | 状态不明确，禁止盲目补单，转 `manual_review`；真实仓位作为未分配资源报警 |
| 已创建的平仓单取消或丢失 | 先转为 `manual_review`；持仓池确认资源充足后自动恢复 |
| 平仓单部分成交后取消 | 扣除已成交数量，再按真实剩余持仓缩量恢复 |
| 建仓成交已落库、首次平仓单尚未创建时进程中断 | 重启后自动创建平仓单 |

## 持仓资源池

协调器以 `market_type + symbol + positionSide` 为资源池，将币安聚合持仓按以下顺序分配：

1. 停止或归档策略的逻辑持仓先保留，但系统不会修改这些策略。
2. 外部手工平仓挂单先预留“平仓订单覆盖量”，但在实际成交前不会缩减 Cell 的逻辑持仓。
3. 同一币对、同一方向的运行组统一按建仓价距现价的距离分配；远价 Cell 优先保留仓位，近价 Cell 最后检查并先吸收短缺。
4. 已存在的有效平仓单只在“订单覆盖量”分配时优先保留，不能改变 Cell 的持仓所有权顺序。
5. 软删除策略的 Cell 只保留审计数据，不再占用资源池；平台残留仓位或订单按未分配/外部资源报告。停止和归档策略仍保留逻辑仓位，但不会被自动修改。

真实仓位归属和订单覆盖量分开计算：只有币安真实仓位减少时才缩减 Cell 的 `open_qty`；外部平仓单待成交期间只缩小网格平仓单，外部单取消后自动扩回，成交后再依据新的真实仓位缩减 Cell。没有真实持仓资源时清空该 Cell 的逻辑持仓并回到未触发状态，运行策略会在下一轮重新挂建仓单。撤单期间发生成交时会刷新真实持仓后重新计算，避免使用旧快照。

无法确认撤单、剩余数量低于最小下单量、停止组的平仓单缺失或订单同步失败时，不冒险修改未知状态，使用 `manual_review/error` 兜底。外部平仓单总量超过真实仓位时标记 `order_excess`。当前持仓池快照可通过 `GET /position-pools` 查看。

API 停止或删除可以与正在执行的调度 tick 并发发生。调度器只允许对 `starting/running/error` 状态写 heartbeat；一旦状态已变为 `stopped` 或策略已删除，在途 tick 不能把它重新激活，也不能终止共享调度进程。

## 测试

24～72 小时测试网只读巡检使用 `scripts/reliability_probe.py`，由 systemd timer 或 cron 每几分钟调用一次，采样结束即退出。安装、告警语义、周期汇总和重启/强杀观察方法见 `docs/reliability-monitoring.md`。

50 组 × 5 Cell 的离线性能矩阵和 24 小时耐久测试使用 `scripts/performance_acceptance.py`。它只连接独立 SQLite 和内存模拟交易所，具体场景、指标及 2 核 2 GB 验收方法见 `docs/performance-acceptance.md`。

```bash
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖等比计算、稳定 Cell ID、同币对多组网格隔离、配置不可逆锁定、SQLite 重启、软删除、API、做多/做空开平仓闭环、平台手动删单和删全部单后的分类恢复、部分成交后撤单、真实持仓部分/全部减少、外部平仓单预留/部分成交/取消、停止组隔离、未知订单防重复建仓、接口失败后的延迟重试、开放订单恢复、移动窗口、COIN-M 合约张数换算与产品资源池隔离，以及 50 组 × 5 Cell 的同币对和多币对调度负载。
