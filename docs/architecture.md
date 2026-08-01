# Grid Trading 架构

## 顶层边界

网格实盘工程与相邻策略工程共同形成以下业务边界：

| 命名空间 | 负责 | 不负责 |
| --- | --- | --- |
| `grid_rule/` | 给定网格参数后的 Cell、订单意图和成交状态转换 | 建仓时机、资金分配、实盘可靠执行 |
| `strategies_system/trading_strategies/` | 创建、组合、调整和退出一组或多组网格 | Binance、SQLite、HTTP、仿真循环 |
| `grid_server/` | 实盘 API、页面、调度、存储、交易所接入和一致性恢复 | 仿真市场生成、策略研究框架 |

依赖方向为：

```text
trading_strategies ─────► GridRulePort ◄──── strategy_simulation/GridRuleEnginePort
                                                   │
                                                   ▼
                                              GridRuleEngine

simulation_runtime ─────► strategy_simulation ─────┘
```

`market_simulator` 只调用注入的通用决策接口，不导入任何具体网格策略。当前
`grid_server` 尚未切换到新规则；高层策略经 `strategies_system` 完成研究和仿真，
验证后再通过实盘 Adapter 接入 Server。

## Grid Server 内部结构

服务内部按变化原因拆分。交易所协议、数据库、进程管理和 HTTP 展示可以分别修改与测试。

```text
app.py (Streamlit)
    │
    ▼
interfaces ─────► application ─────► domain
    │                  │                ▲
    │                  ▼                │
    └──────────► infrastructure ───► ports
                       ▲
runtime ───────────────┘
```

`runtime` 是进程组合层：它把 application 用例与 infrastructure 适配器装配成长期运行的服务。`interfaces` 是请求组合层：它把 FastAPI 请求转换成 application 调用。以下目录都位于 `grid_server/`。

## 目录职责

| 目录 | 放什么 | 不放什么 |
| --- | --- | --- |
| `domain/` | 数据模型、状态枚举、纯网格计算 | HTTP、SQLite、Binance 请求、进程控制 |
| `ports/` | 外部能力的 Protocol/抽象 | 具体交易所或数据库实现 |
| `application/` | 单次交易 tick、策略用例、持仓资源池协调 | Web 路由、环境变量解析、进程启动 |
| `infrastructure/` | Binance、SQLite、快照缓存 | 页面逻辑、策略生命周期编排 |
| `runtime/` | 调度周期、共享进程、信号和 worker | 领域公式、API 响应结构 |
| `interfaces/` | FastAPI 路由、前端 HTTP client | 交易决策、SQL |
| `shared/` | 小型无状态通用工具 | 核心业务规则 |

## 依赖规则

1. `domain` 不依赖其他项目层。
2. 外部系统通过 `ports` 描述，通过 `infrastructure` 实现。
3. `application` 决定业务行为，不读取环境变量，也不直接发 HTTP 请求。
4. `runtime` 和 `interfaces` 负责实例化具体实现。
5. Streamlit 页面只通过 `interfaces.web_client` 访问后端，不直接导入数据库、交易引擎或 Binance 密钥。
6. `grid_server` 根目录旧模块只是兼容入口；新增代码必须导入分层后的规范路径。
7. `grid_rule` 不得反向依赖 `grid_server` 或 `strategies_system`。
8. 策略核心只能依赖规则 DTO 和策略侧 `GridRulePort`，不得创建具体 `GridRuleEngine`。
9. 策略核心不得依赖仿真运行时；协议转换只能放入 `strategy_simulation/adapters`。

## 常见修改的位置

| 修改内容 | 主要位置 |
| --- | --- |
| Cell 状态或网格公式 | `domain/` |
| 触发、成交、补单、移动窗口逻辑 | `application/engine.py` |
| 多组持仓资源池和异常修复 | `application/position_coordinator.py` |
| Binance 签名、订单或仓位字段 | `infrastructure/binance.py` |
| 表结构、事务和查询 | `infrastructure/sqlite_store.py` |
| 轮询周期、共享快照和负载控制 | `runtime/scheduler.py` |
| REST 请求与响应 | `interfaces/api.py` |
| 页面展示与交互 | `app.py`、`interfaces/web_client.py` |

## 兼容策略

旧路径（例如 `grid_server.engine`、`grid_server.store`、`grid_server.api`）暂时继续可用，并转发到规范实现。现有脚本和测试无需一次性修改；新代码应使用 `grid_server.application.engine`、`grid_server.infrastructure.sqlite_store`、`grid_server.interfaces.api` 等规范路径。

兼容层不允许新增业务代码。等所有部署脚本和外部调用迁移完成后，可以单独版本化删除。

## 多合约产品边界

`StrategyConfig.market_type` 是产品边界，不是展示字段。USDⓈ-M 与 COIN-M 各自拥有一个 `SnapshotExchange`；调度器只在同一产品族内共享价格、开放订单和仓位快照。持仓协调器按 `market_type + symbol + positionSide` 建池，SQLite 迁移时旧数据明确归为 `usdm`。

COIN-M 的用户配置使用单格标的币数量；下单时按 `币数量 × Cell 价格 ÷ contractSize` 换算为最接近的有效张数。Cell `open_qty`、订单 `original_qty/executed_qty` 和持仓 `positionAmt` 仍统一使用“合约张数”，保证订单同步和仓位穿透修复不受价格变化影响；API 和页面仅在展示边界按对应买卖价格换算回币数量。
