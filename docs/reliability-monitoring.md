# 长时间只读可靠性巡检

`scripts/reliability_probe.py` 是单次采样程序，不包含循环，也不会调用下单、撤单、改单或数据库修复接口。每次运行只执行以下操作：

- 以 SQLite `mode=ro` 和 `query_only` 读取一个一致快照；
- 对币安只调用 `GET /fapi/v3/positionRisk` 和每个受管币对的 `GET /fapi/v1/openOrders`；
- 以 HTTP GET 检查 FastAPI `/health` 和可选的 Streamlit 首页；
- 读取 FastAPI `/strategies`，确认重启后的 API 仍连接同一份 SQLite；
- 读取本机进程表和可选的 scheduler PID 文件；
- 向独立的 JSONL 巡检文件追加一条记录。

它不会自动执行故障注入。FastAPI/Streamlit 重启、scheduler 强制终止等动作仍由测试人员控制，巡检记录用于比较操作前后的恢复结果。

## 单次采样

测试网配置已经写在 `test.env` 时，可以在项目根目录执行：

```bash
.venv/bin/python scripts/reliability_probe.py \
  --env-file test.env \
  --streamlit-url http://127.0.0.1:8010 \
  --pid-file runtime/scheduler.pid \
  --label baseline \
  --output runtime/reliability/testnet.jsonl
```

`GRID_DB_PATH`、`GRID_API_URL`、`BINANCE_BASE_URL` 和币安测试网密钥从 `test.env` 读取。也可以用 `--db`、`--api-url` 显式覆盖。不要把生产网密钥和测试网地址混在同一个环境文件中。

终端只输出本次状态摘要，完整证据保存在 JSONL。`overall` 分为：

- `ok`：本次没有发现异常；
- `warning`：例如未分配持仓、待回写订单号、预期订单暂时缺失、心跳过旧或 Web 服务不可用；
- `critical`：例如真实仓位短缺、未知网格订单、重复订单、订单属性不匹配、`manual_review/error`、scheduler 丢失或重复。

外部手工订单不以 `wg-` 开头，只计入 `external_open`，不会被误报为未知网格订单。

## 每五分钟采样

Linux 服务器建议使用 systemd timer，由系统按时启动一次采样进程，采样结束后进程立即退出。

`/etc/systemd/system/grid-reliability-probe.service`：

```ini
[Unit]
Description=Grid trading read-only reliability probe
After=network-online.target

[Service]
Type=oneshot
User=grid
WorkingDirectory=/opt/grid_trading
ExecStart=/opt/grid_trading/.venv/bin/python scripts/reliability_probe.py --env-file test.env --streamlit-url http://127.0.0.1:8010 --pid-file runtime/scheduler.pid --label periodic --output runtime/reliability/testnet.jsonl
```

`/etc/systemd/system/grid-reliability-probe.timer`：

```ini
[Unit]
Description=Sample grid reliability every five minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=15s
Persistent=true

[Install]
WantedBy=timers.target
```

其中 `User`、`WorkingDirectory` 和路径要替换为服务器的真实值。启用并查看状态：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now grid-reliability-probe.timer
systemctl list-timers grid-reliability-probe.timer
journalctl -u grid-reliability-probe.service -n 50 --no-pager
```

如果不使用 systemd，也可以加入运行用户的 `crontab -e`：

```cron
*/5 * * * * cd /opt/grid_trading && .venv/bin/python scripts/reliability_probe.py --env-file test.env --streamlit-url http://127.0.0.1:8010 --pid-file runtime/scheduler.pid --label periodic --output runtime/reliability/testnet.jsonl >> runtime/reliability/probe.log 2>&1
```

同一套环境只配置一种定时方式，避免重复采样。

## 24～72 小时汇总

```bash
.venv/bin/python scripts/reliability_probe.py \
  --summary runtime/reliability/testnet.jsonl \
  > runtime/reliability/summary.json
```

汇总包括：

- 样本数量和 `ok/warning/critical` 次数；
- SQLite、WAL、SHM 首值、末值、峰值和增长量；
- 每类异常出现于多少个采样点、哪些异常最终已经收敛、末次仍有哪些异常；
- scheduler PID 变化轨迹，用于核对重启和强杀恢复；
- 每个运行策略观察到的最大 heartbeat age；
- 单次巡检自身的最大耗时和末次耗时。

heartbeat age 可以发现调度器停滞或明显延迟，但它不是每个 scheduler cycle 的精确耗时。若以后需要分析轻微的周期抖动，应在 scheduler 内增加只记录耗时的指标；这不属于当前只读巡检的职责。

## 调度器内建持久化审计

调度器同时把低频、结构化的中断证据写入交易 SQLite，不依赖外部巡检任务：

- `scheduler_runs`：每次调度器启动、最后存活时间、正常停止或非正常重启；
- `scheduler_gaps`：主循环停顿超过阈值的区间、秒数和当时运行策略数，可识别电脑待机；
- `scheduler_incidents`：首次失败、最后失败、连续失败次数、错误类型及恢复时间；
- 策略 `events`：每段故障只写一次 `SCHEDULER_FAILURE_STARTED`，恢复时写一次 `SCHEDULER_RECOVERED`。

连续断网不会每个轮询周期新增一条事件，而是在同一个 incident 上累计
`failure_count`。默认每 30 秒更新一次 scheduler run 存活时间，主循环停顿
5 秒以上记录为 gap：

```dotenv
GRID_SCHEDULER_GAP_THRESHOLD_SEC=5
GRID_SCHEDULER_AUDIT_HEARTBEAT_SEC=30
```

可通过只读接口查看：

```text
GET /scheduler/runs
GET /scheduler/gaps
GET /scheduler/incidents
```

这些表按中断和故障段增长，不按普通轮询增长，适合长期保留。外部 JSONL
巡检仍用于平台、Web 和进程的跨系统快照，两者职责不同。

## 重启和强杀测试的记录方式

定时巡检保持运行，另外在关键动作前后手动追加带标签的采样：

```bash
.venv/bin/python scripts/reliability_probe.py --env-file test.env --label before-scheduler-kill --output runtime/reliability/testnet.jsonl
# 人工执行已评审的 scheduler 故障注入和恢复操作
.venv/bin/python scripts/reliability_probe.py --env-file test.env --label after-scheduler-recovery --output runtime/reliability/testnet.jsonl
```

恢复后的验收条件是 scheduler 最终只有一个候选 PID，`duplicate_order` 和 `unknown_managed_order` 为零，实际/逻辑仓位没有新增短缺，已有异常在后续采样中收敛。脚本本身绝不会执行注释中的故障注入命令。

## 文件维护

JSONL 每五分钟一行，72 小时约 864 行，体积很小。长期运行时可以按月轮转，或使用 `logrotate`；不要把巡检文件写进交易数据库所在的 WAL 文件，也不要提交环境文件、JSONL 或密钥到 Git。
