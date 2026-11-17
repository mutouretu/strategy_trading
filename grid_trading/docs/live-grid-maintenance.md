# 两条实盘网格的订单巡检

macOS LaunchAgent `com.mutouretu.gridtrader.live-maintenance` 每 300 秒（5 分钟）运行一次 `scripts/live_grid_maintenance.py`。它只检查币安 `soxlusdt-fixed-c6ce0449` 和 OKX `soxl-usdt-swap-fixed-197a79bf` 的网格订单：读取各自数据库里的格子与订单引用，再读取交易所的当前挂单和标记价。它不检查进程、开关机、API 健康或账户总仓位，也不会启动或停止策略。

网格调度器本身每约 5 秒同步订单，并负责按原有格子、价格带和最多约 10 单/侧的窗口规则补挂。订单巡检若发现数据库已登记的订单从交易所挂单中消失、格线已穿过却未挂单、持仓格需要人工复核，或发现未被格子记录的托管订单，会在接下来约 60 秒内复查最多三次。已由调度器同步解决的订单问题记为 `resolved_after_sync`；仍未补好的具体格子记入日志，并发本机通知。巡检不另起一套下单逻辑，避免与正在交易的调度器同时下单造成重复仓位。没有挂单的普通窗口外逻辑格不会被当成缺单。

正常情况下，两条网格的检查约需 1–2 秒；有订单问题时会等待调度器同步，最长约 60 秒。结果保存在 `runtime/live-maintenance/checks.jsonl`。`--dry-run` 只读检查订单，不写日志或发通知。若某条网格的调度器没有运行，巡检会记录未补的订单，但不会重启它；用户如需恢复运行，应检查该策略及调度器状态。

```bash
grid_trading/.venv/bin/python grid_trading/scripts/live_grid_maintenance.py --dry-run
launchctl print gui/$(id -u)/com.mutouretu.gridtrader.live-maintenance
tail -n 20 grid_trading/runtime/live-maintenance/checks.jsonl
```
