# 第 4 部分：市场环境系统 v1.0 开发计划

## 1. 文档状态

本文定义策略仿真研究平台第四部分“市场环境”的 v1.0 目标、语义、架构、
场景协议、长期市场基线、开发批次和验收标准。

当前状态：4A—4D 已实现并完成首批内容锁定；4E Experiment / Study 接入为下一步。

2026-08-04 已一次性生成首版完整路径集：6 个场景 ×（8 个 TRAIN + 4 个
VALIDATION + 4 个 HOLDOUT）= 96 条三年 `1h` 路径，共 2,525,280 根 K 线。
Path Manifest 的内容锁指纹为
`dc171110a1394d76a0d42215bce1c8699c94671d8a61c42ecf3784ab8a04933e`。

已确认首版长期市场基线使用三年周期，起始价格使用场景冻结时点的真实
`BTCUSD_PERP` 参考价格，不使用归一化 `P0` 作为正式配置值。其余首批决定按本文
推荐方案执行。

制定本文的直接原因是：第 6 部分 6B 已经用真实 `BTCUSD_PERP` 1 分钟行情完成
执行、记账、指标和 HODL 对比的技术基线，但该历史区间只有约 120 天，主要覆盖
60,000 美元附近的高波动横盘和一段下跌，不能代表未来三至五年的宏观走势假设。

因此开发顺序调整为：

```text
6B 真实历史执行基线
    ↓
第 4 部分：长期市场环境基线
    ↓
6C 人工规则改进
    ↓
6D 参数扫描
    ↓
6E 跨环境和成本鲁棒性
```

6B 结果继续保留，用于校验真实行情接入、账户计算和成交行为，不作为长期策略研究的
唯一市场依据。

本文承接：

- 《策略仿真研究平台总体规划》；
- 《第 1 部分：仿真执行系统 v1.0》；
- 《第 2 部分：实验系统 v1.0》；
- 《第 3 部分：评价指标系统 v1.0》；
- 《第 5 部分：策略体系 v1.0》；
- 《第 6 部分：策略研究与优化 v1.0》；
- 已实现的固定行情、Anchored GBM、日内 Anchored GBM 和历史 Parquet 数据源。

---

## 2. 模块定位

市场环境系统回答：

> 策略是在什么市场假设、什么价格路径和什么波动结构下运行的？

它不负责预测一条“最可能的未来价格”，而是将研究者对未来的基本判断转换为一组
可复现、可比较、可施加压力的市场路径。

市场环境系统负责：

- 表达宏观市场假设；
- 从假设生成具体 OHLC 路径；
- 读取和锁定历史行情；
- 为同一假设生成多个 Seed；
- 计算市场路径自身的画像；
- 保存场景、模型、Seed、生成器版本和数据哈希；
- 将正式路径交给实验系统使用。

市场环境系统不负责：

- 决定什么时候建仓、建网格或平仓；
- 根据未来锚点提前调整策略；
- 计算订单成交、手续费、保证金或强平；
- 选择最优策略或参数；
- 为未来场景声明主观概率并自动计算“期望收益”；
- 将生成行情描述成价格预测结论。

---

## 3. 当前实现基线

### 3.1 已具备

`market_simulator` 当前已经具备：

- `MarketFrame` 与 `MarketSource` 通用协议；
- `FixedBarMarketSource` 和 `FixedSequenceMarketSource`；
- 日线 `AnchoredGBMMarketSource`；
- 可执行日内路径 `AnchoredGBMIntradayMarketSource`；
- 硬价格锚点；
- 固定年化波动率；
- 固定 Seed 和可重复生成；
- 可选价格上下界；
- OHLC 与时间连续性校验；
- `ParquetMarketSource`；
- Parquet 文件哈希和语义内容哈希校验。

`experiment_system` 当前已经具备：

- 市场、策略、执行、账户和参数轴组合；
- 多 Seed；
- 内容寻址的市场 Parquet；
- 市场引用和实验 provenance；
- SQLite 中的 Run、Trace、指标和市场引用。

`strategies_system` 当前已经具备：

- `anchored-gbm/v1` 市场组件；
- `anchored-gbm-intraday/v1` 市场组件；
- `historical-parquet/v1` 市场组件；
- 正式 Study 的 TRAIN、VALIDATION、HOLDOUT 角色；
- 对 HOLDOUT 提前进入第六部分调参的编译期防御；
- Binance COIN-M 真实历史行情的内容锁定样例。

### 3.2 当前不足

现有生成器仍是“路径生成工具”，还不是完整的市场环境系统：

- 硬锚点只能表达某日必须到达某个精确价格；
- 整条路径只能使用一个固定年化波动率；
- 没有“基准牛市、延迟牛市、长期震荡”等正式场景语义；
- 没有把方向假设和随机波动分开建模；
- 没有场景族、路径集和 Seed 角色；
- 没有统一的市场画像和场景验收指标；
- 生成行情没有像正式历史行情一样形成内容锁定的清单；
- Viewer 可以播放单条 K 线，但不能先阅读场景假设并比较多个市场路径；
- 现有 6B 历史窗口过短，不能承担未来数年策略优化的主要市场基线。

---

## 4. 核心语义

### 4.1 Asset Profile

Asset Profile 描述资产的稳定属性，而不是某一条未来判断。

BTC v1 Profile 至少包含：

