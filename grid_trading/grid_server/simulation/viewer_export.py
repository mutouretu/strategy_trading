"""Export an order-only replay to the existing Simulation Viewer schema v1."""

from __future__ import annotations

from datetime import datetime, timezone

from ..domain import OrderStatus
from .replay import ReplayResult


def _date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).date().isoformat()


def to_viewer_run(
    result: ReplayResult, *, run_id: str, description: str,
    entry_mode: str, interval: str = "1m",
    market_data_kind: str = "synthetic_sample",
) -> dict:
    """Preserve observed orders/fills; omit unmodeled account and fee values."""
    if not result.market:
        raise ValueError("replay market must not be empty")
    instrument = result.market[0].instrument
    orders = {order.order_id: order for order in result.orders}
    order_keys = {order_id: f"replay:order:{order_id}" for order_id in orders}
    status_map = {
        OrderStatus.NEW: "ACTIVE",
        OrderStatus.PARTIALLY_FILLED: "ACTIVE",
        OrderStatus.FILLED: "FILLED",
        OrderStatus.CANCELED: "CANCELLED",
        OrderStatus.EXPIRED: "CANCELLED",
        OrderStatus.REJECTED: "CANCELLED",
    }
    return {
        "schema_version": 1,
        "manifest": {
            "run_id": run_id,
            "instrument": instrument,
            "interval": interval,
            "source": "fixed_grid_live_engine_replay",
            "description": description,
            "entry_mode": entry_mode,
            "replay_only": True,
            "accounting_available": False,
            "market_data_kind": market_data_kind,
        },
        "run_status": {
            "completed": True,
            "liquidated": False,
            "bankrupt": False,
            "termination_reason": None,
            "termination_sequence": None,
        },
        "market": [
            {
                "sequence": bar.sequence,
                "timestamp": bar.timestamp,
                "date": _date(bar.timestamp),
                "instrument": bar.instrument,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
            }
            for bar in result.market
        ],
        "orders": [
            {
                "order_key": order_keys[order.order_id],
                "instrument": order.instrument,
                "side": order.side.value,
                "order_type": order.order_type,
                "quantity": str(order.quantity),
                "price": None if order.order_type == "MARKET" else str(order.price),
                "reduce_only": order.side.value == "SELL",
                "active_from_sequence": order.active_from_sequence,
                "active_to_sequence": order.active_to_sequence,
                "status": status_map[order.status],
                "tags": {"replay_order_id": str(order.order_id)},
            }
            for order in result.orders
        ],
        "fills": [
            {
                "fill_id": f"replay:fill:{index}",
                "order_key": order_keys[fill.order_id],
                "instrument": instrument,
                "sequence": fill.sequence,
                "timestamp": fill.timestamp,
                "date": _date(fill.timestamp),
                "side": fill.side.value,
                "price": str(fill.price),
                "quantity": str(fill.quantity),
                "liquidity_role": (
                    "TAKER" if orders[fill.order_id].order_type == "MARKET"
                    or fill.sequence == orders[fill.order_id].active_from_sequence
                    else "MAKER"
                ),
                "reduce_only": fill.side.value == "SELL",
            }
            for index, fill in enumerate(result.fills, start=1)
        ],
        "equity": [],
        "summary": {
            "replay_only": True,
            "accounting_available": False,
            "initial_logical_position": str(result.frames[0].logical_position_qty),
            "final_logical_position": str(result.frames[-1].logical_position_qty),
            "completed_cycles": result.frames[-1].completed_cycles,
        },
    }
