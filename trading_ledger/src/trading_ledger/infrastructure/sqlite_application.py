"""SQLite implementation of the trading-ledger application port."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

from trading_ledger.application.contracts import (
    AccountSummaryView,
    AddTrackedInstrumentCommand,
    ApplicationError,
    ArchiveProjectCommand,
    ArchiveTrackedInstrumentCommand,
    CashEntryView,
    CloseTrackedInstrumentCommand,
    ConfirmManualTradeCommand,
    CreateProjectCommand,
    DailyValuationPointView,
    DailyValuationView,
    ErrorCode,
    ExpireTrackingCommand,
    GetAccountSummaryQuery,
    GetMonthlyStatisticsQuery,
    GetProjectQuery,
    GetTradeQuery,
    InstrumentIdentityView,
    ListOperationHistoryQuery,
    ListPositionsQuery,
    ListProjectsQuery,
    ListStatisticsMonthsQuery,
    ListTrackingQuery,
    MonthlyInstrumentStatisticsView,
    MonthlyStatisticsView,
    OperationHistoryPageView,
    OperationHistoryRowView,
    PositionView,
    PreviewManualTradeCommand,
    PriceRefreshView,
    PROJECT_COLOR_KEYS,
    ProjectView,
    RecordCashEntryCommand,
    RecordConfirmedTradeCommand,
    RecordDailyValuationCommand,
    RecordReferencePriceCommand,
    ReferencePriceView,
    RefreshTrackingPricesCommand,
    RestoreProjectCommand,
    ReverseTradeCommand,
    TrackingPageView,
    TrackingRowView,
    TrackingSummaryView,
    TradePreviewView,
    TradeView,
    UpdateProjectCommand,
    UpdateTrackedInstrumentCommand,
)
from trading_ledger.domain import (
    AccountStatus,
    CashEntryType,
    ExecutionSource,
    OperationKind,
    ProjectStatus,
    RatioBasis,
    TradeRecordStatus,
    TradeSide,
    TrackingStatus,
    calculate_buy,
    calculate_fees,
    calculate_sell,
    round_money,
)

from .database import LedgerDatabase
from .quotes import PublicQuoteProvider, QuoteProvider


def _decimal(value: object | None, default: str = "0") -> Decimal:
    return Decimal(default) if value is None else Decimal(str(value))


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SQLiteTradingLedgerApplication:
    def __init__(
        self,
        database: LedgerDatabase,
        *,
        timezone_name: str = "Asia/Shanghai",
        quote_provider: QuoteProvider | None = None,
    ) -> None:
        self.database = database
        self.timezone = ZoneInfo(timezone_name)
        self.quote_provider = quote_provider or PublicQuoteProvider(timezone_name)

    def initialize(self) -> None:
        self.database.initialize()

    def _now(self) -> datetime:
        return datetime.now(self.timezone).replace(microsecond=0)

    def _time(self, value: datetime | None) -> datetime:
        if value is None:
            return self._now()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=self.timezone, microsecond=0)
        return value.astimezone(self.timezone).replace(microsecond=0)

    @staticmethod
    def _normalize_symbol(symbol: str) -> tuple[str, str, str]:
        value = symbol.strip().upper().replace(".SS", ".SH")
        if value.isdigit() and len(value) == 6:
            if value.startswith(("4", "8", "920")):
                value += ".BJ"
            elif value.startswith(("5", "6", "9")):
                value += ".SH"
            else:
                value += ".SZ"
        if "." not in value:
            raise ApplicationError(
                ErrorCode.INVALID_INPUT, "股票代码格式不正确。", field="symbol"
            )
        code, venue = value.rsplit(".", 1)
        if len(code) != 6 or not code.isdigit() or venue not in {"SH", "SZ", "BJ"}:
            raise ApplicationError(
                ErrorCode.INVALID_INPUT, "股票代码格式不正确。", field="symbol"
            )
        return value, "CN_STOCK", venue

    @staticmethod
    def _project(
        connection: sqlite3.Connection,
        project_key: str,
        *,
        writable: bool = False,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM projects WHERE project_key = ?", (project_key,)
        ).fetchone()
        if row is None:
            raise ApplicationError(ErrorCode.PROJECT_NOT_FOUND, "项目不存在。")
        if writable and row["status"] != ProjectStatus.ACTIVE:
            raise ApplicationError(ErrorCode.PROJECT_ARCHIVED, "项目已归档。")
        return row

    @staticmethod
    def _account(
        connection: sqlite3.Connection,
        project_id: int,
        account_code: str,
        *,
        writable: bool = False,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM accounts WHERE project_id = ? AND account_code = ?",
            (project_id, account_code),
        ).fetchone()
        if row is None:
            raise ApplicationError(ErrorCode.ACCOUNT_NOT_FOUND, "账户不存在。")
        if writable and row["status"] != AccountStatus.ACTIVE:
            raise ApplicationError(ErrorCode.ACCOUNT_UNAVAILABLE, "账户当前不可写。")
        return row

    @staticmethod
    def _instrument(
        connection: sqlite3.Connection, project_id: int, symbol: str
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT i.*
            FROM instruments i
            JOIN tracked_instruments t ON t.instrument_id = i.instrument_id
            WHERE t.project_id = ? AND i.symbol = ?
            """,
            (project_id, symbol),
        ).fetchone()
        if row is None:
            raise ApplicationError(
                ErrorCode.INSTRUMENT_NOT_FOUND, "当前项目中没有该股票。"
            )
        return row

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        project_id: int | None,
        object_type: str,
        object_id: str,
        action: str,
        actor: str,
        request_id: str | None = None,
        before: object | None = None,
        after: object | None = None,
        reason: str | None = None,
        occurred_at: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                audit_event_id, project_id, object_type, object_id, action,
                actor, request_id, before_json, after_json, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _identifier("audit"),
                project_id,
                object_type,
                object_id,
                action,
                actor,
                request_id,
                None if before is None else _json(before),
                None if after is None else _json(after),
                reason,
                occurred_at,
            ),
        )

    @staticmethod
    def _project_view(row: sqlite3.Row) -> ProjectView:
        return ProjectView(
            project_id=int(row["project_id"]),
            project_key=str(row["project_key"]),
            project_name=str(row["project_name"]),
            description=str(row["description"]),
            color_key=str(row["color_key"]),
            status=ProjectStatus(row["status"]),
            initial_capital=_decimal(row["initial_capital"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            archived_at=(
                datetime.fromisoformat(row["archived_at"])
                if row["archived_at"]
                else None
            ),
        )

    @staticmethod
    def _project_select() -> str:
        return """
            SELECT p.*, a.initial_capital
            FROM projects p
            JOIN accounts a ON a.project_id = p.project_id
                           AND a.account_code = 'primary-cny'
        """

    def create_project(self, command: CreateProjectCommand) -> ProjectView:
        now = self._now().isoformat()
        normalized_name = command.project_name.strip().casefold()
        project_key = f"project-{uuid4().hex[:12]}"
        with self.database.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM projects WHERE project_name_normalized = ?",
                (normalized_name,),
            ).fetchone():
                raise ApplicationError(
                    ErrorCode.DUPLICATE_PROJECT_NAME, "项目名称已存在。"
                )
            cursor = connection.execute(
                """
                INSERT INTO projects (
                    project_key, project_name, project_name_normalized,
                    description, status, created_at
                ) VALUES (?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (
                    project_key,
                    command.project_name.strip(),
                    normalized_name,
                    command.description.strip(),
                    now,
                ),
            )
            project_id = int(cursor.lastrowid)
            color_key = PROJECT_COLOR_KEYS[(project_id - 1) % len(PROJECT_COLOR_KEYS)]
            connection.execute(
                "UPDATE projects SET color_key = ? WHERE project_id = ?",
                (color_key, project_id),
            )
            account_cursor = connection.execute(
                """
                INSERT INTO accounts (
                    project_id, account_code, account_name, account_type,
                    market, base_currency, initial_capital, cash_balance,
                    status, opened_at, created_at, updated_at
                ) VALUES (?, 'primary-cny', '主账户', 'MANUAL', 'CN_STOCK',
                          'CNY', ?, ?, 'ACTIVE', ?, ?, ?)
                """,
                (
                    project_id,
                    _decimal_text(command.initial_capital),
                    _decimal_text(command.initial_capital),
                    now,
                    now,
                    now,
                ),
            )
            account_id = int(account_cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO cash_ledger (
                    cash_entry_id, project_id, account_id, entry_type, currency,
                    amount, balance_after, occurred_at, note, created_by, created_at
                ) VALUES (?, ?, ?, 'INITIAL_CAPITAL', 'CNY', ?, ?, ?, ?, ?, ?)
                """,
                (
                    _identifier("cash"),
                    project_id,
                    account_id,
                    _decimal_text(command.initial_capital),
                    _decimal_text(command.initial_capital),
                    now,
                    "项目初始资金",
                    command.actor.strip(),
                    now,
                ),
            )
            self._audit(
                connection,
                project_id=project_id,
                object_type="PROJECT",
                object_id=project_key,
                action="PROJECT_CREATED",
                actor=command.actor,
                after={
                    "project_key": project_key,
                    "project_name": command.project_name,
                    "color_key": color_key,
                },
                occurred_at=now,
            )
            row = connection.execute(
                self._project_select() + " WHERE p.project_id = ?", (project_id,)
            ).fetchone()
        return self._project_view(row)

    def update_project(self, command: UpdateProjectCommand) -> ProjectView:
        now = self._now().isoformat()
        normalized_name = command.project_name.strip().casefold()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            duplicate = connection.execute(
                """
                SELECT 1 FROM projects
                WHERE project_name_normalized = ? AND project_id <> ?
                """,
                (normalized_name, project["project_id"]),
            ).fetchone()
            if duplicate:
                raise ApplicationError(
                    ErrorCode.DUPLICATE_PROJECT_NAME, "项目名称已存在。"
                )
            before = {
                "project_name": project["project_name"],
                "description": project["description"],
                "color_key": project["color_key"],
            }
            color_key = command.color_key or str(project["color_key"])
            connection.execute(
                """
                UPDATE projects
                SET project_name = ?, project_name_normalized = ?, description = ?,
                    color_key = ?
                WHERE project_id = ?
                """,
                (
                    command.project_name.strip(),
                    normalized_name,
                    command.description.strip(),
                    color_key,
                    project["project_id"],
                ),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="PROJECT",
                object_id=command.project_key,
                action="PROJECT_UPDATED",
                actor=command.actor,
                before=before,
                after={
                    "project_name": command.project_name.strip(),
                    "description": command.description.strip(),
                    "color_key": color_key,
                },
                occurred_at=now,
            )
            row = connection.execute(
                self._project_select() + " WHERE p.project_id = ?",
                (project["project_id"],),
            ).fetchone()
        return self._project_view(row)

    def archive_project(self, command: ArchiveProjectCommand) -> ProjectView:
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            active_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM projects WHERE status = 'ACTIVE'"
                ).fetchone()[0]
            )
            if active_count <= 1:
                raise ApplicationError(
                    ErrorCode.CONCURRENCY_CONFLICT, "至少需要保留一个活跃项目。"
                )
            connection.execute(
                "UPDATE projects SET status = 'ARCHIVED', archived_at = ? WHERE project_id = ?",
                (now, project["project_id"]),
            )
            connection.execute(
                "UPDATE accounts SET status = 'ARCHIVED', updated_at = ? WHERE project_id = ?",
                (now, project["project_id"]),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="PROJECT",
                object_id=command.project_key,
                action="PROJECT_ARCHIVED",
                actor=command.actor,
                occurred_at=now,
            )
            row = connection.execute(
                self._project_select() + " WHERE p.project_id = ?",
                (project["project_id"],),
            ).fetchone()
        return self._project_view(row)

    def restore_project(self, command: RestoreProjectCommand) -> ProjectView:
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key)
            connection.execute(
                "UPDATE projects SET status = 'ACTIVE', archived_at = NULL WHERE project_id = ?",
                (project["project_id"],),
            )
            connection.execute(
                "UPDATE accounts SET status = 'ACTIVE', updated_at = ? WHERE project_id = ?",
                (now, project["project_id"]),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="PROJECT",
                object_id=command.project_key,
                action="PROJECT_RESTORED",
                actor=command.actor,
                occurred_at=now,
            )
            row = connection.execute(
                self._project_select() + " WHERE p.project_id = ?",
                (project["project_id"],),
            ).fetchone()
        return self._project_view(row)

    def list_projects(self, query: ListProjectsQuery) -> Sequence[ProjectView]:
        sql = self._project_select()
        params: tuple[object, ...] = ()
        if not query.include_archived:
            sql += " WHERE p.status = ?"
            params = (ProjectStatus.ACTIVE,)
        sql += " ORDER BY p.status = 'ARCHIVED', p.project_name_normalized"
        with self.database.read() as connection:
            return tuple(self._project_view(row) for row in connection.execute(sql, params))

    def get_project(self, query: GetProjectQuery) -> ProjectView:
        with self.database.read() as connection:
            self._project(connection, query.project_key)
            row = connection.execute(
                self._project_select() + " WHERE p.project_key = ?",
                (query.project_key,),
            ).fetchone()
            return self._project_view(row)

    def _upsert_instrument(
        self,
        connection: sqlite3.Connection,
        symbol: str,
        name: str,
        occurred_at: str,
    ) -> sqlite3.Row:
        normalized, market, venue = self._normalize_symbol(symbol)
        row = connection.execute(
            """
            SELECT * FROM instruments
            WHERE market = ? AND venue = ? AND symbol = ?
            """,
            (market, venue, normalized),
        ).fetchone()
        if row:
            connection.execute(
                """
                UPDATE instruments SET name = COALESCE(NULLIF(?, ''), name),
                    status = 'ACTIVE', updated_at = ?
                WHERE instrument_id = ?
                """,
                (name.strip(), occurred_at, row["instrument_id"]),
            )
            return connection.execute(
                "SELECT * FROM instruments WHERE instrument_id = ?",
                (row["instrument_id"],),
            ).fetchone()
        cursor = connection.execute(
            """
            INSERT INTO instruments (
                market, venue, symbol, name, currency, quantity_precision,
                price_precision, lot_size, t_plus_days, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'CNY', 0, 2, 100, 1, 'ACTIVE', ?, ?)
            """,
            (market, venue, normalized, name.strip(), occurred_at, occurred_at),
        )
        return connection.execute(
            "SELECT * FROM instruments WHERE instrument_id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    def lookup_instrument(self, symbol: str) -> InstrumentIdentityView:
        """Resolve an input code from quotes without writing any ledger data."""
        normalized, _, _ = self._normalize_symbol(symbol)
        quotes, _ = self.quote_provider.fetch_many((normalized,))
        quote = quotes.get(normalized)
        if quote is None or quote.symbol != normalized or not quote.name.strip():
            raise ApplicationError(
                ErrorCode.INSTRUMENT_NOT_FOUND,
                "暂时未获取到股票信息，可重试或手动填写名称。",
                retryable=True,
            )
        return InstrumentIdentityView(symbol=normalized, name=quote.name.strip())

    def add_tracking(self, command: AddTrackedInstrumentCommand) -> TrackingRowView:
        occurred_at = self._time(command.added_at)
        occurred_text = occurred_at.isoformat()
        expires_text = datetime.combine(
            occurred_at.date() + timedelta(days=5),
            time.max,
            tzinfo=self.timezone,
        ).replace(microsecond=0).isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            instrument = self._upsert_instrument(
                connection, command.symbol, command.name, occurred_text
            )
            existing = connection.execute(
                """
                SELECT * FROM tracked_instruments
                WHERE project_id = ? AND instrument_id = ?
                """,
                (project["project_id"], instrument["instrument_id"]),
            ).fetchone()
            if existing and existing["tracking_status"] in {
                TrackingStatus.WATCHING,
                TrackingStatus.HOLDING,
            }:
                raise ApplicationError(
                    ErrorCode.CONCURRENCY_CONFLICT, "该股票已在当前跟踪中。"
                )
            if existing:
                tracking_id = int(existing["tracking_id"])
                connection.execute(
                    """
                    UPDATE tracked_instruments
                    SET source_text = ?, tracking_status = 'WATCHING', added_at = ?,
                        expires_at = ?, updated_at = ?
                    WHERE tracking_id = ?
                    """,
                    (
                        command.source_text.strip(),
                        occurred_text,
                        expires_text,
                        occurred_text,
                        tracking_id,
                    ),
                )
                action = "TRACKING_REOPENED"
            else:
                cursor = connection.execute(
                    """
                    INSERT INTO tracked_instruments (
                        project_id, instrument_id, source_text, tracking_status,
                        added_at, expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, 'WATCHING', ?, ?, ?, ?)
                    """,
                    (
                        project["project_id"],
                        instrument["instrument_id"],
                        command.source_text.strip(),
                        occurred_text,
                        expires_text,
                        occurred_text,
                        occurred_text,
                    ),
                )
                tracking_id = int(cursor.lastrowid)
                action = "TRACKING_ADDED"
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="TRACKING",
                object_id=str(tracking_id),
                action=action,
                actor=command.actor,
                after={
                    "symbol": instrument["symbol"],
                    "name": instrument["name"],
                    "source_text": command.source_text.strip(),
                },
                occurred_at=occurred_text,
            )
        return self._tracking_row(command.project_key, tracking_id)

    def update_tracking(
        self, command: UpdateTrackedInstrumentCommand
    ) -> TrackingRowView:
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            current = connection.execute(
                """
                SELECT t.*, i.symbol, i.name
                FROM tracked_instruments t
                JOIN instruments i ON i.instrument_id = t.instrument_id
                WHERE t.project_id = ? AND t.tracking_id = ?
                """,
                (project["project_id"], command.tracking_id),
            ).fetchone()
            if current is None:
                raise ApplicationError(ErrorCode.TRACKING_NOT_FOUND, "跟踪记录不存在。")
            instrument = self._upsert_instrument(
                connection, command.symbol, command.name, now
            )
            if instrument["instrument_id"] != current["instrument_id"]:
                account = self._account(
                    connection, int(project["project_id"]), "primary-cny"
                )
                if self._position_quantity(
                    connection,
                    int(account["account_id"]),
                    int(current["instrument_id"]),
                ) > 0:
                    raise ApplicationError(
                        ErrorCode.CONCURRENCY_CONFLICT,
                        "仍有持仓时不能修改股票代码。",
                    )
            duplicate = connection.execute(
                """
                SELECT 1 FROM tracked_instruments
                WHERE project_id = ? AND instrument_id = ? AND tracking_id <> ?
                  AND tracking_status IN ('WATCHING', 'HOLDING')
                """,
                (
                    project["project_id"],
                    instrument["instrument_id"],
                    command.tracking_id,
                ),
            ).fetchone()
            if duplicate:
                raise ApplicationError(
                    ErrorCode.CONCURRENCY_CONFLICT, "该股票已在当前跟踪中。"
                )
            connection.execute(
                """
                UPDATE tracked_instruments
                SET instrument_id = ?, source_text = ?, updated_at = ?
                WHERE tracking_id = ?
                """,
                (
                    instrument["instrument_id"],
                    command.source_text.strip(),
                    now,
                    command.tracking_id,
                ),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="TRACKING",
                object_id=str(command.tracking_id),
                action="TRACKING_UPDATED",
                actor=command.actor,
                before={
                    "symbol": current["symbol"],
                    "name": current["name"],
                    "source_text": current["source_text"],
                },
                after={
                    "symbol": instrument["symbol"],
                    "name": instrument["name"],
                    "source_text": command.source_text.strip(),
                },
                occurred_at=now,
            )
        return self._tracking_row(command.project_key, command.tracking_id)

    def _set_tracking_status(
        self,
        project_key: str,
        tracking_id: int,
        actor: str,
        status: TrackingStatus,
    ) -> TrackingRowView:
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, project_key, writable=True)
            tracking = connection.execute(
                """
                SELECT t.*, COALESCE(p.quantity, '0') AS quantity
                FROM tracked_instruments t
                LEFT JOIN accounts a ON a.project_id = t.project_id
                                    AND a.account_code = 'primary-cny'
                LEFT JOIN positions p ON p.account_id = a.account_id
                                     AND p.instrument_id = t.instrument_id
                WHERE t.project_id = ? AND t.tracking_id = ?
                """,
                (project["project_id"], tracking_id),
            ).fetchone()
            if tracking is None:
                raise ApplicationError(ErrorCode.TRACKING_NOT_FOUND, "跟踪记录不存在。")
            if _decimal(tracking["quantity"]) > 0:
                raise ApplicationError(
                    ErrorCode.CONCURRENCY_CONFLICT, "该股票仍有持仓，不能移出当前跟踪。"
                )
            connection.execute(
                """
                UPDATE tracked_instruments
                SET tracking_status = ?, expires_at = NULL, updated_at = ?
                WHERE tracking_id = ?
                """,
                (status, now, tracking_id),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="TRACKING",
                object_id=str(tracking_id),
                action=f"TRACKING_{status}",
                actor=actor,
                occurred_at=now,
            )
        return self._tracking_row(project_key, tracking_id)

    def close_tracking(
        self, command: CloseTrackedInstrumentCommand
    ) -> TrackingRowView:
        return self._set_tracking_status(
            command.project_key,
            command.tracking_id,
            command.actor,
            TrackingStatus.CLOSED,
        )

    def archive_tracking(
        self, command: ArchiveTrackedInstrumentCommand
    ) -> TrackingRowView:
        return self._set_tracking_status(
            command.project_key,
            command.tracking_id,
            command.actor,
            TrackingStatus.ARCHIVED,
        )

    @staticmethod
    def _latest_price(
        connection: sqlite3.Connection, instrument_id: int
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM reference_prices
            WHERE instrument_id = ?
            ORDER BY price_time DESC, reference_price_id DESC
            LIMIT 1
            """,
            (instrument_id,),
        ).fetchone()

    def _sellable_quantity(
        self,
        connection: sqlite3.Connection,
        account_id: int,
        instrument_id: int,
    ) -> Decimal:
        rows = connection.execute(
            """
            SELECT remaining_quantity
            FROM position_lots
            WHERE account_id = ? AND instrument_id = ?
              AND remaining_quantity <> '0'
            """,
            (account_id, instrument_id),
        ).fetchall()
        return sum((_decimal(row["remaining_quantity"]) for row in rows), Decimal("0"))

    @staticmethod
    def _position_quantity(
        connection: sqlite3.Connection, account_id: int, instrument_id: int
    ) -> Decimal:
        row = connection.execute(
            """
            SELECT quantity FROM positions
            WHERE account_id = ? AND instrument_id = ?
            """,
            (account_id, instrument_id),
        ).fetchone()
        return _decimal(row["quantity"] if row else None)

    def _position_view(
        self,
        connection: sqlite3.Connection,
        project_key: str,
        account: sqlite3.Row,
        row: sqlite3.Row,
    ) -> PositionView:
        quantity = _decimal(row["quantity"])
        average_cost = _decimal(row["average_cost"])
        latest = self._latest_price(connection, int(row["instrument_id"]))
        price = _decimal(latest["price"]) if latest else None
        market_value = round_money(quantity * price) if price is not None else None
        unrealized = (
            round_money(market_value - quantity * average_cost)
            if market_value is not None
            else None
        )
        sellable = self._sellable_quantity(
            connection,
            int(account["account_id"]),
            int(row["instrument_id"]),
        )
        return PositionView(
            project_key=project_key,
            account_code=str(account["account_code"]),
            symbol=str(row["symbol"]),
            name=str(row["name"]),
            quantity=quantity,
            sellable_quantity=sellable,
            average_cost=average_cost,
            last_price=price,
            market_value=market_value,
            realized_pnl=_decimal(row["realized_pnl"]),
            unrealized_pnl=unrealized,
        )

    def _positions(
        self,
        connection: sqlite3.Connection,
        project_key: str,
        account: sqlite3.Row,
        *,
        include_zero: bool = False,
    ) -> tuple[PositionView, ...]:
        where = "" if include_zero else "AND CAST(p.quantity AS NUMERIC) > 0"
        rows = connection.execute(
            f"""
            SELECT p.*, i.symbol, i.name
            FROM positions p
            JOIN instruments i ON i.instrument_id = p.instrument_id
            WHERE p.account_id = ? {where}
            ORDER BY i.symbol
            """,
            (account["account_id"],),
        ).fetchall()
        return tuple(
            self._position_view(connection, project_key, account, row) for row in rows
        )

    def _account_summary(
        self,
        connection: sqlite3.Connection,
        project_key: str,
        account: sqlite3.Row,
    ) -> AccountSummaryView:
        positions = self._positions(connection, project_key, account, include_zero=True)
        cash = _decimal(account["cash_balance"])
        known_values = [item.market_value for item in positions if item.market_value is not None]
        market_value = round_money(sum(known_values, Decimal("0")))
        equity = round_money(cash + market_value)
        realized = round_money(
            sum((item.realized_pnl for item in positions), Decimal("0"))
        )
        unrealized = round_money(
            sum(
                (
                    item.unrealized_pnl
                    for item in positions
                    if item.unrealized_pnl is not None
                ),
                Decimal("0"),
            )
        )
        return AccountSummaryView(
            project_key=project_key,
            account_code=str(account["account_code"]),
            account_status=AccountStatus(account["status"]),
            currency=str(account["base_currency"]),
            initial_capital=_decimal(account["initial_capital"]),
            cash_balance=cash,
            market_value=market_value,
            equity=equity,
            position_ratio=(market_value / equity if equity > 0 else Decimal("0")),
            realized_pnl=realized,
            unrealized_pnl=unrealized,
        )

    def account_summary(self, query: GetAccountSummaryQuery) -> AccountSummaryView:
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            account = self._account(
                connection, int(project["project_id"]), query.account_code
            )
            return self._account_summary(connection, query.project_key, account)

    def list_positions(self, query: ListPositionsQuery) -> Sequence[PositionView]:
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            account = self._account(
                connection, int(project["project_id"]), query.account_code
            )
            return self._positions(connection, query.project_key, account)

    def _tracking_row(self, project_key: str, tracking_id: int) -> TrackingRowView:
        with self.database.read() as connection:
            project = self._project(connection, project_key)
            account = self._account(
                connection, int(project["project_id"]), "primary-cny"
            )
            account_summary = self._account_summary(connection, project_key, account)
            row = connection.execute(
                """
                SELECT t.*, i.symbol, i.name,
                       COALESCE(p.quantity, '0') AS quantity,
                       COALESCE(p.average_cost, '0') AS average_cost,
                       COALESCE(p.realized_pnl, '0') AS realized_pnl
                FROM tracked_instruments t
                JOIN instruments i ON i.instrument_id = t.instrument_id
                LEFT JOIN positions p ON p.account_id = ?
                                     AND p.instrument_id = t.instrument_id
                WHERE t.project_id = ? AND t.tracking_id = ?
                """,
                (account["account_id"], project["project_id"], tracking_id),
            ).fetchone()
            if row is None:
                raise ApplicationError(ErrorCode.TRACKING_NOT_FOUND, "跟踪记录不存在。")
            latest = self._latest_price(connection, int(row["instrument_id"]))
            price = _decimal(latest["price"]) if latest else None
            quantity = _decimal(row["quantity"])
            average_cost = _decimal(row["average_cost"])
            market_value = round_money(quantity * price) if price is not None else Decimal("0")
            pnl_ratio = None
            if quantity > 0 and average_cost > 0 and price is not None:
                pnl_ratio = (price - average_cost) / average_cost
            return TrackingRowView(
                tracking_id=int(row["tracking_id"]),
                project_key=project_key,
                symbol=str(row["symbol"]),
                name=str(row["name"]),
                source_text=str(row["source_text"]),
                tracking_status=TrackingStatus(row["tracking_status"]),
                added_at=datetime.fromisoformat(row["added_at"]),
                expires_at=(
                    datetime.fromisoformat(row["expires_at"])
                    if row["expires_at"]
                    else None
                ),
                reference_price=price,
                quantity=quantity,
                sellable_quantity=self._sellable_quantity(
                    connection,
                    int(account["account_id"]),
                    int(row["instrument_id"]),
                ),
                average_cost=average_cost if quantity > 0 else None,
                position_ratio=(
                    market_value / account_summary.equity
                    if account_summary.equity > 0
                    else Decimal("0")
                ),
                pnl_ratio=pnl_ratio,
            )

    def list_tracking(self, query: ListTrackingQuery) -> TrackingPageView:
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            account = self._account(
                connection, int(project["project_id"]), "primary-cny"
            )
            account_summary = self._account_summary(connection, query.project_key, account)
            statuses = tuple(str(status) for status in query.statuses)
            placeholders = ",".join("?" for _ in statuses)
            keyword = f"%{query.keyword.strip()}%"
            rows = connection.execute(
                f"""
                SELECT t.tracking_id
                FROM tracked_instruments t
                JOIN instruments i ON i.instrument_id = t.instrument_id
                WHERE t.project_id = ? AND t.tracking_status IN ({placeholders})
                  AND (? = '%%' OR i.symbol LIKE ? OR i.name LIKE ?)
                ORDER BY t.tracking_status = 'HOLDING' DESC, t.added_at DESC
                """,
                (project["project_id"], *statuses, keyword, keyword, keyword),
            ).fetchall()
            summary_rows = dict(
                connection.execute(
                    """
                    SELECT tracking_status, COUNT(*)
                    FROM tracked_instruments WHERE project_id = ?
                    GROUP BY tracking_status
                    """,
                    (project["project_id"],),
                ).fetchall()
            )
            today_prefix = self._now().date().isoformat() + "%"
            today_added = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM tracked_instruments
                    WHERE project_id = ? AND added_at LIKE ?
                    """,
                    (project["project_id"], today_prefix),
                ).fetchone()[0]
            )
        items = tuple(
            self._tracking_row(query.project_key, int(row["tracking_id"])) for row in rows
        )
        return TrackingPageView(
            summary=TrackingSummaryView(
                watching_count=int(summary_rows.get(TrackingStatus.WATCHING, 0)),
                holding_count=int(summary_rows.get(TrackingStatus.HOLDING, 0)),
                today_added_count=today_added,
                expired_count=int(summary_rows.get(TrackingStatus.EXPIRED, 0)),
            ),
            account=account_summary,
            rows=items,
        )

    def expire_tracking(self, command: ExpireTrackingCommand) -> int:
        as_of = self._time(command.as_of)
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            rows = connection.execute(
                """
                SELECT tracking_id FROM tracked_instruments
                WHERE project_id = ? AND tracking_status = 'WATCHING'
                  AND expires_at IS NOT NULL AND expires_at < ?
                """,
                (project["project_id"], as_of.isoformat()),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE tracked_instruments
                    SET tracking_status = 'EXPIRED', updated_at = ?
                    WHERE tracking_id = ?
                    """,
                    (now, row["tracking_id"]),
                )
                self._audit(
                    connection,
                    project_id=project["project_id"],
                    object_type="TRACKING",
                    object_id=str(row["tracking_id"]),
                    action="TRACKING_EXPIRED",
                    actor=command.actor,
                    occurred_at=now,
                )
            return len(rows)

    def refresh_tracking_prices(
        self, command: RefreshTrackingPricesCommand
    ) -> PriceRefreshView:
        with self.database.read() as connection:
            project = self._project(connection, command.project_key, writable=True)
            rows = connection.execute(
                """
                SELECT i.symbol FROM tracked_instruments t
                JOIN instruments i ON i.instrument_id = t.instrument_id
                WHERE t.project_id = ?
                  AND t.tracking_status IN ('WATCHING', 'HOLDING')
                """,
                (project["project_id"],),
            ).fetchall()
        symbols = tuple(str(row["symbol"]) for row in rows)
        quotes, failures = self.quote_provider.fetch_many(symbols)
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            for symbol, quote in quotes.items():
                instrument = self._instrument(
                    connection, int(project["project_id"]), symbol
                )
                connection.execute(
                    """
                    INSERT INTO reference_prices (
                        instrument_id, price, price_time, collected_at, source_name,
                        is_manual, raw_source_summary, created_by
                    ) VALUES (?, ?, ?, ?, ?, 0, '', ?)
                    ON CONFLICT(instrument_id, price_time, source_name) DO UPDATE SET
                        price = excluded.price, collected_at = excluded.collected_at
                    """,
                    (
                        instrument["instrument_id"],
                        _decimal_text(quote.price),
                        quote.price_time.isoformat(),
                        now,
                        quote.source_name,
                        command.actor,
                    ),
                )
                if quote.name.strip():
                    connection.execute(
                        "UPDATE instruments SET name = ?, updated_at = ? WHERE instrument_id = ?",
                        (quote.name.strip(), now, instrument["instrument_id"]),
                    )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="REFERENCE_PRICE",
                object_id=command.project_key,
                action="TRACKING_PRICES_REFRESHED",
                actor=command.actor,
                after={"updated": len(quotes), "failed": sorted(failures)},
                occurred_at=now,
            )
        return PriceRefreshView(
            requested_count=len(symbols),
            updated_count=len(quotes),
            failed_symbols=tuple(sorted(failures)),
        )

    def _trade_inputs(
        self,
        connection: sqlite3.Connection,
        project_key: str,
        account_code: str,
        symbol: str,
        *,
        writable: bool,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        normalized, _, _ = self._normalize_symbol(symbol)
        project = self._project(connection, project_key, writable=writable)
        account = self._account(
            connection,
            int(project["project_id"]),
            account_code,
            writable=writable,
        )
        instrument = self._instrument(
            connection, int(project["project_id"]), normalized
        )
        tracking = connection.execute(
            """
            SELECT tracking_status FROM tracked_instruments
            WHERE project_id = ? AND instrument_id = ?
            """,
            (project["project_id"], instrument["instrument_id"]),
        ).fetchone()
        if tracking is None or tracking["tracking_status"] not in {
            TrackingStatus.WATCHING,
            TrackingStatus.HOLDING,
        }:
            raise ApplicationError(
                ErrorCode.TRACKING_NOT_FOUND, "该股票不在当前跟踪中。"
            )
        return project, account, instrument

    def preview_manual_trade(
        self, command: PreviewManualTradeCommand
    ) -> TradePreviewView:
        with self.database.read() as connection:
            project, account, instrument = self._trade_inputs(
                connection,
                command.project_key,
                command.account_code,
                command.symbol,
                writable=True,
            )
            cash = _decimal(account["cash_balance"])
            sellable = self._sellable_quantity(
                connection,
                int(account["account_id"]),
                int(instrument["instrument_id"]),
            )
            try:
                calculation = (
                    calculate_buy(cash, command.allocation_ratio, command.price)
                    if command.side is TradeSide.BUY
                    else calculate_sell(
                        sellable, command.allocation_ratio, command.price
                    )
                )
            except ValueError as exc:
                code = (
                    ErrorCode.INSUFFICIENT_CASH
                    if command.side is TradeSide.BUY
                    else ErrorCode.INSUFFICIENT_SELLABLE_POSITION
                )
                raise ApplicationError(code, str(exc)) from exc
            return TradePreviewView(
                project_key=command.project_key,
                account_code=command.account_code,
                symbol=str(instrument["symbol"]),
                side=command.side,
                ratio_basis=calculation.ratio_basis,
                allocation_ratio=command.allocation_ratio,
                available_cash=cash,
                sellable_quantity=sellable,
                quantity=calculation.quantity,
                price=calculation.price,
                gross_amount=calculation.gross_amount,
                commission_amount=calculation.commission_amount,
                stamp_tax_amount=calculation.stamp_tax_amount,
                transfer_fee_amount=calculation.transfer_fee_amount,
                net_cash_amount=calculation.net_cash_amount,
            )

    def _consume_lots(
        self,
        connection: sqlite3.Connection,
        account_id: int,
        instrument_id: int,
        quantity: Decimal,
    ) -> Decimal:
        remaining = quantity
        cost = Decimal("0")
        lots = connection.execute(
            """
            SELECT * FROM position_lots
            WHERE account_id = ? AND instrument_id = ?
              AND CAST(remaining_quantity AS NUMERIC) > 0
            ORDER BY created_at, lot_id
            """,
            (account_id, instrument_id),
        ).fetchall()
        for lot in lots:
            available = _decimal(lot["remaining_quantity"])
            consumed = min(remaining, available)
            cost += consumed * _decimal(lot["unit_cost"])
            connection.execute(
                "UPDATE position_lots SET remaining_quantity = ? WHERE lot_id = ?",
                (_decimal_text(available - consumed), lot["lot_id"]),
            )
            remaining -= consumed
            if remaining == 0:
                return round_money(cost)
        raise ApplicationError(
            ErrorCode.INSUFFICIENT_SELLABLE_POSITION, "可卖持仓不足。"
        )

    def _refresh_position(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        account_id: int,
        instrument_id: int,
        *,
        realized_delta: Decimal,
        mark_price: Decimal,
        occurred_at: datetime,
    ) -> None:
        previous = connection.execute(
            """
            SELECT realized_pnl FROM positions
            WHERE account_id = ? AND instrument_id = ?
            """,
            (account_id, instrument_id),
        ).fetchone()
        realized = _decimal(previous["realized_pnl"] if previous else None) + realized_delta
        lots = connection.execute(
            """
            SELECT remaining_quantity, unit_cost, available_date
            FROM position_lots
            WHERE account_id = ? AND instrument_id = ?
            """,
            (account_id, instrument_id),
        ).fetchall()
        quantity = sum(
            (_decimal(lot["remaining_quantity"]) for lot in lots), Decimal("0")
        )
        cost_total = sum(
            (
                _decimal(lot["remaining_quantity"]) * _decimal(lot["unit_cost"])
                for lot in lots
            ),
            Decimal("0"),
        )
        available = quantity
        average = cost_total / quantity if quantity > 0 else Decimal("0")
        market_value = round_money(quantity * mark_price)
        unrealized = round_money(market_value - cost_total)
        connection.execute(
            """
            INSERT INTO positions (
                project_id, account_id, instrument_id, quantity,
                available_quantity, average_cost, realized_pnl, last_price,
                market_value, unrealized_pnl, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, instrument_id) DO UPDATE SET
                quantity = excluded.quantity,
                available_quantity = excluded.available_quantity,
                average_cost = excluded.average_cost,
                realized_pnl = excluded.realized_pnl,
                last_price = excluded.last_price,
                market_value = excluded.market_value,
                unrealized_pnl = excluded.unrealized_pnl,
                updated_at = excluded.updated_at
            """,
            (
                project_id,
                account_id,
                instrument_id,
                _decimal_text(quantity),
                _decimal_text(available),
                _decimal_text(average),
                _decimal_text(round_money(realized)),
                _decimal_text(mark_price),
                _decimal_text(market_value),
                _decimal_text(unrealized),
                occurred_at.isoformat(),
            ),
        )

    def _insert_reference_price(
        self,
        connection: sqlite3.Connection,
        instrument_id: int,
        price: Decimal,
        occurred_at: datetime,
        actor: str,
        source_name: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO reference_prices (
                instrument_id, price, price_time, collected_at, source_name,
                is_manual, raw_source_summary, created_by
            ) VALUES (?, ?, ?, ?, ?, 1, '', ?)
            ON CONFLICT(instrument_id, price_time, source_name) DO UPDATE SET
                price = excluded.price, collected_at = excluded.collected_at
            """,
            (
                instrument_id,
                _decimal_text(price),
                occurred_at.isoformat(),
                self._now().isoformat(),
                source_name,
                actor,
            ),
        )

    @staticmethod
    def _trade_view(row: sqlite3.Row) -> TradeView:
        return TradeView(
            trade_id=str(row["trade_id"]),
            project_key=str(row["project_key"]),
            account_code=str(row["account_code"]),
            market=str(row["market"]),
            venue=str(row["venue"]),
            symbol=str(row["symbol"]),
            side=TradeSide(row["side"]),
            trade_time=datetime.fromisoformat(row["trade_time"]),
            quantity=_decimal(row["quantity"]),
            price=_decimal(row["price"]),
            gross_amount=_decimal(row["gross_amount"]),
            commission_amount=_decimal(row["commission_amount"]),
            stamp_tax_amount=_decimal(row["stamp_tax_amount"]),
            transfer_fee_amount=_decimal(row["transfer_fee_amount"]),
            net_cash_amount=_decimal(row["net_cash_amount"]),
            realized_pnl=_decimal(row["realized_pnl"]),
            signal_text=str(row["signal_text"]),
            execution_source=ExecutionSource(row["execution_source"]),
            record_status=TradeRecordStatus(row["record_status"]),
            external_trade_id=row["external_trade_id"],
            request_id=row["request_id"],
            reversal_of_trade_id=row["reversal_of_trade_id"],
        )

    @staticmethod
    def _trade_select() -> str:
        return """
            SELECT tr.*, p.project_key, a.account_code,
                   i.market, i.venue, i.symbol
            FROM trade_records tr
            JOIN projects p ON p.project_id = tr.project_id
            JOIN accounts a ON a.account_id = tr.account_id
            JOIN instruments i ON i.instrument_id = tr.instrument_id
        """

    def _record_trade(
        self,
        connection: sqlite3.Connection,
        *,
        project: sqlite3.Row,
        account: sqlite3.Row,
        instrument: sqlite3.Row,
        side: TradeSide,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        stamp_tax: Decimal,
        transfer_fee: Decimal,
        signal_text: str,
        actor: str,
        trade_time: datetime,
        execution_source: ExecutionSource,
        allocation_ratio: Decimal | None = None,
        ratio_basis: RatioBasis | None = None,
        request_id: str | None = None,
        request_fingerprint: str | None = None,
        external_trade_id: str | None = None,
        metadata: object | None = None,
    ) -> TradeView:
        cash_before = _decimal(account["cash_balance"])
        gross = round_money(quantity * price)
        if side is TradeSide.BUY:
            net_cash = -(gross + commission + stamp_tax + transfer_fee)
            if cash_before + net_cash < 0:
                raise ApplicationError(
                    ErrorCode.INSUFFICIENT_CASH, "可用资金不足。"
                )
            consumed_cost = Decimal("0")
            realized = Decimal("0")
        else:
            sellable = self._sellable_quantity(
                connection,
                int(account["account_id"]),
                int(instrument["instrument_id"]),
            )
            if quantity > sellable:
                raise ApplicationError(
                    ErrorCode.INSUFFICIENT_SELLABLE_POSITION,
                    f"当前可卖持仓为 {sellable} 股。",
                )
            net_cash = gross - commission - stamp_tax - transfer_fee
            consumed_cost = self._consume_lots(
                connection,
                int(account["account_id"]),
                int(instrument["instrument_id"]),
                quantity,
            )
            realized = round_money(net_cash - consumed_cost)

        trade_id = _identifier("trade")
        created_at = self._now().isoformat()
        connection.execute(
            """
            INSERT INTO trade_records (
                trade_id, project_id, account_id, instrument_id, signal_text,
                side, trade_time, quantity, price, gross_amount,
                commission_amount, stamp_tax_amount, transfer_fee_amount,
                net_cash_amount, realized_pnl, allocation_ratio, ratio_basis,
                execution_source, record_status, external_trade_id, request_id,
                request_fingerprint, reversal_of_trade_id, metadata_json,
                created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      'CONFIRMED', ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                trade_id,
                project["project_id"],
                account["account_id"],
                instrument["instrument_id"],
                signal_text.strip(),
                side,
                trade_time.isoformat(),
                _decimal_text(quantity),
                _decimal_text(price),
                _decimal_text(gross),
                _decimal_text(commission),
                _decimal_text(stamp_tax),
                _decimal_text(transfer_fee),
                _decimal_text(net_cash),
                _decimal_text(realized),
                _decimal_text(allocation_ratio) if allocation_ratio is not None else None,
                str(ratio_basis) if ratio_basis is not None else None,
                str(execution_source),
                external_trade_id,
                request_id,
                request_fingerprint,
                _json(metadata or {}),
                actor.strip(),
                created_at,
            ),
        )
        if side is TradeSide.BUY:
            unit_cost = (gross + commission + stamp_tax + transfer_fee) / quantity
            available_date = trade_time.date().isoformat()
            connection.execute(
                """
                INSERT INTO position_lots (
                    lot_id, project_id, account_id, instrument_id,
                    source_trade_id, original_quantity, remaining_quantity,
                    unit_cost, available_date, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _identifier("lot"),
                    project["project_id"],
                    account["account_id"],
                    instrument["instrument_id"],
                    trade_id,
                    _decimal_text(quantity),
                    _decimal_text(quantity),
                    _decimal_text(unit_cost),
                    available_date,
                    created_at,
                ),
            )

        cash_after = round_money(cash_before + net_cash)
        connection.execute(
            "UPDATE accounts SET cash_balance = ?, updated_at = ? WHERE account_id = ?",
            (_decimal_text(cash_after), created_at, account["account_id"]),
        )
        connection.execute(
            """
            INSERT INTO cash_ledger (
                cash_entry_id, project_id, account_id, trade_id, entry_type,
                currency, amount, balance_after, occurred_at, note,
                created_by, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _identifier("cash"),
                project["project_id"],
                account["account_id"],
                trade_id,
                str(side),
                account["base_currency"],
                _decimal_text(net_cash),
                _decimal_text(cash_after),
                trade_time.isoformat(),
                f"{instrument['symbol']} {'买入' if side is TradeSide.BUY else '卖出'}",
                actor.strip(),
                created_at,
            ),
        )
        self._insert_reference_price(
            connection,
            int(instrument["instrument_id"]),
            price,
            trade_time,
            actor,
            "人工成交" if execution_source is ExecutionSource.MANUAL else "外部成交",
        )
        self._refresh_position(
            connection,
            int(project["project_id"]),
            int(account["account_id"]),
            int(instrument["instrument_id"]),
            realized_delta=realized,
            mark_price=price,
            occurred_at=trade_time,
        )
        current_quantity = _decimal(
            connection.execute(
                """
                SELECT quantity FROM positions
                WHERE account_id = ? AND instrument_id = ?
                """,
                (account["account_id"], instrument["instrument_id"]),
            ).fetchone()[0]
        )
        connection.execute(
            """
            UPDATE tracked_instruments
            SET tracking_status = ?, expires_at = NULL, updated_at = ?
            WHERE project_id = ? AND instrument_id = ?
            """,
            (
                TrackingStatus.HOLDING if current_quantity > 0 else TrackingStatus.CLOSED,
                trade_time.isoformat(),
                project["project_id"],
                instrument["instrument_id"],
            ),
        )
        self._audit(
            connection,
            project_id=project["project_id"],
            object_type="TRADE",
            object_id=trade_id,
            action="TRADE_CONFIRMED",
            actor=actor,
            request_id=request_id,
            after={
                "symbol": instrument["symbol"],
                "side": side,
                "quantity": _decimal_text(quantity),
                "signal_text": signal_text.strip(),
            },
            occurred_at=created_at,
        )
        row = connection.execute(
            self._trade_select() + " WHERE tr.trade_id = ?", (trade_id,)
        ).fetchone()
        return self._trade_view(row)

    def confirm_manual_trade(self, command: ConfirmManualTradeCommand) -> TradeView:
        trade_time = self._time(command.trade_time)
        with self.database.transaction() as connection:
            project, account, instrument = self._trade_inputs(
                connection,
                command.project_key,
                command.account_code,
                command.symbol,
                writable=True,
            )
            cash = _decimal(account["cash_balance"])
            sellable = self._sellable_quantity(
                connection,
                int(account["account_id"]),
                int(instrument["instrument_id"]),
            )
            try:
                calculation = (
                    calculate_buy(cash, command.allocation_ratio, command.price)
                    if command.side is TradeSide.BUY
                    else calculate_sell(
                        sellable, command.allocation_ratio, command.price
                    )
                )
            except ValueError as exc:
                code = (
                    ErrorCode.INSUFFICIENT_CASH
                    if command.side is TradeSide.BUY
                    else ErrorCode.INSUFFICIENT_SELLABLE_POSITION
                )
                raise ApplicationError(code, str(exc)) from exc
            return self._record_trade(
                connection,
                project=project,
                account=account,
                instrument=instrument,
                side=command.side,
                quantity=calculation.quantity,
                price=calculation.price,
                commission=calculation.commission_amount,
                stamp_tax=calculation.stamp_tax_amount,
                transfer_fee=calculation.transfer_fee_amount,
                signal_text=command.signal_text,
                actor=command.actor,
                trade_time=trade_time,
                execution_source=ExecutionSource.MANUAL,
                allocation_ratio=command.allocation_ratio,
                ratio_basis=calculation.ratio_basis,
            )

    @staticmethod
    def _external_fingerprint(command: RecordConfirmedTradeCommand) -> str:
        payload = {
            "project_key": command.project_key,
            "external_trade_id": command.external_trade_id,
            "account_code": command.account_code,
            "market": command.market,
            "venue": command.venue,
            "symbol": command.symbol,
            "side": command.side,
            "trade_time": command.trade_time.isoformat(),
            "quantity": _decimal_text(command.quantity),
            "price": _decimal_text(command.price),
            "signal_text": command.signal_text.strip(),
            "commission_amount": (
                _decimal_text(command.commission_amount)
                if command.commission_amount is not None
                else None
            ),
            "stamp_tax_amount": (
                _decimal_text(command.stamp_tax_amount)
                if command.stamp_tax_amount is not None
                else None
            ),
            "transfer_fee_amount": (
                _decimal_text(command.transfer_fee_amount)
                if command.transfer_fee_amount is not None
                else None
            ),
            "metadata": command.metadata,
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def record_confirmed_trade(
        self, command: RecordConfirmedTradeCommand
    ) -> TradeView:
        fingerprint = self._external_fingerprint(command)
        with self.database.transaction() as connection:
            existing = connection.execute(
                self._trade_select()
                + " WHERE tr.project_id = (SELECT project_id FROM projects WHERE project_key = ?)"
                + " AND tr.request_id = ?",
                (command.project_key, command.request_id),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] == fingerprint:
                    return self._trade_view(existing)
                raise ApplicationError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "相同 request_id 已用于不同内容。",
                )
            external = connection.execute(
                """
                SELECT trade_id FROM trade_records
                WHERE execution_source = ? AND external_trade_id = ?
                """,
                (command.execution_source, command.external_trade_id),
            ).fetchone()
            if external:
                raise ApplicationError(
                    ErrorCode.EXTERNAL_TRADE_CONFLICT, "外部成交已记账。"
                )
            normalized, market, venue = self._normalize_symbol(command.symbol)
            venue_aliases = {
                "SH": {"SH", "SSE"},
                "SZ": {"SZ", "SZSE"},
                "BJ": {"BJ", "BSE"},
            }[venue]
            if command.market != market or command.venue not in venue_aliases:
                raise ApplicationError(
                    ErrorCode.INVALID_INPUT, "市场、交易所与股票代码不一致。"
                )
            project, account, instrument = self._trade_inputs(
                connection,
                command.project_key,
                command.account_code,
                normalized,
                writable=True,
            )
            gross = round_money(command.quantity * command.price)
            if command.commission_amount is None:
                commission, stamp_tax, transfer_fee = calculate_fees(
                    gross, command.side
                )
            else:
                commission = command.commission_amount
                stamp_tax = command.stamp_tax_amount or Decimal("0")
                transfer_fee = command.transfer_fee_amount or Decimal("0")
            return self._record_trade(
                connection,
                project=project,
                account=account,
                instrument=instrument,
                side=command.side,
                quantity=command.quantity,
                price=command.price,
                commission=commission,
                stamp_tax=stamp_tax,
                transfer_fee=transfer_fee,
                signal_text=command.signal_text,
                actor=command.actor,
                trade_time=self._time(command.trade_time),
                execution_source=command.execution_source,
                request_id=command.request_id,
                request_fingerprint=fingerprint,
                external_trade_id=command.external_trade_id,
                metadata=command.metadata,
            )

    def get_trade(self, query: GetTradeQuery) -> TradeView:
        with self.database.read() as connection:
            row = connection.execute(
                self._trade_select()
                + " WHERE p.project_key = ? AND tr.trade_id = ?",
                (query.project_key, query.trade_id),
            ).fetchone()
            if row is None:
                raise ApplicationError(ErrorCode.TRADE_NOT_FOUND, "交易记录不存在。")
            return self._trade_view(row)

    def record_cash_entry(self, command: RecordCashEntryCommand) -> CashEntryView:
        occurred_at = self._time(command.occurred_at)
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT ce.*, p.project_key, a.account_code
                FROM cash_ledger ce
                JOIN projects p ON p.project_id = ce.project_id
                JOIN accounts a ON a.account_id = ce.account_id
                WHERE p.project_key = ? AND ce.request_id = ?
                """,
                (command.project_key, command.request_id),
            ).fetchone()
            signed_amount = (
                command.amount
                if command.entry_type is CashEntryType.DEPOSIT
                else -command.amount
            )
            if existing:
                same = (
                    existing["account_code"] == command.account_code
                    and existing["entry_type"] == command.entry_type
                    and _decimal(existing["amount"]) == signed_amount
                    and existing["currency"] == command.currency
                    and existing["note"] == command.note.strip()
                )
                if not same:
                    raise ApplicationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "相同 request_id 已用于不同资金流水。",
                    )
                return CashEntryView(
                    cash_entry_id=str(existing["cash_entry_id"]),
                    project_key=command.project_key,
                    account_code=str(existing["account_code"]),
                    entry_type=CashEntryType(existing["entry_type"]),
                    amount=_decimal(existing["amount"]),
                    balance_after=_decimal(existing["balance_after"]),
                    occurred_at=datetime.fromisoformat(existing["occurred_at"]),
                )
            project = self._project(connection, command.project_key, writable=True)
            account = self._account(
                connection,
                int(project["project_id"]),
                command.account_code,
                writable=True,
            )
            if command.currency != account["base_currency"]:
                raise ApplicationError(
                    ErrorCode.INVALID_INPUT, "资金流水币种与账户不一致。"
                )
            balance_after = round_money(
                _decimal(account["cash_balance"]) + signed_amount
            )
            if balance_after < 0:
                raise ApplicationError(ErrorCode.INSUFFICIENT_CASH, "可用资金不足。")
            now = self._now().isoformat()
            cash_entry_id = _identifier("cash")
            connection.execute(
                """
                INSERT INTO cash_ledger (
                    cash_entry_id, project_id, account_id, entry_type, currency,
                    amount, balance_after, occurred_at, note, request_id,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cash_entry_id,
                    project["project_id"],
                    account["account_id"],
                    str(command.entry_type),
                    command.currency,
                    _decimal_text(signed_amount),
                    _decimal_text(balance_after),
                    occurred_at.isoformat(),
                    command.note.strip(),
                    command.request_id,
                    command.actor.strip(),
                    now,
                ),
            )
            connection.execute(
                "UPDATE accounts SET cash_balance = ?, updated_at = ? WHERE account_id = ?",
                (_decimal_text(balance_after), now, account["account_id"]),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="CASH_ENTRY",
                object_id=cash_entry_id,
                action=f"CASH_{command.entry_type}",
                actor=command.actor,
                request_id=command.request_id,
                after={"amount": _decimal_text(signed_amount)},
                occurred_at=now,
            )
            return CashEntryView(
                cash_entry_id=cash_entry_id,
                project_key=command.project_key,
                account_code=command.account_code,
                entry_type=command.entry_type,
                amount=signed_amount,
                balance_after=balance_after,
                occurred_at=occurred_at,
            )

    def record_reference_price(
        self, command: RecordReferencePriceCommand
    ) -> ReferencePriceView:
        normalized, market, venue = self._normalize_symbol(command.symbol)
        if command.market != market or command.venue not in {
            venue,
            {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[venue],
        }:
            raise ApplicationError(
                ErrorCode.INVALID_INPUT, "市场、交易所与股票代码不一致。"
            )
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            existing = connection.execute(
                """
                SELECT rp.*, i.market, i.venue, i.symbol
                FROM reference_prices rp
                JOIN instruments i ON i.instrument_id = rp.instrument_id
                WHERE rp.request_id = ?
                """,
                (command.request_id,),
            ).fetchone()
            if existing:
                if (
                    existing["symbol"] != normalized
                    or _decimal(existing["price"]) != command.price
                    or existing["source_name"] != command.source_name.strip()
                ):
                    raise ApplicationError(
                        ErrorCode.IDEMPOTENCY_CONFLICT,
                        "相同 request_id 已用于不同参考价格。",
                    )
                return ReferencePriceView(
                    market=str(existing["market"]),
                    venue=str(existing["venue"]),
                    symbol=str(existing["symbol"]),
                    price=_decimal(existing["price"]),
                    price_time=datetime.fromisoformat(existing["price_time"]),
                    source_name=str(existing["source_name"]),
                )
            instrument = connection.execute(
                """
                SELECT * FROM instruments
                WHERE market = ? AND venue = ? AND symbol = ?
                """,
                (market, venue, normalized),
            ).fetchone()
            if instrument is None:
                raise ApplicationError(
                    ErrorCode.INSTRUMENT_NOT_FOUND,
                    "请先在项目中添加该股票。",
                )
            connection.execute(
                """
                INSERT INTO reference_prices (
                    instrument_id, price, price_time, collected_at, source_name,
                    is_manual, raw_source_summary, request_id, created_by
                ) VALUES (?, ?, ?, ?, ?, 0, '', ?, ?)
                """,
                (
                    instrument["instrument_id"],
                    _decimal_text(command.price),
                    command.price_time.isoformat(),
                    now,
                    command.source_name.strip(),
                    command.request_id,
                    command.actor.strip(),
                ),
            )
            self._audit(
                connection,
                project_id=None,
                object_type="REFERENCE_PRICE",
                object_id=normalized,
                action="REFERENCE_PRICE_RECORDED",
                actor=command.actor,
                request_id=command.request_id,
                after={
                    "price": _decimal_text(command.price),
                    "source_name": command.source_name.strip(),
                },
                occurred_at=now,
            )
            return ReferencePriceView(
                market=market,
                venue=venue,
                symbol=normalized,
                price=command.price,
                price_time=command.price_time,
                source_name=command.source_name.strip(),
            )

    def _replay_effective_trades(
        self,
        connection: sqlite3.Connection,
        project_id: int,
        account_id: int,
    ) -> Decimal:
        connection.execute("DELETE FROM position_lots WHERE account_id = ?", (account_id,))
        connection.execute("DELETE FROM positions WHERE account_id = ?", (account_id,))
        trades = connection.execute(
            """
            SELECT * FROM trade_records
            WHERE account_id = ? AND record_status = 'CONFIRMED'
              AND reversal_of_trade_id IS NULL
            ORDER BY trade_time, created_at, trade_id
            """,
            (account_id,),
        ).fetchall()
        realized_by_instrument: dict[int, Decimal] = {}
        last_price_by_instrument: dict[int, Decimal] = {}
        for trade in trades:
            instrument_id = int(trade["instrument_id"])
            quantity = _decimal(trade["quantity"])
            price = _decimal(trade["price"])
            trade_time = datetime.fromisoformat(trade["trade_time"])
            last_price_by_instrument[instrument_id] = price
            if trade["side"] == TradeSide.BUY:
                total_cost = _decimal(trade["gross_amount"]) + _decimal(
                    trade["commission_amount"]
                )
                unit_cost = total_cost / quantity
                connection.execute(
                    """
                    INSERT INTO position_lots (
                        lot_id, project_id, account_id, instrument_id,
                        source_trade_id, original_quantity, remaining_quantity,
                        unit_cost, available_date, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _identifier("lot"),
                        project_id,
                        account_id,
                        instrument_id,
                        trade["trade_id"],
                        trade["quantity"],
                        trade["quantity"],
                        _decimal_text(unit_cost),
                        trade_time.date().isoformat(),
                        trade["created_at"],
                    ),
                )
                realized = Decimal("0")
            else:
                cost = self._consume_lots(
                    connection,
                    account_id,
                    instrument_id,
                    quantity,
                )
                realized = round_money(_decimal(trade["net_cash_amount"]) - cost)
                realized_by_instrument[instrument_id] = (
                    realized_by_instrument.get(instrument_id, Decimal("0")) + realized
                )
            connection.execute(
                "UPDATE trade_records SET realized_pnl = ? WHERE trade_id = ?",
                (_decimal_text(realized), trade["trade_id"]),
            )
        instrument_ids = set(realized_by_instrument) | set(last_price_by_instrument)
        for instrument_id in instrument_ids:
            self._refresh_position(
                connection,
                project_id,
                account_id,
                instrument_id,
                realized_delta=realized_by_instrument.get(instrument_id, Decimal("0")),
                mark_price=last_price_by_instrument.get(instrument_id, Decimal("0")),
                occurred_at=self._now(),
            )
        cash_events: list[tuple[str, int, Decimal]] = []
        for row in connection.execute(
            """
            SELECT entry_type, occurred_at, amount FROM cash_ledger
            WHERE account_id = ? AND entry_type IN (
                'INITIAL_CAPITAL', 'DEPOSIT', 'WITHDRAWAL'
            )
            """,
            (account_id,),
        ):
            event_time = "" if row["entry_type"] == "INITIAL_CAPITAL" else str(
                row["occurred_at"]
            )
            cash_events.append((event_time, 0, _decimal(row["amount"])))
        for trade in trades:
            cash_events.append(
                (str(trade["trade_time"]), 1, _decimal(trade["net_cash_amount"]))
            )
        balance = Decimal("0")
        for _, _, amount in sorted(cash_events):
            balance = round_money(balance + amount)
            if balance < 0:
                raise ApplicationError(
                    ErrorCode.CONCURRENCY_CONFLICT,
                    "冲正后历史资金会变为负数，已拒绝操作。",
                )
        connection.execute(
            "UPDATE accounts SET cash_balance = ?, updated_at = ? WHERE account_id = ?",
            (_decimal_text(balance), self._now().isoformat(), account_id),
        )
        for row in connection.execute(
            """
            SELECT t.tracking_id, t.tracking_status,
                   COALESCE(p.quantity, '0') AS quantity
            FROM tracked_instruments t
            LEFT JOIN positions p ON p.account_id = ?
                                 AND p.instrument_id = t.instrument_id
            WHERE t.project_id = ?
            """,
            (account_id, project_id),
        ).fetchall():
            quantity = _decimal(row["quantity"])
            if quantity > 0:
                next_status = TrackingStatus.HOLDING
            elif row["tracking_status"] == TrackingStatus.HOLDING:
                next_status = TrackingStatus.CLOSED
            else:
                continue
            connection.execute(
                "UPDATE tracked_instruments SET tracking_status = ?, updated_at = ? WHERE tracking_id = ?",
                (next_status, self._now().isoformat(), row["tracking_id"]),
            )
        return balance

    def reverse_trade(self, command: ReverseTradeCommand) -> TradeView:
        now = self._now()
        fingerprint = hashlib.sha256(
            _json(
                {
                    "project_key": command.project_key,
                    "trade_id": command.trade_id,
                    "reason": command.reason.strip(),
                }
            ).encode("utf-8")
        ).hexdigest()
        with self.database.transaction() as connection:
            existing = connection.execute(
                self._trade_select() + " WHERE p.project_key = ? AND tr.request_id = ?",
                (command.project_key, command.request_id),
            ).fetchone()
            if existing:
                if existing["request_fingerprint"] == fingerprint:
                    return self._trade_view(existing)
                raise ApplicationError(
                    ErrorCode.IDEMPOTENCY_CONFLICT,
                    "相同 request_id 已用于不同冲正。",
                )
            original = connection.execute(
                self._trade_select() + " WHERE p.project_key = ? AND tr.trade_id = ?",
                (command.project_key, command.trade_id),
            ).fetchone()
            if original is None:
                raise ApplicationError(ErrorCode.TRADE_NOT_FOUND, "交易记录不存在。")
            if original["record_status"] != TradeRecordStatus.CONFIRMED:
                raise ApplicationError(
                    ErrorCode.TRADE_ALREADY_REVERSED, "该交易已经冲正。"
                )
            project = self._project(connection, command.project_key, writable=True)
            account = self._account(
                connection,
                int(project["project_id"]),
                str(original["account_code"]),
                writable=True,
            )
            cash_before = _decimal(account["cash_balance"])
            connection.execute(
                "UPDATE trade_records SET record_status = 'REVERSED' WHERE trade_id = ?",
                (command.trade_id,),
            )
            balance_after = self._replay_effective_trades(
                connection, int(project["project_id"]), int(account["account_id"])
            )
            reversal_id = _identifier("trade")
            metadata = {"reason": command.reason.strip()}
            connection.execute(
                """
                INSERT INTO trade_records (
                    trade_id, project_id, account_id, instrument_id, signal_text,
                    side, trade_time, quantity, price, gross_amount,
                    commission_amount, stamp_tax_amount, transfer_fee_amount,
                    net_cash_amount, realized_pnl, allocation_ratio, ratio_basis,
                    execution_source, record_status, external_trade_id, request_id,
                    request_fingerprint, reversal_of_trade_id, metadata_json,
                    created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '0.00',
                          NULL, NULL, ?, 'CONFIRMED', NULL, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reversal_id,
                    project["project_id"],
                    account["account_id"],
                    original["instrument_id"],
                    f"冲正：{command.reason.strip()}",
                    original["side"],
                    now.isoformat(),
                    original["quantity"],
                    original["price"],
                    original["gross_amount"],
                    original["commission_amount"],
                    original["stamp_tax_amount"],
                    original["transfer_fee_amount"],
                    _decimal_text(balance_after - cash_before),
                    original["execution_source"],
                    command.request_id,
                    fingerprint,
                    command.trade_id,
                    _json(metadata),
                    command.actor.strip(),
                    now.isoformat(),
                ),
            )
            original_cash = connection.execute(
                "SELECT cash_entry_id FROM cash_ledger WHERE trade_id = ?",
                (command.trade_id,),
            ).fetchone()
            connection.execute(
                """
                INSERT INTO cash_ledger (
                    cash_entry_id, project_id, account_id, trade_id, entry_type,
                    currency, amount, balance_after, occurred_at,
                    reversal_of_entry_id, note, created_by, created_at
                ) VALUES (?, ?, ?, ?, 'REVERSAL', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _identifier("cash"),
                    project["project_id"],
                    account["account_id"],
                    reversal_id,
                    account["base_currency"],
                    _decimal_text(balance_after - cash_before),
                    _decimal_text(balance_after),
                    now.isoformat(),
                    original_cash["cash_entry_id"] if original_cash else None,
                    command.reason.strip(),
                    command.actor.strip(),
                    now.isoformat(),
                ),
            )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="TRADE",
                object_id=command.trade_id,
                action="TRADE_REVERSED",
                actor=command.actor,
                request_id=command.request_id,
                reason=command.reason.strip(),
                after={"reversal_trade_id": reversal_id},
                occurred_at=now.isoformat(),
            )
            row = connection.execute(
                self._trade_select() + " WHERE tr.trade_id = ?", (reversal_id,)
            ).fetchone()
            return self._trade_view(row)

    def operation_history(
        self, query: ListOperationHistoryQuery
    ) -> OperationHistoryPageView:
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            items: list[OperationHistoryRowView] = []
            for row in connection.execute(
                """
                SELECT tr.*, i.symbol, i.name
                FROM trade_records tr
                JOIN instruments i ON i.instrument_id = tr.instrument_id
                WHERE tr.project_id = ?
                """,
                (project["project_id"],),
            ):
                is_reversal = row["reversal_of_trade_id"] is not None
                items.append(
                    OperationHistoryRowView(
                        operation_kind=(
                            OperationKind.REVERSAL if is_reversal else OperationKind.TRADE
                        ),
                        recorded_at=datetime.fromisoformat(row["trade_time"]),
                        symbol=str(row["symbol"]),
                        name=str(row["name"]),
                        side=TradeSide(row["side"]),
                        source_or_signal=str(row["signal_text"]),
                        position_ratio=(
                            _decimal(row["allocation_ratio"])
                            if row["allocation_ratio"] is not None
                            else None
                        ),
                        price=_decimal(row["price"]),
                        quantity=_decimal(row["quantity"]),
                        gross_amount=_decimal(row["gross_amount"]),
                        cash_change=_decimal(row["net_cash_amount"]),
                    )
                )
            for row in connection.execute(
                """
                SELECT * FROM audit_events
                WHERE project_id = ?
                  AND action IN ('TRACKING_ADDED', 'TRACKING_REOPENED')
                """,
                (project["project_id"],),
            ):
                payload = json.loads(row["after_json"] or "{}")
                items.append(
                    OperationHistoryRowView(
                        operation_kind=OperationKind.TRACKING,
                        recorded_at=datetime.fromisoformat(row["created_at"]),
                        symbol=str(payload.get("symbol") or ""),
                        name=str(payload.get("name") or payload.get("symbol") or ""),
                        side=None,
                        source_or_signal=str(payload.get("source_text") or ""),
                        position_ratio=None,
                        price=None,
                        quantity=None,
                        gross_amount=None,
                        cash_change=None,
                    )
                )
        keyword = query.keyword.strip().casefold()
        if keyword:
            items = [
                item
                for item in items
                if keyword in item.symbol.casefold() or keyword in item.name.casefold()
            ]
        items.sort(key=lambda item: item.recorded_at, reverse=True)
        total_count = len(items)
        symbols = {item.symbol for item in items}
        page_count = max(1, math.ceil(total_count / query.page_size))
        page = min(query.page, page_count)
        start = (page - 1) * query.page_size
        return OperationHistoryPageView(
            rows=tuple(items[start : start + query.page_size]),
            page=page,
            page_size=query.page_size,
            page_count=page_count,
            total_count=total_count,
            matched_symbol_count=len(symbols),
        )

    def list_statistics_months(
        self, query: ListStatisticsMonthsQuery
    ) -> Sequence[str]:
        current = self._now().strftime("%Y-%m")
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            rows = connection.execute(
                """
                SELECT month FROM (
                    SELECT DISTINCT substr(trade_time, 1, 7) AS month
                    FROM trade_records WHERE project_id = ?
                    UNION
                    SELECT DISTINCT substr(valuation_date, 1, 7) AS month
                    FROM daily_account_valuations WHERE project_id = ?
                ) WHERE month IS NOT NULL AND month <> ''
                ORDER BY month DESC
                """,
                (project["project_id"], project["project_id"]),
            ).fetchall()
        return tuple([current] + [str(row["month"]) for row in rows if row["month"] != current])

    def record_daily_valuation(
        self, command: RecordDailyValuationCommand
    ) -> DailyValuationView:
        valuation_time = self._time(command.valuation_time)
        valuation_date = valuation_time.date().isoformat()
        now = self._now().isoformat()
        with self.database.transaction() as connection:
            project = self._project(connection, command.project_key, writable=True)
            account = self._account(
                connection,
                int(project["project_id"]),
                "primary-cny",
                writable=True,
            )
            if command.require_fresh_prices:
                if valuation_time.date() != self._now().date():
                    raise ApplicationError(
                        ErrorCode.INVALID_INPUT, "自动估值只能记录当天，不能用当前持仓补算历史。"
                    )
                stale_symbols = []
                for position in connection.execute(
                    """
                    SELECT p.instrument_id, i.symbol FROM positions p
                    JOIN instruments i ON i.instrument_id = p.instrument_id
                    WHERE p.account_id = ? AND CAST(p.quantity AS NUMERIC) > 0
                    """,
                    (account["account_id"],),
                ):
                    latest = self._latest_price(connection, int(position["instrument_id"]))
                    price_time = (
                        self._time(datetime.fromisoformat(latest["price_time"]))
                        if latest else None
                    )
                    if (
                        price_time is None
                        or bool(latest["is_manual"])
                        or price_time.date() != valuation_time.date()
                        or price_time > valuation_time
                    ):
                        stale_symbols.append(str(position["symbol"]))
                if stale_symbols:
                    raise ApplicationError(
                        ErrorCode.INVALID_INPUT,
                        "缺少当天有效持仓价格，未记录估值：" + "、".join(stale_symbols),
                    )
            summary = self._account_summary(connection, command.project_key, account)
            positions = self._positions(connection, command.project_key, account)
            missing = tuple(
                sorted(item.symbol for item in positions if item.last_price is None)
            )
            external_flow = sum(
                (
                    _decimal(row["amount"])
                    for row in connection.execute(
                        """
                        SELECT amount FROM cash_ledger
                        WHERE account_id = ?
                          AND entry_type IN ('DEPOSIT', 'WITHDRAWAL')
                          AND occurred_at <= ?
                        """,
                        (account["account_id"], valuation_time.isoformat()),
                    )
                ),
                Decimal("0"),
            )
            pnl = round_money(
                summary.equity - summary.initial_capital - external_flow
            )
            return_rate = (
                pnl / summary.initial_capital
                if summary.initial_capital > 0
                else None
            )
            previous_peak = max(
                (
                    _decimal(row["peak_equity"])
                    for row in connection.execute(
                        """
                        SELECT peak_equity FROM daily_account_valuations
                        WHERE account_id = ? AND valuation_date < ?
                        """,
                        (account["account_id"], valuation_date),
                    )
                ),
                default=Decimal("0"),
            )
            peak = max(previous_peak, summary.equity)
            drawdown = (
                (summary.equity - peak) / peak if peak > 0 else Decimal("0")
            )
            existing = connection.execute(
                """
                SELECT valuation_id FROM daily_account_valuations
                WHERE account_id = ? AND valuation_date = ?
                """,
                (account["account_id"], valuation_date),
            ).fetchone()
            valuation_id = (
                str(existing["valuation_id"]) if existing else _identifier("valuation")
            )
            if existing:
                connection.execute(
                    "DELETE FROM daily_position_valuations WHERE valuation_id = ?",
                    (valuation_id,),
                )
                connection.execute(
                    """
                    UPDATE daily_account_valuations SET
                        valuation_time = ?, cash_balance = ?, market_value = ?,
                        equity = ?, external_net_flow = ?, pnl = ?, return_rate = ?,
                        peak_equity = ?, drawdown = ?, is_partial = ?,
                        missing_symbols_json = ?, created_by = ?, created_at = ?
                    WHERE valuation_id = ?
                    """,
                    (
                        valuation_time.isoformat(),
                        _decimal_text(summary.cash_balance),
                        _decimal_text(summary.market_value),
                        _decimal_text(summary.equity),
                        _decimal_text(external_flow),
                        _decimal_text(pnl),
                        _decimal_text(return_rate) if return_rate is not None else None,
                        _decimal_text(peak),
                        _decimal_text(drawdown),
                        bool(missing),
                        _json(missing),
                        command.actor.strip(),
                        now,
                        valuation_id,
                    ),
                )
            else:
                connection.execute(
                    """
                    INSERT INTO daily_account_valuations (
                        valuation_id, project_id, account_id, valuation_date,
                        valuation_time, cash_balance, market_value, equity,
                        external_net_flow, pnl, return_rate, peak_equity,
                        drawdown, is_partial, missing_symbols_json,
                        created_by, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        valuation_id,
                        project["project_id"],
                        account["account_id"],
                        valuation_date,
                        valuation_time.isoformat(),
                        _decimal_text(summary.cash_balance),
                        _decimal_text(summary.market_value),
                        _decimal_text(summary.equity),
                        _decimal_text(external_flow),
                        _decimal_text(pnl),
                        _decimal_text(return_rate) if return_rate is not None else None,
                        _decimal_text(peak),
                        _decimal_text(drawdown),
                        bool(missing),
                        _json(missing),
                        command.actor.strip(),
                        now,
                    ),
                )
            instrument_ids = {
                row["symbol"]: int(row["instrument_id"])
                for row in connection.execute(
                    "SELECT instrument_id, symbol FROM instruments"
                )
            }
            for item in positions:
                connection.execute(
                    """
                    INSERT INTO daily_position_valuations (
                        valuation_id, project_id, account_id, instrument_id,
                        quantity, average_cost, last_price, market_value,
                        unrealized_pnl, is_price_missing
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        valuation_id,
                        project["project_id"],
                        account["account_id"],
                        instrument_ids[item.symbol],
                        _decimal_text(item.quantity),
                        _decimal_text(item.average_cost),
                        (
                            _decimal_text(item.last_price)
                            if item.last_price is not None
                            else None
                        ),
                        (
                            _decimal_text(item.market_value)
                            if item.market_value is not None
                            else None
                        ),
                        (
                            _decimal_text(item.unrealized_pnl)
                            if item.unrealized_pnl is not None
                            else None
                        ),
                        item.last_price is None,
                    ),
                )
            self._audit(
                connection,
                project_id=project["project_id"],
                object_type="VALUATION",
                object_id=valuation_id,
                action="DAILY_VALUATION_RECORDED",
                actor=command.actor,
                after={"valuation_date": valuation_date, "partial": bool(missing)},
                occurred_at=now,
            )
            return DailyValuationView(
                valuation_date=valuation_date,
                project_key=command.project_key,
                account_code=str(account["account_code"]),
                cash_balance=summary.cash_balance,
                market_value=summary.market_value,
                equity=summary.equity,
                external_net_flow=external_flow,
                pnl=pnl,
                return_rate=return_rate,
                peak_equity=peak,
                drawdown=drawdown,
                is_partial=bool(missing),
                missing_symbols=missing,
            )

    def monthly_statistics(
        self, query: GetMonthlyStatisticsQuery
    ) -> MonthlyStatisticsView:
        year, month_number = (int(part) for part in query.month.split("-"))
        start_date = date(year, month_number, 1)
        if month_number == 12:
            end_date = date(year + 1, 1, 1)
        else:
            end_date = date(year, month_number + 1, 1)
        with self.database.read() as connection:
            project = self._project(connection, query.project_key)
            account = self._account(
                connection, int(project["project_id"]), "primary-cny"
            )
            initial_capital = _decimal(account["initial_capital"])
            previous_valuation = connection.execute(
                """
                SELECT * FROM daily_account_valuations
                WHERE account_id = ? AND valuation_date < ?
                ORDER BY valuation_date DESC LIMIT 1
                """,
                (account["account_id"], start_date.isoformat()),
            ).fetchone()
            valuations = connection.execute(
                """
                SELECT * FROM daily_account_valuations
                WHERE account_id = ? AND valuation_date >= ? AND valuation_date < ?
                ORDER BY valuation_date
                """,
                (
                    account["account_id"],
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            ).fetchall()
            opening_capital = (
                _decimal(previous_valuation["equity"])
                if previous_valuation
                else initial_capital
            )
            current_month = self._now().strftime("%Y-%m")
            current_summary: AccountSummaryView | None = None
            if valuations:
                closing_valuation = valuations[-1]
                closing_capital = _decimal(closing_valuation["equity"])
                missing_symbols = tuple(
                    json.loads(closing_valuation["missing_symbols_json"] or "[]")
                )
            elif query.month == current_month:
                closing_valuation = None
                current_summary = self._account_summary(
                    connection, query.project_key, account
                )
                closing_capital = current_summary.equity
                current_positions = self._positions(
                    connection, query.project_key, account
                )
                missing_symbols = tuple(
                    sorted(
                        item.symbol
                        for item in current_positions
                        if item.last_price is None
                    )
                )
            else:
                closing_valuation = None
                closing_capital = opening_capital
                current_positions = ()
                missing_symbols = ()
            external_net_flow = sum(
                (
                    _decimal(row["amount"])
                    for row in connection.execute(
                        """
                        SELECT amount FROM cash_ledger
                        WHERE account_id = ?
                          AND entry_type IN ('DEPOSIT', 'WITHDRAWAL')
                          AND substr(occurred_at, 1, 7) = ?
                        """,
                        (account["account_id"], query.month),
                    )
                ),
                Decimal("0"),
            )
            external_flow_by_date: dict[str, Decimal] = {}
            for row in connection.execute(
                """
                SELECT substr(occurred_at, 1, 10) AS flow_date, amount
                FROM cash_ledger
                WHERE account_id = ?
                  AND entry_type IN ('DEPOSIT', 'WITHDRAWAL')
                  AND substr(occurred_at, 1, 7) = ?
                """,
                (account["account_id"], query.month),
            ):
                flow_date = str(row["flow_date"])
                external_flow_by_date[flow_date] = (
                    external_flow_by_date.get(flow_date, Decimal("0"))
                    + _decimal(row["amount"])
                )
            pnl = round_money(closing_capital - opening_capital - external_net_flow)
            return_rate = pnl / opening_capital if opening_capital > 0 else None
            trade_rows = connection.execute(
                """
                SELECT tr.*, i.symbol, i.name
                FROM trade_records tr
                JOIN instruments i ON i.instrument_id = tr.instrument_id
                WHERE tr.account_id = ? AND substr(tr.trade_time, 1, 7) = ?
                  AND tr.record_status = 'CONFIRMED'
                  AND tr.reversal_of_trade_id IS NULL
                ORDER BY tr.trade_time
                """,
                (account["account_id"], query.month),
            ).fetchall()
            if closing_valuation:
                closing_position_rows = connection.execute(
                    """
                    SELECT dpv.*, i.symbol, i.name
                    FROM daily_position_valuations dpv
                    JOIN instruments i ON i.instrument_id = dpv.instrument_id
                    WHERE dpv.valuation_id = ?
                    """,
                    (closing_valuation["valuation_id"],),
                ).fetchall()
                closing_positions = {
                    str(row["symbol"]): {
                        "name": str(row["name"]),
                        "quantity": _decimal(row["quantity"]),
                        "average_cost": _decimal(row["average_cost"]),
                        "market_value": _decimal(row["market_value"]),
                        "unrealized": _decimal(row["unrealized_pnl"]),
                    }
                    for row in closing_position_rows
                }
            else:
                if query.month == current_month:
                    positions = self._positions(connection, query.project_key, account)
                else:
                    positions = ()
                closing_positions = {
                    item.symbol: {
                        "name": item.name,
                        "quantity": item.quantity,
                        "average_cost": item.average_cost,
                        "market_value": item.market_value or Decimal("0"),
                        "unrealized": item.unrealized_pnl or Decimal("0"),
                    }
                    for item in positions
                }
            if previous_valuation:
                opening_positions = {
                    str(row["symbol"]): _decimal(row["unrealized_pnl"])
                    for row in connection.execute(
                        """
                        SELECT dpv.unrealized_pnl, i.symbol
                        FROM daily_position_valuations dpv
                        JOIN instruments i ON i.instrument_id = dpv.instrument_id
                        WHERE dpv.valuation_id = ?
                        """,
                        (previous_valuation["valuation_id"],),
                    )
                }
            else:
                opening_positions = {}

            grouped: dict[str, dict[str, object]] = {}
            for trade in trade_rows:
                symbol = str(trade["symbol"])
                item = grouped.setdefault(
                    symbol,
                    {
                        "name": str(trade["name"]),
                        "buy_count": 0,
                        "sell_count": 0,
                        "sell_amount": Decimal("0"),
                        "realized": Decimal("0"),
                    },
                )
                if trade["side"] == TradeSide.BUY:
                    item["buy_count"] = int(item["buy_count"]) + 1
                else:
                    item["sell_count"] = int(item["sell_count"]) + 1
                    item["sell_amount"] = _decimal(item["sell_amount"]) + _decimal(
                        trade["gross_amount"]
                    )
                    item["realized"] = _decimal(item["realized"]) + _decimal(
                        trade["realized_pnl"]
                    )
            for symbol, position in closing_positions.items():
                grouped.setdefault(
                    symbol,
                    {
                        "name": position["name"],
                        "buy_count": 0,
                        "sell_count": 0,
                        "sell_amount": Decimal("0"),
                        "realized": Decimal("0"),
                    },
                )
            detail_rows: list[MonthlyInstrumentStatisticsView] = []
            for symbol in sorted(grouped):
                values = grouped[symbol]
                position = closing_positions.get(symbol)
                quantity = (
                    _decimal(position["quantity"]) if position else Decimal("0")
                )
                market_value = (
                    _decimal(position["market_value"]) if position else Decimal("0")
                )
                closing_unrealized = (
                    _decimal(position["unrealized"]) if position else Decimal("0")
                )
                unrealized_change = round_money(
                    closing_unrealized - opening_positions.get(symbol, Decimal("0"))
                )
                realized = round_money(_decimal(values["realized"]))
                instrument_pnl = round_money(realized + unrealized_change)
                cost_basis = (
                    _decimal(position["average_cost"]) * quantity
                    if position
                    else Decimal("0")
                )
                detail_rows.append(
                    MonthlyInstrumentStatisticsView(
                        symbol=symbol,
                        name=str(values["name"]),
                        buy_count=int(values["buy_count"]),
                        sell_count=int(values["sell_count"]),
                        closing_position_ratio=(
                            market_value / closing_capital
                            if closing_capital > 0
                            else Decimal("0")
                        ),
                        sell_amount=round_money(_decimal(values["sell_amount"])),
                        realized_pnl=realized,
                        unrealized_pnl_change=unrealized_change,
                        pnl=instrument_pnl,
                        return_rate=(
                            instrument_pnl / cost_basis if cost_basis > 0 else None
                        ),
                        closing_status="持仓中" if quantity > 0 else "已清仓",
                    )
                )

            daily_returns: list[float] = []
            prior_equity = (
                _decimal(previous_valuation["equity"])
                if previous_valuation
                else opening_capital
            )
            for valuation in valuations:
                equity = _decimal(valuation["equity"])
                interval_flow = external_flow_by_date.get(
                    str(valuation["valuation_date"]), Decimal("0")
                )
                if prior_equity > 0:
                    daily_returns.append(
                        float((equity - prior_equity - interval_flow) / prior_equity)
                    )
                prior_equity = equity
            annualized_volatility = None
            if len(daily_returns) >= 2:
                annualized_volatility = Decimal(
                    str(statistics.pstdev(daily_returns) * math.sqrt(252))
                )
            max_drawdown = (
                min((_decimal(row["drawdown"]) for row in valuations), default=None)
                if valuations
                else None
            )
            is_partial = not valuations or any(
                bool(row["is_partial"]) for row in valuations
            )
            return MonthlyStatisticsView(
                month=query.month,
                instrument_count=len(detail_rows),
                buy_count=sum(1 for row in trade_rows if row["side"] == TradeSide.BUY),
                sell_count=sum(1 for row in trade_rows if row["side"] == TradeSide.SELL),
                opening_capital=round_money(opening_capital),
                closing_capital=round_money(closing_capital),
                external_net_flow=round_money(external_net_flow),
                pnl=pnl,
                return_rate=return_rate,
                max_drawdown=max_drawdown,
                annualized_volatility=annualized_volatility,
                is_partial=is_partial,
                missing_symbols=missing_symbols,
                valuation_points=tuple(
                    DailyValuationPointView(
                        valuation_date=str(row["valuation_date"]),
                        cash_balance=_decimal(row["cash_balance"]),
                        market_value=_decimal(row["market_value"]),
                        equity=_decimal(row["equity"]),
                    )
                    for row in valuations
                ),
                rows=tuple(detail_rows),
            )

    def integrity_check(self) -> tuple[str, ...]:
        issues: list[str] = []
        if self.database.integrity_check() != "ok":
            issues.append("SQLite integrity_check failed")
        with self.database.read() as connection:
            for account in connection.execute("SELECT * FROM accounts"):
                ledger_balance = sum(
                    (
                        _decimal(row["amount"])
                        for row in connection.execute(
                            "SELECT amount FROM cash_ledger WHERE account_id = ?",
                            (account["account_id"],),
                        )
                    ),
                    Decimal("0"),
                )
                if round_money(ledger_balance) != round_money(
                    _decimal(account["cash_balance"])
                ):
                    issues.append(f"account {account['account_id']} cash mismatch")
            for position in connection.execute("SELECT * FROM positions"):
                lot_quantity = sum(
                    (
                        _decimal(row["remaining_quantity"])
                        for row in connection.execute(
                            """
                            SELECT remaining_quantity FROM position_lots
                            WHERE account_id = ? AND instrument_id = ?
                            """,
                            (position["account_id"], position["instrument_id"]),
                        )
                    ),
                    Decimal("0"),
                )
                if lot_quantity != _decimal(position["quantity"]):
                    issues.append(
                        f"position {position['account_id']}/{position['instrument_id']} lot mismatch"
                    )
        return tuple(issues)
