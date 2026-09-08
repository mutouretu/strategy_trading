# 交易账本阶段 1 合同

本文冻结页面、统一账本后端与后续 API 之间的首版边界。阶段 2、3、5 已按本合同实现；阶段 4 HTTP API 尚未开始。

## 1. 独立工程边界

- Python 包名为 `trading_ledger`，源代码位于 `src/trading_ledger`；
- 只使用模块自身配置和数据库，不导入仓库内其他业务包；
- Streamlit 与未来 HTTP API 共同调用 `TradingLedgerApplication`；
- 页面、API 和脚本不直接执行 SQL；
- 配置读取本身不创建目录、数据库或业务数据；
- 当前 Streamlit 页面已经接入统一应用服务，旧原型服务不再保留。

默认配置：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TRADING_LEDGER_DB_PATH` | `trading_ledger/data/trading_ledger.sqlite3` | 相对路径始终相对模块根目录解析 |
| `TRADING_LEDGER_TIMEZONE` | `Asia/Shanghai` | 业务展示时区 |
| `TRADING_LEDGER_OPERATOR` | 空 | 人工操作者；本地 UI 未配置时使用当前系统用户 |
| `TRADING_LEDGER_API_HOST` | `127.0.0.1` | API 默认只监听本机 |
| `TRADING_LEDGER_API_PORT` | `8787` | 独立于仓库现有服务的端口 |
| `TRADING_LEDGER_API_TOKEN` | 空 | API 实现后启用，不写入日志或数据库 |

## 2. 项目与账户边界

- `project_key` 是所有页面链接、Command、Query 和 API 路径使用的稳定外部标识，创建后不可修改；
- `project_key` 长度为 1–64，只允许小写字母、数字和连字符；
- `project_name` 是可修改显示名称，在全部项目中不区分大小写唯一；
- 首版一个项目自动创建一个 `primary-cny` 主账户，页面不要求用户重复选择账户；
- 后续可以在项目下增加账户，但任何交易、持仓、现金流水和统计都不能跨项目；
- 归档项目只读，不允许增加观察、交易或资金事实；
- 全新数据库为空，不自动创建“默认项目”。

## 3. 页面动作对应的 Command

| 页面动作 | Command | 关键输入 |
| --- | --- | --- |
| 新建项目 | `CreateProjectCommand` | 名称、初始资金、可选说明、操作者 |
| 编辑项目 | `UpdateProjectCommand` | `project_key`、名称、说明、操作者 |
| 归档/恢复项目 | `ArchiveProjectCommand` / `RestoreProjectCommand` | `project_key`、操作者 |
| 添加观察股 | `AddTrackedInstrumentCommand` | 项目、代码、可留空的名称、必填 `source_text` |
| 编辑观察股 | `UpdateTrackedInstrumentCommand` | 项目、`tracking_id`、代码、可留空的名称、必填 `source_text` |
| 删除当前跟踪 | `CloseTrackedInstrumentCommand` | 关闭当前跟踪，不删除历史事实 |
| 归档当前跟踪 | `ArchiveTrackedInstrumentCommand` | 归档当前跟踪，不删除历史事实 |
| 刷新价格 | `RefreshTrackingPricesCommand` | 项目、操作者 |
| 预览买卖 | `PreviewManualTradeCommand` | 项目、标的、方向、比例、成交价、必填信号 |
| 确认买卖 | `ConfirmManualTradeCommand` | 与预览相同并增加操作者 |
| 清理到期观察 | `ExpireTrackingCommand` | 项目、操作者、可选执行时间 |
| 记录每日估值 | `RecordDailyValuationCommand` | 项目、操作者、估值时间、`require_fresh_prices`（自动任务启用当天价格校验） |

预览仅用于展示，不锁定现金、持仓、价格或数量。确认时必须在同一事务中重新读取现金和可卖持仓、重新计算数量及费税并再次校验；不能直接信任前端预览值。

## 4. 页面查询与 ViewModel

| 页面 | Query | ViewModel |
| --- | --- | --- |
| 项目管理 | `ListProjectsQuery`、`GetProjectQuery` | `ProjectView` |
| 当前跟踪 | `ListTrackingQuery`、`GetAccountSummaryQuery` | `TrackingPageView`、`TrackingRowView`、`AccountSummaryView` |
| 买卖弹窗 | `PreviewManualTradeCommand` | `TradePreviewView` |
| 操作历史 | `ListOperationHistoryQuery` | `OperationHistoryPageView` |
| 交易统计 | `ListStatisticsMonthsQuery`、`GetMonthlyStatisticsQuery` | `MonthlyStatisticsView` |

所有项目内 Query 必须显式传入 `project_key`。查询没有隐式写入；观察到期、价格刷新和状态重算均由显式 Command 触发。

添加观察股页面只要求股票代码和来源。六位代码在保存时自动补全 `.SH`、`.SZ` 或 `.BJ`（包括 `920` 开头的北交所代码），名称通过刷新行情补齐。名称留空时复用已有标的名称，不清空其他项目已识别的名称；行情失败时保留原记录，可稍后重试。

添加框保留可编辑的代码和名称输入框。“获取”调用只读 `lookup_instrument(symbol)` 查询行情并返回 `InstrumentIdentityView`，回填后缀和名称，不创建观察记录或写入价格；用户仍需点击“保存到观察中”。获取失败时保留已输入内容，支持直接手工录入。

每日估值可以由独立定时任务调用，默认北京时间工作日 15:15 运行，不依赖页面访问。
自动任务先刷新行情，使用 `require_fresh_prices=True` 在估值事务内验证持仓价格为当天且不晚于估值时间；失败不覆盖已有快照。
该模式只记录当天，不提供历史回填；人工按钮仍使用原有估值行为。相同项目和日期沿用同一快照。

操作历史的 `source_or_signal` 规则固定为：

- 观察记录显示 `tracked_instruments.source_text`；
- 买入和卖出记录显示 `trade_records.signal_text`；
- “来源”就是观察说明，不另设选股说明字段；
- “信号”是每笔成交自己的说明，不从最近观察记录自动复制。

## 5. 比例、数量和费用合同

- Application 合同中的 `allocation_ratio` 使用 `Decimal` 比例值，范围为 `0 < ratio <= 1`；页面输入的 `25%` 在适配层转换成 `Decimal("0.25")`；
- 买入比例基数固定为确认时的可用资金，预算必须同时覆盖成交额和手续费；
- 卖出比例基数固定为确认时该标的的全部持仓，账本不限制同日买卖；
- A 股默认一手 100 股，非清仓数量向下折算到整手；卖出比例恰为 100% 时允许清掉合法零股尾差；
- 买卖手续费均为成交额的 `0.000085`，最低 `5.00 CNY`；
- 卖出印花税为成交额的 `0.001`，卖出过户费为成交额的 `0.00001`；买入两项均为零；
- 金额和费税逐项使用 `Decimal`，按分以 `ROUND_HALF_UP` 舍入；
- 前端只提交比例、成交价和信号，不提交最终数量或自行算出的费用。

## 6. 状态合同

| 对象 | 状态 |
| --- | --- |
| 项目 | `ACTIVE`、`ARCHIVED` |
| 账户 | `ACTIVE`、`FROZEN`、`ARCHIVED` |
| 当前跟踪 | `WATCHING`、`HOLDING`、`CLOSED`、`EXPIRED`、`ARCHIVED` |
| 交易方向 | `BUY`、`SELL` |
| 交易状态 | `DRAFT`、`CONFIRMED`、`REVERSED` |
| 录入来源 | `MANUAL`、`AUTO_TRADING`、`IMPORT` |
| 比例基数 | `AVAILABLE_CASH`、`SELLABLE_POSITION` |

`source_text` 和 `signal_text` 是两个不同业务字段；API 请求中的 `source` 表示录入来源，不表示观察说明。

## 7. 应用错误合同

应用层只抛出带 `ErrorCode` 的 `ApplicationError`，页面负责转为中文提示，API 负责转为 HTTP 状态。首版错误码：

| 类别 | 错误码 |
| --- | --- |
| 输入 | `INVALID_INPUT` |
| 项目 | `PROJECT_NOT_FOUND`、`PROJECT_ARCHIVED`、`DUPLICATE_PROJECT_NAME` |
| 账户/标的/观察 | `ACCOUNT_NOT_FOUND`、`ACCOUNT_UNAVAILABLE`、`INSTRUMENT_NOT_FOUND`、`TRACKING_NOT_FOUND` |
| 核算 | `INSUFFICIENT_CASH`、`INSUFFICIENT_SELLABLE_POSITION` |
| 交易/冲正 | `TRADE_NOT_FOUND`、`TRADE_ALREADY_REVERSED` |
| 幂等/并发 | `IDEMPOTENCY_CONFLICT`、`EXTERNAL_TRADE_CONFLICT`、`CONCURRENCY_CONFLICT` |
| 系统 | `INTERNAL_ERROR` |

错误包含 `code`、安全的 `message`、可选 `field` 和 `retryable`。不得把 SQL、数据库路径、密钥或 Authorization 内容放入对外错误。

## 8. `/api/v1` 合同

本节只冻结后续协议，当前没有 HTTP 监听端口或可调用路由。所有小数作为十进制字符串传输，时间使用带时区的 RFC 3339，所有写请求提供 `request_id`，认证客户端身份作为 `actor` 写入应用 Command。

### 8.1 公共错误体

```json
{
  "error": {
    "code": "INSUFFICIENT_CASH",
    "message": "可用资金不足。",
    "field": null,
    "retryable": false,
    "request_id": "01JEXAMPLE9K1A2B3C4D5E6F7G8"
  }
}
```

### 8.2 健康检查

`GET /api/v1/health`

```json
{"status": "ok", "service": "trading-ledger", "api_version": "v1"}
```

健康检查不访问或修改业务数据。

### 8.3 记录已确认成交

`POST /api/v1/projects/{project_key}/trades`

```json
{
  "request_id": "01JEXAMPLE9K1A2B3C4D5E6F7G8",
  "source": "AUTO_TRADING",
  "external_trade_id": "exchange-fill-20260902-0001",
  "account_code": "primary-cny",
  "market": "CN_STOCK",
  "venue": "SSE",
  "symbol": "600000.SH",
  "side": "BUY",
  "trade_time": "2026-09-02T10:15:30+08:00",
  "quantity": "100",
  "price": "12.34",
  "signal_text": "突破前期平台",
  "commission_amount": "5.00",
  "stamp_tax_amount": "0.00",
  "transfer_fee_amount": "0.00",
  "metadata": {"external_order_id": "order-20260902-001"}
}
```

`source` 允许 `AUTO_TRADING` 或 `IMPORT`。三个费用字段可以整体省略，由账本按统一规则计算；若提供则必须三个字段一起提供，并使用成交方确认值校验和记账。成功返回：

```json
{
  "trade_id": "trade-01JEXAMPLE",
  "project_key": "long-term-a",
  "account_code": "primary-cny",
  "market": "CN_STOCK",
  "venue": "SSE",
  "symbol": "600000.SH",
  "side": "BUY",
  "trade_time": "2026-09-02T10:15:30+08:00",
  "quantity": "100",
  "price": "12.34",
  "gross_amount": "1234.00",
  "commission_amount": "5.00",
  "stamp_tax_amount": "0.00",
  "transfer_fee_amount": "0.00",
  "net_cash_amount": "-1239.00",
  "signal_text": "突破前期平台",
  "execution_source": "AUTO_TRADING",
  "record_status": "CONFIRMED",
  "external_trade_id": "exchange-fill-20260902-0001",
  "request_id": "01JEXAMPLE9K1A2B3C4D5E6F7G8",
  "reversal_of_trade_id": null
}
```

响应字段使用 `snake_case`，枚举使用大写字符串，缺失的可选值使用 JSON `null`，金额和数量仍为十进制字符串。

### 8.4 查询与冲正成交

- `GET /api/v1/projects/{project_key}/trades/{trade_id}`：返回该项目下的 `TradeView`；项目不匹配时按不存在处理；
- `POST /api/v1/projects/{project_key}/trades/{trade_id}/reversals`：请求体为 `request_id`、非空 `reason`，成功返回新的冲正 `TradeView`，其 `reversal_of_trade_id` 指向原成交。

### 8.5 资金、账户和持仓

- `POST /api/v1/projects/{project_key}/cash-entries`：请求体为 `request_id`、`account_code`、`entry_type`、`amount`、`currency`、`occurred_at`、非空 `note`；`entry_type` 只允许 `DEPOSIT`、`WITHDRAWAL`；
- `GET /api/v1/projects/{project_key}/accounts/{account_code}`：返回 `AccountSummaryView`；
- `GET /api/v1/projects/{project_key}/accounts/{account_code}/positions`：返回 `PositionView` 数组。

### 8.6 参考价格

`POST /api/v1/reference-prices` 的请求体为 `request_id`、`market`、`venue`、`symbol`、`price`、`price_time`、`source_name`。参考价格只影响预览和估值，不生成成交。

### 8.7 幂等与 HTTP 状态

- 幂等范围为 `(project_key, request_id)`；同一请求和相同内容重试返回原结果；
- 同一幂等键但内容不同返回 `409` 和 `IDEMPOTENCY_CONFLICT`；
- 同一个 `(source, external_trade_id)` 在整个账本中只能记账一次，不能换项目重复提交；
- 首次创建成功返回 `201`，相同内容的幂等重试返回 `200`；
- 输入格式错误、缺失字段或空白信号返回 `400`；
- 未认证或无项目权限返回 `401` 或 `403`；
- 对象不存在返回 `404`；
- 归档、冻结、重复冲正、幂等和并发冲突返回 `409`；
- 现金不足或持仓不足返回 `422`；
- 可重试的数据库繁忙或内部故障返回 `503`，其他未分类故障返回 `500`。

## 9. 当前明确不做

- 不启动 FastAPI，不安装 API 框架，不接外部调用方；
- 不读取、兼容或迁移原型数据库；
- 不引入独立决策表、审批流或其他仓库模块依赖；
- 不提供自动下单、撤单、信号生成或策略控制能力。
