from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import patch

import strategy_simulation  # noqa: F401 - activates local checkout imports

from experiment_system import ComponentSpec
from strategy_simulation.components import (
    build_account_runtime,
    build_execution_runtime,
    resolve_account_component,
    resolve_execution_component,
)
from market_simulator import FixedBarMarketSource
from simulation_runtime import IntentStatus, SimulationRunner

from strategy_simulation.experiment_provider import build_strategy_registry
from strategy_simulation.plugins import (
    TARGET_LIQUIDATION_LADDER_LONG_V1,
    TargetLiquidationLadderSimulationPlugin,
)
from strategy_simulation.registry import (
    SimulationStrategyBuildContext,
    SimulationStrategyRegistry,
)
from strategy_simulation.cli import main, participating_code_revisions


def account():
    return build_account_runtime(
        resolve_account_component(
            ComponentSpec(
                key="coinm",
                type="coinm-inverse/v1",
                parameters={
                    "instrument": "BTCUSD_PERP",
                    "spot_btc": "0",
                    "futures_wallet_btc": "1.1",
                    "margin_model": "flat-maintenance/v1",
                    "leverage": "5",
                    "maintenance_margin_rate": "0.005",
                    "mark_price_sampling": "ADVERSE_EXTREME",
                },
            )
        )
    )


def execution(account_runtime):
    return build_execution_runtime(
        resolve_execution_component(
            ComponentSpec(
                key="daily",
                type="daily-bar-execution/v1",
                parameters={
                    "maker_fee_rate": "0.0001",
                    "taker_fee_rate": "0.0005",
                    "fee_asset": "BTC",
                },
            )
        ),
        contract_size=account_runtime.contract_size,
        settlement_asset=account_runtime.base_asset,
    )


def component() -> ComponentSpec:
    return ComponentSpec(
        key="ladder",
        type=TARGET_LIQUIDATION_LADDER_LONG_V1,
        parameters={
            "instrument": "BTCUSD_PERP",
            "target_liquidation_price": "20000",
            "take_profit_end_price": "100000",
            "take_profit_count": 3,
        },
    )


