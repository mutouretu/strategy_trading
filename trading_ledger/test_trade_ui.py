from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path
import sys
from types import SimpleNamespace

SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

import trade_ui


class TradeUiTest(unittest.TestCase):
    def test_money_and_percentage_formatting(self) -> None:
        self.assertEqual(trade_ui.money(Decimal("1234.5")), "¥1,234.50")
        self.assertEqual(trade_ui.pct(Decimal("0.125")), "12.50%")
        self.assertEqual(trade_ui.money(None), "—")

    def test_monthly_export_contains_summary_and_details(self) -> None:
        report = SimpleNamespace(
            month="2026-09",
            buy_count=1,
            sell_count=0,
            opening_capital=Decimal("100000"),
            external_net_flow=Decimal("0"),
            closing_capital=Decimal("101000"),
            pnl=Decimal("1000"),
            return_rate=Decimal("0.01"),
            max_drawdown=Decimal("-0.02"),
            annualized_volatility=None,
            is_partial=False,
            valuation_points=(),
            rows=(
                SimpleNamespace(
                    symbol="600000.SH",
                    name="浦发银行",
                    buy_count=1,
                    sell_count=0,
                    pnl=Decimal("1000"),
                    return_rate=Decimal("0.01"),
                    closing_status="持仓中",
                ),
            ),
        )
        content = trade_ui._monthly_csv(report).decode("utf-8-sig")
        self.assertIn("汇总", content)
        self.assertIn("600000.SH", content)
        self.assertIn("浦发银行", content)

    def test_valuation_chart_frame_contains_equity_series(self) -> None:
        report = SimpleNamespace(
            valuation_points=(
                SimpleNamespace(
                    valuation_date="2026-08-01",
                    equity=Decimal("100100"),
                    cash_balance=Decimal("60100"),
                    market_value=Decimal("40000"),
                ),
            )
        )
        frame = trade_ui._valuation_chart_frame(report)
        self.assertEqual(list(frame.columns), ["日期", "总权益"])
        self.assertEqual(frame.iloc[0]["总权益"], 100100.0)

    def test_valuation_chart_domain_is_fifteen_percent_around_opening(self) -> None:
        report = SimpleNamespace(opening_capital=Decimal("100000"))
        self.assertEqual(trade_ui._valuation_chart_domain(report), (85000.0, 115000.0))


if __name__ == "__main__":
    unittest.main()
