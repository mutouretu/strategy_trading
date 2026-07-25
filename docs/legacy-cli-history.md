# 旧命令行版本代码谱系

当前 Web 系统的前身是独立仓库
[`mutouretu/GRID_TRADING`](https://github.com/mutouretu/GRID_TRADING)。本仓库曾以
Git submodule 固定引用其
[`6a028d7383da5ee06c2920243b73b54e6447a31f`](https://github.com/mutouretu/GRID_TRADING/commit/6a028d7383da5ee06c2920243b73b54e6447a31f)
提交，用于保留命令行版本的行为基线。

旧版本以 `bot.py` 和 `DualTriggerGrid` 为入口，直接调用 Binance 客户端，使用 CSV
记录和恢复交易状态，并使用 `dtg-*` 客户端订单号。当前 Web 版本从需求出发重新实现，
以 FastAPI、Streamlit、SQLite 和共享调度器为运行基础，不导入或执行旧仓库代码。

2026 年 7 月清理代码谱系时，旧 submodule 和直接执行旧实现的 characterization
测试被移除。旧代码仍永久保存在上述 GitHub 仓库和固定提交中，不再作为本仓库的安装、
运行或测试依赖。

仍需保留的行为已经由当前 Web 实现自身的测试覆盖：

- LONG 价格触发、买入成交后挂对应卖单及完成循环：
  `tests.test_engine.TradingEngineTests.test_long_entry_fill_places_sell_exit_and_closes_cycle`
- SHORT 建仓成交后挂对应买入平仓单：
  `tests.test_engine.TradingEngineTests.test_short_entry_fill_places_buy_exit`
- 移动窗口扩展和安全回收：
  `tests.test_engine.TradingEngineTests.test_long_moving_window_adds_above_and_reclaims_lowest_safe_cell`
- Web 锚点语义、等比价格和稳定 Cell ID：`tests.test_grid_math.GridMathTests`

后续行为基线以 `grid_server` 的规范实现和测试为准，不再从旧命令行代码复制或导入实现。