class StrategySimulationTests(unittest.TestCase):
    def test_result_server_registers_monorepo_market_environment_root(self):
        with patch(
            "strategy_simulation.cli.experiment_main",
            return_value=0,
        ) as experiment_main:
            self.assertEqual(main(["serve-results", "results"]), 0)
        arguments = experiment_main.call_args.args[0]
        option_index = arguments.index("--market-environment-root")
        self.assertTrue(
            arguments[option_index + 1].endswith(
                "market_simulator/market_environments"
            )
        )

    def test_provenance_uses_one_monorepo_revision(self):
        expected = {"strategy_trading": object()}
        with patch(
            "strategy_simulation.cli.collect_code_revisions",
            return_value=expected,
        ) as collect:
            self.assertIs(participating_code_revisions(), expected)
        repositories = collect.call_args.args[0]
        self.assertEqual(tuple(repositories), ("strategy_trading",))

    @staticmethod
    def binding_and_runtimes():
        account_runtime = account()
        execution_runtime = execution(account_runtime)
        plugin = TargetLiquidationLadderSimulationPlugin()
        binding = plugin.build(
            plugin.resolve(component()),
            SimulationStrategyBuildContext(
                instrument=account_runtime.instrument,
                contract_size=account_runtime.contract_size,
                settlement_asset=account_runtime.base_asset,
                ledger_factory=account_runtime.ledger_factory,
                margin_model=account_runtime.margin_model,
                fee_model=execution_runtime.fee_model,
            ),
        )
        return binding, account_runtime, execution_runtime

    @staticmethod
    def run_frames(frames):
        binding, account_runtime, execution_runtime = (
            StrategySimulationTests.binding_and_runtimes()
        )
        result = SimulationRunner(
            FixedBarMarketSource("BTCUSD_PERP", frames),
            trade_port=binding.trade_port,
            fee_model=execution_runtime.fee_model,
            ledger_factory=account_runtime.ledger_factory,
            margin_model=account_runtime.margin_model,
            mark_price_sampling=account_runtime.mark_price_sampling,
        ).run(seed=42)
        return binding, result

    def test_registry_is_explicit_and_rejects_duplicates(self) -> None:
        registry = build_strategy_registry()
        self.assertEqual(
            set(registry.strategy_types),
            {
                "fixed-grid/v1",
                "hold-btc/v1",
                "layered-following-grid/v1",
                "target-liquidation-ladder-long/v1",
                "single-following-grid/v1",
            },
        )
        with self.assertRaisesRegex(ValueError, "already registered"):
            registry.register(TargetLiquidationLadderSimulationPlugin())
        with self.assertRaisesRegex(ValueError, "not registered"):
            registry.get("rsi/v1")

    def test_position_sizing_and_full_reduce_only_ladder(self) -> None:
        binding, result = self.run_frames(
            [
                ("60000", "61000", "59000", "60000"),
                ("60000", "61000", "59000", "60000"),
                ("68000", "70000", "65000", "69000"),
                ("90000", "110000", "68000", "100000"),
            ]
        )
        summary = binding.summarize(result)
        self.assertGreater(Decimal(summary["entry_contracts"]), Decimal("0"))
        self.assertLessEqual(
            Decimal(summary["estimated_liquidation_price_after_entry"]),
            Decimal("20000"),
        )
        self.assertEqual(summary["completed_take_profit_level_count"], 3)
        self.assertEqual(Decimal(summary["remaining_contracts"]), Decimal("0"))
        self.assertTrue(summary["completed"])
        self.assertEqual(len(result.fills), 4)
        self.assertTrue(all(fill.reduce_only for fill in result.fills[1:]))
        self.assertTrue(all(record.status == IntentStatus.FILLED for record in result.intents))

    def test_partial_and_untouched_exit_paths(self) -> None:
        untouched_binding, untouched = self.run_frames(
            [
                ("60000", "61000", "59000", "60000"),
                ("60000", "61000", "59000", "60000"),
                ("62000", "64000", "60000", "63000"),
            ]
        )
        untouched_summary = untouched_binding.summarize(untouched)
        self.assertEqual(untouched_summary["completed_take_profit_level_count"], 0)
        self.assertEqual(len(untouched.fills), 1)

        partial_binding, partial = self.run_frames(
            [
                ("60000", "61000", "59000", "60000"),
                ("60000", "61000", "59000", "60000"),
                ("68000", "70000", "65000", "69000"),
            ]
        )
        partial_summary = partial_binding.summarize(partial)
        self.assertEqual(partial_summary["completed_take_profit_level_count"], 1)
        self.assertGreater(Decimal(partial_summary["remaining_contracts"]), 0)
        self.assertFalse(partial_summary["completed"])

    def test_runtime_liquidation_remains_platform_fact(self) -> None:
        binding, result = self.run_frames(
            [
                ("60000", "61000", "59000", "60000"),
                ("60000", "61000", "59000", "60000"),
                ("30000", "61000", "19000", "30000"),
            ]
        )
        summary = binding.summarize(result)
        self.assertTrue(result.liquidated)
        self.assertFalse(summary["completed"])
        self.assertEqual(len(result.fills), 1)

    def test_minimum_quantity_failure_is_explicit(self) -> None:
        account_runtime = build_account_runtime(
            resolve_account_component(
                ComponentSpec(
                    key="tiny",
                    type="coinm-inverse/v1",
                    parameters={
                        "instrument": "BTCUSD_PERP",
                        "spot_btc": "0",
                        "futures_wallet_btc": "0.000001",
                        "margin_model": "flat-maintenance/v1",
                        "leverage": "5",
                        "maintenance_margin_rate": "0.005",
                        "mark_price_sampling": "ADVERSE_EXTREME",
                    },
                )
            )
        )
        execution_runtime = execution(account_runtime)
        plugin = TargetLiquidationLadderSimulationPlugin()
        binding = plugin.build(
            plugin.resolve(component()),
            SimulationStrategyBuildContext(
                instrument=account_runtime.instrument,
                contract_size=account_runtime.contract_size,
                settlement_asset=account_runtime.base_asset,
                ledger_factory=account_runtime.ledger_factory,
                margin_model=account_runtime.margin_model,
                fee_model=execution_runtime.fee_model,
            ),
        )
        with self.assertRaisesRegex(ValueError, "one quantity step"):
            SimulationRunner(
                FixedBarMarketSource(
                    "BTCUSD_PERP",
                    [
                        ("60000", "60000", "60000", "60000"),
                        ("60000", "60000", "60000", "60000"),
                    ],
                ),
                trade_port=binding.trade_port,
                fee_model=execution_runtime.fee_model,
                ledger_factory=account_runtime.ledger_factory,
                margin_model=account_runtime.margin_model,
            ).run()


if __name__ == "__main__":
    unittest.main()
