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


def hourly_break_even_activity(
    store: SQLiteStore, strategy_id: str, *, now: datetime | None = None,
) -> dict:
    """Pair hourly cycle counts with the first break-even sample in each hour."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    hour = 3600
    completed_end = int(current.timestamp()) // hour * hour
    started_at = store.get_first_started_at(strategy_id)
    periods: list[dict] = []
    if started_at is not None:
        first_start = max(
            completed_end - PERIOD_LIMIT * hour,
            int(datetime.fromisoformat(started_at).timestamp()) // hour * hour,
        )
        if first_start < completed_end:
            start_iso = datetime.fromtimestamp(first_start, timezone.utc).isoformat(timespec="seconds")
            end_iso = datetime.fromtimestamp(completed_end, timezone.utc).isoformat(timespec="seconds")
            counts = {start: 0 for start in range(first_start, completed_end, hour)}
            for event in store.list_cycle_closure_events(strategy_id, start_iso, end_iso):
                if event["event_type"] == "EXIT_ENDED" and event["payload"].get("action") != "cycle_closed":
                    continue
                bucket = int(datetime.fromisoformat(event["created_at"]).timestamp()) // hour * hour
                if bucket in counts:
                    counts[bucket] += 1
            samples = {
                int(datetime.fromisoformat(item["hour_start_utc"]).timestamp()): item
                for item in store.list_break_even_samples(strategy_id, start_iso, end_iso)
            }
            periods = [
                {
                    "started_at": datetime.fromtimestamp(start, BEIJING_TIME).isoformat(timespec="minutes"),
                    "completed_cycles": count,
                    "estimated_price": samples[start]["estimated_price"] if start in samples else None,
                    "sampled_at": samples[start]["observed_at"] if start in samples else None,
                }
                for start, count in counts.items()
            ]
    return {
        "strategy_id": strategy_id,
        "period_hours": 1,
        "timezone": "Asia/Shanghai",
        "periods": periods,
    }