- 24×7 连续市场；
- 每年 365 个自然日；
- 价格最小量化单位；
- 默认展示和执行周期；
- 可接受的波动率范围；
- 不使用交易时段休市和股票涨跌停规则。

资产 Profile 不包含“未来会涨到 200,000”之类的假设。

### 4.2 Market Scenario

Market Scenario 是一项可解释的宏观假设，例如：

- 低位震荡后进入大牛市；
- 牛市延迟两年；
- 先深度回撤再进入牛市；
- 提前上涨后发生大幅回撤；
- 长期宽幅震荡；
- 原始判断完全失败并进入长期熊市。

Scenario 只声明市场条件，不引用任何策略名称和参数。

### 4.3 Market Model

Market Model 是把 Scenario 转换为具体路径的数学机制。

v1.0 的核心模型为：

```text
价格锚点或锚点区间
    + 分段波动率
    + 固定 Seed
    ↓
对数价格空间中的分段随机桥
    ↓
连续的小时级 OHLC
```

Brownian Bridge / Anchored GBM 继续作为基础，不在 v1.0 一开始引入 GARCH、
神经网络或订单簿生成模型。

### 4.4 Market Path

Market Path 是某个 Scenario、模型版本和 Seed 解析后得到的一条具体
`MarketFrame` 序列。

一条正式 Market Path 必须具有：

- 唯一 `market_path_id`；
- `scenario_id`；
- `model_type` 和版本；
- `asset_profile_id`；
- Seed；
- 已解析的具体锚点；
- 时间范围和 K 线周期；
- 帧数；
- 内容哈希；
- Parquet 文件哈希；
- 市场画像；
- 数据角色。

### 4.5 Market Path Set

Market Path Set 是研究时使用的正式市场集合：

```text
多个 Scenario
    × 每个 Scenario 的多个 Seed
    × TRAIN / VALIDATION / HOLDOUT 角色
```

策略研究比较的是 Path Set 上的结果分布，不能只比较一条“漂亮路径”。

### 4.6 Historical Dataset

Historical Dataset 是实际发生过的行情，必须与 Synthetic Market Path 明确区分。

两者可以进入同一个 Study，但必须保留：

- `origin=HISTORICAL` 或 `origin=SYNTHETIC`；
- 独立的来源与生成 provenance；
- 独立聚合结果；
- 禁止把生成行情描述成历史收益。

---

## 5. 总体架构

```text
market_simulator
│
├── Asset Profile
├── Scenario Schema / Catalog
├── Market Model Registry
├── Long-horizon Generator
├── Path Profiler
├── Path Materializer
└── Manifest / Content Lock
          │
          ↓ MarketFrame / locked Parquet
experiment_system
│
├── Market Component
├── Seed / Cartesian Product
├── Market Reference
└── SQLite Run Facts
          │
          ↓
strategies_system
│
├── Strategy Plugin
├── Scenario Study
├── TRAIN / VALIDATION selection
└── Strategy metrics and optimization
```

依赖方向必须保持：

```text
策略可以读取当前和过去 MarketFrame

市场环境不能导入或调用具体策略
市场环境不能读取策略结果后修改既有路径
simulation_runtime 不能感知 Scenario 的宏观含义
```

---

## 6. v1.0 目标

第四部分 v1.0 需要完成：

1. 定义版本化 Asset Profile、Market Scenario 和 Market Path Set；
2. 首版固定三年长期 BTC 市场基线，协议允许以后新增其他周期版本；
3. 支持硬锚点和区间锚点；
4. 支持按锚点区间配置分段年化波动率；
5. 为同一 Scenario 生成多个确定性 Seed；
6. 生成适合网格策略执行的连续小时 OHLC；
7. 将日线、周线和月线作为聚合展示，不将日线作为正式网格执行基线；
8. 将正式生成路径物化为内容寻址 Parquet；
9. 计算每条路径和每个场景族的市场画像；
10. 冻结首批长期 BTC 市场基线；
11. 建立 TRAIN、VALIDATION、HOLDOUT Seed 隔离；
12. 接入现有 Experiment 和 Study，不产生第二套实验运行器；
13. 在现有 Viewer 的“市场环境”页面展示场景说明和完整走势；
14. 为 6C—6E 提供稳定、可复现的市场输入。

---

## 7. 设计原则

### 7.1 场景不是预测

场景名称、说明和页面必须明确使用：

- “假设”；
- “压力场景”；
- “条件路径”；
- “研究范围”。

禁止使用：

- “预测价格”；
- “必然走势”；
- “最可能收益”；
- “保证到达”。

### 7.2 宏观判断与随机扰动分离

宏观判断决定：

- 大致方向；
- 阶段顺序；
- 锚点日期或日期区间；
- 价格目标区间；
- 主要波动率阶段。

Seed 决定：

- 锚点区间内的具体终点；
- 节点之间的具体随机路径；
- 局部高低点和到达顺序。

同一宏观假设下的不同 Seed 应保持相同的场景语义，但不能只是同一曲线的小幅平移。

### 7.3 策略不能获得未来信息

生成器可以使用未来锚点构造完整路径，但运行时只能按顺序暴露当前
`MarketFrame`。Strategy、Adapter、Trace 和指标输入均不得包含：

- 下一个锚点；
- 最终价格；
- 当前所属的未来场景阶段；
- 未发生的冲击；
- 后续波动率配置。

