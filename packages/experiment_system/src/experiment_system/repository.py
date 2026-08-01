"""Storage contract for experiment lifecycle and result payloads."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from .json_values import JsonValue
from .market_data import MarketReference
from .models import (
    ExperimentManifest,
    ExperimentPlan,
    RunRecord,
    RunSpec,
    TracePurgeReport,
)


class ExperimentRepository(Protocol):
    def create_experiment(
        self,
        plan: ExperimentPlan,
        manifest: ExperimentManifest,
    ) -> None:
        """Persist one immutable plan and its PLANNED Run records."""

    def create_or_resume_experiment(
        self,
        plan: ExperimentPlan,
        manifest: ExperimentManifest,
    ) -> bool:
        """Create a plan or validate an identical stored plan.

        Return True when a new Experiment was created.
        """

    def start_run(
        self,
        run_spec: RunSpec,
        *,
        started_at: datetime,
    ) -> None:
        """Move one PLANNED Run to RUNNING."""

    def complete_run(
        self,
        run_spec: RunSpec,
        *,
        summary: Mapping[str, JsonValue],
        trace: Mapping[str, JsonValue],
        market_reference: MarketReference,
        finished_at: datetime,
        duration_seconds: float,
    ) -> None:
        """Atomically store success state, Summary, and Trace."""

    def fail_run(
        self,
        run_spec: RunSpec,
        *,
        error: Mapping[str, JsonValue],
        finished_at: datetime,
        duration_seconds: float,
    ) -> None:
        """Persist a failure without a success Summary or Trace."""

    def reset_failed_run(
        self,
        run_spec: RunSpec,
    ) -> None:
        """Move one FAILED Run back to PLANNED for explicit retry."""

    def recover_interrupted_run(
        self,
        run_spec: RunSpec,
    ) -> None:
        """Move one stale RUNNING Run back to PLANNED."""

    def get_run_record(self, run_id: str) -> RunRecord:
        """Load one compact lifecycle record."""

    def get_summary(self, run_id: str) -> dict[str, object]:
        """Load Summary without reading or decoding Trace."""

    def get_market_reference(self, run_id: str) -> MarketReference:
        """Load the external market reference for one successful Run."""

    def load_trace(self, run_id: str) -> dict[str, object]:
        """Load, verify, decompress, and decode Trace."""

    def get_manifest_document(self) -> dict[str, object]:
        """Load the immutable experiment manifest document."""

    def archive_run(
        self,
        run_id: str,
        *,
        archived_at: datetime,
        reason: str | None = None,
    ) -> RunRecord:
        """Protect a stored Trace from ordinary cleanup."""

    def preview_standard_trace_purge(self) -> TracePurgeReport:
        """Report purgeable STANDARD Trace payloads without mutation."""

    def purge_standard_traces(self) -> TracePurgeReport:
        """Atomically purge all eligible STANDARD Trace payloads."""
