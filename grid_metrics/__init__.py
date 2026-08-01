"""Grid-owned metric extensions for the generic metric system."""

from grid_experiments._bootstrap import ensure_simulator_packages


ensure_simulator_packages()

from .calculator import GRID_METRIC_SET, GridMetricCalculator  # noqa: E402
from .contributor import GridMetricInputContributor  # noqa: E402
from .registry import build_metric_registry  # noqa: E402

__all__ = [
    "GRID_METRIC_SET",
    "GridMetricCalculator",
    "GridMetricInputContributor",
    "build_metric_registry",
]
