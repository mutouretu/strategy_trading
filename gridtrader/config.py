from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


def load_environment(env_file: str | Path | None = None, *, override: bool = False) -> Path:
    """Load the shared project environment without overwriting exported values."""

    configured_path = env_file or os.getenv("GRID_ENV_FILE")
    path = Path(configured_path).expanduser() if configured_path else DEFAULT_ENV_FILE
    load_dotenv(path, override=override)
    return path


def binance_credentials(*, required: bool = False) -> tuple[str, str]:
    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    if required and (not api_key or not api_secret):
        raise RuntimeError("BINANCE_API_KEY/BINANCE_API_SECRET are required")
    return api_key, api_secret


def binance_base_url() -> str:
    return os.getenv("BINANCE_BASE_URL", "https://fapi.binance.com").rstrip("/")


def api_base_url() -> str:
    exported = os.getenv("GRID_API_URL", "").strip()
    if exported:
        return exported.rstrip("/")
    configured_path = os.getenv("GRID_ENV_FILE")
    path = Path(configured_path).expanduser() if configured_path else DEFAULT_ENV_FILE
    value = str(dotenv_values(path).get("GRID_API_URL") or "http://127.0.0.1:8100")
    return value.rstrip("/")
