"""Simulation integration for independently reusable strategy cores."""

from ._bootstrap import ensure_participating_packages

ensure_participating_packages()

from .registry import (  # noqa: E402
    SimulationStrategyBinding,
    SimulationStrategyBuildContext,
    SimulationStrategyPlugin,
    SimulationStrategyRegistry,
)

__all__ = [
    "SimulationStrategyBinding",
    "SimulationStrategyBuildContext",
    "SimulationStrategyPlugin",
    "SimulationStrategyRegistry",
]
