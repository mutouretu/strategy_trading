# 第 5 部分：策略体系 v1.0 验收记录

## 1. 验收结论

第五部分 5A—5G 已完成。策略体系已经形成独立的 `strategies_system` 工程，
纯策略、仿真适配、实验组装、指标和 Viewer 展示边界均按方案落地。

本次验收只确认框架和数值调用关系成立，不表示首批策略已经完成收益优化，
也不授权接入实盘。

## 2. 已实现范围

### 2.1 架构与注册

- 新建 `strategies_system` 独立 Git 工程；
- `trading_strategies` 保持纯领域依赖；
- `strategy_simulation` 承担 Adapter、Plugin、Provider 和 Metric 接入；
- `SimulationStrategyRegistry` 显式注册策略并拒绝重复或未知类型；
- 通用 Provider 不包含具体策略类型分支；
- Runner 支持主动策略在当前 Frame 发布 ACTIVE Intent 并于同一 Frame open 成交。

### 2.2 策略与桥接

- `hold-btc/v1`：零交易 BTC 持有基准；
- `target-liquidation-ladder-long/v1`：目标强平价反算仓位、主动建仓、几何阶梯
  `reduce_only` 退出；
- `single-following-grid/v1`：只通过 Plugin 桥接已有网格 Strategy 和 Adapter，
  未复制或修改网格规则。

### 2.3 COIN-M 数值能力

目标强平价仓位计算复用了 `grid_trading` 已验证的：

- `InverseContractLedger`；
- `InverseContractMarginModel`；
- `InverseContractFeeModel`；
- 账户和执行 Component Factory。

仓位选择包含建仓手续费，按 quantity step 选择满足目标强平价的最大合法合约数，
并用同一个 Margin Model 反向校验。策略工程没有复制盈亏、保证金或强平公式。

### 2.4 实验、指标与 Viewer

- 基线规格：`strategy_baselines_v1.json`；
- 组合：3 个策略 × Seed 42、43，共 6 个 Run；
- 三个策略使用同一三年 Anchored GBM、1.1 BTC 全仓账户、5 倍配置杠杆和相同费用；
- MetricSet：`core/v1`、`btc-accumulation/v1`、`grid/v1`；
- Viewer 可在零实验时展示已注册策略，并显示策略说明、公式、约束和参数元数据；
- 实验生成后可继续使用现有实验详情和 K 线播放页面。

## 3. 验收样本结果

本地完整基线执行结果：6/6 Run 成功。

目标强平价阶梯策略在两个 Seed 中均得到：

- 建仓价：65,000；
- 建仓数量：315 contracts；
- 建仓后预计强平价：约 19,981.09；
- 目标强平价：20,000；
- 10/10 止盈档位完成；
- 期末剩余策略仓位：0。

这些数值用于验收调用关系和约束，不作为策略收益结论。

## 4. 自动化验证范围

- 纯策略包禁止导入 simulator、实验系统、Server 或交易所 SDK；
- Registry 重复注册和未知策略拒绝；
- 几何价格、合约取整和末档余量；
- 目标强平价和最小合约边界；
- 完整退出、部分退出、未触发止盈和平台强平路径；
- 同 Frame ACTIVE Intent 的运行时回归；
- Provider 无具体策略类型分支；
- 6 Run 基线规格预检；
- 三套指标从保存后的实验数据库完成计算和 Scenario 聚合；
- 两个参与旧工程的完整回归测试与新工程测试。

## 5. 保留事项

- 实盘 Server 注册与 Live Adapter 不在 v1 范围；
- 交易所部分成交、重启恢复和实盘对账留待实盘接入设计；
- 参数搜索、市场环境扩展和策略优化进入后续部分；
- 资金费市场条件化和滑点仍按原规划后置；
- 第五部分 tag 和远端 push 需单独执行。
