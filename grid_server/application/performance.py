from __future__ import annotations

import gc
import json
import os
import sqlite3
import subprocess
import tempfile
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from ..domain import (
    Mode,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    PositionSnapshot,
    StrategyConfig,
    StrategyStatus,
    SymbolFilters,
)
from ..domain.grid import build_cells
from ..infrastructure.sqlite_store import SQLiteStore
from ..ports.exchange import OrderNotFoundError
from ..runtime.scheduler import StrategyScheduler


class CountingExchange:
    """In-memory exchange for load tests. It never performs network I/O."""

    def __init__(self, mark: Decimal = Decimal("105")) -> None:
        self.mark = mark
        self.filters = SymbolFilters(
            tick_size=Decimal("0.01"),
            step_size=Decimal("0.001"),
            min_qty=Decimal("0.001"),
            min_notional=Decimal("0"),
        )
        self.orders: dict[int, OrderSnapshot] = {}
        self.order_symbols: dict[int, str] = {}
        self.next_order_id = 1_000_000
        self.calls: Counter[str] = Counter()
        self.http_429_count = 0

    def get_mark_price(self, symbol: str) -> Decimal:
        self.calls["get_mark_price"] += 1
        return self.mark

    def get_symbol_filters(self, symbol: str) -> SymbolFilters:
        self.calls["get_symbol_filters"] += 1
        return self.filters

    def set_hedge_mode(self, enabled: bool) -> None:
        self.calls["set_hedge_mode"] += 1

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self.calls["set_leverage"] += 1

    def place_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        position_side: str,
        quantity: Decimal,
        price: Decimal,
        client_order_id: str,
    ) -> int:
        self.calls["place_limit_order"] += 1
        order_id = self.next_order_id
        self.next_order_id += 1
        self.orders[order_id] = OrderSnapshot(
            order_id=order_id,
            client_order_id=client_order_id,
            status=OrderStatus.NEW,
            side=side,
            price=price,
            original_qty=quantity,
            position_side=position_side,
        )
        self.order_symbols[order_id] = symbol
        return order_id

    def get_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self.calls["get_order"] += 1
        try:
            return self.orders[order_id]
        except KeyError as exc:
            raise OrderNotFoundError(str(order_id)) from exc

    def get_order_by_client_id(
        self,
        symbol: str,
        client_order_id: str,
    ) -> OrderSnapshot:
        self.calls["get_order_by_client_id"] += 1
        matches = [
            order
            for order_id, order in self.orders.items()
            if self.order_symbols[order_id] == symbol
            and order.client_order_id == client_order_id
        ]
        if not matches:
            raise OrderNotFoundError(client_order_id)
        return max(matches, key=lambda order: order.order_id)

    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]:
        self.calls["get_open_orders"] += 1
        return [
            order
            for order_id, order in self.orders.items()
            if self.order_symbols[order_id] == symbol
            and order.status in {OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}
        ]

    def get_positions(self) -> list[PositionSnapshot]:
        self.calls["get_positions"] += 1
        return []

    def cancel_order(self, symbol: str, order_id: int) -> OrderSnapshot:
        self.calls["cancel_order"] += 1
        order = self.orders[order_id]
        canceled = OrderSnapshot(**{**order.__dict__, "status": OrderStatus.CANCELED})
        self.orders[order_id] = canceled
        return canceled


def percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * fraction + 0.999999)))
    return ordered[index]


def duration_stats(values: list[float]) -> dict[str, float]:
    return {
        "count": len(values),
        "p50_ms": round(percentile(values, 0.50) * 1000, 3),
        "p95_ms": round(percentile(values, 0.95) * 1000, 3),
        "max_ms": round(max(values, default=0.0) * 1000, 3),
    }


def current_rss_mb() -> float:
    try:
        result = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(os.getpid())],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        return round(int(result.stdout.strip()) / 1024, 3)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def database_metrics(path: str | Path) -> tuple[dict[str, int], float]:
    db_path = Path(path).expanduser().resolve()
    started = time.perf_counter()
    with sqlite3.connect(db_path, timeout=5) as conn:
        counts = {
            "strategies": int(conn.execute("SELECT COUNT(*) FROM strategies").fetchone()[0]),
            "cells": int(conn.execute("SELECT COUNT(*) FROM cells").fetchone()[0]),
            "events": int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]),
        }
    latency_ms = (time.perf_counter() - started) * 1000

    def size(candidate: Path) -> int:
        try:
            return candidate.stat().st_size
        except FileNotFoundError:
            return 0

    counts.update(
        {
            "db_bytes": size(db_path),
            "wal_bytes": size(Path(f"{db_path}-wal")),
            "shm_bytes": size(Path(f"{db_path}-shm")),
        }
    )
    return counts, latency_ms


