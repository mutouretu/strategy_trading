# Grid 分支与本机运行目录

网格代码从 `grid` 分支继续开发；`ledger` 工作树留给账本。`grid` 工作树位于 `/Users/dengerqiang/Documents/code/Trade/Crypto/strategy_trading_grid`。代码合入 GitHub 的 `main` 不会自动替换本机运行的进程；更新实盘时需要从 `grid` 工作树重启对应服务。

| 用途 | API | 页面 | 数据库 |
| --- | --- | --- | --- |
| Binance 实盘 | 8111 | 8513 统一页面 | `runtime/binance/grid.sqlite3` |
| OKX 实盘 | 8112 | 8513 统一页面 | `runtime/okx/grid.sqlite3` |
| Binance 测试网 | 8110 | 8510 | `runtime/testnet/grid.sqlite3` |

以上路径相对 `grid_trading/`。实盘前端读取 `frontend.env`，两个交易所分别读取 `.env` 和 `okx.env`，测试网读取 `test.env`。这些凭证文件、数据库、日志和 `.venv` 都不进入 Git。实盘 API 和调度器分别使用 `binance-grid-api`、`okx-grid-api` tmux 会话；统一前端使用 `grid-ui-8513`。测试网使用 `grid-testnet-api`、`grid-testnet-ui`。当前测试网策略为已停止状态。

macOS LaunchAgent `com.mutouretu.gridtrader.live-maintenance` 每 300 秒从本工作树执行一次订单巡检。迁移到新分支或目录时，应同时核对该 LaunchAgent 的路径。巡检规则见 [live-grid-maintenance.md](live-grid-maintenance.md)。

实盘切换代码时，先停止该交易所的旧调度器和 API，再对 SQLite 数据库做一致性备份、从新目录启动 API 和调度器。保留交易所已有挂单及原数据库备份；新调度器启动后核对策略状态、心跳和订单巡检结果。不要同时运行两个指向同一策略或账户挂单的调度器。
