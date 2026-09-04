# 交易账本

独立运行的 Streamlit + SQLite 本地交易账本，用于按项目维护观察来源、人工买卖、现金、持仓、收益和估值报告。

当前已经完成统一账本后端、页面接线以及估值报告。模块不导入仓库内其他业务模块；HTTP API 尚未实现。

## 启动

```bash
cd trading_ledger
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m streamlit run app.py --server.port 8522
```

默认地址为 `http://localhost:8522`。该端口与仓库内其他本地服务分开使用。

## 页面

- 当前跟踪：维护观察股和来源，刷新价格，按百分比买卖；
- 操作历史：查看真实来源、买卖信号和冲正记录；
- 交易统计：记录每日估值，查看总权益走势，以及月度收益、回撤、波动率并导出 CSV；走势图纵轴固定为当月期初资金的 ±15%；
- 项目管理：创建、进入、编辑、归档和恢复项目。

当前项目由“项目管理”统一选择，并写入页面 URL。刷新浏览器或重启服务后会按 `project_key` 恢复，不需要在其他页面再次选择。

## 数据与配置

默认数据库为模块目录下的 `data/trading_ledger.sqlite3`，首次启动只创建 Schema，不创建默认项目。原型阶段遗留在模块根目录的 SQLite 文件不会被读取，也没有数据迁移流程。

常用环境变量：

| 变量 | 默认值 | 用途 |
| --- | --- | --- |
| `TRADING_LEDGER_DB_PATH` | `data/trading_ledger.sqlite3` | 独立账本路径；相对路径按模块根目录解析 |
| `TRADING_LEDGER_OPERATOR` | 当前系统用户 | 写入审计事件的操作者 |
| `TRADING_LEDGER_TIMEZONE` | `Asia/Shanghai` | 业务时区 |

SQLite、环境文件、备份和导出文件均已在 `.gitignore` 中排除。

需要生成独立的 2026 年 8 月演示项目时，可执行：

```bash
python3 scripts/seed_august_demo.py
```

脚本只创建“2026年8月模拟”项目；重复执行不会重复写入。数据均为合成数据，不代表真实成交。

## 已实现规则

- “来源”是观察说明，添加和编辑观察股时必填；
- 每笔买入和卖出都必须填写独立“信号”；
- 买入比例以确认时可用资金为基数，卖出比例以确认时可卖持仓为基数；
- A 股非清仓按 100 股整手折算，100% 卖出允许清理合法零股；
- 买卖佣金均为成交额万分之 `0.85`，最低 `5 CNY`；
- 卖出另收 `0.1%` 印花税和十万分之一过户费；
- 账本不限制同日买卖，全部持仓均可参与卖出比例计算，卖出按 FIFO 核算成本；
- 交易、现金、批次、持仓和审计在同一 SQLite 事务中更新；
- 查询不会隐式清理观察、刷新价格或写估值。

## 测试

```bash
cd trading_ledger
python -m pip install -e .
python -m unittest discover -s tests -v
python -m unittest -v test_trade_ui.py
```

测试数据库全部使用临时目录。

详细字段和后续 API 合同见 [CONTRACTS.md](CONTRACTS.md)，总体阶段计划见 [trading_ledger_integration_plan.md](../docs/trading_ledger_integration_plan.md)。
