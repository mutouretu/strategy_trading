"""SQLite lifecycle and transaction boundary for the standalone ledger."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from trading_ledger.config import MODULE_ROOT


DATABASE_IDENTITY = "strategy-trading.trading-ledger"
SCHEMA_VERSION = 3
MIGRATION_PATHS = {
    1: MODULE_ROOT / "migrations" / "001_initial.sql",
    2: MODULE_ROOT / "migrations" / "002_project_color.sql",
    3: MODULE_ROOT / "migrations" / "003_remove_t_plus_one.sql",
}


class DatabaseIdentityError(RuntimeError):
    pass


class LedgerDatabase:
    def __init__(self, path: Path, *, busy_timeout_ms: int = 5000) -> None:
        self.path = path.resolve()
        self.busy_timeout_ms = busy_timeout_ms

    def _open(self, *, must_exist: bool) -> sqlite3.Connection:
        if must_exist:
            target = f"file:{self.path}?mode=rw"
            connection = sqlite3.connect(target, uri=True, timeout=5)
        else:
            connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._open(must_exist=False)
        try:
            table_names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
                if not str(row[0]).startswith("sqlite_")
            }
            if table_names and "ledger_metadata" not in table_names:
                raise DatabaseIdentityError(
                    f"拒绝使用非交易账本数据库：{self.path}"
                )
            if not table_names:
                migration = MIGRATION_PATHS[1].read_text(encoding="utf-8")
                applied_at = datetime.now(timezone.utc).isoformat()
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + migration
                    + "\nINSERT INTO ledger_metadata(key, value) VALUES "
                    f"('database_identity', '{DATABASE_IDENTITY}'), "
                    "('schema_version', '1');\n"
                    + "INSERT INTO schema_migrations(version, applied_at) VALUES "
                    f"(1, '{applied_at}');\nCOMMIT;"
                )
                connection.executescript(script)
            metadata = dict(
                connection.execute("SELECT key, value FROM ledger_metadata").fetchall()
            )
            if metadata.get("database_identity") != DATABASE_IDENTITY:
                raise DatabaseIdentityError(
                    f"数据库身份不匹配：{self.path}"
                )
            version = int(metadata.get("schema_version", "0"))
            if version > SCHEMA_VERSION or version < 1:
                raise DatabaseIdentityError(
                    f"数据库版本 {version} 与应用版本 {SCHEMA_VERSION} 不兼容。"
                )
            for target_version in range(version + 1, SCHEMA_VERSION + 1):
                migration = MIGRATION_PATHS[target_version].read_text(encoding="utf-8")
                applied_at = datetime.now(timezone.utc).isoformat()
                script = (
                    "BEGIN IMMEDIATE;\n"
                    + migration
                    + "\nUPDATE ledger_metadata SET value = "
                    f"'{target_version}' WHERE key = 'schema_version';\n"
                    + "INSERT INTO schema_migrations(version, applied_at) VALUES "
                    f"({target_version}, '{applied_at}');\nCOMMIT;"
                )
                connection.executescript(script)
            connection.execute("PRAGMA journal_mode = WAL")
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._open(must_exist=True)
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._open(must_exist=True)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def integrity_check(self) -> str:
        with self.read() as connection:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
