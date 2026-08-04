from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
for package in ("market_protocol", "market_simulator", "simulation_runtime"):
    sys.path.insert(0, str(PROJECT_ROOT / "packages" / package / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from examples.deterministic_probe import run_probe  # noqa: E402
from simulation_runtime import simulation_result_to_document  # noqa: E402


def build_run() -> dict[str, object]:
    return simulation_result_to_document(
        run_probe(),
        run_id="deterministic-runtime-probe",
        interval="1d",
        source="fixed_bar_probe",
        manifest={
            "description": (
                "Deterministic intent resolution, explicit trades, and ledger probe"
            ),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=(
            PROJECT_ROOT
            / "viewer"
            / "data"
            / "deterministic-probe-run.json"
        ),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_run(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)


if __name__ == "__main__":
    main()
