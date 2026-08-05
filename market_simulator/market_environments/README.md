# 市场环境目录

这里保存市场假设与已锁定路径的可复现定义，不保存策略、订单或收益结果。

```text
asset_profiles/  # 资产的稳定属性
scenarios/       # 宏观市场假设、锚点和分段波动率
path_sets/       # 场景组合及 TRAIN / VALIDATION / HOLDOUT Seed
manifests/       # 正式路径清单、市场画像、双哈希和内容锁
generated/       # 由配置重建的 Parquet，本地生成且不进入 Git
```

首版 `btc-three-year-market-baseline-v1` 使用 2026-08-01 至 2029-08-01 的绝对
BTC 美元价格，包含 6 个场景和 96 条小时路径。真实起点 `62,794.3` 来自 Binance
COIN-M `BTCUSD_PERP` 2026-07-31 23:59 UTC 分钟收盘价。

`eth-three-year-market-baseline-v1` 使用相同三年边界和角色数量，另有 6 个 ETH 场景
与 96 条小时路径。真实起点 `1,859.83` 来自 Binance COIN-M `ETHUSD_PERP`
2026-07-31 23:59 UTC 分钟收盘价；其 Anchor 和波动率按 ETH 自身尺度定义，不是把
BTC 路径按价格比例缩放。

在仓库根目录执行：

```bash
PYTHONPATH=packages/market_protocol/src:\
packages/market_simulator/src:\
packages/simulation_runtime/src:\
packages/experiment_system/src \
python3 scripts/materialize_market_path_set.py

python3 scripts/materialize_market_path_set.py \
  market_environments/path_sets/eth-three-year-market-baseline-v1.json
```

相同配置、模型版本和 Seed 会得到相同身份。脚本发现已有相同内容锁时保持幂等；内容
变化时拒绝静默覆盖。需要改变市场判断时应建立新版本，不能直接刷新已用于研究的锁。

HOLDOUT 会随 Path Set 一起物化，但在策略研究阶段禁止展示完整路径或运行策略。

## 查看路径

PathSet 不需要先运行策略实验即可进入 Viewer。统一仓库中从策略系统启动只读结果服务：

```bash
cd ../../strategies_system
PYTHONPATH=src python3 -m strategy_simulation \
  serve-results experiments/experiment_results \
  --viewer-root ../market_simulator/viewer \
  --port 8088
```

打开 `http://127.0.0.1:8088/experiments.html?page=market-overview`。页面按六个
Scenario 展示 PathSet，可分别切换 TRAIN、VALIDATION、Seed、周线和月线，并显示
期初/期末价格、区间高低点、最大回撤及实现波动率。

服务端从 Manifest 解析路径身份，读取时再次校验 Parquet 的文件哈希和内容哈希，再在
内存中聚合周线或月线。HOLDOUT 只返回场景、Seed、路径 ID 和锁定状态；价格、画像及
Parquet 内容接口在最终样本外验收前统一返回 `403`。
