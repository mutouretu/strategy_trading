"""Local monorepo imports for the participating Python packages."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
SIMULATOR_ROOT = WORKSPACE_ROOT / "market_simulator"
GRID_TRADING_ROOT = WORKSPACE_ROOT / "grid_trading"


def ensure_participating_packages() -> None:
    package_paths = (
        SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
        SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
        SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
        SIMULATOR_ROOT / "packages" / "experiment_system" / "src",
        SIMULATOR_ROOT / "packages" / "metric_system" / "src",
        GRID_TRADING_ROOT,
    )
    for package_path in reversed(package_paths):
        if package_path.exists():
            value = str(package_path)
            if value not in sys.path:
                sys.path.insert(0, value)