场景元数据只供实验解释和事后聚合，不进入策略状态。

### 7.4 正式路径先物化，再运行

开发时可以直接从生成器运行。正式研究必须：

```text
Scenario + Model Version + Seed
    ↓
生成并校验完整 Market Path
    ↓
物化 Parquet
    ↓
写入内容哈希和文件哈希
    ↓
Study 引用锁定路径
```

这样可以避免随机库、浮点实现或生成器代码变化后，旧实验名称相同但实际行情改变。

### 7.5 分辨率是研究协议的一部分

不同 K 线周期下的网格成交数不可直接比较。

v1.0 采用：

- `1h`：三至五年长期策略研究的标准执行周期；
- `5m`：少量候选和边界场景的高保真复核周期；
- `1d / 1w / 1M`：前端预览和宏观走势展示；
- `1m`：继续用于真实短区间成交频率校准，不用于第一版大规模长期生成实验。

日线高低价虽然可以覆盖挂单价，但不能确定一天内多个网格的触发顺序，因此不能作为
高频网格收益的正式执行依据。

### 7.6 不给场景强行设置概率

v1.0 的场景用于覆盖和压力测试，默认等权展示，不声明每个场景发生概率。

如果未来需要概率加权，必须单独版本化概率来源、估计方法和更新时间，不能把主观权重
隐藏在综合评分中。

### 7.7 HOLDOUT 不参与调参

每个 Scenario 的 Seed 分为：

- TRAIN：用于提出规则和参数扫描；
- VALIDATION：用于筛选稳定候选；
- HOLDOUT：只在第 7 部分正式验证时运行。

第六部分 Study 编译时继续禁止 HOLDOUT market key。

---

## 8. 场景协议

### 8.1 建议结构

```json
{
  "schema_version": "market-scenario/v1",
  "scenario_id": "btc-base-bull-v1",
  "name": "BTC 基准牛市假设",
  "description": "低位震荡或回撤后进入长期上涨阶段",
  "origin": "SYNTHETIC",
  "asset_profile_id": "btc-spot-reference/v1",
  "instrument": "BTCUSD_PERP",
  "horizon": {
    "start": "2026-08-01",
    "end": "2029-08-01"
  },
  "interval": "1h",
  "model": {
    "type": "anchored-regime-bridge/v1",
    "price_quantum": "0.1",
    "periods_per_year": 365
  },
  "anchors": [
    {
      "date": "2026-08-01",
      "target": {"type": "HARD", "price": "62794.3"}
    },
    {
      "date": "2027-08-01",
      "target": {"type": "BAND", "minimum": "40000", "maximum": "60000"}
    },
    {
      "date": "2028-08-01",
      "target": {"type": "BAND", "minimum": "130000", "maximum": "180000"}
    },
    {
      "date": "2029-08-01",
      "target": {"type": "BAND", "minimum": "100000", "maximum": "170000"}
    }
  ],
  "volatility_regimes": [
    {
      "start": "2026-08-01",
      "end_exclusive": "2027-08-01",
      "annual_volatility": {"minimum": "0.45", "maximum": "0.70"}
    },
    {
      "start": "2027-08-01",
      "end_exclusive": "2028-08-01",
      "annual_volatility": {"minimum": "0.55", "maximum": "0.90"}
    },
    {
      "start": "2028-08-01",
      "end_exclusive": "2029-08-01",
      "annual_volatility": {"minimum": "0.65", "maximum": "1.00"}
    }
  ],
  "metadata": {
    "thesis": "研究假设，不是价格预测",
    "status": "DRAFT"
  }
}
```

该示例的研究期和起点已经与首版正式配置对齐：2026-08-01 至 2029-08-01，起点
`62,794.3` 美元来自 Binance COIN-M `BTCUSD_PERP` 2026-07-31 23:59 UTC
分钟收盘价。中间和终点 BAND 仍只用于说明协议；六个正式场景的数值以配置文件为准。

### 8.2 Anchor 类型

v1.0 支持：

- `HARD`：指定日期必须到达精确价格，用于初始价格和确定性测试；
- `BAND`：指定日期到达一个价格区间，具体价格由 Seed 决定。

暂不支持：

- 复杂概率分布；
- 从策略结果反向调整 Anchor；
- 模型自动预测 Anchor；
- 未版本化的人工运行时改值。

### 8.3 Anchor 使用实际绝对价格

首版场景正式配置使用实际绝对价格，例如：

```text
40,000
60,000
150,000
200,000
```

`P0` 和相对倍数仍可以用于文档解释与跨起点比较，但不作为首版正式生成器输入。这样
可以确保：

- Viewer 直接展示真实价格；
- 策略订单价格与场景价格使用同一量纲；
- Manifest 不依赖运行时再次解析 `P0`；
- 同一场景版本的起始价格不会随执行日期变化。

如果未来需要将同一场景结构迁移到其他起点，应创建新场景版本，不能静默缩放已经
锁定的路径。

### 8.4 分段波动率

波动率必须属于 Scenario，而不是 Strategy。

第一版至少支持：

- 每个 Anchor 区间独立的年化波动率；
- 固定值或区间值；
- Seed 对区间值进行确定性解析；
- Manifest 保存最终解析值。

v1.0 不要求根据价格自动预测波动率，也不要求生成真实的波动率微笑。

### 8.5 极端事件

