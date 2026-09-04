"""Generic strategy Provider assembled exclusively through plugins."""

from __future__ import annotations

from dataclasses import dataclass, replace

from experiment_system import (
    ComponentSpec,
    ExperimentSpec,
    ProviderRegistry,
    RunSpec,
    ScenarioConfiguration,
)
from market_protocol import MarketSource
from simulation_runtime import SimulationResult, SimulationRunner
from trading_strategies.catalog import build_strategy_definition_registry
from trading_strategies.rules import build_trading_rule_registry

from ..components import (
    AccountRuntime,
    DailyExecutionRuntime,
    build_account_runtime,
    build_execution_runtime,
    build_market_source,
    resolve_account_component,
    resolve_execution_component,
    resolve_market_component,
)

from ..plugins import (
    CoinMLongTakeProfitLadderSimulationPlugin,
    FixedGridSimulationPlugin,
    HoldBtcSimulationPlugin,
    LayeredFollowingGridSimulationPlugin,
    SingleFollowingGridSimulationPlugin,
)
from ..registry import (
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
    SimulationStrategyRegistry,
)


STRATEGIES_SIMULATION_PROVIDER_V1 = "strategies-simulation/v1"
EXPERIMENT_KINDS = frozenset(
    {"BASELINE", "PARAMETER_STUDY", "MARKET_VALIDATION", "ROBUSTNESS"}
)


@dataclass(frozen=True, slots=True)
class StrategyRuntimeComponents:
    source: MarketSource
    binding: SimulationStrategyBinding
    account: AccountRuntime
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
        self.strategy_definitions = build_strategy_definition_registry()

    def component_descriptors(self) -> tuple[dict[str, object], ...]:
        strategy_definition_descriptors = tuple(
            definition.to_document()
            for definition in self.strategy_definitions.definitions
        )
        rule_descriptors = tuple(
            definition.to_document()
            for definition in build_trading_rule_registry().definitions
        )
        return (
            *self.strategies.descriptors,
            *strategy_definition_descriptors,
            *rule_descriptors,
        )

    def validate_experiment_spec(self, spec: ExperimentSpec) -> None:
        """Validate research metadata owned by strategies_system."""

        raw_kind = spec.metadata.get("experiment_kind")
        if raw_kind is None:
            return
        if not isinstance(raw_kind, str) or not raw_kind.strip():
            raise ValueError("metadata.experiment_kind must be a string")
        experiment_kind = raw_kind.strip().upper()
        if experiment_kind not in EXPERIMENT_KINDS:
            raise ValueError(
                "metadata.experiment_kind must be one of "
                + ", ".join(sorted(EXPERIMENT_KINDS))
            )
        if experiment_kind == "PARAMETER_STUDY" and not any(
            group.parameter_axes
            for group in spec.scenario_groups
            if group.run_provider == self.provider_id
        ):
            raise ValueError(
                "PARAMETER_STUDY requires at least one parameter axis"
            )

    def resolve(
        self,
        configuration: ScenarioConfiguration,
    ) -> ScenarioConfiguration:
        strategy = self._resolve_strategy_definition_reference(
            self.strategies.resolve(configuration.strategy)
        )
        return replace(
            configuration,
            market=resolve_market_component(configuration.market),
            strategy=strategy,
            execution=resolve_execution_component(configuration.execution),
            account=resolve_account_component(configuration.account),
        )

    def _resolve_strategy_definition_reference(
        self,
        component: ComponentSpec,
    ) -> ComponentSpec:
        """Validate and canonicalize an optional StrategyDefinition reference."""

        raw_type = component.parameters.get("strategy_definition_type")
        if raw_type is None:
            return component
        if not isinstance(raw_type, str) or not raw_type.strip():
            raise ValueError(
                "strategy_definition_type must be a non-empty string"
            )
        canonical_type = self.strategy_definitions.canonical_type(
            raw_type.strip()
        )
        if canonical_type == raw_type:
            return component
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={
                **dict(component.parameters),
                "strategy_definition_type": canonical_type,
            },
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
            settlement_asset=account.settlement_asset,
            market_type=account.market_type,
        )
        if source.instrument != account.instrument:
            raise ValueError("market and account instruments must match")
        context = SimulationStrategyBuildContext(
            instrument=account.instrument,
            market_type=account.market_type,
            contract_size=account.contract_size,
            settlement_asset=account.settlement_asset,
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
    registry.register(FixedGridSimulationPlugin())
    registry.register(HoldBtcSimulationPlugin())
    registry.register(CoinMLongTakeProfitLadderSimulationPlugin())
    registry.register(SingleFollowingGridSimulationPlugin())
    registry.register(LayeredFollowingGridSimulationPlugin())
    return registry


def build_provider_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(StrategiesSimulationProvider(build_strategy_registry()))
    return registry
