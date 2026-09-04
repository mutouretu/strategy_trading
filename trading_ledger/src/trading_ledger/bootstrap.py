"""Composition root for the local Streamlit process."""

from __future__ import annotations

import getpass
from functools import cache

from .config import Settings
from .infrastructure.database import LedgerDatabase
from .infrastructure.sqlite_application import SQLiteTradingLedgerApplication


@cache
def get_settings() -> Settings:
    return Settings.from_env()


@cache
def get_application() -> SQLiteTradingLedgerApplication:
    settings = get_settings()
    application = SQLiteTradingLedgerApplication(
        LedgerDatabase(settings.database_path),
        timezone_name=settings.business_timezone,
    )
    application.initialize()
    return application


def current_actor() -> str:
    return get_settings().operator or f"local:{getpass.getuser()}"
