from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from market_simulator.market_environment import (
    ANCHORED_REGIME_BRIDGE_V1,
    AnchorTarget,
    AnchorTargetType,
    AnchoredRegimeBridgeModel,
    AssetProfile,
    DuplicateMarketModelError,
    MarketEnvironmentConfigError,
    MarketModelRegistry,
    MarketModelSpec,
    MarketPathRole,
    MarketScenario,
    RegimeBridgeMarketSource,
    ScenarioAnchor,
    ScenarioOrigin,
    ScenarioStatus,
    VolatilityRegime,
    load_asset_profile,
    load_market_path_set,
    load_market_scenario,
    parse_market_scenario,
    profile_market_path,
)


ROOT = Path(__file__).resolve().parents[1]
ENVIRONMENTS = ROOT / "market_environments"


def asset_profile() -> AssetProfile:
    return AssetProfile(
        profile_id="btc-test/v1",
        name="BTC test",
        calendar="24x7",
        periods_per_year=365,
        price_quantum=Decimal("0.1"),
        default_interval="1h",
    )


def scenario(*, volatility: str = "0.6") -> MarketScenario:
    return MarketScenario(
        scenario_id="test-scenario-v1",
        name="test scenario",
        description="one-day deterministic test scenario",
        origin=ScenarioOrigin.SYNTHETIC,
        asset_profile_id="btc-test/v1",
        instrument="BTCUSD_PERP",
        start=date(2026, 1, 1),
        end=date(2026, 1, 2),
        interval="1h",
        model=MarketModelSpec(
            type=ANCHORED_REGIME_BRIDGE_V1,
            price_quantum=Decimal("0.1"),
            periods_per_year=365,
            substeps_per_bar=6,
        ),
        anchors=(
            ScenarioAnchor(
                date(2026, 1, 1),
                AnchorTarget(AnchorTargetType.HARD, price=Decimal("100")),
            ),
            ScenarioAnchor(
                date(2026, 1, 2),
                AnchorTarget(
                    AnchorTargetType.BAND,
                    minimum=Decimal("75"),
                    maximum=Decimal("85"),
                ),
            ),
        ),
        volatility_regimes=(
            VolatilityRegime(
                date(2026, 1, 1),
                date(2026, 1, 2),
                Decimal(volatility),
                Decimal(volatility),
            ),
        ),
        status=ScenarioStatus.LOCKED,
    )


class MarketEnvironmentSchemaTests(unittest.TestCase):
    def test_locked_catalog_contains_btc_and_eth_path_sets(self) -> None:
        definitions = (
            (
                "btc-three-year-market-baseline-v1.json",
                "btc-24x7/v1",
                "BTCUSD_PERP",
                Decimal("62794.3"),
            ),
            (
                "eth-three-year-market-baseline-v1.json",
                "eth-24x7/v1",
                "ETHUSD_PERP",
                Decimal("1859.83"),
            ),
        )
        all_seeds: set[int] = set()
        for filename, profile_id, instrument, initial_price in definitions:
            path_set_path = ENVIRONMENTS / "path_sets" / filename
            path_set = load_market_path_set(path_set_path)
            profile = load_asset_profile(
                (path_set_path.parent / path_set.asset_profile_path).resolve()
            )
            self.assertEqual(profile.profile_id, profile_id)
            self.assertEqual(len(path_set.scenarios), 6)
            self.assertEqual(path_set.path_count, 96)
            self.assertEqual(len(path_set.role_seeds[MarketPathRole.TRAIN]), 8)
            self.assertEqual(
                len(path_set.role_seeds[MarketPathRole.VALIDATION]),
                4,
            )
            self.assertEqual(
                len(path_set.role_seeds[MarketPathRole.HOLDOUT]),
                4,
            )
            seeds = {
                seed
                for role_seeds in path_set.role_seeds.values()
                for seed in role_seeds
            }
            self.assertTrue(all_seeds.isdisjoint(seeds))
            all_seeds.update(seeds)
            for reference in path_set.scenarios:
                loaded = load_market_scenario(
                    (path_set_path.parent / reference.path).resolve()
                )
                self.assertEqual(loaded.scenario_id, reference.scenario_id)
                self.assertEqual(loaded.asset_profile_id, profile.profile_id)
                self.assertEqual(loaded.instrument, instrument)
                self.assertEqual((loaded.end - loaded.start).days, 1096)
                self.assertEqual(
                    loaded.anchors[0].target.price,
                    initial_price,
                )

        eth_profile = load_asset_profile(
            ENVIRONMENTS / "asset_profiles" / "eth-24x7-v1.json"
        )
        self.assertEqual(eth_profile.metadata["reference_price"], "1859.83")
        self.assertEqual(
            eth_profile.metadata["reference_archive_sha256"],
            "d71f014b80f59b8146c1c3a89934a88c63db1c32aab051850f4be8e6c87c59f5",
        )

    def test_unknown_fields_and_strategy_content_are_rejected(self) -> None:
        document = scenario().to_document()
        document["strategy"] = {"grid_ratio": "0.1"}
        with self.assertRaises(MarketEnvironmentConfigError):
            parse_market_scenario(document)

    def test_path_set_rejects_overlapping_role_seeds(self) -> None:
        path = ENVIRONMENTS / "path_sets" / "btc-three-year-market-baseline-v1.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        document["roles"]["HOLDOUT"][0] = document["roles"]["TRAIN"][0]
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "path-set.json"
            changed.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(MarketEnvironmentConfigError):
                load_market_path_set(changed)


