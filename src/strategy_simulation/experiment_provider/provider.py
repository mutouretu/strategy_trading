"""Generic strategy Provider assembled exclusively through plugins."""

from __future__ import annotations

from dataclasses import dataclass, replace

from experiment_system import ProviderRegistry, RunSpec, ScenarioConfiguration
from grid_experiments.account_factories import (
    CoinMAccountRuntime,
    build_account_runtime,
    resolve_account_component,
)
from grid_experiments.execution_factories import (
    DailyExecutionRuntime,
    build_execution_runtime,
    resolve_execution_component,
)
from grid_experiments.market_factories import (
    build_market_source,
    resolve_market_component,
)
from market_simulator import AnchoredGBMMarketSource
from simulation_runtime import SimulationResult, SimulationRunner

from ..plugins import (
    HoldBtcSimulationPlugin,
    SingleFollowingGridBridgePlugin,
    TargetLiquidationLadderSimulationPlugin,
)
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
    SimulationStrategyRegistry,
)


STRATEGIES_SIMULATION_PROVIDER_V1 = "strategies-simulation/v1"


@dataclass(frozen=True, slots=True)
class StrategyRuntimeComponents:
    source: AnchoredGBMMarketSource
    binding: SimulationStrategyBinding
    account: CoinMAccountRuntime
    execution: DailyExecutionRuntime


class PreparedStrategyRun:
    def __init__(
        self,
        run_spec: RunSpec,
        components: StrategyRuntimeComponents,
    ) -> None:
        self.run_spec = run_spec
        self.components = components
        self._result: SimulationResult | None = None

    def execute(self) -> SimulationResult:
        if self._result is not None:
            raise RuntimeError("PreparedStrategyRun can execute only once")
        account = self.components.account
        execution = self.components.execution
        runner = SimulationRunner(
            self.components.source,
            trade_port=self.components.binding.trade_port,
            fee_model=execution.fee_model,
            funding_model=execution.funding_model,
            ledger_factory=account.ledger_factory,
            margin_model=account.margin_model,
            mark_price_sampling=account.mark_price_sampling,
        )
        self._result = runner.run(seed=self.run_spec.seed)
        return self._result

    def summarize(self, result: SimulationResult) -> dict[str, object]:
        if result is not self._result:
            raise ValueError("summary result must come from this prepared run")
        return self.components.binding.summarize(result)


class StrategiesSimulationProvider:
    provider_id = STRATEGIES_SIMULATION_PROVIDER_V1

    def __init__(self, strategies: SimulationStrategyRegistry) -> None:
        self.strategies = strategies

    def component_descriptors(self) -> tuple[dict[str, object], ...]:
        return self.strategies.descriptors

    def resolve(
        self,
        configuration: ScenarioConfiguration,
    ) -> ScenarioConfiguration:
        return replace(
            configuration,
            market=resolve_market_component(configuration.market),
            strategy=self.strategies.resolve(configuration.strategy),
            execution=resolve_execution_component(configuration.execution),
            account=resolve_account_component(configuration.account),
        )

    def validate(self, configuration: ScenarioConfiguration) -> None:
        self._build_components(configuration)

    def prepare(self, run_spec: RunSpec) -> PreparedStrategyRun:
        return PreparedStrategyRun(
            run_spec,
            self._build_components(run_spec.configuration),
        )

    def _build_components(
        self,
        configuration: ScenarioConfiguration,
    ) -> StrategyRuntimeComponents:
        source = build_market_source(configuration.market)
        account = build_account_runtime(configuration.account)
        execution = build_execution_runtime(
            configuration.execution,
            contract_size=account.contract_size,
            settlement_asset=account.base_asset,
        )
        if source.instrument != account.instrument:
            raise ValueError("market and account instruments must match")
        context = SimulationStrategyBuildContext(
            instrument=account.instrument,
            contract_size=account.contract_size,
            settlement_asset=account.base_asset,
            ledger_factory=account.ledger_factory,
            margin_model=account.margin_model,
            fee_model=execution.fee_model,
        )
        binding = self.strategies.build(configuration.strategy, context)
        if binding.instrument != source.instrument:
            raise ValueError("market, strategy and account instruments must match")
        return StrategyRuntimeComponents(
            source=source,
            binding=binding,
            account=account,
            execution=execution,
        )


def build_strategy_registry() -> SimulationStrategyRegistry:
    registry = SimulationStrategyRegistry()
    registry.register(HoldBtcSimulationPlugin())
    registry.register(TargetLiquidationLadderSimulationPlugin())
    registry.register(SingleFollowingGridBridgePlugin())
    return registry


def build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(StrategiesSimulationProvider(build_strategy_registry()))
    return registry
