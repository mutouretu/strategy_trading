from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from examples.deterministic_probe import run_probe
from experiment_system import (
    CodeRevision,
    ComponentSpec,
    ExperimentPlan,
    ProviderRegistry,
    RunSpec,
    ScenarioConfiguration,
    parse_experiment_spec,
    plan_experiment,
)
from simulation_runtime import SimulationResult


def experiment_document() -> dict[str, object]:
    return {
        "schema_version": "experiment-spec/v1",
        "experiment_id": "grid-research",
        "description": "2A deterministic expansion fixture",
        "scenario_groups": [
            {
                "key": "coinm-grid",
                "run_provider": "test-simulation/v1",
                "markets": [
                    {
                        "key": "market-a",
                        "type": "fixed-market/v1",
                        "parameters": {
                            "instrument": "BTCUSD_PERP",
                            "anchors": [["2026-01-01", "60000"]],
                        },
                    },
                    {
                        "key": "market-b",
                        "type": "gbm-market/v1",
                        "parameters": {
                            "instrument": "BTCUSD_PERP",
                            "annual_volatility": "0.55",
                        },
                    },
                ],
                "strategies": [
                    {
                        "key": "strategy-a",
                        "type": "following-grid/v1",
                        "parameters": {
                            "instrument": "BTCUSD_PERP",
                            "order_quantity": "1",
                        },
                    }
                ],
                "executions": [
                    {
                        "key": "daily-passive",
                        "type": "daily-execution/v1",
                        "parameters": {"maker_fee_rate": "0.0002"},
                    }
                ],
                "accounts": [
                    {
                        "key": "coinm-three-x",
                        "type": "coinm-account/v1",
                        "parameters": {"leverage": "3"},
                    }
                ],
                "parameter_axes": [
                    {
                        "path": "/strategy/parameters/order_quantity",
                        "values": ["1", "2"],
                    }
                ],
            },
            {
                "key": "linear-probe",
                "run_provider": "test-simulation/v1",
                "markets": [
                    {
                        "key": "market-c",
                        "type": "fixed-market/v1",
                        "parameters": {"instrument": "ETHUSDT"},
                    }
                ],
                "strategies": [
                    {
                        "key": "strategy-b",
                        "type": "rsi/v1",
                        "parameters": {"instrument": "ETHUSDT"},
                    }
                ],
                "executions": [
                    {
                        "key": "daily-active",
                        "type": "daily-execution/v1",
                        "parameters": {"taker_fee_rate": "0.0005"},
                    }
                ],
                "accounts": [
                    {
                        "key": "linear-account",
                        "type": "linear-account/v1",
                        "parameters": {"leverage": "1"},
                    }
                ],
            },
        ],
        "seeds": [42, 43],
        "output": {
            "root": "experiment_results",
            "default_retention_class": "standard",
        },
        "controls": {
            "max_runs": 100,
            "continue_on_error": True,
        },
        "metadata": {"purpose": "unit-test"},
    }


class TestProvider:
    provider_id = "test-simulation/v1"

    def __init__(self) -> None:
        self.prepare_calls = 0

    def resolve(
        self,
        configuration: ScenarioConfiguration,
    ) -> ScenarioConfiguration:
        parameters = dict(configuration.execution.parameters)
        parameters.setdefault("resolved_default", "yes")
        return replace(
            configuration,
            execution=ComponentSpec(
                key=configuration.execution.key,
                type=configuration.execution.type,
                parameters=parameters,
            ),
        )

    def validate(self, configuration: ScenarioConfiguration) -> None:
        market_instrument = configuration.market.parameters.get("instrument")
        strategy_instrument = configuration.strategy.parameters.get(
            "instrument"
        )
        if market_instrument != strategy_instrument:
            raise ValueError("market and strategy instruments must match")

    def prepare(self, run_spec):
        self.prepare_calls += 1
        raise AssertionError("prepare() must not be called during 2A planning")


def registry_with_test_provider() -> tuple[ProviderRegistry, TestProvider]:
    registry = ProviderRegistry()
    provider = TestProvider()
    registry.register(provider)
    return registry, provider


def single_experiment_document() -> dict[str, object]:
    document = experiment_document()
    document["experiment_id"] = "single-probe"
    document["description"] = "2B deterministic single-Run fixture"
    group = document["scenario_groups"][0]
    group["markets"] = [group["markets"][0]]
    group["strategies"] = [group["strategies"][0]]
    group["parameter_axes"] = []
    document["scenario_groups"] = [group]
    document["seeds"] = [42]
    return document


class PreparedProbeRun:
    def __init__(
        self,
        *,
        fail_on_execute: bool = False,
        failure_message: str = "deterministic provider failure",
    ) -> None:
        self.fail_on_execute = fail_on_execute
        self.failure_message = failure_message

    def execute(self) -> SimulationResult:
        if self.fail_on_execute:
            raise RuntimeError(self.failure_message)
        return run_probe()

    def summarize(
        self,
        result: SimulationResult,
    ) -> dict[str, object]:
        return {
            "fill_count": len(result.fills),
            "completed": result.completed,
            "final_equity": result.final_equity,
        }


class ExecutableTestProvider(TestProvider):
    def __init__(
        self,
        *,
        fail_on_execute: bool = False,
        fail_on_seeds: set[int] | None = None,
        failure_message: str = "deterministic provider failure",
    ) -> None:
        super().__init__()
        self.fail_on_execute = fail_on_execute
        self.fail_on_seeds = frozenset(fail_on_seeds or ())
        self.failure_message = failure_message

    def prepare(self, run_spec: RunSpec) -> PreparedProbeRun:
        self.prepare_calls += 1
        return PreparedProbeRun(
            fail_on_execute=(
                self.fail_on_execute
                or run_spec.seed in self.fail_on_seeds
            ),
            failure_message=self.failure_message,
        )


def executable_registry(
    *,
    fail_on_execute: bool = False,
    fail_on_seeds: set[int] | None = None,
    failure_message: str = "deterministic provider failure",
) -> tuple[ProviderRegistry, ExecutableTestProvider]:
    registry = ProviderRegistry()
    provider = ExecutableTestProvider(
        fail_on_execute=fail_on_execute,
        fail_on_seeds=fail_on_seeds,
        failure_message=failure_message,
    )
    registry.register(provider)
    return registry, provider


def single_run_plan(
    *,
    registry: ProviderRegistry | None = None,
    code_revisions: Mapping[str, CodeRevision] | None = None,
) -> ExperimentPlan:
    providers = registry or executable_registry()[0]
    return plan_experiment(
        parse_experiment_spec(single_experiment_document()),
        providers,
        code_revisions=code_revisions
        or {
            "market_simulator": CodeRevision(commit="a" * 40),
        },
    )
