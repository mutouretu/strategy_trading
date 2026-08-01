"""Run and explicitly export the canonical layered-grid experiment."""

from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from grid_experiments._bootstrap import SIMULATOR_ROOT
from grid_experiments.example_scripts import run_viewer_example


def main() -> int:
    return run_viewer_example(
        spec_name="layered_following_grid_baseline.json",
        default_output=(
            SIMULATOR_ROOT
            / "viewer"
            / "data"
            / "layered-following-grid-coinm-long-3y-seed-42.json"
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
