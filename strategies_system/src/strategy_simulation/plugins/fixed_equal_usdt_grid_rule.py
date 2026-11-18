"""Experiment plugin for the composed fixed equal-USDT grid Strategy."""

from __future__ import annotations

from decimal import Decimal

from experiment_system import ComponentSpec
from strategy_application import (
    StrategyApplication,
    StrategyApplicationSpec,
    StrategyLaunchSpec,
    StrategyRuntimeBinding,
)
from trading_strategies.fixed_equal_usdt_grid_strategy import (
    FIXED_EQUAL_USDT_GRID_STRATEGY_V1,
    GRID_RULE_KEY,
    SEED_RULE_KEY,
    FixedEqualUsdtGridStrategyDefinition,
    FixedEqualUsdtGridStrategyParameters,
)
from trading_strategies.rules import (
    FixedEqualUsdtGridRule,
    InitialEntryRule,
    InitialEntryRuleContext,
)
from trading_strategies.grid_core.fixed_equal_usdt import (
    FixedEntryMode, build_fixed_cells, fixed_grid_ratio,
)

from ..adapters.fixed_grid_event_router import FixedGridEventRouter, FixedGridSeedSizer
from ..adapters.strategy_application import StrategyApplicationSimulationAdapter
from ..registry import SimulationStrategyBinding, SimulationStrategyBuildContext
from ._values import check_fields, decimal, integer, text


FIXED_EQUAL_USDT_GRID_SIMULATION_V1 = "fixed-equal-usdt-grid-simulation/v1"
_REQUIRED = {
    "strategy_definition_type", "strategy_id", "instrument",
    "lower_price", "upper_price", "grid_count", "grid_budget_usdt",
    "tick_size", "step_size", "min_qty", "min_notional", "entry_mode",
    "orders_per_side", "rebalance_below", "rebalance_above",
}
_DEFAULTS = {
    "entry_mode": FixedEntryMode.SEEDED.value,
    "orders_per_side": 10,
    "rebalance_below": 5,
    "rebalance_above": 15,
}


