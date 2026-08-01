from __future__ import annotations

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
            )
        )
        for strategy_term in (
            "following-grid",
            "grid_count",
            "rsi",
            "coinm",
        ):
            self.assertNotIn(strategy_term, sources)
        for mutation_method in (
            'method: "post"',
            'method: "put"',
            'method: "patch"',
            'method: "delete"',
        ):
            self.assertNotIn(mutation_method, sources)
        self.assertIn("页面不计算指标", sources)

    def test_player_accepts_dynamic_read_api_url(self) -> None:
        app = (self.viewer / "app.js").read_text(encoding="utf-8")
        self.assertIn('.get("run_api")', app)
        self.assertIn("fetch(DEFAULT_RUN)", app)


if __name__ == "__main__":
    unittest.main()
