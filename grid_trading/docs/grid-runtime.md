# 网格本机运行目录

2026-11-22 起，实盘 API、调度器、统一前端和订单巡检均从当前 `/Users/dengerqiang/Documents/code/Trade/Crypto/strategy_trading/grid_trading` 目录运行。实盘数据库也在本目录的 `runtime/binance` 和 `runtime/okx`。旧 `strategy_trading_grid` 运行目录仅留符号链接指向当前数据库，避免产生两份独立账本；以后更新实盘代码需在本目录重启服务。

| 用途 | API | 页面 | 数据库 |
| --- | --- | --- | --- |
| Binance 实盘 | 8111 | 8513 统一页面 | `runtime/binance/grid.sqlite3` |
| OKX 实盘 | 8112 | 8513 统一页面 | `runtime/okx/grid.sqlite3` |
| Binance 测试网 | 8110 | 8510 | `runtime/testnet/grid.sqlite3` |

以上路径相对 `grid_trading/`。实盘前端通过 `GRID_BINANCE_API_URL` 和 `GRID_OKX_API_URL` 连接两个后端，分别读取 `.env` 和 `okx.env`；测试网读取 `test.env`。这些凭证文件、数据库、日志和 `.venv` 都不进入 Git。实盘 API 和调度器分别使用 `binance-grid-api`、`okx-grid-api` tmux 会话；统一前端使用 `grid-ui-8513`。

macOS LaunchAgent `com.mutouretu.gridtrader.live-maintenance` 每 300 秒从本目录执行一次订单巡检。迁移到新分支或目录时，应同时核对该 LaunchAgent 的路径。巡检规则见 [live-grid-maintenance.md](live-grid-maintenance.md)。

实盘切换代码时，先停止该交易所的旧调度器和 API，再对 SQLite 数据库做一致性备份、从新目录启动 API 和调度器。保留交易所已有挂单及原数据库备份；新调度器启动后核对策略状态、心跳和订单巡检结果。不要同时运行两个指向同一策略或账户挂单的调度器。
