from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from ..domain import (
    CellStage,
    ExchangeVenue,
    FuturesMarket,
    FixedEntryMode,
    GridKind,
    GridCell,
    Mode,
    OrderSide,
    OrderSnapshot,
    OrderStatus,
    StrategyConfig,
    StrategyStatus,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLiteStore:
    def __init__(self, path: str | Path = "grid_trading.sqlite3", *, venue: ExchangeVenue = ExchangeVenue.BINANCE) -> None:
        # Connections are opened on demand.  Resolve once so a later cwd
        # change cannot silently open a second, empty database.
        self.path = Path(path).expanduser().resolve()
        self.venue = ExchangeVenue(venue)
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
            conn.execute("CREATE TABLE IF NOT EXISTS deployment_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            bound = conn.execute("SELECT value FROM deployment_meta WHERE key='venue'").fetchone()
            if bound is not None and bound["value"] != self.venue.value:
                raise ValueError(f"database is bound to {bound['value']}, not {self.venue.value}")
            if bound is None:
                has_strategies = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='strategies'").fetchone()
                if has_strategies and self.venue != ExchangeVenue.BINANCE:
                    if conn.execute("SELECT 1 FROM strategies LIMIT 1").fetchone():
                        raise ValueError("existing strategy database is not bound to OKX")
                conn.execute("INSERT INTO deployment_meta(key, value) VALUES('venue', ?)", (self.venue.value,))
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    market_type TEXT NOT NULL DEFAULT 'usdm'
                        CHECK(market_type IN ('usdm', 'coinm')),
                    venue TEXT NOT NULL DEFAULT 'binance'
                        CHECK(venue IN ('binance', 'okx')),
                    mode TEXT NOT NULL CHECK(mode IN ('long', 'short')),
                    anchor_price TEXT NOT NULL,
                    grid_ratio TEXT NOT NULL,
                    grid_count INTEGER NOT NULL,
                    order_usdt TEXT NOT NULL,
                    grid_kind TEXT NOT NULL DEFAULT 'triggered',
                    fixed_entry_mode TEXT NOT NULL DEFAULT 'seeded',
                    lower_price TEXT,
                    upper_price TEXT,
                    grid_budget_usdt TEXT,
                    grid_generation INTEGER NOT NULL DEFAULT 0,
                    upper_seed_count INTEGER NOT NULL DEFAULT 0,
                    upper_seed_frontier INTEGER,
                    auto_reinvest_enabled INTEGER NOT NULL DEFAULT 0,
                    auto_reinvest_percent TEXT NOT NULL DEFAULT '100',
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
                    budget_usdt TEXT NOT NULL DEFAULT '0',
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_cells_strategy_index
                ON cells(strategy_id, cell_index);

                CREATE TABLE IF NOT EXISTS upper_seed_slots (
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    cycle_count INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS cell_price_history (
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    buy_price TEXT NOT NULL,
                    sell_price TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TRIGGER IF NOT EXISTS snapshot_fixed_cell_insert
                AFTER INSERT ON cells
                WHEN (SELECT grid_kind FROM strategies WHERE strategy_id=NEW.strategy_id) = 'fixed_equal_usdt'
                BEGIN
                    INSERT OR IGNORE INTO cell_price_history(strategy_id, cell_id, buy_price, sell_price)
                    VALUES (NEW.strategy_id, NEW.cell_id, NEW.buy_price, NEW.sell_price);
                END;

                CREATE TRIGGER IF NOT EXISTS snapshot_fixed_cell_delete
                BEFORE DELETE ON cells
                WHEN (SELECT grid_kind FROM strategies WHERE strategy_id=OLD.strategy_id) = 'fixed_equal_usdt'
                BEGIN
                    INSERT OR IGNORE INTO cell_price_history(strategy_id, cell_id, buy_price, sell_price)
                    VALUES (OLD.strategy_id, OLD.cell_id, OLD.buy_price, OLD.sell_price);
                END;

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

                CREATE TABLE IF NOT EXISTS strategy_trade_fills (
                    strategy_id TEXT NOT NULL,
                    trade_id INTEGER NOT NULL,
                    order_id INTEGER NOT NULL,
                    side TEXT NOT NULL,
                    quantity TEXT NOT NULL,
                    price TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL,
                    commission TEXT NOT NULL,
                    commission_asset TEXT NOT NULL,
                    traded_at_ms INTEGER NOT NULL,
                    PRIMARY KEY(strategy_id, trade_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_trade_fills_order
                ON strategy_trade_fills(strategy_id, order_id);

                CREATE TABLE IF NOT EXISTS strategy_trade_sync (
                    strategy_id TEXT PRIMARY KEY,
                    history_complete INTEGER NOT NULL,
                    scanned_through_ms INTEGER NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

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

                CREATE TABLE IF NOT EXISTS capital_actions (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('seed', 'topup')),
                    amount_usdt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('requested', 'planned', 'submitted', 'applied', 'rejected', 'manual_review')),
                    expected_sell_cell_ids_json TEXT,
                    sell_cell_ids_json TEXT NOT NULL DEFAULT '[]',
                    planned_allocations_json TEXT,
                    target_qty TEXT NOT NULL DEFAULT '0',
                    client_order_id TEXT NOT NULL DEFAULT '',
                    order_id INTEGER,
                    executed_qty TEXT NOT NULL DEFAULT '0',
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_capital_actions_one_pending
                ON capital_actions(strategy_id)
                WHERE status IN ('requested', 'planned', 'submitted');

                CREATE TABLE IF NOT EXISTS fixed_reconfigurations (
                    id TEXT PRIMARY KEY,
                    strategy_id TEXT NOT NULL,
                    expected_generation INTEGER NOT NULL,
                    lower_price TEXT NOT NULL,
                    upper_price TEXT NOT NULL,
                    grid_count INTEGER NOT NULL,
                    grid_budget_usdt TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN (
                        'requested', 'planned', 'submitted', 'applied', 'manual_review', 'aborted'
                    )),
                    plan_json TEXT,
                    client_order_id TEXT NOT NULL DEFAULT '',
                    adjustment_order_id INTEGER,
                    message TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_fixed_reconfigurations_one_pending
                ON fixed_reconfigurations(strategy_id)
                WHERE status IN ('requested', 'planned', 'submitted', 'manual_review');

                CREATE TABLE IF NOT EXISTS cell_reinvestments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    cycle_count INTEGER NOT NULL,
                    quantity_step TEXT NOT NULL,
                    amount_usdt TEXT NOT NULL,
                    profit_snapshot_usdt TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(strategy_id, cell_id, cycle_count),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS cell_reinvest_earnings (
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    cycle_count INTEGER NOT NULL,
                    net_profit_usdt TEXT NOT NULL,
                    eligible_profit_usdt TEXT NOT NULL,
                    auto_reinvest_percent TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id, cycle_count),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS reinvest_cutovers (
                    strategy_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS cell_reinvest_checkpoints (
                    strategy_id TEXT NOT NULL,
                    cell_id TEXT NOT NULL,
                    last_old_cycle_count INTEGER NOT NULL,
                    last_old_reinvestment_id INTEGER NOT NULL,
                    PRIMARY KEY(strategy_id, cell_id),
                    FOREIGN KEY(strategy_id) REFERENCES strategies(strategy_id)
                );

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
            if "budget_usdt" not in cell_columns:
                conn.execute(
                    "ALTER TABLE cells ADD COLUMN budget_usdt TEXT NOT NULL DEFAULT '0'"
                )
            strategy_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(strategies)").fetchall()
            }
            if "market_type" not in strategy_columns:
                conn.execute(
                    "ALTER TABLE strategies ADD COLUMN market_type TEXT NOT NULL DEFAULT 'usdm'"
                )
            if "venue" not in strategy_columns:
                conn.execute("ALTER TABLE strategies ADD COLUMN venue TEXT NOT NULL DEFAULT 'binance'")
            mismatched = conn.execute("SELECT 1 FROM strategies WHERE venue != ? LIMIT 1", (self.venue.value,)).fetchone()
            if mismatched:
                raise ValueError("strategy venue does not match the database binding")
            if "order_coin_qty" not in strategy_columns:
                conn.execute("ALTER TABLE strategies ADD COLUMN order_coin_qty TEXT")
            if "contract_size" not in strategy_columns:
                conn.execute(
                    "ALTER TABLE strategies ADD COLUMN contract_size TEXT NOT NULL DEFAULT '0'"
                )
            for column, definition in (
                ("grid_kind", "TEXT NOT NULL DEFAULT 'triggered'"),
                ("fixed_entry_mode", "TEXT NOT NULL DEFAULT 'seeded'"),
                ("lower_price", "TEXT"),
                ("upper_price", "TEXT"),
                ("grid_budget_usdt", "TEXT"),
                ("grid_generation", "INTEGER NOT NULL DEFAULT 0"),
                ("upper_seed_count", "INTEGER NOT NULL DEFAULT 0"),
                ("upper_seed_frontier", "INTEGER"),
                ("auto_reinvest_enabled", "INTEGER NOT NULL DEFAULT 0"),
                ("auto_reinvest_percent", "TEXT NOT NULL DEFAULT '100'"),
            ):
                if column not in strategy_columns:
                    conn.execute(f"ALTER TABLE strategies ADD COLUMN {column} {definition}")
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
            action_columns = {
                row["name"] for row in conn.execute("PRAGMA table_info(capital_actions)").fetchall()
            }
            if "expected_sell_cell_ids_json" not in action_columns:
                conn.execute(
                    "ALTER TABLE capital_actions ADD COLUMN expected_sell_cell_ids_json TEXT"
                )
            if "planned_allocations_json" not in action_columns:
                conn.execute(
                    "ALTER TABLE capital_actions ADD COLUMN planned_allocations_json TEXT"
                )
            conn.execute(
                """
                INSERT OR IGNORE INTO cell_price_history(strategy_id, cell_id, buy_price, sell_price)
                SELECT cells.strategy_id, cells.cell_id, cells.buy_price, cells.sell_price
                FROM cells JOIN strategies ON strategies.strategy_id = cells.strategy_id
                WHERE strategies.grid_kind = 'fixed_equal_usdt'
                """
            )

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
        if config.venue != self.venue:
            raise ValueError("strategy venue does not match the database binding")
        now = utc_now()
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO strategies (
                    strategy_id, symbol, market_type, venue, mode, anchor_price, grid_ratio, grid_count,
                    order_usdt, grid_kind, fixed_entry_mode, lower_price, upper_price, grid_budget_usdt,
                    upper_seed_count, upper_seed_frontier,
                    auto_reinvest_enabled, auto_reinvest_percent,
                    order_coin_qty, contract_size, leverage,
                    poll_interval_sec, move_grid, status,
                    has_started, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    config.strategy_id,
                    config.symbol.strip().upper(),
                    config.market_type.value,
                    config.venue.value,
                    config.mode.value,
                    str(config.anchor_price),
                    str(config.grid_ratio),
                    config.grid_count,
                    str(config.order_usdt),
                    config.grid_kind.value,
                    config.fixed_entry_mode.value,
                    None if config.lower_price is None else str(config.lower_price),
                    None if config.upper_price is None else str(config.upper_price),
                    None if config.grid_budget_usdt is None else str(config.grid_budget_usdt),
                    config.upper_seed_count,
                    config.upper_seed_frontier,
                    int(config.auto_reinvest_enabled),
                    str(config.auto_reinvest_percent),
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
        if config.venue != self.venue:
            raise ValueError("strategy venue does not match the database binding")
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
                UPDATE strategies SET symbol=?, market_type=?, venue=?, mode=?, anchor_price=?, grid_ratio=?, grid_count=?,
                    order_usdt=?, grid_kind=?, fixed_entry_mode=?, lower_price=?, upper_price=?, grid_budget_usdt=?,
                    upper_seed_count=?, upper_seed_frontier=NULL,
                    auto_reinvest_enabled=?, auto_reinvest_percent=?,
                    order_coin_qty=?, contract_size=?, leverage=?,
                    poll_interval_sec=?, move_grid=?, updated_at=?
                WHERE strategy_id=?
                """,
                (
                    config.symbol.strip().upper(),
                    config.market_type.value,
                    config.venue.value,
                    config.mode.value,
                    str(config.anchor_price),
                    str(config.grid_ratio),
                    config.grid_count,
                    str(config.order_usdt),
                    config.grid_kind.value,
                    config.fixed_entry_mode.value,
                    None if config.lower_price is None else str(config.lower_price),
                    None if config.upper_price is None else str(config.upper_price),
                    None if config.grid_budget_usdt is None else str(config.grid_budget_usdt),
                    config.upper_seed_count,
                    int(config.auto_reinvest_enabled),
                    str(config.auto_reinvest_percent),
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
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, budget_usdt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [self._cell_values(cell, now) for cell in cells],
            )

    def request_fixed_reconfiguration(
        self, action_id: str, strategy_id: str, lower_price: Decimal,
        upper_price: Decimal, grid_count: int, grid_budget_usdt: Decimal,
    ) -> dict:
        if not action_id or len(action_id) > 80 or not 1 <= grid_count <= 500:
            raise ValueError("invalid reconfiguration request")
        from ..domain.grid import fixed_cell_budgets, fixed_grid_ratio
        fixed_cell_budgets(grid_budget_usdt, grid_count)
        fixed_grid_ratio(lower_price, upper_price, grid_count)
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT * FROM fixed_reconfigurations WHERE id=?", (action_id,),
            ).fetchone()
            if existing is not None:
                if (existing["strategy_id"] != strategy_id
                        or Decimal(existing["lower_price"]) != lower_price
                        or Decimal(existing["upper_price"]) != upper_price
                        or int(existing["grid_count"]) != grid_count
                        or Decimal(existing["grid_budget_usdt"]) != grid_budget_usdt):
                    raise ValueError("reconfiguration request ID was reused with different parameters")
                return dict(existing)
            strategy = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id=? AND deleted_at IS NULL AND archived=0",
                (strategy_id,),
            ).fetchone()
            if strategy is None:
                raise KeyError(strategy_id)
            if strategy["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value or not strategy["has_started"]:
                raise ValueError("reconfiguration requires an existing fixed grid")
            conn.execute(
                """INSERT INTO fixed_reconfigurations(
                    id, strategy_id, expected_generation, lower_price, upper_price,
                    grid_count, grid_budget_usdt, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'requested', ?, ?)""",
                (action_id, strategy_id, int(strategy["grid_generation"]),
                 str(lower_price), str(upper_price), grid_count, str(grid_budget_usdt), now, now),
            )
            return dict(conn.execute(
                "SELECT * FROM fixed_reconfigurations WHERE id=?", (action_id,),
            ).fetchone())

    def get_fixed_reconfiguration(self, action_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM fixed_reconfigurations WHERE id=?", (action_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def list_fixed_reconfigurations(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM fixed_reconfigurations WHERE strategy_id=? ORDER BY created_at, id",
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def plan_fixed_reconfiguration(
        self, action_id: str, plan: dict, client_order_id: str,
    ) -> dict:
        with self.connection() as conn:
            result = conn.execute(
                """UPDATE fixed_reconfigurations SET status='planned', plan_json=?,
                    client_order_id=?, updated_at=? WHERE id=? AND status='requested'""",
                (json.dumps(plan, sort_keys=True), client_order_id, utc_now(), action_id),
            )
            if result.rowcount != 1:
                raise ValueError("reconfiguration is not awaiting a plan")
        return self.get_fixed_reconfiguration(action_id)

    def mark_fixed_reconfiguration_submitted(self, action_id: str, order_id: int | None = None) -> dict:
        with self.connection() as conn:
            result = conn.execute(
                """UPDATE fixed_reconfigurations SET status='submitted',
                    adjustment_order_id=COALESCE(?, adjustment_order_id), updated_at=?
                   WHERE id=? AND status IN ('planned', 'submitted')""",
                (order_id, utc_now(), action_id),
            )
            if result.rowcount != 1:
                raise ValueError("reconfiguration adjustment is not planned")
        return self.get_fixed_reconfiguration(action_id)

    def mark_fixed_reconfiguration_review(self, action_id: str, message: str) -> None:
        with self.connection() as conn:
            conn.execute(
                """UPDATE fixed_reconfigurations SET status='manual_review',
                    message=?, updated_at=? WHERE id=? AND status!='applied'""",
                (str(message), utc_now(), action_id),
            )

    def abort_fixed_reconfiguration(self, action_id: str) -> None:
        with self.connection() as conn:
            result = conn.execute(
                """UPDATE fixed_reconfigurations SET status='aborted', updated_at=?
                   WHERE id=? AND status IN ('requested', 'planned', 'manual_review')""",
                (utc_now(), action_id),
            )
            if result.rowcount != 1:
                raise ValueError("submitted or completed reconfiguration cannot be aborted")

    def reconfigure_fixed_cells(
        self, config: StrategyConfig, cells: list[GridCell],
        allocations: dict[str, Decimal], opening_fees: dict[str, Decimal],
        *, expected_generation: int, mark: Decimal,
        position_delta_qty: Decimal = Decimal("0"), position_delta_order_id: int | None = None,
        action_id: str | None = None,
        upper_seed_cell_ids: list[str] | None = None,
    ) -> None:
        """Atomically advance one stopped strategy to a fresh cell generation."""
        config.validate()
        if config.venue != self.venue or config.grid_kind != GridKind.FIXED_EQUAL_USDT:
            raise ValueError("fixed grid venue mismatch")
        if config.grid_generation != expected_generation + 1:
            raise ValueError("grid generation must advance exactly once")
        if (len(cells) != config.grid_count
                or len({cell.cell_id for cell in cells}) != len(cells)
                or any(cell.strategy_id != config.strategy_id or cell.open_qty != 0
                       or cell.cycle_count != 0 for cell in cells)
                or sum((cell.budget_usdt for cell in cells), Decimal("0")) != config.grid_budget_usdt):
            raise ValueError("invalid replacement cells")
        if not set(allocations).issubset({cell.cell_id for cell in cells}):
            raise ValueError("allocation refers to an unknown new cell")
        seed_ids = upper_seed_cell_ids or []
        if (len(seed_ids) != len(set(seed_ids))
                or not set(seed_ids).issubset(allocations)
                or len(seed_ids) > config.upper_seed_count
                or (config.fixed_entry_mode != FixedEntryMode.PRICE_TRIGGERED and seed_ids)):
            raise ValueError("invalid upper seed slots for replacement cells")
        if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED:
            expected_frontier = max(
                (cell.index for cell in cells if cell.cell_id in seed_ids), default=None
            )
            if config.upper_seed_frontier != expected_frontier:
                raise ValueError("upper seed frontier does not match migrated slots")
        if set(opening_fees) != set(allocations):
            raise ValueError("opening fee allocation does not match position allocation")
        if any(qty <= 0 for qty in allocations.values()) or any(fee < 0 for fee in opening_fees.values()):
            raise ValueError("invalid position or opening fee allocation")
        if (position_delta_qty != 0) != (position_delta_order_id is not None):
            raise ValueError("nonzero position adjustment requires a confirmed order")
        now = utc_now()
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id=? AND deleted_at IS NULL AND archived=0",
                (config.strategy_id,),
            ).fetchone()
            if row is None:
                raise KeyError(config.strategy_id)
            if (row["status"] != StrategyStatus.STOPPED.value
                    or not row["has_started"]
                    or row["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value
                    or int(row["grid_generation"]) != expected_generation
                    or row["symbol"] != config.symbol
                    or row["venue"] != config.venue.value):
                raise ValueError("stopped strategy or expected generation changed")
            if action_id is not None:
                action = conn.execute(
                    "SELECT * FROM fixed_reconfigurations WHERE id=?", (action_id,),
                ).fetchone()
                expected_status = "submitted" if position_delta_qty else "planned"
                if (action is None or action["strategy_id"] != config.strategy_id
                        or int(action["expected_generation"]) != expected_generation
                        or action["status"] != expected_status
                        or Decimal(action["lower_price"]) != config.lower_price
                        or Decimal(action["upper_price"]) != config.upper_price
                        or int(action["grid_count"]) != config.grid_count
                        or Decimal(action["grid_budget_usdt"]) != config.grid_budget_usdt
                        or action["adjustment_order_id"] != position_delta_order_id):
                    raise ValueError("persisted reconfiguration action does not match the commit")
                frozen = json.loads(action["plan_json"] or "{}")
                if (Decimal(frozen.get("mark", "0")) != mark
                        or Decimal(frozen.get("target_qty", "-1")) != sum(allocations.values(), Decimal("0"))
                        or Decimal(frozen.get("delta_qty", "NaN")) != position_delta_qty
                        or frozen.get("new_sell_cell_ids") != list(allocations)
                        or frozen.get("upper_seed_cell_ids", []) != seed_ids
                        or frozen.get("upper_seed_frontier") != config.upper_seed_frontier):
                    raise ValueError("persisted reconfiguration plan changed")
            if conn.execute(
                "SELECT 1 FROM capital_actions WHERE strategy_id=? AND status IN ('requested','planned','submitted','manual_review') LIMIT 1",
                (config.strategy_id,),
            ).fetchone():
                raise ValueError("capital action is still pending or needs review")
            if conn.execute(
                "SELECT 1 FROM cell_actions WHERE strategy_id=? AND status='pending' LIMIT 1",
                (config.strategy_id,),
            ).fetchone():
                raise ValueError("cell action is still pending")
            old_cells = conn.execute("SELECT * FROM cells WHERE strategy_id=?", (config.strategy_id,)).fetchall()
            if any(row["entry_order_id"] is not None or row["exit_order_id"] is not None
                   or row["stage"] == CellStage.MANUAL_REVIEW.value for row in old_cells):
                raise ValueError("old cells still own orders or need review")
            old_qty = sum((Decimal(row["open_qty"]) for row in old_cells), Decimal("0"))
            new_qty = sum(allocations.values(), Decimal("0"))
            if old_qty + position_delta_qty != new_qty:
                raise ValueError("position adjustment does not reconcile old and new cells")
            conn.execute("DELETE FROM upper_seed_slots WHERE strategy_id=?", (config.strategy_id,))
            conn.execute("DELETE FROM cells WHERE strategy_id=?", (config.strategy_id,))
            allocated_cells = [
                replace(
                    cell,
                    open_qty=allocations.get(cell.cell_id, Decimal("0")),
                    stage=(CellStage.PENDING_EXIT if allocations.get(cell.cell_id, Decimal("0")) > 0
                           else (CellStage.UNTRIGGERED
                                 if config.fixed_entry_mode == FixedEntryMode.PRICE_TRIGGERED
                                 and cell.buy_price > mark else CellStage.PENDING_ENTRY)),
                )
                for cell in cells
            ]
            conn.executemany(
                """INSERT INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, budget_usdt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [self._cell_values(cell, now) for cell in allocated_cells],
            )
            conn.executemany(
                "INSERT INTO upper_seed_slots(strategy_id, cell_id, cycle_count, created_at) VALUES (?, ?, 0, ?)",
                [(config.strategy_id, cell_id, now) for cell_id in seed_ids],
            )
            conn.execute(
                """UPDATE strategies SET anchor_price=?, grid_ratio=?, grid_count=?, order_usdt=?,
                    lower_price=?, upper_price=?, grid_budget_usdt=?, grid_generation=?,
                    upper_seed_frontier=?, updated_at=?
                   WHERE strategy_id=?""",
                (str(config.anchor_price), str(config.grid_ratio), config.grid_count,
                 str(config.order_usdt), str(config.lower_price), str(config.upper_price),
                 str(config.grid_budget_usdt), config.grid_generation,
                 config.upper_seed_frontier, now, config.strategy_id),
            )
            conn.execute(
                "INSERT INTO events(strategy_id, run_id, cell_id, event_type, payload_json, created_at) VALUES (?, ?, NULL, ?, ?, ?)",
                (config.strategy_id, None, "GRID_RECONFIGURED", json.dumps({
                    "generation": config.grid_generation,
                    "action_id": action_id,
                    "timestamp_ms": timestamp_ms,
                    "mark_price": str(mark),
                    "old_budget_usdt": row["grid_budget_usdt"],
                    "new_budget_usdt": str(config.grid_budget_usdt),
                    "allocations": {key: str(value) for key, value in allocations.items()},
                    "opening_fees": {key: str(value) for key, value in opening_fees.items()},
                    "position_delta_qty": str(position_delta_qty),
                    "position_delta_order_id": position_delta_order_id,
                    "old_cell_count": len(old_cells),
                    "new_cell_count": len(cells),
                    "upper_seed_cell_ids": seed_ids,
                    "upper_seed_frontier": config.upper_seed_frontier,
                }), now),
            )
            if action_id is not None:
                conn.execute(
                    "UPDATE fixed_reconfigurations SET status='applied', updated_at=? WHERE id=?",
                    (now, action_id),
                )

    def reset_flat_fixed_to_triggered(
        self, config: StrategyConfig, cells: list[GridCell], *,
        expected_generation: int, expected_old_cell_count: int,
        expected_old_qty: Decimal, historical_net_grid_profit: Decimal,
        mark: Decimal,
    ) -> None:
        """Atomically discard flat old cells while retaining strategy history."""
        config.validate()
        if (self.venue not in {ExchangeVenue.BINANCE, ExchangeVenue.OKX}
                or config.venue != self.venue
                or config.grid_kind != GridKind.FIXED_EQUAL_USDT
                or config.fixed_entry_mode != FixedEntryMode.PRICE_TRIGGERED
                or config.grid_generation != expected_generation + 1
                or config.lower_price is None or config.upper_price is None
                or not config.lower_price <= mark <= config.upper_price):
            raise ValueError("invalid flat triggered reset configuration")
        if (len(cells) != config.grid_count
                or len({cell.cell_id for cell in cells}) != len(cells)
                or any(cell.strategy_id != config.strategy_id
                       or cell.open_qty != 0 or cell.cycle_count != 0
                       or cell.entry_order_id is not None
                       or cell.exit_order_id is not None for cell in cells)
                or sum((cell.budget_usdt for cell in cells), Decimal("0"))
                != config.grid_budget_usdt):
            raise ValueError("invalid fresh triggered cells")
        if not historical_net_grid_profit.is_finite() or expected_old_qty < 0:
            raise ValueError("invalid historical reset snapshot")
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM strategies
                   WHERE strategy_id=? AND deleted_at IS NULL AND archived=0""",
                (config.strategy_id,),
            ).fetchone()
            if (row is None or row["status"] != StrategyStatus.STOPPED.value
                    or not row["has_started"]
                    or row["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value
                    or row["fixed_entry_mode"] != FixedEntryMode.SEEDED.value
                    or row["venue"] != self.venue.value
                    or row["symbol"] != config.symbol
                    or int(row["grid_generation"]) != expected_generation):
                raise ValueError("stopped seeded strategy changed before reset")
            if conn.execute(
                """SELECT 1 FROM strategies WHERE strategy_id<>? AND symbol=?
                   AND market_type=? AND status IN (?, ?, ?) AND deleted_at IS NULL
                   LIMIT 1""",
                (config.strategy_id, config.symbol, config.market_type.value,
                 StrategyStatus.STARTING.value, StrategyStatus.RUNNING.value,
                 StrategyStatus.ERROR.value),
            ).fetchone():
                raise ValueError("another active strategy owns the symbol")
            if conn.execute(
                """SELECT 1 FROM capital_actions WHERE strategy_id=?
                   AND status IN ('requested','planned','submitted','manual_review')
                   LIMIT 1""",
                (config.strategy_id,),
            ).fetchone() or conn.execute(
                "SELECT 1 FROM cell_actions WHERE strategy_id=? AND status='pending' LIMIT 1",
                (config.strategy_id,),
            ).fetchone() or conn.execute(
                """SELECT 1 FROM fixed_reconfigurations WHERE strategy_id=?
                   AND status IN ('requested','planned','submitted','manual_review')
                   LIMIT 1""",
                (config.strategy_id,),
            ).fetchone():
                raise ValueError("a strategy action is still pending")
            seed = conn.execute(
                "SELECT status FROM capital_actions WHERE id=?",
                (f"seed:{config.strategy_id}",),
            ).fetchone()
            if seed is None or seed["status"] != "applied":
                raise ValueError("the old seed action is not complete")
            old_cells = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=?", (config.strategy_id,),
            ).fetchall()
            old_qty = sum((Decimal(cell["open_qty"]) for cell in old_cells), Decimal("0"))
            if (len(old_cells) != expected_old_cell_count
                    or old_qty != expected_old_qty
                    or {cell["cell_id"] for cell in old_cells}
                    & {cell.cell_id for cell in cells}):
                raise ValueError("old cells changed before reset")
            old_order_references = sum(
                (cell["entry_order_id"] is not None)
                + (cell["exit_order_id"] is not None)
                for cell in old_cells
            )
            # Closed generations can no longer reinvest into removed cells.
            for cell in old_cells:
                conn.execute(
                    """INSERT INTO cell_reinvest_checkpoints(
                         strategy_id, cell_id, last_old_cycle_count,
                         last_old_reinvestment_id
                       ) VALUES (?, ?, ?, 0)
                       ON CONFLICT(strategy_id, cell_id) DO UPDATE SET
                         last_old_cycle_count=MAX(
                           cell_reinvest_checkpoints.last_old_cycle_count,
                           excluded.last_old_cycle_count
                         )""",
                    (config.strategy_id, cell["cell_id"], int(cell["cycle_count"])),
                )
            conn.execute("DELETE FROM cells WHERE strategy_id=?", (config.strategy_id,))
            new_cells = [replace(cell, stage=CellStage.UNTRIGGERED) for cell in cells]
            conn.executemany(
                """INSERT INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count,
                    budget_usdt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [self._cell_values(cell, now) for cell in new_cells],
            )
            conn.execute(
                """UPDATE strategies SET anchor_price=?, grid_ratio=?, grid_count=?,
                   order_usdt=?, lower_price=?, upper_price=?, grid_budget_usdt=?,
                   grid_generation=?, fixed_entry_mode=?, upper_seed_frontier=NULL, updated_at=?
                   WHERE strategy_id=?""",
                (str(config.anchor_price), str(config.grid_ratio), config.grid_count,
                 str(config.order_usdt), str(config.lower_price),
                 str(config.upper_price), str(config.grid_budget_usdt),
                 config.grid_generation, config.fixed_entry_mode.value, now,
                 config.strategy_id),
            )
            conn.execute(
                """UPDATE position_pools SET actual_qty='0', logical_qty='0',
                   external_reserved_qty='0', unassigned_qty='0', shortage_qty='0',
                   status='consistent', checked_at=?
                   WHERE market_type=? AND symbol=? AND position_side='LONG'""",
                (now, config.market_type.value, config.symbol),
            )
            conn.execute(
                """INSERT INTO events(
                    strategy_id, run_id, cell_id, event_type, payload_json, created_at
                ) VALUES (?, NULL, NULL, 'GRID_FLAT_TRIGGERED_RESET', ?, ?)""",
                (config.strategy_id, json.dumps({
                    "old_generation": expected_generation,
                    "new_generation": config.grid_generation,
                    "old_cell_count": len(old_cells),
                    "new_cell_count": len(new_cells),
                    "discarded_old_logical_qty": str(old_qty),
                    "discarded_old_order_references": old_order_references,
                    "historical_net_grid_profit_usdt": str(historical_net_grid_profit),
                    "mark_price": str(mark),
                    "old_budget_usdt": row["grid_budget_usdt"],
                    "new_budget_usdt": str(config.grid_budget_usdt),
                    "entry_mode": config.fixed_entry_mode.value,
                }), now),
            )

    def settle_reconfiguration_order(
        self, strategy_id: str, cell_id: str, role: str, order: OrderSnapshot,
    ) -> bool:
        """Book a terminal old-grid order without submitting a replacement."""
        if role not in {"entry", "exit"} or order.status not in {
            OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED,
        }:
            raise ValueError("reconfiguration requires a terminal entry or exit order")
        side = OrderSide.BUY if role == "entry" else OrderSide.SELL
        if order.side != side or order.position_side not in {"", "LONG"}:
            raise ValueError("old grid order side does not match its cell role")
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            strategy = conn.execute(
                "SELECT status, grid_kind FROM strategies WHERE strategy_id=? AND deleted_at IS NULL",
                (strategy_id,),
            ).fetchone()
            if (strategy is None or strategy["status"] != StrategyStatus.STOPPED.value
                    or strategy["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value):
                raise ValueError("only a stopped fixed grid can settle old orders")
            row = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchone()
            if row is None:
                raise KeyError(cell_id)
            reference = row["entry_order_id"] if role == "entry" else row["exit_order_id"]
            if reference is None:
                return False
            if int(reference) != order.order_id:
                raise ValueError("old cell order reference changed")
            open_qty = Decimal(row["open_qty"])
            executed = order.executed_qty
            if executed < 0 or executed > order.original_qty:
                raise ValueError("invalid terminal executed quantity")
            if role == "entry":
                # A completed (or finalized partial) entry deliberately keeps
                # its order ID while its exit is active. Its fill is already in
                # open_qty; settling that historical reference must not book it
                # a second time during reconfiguration.
                already_booked = row["stage"] == CellStage.PENDING_EXIT.value
                if already_booked and (open_qty <= 0 or executed <= 0):
                    raise ValueError("booked old entry does not match its open cell")
                new_open = open_qty if already_booked else open_qty + executed
                stage = CellStage.PENDING_EXIT if new_open > 0 else CellStage.PENDING_ENTRY
                conn.execute(
                    """UPDATE cells SET open_qty=?, stage=?, entry_order_id=NULL,
                        entry_client_id='', entry_filled_at=?, updated_at=?
                       WHERE strategy_id=? AND cell_id=?""",
                    (str(new_open), stage.value,
                     (row["entry_filled_at"] or now) if new_open > 0 else "",
                     now, strategy_id, cell_id),
                )
                event_type = "ENTRY_ENDED"
                payload = {"order_id": order.order_id, "status": order.status.value,
                           "executed_qty": str(executed),
                           "action": "reconfigure_booked" if already_booked else "reconfigure"}
            else:
                already = Decimal(row["exit_executed_qty"])
                delta = executed - already
                if delta < 0 or delta > open_qty:
                    raise ValueError("terminal exit fill does not reconcile with cell quantity")
                new_open = open_qty - delta
                closed = new_open == 0
                cycles = int(row["cycle_count"]) + int(closed)
                stage = CellStage.PENDING_ENTRY if closed else CellStage.PENDING_EXIT
                conn.execute(
                    """UPDATE cells SET open_qty=?, stage=?, exit_order_id=NULL,
                        exit_client_id='', exit_executed_qty='0', cycle_count=?,
                        entry_filled_at=?, updated_at=?
                       WHERE strategy_id=? AND cell_id=?""",
                    (str(new_open), stage.value, cycles,
                     "" if closed else row["entry_filled_at"], now, strategy_id, cell_id),
                )
                event_type = "EXIT_ENDED"
                payload = {"order_id": order.order_id, "status": order.status.value,
                           "executed_qty": str(executed), "executed_delta": str(delta),
                           "remaining_open_qty": str(new_open),
                           "action": "cycle_closed" if closed else "reconfigure_remainder",
                           "cycle_count": cycles if closed else None}
            conn.execute(
                "INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (strategy_id, cell_id, event_type, json.dumps(payload), now),
            )
            return True

    def save_cell(self, cell: GridCell) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO cells (
                    strategy_id, cell_id, cell_index, buy_price, sell_price, stage,
                    entry_order_id, exit_order_id, entry_client_id, exit_client_id,
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, budget_usdt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(strategy_id, cell_id) DO UPDATE SET
                    cell_index=excluded.cell_index, buy_price=excluded.buy_price,
                    sell_price=excluded.sell_price, stage=excluded.stage,
                    entry_order_id=excluded.entry_order_id, exit_order_id=excluded.exit_order_id,
                    entry_client_id=excluded.entry_client_id, exit_client_id=excluded.exit_client_id,
                    open_qty=excluded.open_qty, exit_executed_qty=excluded.exit_executed_qty,
                    entry_filled_at=excluded.entry_filled_at, cycle_count=excluded.cycle_count,
                    budget_usdt=excluded.budget_usdt,
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
                    open_qty, exit_executed_qty, entry_filled_at, cycle_count, budget_usdt, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

    def get_cell(self, strategy_id: str, cell_id: str) -> GridCell | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchone()
        return None if row is None else self._cell_from_row(row)

    def get_cell_price_history(self, strategy_id: str) -> dict[str, tuple[Decimal, Decimal]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT cell_id, buy_price, sell_price FROM cell_price_history WHERE strategy_id=?",
                (strategy_id,),
            ).fetchall()
        return {
            row["cell_id"]: (Decimal(row["buy_price"]), Decimal(row["sell_price"]))
            for row in rows
        }

    def list_all_cells(self) -> list[GridCell]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cells ORDER BY strategy_id, cell_index"
            ).fetchall()
        return [self._cell_from_row(row) for row in rows]

    def create_capital_action(
        self,
        action_id: str,
        strategy_id: str,
        operation: str,
        amount_usdt: Decimal,
        expected_sell_cell_ids: list[str] | None = None,
    ) -> dict:
        if operation not in {"seed", "topup"} or amount_usdt <= 0:
            raise ValueError("invalid capital action")
        if operation == "topup":
            from ..domain.grid import fixed_cell_budgets
            config_for_amount = self.get_strategy(strategy_id)
            if config_for_amount is None:
                raise KeyError(strategy_id)
            fixed_cell_budgets(amount_usdt, config_for_amount.grid_count)
        now = utc_now()
        with self.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM capital_actions WHERE id=?", (action_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["strategy_id"] != strategy_id
                    or existing["operation"] != operation
                    or Decimal(existing["amount_usdt"]) != amount_usdt
                    or (
                        expected_sell_cell_ids is not None
                        and json.loads(existing["expected_sell_cell_ids_json"] or "[]") != expected_sell_cell_ids
                    )
                ):
                    raise ValueError("capital action id was used for different parameters")
                return dict(existing)
            config = conn.execute(
                "SELECT grid_kind, status FROM strategies WHERE strategy_id=? AND deleted_at IS NULL",
                (strategy_id,),
            ).fetchone()
            if config is None:
                raise KeyError(strategy_id)
            if config["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value:
                raise ValueError("capital actions require a fixed equal-USDT grid")
            if config["status"] not in {
                StrategyStatus.STARTING.value,
                StrategyStatus.RUNNING.value,
                StrategyStatus.ERROR.value,
            }:
                raise ValueError("strategy must be active")
            unresolved = conn.execute(
                "SELECT id FROM capital_actions WHERE strategy_id=? AND status='manual_review' LIMIT 1",
                (strategy_id,),
            ).fetchone()
            if unresolved is not None:
                raise ValueError("an earlier capital action requires manual review")
            if operation == "topup":
                seed = conn.execute(
                    "SELECT status FROM capital_actions WHERE strategy_id=? AND operation='seed' ORDER BY created_at LIMIT 1",
                    (strategy_id,),
                ).fetchone()
                if seed is None or seed["status"] != "applied":
                    raise ValueError("initial position has not been established")
            conn.execute(
                """
                INSERT INTO capital_actions(id, strategy_id, operation, amount_usdt,
                    status, expected_sell_cell_ids_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'requested', ?, ?, ?)
                """,
                (action_id, strategy_id, operation, str(amount_usdt),
                 None if expected_sell_cell_ids is None else json.dumps(expected_sell_cell_ids), now, now),
            )
            row = conn.execute("SELECT * FROM capital_actions WHERE id=?", (action_id,)).fetchone()
        return dict(row)

    def activate_triggered_fixed_grid(self, strategy_id: str) -> dict:
        """Record a zero-position seed once, without creating an exchange order."""
        action_id = f"seed:{strategy_id}"
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM capital_actions WHERE id=?", (action_id,),
            ).fetchone()
            if row is not None:
                if row["status"] != "applied" or row["order_id"] is not None:
                    raise ValueError("triggered grid seed conflicts with an existing capital action")
                return dict(row)
            config = conn.execute(
                """SELECT grid_kind, fixed_entry_mode, grid_budget_usdt, status
                   FROM strategies WHERE strategy_id=? AND deleted_at IS NULL""",
                (strategy_id,),
            ).fetchone()
            if (config is None or config["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value
                    or config["fixed_entry_mode"] != FixedEntryMode.PRICE_TRIGGERED.value
                    or config["status"] not in {
                        StrategyStatus.STARTING.value, StrategyStatus.RUNNING.value,
                        StrategyStatus.ERROR.value,
                    }):
                raise ValueError("triggered fixed grid must be active before initialization")
            conn.execute(
                """INSERT INTO capital_actions(
                    id, strategy_id, operation, amount_usdt, status,
                    planned_allocations_json, target_qty, executed_qty, created_at, updated_at
                ) VALUES (?, ?, 'seed', ?, 'applied', '{}', '0', '0', ?, ?)""",
                (action_id, strategy_id, config["grid_budget_usdt"], now, now),
            )
            conn.execute(
                """INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at)
                   VALUES (?, NULL, 'TRIGGERED_GRID_ACTIVATED', ?, ?)""",
                (strategy_id, json.dumps({"entry_mode": "price_triggered", "initial_position_qty": "0"}), now),
            )
            return dict(conn.execute(
                "SELECT * FROM capital_actions WHERE id=?", (action_id,),
            ).fetchone())

    def arm_triggered_fixed_cells(self, strategy_id: str, mark: Decimal) -> int:
        """Persist every crossed buy level; exchange orders use the rolling window."""
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            config = conn.execute(
                "SELECT fixed_entry_mode, status FROM strategies WHERE strategy_id=? AND deleted_at IS NULL",
                (strategy_id,),
            ).fetchone()
            if (config is None or config["fixed_entry_mode"] != FixedEntryMode.PRICE_TRIGGERED.value
                    or config["status"] not in {
                        StrategyStatus.STARTING.value,
                        StrategyStatus.RUNNING.value,
                        StrategyStatus.ERROR.value,
                    }):
                raise ValueError("price-triggered fixed grid is not active")
            cells = conn.execute(
                "SELECT cell_id, buy_price FROM cells WHERE strategy_id=? AND stage=?",
                (strategy_id, CellStage.UNTRIGGERED.value),
            ).fetchall()
            armed = [row for row in cells if mark >= Decimal(row["buy_price"])]
            conn.executemany(
                "UPDATE cells SET stage=?, updated_at=? WHERE strategy_id=? AND cell_id=?",
                [(CellStage.PENDING_ENTRY.value, now, strategy_id, row["cell_id"]) for row in armed],
            )
            conn.executemany(
                """INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at)
                   VALUES (?, ?, 'ENTRY_TRIGGERED', ?, ?)""",
                [(strategy_id, row["cell_id"],
                  json.dumps({"buy_price": row["buy_price"], "mark_price": str(mark)}), now)
                 for row in armed],
            )
            return len(armed)

    def set_upper_seed_count(self, strategy_id: str, count: int) -> StrategyConfig:
        if count not in {0, 3, 4, 5}:
            raise ValueError("upper seed count must be 0 or 3–5")
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM strategies WHERE strategy_id=? AND deleted_at IS NULL",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise KeyError(strategy_id)
            if (row["grid_kind"] != GridKind.FIXED_EQUAL_USDT.value
                    or row["fixed_entry_mode"] != FixedEntryMode.PRICE_TRIGGERED.value
                    or row["archived"]):
                raise ValueError("upper seed requires an active price-triggered fixed grid")
            old_count = int(row["upper_seed_count"])
            if old_count != count:
                frontier = row["upper_seed_frontier"] if old_count and count else None
                conn.execute(
                    "UPDATE strategies SET upper_seed_count=?, upper_seed_frontier=?, updated_at=? WHERE strategy_id=?",
                    (count, frontier, now, strategy_id),
                )
                conn.execute(
                    """INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at)
                       VALUES (?, NULL, 'UPPER_SEED_CONFIG_CHANGED', ?, ?)""",
                    (strategy_id, json.dumps({"old_count": old_count, "new_count": count}), now),
                )
        config = self.get_strategy(strategy_id)
        assert config is not None
        return config

    def upper_seed_active_count(self, strategy_id: str) -> int:
        with self.connection() as conn:
            return int(conn.execute(
                """SELECT COUNT(*) FROM upper_seed_slots AS slot
                   JOIN cells AS cell ON cell.strategy_id=slot.strategy_id AND cell.cell_id=slot.cell_id
                   WHERE slot.strategy_id=? AND cell.cycle_count=slot.cycle_count""",
                (strategy_id,),
            ).fetchone()[0])

    def list_active_upper_seed_cell_ids(self, strategy_id: str) -> list[str]:
        with self.connection() as conn:
            return [row[0] for row in conn.execute(
                """SELECT slot.cell_id FROM upper_seed_slots AS slot
                   JOIN cells AS cell ON cell.strategy_id=slot.strategy_id AND cell.cell_id=slot.cell_id
                   WHERE slot.strategy_id=? AND cell.cycle_count=slot.cycle_count
                   ORDER BY cell.cell_index""",
                (strategy_id,),
            )]

    def arm_upper_seed_cells(self, strategy_id: str, mark: Decimal) -> int:
        """Arm only the next few cells above the highest observed buy level.

        The frontier never moves down, so a price decline cannot repeatedly
        prebuy fresh cells. Ordinary entry order recovery handles execution.
        """
        now = utc_now()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            config = conn.execute(
                """SELECT fixed_entry_mode, status, lower_price, upper_price,
                          upper_seed_count, upper_seed_frontier
                   FROM strategies WHERE strategy_id=? AND deleted_at IS NULL""",
                (strategy_id,),
            ).fetchone()
            if (config is None or config["fixed_entry_mode"] != FixedEntryMode.PRICE_TRIGGERED.value
                    or config["status"] not in {
                        StrategyStatus.STARTING.value, StrategyStatus.RUNNING.value,
                        StrategyStatus.ERROR.value,
                    }):
                raise ValueError("price-triggered fixed grid is not active")
            count = int(config["upper_seed_count"])
            if not count or not (Decimal(config["lower_price"]) <= mark <= Decimal(config["upper_price"])):
                return 0
            cells = conn.execute(
                "SELECT cell_id, cell_index, buy_price, stage, cycle_count FROM cells WHERE strategy_id=? ORDER BY cell_index",
                (strategy_id,),
            ).fetchall()
            crossed = max(
                (int(cell["cell_index"]) for cell in cells if Decimal(cell["buy_price"]) <= mark),
                default=0,
            )
            prior = config["upper_seed_frontier"]
            frontier = max(crossed, int(prior)) if prior is not None else crossed
            if prior is None or frontier != int(prior):
                conn.execute(
                    "UPDATE strategies SET upper_seed_frontier=?, updated_at=? WHERE strategy_id=?",
                    (frontier, now, strategy_id),
                )
            active_slots = conn.execute(
                """SELECT COUNT(*) FROM upper_seed_slots AS slot
                   JOIN cells AS cell ON cell.strategy_id=slot.strategy_id AND cell.cell_id=slot.cell_id
                   WHERE slot.strategy_id=? AND cell.cycle_count=slot.cycle_count""",
                (strategy_id,),
            ).fetchone()[0]
            # Lowering the setting does not force-close already held cells.
            # It simply blocks new upper buys until enough old slots finish.
            free_slots = max(0, count - active_slots)
            armed = [
                cell for cell in cells
                if frontier < int(cell["cell_index"]) <= frontier + count
                and cell["stage"] == CellStage.UNTRIGGERED.value
            ][:free_slots]
            conn.executemany(
                "INSERT INTO upper_seed_slots(strategy_id, cell_id, cycle_count, created_at) VALUES (?, ?, ?, ?)",
                [(strategy_id, cell["cell_id"], int(cell["cycle_count"]), now) for cell in armed],
            )
            conn.executemany(
                "UPDATE cells SET stage=?, updated_at=? WHERE strategy_id=? AND cell_id=?",
                [(CellStage.PENDING_ENTRY.value, now, strategy_id, cell["cell_id"]) for cell in armed],
            )
            conn.executemany(
                """INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at)
                   VALUES (?, ?, 'UPPER_SEED_TRIGGERED', ?, ?)""",
                [(strategy_id, cell["cell_id"], json.dumps({
                    "buy_price": cell["buy_price"], "mark_price": str(mark),
                    "frontier_index": frontier, "upper_seed_count": count,
                }), now) for cell in armed],
            )
            return len(armed)

    def get_capital_action(self, action_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM capital_actions WHERE id=?", (action_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_capital_actions(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM capital_actions WHERE strategy_id=? ORDER BY created_at, id",
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_capital_action_strategy_ids(self) -> set[str]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT strategy_id FROM capital_actions WHERE status IN ('requested', 'planned', 'submitted')"
            ).fetchall()
        return {row["strategy_id"] for row in rows}

    def plan_capital_action(
        self,
        action_id: str,
        sell_cell_ids: list[str],
        allocations: dict[str, Decimal],
        target_qty: Decimal,
        client_order_id: str,
    ) -> None:
        if set(allocations) != set(sell_cell_ids) or sum(allocations.values(), Decimal("0")) != target_qty:
            raise ValueError("planned capital allocation does not match aggregate quantity")
        with self.connection() as conn:
            result = conn.execute(
                """
                UPDATE capital_actions SET status='planned', sell_cell_ids_json=?,
                    planned_allocations_json=?, target_qty=?, client_order_id=?, updated_at=?
                WHERE id=? AND status='requested'
                """,
                (
                    json.dumps(sell_cell_ids),
                    json.dumps({cell_id: str(qty) for cell_id, qty in allocations.items()}),
                    str(target_qty), client_order_id, utc_now(), action_id,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("capital action is not requested")

    def mark_capital_action_submitted(self, action_id: str, order_id: int | None = None) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                UPDATE capital_actions SET status='submitted',
                    order_id=COALESCE(?, order_id), updated_at=?
                WHERE id=? AND status IN ('planned', 'submitted')
                """,
                (order_id, utc_now(), action_id),
            )

    def mark_capital_action_review(self, action_id: str, message: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE capital_actions SET status='manual_review', message=?, updated_at=? WHERE id=? AND status!='applied'",
                (message, utc_now(), action_id),
            )

    def mark_capital_action_rejected(self, action_id: str, message: str) -> None:
        with self.connection() as conn:
            conn.execute(
                "UPDATE capital_actions SET status='rejected', message=?, updated_at=? WHERE id=? AND status='requested'",
                (message, utc_now(), action_id),
            )

    def apply_capital_action(self, action_id: str, allocations: dict[str, Decimal], executed_qty: Decimal) -> None:
        """Apply a confirmed aggregate fill and all cell budgets exactly once."""
        with self.connection() as conn:
            action = conn.execute(
                "SELECT * FROM capital_actions WHERE id=?", (action_id,)
            ).fetchone()
            if action is None:
                raise KeyError(action_id)
            if action["status"] == "applied":
                return
            if action["status"] not in {"planned", "submitted"}:
                raise ValueError("capital action is not ready to apply")
            cells = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? ORDER BY cell_index",
                (action["strategy_id"],),
            ).fetchall()
            from ..domain.grid import fixed_cell_budgets
            increments = fixed_cell_budgets(Decimal(action["amount_usdt"]), len(cells))
            sell_ids = set(json.loads(action["sell_cell_ids_json"]))
            if set(allocations) != sell_ids or sum(allocations.values(), Decimal("0")) != executed_qty:
                raise ValueError("capital allocation does not match the aggregate fill")
            for row, increment in zip(cells, increments):
                added_qty = allocations.get(row["cell_id"], Decimal("0"))
                new_budget = Decimal(row["budget_usdt"]) + (
                    increment if action["operation"] == "topup" else Decimal("0")
                )
                new_qty = Decimal(row["open_qty"]) + added_qty
                stage = row["stage"]
                if new_qty > 0 and added_qty > 0:
                    stage = CellStage.PENDING_EXIT.value
                elif stage == CellStage.UNTRIGGERED.value and action["operation"] == "seed":
                    stage = CellStage.PENDING_ENTRY.value
                conn.execute(
                    "UPDATE cells SET budget_usdt=?, open_qty=?, stage=?, updated_at=? WHERE strategy_id=? AND cell_id=?",
                    (str(new_budget), str(new_qty), stage, utc_now(), action["strategy_id"], row["cell_id"]),
                )
            if action["operation"] == "topup":
                strategy = conn.execute(
                    "SELECT grid_budget_usdt FROM strategies WHERE strategy_id=?",
                    (action["strategy_id"],),
                ).fetchone()
                new_total = Decimal(strategy["grid_budget_usdt"]) + Decimal(action["amount_usdt"])
                conn.execute(
                    "UPDATE strategies SET grid_budget_usdt=?, order_usdt=?, updated_at=? WHERE strategy_id=?",
                    (str(new_total), str(new_total / Decimal(len(cells))), utc_now(), action["strategy_id"]),
                )
            conn.execute(
                "UPDATE capital_actions SET status='applied', executed_qty=?, updated_at=? WHERE id=?",
                (str(executed_qty), utc_now(), action_id),
            )

    def set_auto_reinvest_enabled(self, strategy_id: str, enabled: bool) -> None:
        self.set_auto_reinvest_config(strategy_id, enabled=enabled)

    def ensure_reinvest_cutover(self, strategy_id: str) -> bool:
        """Exclude old profits/spends at the first tick of the new engine."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute(
                "SELECT 1 FROM reinvest_cutovers WHERE strategy_id=?", (strategy_id,)
            ).fetchone():
                return False
            strategy = conn.execute(
                "SELECT 1 FROM strategies WHERE strategy_id=? AND grid_kind=?",
                (strategy_id, GridKind.FIXED_EQUAL_USDT.value),
            ).fetchone()
            if strategy is None:
                raise ValueError("fixed grid not found for reinvest cutover")
            conn.execute(
                """
                INSERT INTO cell_reinvest_checkpoints(
                    strategy_id, cell_id, last_old_cycle_count, last_old_reinvestment_id
                )
                SELECT c.strategy_id, c.cell_id, c.cycle_count,
                       COALESCE(MAX(r.id), 0)
                FROM cells c LEFT JOIN cell_reinvestments r
                    ON r.strategy_id=c.strategy_id AND r.cell_id=c.cell_id
                WHERE c.strategy_id=? GROUP BY c.strategy_id, c.cell_id
                """,
                (strategy_id,),
            )
            conn.execute(
                "INSERT INTO reinvest_cutovers(strategy_id, created_at) VALUES (?, ?)",
                (strategy_id, utc_now()),
            )
            return True

    def reinvest_cutover_cycles(self, strategy_id: str) -> dict[str, int]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT cell_id, last_old_cycle_count FROM cell_reinvest_checkpoints WHERE strategy_id=?",
                (strategy_id,),
            ).fetchall()
        return {row["cell_id"]: int(row["last_old_cycle_count"]) for row in rows}

    def set_auto_reinvest_config(
        self, strategy_id: str, *, enabled: bool,
        auto_reinvest_percent: Decimal | None = None,
    ) -> Decimal:
        if auto_reinvest_percent is not None and (
            not auto_reinvest_percent.is_finite()
            or not Decimal("0") <= auto_reinvest_percent <= Decimal("100")
        ):
            raise ValueError("auto_reinvest_percent must be between 0 and 100")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                """
                SELECT auto_reinvest_percent FROM strategies
                WHERE strategy_id=? AND grid_kind=? AND deleted_at IS NULL AND archived=0
                """,
                (strategy_id, GridKind.FIXED_EQUAL_USDT.value),
            ).fetchone()
            if current is None:
                raise ValueError("active fixed grid not found")
            percent = (
                Decimal(current["auto_reinvest_percent"])
                if auto_reinvest_percent is None else auto_reinvest_percent
            )
            result = conn.execute(
                """
                UPDATE strategies SET auto_reinvest_enabled=?, auto_reinvest_percent=?, updated_at=?
                WHERE strategy_id=? AND grid_kind=? AND deleted_at IS NULL AND archived=0
                """,
                (int(enabled), str(percent), utc_now(), strategy_id, GridKind.FIXED_EQUAL_USDT.value),
            )
            if result.rowcount != 1:
                raise ValueError("active fixed grid not found")
            return percent

    def set_auto_reinvest_settings(
        self, strategy_id: str, *, enabled: bool,
        auto_reinvest_percent: Decimal | None = None,
    ) -> Decimal:
        """Keep the Binance branch's settings API during branch integration."""
        return self.set_auto_reinvest_config(
            strategy_id, enabled=enabled,
            auto_reinvest_percent=auto_reinvest_percent,
        )

    def book_cell_reinvest_earnings(
        self, strategy_id: str, cell_id: str, cycle_count: int,
        net_profit_usdt: Decimal, auto_reinvest_percent: Decimal,
    ) -> bool:
        """Permanently earmark this completed cycle's chosen share of net profit."""
        if cycle_count < 1 or not net_profit_usdt.is_finite():
            raise ValueError("invalid completed-cycle profit")
        if not auto_reinvest_percent.is_finite() or not Decimal("0") <= auto_reinvest_percent <= Decimal("100"):
            raise ValueError("auto_reinvest_percent must be between 0 and 100")
        eligible = net_profit_usdt * auto_reinvest_percent / Decimal("100")
        with self.connection() as conn:
            checkpoint = conn.execute(
                "SELECT last_old_cycle_count FROM cell_reinvest_checkpoints WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchone()
            if not conn.execute(
                "SELECT 1 FROM reinvest_cutovers WHERE strategy_id=?", (strategy_id,)
            ).fetchone():
                return False
            if checkpoint is not None and cycle_count <= int(checkpoint["last_old_cycle_count"]):
                return False
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO cell_reinvest_earnings(
                    strategy_id, cell_id, cycle_count, net_profit_usdt,
                    eligible_profit_usdt, auto_reinvest_percent, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, cell_id, cycle_count, str(net_profit_usdt),
                 str(eligible), str(auto_reinvest_percent), utc_now()),
            )
            return cursor.rowcount == 1

    def record_cell_reinvest_earning(
        self, strategy_id: str, cell_id: str, cycle_count: int,
        net_profit_usdt: Decimal, auto_reinvest_percent: Decimal,
    ) -> bool:
        """Keep the Binance branch's earning API during branch integration."""
        return self.book_cell_reinvest_earnings(
            strategy_id, cell_id, cycle_count,
            net_profit_usdt, auto_reinvest_percent,
        )

    def list_cell_reinvest_earnings(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cell_reinvest_earnings WHERE strategy_id=? ORDER BY cell_id, cycle_count",
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_cell_reinvestments(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM cell_reinvestments WHERE strategy_id=? ORDER BY id",
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def apply_cell_reinvestment(
        self,
        strategy_id: str,
        cell_id: str,
        cycle_count: int,
        quantity_step: Decimal,
        profit_snapshot_usdt: Decimal,
    ) -> bool:
        """Spend one earned quantity step atomically before the next entry."""
        if quantity_step <= 0 or cycle_count < 1:
            raise ValueError("invalid reinvestment step or cycle")
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            strategy = conn.execute(
                """
                SELECT grid_budget_usdt, grid_count, auto_reinvest_enabled
                FROM strategies WHERE strategy_id=? AND grid_kind=? AND status IN (?, ?)
                AND archived=0 AND deleted_at IS NULL
                """,
                (strategy_id, GridKind.FIXED_EQUAL_USDT.value,
                 StrategyStatus.RUNNING.value, StrategyStatus.STARTING.value),
            ).fetchone()
            if strategy is None or not strategy["auto_reinvest_enabled"]:
                return False
            cell = conn.execute(
                "SELECT * FROM cells WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchone()
            if (
                cell is None or cell["stage"] not in {
                    CellStage.UNTRIGGERED.value, CellStage.PENDING_ENTRY.value,
                }
                or Decimal(cell["open_qty"]) != 0
                or (cell["entry_client_id"] and cell["entry_order_id"] is None)
                or int(cell["cycle_count"]) != cycle_count
            ):
                return False
            existing_cycle = conn.execute(
                "SELECT 1 FROM cell_reinvestments WHERE strategy_id=? AND cell_id=? AND cycle_count=?",
                (strategy_id, cell_id, cycle_count),
            ).fetchone()
            if existing_cycle is not None:
                return False
            earned_rows = conn.execute(
                "SELECT net_profit_usdt, eligible_profit_usdt FROM cell_reinvest_earnings WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchall()
            eligible = sum((Decimal(row["eligible_profit_usdt"]) for row in earned_rows), Decimal("0"))
            net_earned = sum((Decimal(row["net_profit_usdt"]) for row in earned_rows), Decimal("0"))
            checkpoint = conn.execute(
                "SELECT last_old_reinvestment_id FROM cell_reinvest_checkpoints WHERE strategy_id=? AND cell_id=?",
                (strategy_id, cell_id),
            ).fetchone()
            old_reinvest_id = int(checkpoint["last_old_reinvestment_id"]) if checkpoint else 0
            spent_rows = conn.execute(
                "SELECT amount_usdt FROM cell_reinvestments WHERE strategy_id=? AND cell_id=? AND id>?",
                (strategy_id, cell_id, old_reinvest_id),
            ).fetchall()
            spent = sum((Decimal(row["amount_usdt"]) for row in spent_rows), Decimal("0"))
            amount = Decimal(cell["buy_price"]) * quantity_step
            if eligible - spent < amount or net_earned - spent < amount:
                return False
            now = utc_now()
            new_budget = Decimal(cell["budget_usdt"]) + amount
            new_total = Decimal(strategy["grid_budget_usdt"]) + amount
            conn.execute(
                "UPDATE cells SET budget_usdt=?, updated_at=? WHERE strategy_id=? AND cell_id=?",
                (str(new_budget), now, strategy_id, cell_id),
            )
            conn.execute(
                "UPDATE strategies SET grid_budget_usdt=?, order_usdt=?, updated_at=? WHERE strategy_id=?",
                (str(new_total), str(new_total / Decimal(strategy["grid_count"])), now, strategy_id),
            )
            conn.execute(
                """
                INSERT INTO cell_reinvestments(
                    strategy_id, cell_id, cycle_count, quantity_step,
                    amount_usdt, profit_snapshot_usdt, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (strategy_id, cell_id, cycle_count, str(quantity_step),
                 str(amount), str(profit_snapshot_usdt), now),
            )
            conn.execute(
                "INSERT INTO events(strategy_id, cell_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (strategy_id, cell_id, "CELL_PROFIT_REINVESTED",
                 json.dumps({"cycle_count": cycle_count, "quantity_step": str(quantity_step),
                             "amount_usdt": str(amount), "profit_snapshot_usdt": str(profit_snapshot_usdt),
                             "eligible_profit_usdt": str(eligible)}), now),
            )
            return True

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

    def append_cycle_close_event(
        self, strategy_id: str, cell_id: str, event_type: str,
        payload: dict, cycle_count: int, run_id: str | None,
    ) -> int:
        """Record the completed cycle with the policy committed at that instant."""
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT auto_reinvest_percent FROM strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise ValueError("strategy not found for cycle close")
            enriched = {
                **payload,
                "cycle_count": cycle_count,
                "auto_reinvest_percent": row["auto_reinvest_percent"],
            }
            cursor = conn.execute(
                "INSERT INTO events(strategy_id, run_id, cell_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (strategy_id, run_id, cell_id, event_type,
                 json.dumps(enriched, ensure_ascii=False), utc_now()),
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

    def get_first_started_at(self, strategy_id: str) -> str | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT first_started_at FROM strategies WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else row["first_started_at"]

    def get_strategy_trade_sync(self, strategy_id: str) -> dict | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT history_complete, scanned_through_ms FROM strategy_trade_sync WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else dict(row)

    def save_strategy_trade_sync(
        self, strategy_id: str, history_complete: bool, scanned_through_ms: int
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO strategy_trade_sync(strategy_id, history_complete, scanned_through_ms)
                VALUES (?, ?, ?)
                ON CONFLICT(strategy_id) DO UPDATE SET
                    history_complete=excluded.history_complete,
                    scanned_through_ms=excluded.scanned_through_ms
                """,
                (strategy_id, int(history_complete), scanned_through_ms),
            )

    def latest_strategy_trade_id(self, strategy_id: str) -> int | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT MAX(trade_id) AS latest FROM strategy_trade_fills WHERE strategy_id=?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else row["latest"]

    def save_strategy_trades(self, strategy_id: str, trades: list[dict]) -> None:
        if not trades:
            return
        with self.connection() as conn:
            conn.executemany(
                """
                INSERT OR IGNORE INTO strategy_trade_fills(
                    strategy_id, trade_id, order_id, side, quantity, price,
                    realized_pnl, commission, commission_asset, traded_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        strategy_id, int(trade["id"]), int(trade["orderId"]),
                        str(trade["side"]), str(trade["qty"]), str(trade["price"]),
                        str(trade["realizedPnl"]), str(trade["commission"]),
                        str(trade["commissionAsset"]), int(trade["time"]),
                    )
                    for trade in trades
                ],
            )

    def list_strategy_trades(self, strategy_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM strategy_trade_fills WHERE strategy_id=? ORDER BY trade_id",
                (strategy_id,),
            ).fetchall()
        return [dict(row) for row in rows]

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
            venue=ExchangeVenue(row["venue"]) if "venue" in row.keys() else ExchangeVenue.BINANCE,
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
            grid_kind=GridKind(row["grid_kind"]) if "grid_kind" in row.keys() else GridKind.TRIGGERED,
            fixed_entry_mode=(FixedEntryMode(row["fixed_entry_mode"])
                              if "fixed_entry_mode" in row.keys() else FixedEntryMode.SEEDED),
            lower_price=Decimal(row["lower_price"]) if "lower_price" in row.keys() and row["lower_price"] is not None else None,
            upper_price=Decimal(row["upper_price"]) if "upper_price" in row.keys() and row["upper_price"] is not None else None,
            grid_budget_usdt=Decimal(row["grid_budget_usdt"]) if "grid_budget_usdt" in row.keys() and row["grid_budget_usdt"] is not None else None,
            auto_reinvest_enabled=bool(row["auto_reinvest_enabled"]) if "auto_reinvest_enabled" in row.keys() else False,
            auto_reinvest_percent=Decimal(row["auto_reinvest_percent"]) if "auto_reinvest_percent" in row.keys() else Decimal("100"),
            grid_generation=int(row["grid_generation"]) if "grid_generation" in row.keys() else 0,
            upper_seed_count=int(row["upper_seed_count"]) if "upper_seed_count" in row.keys() else 0,
            upper_seed_frontier=(int(row["upper_seed_frontier"])
                                 if "upper_seed_frontier" in row.keys() and row["upper_seed_frontier"] is not None else None),
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
            str(cell.budget_usdt),
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
            budget_usdt=Decimal(row["budget_usdt"]) if "budget_usdt" in row.keys() else Decimal("0"),
        )
