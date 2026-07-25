# Grid Strategies

这里存放高层网格策略，例如 BTC 积累、天地单和熊市项目做空。策略负责决定：

- 什么时候创建、调整或停止一组或多组网格；
- 如何在不同网格之间分配资金；
- 什么条件下加仓、减仓或整体退出。

策略核心可以依赖 `grid_rule`，但不能依赖 `grid_server`、交易所、数据库或仿真运行时。
对 `SimulationDecisionPort` 等外部接口的转换放在 `adapters/`。

当前最小实现是 `SingleFollowingGridStrategy`：

- 启动时部署一组 `move_grid=True` 的网格；
- 始终只维护这一组网格；
- 不动态改变资本配置，也不主动整体退出；
- 通过 `adapters/` 接入仿真器。

它用于验证以下调用链，不代表最终的 BTC 积累策略：

```text
SimulationRunner
    → SingleFollowingGridSimulationAdapter
    → SingleFollowingGridStrategy
    → GridRuleEngine
```

第二个实现是 `LayeredFollowingGridStrategy`：

- 从 65,000 美元开始，日线收盘价每向下跨过 5,000 美元部署一组新网格；
- 每组网格都独立调用一个 `GridRuleEngine`；
- 下层网格向上跟随到上层网格下沿时，下层回到自己的初始锚点；
- 复位会撤销旧建仓单，但旧仓位的平仓单由退役规则实例继续维护；
- 复位后的下层需等价格重新低于本层锚点，才恢复向上跟随。

其调用链为：

```text
SimulationRunner
    → LayeredFollowingGridSimulationAdapter
    → LayeredFollowingGridStrategy
    → one GridRuleEngine per layer/generation
```
