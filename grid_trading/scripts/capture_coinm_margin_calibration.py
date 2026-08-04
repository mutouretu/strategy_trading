from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


ACCOUNT_ASSET_FIELDS = (
    "asset",
    "walletBalance",
    "unrealizedProfit",
    "marginBalance",
    "maintMargin",
    "initialMargin",
    "positionInitialMargin",
    "openOrderInitialMargin",
    "availableBalance",
    "crossWalletBalance",
    "crossUnPnl",
)
ACCOUNT_POSITION_FIELDS = (
    "symbol",
    "positionSide",
    "positionAmt",
    "entryPrice",
    "unrealizedProfit",
    "initialMargin",
    "positionInitialMargin",
    "openOrderInitialMargin",
    "maintMargin",
    "leverage",
    "isolated",
    "updateTime",
)
POSITION_RISK_FIELDS = (
    "symbol",
    "positionSide",
    "positionAmt",
    "entryPrice",
    "markPrice",
    "unRealizedProfit",
    "liquidationPrice",
    "leverage",
    "maxQty",
    "marginType",
    "isolatedMargin",
    "isAutoAddMargin",
    "updateTime",
)
BRACKET_FIELDS = (
    "bracket",
    "initialLeverage",
    "qtyCap",
    "qtyFloor",
    "maintMarginRatio",
    "cum",
)
SETTLEMENT_FIELD_PRECISION = Decimal("0.00000001")
DERIVED_BALANCE_TOLERANCE = SETTLEMENT_FIELD_PRECISION * 2


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value or "0"))


def _selected(
    entries: Iterable[dict[str, Any]],
    *,
    symbol: str,
    position_side: str | None = None,
    require_nonzero: bool = False,
) -> dict[str, Any]:
    matching = [
        entry
        for entry in entries
        if str(entry.get("symbol", "")).upper() == symbol
        and (
            position_side is None
            or str(entry.get("positionSide", "BOTH")).upper()
            == position_side
        )
    ]
    if require_nonzero:
        matching = [
            entry
            for entry in matching
            if _decimal(entry.get("positionAmt")) != 0
        ]
    if len(matching) != 1:
        side_description = position_side or "single non-zero side"
        raise RuntimeError(
            f"expected exactly one {symbol}/{side_description} record, "
            f"found {len(matching)}"
        )
    return matching[0]


def _whitelist(
    source: dict[str, Any],
    fields: Iterable[str],
) -> dict[str, Any]:
    return {
        field: source[field]
        for field in fields
        if field in source
    }


