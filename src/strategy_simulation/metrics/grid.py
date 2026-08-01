"""Grid-strategy behavior and completed-cycle metrics."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Mapping

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


def definition(
    key: str,
    name: str,
    value_type: MetricValueType,
    level: MetricInputLevel,
    *,
    dimensions: tuple[str, ...] = (),
    unit_kind: str = "count",
    adverse: AdverseDirection = AdverseDirection.NONE,
) -> MetricDefinition:
    return MetricDefinition(
        metric_key=key,
        display_name=name,
        category="grid",
        description=name,
        value_type=value_type,
        unit_kind=unit_kind,
        required_input_level=level,
        dimensions=dimensions,
        adverse_direction=adverse,
    )


GRID_METRIC_SET = MetricSet(
    metric_set_id="grid",
    version="v1",
    description="跟随网格行为和完整循环解释指标",
    definitions=(
        definition("grid.strategy_type", "网格策略类型", MetricValueType.TEXT, MetricInputLevel.SUMMARY, unit_kind="enum"),
        definition("grid.completed_cycles", "完成循环数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY),
        definition("grid.cells_added", "新增 cell 数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY),
        definition("grid.cells_reclaimed", "回收 cell 数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY),
        definition("grid.final_cell_count", "最终 cell 数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY),
        definition("grid.layer_count", "最终 layer 数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY),
        definition("grid.reset_count", "复位次数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY, adverse=AdverseDirection.HIGHER),
        definition("grid.retiring_grid_count", "退役中网格数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY, adverse=AdverseDirection.HIGHER),
        definition("grid.layer.completed_cycles", "每层完成循环数", MetricValueType.INTEGER, MetricInputLevel.SUMMARY, dimensions=("layer_index", "generation")),
        definition("grid.layer.final_position", "每层最终仓位", MetricValueType.DECIMAL, MetricInputLevel.SUMMARY, dimensions=("layer_index", "generation", "quantity_unit"), unit_kind="quantity", adverse=AdverseDirection.HIGHER),
        definition("grid.fill_count_by_role", "ENTRY/EXIT 成交数", MetricValueType.INTEGER, MetricInputLevel.TRACE, dimensions=("role",)),
        definition("grid.fill_count_by_generation", "每代网格成交数", MetricValueType.INTEGER, MetricInputLevel.TRACE, dimensions=("layer_index", "generation")),
        definition("grid.completed_cycle_count_from_trace", "Trace 完整循环数", MetricValueType.INTEGER, MetricInputLevel.TRACE),
        definition("grid.incomplete_entry_count", "期末未完成 ENTRY 数", MetricValueType.INTEGER, MetricInputLevel.TRACE, adverse=AdverseDirection.HIGHER),
        definition("grid.average_net_pnl_per_completed_cycle", "每完成循环平均净收益", MetricValueType.DECIMAL, MetricInputLevel.TRACE, dimensions=("pnl_asset",), unit_kind="asset", adverse=AdverseDirection.LOWER),
        definition("grid.average_fee_per_completed_cycle", "每完成循环平均手续费", MetricValueType.DECIMAL, MetricInputLevel.TRACE, dimensions=("fee_asset",), unit_kind="asset", adverse=AdverseDirection.HIGHER),
    ),
)


def available(
    definition_value: MetricDefinition,
    value: Decimal | int | str,
    *,
    unit: str,
    dimensions: Mapping[str, str] | None = None,
) -> MetricValue:
    return MetricValue(
        metric_key=definition_value.metric_key,
        value_type=definition_value.value_type,
        unit=unit,
        source_level=definition_value.required_input_level,
        status=MetricValueStatus.AVAILABLE,
        value=value,
        dimensions=dimensions or {},
    )


def unavailable(
    definition_value: MetricDefinition,
    reason: str,
    *,
    unit: str,
    dimensions: Mapping[str, str] | None = None,
) -> MetricValue:
    return MetricValue(
        metric_key=definition_value.metric_key,
        value_type=definition_value.value_type,
        unit=unit,
        source_level=definition_value.required_input_level,
        status=MetricValueStatus.UNAVAILABLE,
        reason_code=reason,
        dimensions=dimensions or {},
    )


class GridMetricCalculator:
    metric_set = GRID_METRIC_SET

    def __init__(self) -> None:
        self.definitions = {
            item.metric_key: item for item in self.metric_set.definitions
        }

    def d(self, key: str) -> MetricDefinition:
        return self.definitions[key]

    def calculate(self, metric_input: MetricInput) -> tuple[MetricValue, ...]:
        summary = metric_input.provider_summary
        values: list[MetricValue] = []
        if "strategy_type" in summary:
            values.append(available(self.d("grid.strategy_type"), str(summary["strategy_type"]), unit="enum"))
        for key, field in (
            ("grid.completed_cycles", "completed_cycles"),
            ("grid.cells_added", "cells_added"),
            ("grid.cells_reclaimed", "cells_reclaimed"),
            ("grid.final_cell_count", "final_cell_count"),
            ("grid.layer_count", "layer_count"),
            ("grid.reset_count", "reset_count"),
            ("grid.retiring_grid_count", "retiring_grid_count"),
        ):
            if field in summary:
                values.append(available(self.d(key), int(summary[field]), unit="count"))
        layers = summary.get("layers", [])
        if isinstance(layers, list):
            for layer in layers:
                if not isinstance(layer, Mapping):
                    continue
                dimensions = {
                    "layer_index": str(layer.get("layer_index")),
                    "generation": str(layer.get("generation")),
                }
                values.append(available(self.d("grid.layer.completed_cycles"), int(layer.get("completed_cycles", 0)), unit="count", dimensions=dimensions))
                unit = next(iter(metric_input.position_units.values()), "quantity")
                values.append(available(self.d("grid.layer.final_position"), decimal_value(layer.get("position_quantity", "0"), name="layer.position_quantity"), unit=unit, dimensions={**dimensions, "quantity_unit": unit}))
        values.extend(self._trace_metrics(metric_input))
        return tuple(values)

    def _trace_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        trace_keys = (
            ("grid.fill_count_by_role", {"role": "*"}, "count"),
            ("grid.fill_count_by_generation", {"layer_index": "*", "generation": "*"}, "count"),
            ("grid.completed_cycle_count_from_trace", {}, "count"),
            ("grid.incomplete_entry_count", {}, "count"),
            ("grid.average_net_pnl_per_completed_cycle", {"pnl_asset": "*"}, "asset"),
            ("grid.average_fee_per_completed_cycle", {"fee_asset": "*"}, "asset"),
        )
        if metric_input.trace is None:
            return [unavailable(self.d(key), "TRACE_PURGED", unit=unit, dimensions=dimensions) for key, dimensions, unit in trace_keys]
        raw_fills = metric_input.trace.get("fills", [])
        fills = [fill for fill in raw_fills if isinstance(fill, Mapping)] if isinstance(raw_fills, list) else []
        result: list[MetricValue] = []
        roles = Counter(str(fill.get("tags", {}).get("role", "unknown")) for fill in fills if isinstance(fill.get("tags", {}), Mapping))
        for role, count in sorted(roles.items()):
            result.append(available(self.d("grid.fill_count_by_role"), count, unit="count", dimensions={"role": role}))
        generations = Counter()
        groups: dict[tuple[str, str, str, str], list[Mapping[str, object]]] = defaultdict(list)
        for fill in fills:
            tags = fill.get("tags", {})
            if not isinstance(tags, Mapping) or "cell_id" not in tags or "cycle" not in tags:
                continue
            layer = str(tags.get("layer_index", "0"))
            generation = str(tags.get("layer_generation", "0"))
            generations[(layer, generation)] += 1
            groups[(str(tags["cell_id"]), str(tags["cycle"]), layer, generation)].append(fill)
        for (layer, generation), count in sorted(generations.items()):
            result.append(available(self.d("grid.fill_count_by_generation"), count, unit="count", dimensions={"layer_index": layer, "generation": generation}))
        completed = []
        incomplete_entries = 0
        malformed = False
        for group in groups.values():
            entries = [fill for fill in group if str(fill.get("tags", {}).get("role", "")).lower() == "entry"]
            exits = [fill for fill in group if str(fill.get("tags", {}).get("role", "")).lower() == "exit"]
            if entries and not exits:
                incomplete_entries += len(entries)
                continue
            if len(entries) != 1 or len(exits) != 1:
                malformed = True
                continue
            entry, exit_fill = entries[0], exits[0]
            if decimal_value(entry["quantity"], name="entry.quantity") != decimal_value(exit_fill["quantity"], name="exit.quantity") or str(entry.get("fee_asset")) != str(exit_fill.get("fee_asset")):
                malformed = True
                continue
            completed.append(self._cycle_result(entry, exit_fill))
        result.extend([
            available(self.d("grid.completed_cycle_count_from_trace"), len(completed), unit="count"),
            available(self.d("grid.incomplete_entry_count"), incomplete_entries, unit="count"),
        ])
        if malformed or not completed:
            reason = "INCOMPLETE_GRID_LIFECYCLE" if malformed else "NO_COMPLETED_CYCLE"
            result.extend([
                unavailable(self.d("grid.average_net_pnl_per_completed_cycle"), reason, unit="asset", dimensions={"pnl_asset": "*"}),
                unavailable(self.d("grid.average_fee_per_completed_cycle"), reason, unit="asset", dimensions={"fee_asset": "*"}),
            ])
            return result
        assets = {item[2] for item in completed}
        if len(assets) != 1:
            result.extend([
                unavailable(self.d("grid.average_net_pnl_per_completed_cycle"), "UNIT_MISMATCH", unit="asset", dimensions={"pnl_asset": "*"}),
                unavailable(self.d("grid.average_fee_per_completed_cycle"), "UNIT_MISMATCH", unit="asset", dimensions={"fee_asset": "*"}),
            ])
            return result
        asset = next(iter(assets))
        result.extend([
            available(self.d("grid.average_net_pnl_per_completed_cycle"), sum((item[0] for item in completed), Decimal("0")) / Decimal(len(completed)), unit=asset, dimensions={"pnl_asset": asset}),
            available(self.d("grid.average_fee_per_completed_cycle"), sum((item[1] for item in completed), Decimal("0")) / Decimal(len(completed)), unit=asset, dimensions={"fee_asset": asset}),
        ])
        return result

    @staticmethod
    def _cycle_result(entry: Mapping[str, object], exit_fill: Mapping[str, object]) -> tuple[Decimal, Decimal, str]:
        entry_price = decimal_value(entry["price"], name="entry.price")
        exit_price = decimal_value(exit_fill["price"], name="exit.price")
        quantity = decimal_value(entry["quantity"], name="entry.quantity")
        tags = entry.get("tags", {})
        tags = tags if isinstance(tags, Mapping) else {}
        direction = Decimal("1") if str(entry.get("side")) == "BUY" else Decimal("-1")
        if str(tags.get("market_type")) == "coinm":
            contract_size = decimal_value(tags.get("contract_size", "0"), name="contract_size")
            gross = direction * quantity * contract_size * (Decimal("1") / entry_price - Decimal("1") / exit_price)
        else:
            gross = direction * quantity * (exit_price - entry_price)
        fees = decimal_value(entry.get("fee_amount", "0"), name="entry.fee") + decimal_value(exit_fill.get("fee_amount", "0"), name="exit.fee")
        return gross - fees, fees, str(entry.get("fee_asset", ""))
