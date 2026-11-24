"""Four-hour activity from completed strategy cycles, without exchange calls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..infrastructure.sqlite_store import SQLiteStore


PERIOD_SECONDS = 4 * 60 * 60
PERIOD_LIMIT = 50
BEIJING_TIME = timezone(timedelta(hours=8))


def completed_cycle_activity(
    store: SQLiteStore, strategy_id: str, *, now: datetime | None = None,
) -> dict:
    """Return up to 50 finished, Beijing-aligned four-hour periods."""

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    completed_end = int(current.timestamp()) // PERIOD_SECONDS * PERIOD_SECONDS
    started_at = store.get_first_started_at(strategy_id)
    periods: list[dict] = []
    if started_at is not None:
        started = datetime.fromisoformat(started_at)
        first_start = max(
            completed_end - PERIOD_LIMIT * PERIOD_SECONDS,
            int(started.timestamp()) // PERIOD_SECONDS * PERIOD_SECONDS,
        )
        if first_start < completed_end:
            start_utc = datetime.fromtimestamp(first_start, timezone.utc)
            end_utc = datetime.fromtimestamp(completed_end, timezone.utc)
            counts = {
                start: 0 for start in range(first_start, completed_end, PERIOD_SECONDS)
            }
            for event in store.list_cycle_closure_events(
                strategy_id,
                start_utc.isoformat(timespec="seconds"),
                end_utc.isoformat(timespec="seconds"),
            ):
                if (
                    event["event_type"] == "EXIT_ENDED"
                    and event["payload"].get("action") != "cycle_closed"
                ):
                    continue
                bucket = (
                    int(datetime.fromisoformat(event["created_at"]).timestamp())
                    // PERIOD_SECONDS * PERIOD_SECONDS
                )
                if bucket in counts:
                    counts[bucket] += 1
            periods = [
                {
                    "started_at": datetime.fromtimestamp(
                        start, BEIJING_TIME,
                    ).isoformat(timespec="minutes"),
                    "completed_cycles": count,
                }
                for start, count in counts.items()
            ]
    return {
        "strategy_id": strategy_id,
        "period_hours": 4,
        "timezone": "Asia/Shanghai",
        "periods": periods,
    }