def _origin(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("BINANCE_COINM_BASE_URL must be an HTTP(S) origin")
    return f"{parsed.scheme}://{parsed.netloc}"


def build_fixture(
    *,
    symbol: str,
    base_url: str,
    server_time_start: int,
    server_time_end: int,
    exchange_info: dict[str, Any],
    account: dict[str, Any],
    position_risk: list[dict[str, Any]],
    leverage_brackets: list[dict[str, Any]] | dict[str, Any],
    captured_at: str,
) -> dict[str, Any]:
    """Validate and sanitize a single-position COIN-M calibration snapshot."""

    symbol = symbol.upper()
    symbol_info = next(
        (
            entry
            for entry in exchange_info.get("symbols", [])
            if str(entry.get("symbol", "")).upper() == symbol
        ),
        None,
    )
    if symbol_info is None:
        raise RuntimeError(f"exchangeInfo does not contain {symbol}")

    margin_asset = str(symbol_info.get("marginAsset", "")).upper()
    if not margin_asset:
        raise RuntimeError(f"{symbol} has no marginAsset")

    account_asset = next(
        (
            entry
            for entry in account.get("assets", [])
            if str(entry.get("asset", "")).upper() == margin_asset
        ),
        None,
    )
    if account_asset is None:
        raise RuntimeError(
            f"account does not contain margin asset {margin_asset}"
        )

    account_position = _selected(
        account.get("positions", []),
        symbol=symbol,
        require_nonzero=True,
    )
    position_side = str(
        account_position.get("positionSide", "BOTH")
    ).upper()
    risk_position = _selected(
        position_risk,
        symbol=symbol,
        position_side=position_side,
        require_nonzero=True,
    )
    position_amount = _decimal(account_position.get("positionAmt"))
    if _decimal(risk_position.get("positionAmt")) != position_amount:
        raise RuntimeError(
            "account and positionRisk position amounts differ; recapture"
        )
    if str(account_position.get("isolated", "false")).lower() == "true":
        raise RuntimeError(
            "isolated positions are outside the current cross-margin model"
        )
    if _decimal(account_asset.get("openOrderInitialMargin")) != 0:
        raise RuntimeError(
            f"{margin_asset} has open-order margin; cancel orders and recapture"
        )

    symbol_margin_assets = {
        str(entry.get("symbol", "")).upper(): str(
            entry.get("marginAsset", "")
        ).upper()
        for entry in exchange_info.get("symbols", [])
    }
    other_positions = [
        (
            f"{entry.get('symbol', '')}/"
            f"{entry.get('positionSide', 'BOTH')}"
        )
        for entry in account.get("positions", [])
        if _decimal(entry.get("positionAmt")) != 0
        and (
            str(entry.get("symbol", "")).upper(),
            str(entry.get("positionSide", "BOTH")).upper(),
        )
        != (symbol, position_side)
        and symbol_margin_assets.get(
            str(entry.get("symbol", "")).upper()
        )
        == margin_asset
    ]
    if other_positions:
        raise RuntimeError(
            f"other {margin_asset}-margin positions exist: "
            + ", ".join(sorted(other_positions))
        )

    margin_balance_identity_delta = abs(
        _decimal(account_asset.get("marginBalance"))
        - (
            _decimal(account_asset.get("walletBalance"))
            + _decimal(account_asset.get("unrealizedProfit"))
        )
    )
    available_balance_identity_delta = abs(
        _decimal(account_asset.get("availableBalance"))
        - (
            _decimal(account_asset.get("marginBalance"))
            - _decimal(
                account_asset.get("positionInitialMargin")
            )
            - _decimal(
                account_asset.get("openOrderInitialMargin")
            )
        )
    )
    if (
        margin_balance_identity_delta
        > DERIVED_BALANCE_TOLERANCE
        or available_balance_identity_delta
        > DERIVED_BALANCE_TOLERANCE
    ):
        raise RuntimeError(
            "account balance fields are not internally coherent within "
            "the 8-decimal API precision; recapture"
        )

    bracket_documents = (
        leverage_brackets
        if isinstance(leverage_brackets, list)
        else [leverage_brackets]
    )
    bracket_entry = next(
        (
            entry
            for entry in bracket_documents
            if str(entry.get("symbol", "")).upper() == symbol
        ),
        None,
    )
    if bracket_entry is None:
        raise RuntimeError(f"leverageBracket does not contain {symbol}")
    raw_brackets = bracket_entry.get("brackets", [])
    if not raw_brackets:
        raise RuntimeError(f"{symbol} has no maintenance brackets")

    request_window_ms = int(server_time_end) - int(server_time_start)
    if request_window_ms < 0:
        raise RuntimeError("server time moved backwards during capture")

    return {
        "fixture_id": (
            f"binance-coinm-authenticated-{symbol.lower()}-"
            f"{position_side.lower()}-"
            f"{captured_at[:10]}"
        ),
        "source_kind": "authenticated_account_capture",
        "captured_at": captured_at,
        "api_product": "Binance COIN-M Futures",
        "api_versions": {
            "account": "dapi-v1",
            "position_risk": "dapi-v1",
            "leverage_bracket": "dapi-v2",
            "exchange_info": "dapi-v1",
        },
        "source_origin": _origin(base_url),
        "request_window": {
            "server_time_start": int(server_time_start),
            "server_time_end": int(server_time_end),
            "duration_ms": request_window_ms,
            "request_order": [
                "exchangeInfo",
                "account",
                "positionRisk",
                "leverageBracket",
            ],
        },
        "capture_validation": {
            "settlement_field_precision": str(
                SETTLEMENT_FIELD_PRECISION
            ),
            "derived_balance_tolerance": str(
                DERIVED_BALANCE_TOLERANCE
            ),
            "margin_balance_identity_delta": str(
                margin_balance_identity_delta
            ),
            "available_balance_identity_delta": str(
                available_balance_identity_delta
            ),
        },
        "limitations": [
            "Responses are sequential, not an atomic exchange snapshot.",
            "The fixture is valid only for the captured symbol, margin mode, "
            "maintenance schedule, and API versions.",
            "Higher maintenance tiers require a separately reviewed unit "
            "conversion before they are loaded into the Runtime.",
        ],
        "instrument": symbol,
        "position_side": position_side,
        "contract_size": str(symbol_info.get("contractSize", "")),
        "margin_asset": margin_asset,
        "notional_asset": str(
            symbol_info.get("quoteAsset", "")
        ).upper(),
        "symbol_metadata": _whitelist(
            symbol_info,
            (
                "symbol",
                "pair",
                "contractType",
                "contractSize",
                "baseAsset",
                "quoteAsset",
                "marginAsset",
                "pricePrecision",
                "quantityPrecision",
            ),
        ),
        "platform_account_asset": _whitelist(
            account_asset,
            ACCOUNT_ASSET_FIELDS,
        ),
        "platform_account_position": _whitelist(
            account_position,
            ACCOUNT_POSITION_FIELDS,
        ),
        "platform_position_risk": _whitelist(
            risk_position,
            POSITION_RISK_FIELDS,
        ),
        "maintenance_schedule": {
            "symbol": symbol,
            "notional_coef": bracket_entry.get("notionalCoef", "1"),
            "raw_brackets": [
                _whitelist(entry, BRACKET_FIELDS)
                for entry in raw_brackets
            ],
        },
        "absolute_tolerances": {
            margin_asset: "0.00000001",
            "USD": "0.01",
            "PRICE": next(
                (
                    str(price_filter.get("tickSize"))
                    for price_filter in symbol_info.get("filters", [])
                    if price_filter.get("filterType") == "PRICE_FILTER"
                ),
                "0.1",
            ),
        },
    }


def capture(symbol: str, base_url: str) -> dict[str, Any]:
    from grid_server.config import binance_credentials
    from grid_server.infrastructure.binance import BinanceCoinMExchange

    api_key, api_secret = binance_credentials(required=True)
    exchange = BinanceCoinMExchange(
        api_key,
        api_secret,
        base_url=base_url,
        confirmation_delays=(),
    )
    started = exchange._request("GET", exchange._path("time"))
    exchange_info = exchange._request(
        "GET",
        exchange._path("exchangeInfo"),
    )
    account = exchange._request(
        "GET",
        exchange._path("account"),
        signed=True,
    )
    position_risk = exchange._request(
        "GET",
        exchange._path("positionRisk"),
        {"symbol": symbol},
        signed=True,
    )
    leverage_brackets = exchange._request(
        "GET",
        exchange._path("leverageBracket", version="v2"),
        {"symbol": symbol},
        signed=True,
    )
    finished = exchange._request("GET", exchange._path("time"))
    return build_fixture(
        symbol=symbol,
        base_url=base_url,
        server_time_start=int(started["serverTime"]),
        server_time_end=int(finished["serverTime"]),
        exchange_info=exchange_info,
        account=account,
        position_risk=position_risk,
        leverage_brackets=leverage_brackets,
        captured_at=datetime.now(timezone.utc).isoformat(),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a read-only, sanitized Binance COIN-M margin "
            "calibration fixture."
        )
    )
    parser.add_argument("--symbol", default="BTCUSD_PERP")
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="New JSON file to create; existing files are never overwritten.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Environment file containing Binance read-only credentials.",
    )
    parser.add_argument(
        "--acknowledge-account-read",
        action="store_true",
        help="Required acknowledgement that private account data will be read.",
    )
    return parser.parse_args()


def main() -> None:
    from grid_server.config import (
        binance_coinm_base_url,
        load_environment,
    )

    args = parse_args()
    if not args.acknowledge_account_read:
        raise SystemExit(
            "--acknowledge-account-read is required; the script performs "
            "authenticated read-only account requests"
        )
    output = args.output.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing file: {output}")
    load_environment(args.env_file)
    fixture = capture(
        args.symbol.upper(),
        binance_coinm_base_url(),
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(fixture, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote sanitized fixture: {output}")
    print(f"fixture_id: {fixture['fixture_id']}")


if __name__ == "__main__":
    main()
