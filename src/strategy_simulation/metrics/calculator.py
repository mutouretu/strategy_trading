"""Metrics that explain the target-liquidation ladder's own progress."""

from __future__ import annotations

from decimal import Decimal

from metric_system import (
    AdverseDirection,
    MetricDefinition,
    MetricInput,
    MetricInputLevel,
    MetricSet,
    MetricValue,
    MetricValueStatus,
    MetricValueType,
    decimal_value,
)

from ..plugins import TARGET_LIQUIDATION_LADDER_LONG_V1


def definition(
    key: str,
    name: str,
    value_type: MetricValueType,
    unit_kind: str,
    *,
    adverse: AdverseDirection = AdverseDirection.NONE,
) -> MetricDefinition:
    return MetricDefinition(
        metric_key=key,
        display_name=name,
        category="strategy",
        description=name,
        value_type=value_type,
        unit_kind=unit_kind,
        required_input_level=MetricInputLevel.SUMMARY,
        adverse_direction=adverse,
    )


BTC_ACCUMULATION_METRIC_SET = MetricSet(
    metric_set_id="btc-accumulation",
    version="v1",
    description="BTC 建仓和阶梯退出策略行为指标",
    definitions=(
        definition("strategy.entry_contracts", "建仓合约数", MetricValueType.DECIMAL, "contracts", adverse=AdverseDirection.HIGHER),
        definition("strategy.estimated_liquidation_price_after_entry", "建仓后预计强平价", MetricValueType.DECIMAL, "price", adverse=AdverseDirection.HIGHER),
        definition("strategy.liquidation_target_deviation_rate", "强平价目标偏差率", MetricValueType.DECIMAL, "ratio", adverse=AdverseDirection.HIGHER),
        definition("strategy.take_profit_level_count", "止盈档位总数", MetricValueType.INTEGER, "count"),
        definition("strategy.completed_take_profit_level_count", "已完成止盈档位数", MetricValueType.INTEGER, "count"),
        definition("strategy.take_profit_completion_rate", "止盈完成率", MetricValueType.DECIMAL, "ratio", adverse=AdverseDirection.LOWER),
        definition("strategy.exited_contracts", "已退出合约数", MetricValueType.DECIMAL, "contracts"),
        definition("strategy.remaining_contracts", "剩余合约数", MetricValueType.DECIMAL, "contracts", adverse=AdverseDirection.HIGHER),
        definition("strategy.completed", "策略已完成", MetricValueType.BOOLEAN, "boolean", adverse=AdverseDirection.LOWER),
    ),
)


class BtcAccumulationMetricCalculator:
    metric_set = BTC_ACCUMULATION_METRIC_SET

    def __init__(self) -> None:
        self.definitions = {
            item.metric_key: item for item in self.metric_set.definitions
        }

    def calculate(self, metric_input: MetricInput) -> tuple[MetricValue, ...]:
        summary = metric_input.provider_summary
        if summary.get("strategy_type") != TARGET_LIQUIDATION_LADDER_LONG_V1:
            return tuple(
                self._unavailable(item, "NOT_APPLICABLE")
                for item in self.metric_set.definitions
            )
        total = int(summary["take_profit_level_count"])
        completed = int(summary["completed_take_profit_level_count"])
        values = (
            self._decimal("strategy.entry_contracts", summary["entry_contracts"]),
            self._optional_decimal(
                "strategy.estimated_liquidation_price_after_entry",
                summary.get("estimated_liquidation_price_after_entry"),
                "ENTRY_NOT_FILLED",
            ),
            self._optional_decimal(
                "strategy.liquidation_target_deviation_rate",
                summary.get("liquidation_target_deviation_rate"),
                "ENTRY_NOT_FILLED",
            ),
            self._integer("strategy.take_profit_level_count", total),
            self._integer("strategy.completed_take_profit_level_count", completed),
            (
                self._decimal(
                    "strategy.take_profit_completion_rate",
                    Decimal(completed) / Decimal(total),
                )
                if total > 0
                else self._unavailable(
                    self.definitions["strategy.take_profit_completion_rate"],
                    "ENTRY_NOT_FILLED",
                )
            ),
            self._decimal("strategy.exited_contracts", summary["exited_contracts"]),
            self._decimal("strategy.remaining_contracts", summary["remaining_contracts"]),
            self._boolean("strategy.completed", bool(summary["completed"])),
        )
        return values

    def _decimal(self, key: str, value: object) -> MetricValue:
        definition_value = self.definitions[key]
        return self._available(
            definition_value,
            decimal_value(value, name=key),
        )

    def _optional_decimal(
        self, key: str, value: object, reason: str
    ) -> MetricValue:
        definition_value = self.definitions[key]
        if value is None:
            return self._unavailable(definition_value, reason)
        return self._decimal(key, value)

    def _integer(self, key: str, value: int) -> MetricValue:
        return self._available(self.definitions[key], value)

    def _boolean(self, key: str, value: bool) -> MetricValue:
        return self._available(self.definitions[key], value)

    @staticmethod
    def _available(
        definition_value: MetricDefinition,
        value: Decimal | int | bool,
    ) -> MetricValue:
        return MetricValue(
            metric_key=definition_value.metric_key,
            value_type=definition_value.value_type,
            unit=definition_value.unit_kind,
            source_level=MetricInputLevel.SUMMARY,
            status=MetricValueStatus.AVAILABLE,
            value=value,
        )

    @staticmethod
    def _unavailable(
        definition_value: MetricDefinition,
        reason: str,
    ) -> MetricValue:
        return MetricValue(
            metric_key=definition_value.metric_key,
            value_type=definition_value.value_type,
            unit=definition_value.unit_kind,
            source_level=MetricInputLevel.SUMMARY,
            status=MetricValueStatus.UNAVAILABLE,
            reason_code=reason,
        )
