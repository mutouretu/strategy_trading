"""Shared thin entry point for reproducible Viewer example scripts."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from ._bootstrap import PROJECT_ROOT
from .cli import main as experiment_main


def run_viewer_example(
    *,
    spec_name: str,
    default_output: Path,
    argv: Sequence[str] | None = None,
) -> int:
    """Delegate one fixed single-Run example to the generic experiment CLI."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--market-root", type=Path)
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--rerun-failed", action="store_true")
    parser.add_argument("--resume-interrupted", action="store_true")
    arguments = parser.parse_args(argv)

    command = [
        "run",
        str(PROJECT_ROOT / "experiments" / spec_name),
        "--export-viewer",
        str(arguments.output),
    ]
    if arguments.database is not None:
        command.extend(["--database", str(arguments.database)])
    if arguments.market_root is not None:
        command.extend(["--market-root", str(arguments.market_root)])
    if arguments.allow_dirty:
        command.append("--allow-dirty")
    if arguments.rerun_failed:
        command.append("--rerun-failed")
    if arguments.resume_interrupted:
        command.append("--resume-interrupted")
    return experiment_main(command)


__all__ = ["run_viewer_example"]
