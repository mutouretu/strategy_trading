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
from streamlit.testing.v1 import AppTest


class TradeUiTest(unittest.TestCase):
    def test_tracking_background_state_follows_position_and_sales(self) -> None:
        for quantity, sell_price, expected in (
            ("0", None, "watching"),
            ("100", None, "holding"),
            ("100", Decimal("24"), "holding"),
            ("0", Decimal("24"), "closed"),
        ):
            with self.subTest(quantity=quantity, sell_price=sell_price):
                row = SimpleNamespace(quantity=Decimal(quantity), average_sell_price=sell_price)
                self.assertEqual(trade_ui._tracking_row_state(row), expected)

    def test_buy_dialog_caption_uses_amount_with_parenthesized_ratio(self) -> None:
        app = AppTest.from_string(
            'from decimal import Decimal\n'
            'from trade_ui import render_buy_dialog\n'
            'render_buy_dialog(None, "test", "tester", "300750.SZ", "宁德时代", '
            'Decimal("50000"), Decimal("200"), Decimal("100000"))'
        ).run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.caption[0].value, "300750.SZ · 宁德时代 · 可用资金 ¥50,000.00（50.00%）")

    def test_sell_dialog_caption_uses_market_value_not_share_count(self) -> None:
        for price, ratio, expected in (
            ('Decimal("200")', 'Decimal("0.2")', '¥20,000.00（20.00%）'),
            ('None', 'None', '—（—）'),
        ):
            with self.subTest(price=price):
                app = AppTest.from_string(
                    'from decimal import Decimal\n'
                    'from trade_ui import render_sell_dialog\n'
                    'render_sell_dialog(None, "test", "tester", "300750.SZ", "宁德时代", '
                    f'Decimal("100"), {price}, {ratio})'
                ).run()
                self.assertEqual(app.exception, [])
                self.assertEqual(app.caption[0].value, f"300750.SZ · 宁德时代 · 持仓市值 {expected}")

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

    def test_valuation_chart_date_domain_covers_the_selected_month(self) -> None:
        for month, last_day in (("2026-09", 30), ("2026-12", 31), ("2026-02", 28), ("2028-02", 29)):
            with self.subTest(month=month):
                domain = trade_ui._valuation_chart_date_domain(SimpleNamespace(month=month))
                year, number = map(int, month.split("-"))
                self.assertEqual(domain, [
                    {"year": year, "month": number, "date": 1},
                    {"year": year, "month": number, "date": last_day},
                ])


if __name__ == "__main__":
    unittest.main()
