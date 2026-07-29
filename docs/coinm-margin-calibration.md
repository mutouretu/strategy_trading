# COIN-M 保证金外部校准

本目录把“平台返回值”和“模型计算值”分开保存。校准文件不得包含 API Key、Secret、
账户别名、订单 ID 或其他身份信息。

当前固定基线
`tests/fixtures/binance_coinm_margin_official_zero_v1.json` 来自 Binance COIN-M
官方 API 文档中的空仓账户示例，固定了读取日期、接口版本、原始字段、绝对容差和局限。
它用于验证以下映射：

- wallet balance；
- unrealized PnL；
- margin balance；
- position initial margin；
- maintenance margin；
- available balance；
- position quantity 和 notional；
- 空仓时没有强平价格。

官方接口依据：

- `GET /dapi/v1/account` 提供资产钱包、未实现盈亏、保证金余额、维持保证金、仓位初始
  保证金和可用余额；
- `GET /dapi/v1/positionRisk` 提供 position amount、entry price、mark price、
  unrealized profit、liquidation price 和 leverage；
- `GET /dapi/v2/leverageBracket` 提供 symbol 级维持保证金档位。

官方文档链接已经保存在 fixture 的 `source_urls` 中。

## Demo 非零仓位基线

以下文件是从 Binance COIN-M Demo 只读采集的 AAVEUSD_PERP 全仓快照：

- `binance_coinm_margin_demo_aaveusd_perp_2026-07-28.json`：LONG 1 张；
- `binance_coinm_margin_demo_aaveusd_perp_short_2026-07-28.json`：SHORT 1 张；
- 两者合约面值均为 10 USD；
- 10 倍杠杆；
- 无 AAVE 挂单保证金占用；
- 第一档维持保证金率 2.5%；
- AAVE 钱包、仓位和平台强平价均经过字段白名单脱敏保存。

`tests/test_coinm_margin_demo_calibration.py` 离线比较仓位名义价值、未实现盈亏、保证金
余额、初始保证金、维持保证金、可用余额和预估强平价。账户与 positionRisk 是顺序
响应，account 接口没有返回它采用的标记价，因此测试从账户未实现盈亏反推该时点
mark，并要求由初始保证金和维持保证金分别反推的 mark 都在一个价格 tick 内收敛。
空仓样例还验证了 Binance 用 `0`、Runtime 用 `None/null` 表示“抵押资金大于反向
合约最大可能亏损，因此不存在有限正数强平价”的等价边界。

结算币直接返回的未实现盈亏和保证金余额使用 `1e-8 AAVE` 绝对容差；由反推 mark
再次计算的初始保证金、维持保证金和可用余额涉及两个 8 位字段，使用有明确来源的
`2e-8 AAVE` 组合舍入上界；强平价使用合约 tick size `0.010 USD`。采集器会在写文件
以前验证平台返回字段自身满足钱包、保证金余额和可用余额恒等式，拒绝连续请求期间
发生字段刷新错位的快照。

空仓样例只能证明字段映射和零值边界正确，不能证明非零仓位的强平价格已经与平台完全
一致。正式校准必须额外捕获一个只有单一 BTC 保证金仓位、没有挂单、没有其他 BTC
保证金仓位的脱敏快照，并同时保存：

```text
/dapi/v1/account
/dapi/v1/positionRisk
/dapi/v2/leverageBracket
/dapi/v1/exchangeInfo
```

捕获时必须保证四个响应尽量接近同一时刻。若 mark price 在请求之间变化，应重新捕获，
不能用放宽百分比误差掩盖时间错位。BTC 数值默认使用 `1e-8 BTC` 绝对误差，价格误差
按对应合约 tick size 定义。

当前第一档官方示例的 `cum=0` 可以直接用固定维持保证金率校准。更高档位必须先确认
Binance 当前 `qtyCap/qtyFloor/cum` 的产品单位，再转换成 Runtime 的
`MaintenanceMarginTier`，不得根据字段名猜测单位。

## 脱敏快照采集

仓库提供只读采集脚本，但不会在测试或示例中自动访问账户：

```bash
python3 scripts/capture_coinm_margin_calibration.py \
  --symbol BTCUSD_PERP \
  --output tests/fixtures/binance_coinm_margin-live-YYYY-MM-DD.json \
  --acknowledge-account-read
```

脚本读取 `BINANCE_API_KEY`、`BINANCE_API_SECRET` 和可选的
`BINANCE_COINM_BASE_URL`，依次获取服务器时间、exchangeInfo、account、
positionRisk 和 leverageBracket。输出采用字段白名单，不保存 API 凭据、账户别名、
订单信息或完整原始响应；已有输出文件不会被覆盖。

采集入口会拒绝以下不适合作为当前模型校准基准的账户状态：

- 目标合约为空仓，或 Hedge Mode 下同时存在两个非零方向；
- account 与 positionRisk 的仓位不一致；
- 逐仓仓位；
- 同一保证金币种存在其他非零仓位；
- 保证金币种仍有挂单占用。

账户即使启用了 Hedge Mode，只要 LONG、SHORT 中仅一个方向非零，也可以映射为
Runtime 的单净仓位进行校准；这里拒绝的是实际双向持仓，不是账户模式本身。

四个接口不是原子快照，因此文件同时记录 Binance server time 请求窗口。生成文件后
仍需人工确认窗口内标记价格没有发生足以超过价格 tick 容差的变化，再将脱敏文件作为
新的版本化 fixture 提交。脚本只解决可靠采集和脱敏，不会把未经核对的数据自动标记为
“模型已通过平台校准”。
