from __future__ import annotations

import sys
from collections.abc import Sequence

from metric_system.cli import main as metric_main

from ..cli import participating_code_revisions
from .registry import build_metric_registry


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    revisions = {
        key: value.to_document()
        for key, value in participating_code_revisions().items()
    }
    return metric_main(
        arguments,
        registry=build_metric_registry(),
        evaluator_revisions=revisions,
    )