class FixedEqualUsdtGridSimulationPlugin:
    strategy_type = FIXED_EQUAL_USDT_GRID_SIMULATION_V1

    def descriptor(self) -> dict[str, object]:
        return {
            "kind": "strategy",
            "type": self.strategy_type,
            "display_name": "固定区间等 U 网格 · Rule 仿真",
            "family": "网格",
            "version": "v1",
            "description": (
                "把固定网格 Strategy 中的初始建仓 Rule 和网格 Rule 接入通用仿真适配器。"
                "按分钟 K 线生成成交事实，再交还 Rule 决定下一批挂单。"
            ),
            "formulae": [
                "每格预算 = 总预算 / 网格数",
                "单格价格比 = (上限 / 下限)^(1 / 网格数) - 1",
                "期末总盈亏 = 已实现盈亏 + 未实现盈亏（零费用情景）",
            ],
            "parameters": [
                {"key": "strategy_definition_type", "name": "策略定义", "required": True},
                {"key": "lower_price", "name": "下限", "required": True},
                {"key": "upper_price", "name": "上限", "required": True},
                {"key": "grid_count", "name": "单元格数量", "required": True},
                {"key": "grid_budget_usdt", "name": "总预算 U", "required": True},
                {"key": "entry_mode", "name": "启动模式", "required": True},
            ],
            "rule_composition": [
                {"rule_key": SEED_RULE_KEY, "rule_type": InitialEntryRule.rule_type},
                {"rule_key": GRID_RULE_KEY, "rule_type": FixedEqualUsdtGridRule.rule_type},
            ],
        }

    def resolve(self, component: ComponentSpec) -> ComponentSpec:
        return ComponentSpec(
            key=component.key,
            type=component.type,
            parameters={**_DEFAULTS, **dict(component.parameters)},
        )

    def build(
        self,
        component: ComponentSpec,
        context: SimulationStrategyBuildContext,
    ) -> SimulationStrategyBinding:
        raw = component.parameters
        check_fields(raw, _REQUIRED, context=self.strategy_type)
        declared = text(raw, "strategy_definition_type", context=self.strategy_type)
        if declared != FIXED_EQUAL_USDT_GRID_STRATEGY_V1:
            raise ValueError("fixed grid simulation requires its Grid StrategyDefinition")
        if context.market_type != "usdm" or context.settlement_asset != "USDT":
            raise ValueError("fixed equal-USDT grid simulation requires USD-M/USDT")
        parameters = FixedEqualUsdtGridStrategyParameters(
            strategy_id=text(raw, "strategy_id", context=self.strategy_type),
            instrument=text(raw, "instrument", context=self.strategy_type),
            lower_price=decimal(raw, "lower_price", context=self.strategy_type),
            upper_price=decimal(raw, "upper_price", context=self.strategy_type),
            grid_count=integer(raw, "grid_count", context=self.strategy_type),
            grid_budget_usdt=decimal(raw, "grid_budget_usdt", context=self.strategy_type),
            tick_size=decimal(raw, "tick_size", context=self.strategy_type),
            step_size=decimal(raw, "step_size", context=self.strategy_type),
            min_qty=decimal(raw, "min_qty", context=self.strategy_type),
            min_notional=decimal(raw, "min_notional", context=self.strategy_type),
            entry_mode=FixedEntryMode(
                text(raw, "entry_mode", context=self.strategy_type)
            ),
            orders_per_side=integer(raw, "orders_per_side", context=self.strategy_type),
            rebalance_below=integer(raw, "rebalance_below", context=self.strategy_type),
            rebalance_above=integer(raw, "rebalance_above", context=self.strategy_type),
        )
        if parameters.instrument != context.instrument:
            raise ValueError("grid strategy and market/account instrument must match")
        account = context.ledger_factory()
        if account.initial_equity < parameters.grid_budget_usdt:
            raise ValueError("account equity is below fixed grid budget")
        spec = FixedEqualUsdtGridStrategyDefinition().bind(parameters)
        seed_sizer = FixedGridSeedSizer(
            parameters,
            build_fixed_cells(spec.slot(GRID_RULE_KEY).rule.config.spec),
        )
        application = StrategyApplication.build(
            StrategyApplicationSpec(
                application_id=f"{parameters.strategy_id}:application",
                strategy_launches=(StrategyLaunchSpec(
                    strategy_instance_id=parameters.strategy_id,
                    strategy_spec=spec,
                ),),
            ),
            lambda launch: StrategyRuntimeBinding(
                rules={
                    SEED_RULE_KEY: InitialEntryRule(),
                    GRID_RULE_KEY: FixedEqualUsdtGridRule(),
                },
                contexts={
                    SEED_RULE_KEY: InitialEntryRuleContext(seed_sizer),
                    GRID_RULE_KEY: None,
                },
            ),
        )
        router = FixedGridEventRouter(parameters, seed_sizer)
        adapter = StrategyApplicationSimulationAdapter(
            application,
            event_router=router,
            contract_size=Decimal("1"),
            settlement_asset="USDT",
            external_strategy_type=self.strategy_type,
            active_intent_at_open=True,
        )

        def summary(result) -> dict[str, object]:
            mark = result.frames[-1].close
            unrealized = router.final_unrealized(mark)
            accounted = router.grid_profit + router.seed_exit_profit + unrealized
            total = result.final_equity - result.initial_equity
            return {
                "strategy_type": self.strategy_type,
                "strategy_definition_type": spec.strategy_type,
                "strategy_id": parameters.strategy_id,
                "primary_rule_key": GRID_RULE_KEY,
                "primary_rule_type": FixedEqualUsdtGridRule.rule_type,
                "grid_count": parameters.grid_count,
                "grid_ratio": str(fixed_grid_ratio(
                    parameters.lower_price, parameters.upper_price,
                    parameters.grid_count,
                )),
                "cell_budget_usdt": str(
                    parameters.grid_budget_usdt / parameters.grid_count
                ),
                "seed_quantity": str(router.seed_quantity),
                "seed_notional": str(router.seed_notional),
                "seed_exit_profit": str(router.seed_exit_profit),
                "seed_exit_count": router.seed_exits,
                "paired_grid_profit": str(router.grid_profit),
                "paired_grid_count": router.grid_round_trips,
                "cell_unrealized_pnl": str(unrealized),
                "total_pnl": str(total),
                "pnl_reconciliation_difference": str(total - accounted),
                "peak_position_notional": str(router.peak_position_notional),
                "final_position_quantity": str(result.final_positions.get(
                    parameters.instrument, Decimal("0")
                )),
                "final_mark": str(mark),
                "application": adapter.application_document(result),
            }

        return SimulationStrategyBinding(
            strategy_type=self.strategy_type,
            instrument=parameters.instrument,
            trade_port=adapter,
            summary_reader=summary,
            descriptor=self.descriptor(),
        )
