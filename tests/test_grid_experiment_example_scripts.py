from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from grid_experiments.example_scripts import run_viewer_example


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class GridExperimentExampleScriptTests(unittest.TestCase):
    def test_shared_entry_delegates_to_single_run_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "viewer.json"
            database = root / "result.sqlite3"
            market_root = root / "market"
            with patch(
                "grid_experiments.example_scripts.experiment_main",
                return_value=0,
            ) as generic_main:
                code = run_viewer_example(
                    spec_name="single_following_grid_baseline.json",
                    default_output=Path("unused.json"),
                    argv=[
                        "--output",
                        str(output),
                        "--database",
                        str(database),
                        "--market-root",
                        str(market_root),
                        "--allow-dirty",
                        "--rerun-failed",
                        "--resume-interrupted",
                    ],
                )

        self.assertEqual(code, 0)
        command = generic_main.call_args.args[0]
        self.assertEqual(command[0], "run")
        self.assertTrue(
            command[1].endswith(
                "experiments/single_following_grid_baseline.json"
            )
        )
        self.assertEqual(
            command[command.index("--export-viewer") + 1],
            str(output),
        )
        self.assertEqual(
            command[command.index("--database") + 1],
            str(database),
        )
        self.assertEqual(
            command[command.index("--market-root") + 1],
            str(market_root),
        )
        self.assertIn("--allow-dirty", command)
        self.assertIn("--rerun-failed", command)
        self.assertIn("--resume-interrupted", command)

    def test_public_scripts_are_thin_experiment_wrappers(self) -> None:
        scripts = {
            "run_single_following_grid_simulation.py": (
                "single_following_grid_baseline.json"
            ),
            "run_layered_following_grid_simulation.py": (
                "layered_following_grid_baseline.json"
            ),
        }
        for filename, expected_spec in scripts.items():
            with self.subTest(script=filename):
                source = (
                    PROJECT_ROOT / "scripts" / filename
                ).read_text(encoding="utf-8")
                self.assertIn("run_viewer_example", source)
                self.assertIn(expected_spec, source)
                self.assertNotIn("SimulationRunner", source)
                self.assertNotIn("GridRuleConfig", source)
                self.assertLessEqual(len(source.splitlines()), 30)


if __name__ == "__main__":
    unittest.main()
