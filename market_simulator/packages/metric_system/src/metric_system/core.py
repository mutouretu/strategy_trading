"""Versioned domain-neutral single-Run financial metrics."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, ROUND_FLOOR, localcontext
from typing import Callable, Iterable, Mapping, Sequence

from .inputs import EquityPoint, MetricInput, decimal_value, integer_value
from .models import (
    AdverseDirection,
    MetricDefinition,
    MetricInputLevel,
    MetricSet,
    MetricValue,
    MetricValueStatus,
    MetricValueType,
)


DAY_MS = 86_400_000
YEAR_MS = 365 * DAY_MS
SQRT_365 = Decimal(365).sqrt()


def _definition(
    key: str,
    name: str,
    category: str,
    value_type: MetricValueType,
    unit_kind: str,
    level: MetricInputLevel,
    *,
    dimensions: tuple[str, ...] = (),
    adverse: AdverseDirection = AdverseDirection.NONE,
) -> MetricDefinition:
    return MetricDefinition(
        metric_key=key,
        display_name=name,
        category=category,
        description=name,
        value_type=value_type,
        unit_kind=unit_kind,
        required_input_level=level,
        dimensions=dimensions,
        adverse_direction=adverse,
    )


_EQUITY_DIMENSIONS = ("scope", "valuation_asset")
_POSITION_DIMENSIONS = ("instrument", "quantity_unit")
_MARGIN_DIMENSIONS = (
    "instrument",
    "settlement_asset",
    "notional_asset",
)


CORE_METRIC_SET = MetricSet(
    metric_set_id="core",
    version="v1",
    description="通用仿真收益、风险、仓位、保证金和执行指标",
    definitions=(
        _definition("return.initial_equity", "初始权益", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=_EQUITY_DIMENSIONS),
        _definition("return.final_equity", "期末权益", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=_EQUITY_DIMENSIONS),
        _definition("return.absolute", "绝对收益", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("return.total_rate", "总收益率", "return", MetricValueType.DECIMAL, "ratio", MetricInputLevel.SUMMARY, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("return.annualized_rate", "年化收益率", "return", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("pnl.gross_realized", "毛已实现盈亏", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=("valuation_asset",), adverse=AdverseDirection.LOWER),
        _definition("cost.total_fees", "累计手续费", "cost", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=("valuation_asset",), adverse=AdverseDirection.HIGHER),
        _definition("pnl.net_realized", "扣手续费已实现盈亏", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=("valuation_asset",), adverse=AdverseDirection.LOWER),
        _definition("funding.net_wallet_delta", "资金费净钱包变动", "cost", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=("valuation_asset",), adverse=AdverseDirection.LOWER),
        _definition("pnl.net_after_fees_funding", "费用和资金费后已实现盈亏", "return", MetricValueType.DECIMAL, "asset", MetricInputLevel.SUMMARY, dimensions=("valuation_asset",), adverse=AdverseDirection.LOWER),
        _definition("risk.minimum_equity", "最低权益", "risk", MetricValueType.DECIMAL, "asset", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("risk.minimum_equity_timestamp", "最低权益时间", "risk", MetricValueType.TIMESTAMP, "timestamp_ms", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS),
        _definition("risk.max_drawdown_amount", "最大回撤金额", "risk", MetricValueType.DECIMAL, "asset", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.max_drawdown_rate", "最大回撤率", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.max_drawdown_peak_timestamp", "最大回撤峰值时间", "risk", MetricValueType.TIMESTAMP, "timestamp_ms", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS),
        _definition("risk.max_drawdown_trough_timestamp", "最大回撤谷底时间", "risk", MetricValueType.TIMESTAMP, "timestamp_ms", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS),
        _definition("risk.longest_underwater_seconds", "最长水下时间", "risk", MetricValueType.DECIMAL, "seconds", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.underwater_time_ratio", "水下时间占比", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.end_underwater", "期末仍在水下", "risk", MetricValueType.BOOLEAN, "boolean", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.daily_mean_return", "日收益均值", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("risk.daily_return_std", "日收益样本标准差", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.annualized_volatility", "年化波动率", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("risk.sharpe", "Sharpe", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("risk.sortino", "Sortino", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("risk.daily_return_p05", "日收益 P05", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("risk.daily_expected_shortfall_p05", "日收益 5% Expected Shortfall", "risk", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_EQUITY_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("run.completed", "完整运行", "termination", MetricValueType.BOOLEAN, "boolean", MetricInputLevel.SUMMARY, adverse=AdverseDirection.LOWER),
        _definition("run.liquidated", "强平", "termination", MetricValueType.BOOLEAN, "boolean", MetricInputLevel.SUMMARY, adverse=AdverseDirection.HIGHER),
        _definition("run.bankrupt", "破产", "termination", MetricValueType.BOOLEAN, "boolean", MetricInputLevel.SUMMARY, adverse=AdverseDirection.HIGHER),
        _definition("run.termination_reason", "终止原因", "termination", MetricValueType.TEXT, "enum", MetricInputLevel.SUMMARY),
        _definition("run.termination_sequence", "终止序号", "termination", MetricValueType.INTEGER, "sequence", MetricInputLevel.SUMMARY),
        _definition("run.termination_timestamp", "终止时间", "termination", MetricValueType.TIMESTAMP, "timestamp_ms", MetricInputLevel.TRACE),
        _definition("run.observed_frame_count", "实际处理 K 线数", "termination", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE),
        _definition("margin.max_position_notional", "最大仓位名义价值", "capital", MetricValueType.DECIMAL, "notional_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.average_position_notional", "平均仓位名义价值", "capital", MetricValueType.DECIMAL, "notional_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.max_initial_margin", "最大初始保证金", "capital", MetricValueType.DECIMAL, "settlement_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.average_initial_margin", "平均初始保证金", "capital", MetricValueType.DECIMAL, "settlement_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.max_maintenance_margin", "最大维持保证金", "capital", MetricValueType.DECIMAL, "settlement_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.minimum_buffer", "最低保证金缓冲", "risk", MetricValueType.DECIMAL, "settlement_asset", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("margin.max_initial_utilization", "最大初始保证金使用率", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.max_maintenance_utilization", "最大维持保证金使用率", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.max_effective_leverage", "最大有效杠杆", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("margin.average_effective_leverage", "平均有效杠杆", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("capital.realized_pnl_on_max_initial_margin", "已实现净盈亏/最大初始保证金", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("capital.realized_pnl_on_average_initial_margin", "已实现净盈亏/平均初始保证金", "capital", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_MARGIN_DIMENSIONS, adverse=AdverseDirection.LOWER),
        _definition("position.final", "最终净仓位", "position", MetricValueType.DECIMAL, "quantity", MetricInputLevel.SUMMARY, dimensions=_POSITION_DIMENSIONS),
        _definition("position.max_absolute", "最大绝对仓位", "position", MetricValueType.DECIMAL, "quantity", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.average_absolute", "平均绝对仓位", "position", MetricValueType.DECIMAL, "quantity", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.max_long", "最大多头仓位", "position", MetricValueType.DECIMAL, "quantity", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.max_short_absolute", "最大空头仓位", "position", MetricValueType.DECIMAL, "quantity", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.in_market_time_ratio", "有仓位时间占比", "position", MetricValueType.DECIMAL, "ratio", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.longest_holding_seconds", "最长连续持仓时间", "position", MetricValueType.DECIMAL, "seconds", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS, adverse=AdverseDirection.HIGHER),
        _definition("position.direction_switch_count", "方向切换次数", "position", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE, dimensions=_POSITION_DIMENSIONS),
        _definition("execution.fill_count", "成交数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.SUMMARY),
        _definition("execution.fill_count_by_side", "按方向成交数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE, dimensions=("side",)),
        _definition("execution.fill_count_by_intent_mode", "按触发模式成交数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE, dimensions=("intent_mode",)),
        _definition("execution.fill_count_by_liquidity_role", "按流动性角色成交数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE, dimensions=("liquidity_role",)),
        _definition("execution.reduce_only_fill_count", "reduce-only 成交数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE),
        _definition("execution.active_frame_count", "有成交 K 线数", "execution", MetricValueType.INTEGER, "count", MetricInputLevel.TRACE),
        _definition("funding.settlement_count", "资金费结算次数", "cost", MetricValueType.INTEGER, "count", MetricInputLevel.SUMMARY),
    ),
)


def _available(
    definition: MetricDefinition,
    value: Decimal | int | bool | str,
    *,
    unit: str,
    dimensions: Mapping[str, str] | None = None,
    source_level: MetricInputLevel | None = None,
) -> MetricValue:
    return MetricValue(
        metric_key=definition.metric_key,
        value_type=definition.value_type,
        unit=unit,
        source_level=source_level or definition.required_input_level,
        status=MetricValueStatus.AVAILABLE,
        value=value,
        dimensions=dimensions or {},
    )


def _unavailable(
    definition: MetricDefinition,
    reason: str,
    *,
    unit: str,
    dimensions: Mapping[str, str] | None = None,
) -> MetricValue:
    return MetricValue(
        metric_key=definition.metric_key,
        value_type=definition.value_type,
        unit=unit,
        source_level=definition.required_input_level,
        status=MetricValueStatus.UNAVAILABLE,
        dimensions=dimensions or {},
        reason_code=reason,
    )


def _mean(values: Sequence[Decimal]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def _sample_std(values: Sequence[Decimal]) -> Decimal:
    average = _mean(values)
    return (
        sum((value - average) ** 2 for value in values)
        / Decimal(len(values) - 1)
    ).sqrt()


def decimal_quantile(values: Sequence[Decimal], probability: Decimal) -> Decimal:
    if not values:
        raise ValueError("quantile requires at least one value")
    if not Decimal("0") <= probability <= Decimal("1"):
        raise ValueError("quantile probability must be between zero and one")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * probability
    lower = int(position.to_integral_value(rounding=ROUND_FLOOR))
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _time_weighted_average(
    rows: Sequence[Mapping[str, object]],
    value: Callable[[Mapping[str, object]], Decimal | None],
) -> Decimal | None:
    samples = [(integer_value(row.get("timestamp"), name="timestamp"), value(row)) for row in rows]
    samples = [(timestamp, item) for timestamp, item in samples if item is not None]
    if not samples:
        return None
    if len(samples) == 1:
        return samples[0][1]
    numerator = Decimal("0")
    denominator = 0
    for index in range(len(samples) - 1):
        timestamp, item = samples[index]
        next_timestamp = samples[index + 1][0]
        duration = next_timestamp - timestamp
        if duration <= 0:
            raise ValueError("time-weighted rows must be strictly increasing")
        numerator += item * Decimal(duration)
        denominator += duration
    if denominator == 0:
        return _mean([item for _, item in samples])
    return numerator / Decimal(denominator)


class CoreMetricCalculator:
    metric_set = CORE_METRIC_SET

    def __init__(self) -> None:
        self._definitions = {
            definition.metric_key: definition
            for definition in self.metric_set.definitions
        }

    def d(self, key: str) -> MetricDefinition:
        return self._definitions[key]

    def calculate(self, metric_input: MetricInput) -> tuple[MetricValue, ...]:
        values: list[MetricValue] = []
        for series in metric_input.equity_series:
            values.extend(self._equity_metrics(metric_input, series))
        values.extend(self._pnl_metrics(metric_input))
        values.extend(self._termination_metrics(metric_input))
        values.extend(self._margin_metrics(metric_input))
        values.extend(self._position_metrics(metric_input))
        values.extend(self._execution_metrics(metric_input))
        return tuple(values)

    def _equity_metrics(self, metric_input: MetricInput, series) -> list[MetricValue]:
        dimensions = {
            "scope": series.series_key,
            "valuation_asset": series.valuation_asset,
        }
        unit = series.valuation_asset
        absolute = series.final_value - series.initial_value
        result = [
            _available(self.d("return.initial_equity"), series.initial_value, unit=unit, dimensions=dimensions),
            _available(self.d("return.final_equity"), series.final_value, unit=unit, dimensions=dimensions),
            _available(self.d("return.absolute"), absolute, unit=unit, dimensions=dimensions),
        ]
        if series.initial_value > 0:
            result.append(_available(self.d("return.total_rate"), series.final_value / series.initial_value - 1, unit="ratio", dimensions=dimensions))
        else:
            result.append(_unavailable(self.d("return.total_rate"), "NONPOSITIVE_INITIAL_EQUITY", unit="ratio", dimensions=dimensions))

        path_keys = (
            "return.annualized_rate",
            "risk.minimum_equity",
            "risk.minimum_equity_timestamp",
            "risk.max_drawdown_amount",
            "risk.max_drawdown_rate",
            "risk.max_drawdown_peak_timestamp",
            "risk.max_drawdown_trough_timestamp",
            "risk.longest_underwater_seconds",
            "risk.underwater_time_ratio",
            "risk.end_underwater",
            "risk.daily_mean_return",
            "risk.daily_return_std",
            "risk.annualized_volatility",
            "risk.sharpe",
            "risk.sortino",
            "risk.daily_return_p05",
            "risk.daily_expected_shortfall_p05",
        )
        if not series.points:
            for key in path_keys:
                definition = self.d(key)
                result.append(_unavailable(definition, "TRACE_PURGED", unit=(unit if definition.unit_kind == "asset" else definition.unit_kind), dimensions=dimensions))
            return result

        points = series.points
        elapsed_ms = points[-1].timestamp - points[0].timestamp
        regular = (
            metric_input.interval_ms is not None
            and all(
                points[index + 1].timestamp - points[index].timestamp
                == metric_input.interval_ms
                for index in range(len(points) - 1)
            )
        )
        if series.initial_value > 0 and series.final_value > 0 and elapsed_ms > 0 and regular:
            with localcontext() as context:
                context.prec = 40
                exponent = Decimal(YEAR_MS) / Decimal(elapsed_ms)
                annualized = ((series.final_value / series.initial_value).ln() * exponent).exp() - 1
            result.append(_available(self.d("return.annualized_rate"), annualized, unit="ratio", dimensions=dimensions))
        else:
            reason = "AMBIGUOUS_TIME_AXIS" if not regular else "NONPOSITIVE_EQUITY"
            result.append(_unavailable(self.d("return.annualized_rate"), reason, unit="ratio", dimensions=dimensions))

        minimum = min(points, key=lambda item: item.value)
        result.extend([
            _available(self.d("risk.minimum_equity"), minimum.value, unit=unit, dimensions=dimensions),
            _available(self.d("risk.minimum_equity_timestamp"), minimum.timestamp, unit="timestamp_ms", dimensions=dimensions),
        ])
        peak = points[0]
        worst_amount = Decimal("0")
        worst_rate = Decimal("0")
        worst_peak = peak.timestamp
        worst_trough = peak.timestamp
        underwater_start: int | None = None
        longest_underwater = 0
        underwater_time = 0
        for index, point in enumerate(points):
            if point.value >= peak.value:
                if underwater_start is not None:
                    longest_underwater = max(longest_underwater, point.timestamp - underwater_start)
                    underwater_start = None
                peak = point
            elif peak.value > 0:
                if underwater_start is None:
                    underwater_start = point.timestamp
                amount = peak.value - point.value
                rate = amount / peak.value
                if rate > worst_rate or (rate == worst_rate and amount > worst_amount):
                    worst_amount = amount
                    worst_rate = rate
                    worst_peak = peak.timestamp
                    worst_trough = point.timestamp
            if index < len(points) - 1 and point.value < peak.value:
                underwater_time += points[index + 1].timestamp - point.timestamp
        if underwater_start is not None:
            longest_underwater = max(longest_underwater, points[-1].timestamp - underwater_start)
        result.extend([
            _available(self.d("risk.max_drawdown_amount"), worst_amount, unit=unit, dimensions=dimensions),
            _available(self.d("risk.max_drawdown_rate"), worst_rate, unit="ratio", dimensions=dimensions),
            _available(self.d("risk.max_drawdown_peak_timestamp"), worst_peak, unit="timestamp_ms", dimensions=dimensions),
            _available(self.d("risk.max_drawdown_trough_timestamp"), worst_trough, unit="timestamp_ms", dimensions=dimensions),
        ])
        if regular and elapsed_ms > 0:
            result.extend([
                _available(self.d("risk.longest_underwater_seconds"), Decimal(longest_underwater) / Decimal(1000), unit="seconds", dimensions=dimensions),
                _available(self.d("risk.underwater_time_ratio"), Decimal(underwater_time) / Decimal(elapsed_ms), unit="ratio", dimensions=dimensions),
            ])
        else:
            result.extend([
                _unavailable(self.d("risk.longest_underwater_seconds"), "AMBIGUOUS_TIME_AXIS", unit="seconds", dimensions=dimensions),
                _unavailable(self.d("risk.underwater_time_ratio"), "AMBIGUOUS_TIME_AXIS", unit="ratio", dimensions=dimensions),
            ])
        result.append(_available(self.d("risk.end_underwater"), points[-1].value < max(point.value for point in points), unit="boolean", dimensions=dimensions))
        result.extend(self._period_return_metrics(metric_input, points, dimensions))
        return result

    def _period_return_metrics(self, metric_input: MetricInput, points: Sequence[EquityPoint], dimensions: Mapping[str, str]) -> list[MetricValue]:
        keys = (
            "risk.daily_mean_return",
            "risk.daily_return_std",
            "risk.annualized_volatility",
            "risk.sharpe",
            "risk.sortino",
            "risk.daily_return_p05",
            "risk.daily_expected_shortfall_p05",
        )
        interval_ms = metric_input.interval_ms
        if (
            interval_ms is None
            or interval_ms <= 0
            or interval_ms > DAY_MS
            or DAY_MS % interval_ms != 0
        ):
            return [_unavailable(self.d(key), "UNSUPPORTED_FREQUENCY", unit="ratio", dimensions=dimensions) for key in keys]
        daily_points = (
            tuple(points)
            if interval_ms == DAY_MS
            else self._daily_equity_points(points, interval_ms)
        )
        if len(daily_points) < 3:
            return [_unavailable(self.d(key), "INSUFFICIENT_OBSERVATIONS", unit="ratio", dimensions=dimensions) for key in keys]
        returns: list[Decimal] = []
        for previous, current in zip(daily_points, daily_points[1:]):
            if previous.value <= 0:
                return [_unavailable(self.d(key), "NONPOSITIVE_EQUITY", unit="ratio", dimensions=dimensions) for key in keys]
            returns.append(current.value / previous.value - 1)
        average = _mean(returns)
        standard_deviation = _sample_std(returns)
        p05 = decimal_quantile(returns, Decimal("0.05"))
        tail = [value for value in returns if value <= p05]
        result = [
            _available(self.d("risk.daily_mean_return"), average, unit="ratio", dimensions=dimensions),
            _available(self.d("risk.daily_return_std"), standard_deviation, unit="ratio", dimensions=dimensions),
            _available(self.d("risk.annualized_volatility"), standard_deviation * SQRT_365, unit="ratio", dimensions=dimensions),
            _available(self.d("risk.daily_return_p05"), p05, unit="ratio", dimensions=dimensions),
            _available(self.d("risk.daily_expected_shortfall_p05"), _mean(tail), unit="ratio", dimensions=dimensions),
        ]
        if standard_deviation == 0:
            result.append(_unavailable(self.d("risk.sharpe"), "ZERO_VOLATILITY", unit="ratio", dimensions=dimensions))
        else:
            result.append(_available(self.d("risk.sharpe"), average / standard_deviation * SQRT_365, unit="ratio", dimensions=dimensions))
        downside = _mean([min(value, Decimal("0")) ** 2 for value in returns]).sqrt()
        if downside == 0:
            result.append(_unavailable(self.d("risk.sortino"), "ZERO_DOWNSIDE_DEVIATION", unit="ratio", dimensions=dimensions))
        else:
            result.append(_available(self.d("risk.sortino"), average / downside * SQRT_365, unit="ratio", dimensions=dimensions))
        return result

    @staticmethod
    def _daily_equity_points(
        points: Sequence[EquityPoint],
        interval_ms: int,
    ) -> tuple[EquityPoint, ...]:
        """Sample a regular intraday equity series at daily boundaries."""

        if len(points) < 2:
            return tuple(points)
        first_observed = points[1]
        boundary_offset = first_observed.timestamp % DAY_MS
        sampled = [
            point
            for point in points[1:]
            if point.timestamp % DAY_MS == boundary_offset
        ]
        if not sampled or sampled[0] != first_observed:
            sampled.insert(0, first_observed)
        if points[-1].timestamp - sampled[-1].timestamp >= interval_ms:
            sampled.append(points[-1])
        return tuple(sampled)

    def _pnl_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        result = metric_input.result_summary
        asset = str(result["equity_asset"]).upper()
        dimensions = {"valuation_asset": asset}
        mapping = {
            "pnl.gross_realized": "gross_realized_pnl",
            "cost.total_fees": "total_fees",
            "pnl.net_realized": "net_realized_pnl",
            "funding.net_wallet_delta": "total_funding",
            "pnl.net_after_fees_funding": "net_pnl_after_fees_and_funding",
        }
        return [
            _available(self.d(key), decimal_value(result[field], name=field), unit=asset, dimensions=dimensions)
            for key, field in mapping.items()
        ]

    def _termination_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        result = metric_input.result_summary
        values = [
            _available(self.d("run.completed"), bool(result.get("completed")), unit="boolean"),
            _available(self.d("run.liquidated"), bool(result.get("liquidated")), unit="boolean"),
            _available(self.d("run.bankrupt"), bool(result.get("bankrupt")), unit="boolean"),
            _available(self.d("run.termination_reason"), str(result.get("termination_reason") or "NONE"), unit="enum"),
        ]
        sequence = result.get("termination_sequence")
        if sequence is None:
            values.append(_unavailable(self.d("run.termination_sequence"), "NOT_TERMINATED", unit="sequence"))
        else:
            values.append(_available(self.d("run.termination_sequence"), integer_value(sequence, name="termination_sequence"), unit="sequence"))
        if metric_input.trace is None:
            values.extend([
                _unavailable(self.d("run.termination_timestamp"), "TRACE_PURGED", unit="timestamp_ms"),
                _unavailable(self.d("run.observed_frame_count"), "TRACE_PURGED", unit="count"),
            ])
        else:
            equity = metric_input.trace.get("equity", [])
            frame_count = len(equity) if isinstance(equity, list) else 0
            values.append(_available(self.d("run.observed_frame_count"), frame_count, unit="count"))
            if sequence is None or not equity:
                values.append(_unavailable(self.d("run.termination_timestamp"), "NOT_TERMINATED", unit="timestamp_ms"))
            else:
                values.append(_available(self.d("run.termination_timestamp"), integer_value(equity[-1].get("timestamp"), name="termination_timestamp"), unit="timestamp_ms"))
        return values

    def _margin_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        keys = (
            "margin.max_position_notional",
            "margin.average_position_notional",
            "margin.max_initial_margin",
            "margin.average_initial_margin",
            "margin.max_maintenance_margin",
            "margin.minimum_buffer",
            "margin.max_initial_utilization",
            "margin.max_maintenance_utilization",
            "margin.max_effective_leverage",
            "margin.average_effective_leverage",
            "capital.realized_pnl_on_max_initial_margin",
            "capital.realized_pnl_on_average_initial_margin",
        )
        missing_dimensions = {"instrument": "*", "settlement_asset": "*", "notional_asset": "*"}
        if metric_input.trace is None:
            return [_unavailable(self.d(key), "TRACE_PURGED", unit=self.d(key).unit_kind, dimensions=missing_dimensions) for key in keys]
        raw = metric_input.trace.get("margin", [])
        if not isinstance(raw, list) or not raw:
            return [_unavailable(self.d(key), "NO_MARGIN_MODEL", unit=self.d(key).unit_kind, dimensions=missing_dimensions) for key in keys]
        groups: dict[tuple[str, str, str], list[Mapping[str, object]]] = defaultdict(list)
        for row in raw:
            if isinstance(row, Mapping):
                groups[(str(row.get("instrument", "")), str(row.get("settlement_asset", "")), str(row.get("notional_asset", "")))].append(row)
        result: list[MetricValue] = []
        net_pnl = decimal_value(metric_input.result_summary["net_pnl_after_fees_and_funding"], name="net_pnl_after_fees_and_funding")
        equity_asset = str(metric_input.result_summary["equity_asset"]).upper()
        for (instrument, settlement, notional), rows in sorted(groups.items()):
            dimensions = {"instrument": instrument, "settlement_asset": settlement, "notional_asset": notional}
            def values_for(field: str) -> list[Decimal]:
                return [decimal_value(row[field], name=field) for row in rows if row.get(field) is not None]
            notionals = values_for("position_notional")
            initials = values_for("position_initial_margin")
            maintenance = values_for("maintenance_margin")
            buffers = values_for("margin_buffer")
            initial_utilization = values_for("initial_margin_utilization")
            maintenance_utilization = values_for("maintenance_margin_utilization")
            leverage = values_for("effective_leverage")
            average_notional = _time_weighted_average(rows, lambda row: decimal_value(row["position_notional"], name="position_notional"))
            average_initial = _time_weighted_average(rows, lambda row: decimal_value(row["position_initial_margin"], name="position_initial_margin"))
            average_leverage = _time_weighted_average(rows, lambda row: None if row.get("effective_leverage") is None else decimal_value(row["effective_leverage"], name="effective_leverage"))
            result.extend([
                _available(self.d("margin.max_position_notional"), max(notionals), unit=notional, dimensions=dimensions),
                _available(self.d("margin.average_position_notional"), average_notional or Decimal("0"), unit=notional, dimensions=dimensions),
                _available(self.d("margin.max_initial_margin"), max(initials), unit=settlement, dimensions=dimensions),
                _available(self.d("margin.average_initial_margin"), average_initial or Decimal("0"), unit=settlement, dimensions=dimensions),
                _available(self.d("margin.max_maintenance_margin"), max(maintenance), unit=settlement, dimensions=dimensions),
                _available(self.d("margin.minimum_buffer"), min(buffers), unit=settlement, dimensions=dimensions),
            ])
            for key, values in (("margin.max_initial_utilization", initial_utilization), ("margin.max_maintenance_utilization", maintenance_utilization), ("margin.max_effective_leverage", leverage)):
                result.append(_available(self.d(key), max(values), unit="ratio", dimensions=dimensions) if values else _unavailable(self.d(key), "NO_OPEN_POSITION", unit="ratio", dimensions=dimensions))
            result.append(_available(self.d("margin.average_effective_leverage"), average_leverage, unit="ratio", dimensions=dimensions) if average_leverage is not None else _unavailable(self.d("margin.average_effective_leverage"), "NO_OPEN_POSITION", unit="ratio", dimensions=dimensions))
            for key, denominator in (("capital.realized_pnl_on_max_initial_margin", max(initials)), ("capital.realized_pnl_on_average_initial_margin", average_initial or Decimal("0"))):
                if settlement.upper() != equity_asset:
                    result.append(_unavailable(self.d(key), "UNIT_MISMATCH", unit="ratio", dimensions=dimensions))
                elif denominator <= 0:
                    result.append(_unavailable(self.d(key), "ZERO_MARGIN", unit="ratio", dimensions=dimensions))
                else:
                    result.append(_available(self.d(key), net_pnl / denominator, unit="ratio", dimensions=dimensions))
        return result

    def _position_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        final_raw = metric_input.result_summary.get("final_positions", {})
        final_positions = final_raw if isinstance(final_raw, Mapping) else {}
        instruments = set(str(key) for key in final_positions)
        for point in metric_input.position_points:
            instruments.update(point.positions)
        result: list[MetricValue] = []
        for instrument in sorted(instruments):
            unit = metric_input.position_units.get(instrument, "quantity")
            dimensions = {"instrument": instrument, "quantity_unit": unit}
            final = decimal_value(final_positions.get(instrument, "0"), name=f"final_positions.{instrument}")
            result.append(_available(self.d("position.final"), final, unit=unit, dimensions=dimensions))
            if not metric_input.position_points:
                for key in ("position.max_absolute", "position.average_absolute", "position.max_long", "position.max_short_absolute", "position.in_market_time_ratio", "position.longest_holding_seconds", "position.direction_switch_count"):
                    result.append(_unavailable(self.d(key), "TRACE_PURGED", unit=("count" if self.d(key).value_type is MetricValueType.INTEGER else ("seconds" if "seconds" in key else "ratio" if "ratio" in key else unit)), dimensions=dimensions))
                continue
            points = metric_input.position_points
            quantities = [point.positions.get(instrument, Decimal("0")) for point in points]
            elapsed = points[-1].timestamp - points[0].timestamp
            weighted = Decimal("0")
            in_market = 0
            holding_start: int | None = None
            longest = 0
            last_direction = 0
            switches = 0
            for index, (point, quantity) in enumerate(zip(points, quantities)):
                direction = 1 if quantity > 0 else -1 if quantity < 0 else 0
                if direction and last_direction and direction != last_direction:
                    switches += 1
                if direction:
                    last_direction = direction
                    if holding_start is None:
                        holding_start = point.timestamp
                elif holding_start is not None:
                    longest = max(longest, point.timestamp - holding_start)
                    holding_start = None
                if index < len(points) - 1:
                    duration = points[index + 1].timestamp - point.timestamp
                    weighted += abs(quantity) * Decimal(duration)
                    if quantity != 0:
                        in_market += duration
            if holding_start is not None:
                longest = max(longest, points[-1].timestamp - holding_start)
            result.extend([
                _available(self.d("position.max_absolute"), max(abs(value) for value in quantities), unit=unit, dimensions=dimensions),
                _available(self.d("position.average_absolute"), weighted / Decimal(elapsed) if elapsed > 0 else abs(quantities[-1]), unit=unit, dimensions=dimensions),
                _available(self.d("position.max_long"), max(max(quantities), Decimal("0")), unit=unit, dimensions=dimensions),
                _available(self.d("position.max_short_absolute"), abs(min(min(quantities), Decimal("0"))), unit=unit, dimensions=dimensions),
                _available(self.d("position.in_market_time_ratio"), Decimal(in_market) / Decimal(elapsed) if elapsed > 0 else Decimal("0"), unit="ratio", dimensions=dimensions),
                _available(self.d("position.longest_holding_seconds"), Decimal(longest) / Decimal(1000), unit="seconds", dimensions=dimensions),
                _available(self.d("position.direction_switch_count"), switches, unit="count", dimensions=dimensions),
            ])
        return result

    def _execution_metrics(self, metric_input: MetricInput) -> list[MetricValue]:
        result_summary = metric_input.result_summary
        values = [
            _available(self.d("execution.fill_count"), integer_value(result_summary.get("fill_count", 0), name="fill_count"), unit="count"),
            _available(self.d("funding.settlement_count"), integer_value(result_summary.get("funding_event_count", 0), name="funding_event_count"), unit="count"),
        ]
        if metric_input.trace is None:
            values.extend([
                _unavailable(self.d("execution.fill_count_by_side"), "TRACE_PURGED", unit="count", dimensions={"side": "*"}),
                _unavailable(self.d("execution.fill_count_by_intent_mode"), "TRACE_PURGED", unit="count", dimensions={"intent_mode": "*"}),
                _unavailable(self.d("execution.fill_count_by_liquidity_role"), "TRACE_PURGED", unit="count", dimensions={"liquidity_role": "*"}),
                _unavailable(self.d("execution.reduce_only_fill_count"), "TRACE_PURGED", unit="count"),
                _unavailable(self.d("execution.active_frame_count"), "TRACE_PURGED", unit="count"),
            ])
            return values
        fills = metric_input.trace.get("fills", [])
        fills = fills if isinstance(fills, list) else []
        for key, field, dimension in (
            ("execution.fill_count_by_side", "side", "side"),
            ("execution.fill_count_by_intent_mode", "intent_mode", "intent_mode"),
            ("execution.fill_count_by_liquidity_role", "liquidity_role", "liquidity_role"),
        ):
            counts = Counter(str(fill.get(field, "UNKNOWN")) for fill in fills if isinstance(fill, Mapping))
            for item, count in sorted(counts.items()):
                values.append(_available(self.d(key), count, unit="count", dimensions={dimension: item}))
        values.extend([
            _available(self.d("execution.reduce_only_fill_count"), sum(1 for fill in fills if isinstance(fill, Mapping) and fill.get("reduce_only") is True), unit="count"),
            _available(self.d("execution.active_frame_count"), len({fill.get("sequence") for fill in fills if isinstance(fill, Mapping)}), unit="count"),
        ])
        return values
