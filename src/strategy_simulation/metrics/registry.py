from grid_metrics import GridMetricCalculator
from metric_system import CoreMetricCalculator, MetricRegistry

from .calculator import BtcAccumulationMetricCalculator
from .contributor import StrategiesCoinMMetricInputContributor


class StrategySystemGridMetricCalculator(GridMetricCalculator):
    """Apply grid metrics only to the registered grid bridge."""

    def calculate(self, metric_input):
        if metric_input.provider_summary.get("strategy_type") != (
            "single-following-grid/v1"
        ):
            return ()
        return super().calculate(metric_input)


def build_metric_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_calculator(CoreMetricCalculator())
    registry.register_calculator(BtcAccumulationMetricCalculator())
    registry.register_calculator(StrategySystemGridMetricCalculator())
    registry.register_contributor(StrategiesCoinMMetricInputContributor())
    return registry
