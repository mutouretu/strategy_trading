from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Mapping

from .funding import FundingSettlement
from .margin import LiquidationEvent, MarginSnapshot
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


def _optional_decimal(value: Decimal | None) -> str | None:
    return _decimal(value) if value is not None else None


def _margin_snapshot_document(
    snapshot: MarginSnapshot,
) -> dict[str, object]:
    return {
        "sequence": snapshot.sequence,
        "timestamp": snapshot.timestamp,
        "date": _date(snapshot.timestamp),
        "instrument": snapshot.instrument,
        "settlement_asset": snapshot.settlement_asset,
        "notional_asset": snapshot.notional_asset,
        "mark_price": _decimal(snapshot.mark_price),
        "mark_price_source": snapshot.mark_price_source,
        "leverage": _decimal(snapshot.leverage),
        "position_quantity": _decimal(snapshot.position_quantity),
        "position_unit": snapshot.position_unit,
        "average_entry_price": _decimal(
            snapshot.average_entry_price
        ),
        "position_notional": _decimal(snapshot.position_notional),
        "wallet_balance": _decimal(snapshot.wallet_balance),
        "unrealized_pnl": _decimal(snapshot.unrealized_pnl),
        "margin_balance": _decimal(snapshot.margin_balance),
        "position_initial_margin": _decimal(
            snapshot.position_initial_margin
        ),
        "maintenance_margin": _decimal(
            snapshot.maintenance_margin
        ),
        "available_balance": _decimal(snapshot.available_balance),
        "margin_buffer": _decimal(snapshot.margin_buffer),
        "initial_margin_utilization": _optional_decimal(
            snapshot.initial_margin_utilization
        ),
        "maintenance_margin_utilization": _optional_decimal(
            snapshot.maintenance_margin_utilization
        ),
        "effective_leverage": _optional_decimal(
            snapshot.effective_leverage
        ),
        "estimated_liquidation_price": _optional_decimal(
            snapshot.estimated_liquidation_price
        ),
        "liquidation_triggered": snapshot.liquidation_triggered,
        "bankrupt": snapshot.bankrupt,
    }


def _liquidation_event_document(
    event: LiquidationEvent,
) -> dict[str, object]:
    return {
        "event_type": "LIQUIDATION",
        "sequence": event.sequence,
        "timestamp": event.timestamp,
        "date": _date(event.timestamp),
        "instrument": event.instrument,
        "mark_price_sampling": event.mark_price_sampling.value,
        "maintenance_schedule_version": (
            event.maintenance_schedule_version
        ),
        "intrabar_ordering_ambiguous": (
            event.intrabar_ordering_ambiguous
        ),
        "bankrupt": event.bankrupt,
        "snapshot": _margin_snapshot_document(event.snapshot),
    }


