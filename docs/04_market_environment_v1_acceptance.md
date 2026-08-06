# 第 4 部分：市场环境系统 v1.0 验收记录

## 1. 验收结论

第四部分 v1.0 已通过验收，冻结标签为 `market-environment-v1.0.0`。

本次冻结覆盖 4A—4F 的市场语义、生成、内容锁、实验接入与只读展示，以及 4H 的
整体复现和回归。4G 调整为 6C / 6D 筛出候选策略后的高保真敏感性复核，不属于
v1.0 的阻塞条件。

这些路径是可复现的研究假设，不是对 BTC 或 ETH 未来价格的预测，也不是策略收益
结论。HOLDOUT 已物化和锁定，但仍禁止进入策略开发、参数选择与日常 Viewer 浏览。

## 2. 冻结范围

### 2.1 市场定义

- BTC、ETH 各 1 个三年 PathSet；
- 每个 PathSet 包含 6 个 Scenario、96 条 `1h` 路径；
- 每条路径包含 26,305 根 K 线，覆盖 1,096 天；
- 每个 PathSet 的角色分布为 TRAIN 48、VALIDATION 24、HOLDOUT 24；
- 两个资产合计 192 条路径、5,050,560 根 K 线；
- BTC 与 ETH 独立生成，不把相同 Seed 解释为联合或相关路径。

冻结身份：

| PathSet | Lock fingerprint | 状态 |
| --- | --- | --- |
| `btc-three-year-market-baseline-v1` | `dc171110a1394d76a0d42215bce1c8699c94671d8a61c42ecf3784ab8a04933e` | `CONTENT_LOCKED` |
| `eth-three-year-market-baseline-v1` | `8701579f58b24cd206ecc9aaf0d46f5f4ccb3dbcecd403e2ce8ddd9198df7cae` | `CONTENT_LOCKED` |

每条路径的语义内容哈希和 Parquet 文件哈希均写入 Manifest。旧 Manifest 与重新生成的
内容不一致时，物化命令会拒绝覆盖；需要修改市场假设时必须新建 Scenario / PathSet
版本。

### 2.2 Experiment / Study 接入

- Study 使用 `market-path-selection/v1` 明确选择 PathSet、Scenario、role 和 Market Seed；
- 编译后每条路径成为 `locked-market-path/v1` 市场组件；
- Market Seed 与 Experiment Run Seed 分离，锁定路径不会被二次随机化；
- Run 保存 PathSet、Scenario、role、Market Seed、路径身份和双哈希；
- 编译器、Provider 和 Schema 三层拒绝 HOLDOUT；
- Provider 在执行前复核 Manifest 身份、存储边界、内容哈希和文件哈希；
- 旧的历史数据集 Study 仍沿原接口读取，不需要迁移。

### 2.3 Viewer

- 市场环境页可直接发现锁定的 BTC、ETH PathSet；
- 可展开 Scenario 并选择 TRAIN / VALIDATION 路径；
- 可查看周线、月线、画像、Seed、role 和哈希；
- HOLDOUT 只显示数量，不显示可选择的路径和完整走势；
- Viewer 只读取已经保存的定义和数据，不在页面内生成正式市场路径。

## 3. clean worktree 正式复跑

正式烟雾 Study 使用：

- PathSet：`btc-three-year-market-baseline-v1`；
- Scenario：`btc-long-range-v1`；
- TRAIN Market Seed：1101；
- VALIDATION Market Seed：2101；
- Strategy：HODL、单组跟随网格；
- Experiment Run Seed：0；
- 账户：1.1 BTC COIN-M 全仓账户；
- 费用：maker 0.02%、taker 0.05%，不计资金费。

验收结果：

- 编译为 2 条锁定市场路径和 4 个 Run；
- 4/4 Run 成功；
- `core/v1`、`btc-accumulation/v1`、`grid/v2` 均为 4/4 成功；
- SQLite 只包含 2 个 `market_path_id`，各由两个策略复用；
- Study 最终状态为 `EVALUATED`；
- provenance 为 clean、`reproducible=true`；
- TRAIN / VALIDATION 均未强平或破产；
- 无 HOLDOUT Run。

单组跟随网格分别完成 119 / 106 个循环，最终相对 HODL 多 `0.02433519` /
`0.02702143 BTC`。这些结果只用于证明市场、策略、执行、记账、指标和数据库调用链
成立，不用于选择策略。

## 4. 回归与一致性检查

冻结提交完成以下检查：

- BTC、ETH 两个 PathSet 全量重新物化，均返回现有内容锁，Manifest 身份不变；
- 单体仓库 `market_simulator`、`strategies_system`、`grid_trading` 三个工程全量回归通过；
- 固定行情、Anchored GBM v1、实验系统、指标系统和现有网格规则回归通过；
- 6B 历史基线数据库和已保存的对比报告仍可读取；
- 第六部分只能通过显式选择协议使用 TRAIN / VALIDATION；
- 文档、代码、Manifest 和 Viewer 对 PathSet 数量、角色与状态的表达一致；
- 验收结束时 Git worktree clean，标签指向被复验的同一提交。

## 5. 后置事项

- 4G 不对全部 192 条路径生成 `5m` 数据；
- 只有当 6C / 6D 形成少量候选、且 `1h` Bar 内路径顺序可能改变结论时，才做
  `1h` / `5m` 对照；
- 极端冲击、现实波动率再校准、资金费市场条件化和更高频成交不改变本次已冻结路径；
- 新假设、新参数或新生成器必须发布新版本，不能原地刷新本次内容锁；
- 本次只创建本地冻结标签，远端推送由后续明确指令执行。
