from metric_system import CoreMetricCalculator, MetricRegistry

from .calculator import GridMetricCalculator
from .contributor import GridMetricInputContributor


def build_metric_registry() -> MetricRegistry:
    registry = MetricRegistry()
    registry.register_calculator(CoreMetricCalculator())
    registry.register_calculator(GridMetricCalculator())
    registry.register_contributor(GridMetricInputContributor())
    return registry