闪崩和跳跃需要显式建模，不能依赖 GBM 偶然生成。

但为避免 4A 范围过大，第一批长期基线可以先用密集 Anchor 表达主要暴跌和恢复；
后续再增加版本化的 `shock_events`：

- 冲击时间窗口；
- 跌幅或涨幅区间；
- 冲击持续时间；
- 是否恢复；
- 恢复时间区间。

极端事件属于 4G 增强项，不阻塞首批长期基线。

---

## 9. 首批长期 BTC 场景族

首批建议冻结六个互补场景。以下相对价格路径只表示场景结构，实际 Anchor 和日期在
4C 前由研究者确认。

### 9.1 S1：基准牛市

```text
P0
→ 0.65～0.90 P0 的建仓与震荡阶段
→ 1.20～1.60 P0 的趋势确认阶段
→ 2.20～3.00 P0 的牛市阶段
→ 高位回撤或震荡结束
```

主要验证：低位建仓、币本位积累、牛市前网格收益以及高位退出。

### 9.2 S2：延迟牛市

```text
P0
→ 两至三年的 0.60～1.10 P0 宽幅震荡
→ 研究后期才进入 2.00～3.00 P0
```

主要验证：策略能否长期承受资金占用、费用和多次跟随复位。

### 9.3 S3：深度回撤后牛市

```text
P0
→ 0.45～0.65 P0
→ 低位停留
→ 逐步恢复
→ 2.00～3.00 P0
```

主要验证：建仓节奏、保证金安全、强平风险以及低位资本是否过早耗尽。

### 9.4 S4：提前牛市后暴跌

```text
P0
→ 较早进入 2.00～3.00 P0
→ 40%～60% 回撤
→ 中等程度恢复或长期高位震荡
```

主要验证：是否过早卖出 BTC、高位网格库存以及牛熊转换时的回撤。

### 9.5 S5：长期宽幅震荡

```text
长期运行于 0.70～1.40 P0
多次上沿和下沿往返
没有真正的大牛市
```

主要验证：网格成交频率、费用侵蚀、跟随与复位规则是否真正创造 BTC 超额。

### 9.6 S6：原始判断失败

```text
P0
→ 0.60～0.80 P0
→ 0.40～0.70 P0 长期停留
→ 期末仍未恢复到 P0
```

主要验证：如果“未来有大牛市”的核心判断错误，策略会亏损多少、是否强平以及还能
保留多少 BTC。

### 9.7 场景之间的要求

六个场景必须满足：

- 不是只改变最终价格；
- 低点出现时间不同；
- 高波动阶段出现时间不同；
- 上涨和回撤顺序不同；
- 至少一个场景不进入牛市；
- 至少一个场景出现深度回撤；
- 至少一个场景长期震荡；
- 每个场景都有明确的研究意义和失效机制。

---

## 10. Seed 与数据角色

### 10.1 推荐首版规模

每个场景建议：

- TRAIN：8 个 Seed；
- VALIDATION：4 个 Seed；
- HOLDOUT：4 个 Seed。

六个场景共计：

```text
6 × (8 + 4 + 4) = 96 条长期路径
```

三年 `1h` 路径约 26,300 根 K 线，96 条路径约 252 万根 K 线，适合作为第一版
长期研究规模。

正式高保真 `5m` 路径约为 `1h` 的 12 倍，不对全部参数候选运行，只用于少量入选
策略和极端路径复核。

### 10.2 Seed 身份

Seed 不能仅作为一个裸整数。正式 Manifest 需要绑定：

```text
scenario_id
model_type + model_version
asset_profile_id
role
seed
resolved anchors
resolved volatility regimes
path content hash
```

### 10.3 HOLDOUT 处理

本文推荐提前物化 HOLDOUT，以便同一版本一次性锁定完整 Path Set；但在第六部分必须：

- 不生成 Viewer 预览；
- 不执行策略；
- 不计算策略指标；
- 不用于决定规则和参数；
- 只在第七部分打开。

HOLDOUT 的完整 K 线和市场画像不进入前端。第六部分 Study 不得提供运行入口；只有
第七部分可以读取这些已锁定路径。

### 10.4 Market Seed 与 Run Seed

开发期直接运行生成器时，Experiment Seed 可以直接作为 Market Seed。

正式路径物化后，Market Seed 已经成为 Parquet 路径身份的一部分，不能再被 Experiment
Seed 重新随机化。此时采用：

```text
每条物化路径 = 一个明确 market component
market component metadata 保存原始 Market Seed
Experiment 使用单一确定性 Run Seed
```

如果未来执行或其他组件也需要随机数，再单独版本化 Run Seed。正式 Experiment 不得把
“路径组件列表 × 同一组 Market Seed”再次做笛卡尔积，否则会重复运行相同行情。

---

## 11. 长期路径生成模型

### 11.1 v1 核心模型

新增 `anchored-regime-bridge/v1`，在当前 Anchored GBM 基础上增加：

- HARD / BAND Anchor 解析；
- 每段独立波动率；
- 小时级 OHLC；
- 场景和 Seed provenance；
- 正式物化；
- 内容哈希。

现有 `anchored-gbm/v1` 和 `anchored-gbm-intraday/v1` 继续保留，不做破坏式替换。

### 11.2 小时 OHLC

