from __future__ import annotations

import re
import unittest
from pathlib import Path


class ExperimentResultsFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.viewer = Path(__file__).resolve().parents[1] / "viewer"

    def test_results_frontend_is_strategy_neutral_and_read_only(self) -> None:
        sources = "\n".join(
            (self.viewer / name).read_text(encoding="utf-8").lower()
            for name in (
                "experiments.html",
                "experiments.css",
                "experiment-api.js",
                "research-model.js",
            )
        )
        for strategy_term in (
            "following-grid",
            "grid_count",
            "rsi",
            "coinm",
        ):
            self.assertIsNone(
                re.search(
                    rf"(?<![a-z0-9_]){re.escape(strategy_term)}(?![a-z0-9_])",
                    sources,
                )
            )
        for mutation_method in (
            'method: "post"',
            'method: "put"',
            'method: "patch"',
            'method: "delete"',
        ):
            self.assertNotIn(mutation_method, sources)
        self.assertIn("页面不重新计算指标", sources)

    def test_player_accepts_dynamic_read_api_url(self) -> None:
        app = (self.viewer / "app.js").read_text(encoding="utf-8")
        self.assertIn('.get("run_api")', app)
        self.assertIn("fetch(DEFAULT_RUN)", app)

    def test_results_frontend_uses_research_information_architecture(self) -> None:
        html = (self.viewer / "experiments.html").read_text(
            encoding="utf-8"
        )
        script = (self.viewer / "experiment-api.js").read_text(
            encoding="utf-8"
        )
        styles = (self.viewer / "experiments.css").read_text(
            encoding="utf-8"
        )

        for page in (
            "strategy-overview",
            "strategy-detail",
            "strategy-runs",
            "rule-overview",
            "rule-detail",
            "market-overview",
            "experiment-overview",
            "experiment-detail",
            "playback",
        ):
            self.assertIn(f'data-page="{page}"', html)
            self.assertIn(f'data-page-panel="{page}"', html)
        self.assertIn('id="market-chart"', html)
        self.assertIn('id="market-role-select"', html)
        self.assertIn('id="market-profile-facts"', html)
        self.assertIn('data-interval="1w"', html)
        self.assertIn('data-interval="1m"', html)
        self.assertIn('id="detail-scenario-select"', html)
        self.assertIn('id="detail-seed-select"', html)
        self.assertIn('id="run-return-chart"', html)
        self.assertIn('id="run-margin-risk-chart"', html)
        self.assertIn('id="run-performance-asset-switch"', html)
        self.assertIn('class="run-tearsheet-layout run-report-width"', html)
        self.assertIn('id="playback-frame"', html)
        self.assertIn('id="strategy-rule-body"', html)
        self.assertIn('id="strategy-coordination"', html)
        self.assertIn('id="strategy-lifecycle"', html)
        self.assertIn('id="strategy-run-select"', html)
        self.assertIn('id="strategy-run-head"', html)
        self.assertIn('id="strategy-run-body"', html)
        self.assertIn("每个 Experiment 只占一行", html)
        self.assertIn('strategyRuleName(rule)).join("&")', script)
        self.assertIn("Runs / 版本", script)
        self.assertIn("function openStrategyExperiment", script)
        self.assertIn("Model.strategyExperimentGroups(strategy)", script)
        self.assertIn("查看参数研究 →", script)
        self.assertIn("experimentOverviewHref(record.experiment)", script)
        self.assertIn('id="rule-formulae"', html)
        self.assertIn('id="rule-constraints"', html)
        self.assertIn('id="rule-parameters"', html)
        self.assertIn('id="strategy-constraints"', html)
        self.assertIn('id="strategy-parameters"', html)
        self.assertIn("页面不重新计算指标", html)
        for metric_key in (
            "return.total_rate",
            "risk.max_drawdown_rate",
            "run.liquidated",
            "margin.max_maintenance_utilization",
            "margin.minimum_buffer",
            "margin.max_effective_leverage",
            "execution.fill_count",
            "cost.total_fees",
        ):
            self.assertIn(metric_key, script)
        self.assertIn('`${formatNumber(value.value, 2)}×`', script)
        self.assertIn(
            "Model.buildCatalog(rawRecords, components.items || [])",
            script,
        )
        self.assertIn('request("/api/components")', script)
        self.assertIn("Model.scenarioRows(record)", script)
        self.assertIn("Model.aggregateBars(", script)
        self.assertIn("Model.pathSetMarkets(state.pathSets)", script)
        self.assertIn("Model.marketAssetGroups(state.markets)", script)
        self.assertIn('"market-asset-group"', script)
        self.assertIn("币种与行情类型", html)
        self.assertIn('request("/api/market-path-sets")', script)
        self.assertIn("HOLDOUT 路径已经物化并锁定", script)
        self.assertIn("renderExperimentOverview()", script)
        self.assertIn("Model.studyGroups(state.records, strategy.type)", script)
        self.assertNotIn("Model.researchFocusGroups(state.strategies)", script)
        self.assertIn("state.strategyDefinitions.forEach((strategy)", script)
        self.assertIn("Model.strategyDefinitionRuns(", script)
        self.assertIn("Model.strategyParameterAxisGroups(", script)
        self.assertIn("Model.companionStrategies(", script)
        self.assertIn("规则组成：", script)
        self.assertIn("配合策略：", script)
        self.assertIn("Model.candidateRows(", script)
        self.assertIn("TRAIN 收益中位数", script)
        self.assertIn("VALIDATION 收益中位数", script)
        self.assertIn("用于比较", script)
        self.assertIn("function tearSheetCharts(candidates)", script)
        self.assertIn("function tearSheetMetrics(candidates)", script)
        self.assertIn("指标尚未评估；完成 MetricSet 计算后", script)
        self.assertIn("收益、最差回撤与保证金风险", script)
        self.assertIn("峰值保证金风险", script)
        self.assertIn("峰值初始保证金占用", script)
        self.assertIn("最大实际仓位倍率", script)
        self.assertIn("成交与完整循环", script)
        self.assertIn("资金费净变动", script)
        self.assertIn('path === "execution.funding_rate"', script)
        self.assertIn("参数组合与 Run 明细", script)
        self.assertIn("loadSelectedRun()", script)
        self.assertIn('"performance"', script)
        self.assertIn("function renderPerformanceCharts()", script)
        self.assertIn("experimentGroups.forEach", script)
        self.assertIn('performanceSeries(asset, "return_rate")', script)
        self.assertIn("function liquidationDistanceSeries()", script)
        self.assertIn("function marginPriceSeries(field)", script)
        self.assertIn("function drawPriceReturnChart(", script)
        self.assertIn('marginPriceSeries("mark_price")', script)
        self.assertIn('marginPriceSeries("estimated_liquidation_price")', script)
        self.assertIn("价格、预计强平价与累计收益", html)
        self.assertIn(
            "point.margin?.minimum_liquidation_distance_rate",
            script,
        )
        self.assertIn("全时点最小安全距离", script)
        self.assertIn("距强平安全距离", html)
        self.assertIn("timeline: performance.points || []", script)
        self.assertIn("const domainSeries = timeline.length ? timeline : series", script)
        self.assertIn('make(\n        "details",', script)
        self.assertIn(
            'url.searchParams.set("experiment", experiment.experiment_id)',
            script,
        )
        self.assertIn(
            'url.searchParams.set("database", experiment.database_name)',
            script,
        )
        self.assertIn("link.href = experimentDetailHref(experiment)", script)
        self.assertIn(
            "titleLink.href = experimentDetailHref(record.experiment)",
            script,
        )
        self.assertIn("state.overviewExperimentId", script)
        self.assertIn('card.dataset.requested = requested ? "true" : "false"', script)
        self.assertIn("experimentApiPath(experiment", script)
        self.assertIn('requestedParams.get("experiment")', script)
        self.assertIn('requestedParams.get("database")', script)
        self.assertIn('"experiment-detail-link"', script)
        self.assertIn("scenarioLiquidationRate(scenario)", script)
        self.assertIn('label = "强平状态"', script)
        self.assertIn("descriptor.formulae", script)
        self.assertIn("descriptor.config_fields", script)
        self.assertIn("交易规则目录", html)
        self.assertIn("策略定义目录", html)
        self.assertIn("strategyDefinitions", script)
        self.assertIn("一级按 StrategyDefinition 分组", html)
        self.assertIn("PARAMETER_STUDY 实验", html)
        self.assertRegex(
            styles,
            r"\.tearsheet-layout\s*\{[^}]*"
            r"grid-template-columns:\s*minmax\(0,\s*1fr\);",
        )

        model = (self.viewer / "research-model.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('item.kind === "strategy-definition"', model)
        self.assertIn("descriptor: strategyDescriptors.get", model)
        self.assertNotIn("strategyDescriptors.forEach", model)
        self.assertIn("runs: []", model)
        self.assertIn("function pathSetMarkets(pathSets)", model)
        self.assertIn("function marketAssetFromInstrument(instrument)", model)
        self.assertIn("function marketAssetGroups(markets)", model)
        self.assertIn(
            "function studyGroups(records, strategyDefinitionType)",
            model,
        )
        self.assertIn(
            "function strategyDefinitionRuns(record, strategyDefinitionType)",
            model,
        )
        self.assertIn(
            "function strategyExperimentGroups(strategyDefinition)",
            model,
        )
        self.assertIn("function strategyParameterAxisGroups(", model)
        self.assertIn('"EXPERIMENT_SPEC"', model)
        self.assertIn('"PROVIDER_SUMMARY"', model)
        self.assertIn('record.experiment_kind === "PARAMETER_STUDY"', model)
        self.assertIn("function candidateRows(", model)
        self.assertIn("funding_median", model)
        self.assertIn('"execution.funding_rate"', script)
        self.assertIn("function applicationSummary(run)", model)
        self.assertIn("function companionStrategies(run)", model)
        self.assertIn("function researchFocusGroups(strategies)", model)
        self.assertIn("IDENTITY_PARAMETER_KEYS", model)
        self.assertIn("策略参数实验", html)


if __name__ == "__main__":
    unittest.main()