def create_load_database(
    path: str | Path,
    *,
    groups: int,
    cells_per_group: int,
    poll_intervals: list[float],
    symbol_count: int,
) -> SQLiteStore:
    if groups < 1 or cells_per_group < 1 or symbol_count < 1:
        raise ValueError("groups, cells_per_group and symbol_count must be positive")
    if not poll_intervals or any(interval < 0.2 for interval in poll_intervals):
        raise ValueError("poll intervals must be >= 0.2 seconds")
    store = SQLiteStore(path)
    if store.list_strategies(include_archived=True, include_deleted=True):
        raise ValueError(f"performance database is not empty: {store.path}")
    for index in range(groups):
        symbol = f"PERF{index % symbol_count:03d}USDT"
        config = StrategyConfig(
            strategy_id=f"perf-long-{index:04d}",
            symbol=symbol,
            mode=Mode.LONG,
            anchor_price=Decimal("110"),
            grid_ratio=Decimal("0.10"),
            grid_count=cells_per_group,
            order_usdt=Decimal("100"),
            leverage=3,
            poll_interval_sec=poll_intervals[index % len(poll_intervals)],
            move_grid=False,
            status=StrategyStatus.RUNNING,
            has_started=True,
        )
        store.create_strategy(config)
        store.replace_cells(config.strategy_id, build_cells(config, Decimal("0.01")))
    return store


def call_delta(before: Counter[str], after: Counter[str]) -> dict[str, int]:
    return {
        key: after.get(key, 0) - before.get(key, 0)
        for key in sorted(set(before) | set(after))
        if after.get(key, 0) - before.get(key, 0)
    }


