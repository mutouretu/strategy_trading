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

- 当前跟踪：输入股票代码后点击“获取”补全后缀与名称，也可手动填写；来源必填，名称留空时可在刷新价格后补全，按百分比买卖；
- 操作历史：股票代码和名称合并为一列，查看真实来源、买卖信号、交易比例、总仓占比和冲正记录；交易比例为当时填写的买入可用资金比例或卖出可卖持仓比例，未记录比例时显示 `—`；总仓占比为本笔成交金额（不含税费）÷ 交易前总权益，不是用户填写的买卖比例，也不是交易后该股的持仓占比；
- 交易统计：顶部八项统计按两行四列显示，不展示外部资金净流入和年化波动率卡片；点击“估值走势”旁的图表图标记录今日估值，查看总权益走势、月度收益和回撤并导出 CSV；走势图横轴固定覆盖所选月份并按天刻度显示，纵轴固定为当月期初资金的 ±15%；
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
- 清仓后继续保留当前跟踪，仓位为 0，可直接再次买入；页面只有手动归档会移出该股票，归档不删除交易历史；
- 当前跟踪的“买入/卖出均价”合并为一列，按该项目该股票的累计有效成交金额 ÷ 累计成交股数计算，不含税费、排除冲正；“实盈/浮盈”合并为一列，均显示百分比：实盈为累计已实现净盈亏 ÷ 已卖出部分的 FIFO 成本（含买入费用），浮盈为剩余持仓未实现盈亏 ÷ 剩余持仓成本。实盈包含已发生税费，浮盈不预扣未来卖出费用。清仓后保留均价与实盈，浮盈为 0；纯观察记录两项均为 `—`；
- 当月统计汇总和股票状态使用最新账本，清仓后立即显示“已清仓”，无需重新记录估值；历史月份及走势图仍读取估值快照；
- 统计明细中已清仓股票的收益率为截至所选月末、该项目该股票的累计实际收益率：`（卖出净回款 − 买入总支出）÷ 买入总支出`，包含税费，排除冲正交易；跨月和多轮买卖累计计算，不等同于本月收益率；
- 交易、现金、批次、持仓和审计在同一 SQLite 事务中更新；
- 查询不会隐式清理观察、刷新价格或写估值。

“当前价格”和持仓估值优先采用真实行情，不会被时间更晚的手工成交价覆盖。完全没有行情时才使用成交价兜底，并在当前价格下标注“成交参考”；自动每日估值仍要求当天真实行情，不接受成交参考价。

操作历史的总仓占比：该股按本笔成交价、其他持仓按交易时已有参考价估值；新交易在现有交易元数据中保存交易前总权益，后续价格变化不影响该笔占比。旧记录按记账顺序、资金流水和当时已有行情只读还原；观察、冲正和历史依据不足的记录显示 `—`。补录交易使用录入前的账本持仓及现金，不代表对过去账户状态的完整回放。

## 自动每日估值

Linux 服务器可以启用独立的 systemd 定时任务，不依赖浏览器或 Streamlit 页面保持打开。
默认北京时间周一至周五 15:15 刷新各活动项目的行情并记录当天估值，已归档项目跳过。
现金项目无需行情；有持仓的项目若刷新失败、价格日期不是当天或价格时间在未来，则跳过并记录错误。
单个项目失败不会中断其他项目。同一项目同一天重复执行更新原快照，不新增重复记录。

目前没有交易所节假日日历：周末跳过；节假日或停牌导致价格日期较旧时，有关持仓项目跳过，现金项目仍可生成快照。
任务记录执行时的持仓和资金，不能自动补算漏掉的历史日期。当天 15:15 之后补录交易时，使用页面“记录今日估值”更新，或重新运行当天任务。

服务器路径为 `/opt/strategy_trading_ledger/trading_ledger`、运行用户为 `admin` 时，更新代码后执行：

```bash
cd /opt/strategy_trading_ledger/trading_ledger
sudo install -m 644 deploy/trading-ledger-valuation.service /etc/systemd/system/
sudo install -m 644 deploy/trading-ledger-valuation.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now trading-ledger-valuation.timer
sudo systemctl list-timers --all trading-ledger-valuation.timer
```

配置使用与网页服务相同的 `TRADING_LEDGER_DB_PATH`。自定义数据库、目录、用户或时区时，需同步修改 service 和 timer。
首次使用先打开账本创建项目。定时任务不会因错误的数据库路径创建一个空账本。

查看执行日志，或在工作日 15:15 之后手动重试当天任务：

```bash
sudo journalctl -u trading-ledger-valuation.service -n 60 --no-pager
sudo systemctl start trading-ledger-valuation.service
```

进程退出码非零表示存在失败项目。服务未运行期间错过的时间不会在重启后补写历史；定时器下一工作日继续执行。

## 测试

```bash
cd trading_ledger
python -m pip install -e .
python -m unittest discover -s tests -v
python -m unittest -v test_trade_ui.py
```

测试数据库全部使用临时目录。

详细字段和后续 API 合同见 [CONTRACTS.md](CONTRACTS.md)，总体阶段计划见 [trading_ledger_integration_plan.md](../docs/trading_ledger_integration_plan.md)。
