"""Explicit plugin registry shared by all strategy simulation hosts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from experiment_system import ComponentSpec
from simulation_runtime import FeeModel, SimulationResult, SimulationTradePort


@dataclass(frozen=True, slots=True)
class SimulationStrategyBuildContext:
    instrument: str
    contract_size: Decimal
    settlement_asset: str
    ledger_factory: Callable[[], object]
    margin_model: object | None
    fee_model: FeeModel
    market_type: str = "coinm"


@dataclass(frozen=True, slots=True)
class SimulationStrategyBinding:
    strategy_type: str
    instrument: str
    trade_port: SimulationTradePort
    summary_reader: Callable[[SimulationResult], Mapping[str, object]]
    descriptor: Mapping[str, object]

    def summarize(self, result: SimulationResult) -> dict[str, object]:
        return dict(self.summary_reader(result))


@runtime_checkable
class SimulationStrategyPlugin(Protocol):
    strategy_type: str

    def descriptor(self) -> Mapping[str, object]: ...

    def resolve(self, component: ComponentSpec) -> ComponentSpec: ...

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding: ...


class SimulationStrategyRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, SimulationStrategyPlugin] = {}

    def register(self, plugin: SimulationStrategyPlugin) -> None:
        strategy_type = getattr(plugin, "strategy_type", None)
        if not isinstance(strategy_type, str) or not strategy_type.strip():
            raise ValueError("strategy plugin requires a non-empty strategy_type")
        for method in ("descriptor", "resolve", "build"):
            if not callable(getattr(plugin, method, None)):
                raise TypeError(f"strategy plugin requires {method}()")
        if strategy_type in self._plugins:
            raise ValueError(
                f"strategy plugin {strategy_type!r} is already registered"
            )
        descriptor = dict(plugin.descriptor())
        if descriptor.get("kind") != "strategy":
            raise ValueError("strategy descriptor kind must be 'strategy'")
        if descriptor.get("type") != strategy_type:
            raise ValueError("strategy descriptor type must match strategy_type")
        if not str(descriptor.get("display_name", "")).strip():
            raise ValueError("strategy descriptor requires display_name")
        self._plugins[strategy_type] = plugin

    def get(self, strategy_type: str) -> SimulationStrategyPlugin:
        try:
            return self._plugins[strategy_type]
        except KeyError as exc:
            raise ValueError(
                f"strategy type {strategy_type!r} is not registered"
            ) from exc

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        resolved = self.get(component.type).resolve(component)
        if resolved.type != component.type:
            raise ValueError("strategy plugin must not change component type")
        return resolved

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        binding = self.get(component.type).build(component, context)
        if binding.strategy_type != component.type:
            raise ValueError("strategy binding type must match component type")
        return binding

    @property
    def descriptors(self) -> tuple[dict[str, object], ...]:
        return tuple(
            dict(self._plugins[key].descriptor())
            for key in sorted(self._plugins)
        )

    @property
    def strategy_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._plugins))