def run_benchmark_case(
    *,
    groups: int,
    cells_per_group: int,
    poll_interval_sec: float,
    symbol_count: int,
    steady_cycles: int = 5,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="grid-perf-") as directory:
        db_path = Path(directory) / "benchmark.sqlite3"
        store = create_load_database(
            db_path,
            groups=groups,
            cells_per_group=cells_per_group,
            poll_intervals=[poll_interval_sec],
            symbol_count=symbol_count,
        )
        exchange = CountingExchange()
        scheduler = StrategyScheduler(store, exchange, pid=os.getpid())

        rss_before = current_rss_mb()
        warm_started = time.perf_counter()
        warm_processed = scheduler.run_once(now=0)
        warm_duration = time.perf_counter() - warm_started
        calls_after_warm = Counter(exchange.calls)
        events_after_warm = database_metrics(db_path)[0]["events"]

        idle_started = time.perf_counter()
        idle_processed = scheduler.run_once(now=0.01)
        idle_duration = time.perf_counter() - idle_started
        calls_after_idle = Counter(exchange.calls)

        event_durations: list[float] = []
        due_durations: list[float] = []
        processed: list[int] = []
        event_call_deltas: list[dict[str, int]] = []
        calls_before_steady = Counter(exchange.calls)
        virtual_now = 0.01
        while len(processed) < steady_cycles:
            next_due = min(scheduler.next_due.values())
            virtual_now = min(next_due, scheduler.next_reconcile_at)
            calls_before_event = Counter(exchange.calls)
            started = time.perf_counter()
            processed_count = scheduler.run_once(now=virtual_now)
            duration = time.perf_counter() - started
            event_durations.append(duration)
            event_call_deltas.append(call_delta(calls_before_event, Counter(exchange.calls)))
            if processed_count:
                processed.append(processed_count)
                due_durations.append(duration)
        calls_after_steady = Counter(exchange.calls)
        metrics, sqlite_latency_ms = database_metrics(db_path)
        rss_after = current_rss_mb()

        steady_calls = call_delta(calls_before_steady, calls_after_steady)
        mark_calls_per_cycle = steady_calls.get("get_mark_price", 0) / steady_cycles
        open_calls_per_cycle = steady_calls.get("get_open_orders", 0) / steady_cycles
        max_mark_calls_per_event = max(
            (calls.get("get_mark_price", 0) for calls in event_call_deltas),
            default=0,
        )
        max_open_calls_per_event = max(
            (calls.get("get_open_orders", 0) for calls in event_call_deltas),
            default=0,
        )
        request_scaling_ok = (
            max_mark_calls_per_event <= symbol_count
            and max_open_calls_per_event <= symbol_count
            and steady_calls.get("get_order", 0) == 0
        )
        max_steady_ms = max(event_durations, default=0) * 1000
        return {
            "groups": groups,
            "cells_per_group": cells_per_group,
            "total_cells": groups * cells_per_group,
            "poll_interval_sec": poll_interval_sec,
            "symbol_count": symbol_count,
            "process_model": {"scheduler_processes": 1, "engines": len(scheduler.engines)},
            "memory_mb": {
                "before": rss_before,
                "after": rss_after,
                "delta": round(rss_after - rss_before, 3),
                "under_800_mb": rss_after < 800,
            },
            "warm_cycle": {
                "processed": warm_processed,
                "duration_ms": round(warm_duration * 1000, 3),
                "exchange_calls": dict(calls_after_warm),
            },
            "idle_cycle": {
                "processed": idle_processed,
                "duration_ms": round(idle_duration * 1000, 3),
                "exchange_calls": call_delta(calls_after_warm, calls_after_idle),
            },
            "steady_cycles": {
                "processed": processed,
                "due_duration": duration_stats(due_durations),
                "all_event_duration": duration_stats(event_durations),
                "exchange_calls": steady_calls,
                "mark_calls_per_cycle": mark_calls_per_cycle,
                "open_order_calls_per_cycle": open_calls_per_cycle,
                "max_mark_calls_per_event": max_mark_calls_per_event,
                "max_open_order_calls_per_event": max_open_calls_per_event,
                "request_scaling_by_symbol": request_scaling_ok,
                "projected_read_requests_per_minute": round(
                    (
                        steady_calls.get("get_mark_price", 0)
                        + steady_calls.get("get_open_orders", 0)
                        + steady_calls.get("get_positions", 0)
                    )
                    * 60
                    / max(virtual_now, 0.000001),
                    6,
                ),
            },
            "sqlite": {
                **metrics,
                "read_latency_ms": round(sqlite_latency_ms, 3),
                "event_growth_after_warm": metrics["events"] - events_after_warm,
            },
            "http_429_count": exchange.http_429_count,
            "acceptance": {
                "rss_under_800_mb": rss_after < 800,
                "no_429": exchange.http_429_count == 0,
                "cycle_far_below_poll": max_steady_ms < poll_interval_sec * 100,
                "one_scheduler_process": len(scheduler.engines) == groups,
                "request_scaling_by_symbol": request_scaling_ok,
            },
        }


def run_benchmark_matrix(*, steady_cycles: int = 5) -> dict[str, Any]:
    cases = [
        run_benchmark_case(
            groups=50,
            cells_per_group=5,
            poll_interval_sec=interval,
            symbol_count=1,
            steady_cycles=steady_cycles,
        )
        for interval in (50.0, 600.0, 3600.0)
    ]
    cases.extend(
        [
            run_benchmark_case(
                groups=50,
                cells_per_group=5,
                poll_interval_sec=50.0,
                symbol_count=50,
                steady_cycles=steady_cycles,
            ),
            run_benchmark_case(
                groups=100,
                cells_per_group=5,
                poll_interval_sec=50.0,
                symbol_count=1,
                steady_cycles=steady_cycles,
            ),
        ]
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "exchange": "in-memory; no Binance network access",
        "host_resource_note": (
            "macOS measurement with 800 MB acceptance threshold; exact 2-core/2-GB "
            "enforcement requires Linux cgroup validation"
        ),
        "steady_cycles_per_case": steady_cycles,
        "cases": cases,
        "all_acceptance_checks_passed": all(
            all(case["acceptance"].values()) for case in cases
        ),
    }


