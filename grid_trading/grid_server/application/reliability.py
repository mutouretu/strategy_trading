from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from ..domain import OrderSnapshot, PositionSnapshot


ACTIVE_STATUSES = {"starting", "running", "error"}
MANAGED_ORDER_PREFIX = "wg-"


class ReadOnlyExchange(Protocol):
    def get_open_orders(self, symbol: str) -> list[OrderSnapshot]: ...

    def get_positions(self) -> list[PositionSnapshot]: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def decimal(value: object) -> Decimal:
    return Decimal(str(value or "0"))


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def managed_client_id(strategy_id: str, cell_id: str, role: str) -> str:
    strategy_tag = hashlib.sha1(strategy_id.encode("utf-8")).hexdigest()[:8]
    return f"wg-{strategy_tag}-{cell_id[:8]}-{role}"


def _readonly_connection(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=5,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def read_database(path: str | Path) -> dict[str, Any]:
    """Read one consistent SQLite snapshot without initializing or mutating it."""

    db_path = Path(path).expanduser().resolve()
    with _readonly_connection(db_path) as conn:
        conn.execute("BEGIN")
        strategies = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM strategies WHERE deleted_at IS NULL ORDER BY strategy_id"
            )
        ]
        cells = [
            dict(row)
            for row in conn.execute(
                """
                SELECT c.*
                FROM cells AS c
                JOIN strategies AS s ON s.strategy_id = c.strategy_id
                WHERE s.deleted_at IS NULL
                ORDER BY c.strategy_id, c.cell_index
                """
            )
        ]
        runtimes = [
            dict(row)
            for row in conn.execute(
                """
                SELECT r.*
                FROM runtime AS r
                JOIN strategies AS s ON s.strategy_id = r.strategy_id
                WHERE s.deleted_at IS NULL
                ORDER BY r.strategy_id
                """
            )
        ]
        pools = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM position_pools ORDER BY symbol, position_side"
            )
        ]
        action_counts = {
            str(row["status"]): int(row["count"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS count FROM cell_actions GROUP BY status"
            )
        }
        table_counts = {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "strategies",
                "cells",
                "events",
                "runtime",
                "position_pools",
                "cell_actions",
            )
        }
        latest_event_id = int(
            conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]
        )
        conn.rollback()

    return {
        "path": str(db_path),
        "strategies": strategies,
        "cells": cells,
        "runtimes": runtimes,
        "position_pools": pools,
        "action_counts": action_counts,
        "table_counts": table_counts,
        "latest_event_id": latest_event_id,
    }


def database_sizes(path: str | Path) -> dict[str, int]:
    db_path = Path(path).expanduser().resolve()

    def size(candidate: Path) -> int:
        try:
            return candidate.stat().st_size
        except FileNotFoundError:
            return 0

    db_size = size(db_path)
    wal_size = size(Path(f"{db_path}-wal"))
    shm_size = size(Path(f"{db_path}-shm"))
    return {
        "db_bytes": db_size,
        "wal_bytes": wal_size,
        "shm_bytes": shm_size,
        "total_bytes": db_size + wal_size + shm_size,
    }


def _expected_order(
    cell: dict[str, Any], symbol: str, mode: str, role: str
) -> dict[str, Any]:
    is_long = mode == "long"
    if role == "entry":
        side = "BUY" if is_long else "SELL"
        price = cell["buy_price"] if is_long else cell["sell_price"]
        order_id = cell.get("entry_order_id")
        stored_client_id = str(cell.get("entry_client_id") or "")
        active = cell["stage"] == "pending_entry"
        role_tag = "e"
    else:
        side = "SELL" if is_long else "BUY"
        price = cell["sell_price"] if is_long else cell["buy_price"]
        order_id = cell.get("exit_order_id")
        stored_client_id = str(cell.get("exit_client_id") or "")
        active = cell["stage"] == "pending_exit"
        role_tag = "x"
    return {
        "strategy_id": cell["strategy_id"],
        "symbol": symbol,
        "cell_id": cell["cell_id"],
        "cell_index": int(cell["cell_index"]),
        "role": role,
        "active": active,
        "order_id": int(order_id) if order_id is not None else None,
        "stored_client_id": stored_client_id,
        "derived_client_id": managed_client_id(
            str(cell["strategy_id"]), str(cell["cell_id"]), role_tag
        ),
        "side": side,
        "position_side": "LONG" if is_long else "SHORT",
        "price": decimal(price),
    }