标准 `1h` Bar 不应只有 open 与 close。生成每根小时 K 线时，内部至少生成若干子步，
再聚合得到：

- open；
- high；
- low；
- close。

必须满足：

```text
high >= max(open, close)
low <= min(open, close)
open(t) = close(t-1)
timestamp 严格递增且无缺口
price > 0
```

子步只用于构造 OHLC，不向 Strategy 暴露。如果候选结果对小时内触发顺序敏感，必须
使用 `5m` 路径复核，不能从小时 high/low 推断唯一顺序。

### 11.3 价格边界

价格 floor / ceiling 是人工模型假设，可能改变尾部概率。正式场景必须显式记录：

- 是否启用；
- 边界值；
- 边界处理方式；
- 触碰次数和持续时间。

默认不使用反射边界来“美化”路径。需要表达最低 40,000 或最高 200,000 时，优先用
Anchor 区间；只有明确要求价格绝不越界时才启用硬边界。

### 11.4 后续模型接口

Market Model Registry 为以后保留：

- Jump Diffusion；
- Regime Switching；
- Block Bootstrap；
- GARCH / 随机波动率；
- 深度条件生成模型。

所有模型最终仍只输出 `MarketSource` / `MarketFrame`，不能建立第二套执行系统。

---

## 12. 市场画像指标

市场画像描述行情，不评价策略。

### 12.1 单路径指标

`market-profile/v1` 至少计算：

- 起始价和期末价；
- 总涨跌幅和年化涨跌幅；
- 最低价、最高价及发生时间；
- 峰谷最大回撤；
- 年化实现波动率；
- 上行和下行波动率；
- 最大单 Bar 涨跌幅；
- 20%、30%、50% 回撤事件数量；
- 价格低于 `0.75 × P0` 的时间占比；
- 价格低于 `0.50 × P0` 的时间占比；
- 价格高于 `1.50 × P0` 的时间占比；
- 价格高于 `2.00 × P0` 的时间占比；
- 最长低位停留时间；
- 最长高位停留时间；
- HARD Anchor 偏差；
- 硬边界触碰次数；
- K 线完整性和连续性。

### 12.2 场景族聚合

同一 Scenario 的多 Seed 至少展示：

- 期末价格分布；
- 最大回撤分布；
- 实现波动率分布；
- 最低价和最高价分布；
- 低位停留时间分布；
- 场景约束通过率；
- 异常路径和原因。

### 12.3 画像的用途

市场画像用于回答：

- 生成路径是否符合场景描述；
- 不同 Seed 是否具有足够差异；
- 是否出现大量不合理边界反射；
- TRAIN、VALIDATION 和 HOLDOUT 的难度是否明显失衡；
- 1h 与 5m 聚合后的宏观特征是否一致。

市场画像不得包含策略收益、成交数或强平结果。

---

## 13. 数据持久化与版本

### 13.1 建议目录

```text
market_simulator/
├── market_environments/
│   ├── asset_profiles/
│   ├── scenarios/
│   ├── path_sets/
│   ├── manifests/
│   └── generated/          # Parquet，默认 Git ignore
├── packages/
│   └── market_simulator/
│       └── src/market_simulator/
│           ├── scenarios/
│           ├── generators/
│           ├── profiling/
│           └── materialization/
└── tests/

strategies_system/
└── research/
    └── scenario_studies/   # 只引用已发布的 Market Path Set
```

最终目录名在 4A 可以根据现有 Python 包结构微调，但职责不能混合。

### 13.2 Path Manifest

每条路径 Manifest 至少包含：

- schema version；
- scenario definition hash；
- model implementation version；
- asset profile hash；
- role 和 Seed；
- resolved anchors；
- resolved volatility regimes；
- interval 和 frame count；
- first / last timestamp；
- semantic content SHA-256；
- Parquet file SHA-256；
- market profile version 和结果；
- materialized time；
- participating code revision。

### 13.3 不可变规则

同一 `scenario_id + model version + seed + role` 已经锁定后：

- 内容相同：重复物化为幂等操作；
- 内容不同：拒绝覆盖；
- 需要修改：提升 Scenario 或 Model 版本；
- 不允许只改文件名继续使用旧身份。

---

## 14. 与现有实验和策略系统的关系

### 14.1 Experiment

实验系统继续只处理：

```text
market component
× strategy component
× execution component
× account component
× parameter axes
× seeds
```

对于正式长期市场，每条已物化 Path 作为一个明确 market component，组件元数据保存
Scenario、role 和原始 Market Seed；Experiment 使用单一确定性 Run Seed，不再次展开
Market Seed。Experiment 不负责解释 Anchor 或重新生成路径。

### 14.2 Strategy

策略只接收顺序到达的 `MarketFrame` 和自身状态。第四部分不修改：

- `trading_strategies`；
- `grid_rule`；
- Simulation Adapter；
- 订单与成交语义；
- COIN-M 或 USD-M 账户公式。

### 14.3 Study

`strategies_system/strategy_optimization` 负责选择：

- 使用哪些 Scenario；
- 使用哪些 TRAIN / VALIDATION Path；
- 策略和参数组合；
- 评价目标和筛选方法。

第四部分只发布路径和市场画像，不根据策略结果选择保留哪些 Seed。

### 14.4 6B 历史基线

6B 继续承担：

- 真实市场数据接入回归；
- 真实分钟波动和成交频率参考；
- COIN-M 权益、费用和 HODL 对比校验；
- 生成模型校准参考。

