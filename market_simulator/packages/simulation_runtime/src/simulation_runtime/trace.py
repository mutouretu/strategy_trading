from __future__ import annotations

from typing import Protocol

from .models import IntentSnapshot


class SimulationTracePort(Protocol):
    """Expose strategy-owned intents for read-only lifecycle reporting."""

    def visible_intents(self) -> tuple[IntentSnapshot, ...]: ...
