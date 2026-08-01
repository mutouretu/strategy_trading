from metric_system import CoreMetricCalculator, MetricRegistry

from .calculator import BtcAccumulationMetricCalculator
from .coinm_contributor import StrategiesCoinMMetricInputContributor
from .grid import GridMetricCalculator


class StrategySystemGridMetricCalculator(GridMetricCalculator):
    """Apply grid metrics only to registered grid strategies."""

    def calculate(self, metric_input):
        if metric_input.provider_summary.get("strategy_type") not in {
            "single-following-grid/v1",
            "layered-following-grid/v1",
        }:
            return ()
        return super().calculate(metric_input)


def build_metric_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_calculator(CoreMetricCalculator())
    registry.register_calculator(BtcAccumulationMetricCalculator())
    registry.register_calculator(StrategySystemGridMetricCalculator())
    registry.register_contributor(StrategiesCoinMMetricInputContributor())
    return registry
