from grid_metrics import GridMetricCalculator
from metric_system import CoreMetricCalculator, MetricRegistry

from .calculator import BtcAccumulationMetricCalculator
from .contributor import StrategiesCoinMMetricInputContributor


def build_metric_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_calculator(CoreMetricCalculator())
    registry.register_calculator(BtcAccumulationMetricCalculator())
    registry.register_calculator(GridMetricCalculator())
    registry.register_contributor(StrategiesCoinMMetricInputContributor())
    return registry
