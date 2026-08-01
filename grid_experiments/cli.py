"""Thin grid host for the generic experiment-system CLI."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

from experiment_system import (
    CodeRevision,
    collect_code_revisions,
)
from experiment_system.cli import main as experiment_main

from ._bootstrap import PROJECT_ROOT, SIMULATOR_ROOT
from .provider import build_registry


def participating_code_revisions() -> dict[str, CodeRevision]:
    return collect_code_revisions(
        {
            "market_simulator": SIMULATOR_ROOT,
            "grid_trading": PROJECT_ROOT,
        }
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    code_revisions: Mapping[str, CodeRevision] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else None
    revisions = code_revisions
    if revisions is None and command in {"plan", "run"}:
        revisions = participating_code_revisions()
    return experiment_main(
        arguments,
        registry=build_registry(),
        code_revisions=revisions,
    )
