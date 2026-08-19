# Simulation Viewer

通用仿真运行结果播放器。页面使用 TradingView 风格的深色配色，支持：

- 标准 OHLC 日线 K 线；
- 播放、暂停、逐日推进和时间轴跳转；
- 悬停十字线与精确 OHLC；
- 等待中的被动意图价格水平线；
- 绿色买单/B 成交标记与红色卖单/S 成交标记；
- 当前现金、持仓、持仓均价、净已实现盈亏、累计手续费、资金费净入账和总权益；
- 成交参考价、最终有效价和有符号滑点 bps；旧结果缺少滑点字段时按零滑点显示；
- 随时间推进的账户权益曲线；
- 截至当前回放日期的成交明细、Maker/Taker 角色和逐笔手续费；
- 主动/被动意图的等待、成交和撤销生命周期；
- 正常完成、强平终止和穿越破产线的 Run 状态；
- 每日保证金余额、维持保证金、可用余额和预估强平价；
- K 线上的强平标记及触发时完整账户事实；
- 打开其他 `SimulationRun` JSON。

启动：

```bash
cd market_simulator
python3 -m http.server 8088 --directory viewer
```

访问 `http://127.0.0.1:8088/`。

重新生成默认数据：

```bash
python3 scripts/generate_ladder_run.py
```

当前默认文件为 `viewer/data/deterministic-probe-run.json`。它只用于核对仿真框架的
主动/被动意图、成交、账本和回放，不代表任何研究策略。策略运行应从实验结果页面进入，
或在新的 Rule → Strategy → Application 组合完成实验后再显式导出。

COIN-M 强平展示样例位于
`viewer/data/coinm-liquidation-adverse-extreme-v1.json`。它使用 5 倍杠杆、10 张
BTCUSD 永续合约和 `ADVERSE_EXTREME` 日线采样，在第 4 根 K 线的 77,000 USD
盘中低点触发强平并终止；没有生成虚构的强平平仓成交。重新生成并查看：

```bash
cd ../grid_trading
python3 scripts/run_coinm_liquidation_demo.py
```

随后在 Viewer 右上角选择“打开 run JSON”，载入上述文件。

6 日逐笔核对数据可通过 `python3 scripts/generate_probe_run.py` 生成，并从页面右上角
手动载入。
数据约束见 `viewer/simulation-run.schema.json`。

Viewer 同时支持两种文档：schema v1 的 `orders` 会在页面内投影为展示意图；
schema v2 直接读取 `intents` 和 `instructions`。兼容投影只影响显示，不改变原始
run 数据或成交结果。没有 9F 状态字段的历史 v1/v2 文档按“正常完成、无保证金
快照”读取；新的强平文档必须同时包含 `run_status`、`margin` 和
`account_events`。

资金费文档使用顶层 `funding_events` 保存强类型结算事件，并在 manifest 中明确
`funding_enabled`、`funding_source` 和 `funding_market_conditioned`。旧文档缺少
这些字段时按零资金费读取；`total_funding` 正数表示账户收到，负数表示账户支付。

新生成的 Fill 使用 `reference_price`、`slippage_amount` 和 `slippage_bps` 记录滑点。
`price` 始终表示账本、费用和保证金实际使用的最终有效成交价。旧文档缺少滑点字段时
Viewer 使用 `reference_price = price`、滑点为零。

实验研究入口 `experiments.html` 使用左侧分组导航组织以下页面：

- 规则总览：直接读取 TradingRuleRegistry，展示规则类型、输入、输出、产品能力和研究记录；
- 规则详情：展示规则状态机、公式、约束、内部 RuleConfig 契约及其 Strategy 使用记录；
- 市场环境：直接列出已锁定 PathSet 的 Scenario、TRAIN/VALIDATION Seed 和周/月
  K 线，同时保留实验数据库中使用过的市场记录；
- 参数研究：新式 Strategy 先按核心 Trading Rule Type 分组，再把相同市场、执行成本和账户条件归为一个 Study；旧结果自动保留为历史 Strategy 分组；
- StrategyApplication 结果：Run 保留 Application、StrategyInstance、RuleInstance、Allocation 与 PositionOwner 身份，Study 展示规则组成和按 StrategyInstance 汇总的配合 Strategy；
  Study 展开后使用 tearsheet 报告布局，横向比较参数组合的 TRAIN/VALIDATION 收益
  中位数、相对 HODL、最差回撤、强平率和成交/循环数，原始市场路径与 Seed 按需
  展开；
- Run 详情：固定到一个策略、一个配置、一个市场和一个 Seed，使用 tearsheet 双栏布局
  展示累计收益、保证金强平风险率和紧凑绩效指标；BTC/USDT 权益可切换，配置与全部指标折叠核对；
- K 线播放：在实验上下文内嵌现有逐日播放器，也可在独立窗口打开。

页面从 SQLite 中读取已经保存的 `core/v1` 和应用扩展指标，不在浏览器重算指标。
TradingRuleRegistry 独立提供规则目录，因此清空实验数据库后规则总览和规则详情仍会
显示已注册 Rule；Strategy、Study、配置和实际 RuleInstance 记录只有运行实验后才出现。
BTC 计价收益优先用于区分币本位策略效果；USDT 总收益会标注“含行情”，避免把标的
自身涨跌误认为策略收益。全部 MetricSet 和组件参数仍保留在实验详情中供核对。
同一个 `experiment_id` 存在多份数据库时，参数研究页优先选择带标签、可复现且 clean
的版本参与比较，其他执行版本折叠保留，避免重复计入样本。

PathSet K 线由服务端读取内容锁定的 Parquet，并聚合为周线或月线。HOLDOUT 只在目录
中显示身份和锁定状态；页面不会请求或接收其价格、画像和 K 线。

研究入口必须通过实验结果服务启动，普通静态 HTTP Server 不提供 `/api`：

```bash
cd ../strategies_system
PYTHONPATH=src python3 -m strategy_simulation serve-results \
  experiments/experiment_results \
  --viewer-root ../market_simulator/viewer \
  --port 8088
```
