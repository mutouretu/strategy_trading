# Grid Trading Web

面向 Web 服务重构的 Binance U 本位触发式移动等比网格。项目保留原始 `GRID_TRADING` 作为行为基线，新后端不再依赖 CSV 恢复或 tmux 管理。

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
Binance USDⓈ-M Futures
```

主要模块：

- `gridtrader/domain.py`：Web 原生策略、Cell、订单和生命周期模型。
- `gridtrader/grid_math.py`：做多向下、做空向上的等比 Cell 生成及稳定 Cell ID。
- `gridtrader/store.py`：SQLite 事务、配置锁定、Cell、事件和 heartbeat。
- `gridtrader/engine.py`：可单步测试的触发、建仓、平仓、恢复和移动窗口引擎。
- `gridtrader/scheduler.py`：单进程调度全部策略，每组策略只是一个轻量状态机。
- `gridtrader/snapshot_exchange.py`：同一调度轮次按币对复用价格和开放订单快照。
- `gridtrader/position_coordinator.py`：按币对和方向分配真实持仓资源并修复平仓单。
- `gridtrader/supervisor.py`：保证共享调度器只启动一次；停止单组不会影响其他组。
- `gridtrader/api.py`：前端可直接调用的 FastAPI 接口。
- `legacy_grid/`：原仓库快照，仅用于行为对照和特征测试。

## 锚点语义

- 做多：锚点是最高卖出边界，按 `p(i+1) = p(i) / (1+r)` 向下生成。
- 做空：锚点是最低买入边界，按 `p(i+1) = p(i) × (1+r)` 向上生成。
- Cell 永远按价格从低到高编号，`#001` 是最低 Cell。

## 安装

项目通过 Git submodule 引用原始 `GRID_TRADING` 行为基线。首次克隆请初始化子模块：

```bash
git clone --recurse-submodules git@github.com:mutouretu/grid_trading_web.git
cd grid_trading_web
```

如果已经完成普通克隆，则执行：

```bash
git submodule update --init --recursive
```

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 启动后端

项目会从仓库根目录的 `.env` 统一加载配置；已经由 shell/export 提供的变量优先，不会被 `.env` 覆盖。可参考 `.env.example`。因此无需再手动导出币安密钥：

```bash
.venv/bin/uvicorn gridtrader.api:create_app --factory --host 127.0.0.1 --port 8100 --workers 1
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
- 当前阶段只处理 USDⓈ-M，COIN-M 不在本轮范围内。

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

协调器以 `symbol + positionSide` 为资源池，将币安聚合持仓按以下顺序分配：

1. 停止或归档策略的逻辑持仓先保留，但系统不会修改这些策略。
2. 外部手工平仓挂单先预留“平仓订单覆盖量”，但在实际成交前不会缩减 Cell 的逻辑持仓。
3. 同一币对、同一方向的运行组统一按建仓价距现价的距离分配；远价 Cell 优先保留仓位，近价 Cell 最后检查并先吸收短缺。
4. 已存在的有效平仓单只在“订单覆盖量”分配时优先保留，不能改变 Cell 的持仓所有权顺序。
5. 软删除策略的 Cell 只保留审计数据，不再占用资源池；平台残留仓位或订单按未分配/外部资源报告。停止和归档策略仍保留逻辑仓位，但不会被自动修改。

真实仓位归属和订单覆盖量分开计算：只有币安真实仓位减少时才缩减 Cell 的 `open_qty`；外部平仓单待成交期间只缩小网格平仓单，外部单取消后自动扩回，成交后再依据新的真实仓位缩减 Cell。没有真实持仓资源时清空该 Cell 的逻辑持仓并回到未触发状态，运行策略会在下一轮重新挂建仓单。撤单期间发生成交时会刷新真实持仓后重新计算，避免使用旧快照。

无法确认撤单、剩余数量低于最小下单量、停止组的平仓单缺失或订单同步失败时，不冒险修改未知状态，使用 `manual_review/error` 兜底。外部平仓单总量超过真实仓位时标记 `order_excess`。当前持仓池快照可通过 `GET /position-pools` 查看。

API 停止或删除可以与正在执行的调度 tick 并发发生。调度器只允许对 `starting/running/error` 状态写 heartbeat；一旦状态已变为 `stopped` 或策略已删除，在途 tick 不能把它重新激活，也不能终止共享调度进程。

## 测试

新后端测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

原引擎基线测试：

```bash
PYTHONPATH=legacy_grid python3 -m unittest discover -s legacy_grid/tests -p 'test_*.py' -v
```

测试覆盖等比计算、稳定 Cell ID、同币对多组网格隔离、配置不可逆锁定、SQLite 重启、软删除、API、做多/做空开平仓闭环、平台手动删单和删全部单后的分类恢复、部分成交后撤单、真实持仓部分/全部减少、外部平仓单预留/部分成交/取消、停止组隔离、未知订单防重复建仓、接口失败后的延迟重试、开放订单恢复、移动窗口，以及 50 组 × 5 Cell 的同币对和多币对调度负载。