长期 Synthetic Path Set 承担：

- 未来三至五年宏观假设；
- 多种牛熊顺序；
- 多 Seed 路径不确定性；
- 规则和参数的长期研究输入。

两者互补，不能互相替代。

---

## 15. 前端展示

### 15.1 页面归属

继续使用现有 Viewer 的“市场环境”一级目录，不建立独立前端工程。

### 15.2 市场环境总览

按 Scenario 分组展示：

- 场景名称和版本；
- 宏观假设摘要；
- 时间范围；
- 起始价格或 `P0`；
- Anchor 区间；
- 波动率阶段；
- TRAIN / VALIDATION / HOLDOUT Path 数；
- 状态：DRAFT、LOCKED、RETIRED；
- 是否允许进入策略 Study。

默认收起每个 Scenario，点击后展开多 Seed 路径。

### 15.3 场景详情

展示：

- 场景假设说明；
- Anchor 时间线；
- 波动率阶段；
- 模型和版本；
- 已知局限；
- 多 Seed 市场画像分布；
- 周线或月线叠加图。

### 15.4 路径详情

展示单条路径：

- 周线、月线切换；
- 必要时切换日线；
- Anchor 标记；
- 最低点、最高点和最大回撤区间；
- 市场画像；
- Scenario、Seed、role 和内容哈希；
- 被哪些 Experiment 引用。

HOLDOUT 不展示完整 K 线，不提供策略运行链接。

### 15.5 Viewer 边界

Viewer 只读取已保存的定义、Manifest 和市场画像：

- 不在浏览器生成正式路径；
- 不在浏览器重新计算官方画像；
- 不修改 Scenario；
- 不运行策略；
- 不打开 HOLDOUT 内容。

---

## 16. 开发批次

### 16.1 4A：语义、Schema 和 Registry

开发内容：

- 定义 Asset Profile、Scenario、Anchor、Volatility Regime 和 Path Set；
- 定义严格 JSON Schema 和加载器；
- 定义 Market Model Registry；
- 固定 Scenario、Model、Path 的身份和版本算法；
- 建立架构依赖测试；
- 保留现有 v1 MarketSource，不破坏兼容。

验收条件：

- 非法日期、重叠波动率区间和错误 Anchor 被拒绝；
- Scenario 中出现策略字段时被拒绝；
- 相同文档产生稳定指纹；
- 市场模型必须显式注册；
- `market_simulator` 不导入 `strategies_system` 或 `grid_trading`。

### 16.2 4B：长期分段随机桥

开发内容：

- 实现 `anchored-regime-bridge/v1`；
- 支持 HARD / BAND Anchor；
- 支持分段波动率；
- 支持标准 `1h` OHLC；
- 保存 resolved anchors 和 resolved volatility；
- 保证固定 Seed 完全可重复。

验收条件：

- HARD Anchor 精确到达；
- BAND Anchor 不越界；
- 同 Seed 路径逐 Bar 相同；
- 不同 Seed 路径发生变化；
- OHLC、连续性、时间戳和价格合法；
- 三年路径可以在合理时间和内存内生成。

### 16.3 4C：首批长期 BTC 场景目录

开发内容：

- 冻结研究起点和周期；
- 冻结六个场景的 Anchor 和波动率范围；
- 为每个场景编写假设、用途和已知局限；
- 冻结 TRAIN、VALIDATION、HOLDOUT Seed；
- 校验场景之间不是简单复制。

验收条件：

- 六个场景均有独立机制意义；
- 覆盖牛市、延迟、深跌、暴跌、横盘和失败；
- 场景不包含策略逻辑；
- HOLDOUT Seed 与开发 Seed 分离；
- 用户确认宏观假设后才将状态改为 LOCKED。

### 16.4 4D：物化、市场画像和内容锁定

开发内容：

- 批量生成 Path Set；
- 持久化内容寻址 Parquet；
- 计算 `market-profile/v1`；
- 写入 Path Manifest；
- 实现幂等物化和冲突拒绝；
- 建立内容锁定测试。

验收条件：

- 正式 Path 可脱离生成器直接回放；
- 内容哈希和文件哈希均可验证；
- 重复生成不产生新身份；
- 生成器变化不能静默覆盖旧路径；
- 每个 Scenario 的画像符合其书面描述。

### 16.5 4E：Experiment / Study 接入

开发内容：

- 将锁定路径注册为市场组件；
- 让 Study 引用 Path Set 和 role；
- 保留历史与生成市场来源标识；
- 防止第六部分编译 HOLDOUT；
- 建立 HODL 和单一网格的最小烟雾实验。

验收条件：

- Experiment 只引用市场路径，不解释场景模型；
- 同一 Path 可供多个策略复用；
- Historical 与 Synthetic 结果不混淆；
- HOLDOUT 拒绝路径有自动化测试；
- 不修改策略核心和执行公式。

### 16.6 4F：Viewer 与市场目录

开发内容：

- 完成市场环境总览；
- 完成场景详情和路径详情；
- 周线、月线预览；
- 多 Seed 画像展示；
- Experiment 反向引用；
- HOLDOUT 隐藏。

验收条件：

- 用户可以先读懂假设，再选择市场；
- 长期图表无需加载全部小时 K 线；
- Seed、role 和哈希可见；
- 页面不执行正式计算；
- 不暴露 HOLDOUT 完整走势。