class RegimeBridgeTests(unittest.TestCase):
    def test_same_seed_is_exact_and_other_seed_changes_path(self) -> None:
        model = AnchoredRegimeBridgeModel()
        first = model.generate(scenario(), asset_profile(), seed=42)
        repeated = model.generate(scenario(), asset_profile(), seed=42)
        other = model.generate(scenario(), asset_profile(), seed=43)
        self.assertEqual(first, repeated)
        self.assertNotEqual(first.frames, other.frames)
        self.assertEqual(len(first.frames), 25)
        self.assertEqual(first.frames[0].close, Decimal("100.0"))
        self.assertGreaterEqual(first.frames[-1].close, Decimal("75"))
        self.assertLessEqual(first.frames[-1].close, Decimal("85"))
        self.assertEqual(first.frames[-1].close, first.resolved_anchors[-1].price)

    def test_hourly_ohlc_is_contiguous_and_has_intrabar_range(self) -> None:
        generated = AnchoredRegimeBridgeModel().generate(
            scenario(), asset_profile(), seed=123
        )
        has_wick = False
        for index, frame in enumerate(generated.frames):
            self.assertEqual(frame.sequence, index)
            if index:
                previous = generated.frames[index - 1]
                self.assertEqual(frame.timestamp - previous.timestamp, 3_600_000)
                self.assertEqual(frame.open, previous.close)
            self.assertGreaterEqual(frame.high, max(frame.open, frame.close))
            self.assertLessEqual(frame.low, min(frame.open, frame.close))
            has_wick = has_wick or frame.high > max(frame.open, frame.close)
            has_wick = has_wick or frame.low < min(frame.open, frame.close)
            self.assertEqual(dict(frame.features), {})
        self.assertTrue(has_wick)

    def test_source_replays_and_market_profile_is_strategy_neutral(self) -> None:
        source = RegimeBridgeMarketSource(scenario(), asset_profile())
        first = source.reset(99)
        frames = [first]
        while not source.done:
            frames.append(source.next())
        profile = profile_market_path(tuple(frames), scenario())
        self.assertEqual(profile.frame_count, 25)
        self.assertEqual(profile.initial_price, Decimal("100.0"))
        self.assertEqual(profile.maximum_hard_anchor_deviation, Decimal("0.0"))
        self.assertGreaterEqual(profile.max_drawdown_rate, Decimal("0"))
        self.assertEqual(source.reset(99), first)

    def test_registry_is_explicit_and_rejects_duplicates(self) -> None:
        registry = MarketModelRegistry()
        registry.register(AnchoredRegimeBridgeModel())
        self.assertEqual(registry.model_types, (ANCHORED_REGIME_BRIDGE_V1,))
        with self.assertRaises(DuplicateMarketModelError):
            registry.register(AnchoredRegimeBridgeModel())

    def test_market_environment_core_has_no_strategy_imports(self) -> None:
        package = (
            ROOT
            / "packages"
            / "market_simulator"
            / "src"
            / "market_simulator"
            / "market_environment"
        )
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        )
        self.assertNotIn("trading_strategies", source)
        self.assertNotIn("grid_rule", source)
        self.assertNotIn("strategy_simulation", source)


if __name__ == "__main__":
    unittest.main()
