# 网格本机运行目录

## 当前本地备用状态（2026-11-23）

OKX 的 SOXL 实盘已迁到服务器。本机保留 `runtime/okx/grid.sqlite3` 的停用策略和历史数据；本地 8112 API 与 8513 页面继续运行，且不使用 SSH 端口转发。本地 API 设置 `GRID_LOCAL_STANDBY=1` 后，停用策略的收益和仓位接口只读取本地快照，不从交易所补录成交。拨码开关仍调用原有的策略启停接口。

切回本机交易时，先停止服务器上对应策略的调度，再将服务器最新数据库一致性快照同步到本机，核对策略、持仓和挂单后打开本地拨码。当前本地数据只代表迁移时刻，不能直接拿来接续后续交易。

## 迁移前的本机布局

2026-11-22 至服务器接管前，实盘 API、调度器、统一前端和订单巡检均从当前 `/Users/dengerqiang/Documents/code/Trade/Crypto/strategy_trading/grid_trading` 目录运行。实盘数据库位于本目录的 `runtime/binance` 和 `runtime/okx`。旧 `strategy_trading_grid` 运行目录仅留符号链接指向当前数据库，避免产生两份独立账本。

| 用途 | API | 页面 | 数据库 |
| --- | --- | --- | --- |
| Binance 实盘 | 8111 | 8513 统一页面 | `runtime/binance/grid.sqlite3` |
| OKX 实盘 | 8112 | 8513 统一页面 | `runtime/okx/grid.sqlite3` |
| Binance 测试网 | 8110 | 8510 | `runtime/testnet/grid.sqlite3` |

以上路径相对 `grid_trading/`，表中端口和会话名记录迁移前的部署。原实盘前端通过 `GRID_BINANCE_API_URL` 和 `GRID_OKX_API_URL` 连接两个后端，分别读取 `.env` 和 `okx.env`；测试网读取 `test.env`。这些凭证文件、数据库、日志和 `.venv` 都不进入 Git。

macOS LaunchAgent `com.mutouretu.gridtrader.live-maintenance` 原本每 300 秒从本目录执行一次订单巡检；本机停用后已卸载。巡检规则见 [live-grid-maintenance.md](live-grid-maintenance.md)。

实盘切换代码时，先停止该交易所的旧调度器和 API，再对 SQLite 数据库做一致性备份、从新目录启动 API 和调度器。保留交易所已有挂单及原数据库备份；新调度器启动后核对策略状态、心跳和订单巡检结果。不要同时运行两个指向同一策略或账户挂单的调度器。