### 16.7 4G：高保真与现实校准

开发内容：

- 从 6B 和其他真实历史窗口计算波动率参考；
- 对少量路径生成 `5m` 高保真版本；
- 比较 `1h` 与 `5m` 下网格成交和收益差异；
- 增加显式冲击事件；
- 记录模型无法还原的真实市场特征。

验收条件：

- 不为了拟合单一策略修改市场路径；
- 校准只影响新版本 Scenario / Model；
- 1h 结果对分辨率敏感时有明确警告；
- 最终候选可以在 5m 路径复核；
- 不要求 v1.0 生成订单簿或资金费预测。

### 16.8 4H：总体验收与版本冻结

开发内容：

- 运行单体仓库三个工程的全量回归；
- 运行长期 Path Set 的完整校验；
- 生成场景和市场画像验收摘要；
- clean worktree 复现至少一条路径；
- 打标签后允许 6C 使用锁定市场基线。

验收条件：

- 4A—4G 的必选项全部通过；
- 现有 6B 历史基线仍可读取；
- 固定行情和 Anchored GBM v1 回归不变；
- 96 条首版三年路径身份稳定；
- 第六部分能够只选择 TRAIN / VALIDATION；
- 文档、代码、Manifest 和 Viewer 状态一致。

---

## 17. 自动化测试计划

### 17.1 Schema 测试

- 未知字段；
- 非法日期和时间范围；
- Anchor 未排序或重复；
- HARD / BAND 字段不匹配；
- BAND 上下界颠倒；
- 波动率区间缺口或重叠；
- 非法 interval；
- 非法 role 和重复 Seed；
- Scenario 中混入策略参数。

### 17.2 生成器测试

- 相同 Seed 字节级稳定；
- 不同 Seed 路径不同；
- HARD Anchor 精确；
- BAND Anchor 合法；
- OHLC 不变量；
- 时间连续；
- 分段波动率确实影响路径；
- 长周期无负价格、NaN 或无穷值；
- 空间和时间复杂度边界。

### 17.3 物化测试

- Parquet round trip；
- 内容哈希稳定；
- 文件被改动时拒绝；
- 语义内容变化时拒绝；
- 同身份不同内容发生冲突；
- Manifest 缺字段时拒绝；
- 路径不完整时不升级为 LOCKED。

### 17.4 市场画像测试

- 手算涨跌幅和最大回撤；
- 年化实现波动率；
- 阈值停留时间；
- 回撤事件计数；
- 多 Seed 分位数；
- 日/周/月聚合保持开高低收关系。

### 17.5 集成测试

- 锁定 Path 进入 Experiment；
- 同 Path 被两个策略复用；
- Scenario 信息不进入 Strategy 输入；
- Historical / Synthetic 来源可区分；
- HOLDOUT 在第六部分被拒绝；
- Viewer 只读；
- HODL 与网格最小长期实验成功。

---

## 18. v1.0 暂不支持

- 预测唯一未来价格；
- 为六个场景自动分配概率；
- 根据策略收益挑选或删除 Seed；
- 订单簿、盘口深度和排队生成；
- 市场多空比和情绪数据生成；
- 资金费率预测；
- 多资产相关路径；
- GARCH、随机波动率和 Regime Switching 的完整模型库；
- Transformer、GAN、VAE 或 Diffusion；
- 自动在线校准；
- 根据实盘结果实时改写锁定场景；
- 在浏览器生成或修改正式行情；
- 将 Synthetic Path 宣称为历史回测或价格预测。

这些内容必须在基础场景协议、数据锁定和策略评价稳定后单独规划。

---

## 19. 主要风险与处理

### 19.1 精确 Anchor 导致路径过度确定

处理：未来场景优先使用 BAND Anchor，并在不同 Seed 中解析不同具体终点。

### 19.2 策略对场景节点过拟合

处理：Strategy 不接收节点；使用多个 Seed；场景日期和价格设置区间；VALIDATION 与
HOLDOUT 分离。

### 19.3 单一 GBM 低估厚尾和波动聚集

处理：v1.0 明确记录局限；用分段波动率和显式冲击逐步增强；候选在真实历史和 5m
高保真路径复核。

### 19.4 小时线高低价无法表达唯一触发顺序

处理：小时线用于长期筛选；对成交密集的参数和最终候选使用 5m 路径复核；不同分辨率
结果不直接合并。

### 19.5 场景数量扩大实验规模

处理：先运行 HODL 和少量基线；参数扫描采用 TRAIN 粗筛、VALIDATION 复核；5m 只运行
少量候选；继续使用 `max_runs`。

### 19.6 生成器升级破坏复现

处理：正式路径物化、双哈希锁定、模型显式版本化；旧路径继续由 Parquet 回放。

### 19.7 主观假设被误解为结论

处理：Scenario 页面必须展示假设来源、版本、锁定时间和“非预测”声明；策略结果按每个
场景分别展示，不默认加权成一个收益数字。

---

## 20. v1.0 总体验收标准

第四部分 v1.0 完成时必须满足：