def _funding_settlement_document(
    settlement: FundingSettlement,
) -> dict[str, object]:
    return {
        "event_type": "FUNDING_SETTLEMENT",
        "settlement_id": settlement.settlement_id,
        "sequence": settlement.sequence,
        "timestamp": settlement.timestamp,
        "date": _date(settlement.timestamp),
        "instrument": settlement.instrument,
        "source": settlement.source,
        "funding_rate": _decimal(settlement.funding_rate),
        "position_quantity": _decimal(
            settlement.position_quantity
        ),
        "mark_price": _decimal(settlement.mark_price),
        "mark_price_source": settlement.mark_price_source,
        "position_notional": _decimal(
            settlement.position_notional
        ),
        "notional_asset": settlement.notional_asset,
        "position_value": _decimal(settlement.position_value),
        "settlement_asset": settlement.settlement_asset,
        "wallet_delta": _decimal(settlement.wallet_delta),
    }


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
    manifest_document.setdefault(
        "funding_enabled",
        result.funding_enabled,
    )
    manifest_document.setdefault(
        "funding_source",
        result.funding_source,
    )
    manifest_document.setdefault(
        "funding_market_conditioned",
        result.funding_market_conditioned,
    )
    manifest_document.setdefault(
        "slippage_enabled",
        result.slippage_enabled,
    )
    manifest_document.setdefault(
        "slippage_source",
        result.slippage_source,
    )
    mark_price_sources = {
        snapshot.mark_price_source
        for snapshot in result.margin_snapshots
    }
    if mark_price_sources:
        manifest_document.setdefault(
            "mark_price_source",
            (
                next(iter(mark_price_sources))
                if len(mark_price_sources) == 1
                else "mixed"
            ),
        )

    market_document = [
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
    ]
    frame_by_sequence = {
        frame.sequence: frame for frame in result.frames
    }
    fills_document = [
        {
            "fill_id": fill.fill_id,
            "source_intent_key": fill.source_intent_key,
            "instruction_key": fill.instruction_key,
            "intent_mode": fill.intent_mode.value,
            "instrument": fill.instrument,
            "sequence": fill.sequence,
            "timestamp": fill.timestamp,
            "date": _date(fill.timestamp),
            "side": fill.side.value,
            "reference_price": _decimal(fill.reference_price),
            "price": _decimal(fill.price),
            "slippage_amount": _decimal(fill.slippage_amount),
            "slippage_bps": _decimal(fill.slippage_bps),
            "quantity": _decimal(fill.quantity),
            "liquidity_role": fill.liquidity_role.value,
            "fee_rate": _decimal(fill.fee_rate),
            "fee_amount": _decimal(fill.fee_amount),
            "fee_asset": fill.fee_asset,
            "reduce_only": fill.reduce_only,
            "tags": dict(fill.tags),
        }
        for fill in result.fills
    ]
    summary_document: dict[str, object] = {
        "initial_equity": _decimal(result.initial_equity),
        "final_cash": _decimal(result.final_cash),
        "final_positions": _decimal_map(result.final_positions),
        "final_average_costs": _decimal_map(result.final_average_costs),
        "gross_realized_pnl": _decimal(
            result.gross_realized_pnl
        ),
        "total_fees": _decimal(result.total_fees),
        "net_realized_pnl": _decimal(result.net_realized_pnl),
        "total_funding": _decimal(result.total_funding),
        "net_pnl_after_fees_and_funding": _decimal(
            result.net_pnl_after_fees_and_funding
        ),
        "realized_pnl": _decimal(result.realized_pnl),
        "final_equity": _decimal(result.final_equity),
        "equity_asset": result.equity_asset,
        "final_account_metrics": _decimal_map(
            result.final_account_metrics
        ),
        "completed": result.completed,
        "liquidated": result.liquidated,
        "bankrupt": result.bankrupt,
        "termination_reason": (
            result.termination_reason.value
            if result.termination_reason is not None
            else None
        ),
        "termination_sequence": result.termination_sequence,
    }
    document: dict[str, object] = {
        "schema_version": 2,
        "manifest": manifest_document,
        "run_status": {
            "completed": result.completed,
            "liquidated": result.liquidated,
            "bankrupt": result.bankrupt,
            "termination_reason": (
                result.termination_reason.value
                if result.termination_reason is not None
                else None
            ),
            "termination_sequence": result.termination_sequence,
        },
        "market": market_document,
        "fills": fills_document,
        "equity": [
            {
                "sequence": snapshot.sequence,
                "timestamp": snapshot.timestamp,
                "date": _date(snapshot.timestamp),
                "cash": _decimal(snapshot.cash),
                "positions": _decimal_map(snapshot.positions),
                "average_costs": _decimal_map(snapshot.average_costs),
                "marks": _decimal_map(snapshot.marks),
                "gross_realized_pnl": _decimal(
                    snapshot.gross_realized_pnl
                ),
                "total_fees": _decimal(snapshot.total_fees),
                "net_realized_pnl": _decimal(
                    snapshot.net_realized_pnl
                ),
                "total_funding": _decimal(snapshot.total_funding),
                "net_pnl_after_fees_and_funding": _decimal(
                    snapshot.net_pnl_after_fees_and_funding
                ),
                "realized_pnl": _decimal(snapshot.realized_pnl),
                "equity": _decimal(snapshot.equity),
                "equity_asset": snapshot.equity_asset,
                "account_metrics": _decimal_map(snapshot.account_metrics),
            }
            for snapshot in result.equity_curve
        ],
        "margin": [
            _margin_snapshot_document(snapshot)
            for snapshot in result.margin_snapshots
        ],
        "account_events": [
            _liquidation_event_document(event)
            for event in result.account_events
        ],
        "funding_events": [
            _funding_settlement_document(event)
            for event in result.funding_events
        ],
        "summary": summary_document,
    }
    document["intents"] = [
        {
            "intent_key": record.intent.intent_key,
            "instrument": record.intent.instrument,
            "intent_mode": record.intent.intent_mode.value,
            "side": record.intent.side.value,
            "quantity": _decimal(record.intent.quantity),
            "target_price": (
                _decimal(record.intent.target_price)
                if record.intent.target_price is not None
                else None
            ),
            "reduce_only": record.intent.reduce_only,
            "active_from_sequence": record.active_from_sequence,
            "active_to_sequence": record.active_to_sequence,
            "status": record.status.value,
            "tags": dict(record.intent.tags),
        }
        for record in result.intents
    ]
    document["instructions"] = [
        {
            "instruction_key": instruction.instruction_key,
            "source_intent_key": instruction.source_intent_key,
            "instrument": instruction.instrument,
            "frame_sequence": instruction.frame_sequence,
            "timestamp": frame_by_sequence[
                instruction.frame_sequence
            ].timestamp,
            "date": _date(
                frame_by_sequence[
                    instruction.frame_sequence
                ].timestamp
            ),
            "intent_mode": instruction.intent_mode.value,
            "side": instruction.side.value,
            "price": _decimal(instruction.price),
            "quantity": _decimal(instruction.quantity),
            "reduce_only": instruction.reduce_only,
            "tags": dict(instruction.tags),
        }
        for instruction in result.instructions
    ]
    summary_document.update({
        "intent_count": len(result.intents),
        "instruction_count": len(result.instructions),
        "fill_count": len(result.fills),
        "margin_snapshot_count": len(result.margin_snapshots),
        "account_event_count": len(result.account_events),
        "funding_event_count": len(result.funding_events),
    })
    return document
