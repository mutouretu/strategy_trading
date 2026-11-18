"""Import the shared strategy core from this monorepo checkout.

The grid service can still be started from its own directory before the
strategies-system distribution is installed. This bridge is temporary; new
runtime code should import the installed package directly.
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_strategy_core() -> None:
    source = Path(__file__).resolve().parents[2] / "strategies_system" / "src"
    if source.is_dir() and str(source) not in sys.path:
        sys.path.insert(0, str(source))
