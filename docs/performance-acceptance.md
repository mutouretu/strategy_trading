# 50 组 × 5 Cell 性能验收

`scripts/performance_acceptance.py` 使用独立 SQLite 和内存模拟交易所，不读取环境文件、不访问币安，也不会影响测试网 AKE/HOME。它包含快速矩阵和长时间耐久两部分。

## 快速矩阵

```bash
.venv/bin/python scripts/performance_acceptance.py benchmark \
  --steady-cycles 5 \
  --output runtime/performance/benchmark.json
```

矩阵依次执行：

- 50 组 × 5 Cell、单币对、50 秒轮询；
- 50 组 × 5 Cell、单币对、10 分钟轮询；
- 50 组 × 5 Cell、单币对、1 小时轮询；
- 50 组 × 5 Cell、50 个币对、50 秒轮询；
- 100 组 × 5 Cell、单币对、50 秒轮询余量测试。

轮询时间使用虚拟时钟推进，因此不需要真的等待一小时。每组场景测量冷启动轮、未到期空轮和多个稳定轮，并记录：

- 当前 RSS 和 800 MB 门槛；
- 调度轮 p50、p95、最大耗时；
- SQLite 查询延迟以及 DB/WAL/SHM 大小；
- 行情、开放订单、单订单和持仓接口调用次数；
- 事件表在稳定挂单阶段的增长量；
- 一个 scheduler 进程内的 engine 数量。

虚拟时钟会保留默认 60 秒订单/持仓一致性周期。因此即使策略轮询设为 10 分钟或 1 小时，中间的一致性轮询请求仍会计入每分钟请求量，而不会被错误地按策略周期摊薄。

单币对 50 组时，一个稳定轮的 `get_mark_price` 和 `get_open_orders` 都应为 1；50 个币对时才应增长为 50。请求量不能随 250 个 Cell 线性增长。

## 24 小时耐久测试

```bash
.venv/bin/python -u scripts/performance_acceptance.py soak \
  --db runtime/performance/soak.sqlite3 \
  --output runtime/performance/soak.jsonl \
  --duration-sec 86400 \
  --sample-interval-sec 60 \
  --groups 50 \
  --cells 5 \
  --symbols 1 \
  --poll-intervals 50 600 3600
```

50 个策略在同一个 scheduler 中运行，轮询周期按 50 秒、600 秒、3600 秒循环分配。每分钟记录：

- RSS、进程 CPU 占单核百分比；
- 最近一分钟调度轮耗时；
- SQLite 只读延迟、表行数和文件大小；
- 最近一分钟模拟交易所调用量；
- 429 计数、数据库锁错误、engine 数和 scheduler 进程数；
- 当前是否仍满足验收门槛。

测试数据库必须是一个不存在的新路径；脚本拒绝复用旧数据库，以免历史数据污染结果。JSONL 的最后一条 `record_type=complete` 是最终结论。

## 验收解释

- RSS 小于 800 MB；
- 调度轮最大耗时小于最短轮询周期的 10%；
- 一个 scheduler 进程承载全部策略 engine；
- 无 SQLite `database is locked`；
- 模拟交易所不产生 429，请求计数符合按币对聚合；
- 事件表和指标日志增长可由首末样本直接计算。

离线模拟中的 `http_429_count=0` 只能证明程序没有制造模拟限流，不能代替币安真实限流观察。测试网巡检会通过持续的 `strategy_error/runtime_error` 发现未自动收敛的真实接口错误。

## 2 核 2 GB 限制

macOS 可以测量资源并按 800 MB 门槛判定，但不能原生提供等同 Linux cgroup 的精确 2 核 2 GB 沙箱。部署前可在 Linux 使用 systemd 的 `CPUQuota=200%` 和 `MemoryMax=2G` 再运行同一命令，作为最终服务器环境验收。

## 本机后台任务

当前本机任务由 LaunchAgent 托管时，可查看：

```bash
launchctl print gui/$(id -u)/com.mutouretu.gridtrader.performance-soak
tail -n 3 runtime/performance/soak-20260719.jsonl
```

需要提前终止时：

```bash
launchctl bootout gui/$(id -u)/com.mutouretu.gridtrader.performance-soak
```

提前终止不会产生完整的 `complete` 记录，不能判定 24 小时验收通过。
