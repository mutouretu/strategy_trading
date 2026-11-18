"""Materialize fixed-grid order replays for the existing Simulation Viewer."""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SIMULATOR_ROOT = PROJECT_ROOT / "market_simulator"
for package in ("market_protocol", "market_simulator"):
    sys.path.insert(0, str(SIMULATOR_ROOT / "packages" / package / "src"))

from market_simulator import FixedBarMarketSource  # noqa: E402

from grid_rule.fixed_equal_usdt import FixedEntryMode  # noqa: E402
from grid_server.domain import SymbolFilters  # noqa: E402
from grid_server.simulation import FixedGridReplay  # noqa: E402
from grid_server.simulation.sample import SAMPLE_BARS  # noqa: E402
from grid_server.simulation.viewer_export import to_viewer_run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=SIMULATOR_ROOT / "viewer" / "data",
    )
    arguments = parser.parse_args()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    filters = SymbolFilters(
        tick_size=Decimal("0.01"), step_size=Decimal("0.01"),
        min_qty=Decimal("0.01"), min_notional=Decimal("5"),
        contract_type="TRADIFI_PERPETUAL",
        percent_price_up=Decimal("1.03"),
        percent_price_down=Decimal("0.97"),
    )
    for mode in (FixedEntryMode.PRICE_TRIGGERED, FixedEntryMode.SEEDED):
        source = FixedBarMarketSource(
            "SOXLUSDT", SAMPLE_BARS,
            start_timestamp=1767225600000,
            step_milliseconds=60_000,
        )
        result = FixedGridReplay(
            source, symbol="SOXLUSDT",
            lower_price=Decimal("40"), upper_price=Decimal("60"),
            grid_count=135, grid_budget_usdt=Decimal("2000"),
            filters=filters, entry_mode=mode,
        ).run()
        document = to_viewer_run(
            result,
            run_id=f"grid-rule-parity-soxl-{mode.value}-sample",
            description=(
                "人工 SOXL 震荡样例；使用实盘固定网格引擎回放订单。"
                "没有费用、资金费、保证金或收益账本。"
            ),
            entry_mode=mode.value,
        )
        target = arguments.output_dir / f"grid-rule-parity-soxl-{mode.value}-sample.json"
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        print(
            f"{target}: {len(result.orders)} orders, {len(result.fills)} fills, "
            f"{result.frames[-1].completed_cycles} cycles"
        )


if __name__ == "__main__":
    main()
