"""Host the generic experiment CLI with the strategy Provider."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence

from experiment_system import CodeRevision, collect_code_revisions
from experiment_system.cli import main as experiment_main

from ._bootstrap import SIMULATOR_ROOT, WORKSPACE_ROOT
from .experiment_provider import build_provider_registry


def participating_code_revisions() -> dict[str, CodeRevision]:
    return collect_code_revisions(
        {
            "strategy_trading": WORKSPACE_ROOT,
        }
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    code_revisions: Mapping[str, CodeRevision] | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    command = arguments[0] if arguments else None
    if (
        command == "serve-results"
        and "--market-environment-root" not in arguments
    ):
        arguments.extend(
            [
                "--market-environment-root",
                str(SIMULATOR_ROOT / "market_environments"),
            ]
        )
    revisions = code_revisions
    if revisions is None and command in {"plan", "run"}:
        revisions = participating_code_revisions()
    return experiment_main(
        arguments,
        registry=build_provider_registry(),
        code_revisions=revisions,
    )
