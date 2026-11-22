#!/usr/bin/env python3
"""Compare SOL spot and 1x USD-M fixed grids on Binance monthly archives.

This is an exploratory historical study. It uses the repository's fixed-grid
strategy and SimulationRunner, with a cash/coin constrained spot ledger and
the archived, time-varying USD-M funding rates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import strategy_simulation  # noqa: F401 - install adjacent package paths
from market_simulator import FixedBarMarketSource
from simulation_runtime import (
    FixedRateFeeModel,
    FlatMaintenanceMarginSchedule,
    FundingSettlement,
    LinearLedger,
    MarginConfig,
    MarkPriceSampling,
    SimulationRunner,
)
from strategy_simulation.adapters.fixed_grid import FixedGridSimulationAdapter
from strategy_simulation.adapters.linear_margin import LinearContractMarginModel
from trading_strategies.grid_following import FixedGridStrategyConfig
from grid_rule import GridMarketType, GridMode, GridRuleConfig


START = "2023-10"
END = "2026-09"
FIRST_TIMESTAMP = 1696118400000  # 2023-10-01 00:00 UTC
HOUR_MS = 3_600_000
CAPITAL = Decimal("10000")
SYMBOL = "SOLUSDT"
ARCHIVE_ROOT = "https://data.binance.vision/data"


def months() -> list[str]:
    return [f"{year}-{month:02d}" for year in range(2023, 2027)
            for month in range(1, 13) if START <= f"{year}-{month:02d}" <= END]


def archive_path(kind: str, month: str) -> str:
    if kind == "spot":
        return f"spot/monthly/klines/{SYMBOL}/1h/{SYMBOL}-1h-{month}.zip"
    if kind == "perp":
        return f"futures/um/monthly/klines/{SYMBOL}/1h/{SYMBOL}-1h-{month}.zip"
    if kind == "mark":
        return f"futures/um/monthly/markPriceKlines/{SYMBOL}/1h/{SYMBOL}-1h-{month}.zip"
    if kind == "funding":
        return f"futures/um/monthly/fundingRate/{SYMBOL}/{SYMBOL}-fundingRate-{month}.zip"
    raise ValueError(kind)


def read_url(url: str) -> bytes:
    return subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "3", "--max-time", "40", url],
        check=True,
        capture_output=True,
    ).stdout


def fetch_archive(cache: Path, kind: str, month: str) -> tuple[str, str, Path]:
    relative = archive_path(kind, month)
    destination = cache / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        data = read_url(f"{ARCHIVE_ROOT}/{relative}")
        destination.write_bytes(data)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksum_file = destination.with_name(destination.name + ".CHECKSUM")
    if not checksum_file.exists():
        checksum_file.write_bytes(read_url(f"{ARCHIVE_ROOT}/{relative}.CHECKSUM"))
    expected = checksum_file.read_text().split()[0].lower()
    if digest != expected:
        raise ValueError(f"archive checksum mismatch: {relative}")
    return relative, digest, destination


def zip_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"expected one CSV in {path}")
        with archive.open(names[0]) as raw:
            return list(csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig")))


def klines(paths: list[Path], *, require_contiguous: bool = True) -> list[tuple[int, Decimal, Decimal, Decimal, Decimal]]:
    result = []
    for path in paths:
        for row in zip_rows(path):
            if not row or not row[0].isdigit():
                continue
            timestamp = int(row[0])
            if timestamp > 10**15:  # Spot archive uses microseconds after 2025-01-01.
                timestamp //= 1000
            result.append((timestamp, *(Decimal(value) for value in row[1:5])))
    result.sort(key=lambda item: item[0])
    for index, row in enumerate(result):
        if require_contiguous and row[0] != FIRST_TIMESTAMP + index * HOUR_MS:
            raise ValueError(f"missing or duplicate hourly K-line at {index}: {row[0]}")
        if index and row[0] <= result[index - 1][0]:
            raise ValueError(f"duplicate or reversed hourly K-line: {row[0]}")
    return result


def funding_rates(paths: list[Path]) -> dict[int, Decimal]:
    rates: dict[int, Decimal] = {}
    for path in paths:
        rows = zip_rows(path)
        for row in rows:
            if not row or not row[0].isdigit():
                continue
            funding_time = int(row[0])
            # Settle on the preceding hourly close, before the next hour opens.
            prior_hour = funding_time // HOUR_MS * HOUR_MS - HOUR_MS
            if prior_hour in rates:
                raise ValueError(f"duplicate funding settlement at {prior_hour}")
            rates[prior_hour] = Decimal(row[2])
    return rates


class SpotLedger(LinearLedger):
    """Use the linear asset ledger with actual cash and SOL ownership limits."""

    def apply(self, fill) -> None:
        if fill.side.value == "BUY" and self.cash < fill.price * fill.quantity + fill.fee_amount:
            raise ValueError("spot fill exceeds available USDT")
        if fill.side.value == "SELL" and self.positions.get(fill.instrument, Decimal(0)) < fill.quantity:
            raise ValueError("spot fill exceeds owned SOL")
        super().apply(fill)


class ArchivedFundingModel:
    enabled = True
    source = "BINANCE_ARCHIVE"
    market_conditioned = True

    def __init__(self, rates: dict[int, Decimal], mark_closes: dict[int, Decimal]):
        self.rates = rates
        self.mark_closes = mark_closes

    def settle(self, frame, ledger, marks):
        del marks
        rate = self.rates.get(frame.timestamp, Decimal(0))
        position = ledger.positions.get(frame.instrument, Decimal(0))
        if rate == 0 or position == 0:
            return None
        mark = self.mark_closes[frame.timestamp]
        notional = abs(position) * mark
        delta = -position * mark * rate
        return FundingSettlement(
            settlement_id=f"binance-archive:{frame.timestamp}",
            sequence=frame.sequence,
            timestamp=frame.timestamp,
            instrument=frame.instrument,
            source=self.source,
            funding_rate=rate,
            position_quantity=position,
            mark_price=mark,
            mark_price_source="archived_mark_price_hourly_close",
            position_notional=notional,
            notional_asset="USDT",
            position_value=notional,
            settlement_asset="USDT",
            wallet_delta=delta,
        )


class LeanFixedGridPort:
    """Keep standard grid behavior without retaining every intent trace."""

    def __init__(self, config: FixedGridStrategyConfig):
        self.adapter = FixedGridSimulationAdapter(config)

    def initialize(self, frame):
        self.adapter.initialize(frame)

    def instructions_for(self, frame):
        return self.adapter.instructions_for(frame)

    def on_fills(self, fills):
        self.adapter.on_fills(fills)

    def on_market(self, frame):
        self.adapter.on_market(frame)


def run_grid(kind: str, bars, rates, mark_closes):
    ratio = Decimal("0.05")
    count = 56
    order_notional = Decimal("150")
    rule = GridRuleConfig(
        grid_id=f"sol-{kind}-fixed-grid",
        instrument=SYMBOL,
        mode=GridMode.LONG,
        anchor_price=Decimal("300"),
        grid_ratio=ratio,
        grid_count=count,
        order_notional=order_notional,
        tick_size=Decimal("0.01"),
        quantity_step=Decimal("0.01"),
        move_grid=False,
        market_type=GridMarketType.USDM,
    )
    port = LeanFixedGridPort(FixedGridStrategyConfig(f"sol-{kind}", rule))
    source = FixedBarMarketSource(
        SYMBOL,
        (row[1:] for row in bars),
        start_timestamp=FIRST_TIMESTAMP,
        step_milliseconds=HOUR_MS,
    )
    fee_rate = Decimal("0.001") if kind == "spot" else Decimal("0.0002")
    kwargs = {
        "ledger_factory": lambda: SpotLedger(CAPITAL, equity_asset="USDT") if kind == "spot" else LinearLedger(CAPITAL, equity_asset="USDT"),
        "fee_model": FixedRateFeeModel(maker_fee_rate=fee_rate, taker_fee_rate=fee_rate, fee_asset="USDT"),
    }
    if kind == "perp":
        kwargs.update({
            "funding_model": ArchivedFundingModel(rates, mark_closes),
            "margin_model": LinearContractMarginModel(
                MarginConfig(Decimal("1"), FlatMaintenanceMarginSchedule(Decimal("0.005"))),
                instrument=SYMBOL,
            ),
            "mark_price_sampling": MarkPriceSampling.ADVERSE_EXTREME,
        })
    result = SimulationRunner(source, port, **kwargs).run()
    curve = [float(item.equity) for item in result.equity_curve]
    peak = curve[0]
    max_drawdown = 0.0
    for equity in curve:
        peak = max(peak, equity)
        max_drawdown = min(max_drawdown, equity / peak - 1)
    year_ends = [
        item for item in result.equity_curve
        if datetime.fromtimestamp(item.timestamp / 1000, tz=timezone.utc).strftime("%m-%d %H") == "09-30 23"
    ]
    yearly = []
    starting_equity = CAPITAL
    period_starts = [FIRST_TIMESTAMP] + [item.timestamp + HOUR_MS for item in year_ends[:-1]]
    for start_time, endpoint in zip(period_starts, year_ends):
        end_time = endpoint.timestamp
        yearly.append({
            "end_date_utc": datetime.fromtimestamp(end_time / 1000, tz=timezone.utc).date().isoformat(),
            "end_equity_usdt": str(endpoint.equity),
            "period_return_pct": float((endpoint.equity / starting_equity - 1) * 100),
            "fees_usdt": str(sum((fill.fee_amount for fill in result.fills if start_time <= fill.timestamp <= end_time), Decimal(0))),
            "funding_usdt": str(sum((event.wallet_delta for event in result.funding_events if start_time <= event.timestamp <= end_time), Decimal(0))),
            "fill_count": sum(start_time <= fill.timestamp <= end_time for fill in result.fills),
        })
        starting_equity = endpoint.equity
    max_exposure = max(
        abs(item.positions.get(SYMBOL, Decimal(0))) * item.marks[SYMBOL]
        for item in result.equity_curve
    )
    return result, {
        "final_equity_usdt": str(result.final_equity),
        "return_pct": float((result.final_equity / CAPITAL - 1) * 100),
        "gross_realized_pnl_usdt": str(result.gross_realized_pnl),
        "total_fees_usdt": str(result.total_fees),
        "total_funding_usdt": str(result.total_funding),
        "final_sol_quantity": str(result.final_positions.get(SYMBOL, Decimal(0))),
        "completed_grid_cycles": port.adapter.strategy.rule.snapshot().completed_cycles,
        "fill_count": len(result.fills),
        "funding_settlement_count": len(result.funding_events),
        "max_drawdown_pct": max_drawdown * 100,
        "liquidated": result.liquidated,
        "max_position_notional_usdt": str(max_exposure),
        "max_position_notional_to_initial_capital_pct": float(max_exposure / CAPITAL * 100),
        "minimum_cash_usdt": str(min(item.cash for item in result.equity_curve)),
        "yearly": yearly,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=Path, default=Path("experiments/market_data/sol_spot_perp_hourly"))
    parser.add_argument("--output", type=Path, default=Path("experiments/experiment_results/sol_spot_perp_3y.json"))
    args = parser.parse_args()
    jobs = [(kind, month) for kind in ("spot", "perp", "mark", "funding") for month in months()]
    jobs.append(("funding", "2026-10"))  # includes 2026-09-30 23:00 UTC settlement
    with ThreadPoolExecutor(max_workers=12) as pool:
        downloaded = list(pool.map(lambda job: fetch_archive(args.cache, *job), jobs))
    grouped = {kind: [] for kind in ("spot", "perp", "mark", "funding")}
    for (kind, _), (_, _, path) in zip(jobs, downloaded):
        grouped[kind].append(path)
    spot_bars = klines(grouped["spot"])
    perp_bars = klines(grouped["perp"])
    mark_bars = klines(grouped["mark"], require_contiguous=False)
    if len(spot_bars) != len(perp_bars):
        raise ValueError("spot and futures history lengths differ")
    rates = {
        timestamp: rate for timestamp, rate in funding_rates(grouped["funding"]).items()
        if FIRST_TIMESTAMP <= timestamp <= perp_bars[-1][0]
    }
    if len(rates) != len(perp_bars) // 8:
        raise ValueError("funding archive does not cover every 8-hour settlement")
    mark_closes = {row[0]: row[4] for row in mark_bars}
    missing_marks = [row[0] for row in perp_bars if row[0] not in mark_closes]
    mark_closes.update({row[0]: row[4] for row in perp_bars if row[0] not in mark_closes})
    if any(timestamp not in mark_closes for timestamp in rates if FIRST_TIMESTAMP <= timestamp <= perp_bars[-1][0]):
        raise ValueError("funding event has no mark or futures close")
    spot_result, spot = run_grid("spot", spot_bars, rates, mark_closes)
    perp_result, perp = run_grid("perp", perp_bars, rates, mark_closes)
    del spot_result, perp_result
    spot_gross_mtm = Decimal(spot["final_equity_usdt"]) - CAPITAL + Decimal(spot["total_fees_usdt"])
    perp_gross_mtm = Decimal(perp["final_equity_usdt"]) - CAPITAL + Decimal(perp["total_fees_usdt"]) - Decimal(perp["total_funding_usdt"])
    gross_mtm_gap = perp_gross_mtm - spot_gross_mtm
    fee_advantage = Decimal(spot["total_fees_usdt"]) - Decimal(perp["total_fees_usdt"])
    funding_effect = Decimal(perp["total_funding_usdt"])
    equity_gap = Decimal(perp["final_equity_usdt"]) - Decimal(spot["final_equity_usdt"])
    if gross_mtm_gap + fee_advantage + funding_effect != equity_gap:
        raise ValueError("return gap does not reconcile")
    document = {
        "study": "SOLUSDT fixed long grid, Binance spot vs USD-M 1x perpetual",
        "period_utc": ["2023-10-01", "2026-09-30"],
        "capital_usdt": str(CAPITAL),
        "parameters": {"upper_price": "300", "grid_ratio": "0.05", "grid_count": 56, "lower_price": "19.42", "order_notional_usdt": "150", "maker_fee_spot": "0.001", "maker_fee_perp": "0.0002", "leverage_perp": "1", "maintenance_margin_rate_assumed": "0.005"},
        "source": ARCHIVE_ROOT,
        "interval": "1h",
        "bar_count": len(spot_bars),
        "funding_rate_count": len(rates),
        "funding_rate_sum": str(sum(rates.values(), Decimal(0))),
        "hypothetical_fixed_10000_usdt_long_funding_usdt": str(-CAPITAL * sum(rates.values(), Decimal(0))),
        "missing_mark_hours_filled_with_perp_close": len(missing_marks),
        "archive_sha256": {relative: digest for relative, digest, _ in downloaded},
        "spot": spot,
        "perp": perp,
        "return_gap_perp_minus_spot_pct_points": perp["return_pct"] - spot["return_pct"],
        "final_equity_gap_perp_minus_spot_usdt": str(equity_gap),
        "gap_decomposition_usdt": {
            "market_price_and_fill_difference": str(gross_mtm_gap),
            "perp_fee_advantage": str(fee_advantage),
            "perp_funding_effect": str(funding_effect),
        },
        "notes": ["OHLC touch is treated as a complete maker fill; queue, spread, slippage and partial fills are not modeled.", "Spot fees are charged in USDT and spot cannot borrow cash or SOL.", "Funding uses archived settlement rate and the preceding hourly mark-price close; missing mark-price archive hours use futures trade close.", "One-hour bars cannot establish intrabar fill order; adverse-extreme liquidation uses a conservative check.", "This is an exploratory study; archive hashes make the source files identifiable, but the current worktree is dirty."],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: document[key] for key in ("period_utc", "bar_count", "funding_rate_count", "spot", "perp", "return_gap_perp_minus_spot_pct_points")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
