"""Grid-specific host integration for the generic experiment system."""

from ._bootstrap import ensure_simulator_packages


ensure_simulator_packages()

from .provider import (  # noqa: E402
    GRID_SIMULATION_PROVIDER_V1,
    GridSimulationProvider,
    PreparedGridRun,
    build_registry,
)

__all__ = [
    "GRID_SIMULATION_PROVIDER_V1",
    "GridSimulationProvider",
    "PreparedGridRun",
    "build_registry",
]
