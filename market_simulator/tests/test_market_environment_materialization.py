from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from experiment_system import CodeRevision


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "materialize_market_path_set.py"
SPEC = importlib.util.spec_from_file_location("materialize_market_path_set", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MarketPathMaterializationTests(unittest.TestCase):
    def test_materialization_is_content_locked_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = root / "profile.json"
            scenario = root / "scenario.json"
            path_set = root / "path-set.json"
            output = root / "generated"
            manifest = root / "manifest.json"
            profile.write_text(
                json.dumps(
                    {
                        "schema_version": "asset-profile/v1",
                        "profile_id": "btc-test/v1",
                        "name": "BTC test",
                        "calendar": "24x7",
                        "periods_per_year": 365,
                        "price_quantum": "0.1",
                        "default_interval": "1h",
                        "metadata": {},
                    }
                ),
                encoding="utf-8",
            )
            scenario.write_text(
                json.dumps(
                    {
                        "schema_version": "market-scenario/v1",
                        "scenario_id": "short-v1",
                        "name": "short",
                        "description": "short materialization probe",
                        "origin": "SYNTHETIC",
                        "asset_profile_id": "btc-test/v1",
                        "instrument": "BTCUSD_PERP",
                        "horizon": {"start": "2026-01-01", "end": "2026-01-02"},
                        "interval": "1h",
                        "model": {
                            "type": "anchored-regime-bridge/v1",
                            "price_quantum": "0.1",
                            "periods_per_year": 365,
                            "substeps_per_bar": 3,
                        },
                        "anchors": [
                            {
                                "date": "2026-01-01",
                                "target": {"type": "HARD", "price": "100"},
                            },
                            {
                                "date": "2026-01-02",
                                "target": {
                                    "type": "BAND",
                                    "minimum": "80",
                                    "maximum": "120",
                                },
                            },
                        ],
                        "volatility_regimes": [
                            {
                                "start": "2026-01-01",
                                "end_exclusive": "2026-01-02",
                                "annual_volatility": {
                                    "minimum": "0.3",
                                    "maximum": "0.5",
                                },
                            }
                        ],
                        "status": "LOCKED",
                        "metadata": {},
                    }
                ),
                encoding="utf-8",
            )
            path_set.write_text(
                json.dumps(
                    {
                        "schema_version": "market-path-set/v1",
                        "path_set_id": "short-set-v1",
                        "description": "short materialization probe",
                        "asset_profile_path": "profile.json",
                        "scenarios": [
                            {"scenario_id": "short-v1", "path": "scenario.json"}
                        ],
                        "roles": {
                            "TRAIN": [1],
                            "VALIDATION": [2],
                            "HOLDOUT": [3],
                        },
                        "status": "LOCKED",
                    }
                ),
                encoding="utf-8",
            )

            first, created = MODULE.materialize_path_set(
                path_set,
                output_root=output,
                manifest_path=manifest,
                progress=None,
            )
            repeated, repeated_created = MODULE.materialize_path_set(
                path_set,
                output_root=output,
                manifest_path=manifest,
                progress=None,
            )
            self.assertTrue(created)
            self.assertFalse(repeated_created)
            self.assertEqual(first, repeated)
            self.assertEqual(first["path_count"], 3)
            self.assertEqual(first["scenario_count"], 1)
            self.assertTrue(first["holdout_materialized"])
            self.assertFalse(first["holdout_strategy_execution_allowed"])
            self.assertEqual(len(first["paths"]), 3)
            self.assertEqual(len(list(output.glob("*.parquet"))), 3)
            self.assertTrue(all(item["frame_count"] == 25 for item in first["paths"]))

            clean_revision = CodeRevision(commit="a" * 40)
            with patch.object(
                MODULE,
                "collect_git_revision",
                return_value=clean_revision,
            ):
                refreshed, refresh_created = MODULE.materialize_path_set(
                    path_set,
                    output_root=output,
                    manifest_path=manifest,
                    refresh_lock=True,
                    progress=None,
                )
            self.assertTrue(refresh_created)
            self.assertEqual(
                refreshed["lock_fingerprint"], first["lock_fingerprint"]
            )
            self.assertTrue(refreshed["reproducible"])
            self.assertEqual(refreshed["code_revision"]["commit"], "a" * 40)


if __name__ == "__main__":
    unittest.main()
