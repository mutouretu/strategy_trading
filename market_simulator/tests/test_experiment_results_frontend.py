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

        for page in (
            "strategy-overview",
            "strategy-detail",
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
        self.assertIn('id="playback-frame"', html)
        self.assertIn('id="strategy-formulae"', html)
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
        self.assertIn('request("/api/market-path-sets")', script)
        self.assertIn("HOLDOUT 路径已经物化并锁定", script)
        self.assertIn("renderExperimentOverview()", script)
        self.assertIn("loadSelectedRun()", script)
        self.assertIn('make(\n        "details",', script)
        self.assertIn('url.searchParams.set("experiment", experimentId)', script)
        self.assertIn('requestedParams.get("experiment")', script)
        self.assertIn('"experiment-detail-link"', script)
        self.assertIn("scenarioLiquidationRate(scenario)", script)
        self.assertIn('label = "强平状态"', script)
        self.assertIn("strategy.descriptor?.formulae", script)
        self.assertIn("strategy.descriptor?.parameters", script)

        model = (self.viewer / "research-model.js").read_text(
            encoding="utf-8"
        )
        self.assertIn("strategyDescriptors.forEach", model)
        self.assertIn("runs: []", model)
        self.assertIn("function pathSetMarkets(pathSets)", model)


if __name__ == "__main__":
    unittest.main()
