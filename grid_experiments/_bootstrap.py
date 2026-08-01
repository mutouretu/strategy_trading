"""Local-checkout import bootstrap for sibling simulator packages."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = PROJECT_ROOT.parent / "market_simulator"


def ensure_simulator_packages() -> None:
    """Make sibling editable sources importable without duplicating code."""

    package_paths = (
        SIMULATOR_ROOT / "packages" / "market_protocol" / "src",
        SIMULATOR_ROOT / "packages" / "market_simulator" / "src",
        SIMULATOR_ROOT / "packages" / "simulation_runtime" / "src",
        SIMULATOR_ROOT / "packages" / "experiment_system" / "src",
        SIMULATOR_ROOT / "packages" / "metric_system" / "src",
    )
    for package_path in reversed(package_paths):
        if package_path.exists():
            value = str(package_path)
            if value not in sys.path:
                sys.path.insert(0, value)