1. Scenario、Model、Path 和 Path Set 的语义边界明确；
2. 首批六个 BTC 长期场景已由用户确认并锁定；
3. 每个场景具有 TRAIN、VALIDATION、HOLDOUT Seed；
4. 至少形成 96 条三年的标准 `1h` 路径；
5. 所有路径满足 OHLC、连续性和 Anchor 约束；
6. 所有正式路径有内容哈希、文件哈希和 Manifest；
7. 市场画像可以说明每个场景是否符合书面假设；
8. 同 Scenario 多 Seed 有足够差异；
9. HOLDOUT 未用于第六部分策略研究；
10. 现有 Experiment 和 Study 可以引用路径；
11. Strategy 和 simulation runtime 未引入 Scenario 特殊逻辑；
12. Viewer 可以查看场景、周/月线和市场画像；
13. 至少一个 HODL 和一个网格策略完成长期烟雾实验；
14. 至少一个候选路径完成 `1h` / `5m` 分辨率差异复核；
15. 6B 真实历史基线仍然可复现；
16. 单体仓库三个工程的完整回归测试通过；
17. clean/tag 版本可以重新生成相同身份路径；
18. 第 6 部分可以在该 Path Set 上继续 6C。

---

## 21. 已确认的首批决定

2026-08-04 已确认：

1. 首版长期基线周期固定为三年；如需其他周期，新增版本，不修改已锁定基线；
2. 起始价格使用场景冻结时点的真实 `BTCUSD_PERP` 参考价格，并保存为绝对值；
3. 接受首批六个场景；
4. 关键 Anchor 日期和价格区间按第 9 节的场景结构在 4C 形成具体配置；
5. 标准执行周期采用 `1h`；
6. 最终候选使用 `5m` 复核；
7. 每个场景采用 8/4/4 个 TRAIN/VALIDATION/HOLDOUT Seed；
8. 第一版暂不加入显式 Jump/Shock，先用 Anchor 表达主要暴跌；
9. HOLDOUT 提前物化和锁定，但第六部分不展示、不运行；
10. 首版 Path Set 命名为 `btc-three-year-market-baseline-v1`。

首版配置已冻结为 2026-08-01 至 2029-08-01，起始硬锚点为 `62,794.3` 美元。
根据“直接全部生成，不合适再调”的决定，4D 未经过少量预览阶段，已经一次性物化并
锁定全部 96 条路径。以后如需修改 Anchor、波动率或生成器，不覆盖 v1 内容锁，而是
创建新的 Scenario、Model 或 Path Set 版本。

---

## 22. 首版实现与验收记录

### 22.1 已实现范围

- 4A：Asset Profile、Scenario、Anchor、Volatility Regime、Path Set、严格加载器、
  指纹算法和显式 Model Registry；
- 4B：`anchored-regime-bridge/v1`，支持 HARD / BAND Anchor、分段波动率、固定 Seed、
  小时 OHLC 和 Bar 内 6 个子步；
- 4C：六类 BTC 场景及 8/4/4 Seed 角色已经配置为 `LOCKED`；
- 4D：96 条路径已经物化为内容寻址 Parquet，完成市场画像、双哈希和 Manifest 内容锁。

实现位置：

```text
market_simulator/
├── packages/market_simulator/src/market_simulator/market_environment/
├── market_environments/
│   ├── asset_profiles/
│   ├── scenarios/
│   ├── path_sets/
│   ├── manifests/
│   └── generated/                 # 可复现生成，不进入 Git
└── scripts/materialize_market_path_set.py
```

### 22.2 数据验收结果

- 路径数：96，角色分布为 TRAIN 48、VALIDATION 24、HOLDOUT 24；
- 每条路径：26,305 根 `1h` K 线，覆盖 1,096 天并包含 2028 闰年；
- 总 K 线数：2,525,280；本地 Parquet 约 65 MB；
- 96 个语义内容哈希和 96 个文件哈希均唯一；
- 所有路径起点为 `62,794.3`，所有 HARD Anchor 偏差为 0；
- 各场景终点均落入其 BAND，HOLDOUT 已物化但禁止策略执行；
- 九项新增单元与物化测试通过。

当前模型允许锚点之间出现高于 BAND 的盘中超调。首版中，基准牛市场景的极端路径
最高约 602,441 美元，提前牛市后暴跌场景最高约 395,751 美元。这是高波动随机桥的
厚尾结果，不影响 Anchor 和内容锁正确性，但在 4G 真实历史校准时应明确判断是否需要
收窄波动率或增加软边界。不得为了提高某个策略的收益而挑选或删除这些 Seed。

4E—4H 尚未完成，因此此时只表示“长期行情输入已经生成并锁定”，不表示第四部分
整体已经验收，也不表示这些路径已经可以从 Experiment、Study 或 Viewer 中选择。

---

## 23. 推荐执行顺序

```text
4A 语义、Schema 和 Registry
    ↓
4B 长期分段随机桥
    ↓
4C 冻结首批 BTC 长期场景
    ↓
4D 路径物化、画像和内容锁定
    ↓
4E Experiment / Study 接入
    ↓
4F Viewer 市场环境页面
    ↓
4G 5m 高保真与真实历史校准
    ↓
4H 总体验收和版本冻结
    ↓
返回 6C 人工规则改进
```

第四部分完成后的产物不是一个市场预测模型，而是一套明确回答以下问题的研究输入：

```text
如果未来大势按这种结构发展，
但具体路径、波动和到达顺序存在不确定性，
策略的 BTC 积累、收益、回撤和强平风险会怎样？
```