def analyze_state(
    database: dict[str, Any],
    open_orders: dict[str, list[OrderSnapshot]],
    positions: list[PositionSnapshot],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare a read-only exchange snapshot with the SQLite snapshot."""

    current = now or utc_now()
    strategies = database["strategies"]
    cells = database["cells"]
    runtimes = {row["strategy_id"]: row for row in database["runtimes"]}
    configs = {row["strategy_id"]: row for row in strategies}
    symbols = sorted({str(row["symbol"]) for row in strategies})

    logical: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for cell in cells:
        config = configs[cell["strategy_id"]]
        side = "LONG" if config["mode"] == "long" else "SHORT"
        logical[(str(config["symbol"]), side)] += decimal(cell["open_qty"])

    actual: dict[tuple[str, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for position in positions:
        if position.symbol in symbols:
            actual[(position.symbol, position.position_side)] += abs(position.quantity)

    position_rows: list[dict[str, str]] = []
    shortages = 0
    unassigned = 0
    for key in sorted(set(logical) | set(actual)):
        logical_qty = logical[key]
        actual_qty = actual[key]
        shortage_qty = max(Decimal("0"), logical_qty - actual_qty)
        unassigned_qty = max(Decimal("0"), actual_qty - logical_qty)
        shortages += int(shortage_qty > 0)
        unassigned += int(unassigned_qty > 0)
        position_rows.append(
            {
                "symbol": key[0],
                "position_side": key[1],
                "actual_qty": decimal_text(actual_qty),
                "logical_qty": decimal_text(logical_qty),
                "shortage_qty": decimal_text(shortage_qty),
                "unassigned_qty": decimal_text(unassigned_qty),
            }
        )

    role_rows: list[dict[str, Any]] = []
    for cell in cells:
        config = configs[cell["strategy_id"]]
        role_rows.append(
            _expected_order(cell, str(config["symbol"]), str(config["mode"]), "entry")
        )
        role_rows.append(
            _expected_order(cell, str(config["symbol"]), str(config["mode"]), "exit")
        )

    by_order_id: dict[tuple[str, int], dict[str, Any]] = {}
    by_client_id: dict[str, dict[str, Any]] = {}
    derived_by_client_id: dict[str, dict[str, Any]] = {}
    db_order_ids: list[tuple[str, int]] = []
    db_client_ids: list[str] = []
    for role in role_rows:
        if role["order_id"] is not None:
            order_key = (role["symbol"], role["order_id"])
            db_order_ids.append(order_key)
            by_order_id[order_key] = role
        if role["stored_client_id"]:
            db_client_ids.append(role["stored_client_id"])
            by_client_id[role["stored_client_id"]] = role
        derived_by_client_id[role["derived_client_id"]] = role

    duplicate_db_order_ids = sorted(
        f"{symbol}:{order_id}"
        for (symbol, order_id), count in Counter(db_order_ids).items()
        if count > 1
    )
    duplicate_db_client_ids = sorted(
        client_id for client_id, count in Counter(db_client_ids).items() if count > 1
    )

    platform_orders = [
        (symbol, order) for symbol in symbols for order in open_orders.get(symbol, [])
    ]
    platform_order_ids = Counter(
        (symbol, order.order_id) for symbol, order in platform_orders
    )
    platform_client_ids = Counter(
        order.client_order_id
        for _symbol, order in platform_orders
        if order.client_order_id
    )
    duplicate_platform_order_ids = sorted(
        f"{symbol}:{order_id}"
        for (symbol, order_id), count in platform_order_ids.items()
        if count > 1
    )
    duplicate_platform_client_ids = sorted(
        client_id for client_id, count in platform_client_ids.items() if count > 1
    )

    matched_roles: set[tuple[str, str, str]] = set()
    unknown_managed: list[dict[str, Any]] = []
    recoverable: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    stage_conflicts: list[dict[str, Any]] = []
    external_orders = 0
    for symbol, order in platform_orders:
        role = (
            by_order_id.get((symbol, order.order_id))
            or by_client_id.get(order.client_order_id)
            or derived_by_client_id.get(order.client_order_id)
        )
        if role is None:
            if order.client_order_id.startswith(MANAGED_ORDER_PREFIX):
                unknown_managed.append(
                    {
                        "order_id": order.order_id,
                        "client_order_id": order.client_order_id,
                    }
                )
            else:
                external_orders += 1
            continue

        identity = (role["strategy_id"], role["cell_id"], role["role"])
        matched_roles.add(identity)
        if role["order_id"] != order.order_id or not role["stored_client_id"]:
            recoverable.append(
                {
                    "strategy_id": role["strategy_id"],
                    "cell_id": role["cell_id"],
                    "role": role["role"],
                    "platform_order_id": order.order_id,
                    "database_order_id": role["order_id"],
                    "client_order_id": order.client_order_id,
                }
            )
        if not role["active"]:
            stage_conflicts.append(
                {
                    "strategy_id": role["strategy_id"],
                    "cell_id": role["cell_id"],
                    "role": role["role"],
                    "order_id": order.order_id,
                }
            )
        details: list[str] = []
        if order.side.value != role["side"]:
            details.append(f"side:{order.side.value}!={role['side']}")
        if order.position_side != role["position_side"]:
            details.append(
                f"position_side:{order.position_side}!={role['position_side']}"
            )
        if order.price != role["price"]:
            details.append(f"price:{order.price}!={role['price']}")
        if details:
            mismatches.append(
                {
                    "strategy_id": role["strategy_id"],
                    "cell_id": role["cell_id"],
                    "role": role["role"],
                    "order_id": order.order_id,
                    "details": details,
                }
            )

    missing_expected: list[dict[str, Any]] = []
    for role in role_rows:
        identity = (role["strategy_id"], role["cell_id"], role["role"])
        if role["active"] and identity not in matched_roles:
            missing_expected.append(
                {
                    "strategy_id": role["strategy_id"],
                    "cell_id": role["cell_id"],
                    "role": role["role"],
                    "database_order_id": role["order_id"],
                    "client_order_id": role["stored_client_id"]
                    or role["derived_client_id"],
                }
            )

    heartbeat_rows: list[dict[str, Any]] = []
    stale_heartbeats = 0
    dead_runtime_pids: set[int] = set()
    for config in strategies:
        if not config["has_started"] or config["status"] not in ACTIVE_STATUSES:
            continue
        runtime = runtimes.get(config["strategy_id"], {})
        heartbeat = parse_time(runtime.get("heartbeat_at"))
        age = (current - heartbeat).total_seconds() if heartbeat else None
        poll = float(config["poll_interval_sec"])
        stale_after = max(poll * 3, 180.0)
        stale = age is None or age > stale_after
        stale_heartbeats += int(stale)
        pid = int(runtime.get("pid") or 0)
        if pid and not pid_alive(pid):
            dead_runtime_pids.add(pid)
        heartbeat_rows.append(
            {
                "strategy_id": config["strategy_id"],
                "poll_interval_sec": poll,
                "heartbeat_age_sec": round(age, 3) if age is not None else None,
                "stale_after_sec": stale_after,
                "stale": stale,
                "pid": pid,
                "last_error": str(runtime.get("last_error") or ""),
            }
        )

    manual_review = sum(cell["stage"] == "manual_review" for cell in cells)
    strategy_errors = sum(row["status"] == "error" for row in strategies)
    runtime_errors = sum(bool(row.get("last_error")) for row in database["runtimes"])
    pool_status_counts = Counter(str(row["status"]) for row in database["position_pools"])
    unhealthy_pool_rows = sum(
        int(decimal(row["shortage_qty"]) > 0 or decimal(row["unassigned_qty"]) > 0)
        for row in database["position_pools"]
    )

    return {
        "symbols": symbols,
        "strategy_count": len(strategies),
        "active_strategy_count": len(heartbeat_rows),
        "cell_count": len(cells),
        "positions": position_rows,
        "orders": {
            "platform_open": len(platform_orders),
            "external_open": external_orders,
            "unknown_managed": unknown_managed,
            "missing_expected": missing_expected,
            "recoverable_by_client_id": recoverable,
            "attribute_mismatches": mismatches,
            "stage_conflicts": stage_conflicts,
            "duplicate_database_order_ids": duplicate_db_order_ids,
            "duplicate_database_client_ids": duplicate_db_client_ids,
            "duplicate_platform_order_ids": duplicate_platform_order_ids,
            "duplicate_platform_client_ids": duplicate_platform_client_ids,
        },
        "heartbeats": heartbeat_rows,
        "database_state": {
            "manual_review_cells": manual_review,
            "strategy_errors": strategy_errors,
            "runtime_errors": runtime_errors,
            "stale_heartbeats": stale_heartbeats,
            "dead_runtime_pids": sorted(dead_runtime_pids),
            "position_pool_status_counts": dict(sorted(pool_status_counts.items())),
            "unhealthy_stored_position_pools": unhealthy_pool_rows,
            "cell_action_counts": database["action_counts"],
        },
        "anomaly_counts": {
            "position_shortage": shortages,
            "position_unassigned": unassigned,
            "unknown_managed_order": len(unknown_managed),
            "missing_expected_order": len(missing_expected),
            "recoverable_order_reference": len(recoverable),
            "order_attribute_mismatch": len(mismatches),
            "order_stage_conflict": len(stage_conflicts),
            "duplicate_order": sum(
                len(items)
                for items in (
                    duplicate_db_order_ids,
                    duplicate_db_client_ids,
                    duplicate_platform_order_ids,
                    duplicate_platform_client_ids,
                )
            ),
            "manual_review": manual_review,
            "strategy_error": strategy_errors,
            "runtime_error": runtime_errors,
            "stale_heartbeat": stale_heartbeats,
            "failed_cell_action": int(database["action_counts"].get("failed", 0)),
        },
    }


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def inspect_scheduler_processes(
    db_path: str | Path,
    runtime_pids: set[int],
    pid_file: str | Path | None = None,
) -> dict[str, Any]:
    resolved_db = Path(db_path).expanduser().resolve()
    discovered: list[dict[str, Any]] = []
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,etime=,command="],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            pid_text, elapsed, command = parts
            if "grid_server.runtime.scheduler" not in command and "grid_server.scheduler" not in command:
                continue
            exact_db = False
            try:
                argv = shlex.split(command)
                index = argv.index("--db")
                configured = Path(argv[index + 1]).expanduser()
                exact_db = configured.is_absolute() and configured.resolve() == resolved_db
            except (ValueError, IndexError):
                pass
            discovered.append(
                {"pid": int(pid_text), "elapsed": elapsed, "exact_database": exact_db}
            )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": str(exc), "processes": [], "candidate_pids": []}

    pid_file_pid = 0
    if pid_file:
        try:
            pid_file_pid = int(Path(pid_file).read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            pass
    candidate_pids = {
        item["pid"] for item in discovered if item["exact_database"]
    } | {pid for pid in runtime_pids if pid_alive(pid)}
    if pid_file_pid and pid_alive(pid_file_pid):
        candidate_pids.add(pid_file_pid)
    return {
        "processes": discovered,
        "candidate_pids": sorted(candidate_pids),
        "pid_file": str(Path(pid_file).expanduser().resolve()) if pid_file else None,
        "pid_file_pid": pid_file_pid or None,
    }


def http_probe(url: str | None, *, timeout: float = 5.0) -> dict[str, Any]:
    if not url:
        return {"checked": False}
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "grid-reliability-probe/1"})
        with urlopen(request, timeout=timeout) as response:
            response.read(256)
            status = int(response.status)
        return {
            "checked": True,
            "ok": 200 <= status < 400,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        return {
            "checked": True,
            "ok": False,
            "error": str(exc),
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
        }


def api_strategy_alignment_probe(
    api_url: str | None,
    expected_strategy_ids: set[str],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    if not api_url:
        return {"checked": False}
    url = f"{api_url.rstrip('/')}/strategies?include_archived=true"
    started = time.monotonic()
    try:
        request = Request(url, headers={"User-Agent": "grid-reliability-probe/1"})
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, list):
            raise ValueError("strategy endpoint did not return a JSON list")
        actual_ids = {
            str(item["strategy_id"])
            for item in payload
            if isinstance(item, dict) and item.get("strategy_id")
        }
        missing = sorted(expected_strategy_ids - actual_ids)
        extra = sorted(actual_ids - expected_strategy_ids)
        return {
            "checked": True,
            "ok": 200 <= status < 400 and not missing and not extra,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
            "database_strategy_count": len(expected_strategy_ids),
            "api_strategy_count": len(actual_ids),
            "missing_in_api": missing,
            "extra_in_api": extra,
        }
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "checked": True,
            "ok": False,
            "error": str(exc),
            "latency_ms": round((time.monotonic() - started) * 1000, 3),
        }


def build_alerts(sample: dict[str, Any]) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []

    def add(severity: str, code: str, message: str) -> None:
        alerts.append({"severity": severity, "code": code, "message": message})

    counts = sample.get("analysis", {}).get("anomaly_counts", {})
    critical_codes = {
        "position_shortage",
        "unknown_managed_order",
        "duplicate_order",
        "order_attribute_mismatch",
        "manual_review",
        "strategy_error",
        "runtime_error",
        "failed_cell_action",
    }
    for code, count in counts.items():
        if count:
            severity = "critical" if code in critical_codes else "warning"
            add(severity, code, f"{code}={count}")

    process = sample.get("process", {})
    active = int(sample.get("analysis", {}).get("active_strategy_count", 0))
    candidates = process.get("candidate_pids", [])
    if active and len(candidates) == 0:
        add("critical", "scheduler_missing", "active strategies exist but no scheduler PID is alive")
    if len(candidates) > 1:
        add("critical", "scheduler_duplicate", f"scheduler candidate_pids={candidates}")

    for service_name in ("api", "api_database_alignment", "streamlit"):
        result = sample.get("http", {}).get(service_name, {})
        if result.get("checked") and not result.get("ok"):
            severity = "critical" if service_name == "api_database_alignment" else "warning"
            code = (
                "api_database_mismatch"
                if service_name == "api_database_alignment"
                else f"{service_name}_unhealthy"
            )
            add(severity, code, str(result.get("error") or result))

    for error in sample.get("errors", []):
        add("critical", "probe_error", error)
    return alerts


def collect_sample(
    db_path: str | Path,
    exchange: ReadOnlyExchange,
    *,
    api_url: str | None = None,
    streamlit_url: str | None = None,
    pid_file: str | Path | None = None,
    label: str = "",
    timeout: float = 5.0,
) -> dict[str, Any]:
    started = time.monotonic()
    now = utc_now()
    sample: dict[str, Any] = {
        "schema_version": 1,
        "sampled_at": iso_text(now),
        "label": label,
        "errors": [],
    }
    expected_strategy_ids: set[str] = set()
    try:
        database = read_database(db_path)
        sample["database"] = {
            "path": database["path"],
            "sizes": database_sizes(db_path),
            "table_counts": database["table_counts"],
            "latest_event_id": database["latest_event_id"],
        }
        symbols = sorted({str(row["symbol"]) for row in database["strategies"]})
        expected_strategy_ids = {
            str(row["strategy_id"]) for row in database["strategies"]
        }
        positions = exchange.get_positions()
        bulk_open_orders = getattr(exchange, "get_open_orders_by_symbol", None)
        if callable(bulk_open_orders):
            grouped_orders = bulk_open_orders(set(symbols))
            open_orders = {symbol: grouped_orders.get(symbol, []) for symbol in symbols}
        else:
            open_orders = {symbol: exchange.get_open_orders(symbol) for symbol in symbols}
        sample["analysis"] = analyze_state(database, open_orders, positions, now=now)
        runtime_pids = {
            int(row["pid"])
            for row in database["runtimes"]
            if row.get("pid")
            and row["strategy_id"]
            in {
                strategy["strategy_id"]
                for strategy in database["strategies"]
                if strategy["has_started"] and strategy["status"] in ACTIVE_STATUSES
            }
        }
        sample["process"] = inspect_scheduler_processes(db_path, runtime_pids, pid_file)
    except Exception as exc:
        sample["errors"].append(f"{type(exc).__name__}: {exc}")

    api_health_url = f"{api_url.rstrip('/')}/health" if api_url else None
    sample["http"] = {
        "api": http_probe(api_health_url, timeout=timeout),
        "api_database_alignment": api_strategy_alignment_probe(
            api_url, expected_strategy_ids, timeout=timeout
        ),
        "streamlit": http_probe(streamlit_url, timeout=timeout),
    }
    sample["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    sample["alerts"] = build_alerts(sample)
    severities = {alert["severity"] for alert in sample["alerts"]}
    sample["overall"] = (
        "critical" if "critical" in severities else "warning" if "warning" in severities else "ok"
    )
    return sample


def append_jsonl(path: str | Path, sample: dict[str, Any]) -> None:
    output = Path(path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor = os.open(output, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


def summarize_jsonl(path: str | Path) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    invalid_lines = 0
    with Path(path).expanduser().open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                samples.append(json.loads(line))
            except json.JSONDecodeError:
                invalid_lines += 1
    if not samples:
        raise ValueError("no valid reliability samples")

    status_counts = Counter(str(sample.get("overall", "unknown")) for sample in samples)
    first_sizes = samples[0].get("database", {}).get("sizes", {})
    last_sizes = samples[-1].get("database", {}).get("sizes", {})
    max_sizes = {
        key: max(int(sample.get("database", {}).get("sizes", {}).get(key, 0)) for sample in samples)
        for key in ("db_bytes", "wal_bytes", "shm_bytes", "total_bytes")
    }
    alert_occurrences: Counter[str] = Counter()
    for sample in samples:
        alert_occurrences.update({alert["code"] for alert in sample.get("alerts", [])})
    first_alerts = {alert["code"] for alert in samples[0].get("alerts", [])}
    last_alerts = {alert["code"] for alert in samples[-1].get("alerts", [])}
    scheduler_pid_history: list[list[int]] = []
    for sample in samples:
        pids = list(sample.get("process", {}).get("candidate_pids", []))
        if not scheduler_pid_history or scheduler_pid_history[-1] != pids:
            scheduler_pid_history.append(pids)

    max_heartbeat_age: dict[str, float] = {}
    for sample in samples:
        for row in sample.get("analysis", {}).get("heartbeats", []):
            age = row.get("heartbeat_age_sec")
            if age is not None:
                strategy_id = str(row["strategy_id"])
                max_heartbeat_age[strategy_id] = max(
                    float(age), max_heartbeat_age.get(strategy_id, 0.0)
                )

    return {
        "source": str(Path(path).expanduser().resolve()),
        "sample_count": len(samples),
        "invalid_line_count": invalid_lines,
        "first_sampled_at": samples[0].get("sampled_at"),
        "last_sampled_at": samples[-1].get("sampled_at"),
        "overall_counts": dict(sorted(status_counts.items())),
        "duration_ms": {
            "max": max(float(sample.get("duration_ms", 0)) for sample in samples),
            "last": float(samples[-1].get("duration_ms", 0)),
        },
        "database_sizes": {
            "first": first_sizes,
            "last": last_sizes,
            "max": max_sizes,
            "growth_bytes": {
                key: int(last_sizes.get(key, 0)) - int(first_sizes.get(key, 0))
                for key in ("db_bytes", "wal_bytes", "total_bytes")
            },
        },
        "alert_sample_occurrences": dict(sorted(alert_occurrences.items())),
        "resolved_since_first": sorted(first_alerts - last_alerts),
        "resolved_by_end": sorted(set(alert_occurrences) - last_alerts),
        "present_in_last_sample": sorted(last_alerts),
        "scheduler_pid_history": scheduler_pid_history,
        "max_heartbeat_age_sec": dict(sorted(max_heartbeat_age.items())),
    }
