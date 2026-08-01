"""Grid-owned Provider that assembles the existing simulation objects."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from experiment_system import (
    ProviderRegistry,
    RunSpec,
    ScenarioConfiguration,
)
from grid_strategies.adapters import (
    LayeredFollowingGridSimulationAdapter,
    SingleFollowingGridSimulationAdapter,
)
from market_simulator import AnchoredGBMMarketSource
from simulation_runtime import SimulationResult, SimulationRunner

from .account_factories import (
    CoinMAccountRuntime,
    build_account_runtime,
    resolve_account_component,
)
from .execution_factories import (
    DailyExecutionRuntime,
    build_execution_runtime,
    resolve_execution_component,
)
from .market_factories import (
    build_market_source,
    resolve_market_component,
)
from .strategy_factories import (
    LAYERED_FOLLOWING_GRID_V1,
    SINGLE_FOLLOWING_GRID_V1,
    GridStrategyAdapter,
    adapter_rule_config,
    build_strategy_adapter,
    resolve_strategy_component,
)


GRID_SIMULATION_PROVIDER_V1 = "grid-simulation/v1"


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class GridRuntimeComponents:
    source: AnchoredGBMMarketSource
    adapter: GridStrategyAdapter
    account: CoinMAccountRuntime
    execution: DailyExecutionRuntime


class PreparedGridRun:
    """One prepared grid-strategy simulation."""

    def __init__(
        self,
        run_spec: RunSpec,
        components: GridRuntimeComponents,
    ) -> None:
        self.run_spec = run_spec
        self.components = components
        self._result: SimulationResult | None = None

    def execute(self) -> SimulationResult:
        if self._result is not None:
            raise RuntimeError("PreparedGridRun can execute only once")
        account = self.components.account
        execution = self.components.execution
        runner = SimulationRunner(
            self.components.source,
            trade_port=self.components.adapter,
            fee_model=execution.fee_model,
            funding_model=execution.funding_model,
            ledger_factory=account.ledger_factory,
            margin_model=account.margin_model,
            mark_price_sampling=account.mark_price_sampling,
        )
        self._result = runner.run(seed=self.run_spec.seed)
        return self._result

    def summarize(
        self,
        result: SimulationResult,
    ) -> dict[str, object]:
        if result is not self._result:
            raise ValueError(
                "summary result must come from this PreparedGridRun"
            )
        adapter = self.components.adapter
        if isinstance(adapter, SingleFollowingGridSimulationAdapter):
            strategy = adapter.strategy
            engine = strategy.engine
            summary: dict[str, object] = {
                "strategy_type": "single_following_grid",
                "strategy_id": strategy.config.strategy_id,
                "grid_id": strategy.config.rule.grid_id,
                "completed_cycles": engine.completed_cycles,
                "cells_added": engine.cells_added,
                "cells_reclaimed": engine.cells_reclaimed,
                "final_cell_count": len(engine.cells),
                "final_cells": [
                    {
                        "cell_id": cell.cell_id,
                        "buy_price": str(cell.buy_price),
                        "sell_price": str(cell.sell_price),
                        "phase": cell.phase.value,
                        "position_quantity": str(
                            cell.position_quantity
                        ),
                        "cycle_count": cell.cycle_count,
                    }
                    for cell in engine.cells
                ],
            }
        elif isinstance(
            adapter,
            LayeredFollowingGridSimulationAdapter,
        ):
            strategy = adapter.strategy
            summary = {
                "strategy_type": "layered_following_grid",
                "strategy_id": strategy.config.strategy_id,
                "grid_id": strategy.config.rule_template.grid_id,
                "completed_cycles": strategy.completed_cycles,
                "cells_added": strategy.cells_added,
                "cells_reclaimed": strategy.cells_reclaimed,
                "layer_count": strategy.layer_count,
                "reset_count": strategy.reset_count,
                "retiring_grid_count": strategy.retiring_grid_count,
                "layers": [
                    {
                        "layer_index": layer.layer_index,
                        "anchor_price": str(layer.anchor_price),
                        "generation": layer.generation,
                        "lower_edge": str(layer.lower_edge),
                        "upper_edge": str(layer.upper_edge),
                        "waiting_for_reentry": (
                            layer.waiting_for_reentry
                        ),
                        "reset_count": layer.reset_count,
                        "completed_cycles": layer.completed_cycles,
                        "position_quantity": str(
                            layer.position_quantity
                        ),
                    }
                    for layer in strategy.layers
                ],
            }
        else:
            raise TypeError(
                f"unsupported strategy adapter {type(adapter).__name__}"
            )
        summary.update(
            {
                "intent_count": len(result.intents),
                "instruction_count": len(result.instructions),
                "fill_count": len(result.fills),
            }
        )
        return summary


class GridSimulationProvider:
    provider_id = GRID_SIMULATION_PROVIDER_V1

    @staticmethod
    def component_descriptors() -> tuple[dict[str, object], ...]:
        return (
            {
                "kind": "strategy",
                "type": SINGLE_FOLLOWING_GRID_V1,
                "display_name": "单组跟随网格",
                "description": (
                    "围绕一个锚点建立等比网格；价格覆盖挂单价时完成被动成交，"
                    "成交后在相邻网格价位安排反向交易，越过当前区间后网格继续跟随。"
                ),
                "flow": [
                    {"title": "建立网格", "detail": "按锚点、间距和网格数生成 Cell"},
                    {"title": "等待覆盖", "detail": "K 线高低价覆盖挂单价"},
                    {"title": "完成换手", "detail": "成交后安排相邻反向意图"},
                    {"title": "跟随移动", "detail": "越界后补充新 Cell 并回收旧 Cell"},
                ],
            },
            {
                "kind": "strategy",
                "type": LAYERED_FOLLOWING_GRID_V1,
                "display_name": "分层跟随网格",
                "description": (
                    "按固定价格步长部署多组跟随网格；每组独立运行，层间发生边界"
                    "碰撞时对下位网格进行复位。"
                ),
                "flow": [
                    {"title": "部署首层", "detail": "在初始锚点建立跟随网格"},
                    {"title": "下跌加层", "detail": "每下移一个部署步长建立新层"},
                    {"title": "独立成交", "detail": "各层分别处理 Entry 与 Exit"},
                    {"title": "碰撞复位", "detail": "下位上沿触及上位下沿时复位"},
                ],
            },
        )

    def resolve(
        self,
        configuration: ScenarioConfiguration,
    ) -> ScenarioConfiguration:
        return replace(
            configuration,
            market=resolve_market_component(
                configuration.market
            ),
            strategy=resolve_strategy_component(
                configuration.strategy
            ),
            execution=resolve_execution_component(
                configuration.execution
            ),
            account=resolve_account_component(
                configuration.account
            ),
        )

    def validate(
        self,
        configuration: ScenarioConfiguration,
    ) -> None:
        self._build_components(configuration)

    def prepare(self, run_spec: RunSpec) -> PreparedGridRun:
        return PreparedGridRun(
            run_spec,
            self._build_components(run_spec.configuration),
        )

    @staticmethod
    def _build_components(
        configuration: ScenarioConfiguration,
    ) -> GridRuntimeComponents:
        source = build_market_source(configuration.market)
        adapter = build_strategy_adapter(configuration.strategy)
        account = build_account_runtime(configuration.account)
        execution = build_execution_runtime(
            configuration.execution,
            contract_size=account.contract_size,
            settlement_asset=account.base_asset,
        )
        rule = adapter_rule_config(adapter)
        instruments = {
            source.instrument,
            rule.instrument,
            account.instrument,
        }
        if len(instruments) != 1:
            raise ValueError(
                "market, strategy and account instruments must match"
            )
        if rule.contract_size != account.contract_size:
            raise ValueError(
                "strategy and account contract_size must match"
            )
        return GridRuntimeComponents(
            source=source,
            adapter=adapter,
            account=account,
            execution=execution,
        )


def build_registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(GridSimulationProvider())
    return registry
