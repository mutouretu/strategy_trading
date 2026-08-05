from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from experiment_system import (
    ExperimentAccessError,
    MarketPathSetCatalog,
    ParquetMarketStore,
    create_read_server,
)
from market_protocol import MarketFrame


def _timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp() * 1000)


def _frames(*, offset: Decimal = Decimal("0")) -> tuple[MarketFrame, ...]:
    values = (
        ("2026-08-01", "100", "110", "90", "105"),
        ("2026-08-03", "105", "120", "100", "115"),
        ("2026-08-10", "115", "130", "108", "125"),
        ("2026-09-01", "125", "140", "120", "135"),
    )
    return tuple(
        MarketFrame(
            sequence=index,
            timestamp=_timestamp(date),
            instrument="BTCUSD_PERP",
            open=Decimal(open_) + offset,
            high=Decimal(high) + offset,
            low=Decimal(low) + offset,
            close=Decimal(close) + offset,
        )
        for index, (date, open_, high, low, close) in enumerate(values)
    )


class MarketPathCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)
        self.environment_root = self.workspace / "market_environments"
        for name in ("manifests", "path_sets", "scenarios"):
            (self.environment_root / name).mkdir(parents=True)
        generated = self.environment_root / "generated" / "probe-set-v1"
        store = ParquetMarketStore(generated)
        train_frames = _frames()
        holdout_frames = _frames(offset=Decimal("50"))
        train = store.persist(train_frames)
        holdout = store.persist(holdout_frames)

        scenario = {
            "schema_version": "market-scenario/v1",
            "scenario_id": "probe-scenario-v1",
            "name": "探针市场",
            "description": "用于验证只读市场路径目录",
            "origin": "SYNTHETIC",
            "instrument": "BTCUSD_PERP",
            "horizon": {"start": "2026-08-01", "end": "2026-09-01"},
            "interval": "1h",
            "model": {"type": "probe-model/v1"},
            "anchors": [],
            "volatility_regimes": [],
            "status": "LOCKED",
            "metadata": {},
        }
        (self.environment_root / "scenarios" / "probe.json").write_text(
            json.dumps(scenario),
            encoding="utf-8",
        )
        path_set = {
            "schema_version": "market-path-set/v1",
            "path_set_id": "probe-set-v1",
            "description": "PathSet 目录探针",
            "asset_profile_path": "../asset_profiles/probe.json",
            "scenarios": [
                {
                    "scenario_id": "probe-scenario-v1",
                    "path": "../scenarios/probe.json",
                }
            ],
            "roles": {
                "TRAIN": [11],
                "VALIDATION": [21],
                "HOLDOUT": [31],
            },
            "status": "LOCKED",
        }
        (
            self.environment_root
            / "path_sets"
            / "probe-set-v1.json"
        ).write_text(json.dumps(path_set), encoding="utf-8")

        def entry(role, seed, reference, frames):
            return {
                "path_key": f"probe-scenario-v1:{role}:{seed}",
                "scenario_id": "probe-scenario-v1",
                "scenario_name": "探针市场",
                "role": role,
                "market_seed": seed,
                "origin": "SYNTHETIC",
                "instrument": "BTCUSD_PERP",
                "interval": "1h",
                "model_type": "probe-model/v1",
                "market_path_id": reference.market_path_id,
                "content_sha256": reference.content_hash,
                "file_sha256": reference.file_sha256,
                "storage_path": reference.storage_path,
                "frame_count": len(frames),
                "first_timestamp": frames[0].timestamp,
                "last_timestamp": frames[-1].timestamp,
                "resolved_anchors": [],
                "resolved_volatility_regimes": [],
                "market_profile": {
                    "initial_price": str(frames[0].open),
                    "final_price": str(frames[-1].close),
                    "minimum_low": str(min(frame.low for frame in frames)),
                    "maximum_high": str(max(frame.high for frame in frames)),
                    "max_drawdown_rate": "0.1",
                    "annualized_realized_volatility": "0.5",
                },
            }

        manifest = {
            "schema_version": "synthetic-market-path-set-manifest/v1",
            "path_set_id": "probe-set-v1",
            "status": "CONTENT_LOCKED",
            "reproducible": True,
            "holdout_materialized": True,
            "holdout_strategy_execution_allowed": False,
            "lock_fingerprint": "f" * 64,
            "paths": [
                entry("TRAIN", 11, train, train_frames),
                entry("HOLDOUT", 31, holdout, holdout_frames),
            ],
        }
        (
            self.environment_root
            / "manifests"
            / "probe-set-v1.json"
        ).write_text(json.dumps(manifest), encoding="utf-8")
        self.train_id = train.market_path_id
        self.holdout_id = holdout.market_path_id

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_catalog_redacts_holdout_and_aggregates_visible_paths(self) -> None:
        catalog = MarketPathSetCatalog(self.environment_root)
        path_sets = catalog.path_sets()
        self.assertEqual(len(path_sets), 1)
        path_set = path_sets[0]
        self.assertEqual(path_set["description"], "PathSet 目录探针")
        scenario = path_set["scenarios"][0]
        self.assertEqual(scenario["name"], "探针市场")
        self.assertEqual(
            scenario["role_counts"],
            {"TRAIN": 1, "VALIDATION": 0, "HOLDOUT": 1},
        )
        train = next(path for path in scenario["paths"] if path["role"] == "TRAIN")
        holdout = next(
            path for path in scenario["paths"] if path["role"] == "HOLDOUT"
        )
        self.assertIn("market_profile", train)
        self.assertNotIn("market_profile", holdout)
        self.assertEqual(holdout["availability"], "LOCKED")

        weekly = catalog.path_document(
            "probe-set-v1",
            self.train_id,
            interval="1w",
        )
        self.assertEqual(weekly["source_frame_count"], 4)
        self.assertEqual(
            [bar["date"] for bar in weekly["market"]],
            ["2026-07-27", "2026-08-03", "2026-08-10", "2026-08-31"],
        )
        monthly = catalog.path_document(
            "probe-set-v1",
            self.train_id,
            interval="1m",
        )
        self.assertEqual(len(monthly["market"]), 2)
        self.assertEqual(monthly["market"][0]["high"], "130")
        self.assertEqual(monthly["market"][0]["close"], "125")

        with self.assertRaises(ExperimentAccessError):
            catalog.path_document(
                "probe-set-v1",
                self.holdout_id,
                interval="1w",
            )

    def test_read_api_lists_path_sets_and_rejects_holdout_prices(self) -> None:
        results = self.workspace / "results"
        results.mkdir()
        viewer_root = Path(__file__).resolve().parents[1] / "viewer"
        server = create_read_server(
            results,
            viewer_root=viewer_root,
            market_environment_root=self.environment_root,
            port=0,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        host, port = server.server_address
        base_url = f"http://{host}:{port}"
        try:
            with urlopen(f"{base_url}/api/market-path-sets", timeout=5) as response:
                document = json.loads(response.read())
            self.assertEqual(document["total"], 1)

            with urlopen(
                f"{base_url}/api/market-path-sets/probe-set-v1/paths/"
                f"{self.train_id}?interval=1m",
                timeout=5,
            ) as response:
                path = json.loads(response.read())
            self.assertEqual(path["aggregation_interval"], "1m")
            self.assertEqual(len(path["market"]), 2)

            with self.assertRaises(HTTPError) as context:
                urlopen(
                    f"{base_url}/api/market-path-sets/probe-set-v1/paths/"
                    f"{self.holdout_id}?interval=1w",
                    timeout=5,
                )
            self.assertEqual(context.exception.code, 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
