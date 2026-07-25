from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from ..domain import (
    CellStage,
    FuturesMarket,
    GridCell,
    Mode,
    StrategyConfig,
    StrategyStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLiteStore:
    def __init__(self, path: str | Path = "grid_trading.sqlite3") -> None:
        # Connections are opened on demand.  Resolve once so a later cwd
        # change cannot silently open a second, empty database.
        self.path = Path(path).expanduser().resolve()
        self.init_schema()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market_type TEXT NOT NULL DEFAULT 'usdm'
                        CHECK(market_type IN ('usdm', 'coinm')),
                    mode TEXT NOT NULL CHECK(mode IN ('long', 'short')),
                    anchor_price TEXT NOT NULL,
                    grid_ratio TEXT NOT NULL,
                    grid_count INTEGER NOT NULL,
                    order_usdt TEXT NOT NULL,
                    order_coin_qty TEXT,
                    contract_size TEXT NOT NULL DEFAULT '0',
                    leverage INTEGER NOT NULL,
                    poll_interval_sec REAL NOT NULL,
                    move_grid INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    has_started INTEGER NOT NULL DEFAULT 0,
                    archived INTEGER NOT NULL DEFAULT 0,
                    deleted_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    first_started_at TEXT
                );

                DROP INDEX IF EXISTS idx_strategies_active_symbol_mode;
                DROP INDEX IF EXISTS idx_strategies_symbol_mode;

                CREATE INDEX IF NOT EXISTS idx_strategies_symbol_mode
                ON strategies(market_type, symbol, mode);

                CREATE TABLE IF NOT EXISTS cells (
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    cell_index INTEGER NOT NULL,
                    buy_price TEXT NOT NULL,
                    sell_price TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    entry_order_id INTEGER,
                    exit_order_id INTEGER,
                    entry_client_id TEXT NOT NULL DEFAULT '',
                    exit_client_id TEXT NOT NULL DEFAULT '',
                    open_qty TEXT NOT NULL DEFAULT '0',
                    exit_executed_qty TEXT NOT NULL DEFAULT '0',
                    entry_filled_at TEXT NOT NULL DEFAULT '',
                    cycle_count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_cells_strategy_index
                ON cells(strategy_id, cell_index);

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    run_id TEXT,
                    cell_id TEXT,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_strategy_id
                ON events(strategy_id, id);

                CREATE TABLE IF NOT EXISTS runtime (
                    strategy_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    pid INTEGER,
                    mark_price TEXT,
                    heartbeat_at TEXT,
                    started_at TEXT,
                    stopped_at TEXT,
                    last_error TEXT,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS position_pools (
                    market_type TEXT NOT NULL DEFAULT 'usdm'
                        CHECK(market_type IN ('usdm', 'coinm')),
                    symbol TEXT NOT NULL,
                    position_side TEXT NOT NULL,
                    actual_qty TEXT NOT NULL,
                    logical_qty TEXT NOT NULL,
                    external_reserved_qty TEXT NOT NULL,
                    unassigned_qty TEXT NOT NULL,
                    shortage_qty TEXT NOT NULL,
                    status TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    PRIMARY KEY(market_type, symbol, position_side)
                );

                CREATE TABLE IF NOT EXISTS cell_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('add', 'remove')),
                    boundary TEXT NOT NULL CHECK(boundary IN ('lower', 'upper')),
                    target_cell_id TEXT NOT NULL,
                    result_cell_id TEXT,
                    status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'failed')),
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_cell_actions_strategy_id
                ON cell_actions(strategy_id, id);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_cell_actions_one_pending
                ON cell_actions(strategy_id) WHERE status = 'pending';

                CREATE TABLE IF NOT EXISTS scheduler_runs (
                    run_id TEXT PRIMARY KEY,
                    pid INTEGER NOT NULL,
                    started_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    stopped_at TEXT,
                    stop_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS scheduler_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    previous_seen_at TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    gap_seconds REAL NOT NULL,
                    active_strategy_count INTEGER NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES scheduler_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_scheduler_gaps_detected_at
                ON scheduler_gaps(detected_at DESC);

                CREATE TABLE IF NOT EXISTS scheduler_incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    strategy_id TEXT,
                    market_type TEXT,
                    run_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    last_failed_at TEXT NOT NULL,
                    recovered_at TEXT,
                    failure_count INTEGER NOT NULL DEFAULT 1,
                    error_type TEXT NOT NULL,
                    first_error TEXT NOT NULL,
                    last_error TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_scheduler_incidents_open_scope
                ON scheduler_incidents(scope) WHERE recovered_at IS NULL;

                CREATE INDEX IF NOT EXISTS idx_scheduler_incidents_started_at
                ON scheduler_incidents(started_at DESC);
                """
            )
            cell_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(cells)").fetchall()
            }
            if "exit_executed_qty" not in cell_columns:
                conn.execute(
                    "ALTER TABLE cells ADD COLUMN exit_executed_qty TEXT NOT NULL DEFAULT '0'"
                )
            if "entry_filled_at" not in cell_columns:
                conn.execute(
                    "ALTER TABLE cells ADD COLUMN entry_filled_at TEXT NOT NULL DEFAULT ''"
                )
            strategy_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(strategies)").fetchall()
            }
            if "market_type" not in strategy_columns:
                conn.execute(
                    "ALTER TABLE strategies ADD COLUMN market_type TEXT NOT NULL DEFAULT 'usdm'"
                )
            if "order_coin_qty" not in strategy_columns:
                conn.execute("ALTER TABLE strategies ADD COLUMN order_coin_qty TEXT")
            if "contract_size" not in strategy_columns:
                conn.execute(
                    "ALTER TABLE strategies ADD COLUMN contract_size TEXT NOT NULL DEFAULT '0'"
                )
            # The first COIN-M prototype stored a USD face value in
            # order_usdt. Preserve its exposure while migrating the user-facing
            # configuration to margin/base-coin quantity.
            legacy_coinm = conn.execute(
                """
                SELECT strategy_id, order_usdt, anchor_price
                FROM strategies
                WHERE market_type='coinm' AND order_coin_qty IS NULL
                """
            ).fetchall()
            for row in legacy_coinm:
                anchor = Decimal(row["anchor_price"])
                quantity = Decimal(row["order_usdt"]) / anchor
                conn.execute(
                    "UPDATE strategies SET order_coin_qty=? WHERE strategy_id=?",
                    (str(quantity), row["strategy_id"]),
                )
            pool_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(position_pools)").fetchall()
            }
            if "market_type" not in pool_columns:
                self._migrate_position_pools_market_type(conn)

    @staticmethod
    def _migrate_position_pools_market_type(conn: sqlite3.Connection) -> None:
        """Rebuild the legacy two-column pool key without losing snapshots."""

        conn.executescript(
            """
            ALTER TABLE position_pools RENAME TO position_pools_legacy;
            CREATE TABLE position_pools (
                market_type TEXT NOT NULL DEFAULT 'usdm'
                    CHECK(market_type IN ('usdm', 'coinm')),
                symbol TEXT NOT NULL,
                position_side TEXT NOT NULL,
                actual_qty TEXT NOT NULL,
                logical_qty TEXT NOT NULL,
                external_reserved_qty TEXT NOT NULL,
                unassigned_qty TEXT NOT NULL,
                shortage_qty TEXT NOT NULL,
                status TEXT NOT NULL,
                checked_at TEXT NOT NULL,
                PRIMARY KEY(market_type, symbol, position_side)
            );
            INSERT INTO position_pools(
                market_type, symbol, position_side, actual_qty, logical_qty,
                external_reserved_qty, unassigned_qty, shortage_qty, status,
                checked_at
            )
            SELECT
                'usdm', symbol, position_side, actual_qty, logical_qty,
                external_reserved_qty, unassigned_qty, shortage_qty, status,
                checked_at
            FROM position_pools_legacy;
            DROP TABLE position_pools_legacy;
            """
        )

    def create_strategy(self, config: StrategyConfig) -> None:
        config.validate()
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO strategies (
                    strategy_id, symbol, market_type, mode, anchor_price, grid_ratio, grid_count,
                    order_usdt, order_coin_qty, contract_size, leverage,
                    poll_interval_sec, move_grid, status,
                    has_started, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.strategy_id,
                    config.symbol.strip().upper(),
                    config.market_type.value,
                    config.mode.value,
                    str(config.anchor_price),
                    str(config.grid_ratio),
                    config.grid_count,
                    str(config.order_usdt),
                    None if config.order_coin_qty is None else str(config.order_coin_qty),
                    str(config.contract_size),
                    config.leverage,
                    config.poll_interval_sec,
                    int(config.move_grid),
                    config.status.value,
                    int(config.has_started),
                    int(config.archived),
                    now,
                    now,
                ),
            )

    def get_strategy(self, strategy_id: str, include_deleted: bool = False) -> StrategyConfig | None:
        where_deleted = "" if include_deleted else "AND deleted_at IS NULL"
        with self.connection() as conn:
            row = conn.execute(
                f"SELECT * FROM strategies WHERE strategy_id = ? {where_deleted}",
                (strategy_id,),
            ).fetchone()
        return self._strategy_from_row(row) if row else None

    def list_strategies(
        self,
        include_archived: bool = False,
        include_deleted: bool = False,
    ) -> list[StrategyConfig]:
        archived_clause = "" if include_archived else "AND archived = 0"
        deleted_clause = "" if include_deleted else "AND deleted_at IS NULL"
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM strategies WHERE 1=1 {deleted_clause} {archived_clause} ORDER BY created_at, strategy_id"
            ).fetchall()
        return [self._strategy_from_row(row) for row in rows]

    def update_draft(self, config: StrategyConfig) -> None:
        config.validate()
        with self.connection() as conn:
            current = conn.execute(
                "SELECT has_started, archived FROM strategies WHERE strategy_id = ? AND deleted_at IS NULL",
                (config.strategy_id,),
            ).fetchone()
            if current is None:
                raise KeyError(config.strategy_id)
            if current["has_started"] or current["archived"]:
                raise ValueError("configuration is immutable after first start or archive")
            conn.execute(
                """
                UPDATE strategies SET symbol=?, market_type=?, mode=?, anchor_price=?, grid_ratio=?, grid_count=?,
                    order_usdt=?, order_coin_qty=?, contract_size=?, leverage=?,
                    poll_interval_sec=?, move_grid=?, updated_at=?
                WHERE strategy_id=?
                """,
                (
                    config.symbol.strip().upper(),
                    config.market_type.value,
                    config.mode.value,
                    str(config.anchor_price),
                    str(config.grid_ratio),
                    config.grid_count,
                    str(config.order_usdt),
                    None if config.order_coin_qty is None else str(config.order_coin_qty),
                    str(config.contract_size),
                    config.leverage,
                    config.poll_interval_sec,
                    int(config.move_grid),
                    utc_now(),
                    config.strategy_id,
                ),
            )

    def mark_started(self, strategy_id: str) -> None:
        now = utc_now()
        with self.connection() as conn:
            result = conn.execute(
                """
                UPDATE strategies SET has_started=1, status=?,
                    first_started_at=COALESCE(first_started_at, ?), updated_at=?
                WHERE strategy_id=? AND deleted_at IS NULL AND archived=0
                """,
                (StrategyStatus.STARTING.value, now, now, strategy_id),
            )
            if result.rowcount != 1:
                raise KeyError(strategy_id)

    def set_status(self, strategy_id: str, status: StrategyStatus) -> None:
        with self.connection() as conn:
            result = conn.execute(
                "UPDATE strategies SET status=?, updated_at=? WHERE strategy_id=? AND deleted_at IS NULL",
                (status.value, utc_now(), strategy_id),
            )
            if result.rowcount != 1:
                raise KeyError(strategy_id)

    def set_status_if_active(
        self,
        strategy_id: str,
        status: StrategyStatus,
    ) -> bool:
        """Update scheduler-owned status without reviving a stopped strategy."""
        with self.connection() as conn:
            result = conn.execute(
                """
                UPDATE strategies SET status=?, updated_at=?
                WHERE strategy_id=? AND deleted_at IS NULL
                  AND status IN (?, ?, ?)
                """,
                (
                    status.value,
                    utc_now(),
                    strategy_id,
                    StrategyStatus.STARTING.value,
                    StrategyStatus.RUNNING.value,
                    StrategyStatus.ERROR.value,
                ),
            )
        return result.rowcount == 1

    def archive_strategy(self, strategy_id: str) -> None:
        with self.connection() as conn:
            result = conn.execute(
                "UPDATE strategies SET archived=1, status=?, updated_at=? WHERE strategy_id=? AND deleted_at IS NULL",
                (StrategyStatus.ARCHIVED.value, utc_now(), strategy_id),
            )
            if result.rowcount != 1:
                raise KeyError(strategy_id)

    def soft_delete_strategy(self, strategy_id: str) -> None:
        with self.connection() as conn:
            result = conn.execute(
                "UPDATE strategies SET deleted_at=?, updated_at=? WHERE strategy_id=? AND deleted_at IS NULL",
                (utc_now(), utc_now(), strategy_id),
            )
            if result.rowcount != 1:
                raise KeyError(strategy_id)

    def replace_cells(self, strategy_id: str, cells: list[GridCell]) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.execute("DELETE FROM cells WHERE strategy_id=?", (strategy_id,))
            conn.executemany(
                """
                INSERT INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._cell_values(cell, now) for cell in cells],
            )

    def save_cell(self, cell: GridCell) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, cell_id) DO UPDATE SET
                    cell_index=excluded.cell_index, buy_price=excluded.buy_price,
                    sell_price=excluded.sell_price, stage=excluded.stage,
                    entry_order_id=excluded.entry_order_id, exit_order_id=excluded.exit_order_id,
                    entry_client_id=excluded.entry_client_id, exit_client_id=excluded.exit_client_id,
                    open_qty=excluded.open_qty, exit_executed_qty=excluded.exit_executed_qty,
                    entry_filled_at=excluded.entry_filled_at, cycle_count=excluded.cycle_count,
                    updated_at=excluded.updated_at
                """,
                self._cell_values(cell, utc_now()),
            )

    def delete_cell(self, strategy_id: str, cell_id: str) -> None:
        with self.connection() as conn:
            conn.execute("DELETE FROM cells WHERE strategy_id=? AND cell_id=?", (strategy_id, cell_id))

    def request_cell_action(
        self,
        strategy_id: str,
        operation: str,
        boundary: str,
    ) -> dict:
        if operation not in {"add", "remove"}:
            raise ValueError("operation must be add or remove")
        if boundary not in {"lower", "upper"}:
            raise ValueError("boundary must be lower or upper")
        now = utc_now()
        with self.connection() as conn:
            strategy = conn.execute(
                "SELECT status FROM strategies WHERE strategy_id=? AND deleted_at IS NULL AND archived=0",
                (strategy_id,),
            ).fetchone()
            if strategy is None:
                raise KeyError(strategy_id)
            if strategy["status"] not in {
                StrategyStatus.STARTING.value,
                StrategyStatus.RUNNING.value,
                StrategyStatus.ERROR.value,
            }:
                raise ValueError("Cell adjustments require a running strategy")
            pending = conn.execute(
                "SELECT id FROM cell_actions WHERE strategy_id=? AND status='pending'",
                (strategy_id,),
            ).fetchone()
            if pending is not None:
                raise ValueError("another Cell adjustment is still pending")
            cells = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? ORDER BY CAST(buy_price AS REAL), cell_index",
                (strategy_id,),
            ).fetchall()
            if not cells:
                raise ValueError("strategy has no Cells")
            target = cells[0] if boundary == "lower" else cells[-1]
            if operation == "remove":
                if len(cells) <= 1:
                    raise ValueError("at least one Cell must remain")
                if Decimal(target["open_qty"]) > 0 or target["stage"] in {
                    CellStage.PENDING_EXIT.value,
                    CellStage.MANUAL_REVIEW.value,
                }:
                    raise ValueError("a Cell with position or uncertain state cannot be removed")
            cursor = conn.execute(
                """
                INSERT INTO cell_actions(
                    strategy_id, operation, boundary, target_cell_id,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (strategy_id, operation, boundary, target["cell_id"], now, now),
            )
            action_id = int(cursor.lastrowid)
            row = conn.execute(
                "SELECT * FROM cell_actions WHERE id=?",
                (action_id,),
            ).fetchone()
        return dict(row)

    def list_pending_cell_actions(self, strategy_id: str | None = None) -> list[dict]:
        clause = "" if strategy_id is None else "AND strategy_id=?"
        params = () if strategy_id is None else (strategy_id,)
        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT * FROM cell_actions WHERE status='pending' {clause} ORDER BY id",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_cell_actions(self, strategy_id: str, limit: int = 20) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cell_actions WHERE strategy_id=? ORDER BY id DESC LIMIT ?",
                (strategy_id, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_cell_action_strategy_ids(self) -> set[str]:
        return {
            action["strategy_id"]
            for action in self.list_pending_cell_actions()
        }

    def complete_add_cell_action(self, action_id: int, cell: GridCell) -> None:
        with self.connection() as conn:
            action = conn.execute(
                "SELECT * FROM cell_actions WHERE id=? AND status='pending'",
                (action_id,),
            ).fetchone()
            if action is None:
                return
            conn.execute(
                """
                INSERT OR IGNORE INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                self._cell_values(cell, utc_now()),
            )
            self._sync_cell_count_and_indices(conn, cell.strategy_id)
            conn.execute(
                """
                UPDATE cell_actions SET status='completed', result_cell_id=?,
                    message='Cell added', updated_at=? WHERE id=?
                """,
                (cell.cell_id, utc_now(), action_id),
            )

    def complete_remove_cell_action(self, action_id: int, strategy_id: str, cell_id: str) -> None:
        with self.connection() as conn:
            action = conn.execute(
                "SELECT * FROM cell_actions WHERE id=? AND status='pending'",
                (action_id,),
            ).fetchone()
            if action is None:
                return
            conn.execute(
                "DELETE FROM cells WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            )
            count = self._sync_cell_count_and_indices(conn, strategy_id)
            if count < 1:
                raise ValueError("at least one Cell must remain")
            conn.execute(
                """
                UPDATE cell_actions SET status='completed', result_cell_id=?,
                    message='Cell removed', updated_at=? WHERE id=?
                """,
                (cell_id, utc_now(), action_id),
            )

    def fail_cell_action(self, action_id: int, message: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE cell_actions SET status='failed', message=?, updated_at=?
                WHERE id=? AND status='pending'
                """,
                (str(message), utc_now(), action_id),
            )

    @staticmethod
    def _sync_cell_count_and_indices(conn: sqlite3.Connection, strategy_id: str) -> int:
        rows = conn.execute(
            "SELECT cell_id FROM cells WHERE strategy_id=? ORDER BY CAST(buy_price AS REAL), cell_index",
            (strategy_id,),
        ).fetchall()
        for index, row in enumerate(rows, start=1):
            conn.execute(
                "UPDATE cells SET cell_index=?, updated_at=? WHERE strategy_id=? AND cell_id=?",
                (index, utc_now(), strategy_id, row["cell_id"]),
            )
        conn.execute(
            "UPDATE strategies SET grid_count=?, updated_at=? WHERE strategy_id=?",
            (len(rows), utc_now(), strategy_id),
        )
        return len(rows)

    def list_cells(self, strategy_id: str) -> list[GridCell]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? ORDER BY buy_price + 0, cell_index",
                (strategy_id,),
            ).fetchall()
        cells = [self._cell_from_row(row) for row in rows]
        cells.sort(key=lambda cell: cell.buy_price)
        return cells

    def list_all_cells(self) -> list[GridCell]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cells ORDER BY strategy_id, cell_index"
            ).fetchall()
        return [self._cell_from_row(row) for row in rows]

    def append_event(
        self,
        strategy_id: str,
        event_type: str,
        payload: dict | None = None,
        cell_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                "INSERT INTO events(strategy_id, run_id, cell_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (strategy_id, run_id, cell_id, event_type, json.dumps(payload or {}, ensure_ascii=False), utc_now()),
            )
            return int(cursor.lastrowid)

    def list_events(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE strategy_id=? ORDER BY id", (strategy_id,)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "strategy_id": row["strategy_id"],
                "run_id": row["run_id"],
                "cell_id": row["cell_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def get_order_quantities(
        self,
        strategy_id: str,
        order_ids: set[int],
    ) -> dict[int, Decimal]:
        """Return recorded quantities for the requested current orders."""

        if not order_ids:
            return {}
        placeholders = ",".join("?" for _ in order_ids)
        params: list[object] = [strategy_id, *sorted(order_ids)]
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT payload_json
                FROM events
                WHERE strategy_id=?
                  AND json_extract(payload_json, '$.qty') IS NOT NULL
                  AND CAST(json_extract(payload_json, '$.order_id') AS INTEGER)
                      IN ({placeholders})
                ORDER BY id DESC
                """,
                params,
            ).fetchall()

        quantities: dict[int, Decimal] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            try:
                order_id = int(payload["order_id"])
                quantity = Decimal(str(payload["qty"]))
            except (KeyError, TypeError, ValueError):
                continue
            quantities.setdefault(order_id, quantity)
        return quantities

    def heartbeat(
        self,
        strategy_id: str,
        run_id: str,
        pid: int,
        mark_price: Decimal | None = None,
        last_error: str | None = None,
    ) -> None:
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO runtime(strategy_id, run_id, pid, mark_price, heartbeat_at, started_at, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET run_id=excluded.run_id, pid=excluded.pid,
                    mark_price=excluded.mark_price, heartbeat_at=excluded.heartbeat_at,
                    last_error=excluded.last_error, stopped_at=NULL
                """,
                (strategy_id, run_id, pid, None if mark_price is None else str(mark_price), now, now, last_error),
            )

    def heartbeat_if_active(
        self,
        strategy_id: str,
        run_id: str,
        pid: int,
        mark_price: Decimal | None = None,
        last_error: str | None = None,
    ) -> bool:
        """Write runtime state only while the strategy still accepts scheduler work."""
        now = utc_now()
        with self.connection() as conn:
            result = conn.execute(
                """
                INSERT INTO runtime(
                    strategy_id, run_id, pid, mark_price,
                    heartbeat_at, started_at, last_error
                )
                SELECT ?, ?, ?, ?, ?, ?, ?
                WHERE EXISTS (
                    SELECT 1 FROM strategies
                    WHERE strategy_id=? AND deleted_at IS NULL
                      AND status IN (?, ?, ?)
                )
                ON CONFLICT(strategy_id) DO UPDATE SET
                    run_id=excluded.run_id,
                    pid=excluded.pid,
                    mark_price=excluded.mark_price,
                    heartbeat_at=excluded.heartbeat_at,
                    last_error=excluded.last_error,
                    stopped_at=NULL
                """,
                (
                    strategy_id,
                    run_id,
                    pid,
                    None if mark_price is None else str(mark_price),
                    now,
                    now,
                    last_error,
                    strategy_id,
                    StrategyStatus.STARTING.value,
                    StrategyStatus.RUNNING.value,
                    StrategyStatus.ERROR.value,
                ),
            )
        return result.rowcount == 1

    def mark_runtime_stopped(self, strategy_id: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE runtime SET stopped_at=? WHERE strategy_id=?", (utc_now(), strategy_id))

    def get_runtime(self, strategy_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runtime WHERE strategy_id=?", (strategy_id,)).fetchone()
        return dict(row) if row else None

    def record_scheduler_run_start(
        self,
        run_id: str,
        pid: int,
        *,
        observed_at: str | None = None,
    ) -> None:
        now = observed_at or utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduler_runs
                SET stopped_at=last_seen_at,
                    stop_reason=COALESCE(stop_reason, 'unclean_restart')
                WHERE stopped_at IS NULL AND run_id<>?
                """,
                (run_id,),
            )
            conn.execute(
                """
                INSERT INTO scheduler_runs(
                    run_id, pid, started_at, last_seen_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    pid=excluded.pid,
                    last_seen_at=excluded.last_seen_at
                """,
                (run_id, pid, now, now),
            )

    def touch_scheduler_run(
        self,
        run_id: str,
        *,
        observed_at: str | None = None,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduler_runs
                SET last_seen_at=?
                WHERE run_id=? AND stopped_at IS NULL
                """,
                (observed_at or utc_now(), run_id),
            )

    def stop_scheduler_run(
        self,
        run_id: str,
        reason: str,
        *,
        observed_at: str | None = None,
    ) -> None:
        now = observed_at or utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE scheduler_runs
                SET last_seen_at=?, stopped_at=?, stop_reason=?
                WHERE run_id=? AND stopped_at IS NULL
                """,
                (now, now, str(reason)[:200], run_id),
            )

    def record_scheduler_gap(
        self,
        run_id: str,
        previous_seen_at: str,
        detected_at: str,
        gap_seconds: float,
        active_strategy_count: int,
    ) -> int:
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scheduler_gaps(
                    run_id, previous_seen_at, detected_at,
                    gap_seconds, active_strategy_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    previous_seen_at,
                    detected_at,
                    float(gap_seconds),
                    int(active_strategy_count),
                ),
            )
            return int(cursor.lastrowid)

    def record_scheduler_failure(
        self,
        scope: str,
        run_id: str,
        error: Exception,
        *,
        strategy_id: str | None = None,
        market_type: FuturesMarket | None = None,
    ) -> dict:
        now = utc_now()
        error_type = type(error).__name__
        message = str(error)[:4000]
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM scheduler_incidents
                WHERE scope=? AND recovered_at IS NULL
                """,
                (scope,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO scheduler_incidents(
                        scope, strategy_id, market_type, run_id,
                        started_at, last_failed_at, failure_count,
                        error_type, first_error, last_error
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        scope,
                        strategy_id,
                        None if market_type is None else market_type.value,
                        run_id,
                        now,
                        now,
                        error_type,
                        message,
                        message,
                    ),
                )
                incident_id = int(cursor.lastrowid)
                opened = True
            else:
                incident_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE scheduler_incidents
                    SET run_id=?, last_failed_at=?,
                        failure_count=failure_count+1,
                        error_type=?, last_error=?
                    WHERE id=?
                    """,
                    (run_id, now, error_type, message, incident_id),
                )
                opened = False
            saved = conn.execute(
                "SELECT * FROM scheduler_incidents WHERE id=?",
                (incident_id,),
            ).fetchone()
        result = dict(saved)
        result["opened"] = opened
        return result

    def record_scheduler_recovery(
        self,
        scope: str,
        run_id: str,
    ) -> dict | None:
        now = utc_now()
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM scheduler_incidents
                WHERE scope=? AND recovered_at IS NULL
                """,
                (scope,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE scheduler_incidents
                SET run_id=?, recovered_at=?
                WHERE id=?
                """,
                (run_id, now, row["id"]),
            )
            saved = conn.execute(
                "SELECT * FROM scheduler_incidents WHERE id=?",
                (row["id"],),
            ).fetchone()
        return dict(saved)

    def list_scheduler_incidents(self, limit: int = 100) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduler_incidents
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_scheduler_runs(self, limit: int = 50) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduler_runs
                ORDER BY started_at DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_scheduler_gaps(self, limit: int = 100) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM scheduler_gaps
                ORDER BY id DESC LIMIT ?
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_position_pool(
        self,
        symbol: str,
        position_side: str,
        actual_qty: Decimal,
        logical_qty: Decimal,
        external_reserved_qty: Decimal,
        unassigned_qty: Decimal,
        shortage_qty: Decimal,
        status: str,
        market_type: FuturesMarket = FuturesMarket.USDM,
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO position_pools(
                    market_type, symbol, position_side, actual_qty, logical_qty,
                    external_reserved_qty, unassigned_qty, shortage_qty,
                    status, checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(market_type, symbol, position_side) DO UPDATE SET
                    actual_qty=excluded.actual_qty,
                    logical_qty=excluded.logical_qty,
                    external_reserved_qty=excluded.external_reserved_qty,
                    unassigned_qty=excluded.unassigned_qty,
                    shortage_qty=excluded.shortage_qty,
                    status=excluded.status,
                    checked_at=excluded.checked_at
                """,
                (
                    market_type.value,
                    symbol,
                    position_side,
                    str(actual_qty),
                    str(logical_qty),
                    str(external_reserved_qty),
                    str(unassigned_qty),
                    str(shortage_qty),
                    status,
                    utc_now(),
                ),
            )

    def list_position_pools(self) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM position_pools ORDER BY market_type, symbol, position_side"
            ).fetchall()
        return [dict(row) for row in rows]

    def get_position_pool(
        self,
        symbol: str,
        position_side: str,
        market_type: FuturesMarket = FuturesMarket.USDM,
    ) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM position_pools
                WHERE market_type=? AND symbol=? AND position_side=?
                """,
                (market_type.value, symbol, position_side),
            ).fetchone()
        return dict(row) if row else None

    def delete_position_pools_except(
        self,
        keys: set[tuple[FuturesMarket, str, str] | tuple[str, str]],
    ) -> None:
        normalized = {
            (FuturesMarket.USDM.value, key[0], key[1])
            if len(key) == 2
            else (FuturesMarket(key[0]).value, key[1], key[2])
            for key in keys
        }
        with self.connection() as conn:
            if not normalized:
                conn.execute("DELETE FROM position_pools")
                return
            placeholders = ", ".join("(?, ?, ?)" for _ in normalized)
            params = [value for key in sorted(normalized) for value in key]
            conn.execute(
                f"DELETE FROM position_pools WHERE (market_type, symbol, position_side) NOT IN ({placeholders})",
                params,
            )

    def delete_position_pools_for_market_except(
        self,
        market_type: FuturesMarket,
        keys: set[tuple[str, str]],
    ) -> None:
        with self.connection() as conn:
            if not keys:
                conn.execute(
                    "DELETE FROM position_pools WHERE market_type=?",
                    (market_type.value,),
                )
                return
            placeholders = ", ".join("(?, ?)" for _ in keys)
            params: list[object] = [market_type.value]
            params.extend(value for key in sorted(keys) for value in key)
            conn.execute(
                f"""
                DELETE FROM position_pools
                WHERE market_type=?
                  AND (symbol, position_side) NOT IN ({placeholders})
                """,
                params,
            )

    @staticmethod
    def _strategy_from_row(row: sqlite3.Row) -> StrategyConfig:
        market_type = FuturesMarket(row["market_type"])
        order_coin_qty = (
            Decimal(row["order_coin_qty"])
            if "order_coin_qty" in row.keys() and row["order_coin_qty"] is not None
            else None
        )
        if market_type == FuturesMarket.COINM and order_coin_qty is None:
            order_coin_qty = Decimal(row["order_usdt"]) / Decimal(row["anchor_price"])
        return StrategyConfig(
            strategy_id=row["strategy_id"],
            symbol=row["symbol"],
            market_type=market_type,
            mode=Mode(row["mode"]),
            anchor_price=Decimal(row["anchor_price"]),
            grid_ratio=Decimal(row["grid_ratio"]),
            grid_count=int(row["grid_count"]),
            order_usdt=Decimal(row["order_usdt"]),
            leverage=int(row["leverage"]),
            poll_interval_sec=float(row["poll_interval_sec"]),
            move_grid=bool(row["move_grid"]),
            status=StrategyStatus(row["status"]),
            has_started=bool(row["has_started"]),
            archived=bool(row["archived"]),
            order_coin_qty=order_coin_qty,
            contract_size=(
                Decimal(row["contract_size"])
                if "contract_size" in row.keys()
                else Decimal("0")
            ),
        )

    @staticmethod
    def _cell_values(cell: GridCell, updated_at: str) -> tuple:
        return (
            cell.strategy_id,
            cell.cell_id,
            cell.index,
            str(cell.buy_price),
            str(cell.sell_price),
            cell.stage.value,
            cell.entry_order_id,
            cell.exit_order_id,
            cell.entry_client_id,
            cell.exit_client_id,
            str(cell.open_qty),
            str(cell.exit_executed_qty),
            cell.entry_filled_at,
            cell.cycle_count,
            updated_at,
        )

    @staticmethod
    def _cell_from_row(row: sqlite3.Row) -> GridCell:
        return GridCell(
            strategy_id=row["strategy_id"],
            cell_id=row["cell_id"],
            index=int(row["cell_index"]),
            buy_price=Decimal(row["buy_price"]),
            sell_price=Decimal(row["sell_price"]),
            stage=CellStage(row["stage"]),
            entry_order_id=row["entry_order_id"],
            exit_order_id=row["exit_order_id"],
            entry_client_id=row["entry_client_id"],
            exit_client_id=row["exit_client_id"],
            open_qty=Decimal(row["open_qty"]),
            exit_executed_qty=Decimal(row["exit_executed_qty"]),
            entry_filled_at=row["entry_filled_at"],
            cycle_count=int(row["cycle_count"]),
        )
