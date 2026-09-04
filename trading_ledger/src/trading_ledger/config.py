"""Configuration contract for the standalone ledger.

Loading settings has no filesystem side effects. Database creation belongs to the
infrastructure layer in stage 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from os import environ as process_environ
from pathlib import Path
from typing import Mapping


MODULE_ROOT = Path(__file__).resolve().parents[2]


def _module_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = MODULE_ROOT / path
    return path.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    """Process settings shared by the future web and API entry points."""

    database_path: Path
    business_timezone: str = "Asia/Shanghai"
    operator: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    api_bearer_token: str | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls, values: Mapping[str, str] | None = None) -> Settings:
        source = process_environ if values is None else values
        operator = source.get("TRADING_LEDGER_OPERATOR", "").strip() or None
        token = source.get("TRADING_LEDGER_API_TOKEN", "").strip() or None
        host = source.get("TRADING_LEDGER_API_HOST", "127.0.0.1").strip()
        timezone = source.get(
            "TRADING_LEDGER_TIMEZONE", "Asia/Shanghai"
        ).strip()
        try:
            port = int(source.get("TRADING_LEDGER_API_PORT", "8787"))
        except ValueError as exc:
            raise ValueError("TRADING_LEDGER_API_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise ValueError("TRADING_LEDGER_API_PORT must be between 1 and 65535.")
        if not host:
            raise ValueError("TRADING_LEDGER_API_HOST cannot be empty.")
        if not timezone:
            raise ValueError("TRADING_LEDGER_TIMEZONE cannot be empty.")
        return cls(
            database_path=_module_path(
                source.get(
                    "TRADING_LEDGER_DB_PATH", "data/trading_ledger.sqlite3"
                )
            ),
            business_timezone=timezone,
            operator=operator,
            api_host=host,
            api_port=port,
            api_bearer_token=token,
        )
