# OKX U 本位合约固定等 U 网格

本版本位于 `feat/okx-swap-grid`，继承 `feat/fixed-equal-usdt-grid` 的网格计算、初始持仓、每格收益和复投逻辑。首版只支持 OKX 的 `*-USDT-SWAP`、1 倍、全仓、做多固定天地网格；触发式网格与现货仍使用原有实现或暂不开放。

## 交易所边界

- `GRID_EXCHANGE=okx` 选择 OKX 适配器，缺省仍为 `binance`。API 和调度器使用同一配置。
- OKX 与币安使用独立数据库、调度器 PID、日志、API 和页面端口。SQLite 会把数据库绑定到交易所；错误地用另一交易所启动时直接拒绝。
- OKX 数量在系统内部按标的币计，提交订单和读取成交时按合约的 `ctVal` 换算为张数；价格、最小张数、张数步长和手续费均从 OKX 读取。
- OKX 当前价格带通过 `public/price-limit` 读取。超出价格带的远端挂单暂缓；现价越过网格线时，追价限价单仍限制在 OKX 允许的边界内。
- 每侧常规挂单窗口以 10 笔为目标，少于 5 笔才补满。窗口内卖单在交易所被手动撤销后，程序先核对实际持仓；确认一致且没有外部平仓单占用仓位时，近价持仓格优先补卖，不等待常规阈值。价格已经跌过某格的买线时，该格待买单优先占用 10 笔买单目标中的一个名额；窗口满时先撤较远旧单，再补这格。
- OKX K 线上的订单线被拖动会修改交易所实际挂价。当前运行中订单巡检不会逐轮校正手动改过的价格；撤销后才会进入上述持仓核对与补单流程。因此不要拖动本策略的挂单线。
- 账户须预先设为双向持仓，目标合约的全仓多头杠杆须为 1 倍。程序只检查这些设置，不代用户改变账户模式或杠杆。
- 成交历史和手续费按 OKX 返回值入库。OKX 的成交历史接口只覆盖最近三个月；长时间停机后，历史覆盖不足时纯网格净利和自动复投会停止确认，不能把缺失手续费当零。

## 独立启动

在本工作区的 `grid_trading` 目录下，后端和前端终端都设置各自需要的变量。密钥文件继续放在原工作区，不复制进 Git。

```bash
export GRID_EXCHANGE=okx
export GRID_ENV_FILE=/absolute/path/to/strategy_trading/grid_trading/okx.env
export GRID_DB_PATH="$PWD/runtime/okx/grid.sqlite3"
export GRID_RUNTIME_DIR="$PWD/runtime/okx"
export GRID_API_URL=http://127.0.0.1:8112

# 后端：使用已安装依赖的 Python 环境
python -m uvicorn grid_server.interfaces.api:create_app --factory --host 127.0.0.1 --port 8112 --workers 1

# 另一个终端，导出相同的 GRID_API_URL 后启动页面
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8512
```

访问 `http://127.0.0.1:8512`。页面从 `/health` 读取交易所并显示“OKX 实盘”或“OKX 模拟盘”。模拟盘需另设 `OKX_SIMULATED=1`，并使用模拟盘可用的 API 凭证。币安与 OKX 的后端可保持独立运行，再按 [统一前端配置](../README.md#币安与-okx-统一前端) 接入同一个页面。

网格详情主表只显示订单状态和数量；较长的 OKX 订单号收在表格下方的「完整订单号」折叠区，可按格子核对原始编号。

预览和创建草稿只读 OKX 合约、费率与价格，不会下单。实盘启动策略需要显式设置 `GRID_ENABLE_OKX_LIVE_FIXED_GRID=1`，然后由用户在页面启动；设置此变量本身不会启动策略。停止策略只暂停调度，不会自动撤单或平仓。

## 验证记录

2026-11-13，已用当前 OKX 凭证只读验证 `SOXL-USDT-SWAP`：最小数量和步长均为 `0.01 SOXL`，合约乘数为 `1 SOXL/张`，报价步长为 `0.01 USDT`，费率接口返回 maker `0.02%`、taker `0.05%`。`30–150 USDT`、`323` 格、总预算 `5000 USDT` 的预览返回成功；这只验证配置与行情读取，不代表已创建或启动实盘网格。

OKX API 字段和分页以[官方 API V5 文档](https://www.okx.com/docs-v5/en/)为准。