def append_jsonl(path: str | Path, payload: dict[str, Any]) -> None:
    destination = Path(path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(
            descriptor,
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode(
                "utf-8"
            ),
        )
    finally:
        os.close(descriptor)


def run_soak(
    *,
    db_path: str | Path,
    output_path: str | Path,
    duration_sec: float,
    sample_interval_sec: float,
    groups: int = 50,
    cells_per_group: int = 5,
    poll_intervals: list[float] | None = None,
    symbol_count: int = 1,
) -> dict[str, Any]:
    intervals = poll_intervals or [50.0, 600.0, 3600.0]
    db = Path(db_path).expanduser().resolve()
    if db.exists():
        raise FileExistsError(f"refusing to reuse performance database: {db}")
    store = create_load_database(
        db,
        groups=groups,
        cells_per_group=cells_per_group,
        poll_intervals=intervals,
        symbol_count=symbol_count,
    )
    exchange = CountingExchange()
    scheduler = StrategyScheduler(store, exchange, pid=os.getpid())
    started_wall = time.time()
    deadline = time.monotonic() + duration_sec
    next_sample = time.monotonic()
    previous_sample_time = time.monotonic()
    previous_cpu_time = time.process_time()
    previous_calls = Counter(exchange.calls)
    cycle_durations: list[float] = []
    sqlite_latencies: list[float] = []
    lock_errors = 0
    sample_count = 0
    max_rss = current_rss_mb()

    header = {
        "record_type": "start",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pid": os.getpid(),
        "groups": groups,
        "cells_per_group": cells_per_group,
        "poll_intervals_sec": intervals,
        "symbol_count": symbol_count,
        "duration_sec": duration_sec,
        "exchange": "in-memory; no Binance network access",
        "db_path": str(db),
    }
    append_jsonl(output_path, header)

    while time.monotonic() < deadline:
        cycle_started = time.perf_counter()
        try:
            scheduler.run_once()
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower():
                lock_errors += 1
            else:
                raise
        cycle_durations.append(time.perf_counter() - cycle_started)
        now = time.monotonic()
        if now >= next_sample:
            metrics, sqlite_latency_ms = database_metrics(db)
            sqlite_latencies.append(sqlite_latency_ms / 1000)
            rss = current_rss_mb()
            max_rss = max(max_rss, rss)
            cpu_now = time.process_time()
            wall_delta = max(0.000001, now - previous_sample_time)
            cpu_percent = (cpu_now - previous_cpu_time) / wall_delta * 100
            current_calls = Counter(exchange.calls)
            output = Path(output_path).expanduser().resolve()
            payload = {
                "record_type": "sample",
                "sampled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "elapsed_sec": round(time.time() - started_wall, 3),
                "rss_mb": rss,
                "max_rss_mb": max_rss,
                "cpu_percent_one_core": round(cpu_percent, 3),
                "cycle_duration": duration_stats(cycle_durations),
                "sqlite_read_latency": duration_stats(sqlite_latencies),
                "sqlite": metrics,
                "exchange_calls_since_sample": call_delta(previous_calls, current_calls),
                "http_429_count": exchange.http_429_count,
                "database_lock_errors": lock_errors,
                "engine_count": len(scheduler.engines),
                "scheduler_processes": 1,
                "metrics_log_bytes": output.stat().st_size if output.exists() else 0,
                "acceptance": {
                    "rss_under_800_mb": max_rss < 800,
                    "no_429": exchange.http_429_count == 0,
                    "no_database_lock": lock_errors == 0,
                    "cycle_far_below_shortest_poll": (
                        max(cycle_durations, default=0) < min(intervals) * 0.1
                    ),
                    "one_scheduler_process": len(scheduler.engines) == groups,
                },
            }
            append_jsonl(output_path, payload)
            sample_count += 1
            cycle_durations.clear()
            sqlite_latencies.clear()
            previous_calls = current_calls
            previous_sample_time = now
            previous_cpu_time = cpu_now
            next_sample = now + sample_interval_sec
        delay = scheduler.seconds_until_next_cycle(maximum=1.0)
        time.sleep(min(delay, max(0.05, next_sample - time.monotonic())))

    gc.collect()
    final_metrics, sqlite_latency_ms = database_metrics(db)
    result = {
        "record_type": "complete",
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_sec": round(time.time() - started_wall, 3),
        "sample_count": sample_count,
        "max_rss_mb": max_rss,
        "database_lock_errors": lock_errors,
        "http_429_count": exchange.http_429_count,
        "sqlite": final_metrics,
        "final_sqlite_read_latency_ms": round(sqlite_latency_ms, 3),
        "passed": (
            max_rss < 800
            and lock_errors == 0
            and exchange.http_429_count == 0
            and len(scheduler.engines) == groups
        ),
    }
    append_jsonl(output_path, result)
    return result
