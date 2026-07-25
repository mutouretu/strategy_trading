from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from .models import SimulationResult


def _date(timestamp: int) -> str:
    return datetime.fromtimestamp(
        timestamp / 1_000,
        tz=timezone.utc,
    ).date().isoformat()


def _decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _decimal_map(values: Mapping[str, Decimal]) -> dict[str, str]:
    return {key: _decimal(value) for key, value in values.items()}


def simulation_result_to_document(
    result: SimulationResult,
    *,
    run_id: str,
    interval: str,
    source: str,
    seed: int | None = None,
    manifest: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Convert a runtime result into the domain-neutral viewer document."""

    if not run_id.strip():
        raise ValueError("run_id must not be empty")
    if not interval.strip():
        raise ValueError("interval must not be empty")
    if not source.strip():
        raise ValueError("source must not be empty")
    if not result.frames:
        raise ValueError("result must contain at least one market frame")

    instrument = result.frames[0].instrument
    manifest_document: dict[str, object] = {}
    if manifest:
        manifest_document.update(dict(manifest))
    manifest_document.update({
        "run_id": run_id,
        "instrument": instrument,
        "interval": interval,
        "source": source,
        "seed": seed,
    })

    return {
        "schema_version": 1,
        "manifest": manifest_document,
        "market": [
            {
                "sequence": frame.sequence,
                "timestamp": frame.timestamp,
                "date": _date(frame.timestamp),
                "instrument": frame.instrument,
                "open": _decimal(frame.open),
                "high": _decimal(frame.high),
                "low": _decimal(frame.low),
                "close": _decimal(frame.close),
            }
            for frame in result.frames
        ],
        "orders": [
            {
                "order_key": record.order.order_key,
                "instrument": record.order.instrument,
                "side": record.order.side.value,
                "order_type": record.order.order_type.value,
                "quantity": _decimal(record.order.quantity),
                "price": (
                    _decimal(record.order.limit_price)
                    if record.order.limit_price is not None
                    else None
                ),
                "active_from_sequence": record.active_from_sequence,
                "active_to_sequence": record.active_to_sequence,
                "status": record.status.value,
                "tags": dict(record.order.tags),
            }
            for record in result.orders
        ],
        "fills": [
            {
                "fill_id": fill.fill_id,
                "order_key": fill.order_key,
                "instrument": fill.instrument,
                "sequence": fill.sequence,
                "timestamp": fill.timestamp,
                "date": _date(fill.timestamp),
                "side": fill.side.value,
                "price": _decimal(fill.price),
                "quantity": _decimal(fill.quantity),
                "tags": dict(fill.tags),
            }
            for fill in result.fills
        ],
        "equity": [
            {
                "sequence": snapshot.sequence,
                "timestamp": snapshot.timestamp,
                "date": _date(snapshot.timestamp),
                "cash": _decimal(snapshot.cash),
                "positions": _decimal_map(snapshot.positions),
                "average_costs": _decimal_map(snapshot.average_costs),
                "marks": _decimal_map(snapshot.marks),
                "realized_pnl": _decimal(snapshot.realized_pnl),
                "equity": _decimal(snapshot.equity),
                "equity_asset": snapshot.equity_asset,
                "account_metrics": _decimal_map(snapshot.account_metrics),
            }
            for snapshot in result.equity_curve
        ],
        "summary": {
            "initial_equity": _decimal(result.initial_equity),
            "final_cash": _decimal(result.final_cash),
            "final_positions": _decimal_map(result.final_positions),
            "final_average_costs": _decimal_map(result.final_average_costs),
            "realized_pnl": _decimal(result.realized_pnl),
            "final_equity": _decimal(result.final_equity),
            "equity_asset": result.equity_asset,
            "final_account_metrics": _decimal_map(
                result.final_account_metrics
            ),
        },
    }
