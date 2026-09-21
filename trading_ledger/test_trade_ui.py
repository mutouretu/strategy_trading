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

    def test_sell_price_prefills_current_price_and_keeps_manual_edits(self) -> None:
        for reference, expected in (("Decimal('25.92')", 25.92), ("None", None), ("Decimal('0')", None)):
            with self.subTest(reference=reference):
                app = AppTest.from_string(
                    'from decimal import Decimal\n'
                    'from trade_ui import render_sell_dialog\n'
                    'render_sell_dialog(None, "test", "tester", "002787.SZ", "华源控股", '
                    f'Decimal("130800"), {reference}, Decimal("0.3"))'
                ).run()
                self.assertEqual(app.exception, [])
                self.assertEqual(app.number_input(key="tracking_sell_price_002787.SZ").value, expected)
                self.assertIsNone(app.number_input(key="tracking_sell_ratio_002787.SZ").value)
                self.assertTrue(app.button(key="tracking_sell_confirm_002787.SZ").disabled)
                app.number_input(key="tracking_sell_price_002787.SZ").set_value(26.1).run()
                app.text_input(key="tracking_sell_signal_002787.SZ").set_value("实际成交价").run()
                self.assertEqual(app.exception, [])
                self.assertEqual(app.number_input(key="tracking_sell_price_002787.SZ").value, 26.1)

    def test_buy_price_prefills_current_price_and_keeps_manual_edits(self) -> None:
        for reference, expected in (("Decimal('25.92')", 25.92), ("None", None), ("Decimal('0')", None)):
            with self.subTest(reference=reference):
                app = AppTest.from_string(
                    'from decimal import Decimal\n'
                    'from trade_ui import render_buy_dialog\n'
                    'render_buy_dialog(None, "test", "tester", "002787.SZ", "华源控股", '
                    f'Decimal("50000"), {reference}, Decimal("100000"))'
                ).run()
                self.assertEqual(app.exception, [])
                self.assertEqual(app.number_input(key="tracking_buy_price_002787.SZ").value, expected)
                self.assertIsNone(app.number_input(key="tracking_buy_ratio_002787.SZ").value)
                self.assertTrue(app.button(key="tracking_buy_confirm_002787.SZ").disabled)
                app.number_input(key="tracking_buy_price_002787.SZ").set_value(26.1).run()
                app.text_input(key="tracking_buy_signal_002787.SZ").set_value("实际成交价").run()
                self.assertEqual(app.exception, [])
                self.assertEqual(app.number_input(key="tracking_buy_price_002787.SZ").value, 26.1)

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
                    cycle_number=2,
                ),
            ),
        )
        content = trade_ui._monthly_csv(report).decode("utf-8-sig")
        self.assertIn("汇总", content)
        self.assertIn("600000.SH", content)
        self.assertIn("浦发银行", content)
        self.assertIn("交易轮次", content)

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
        self.assertEqual(list(frame.columns), ["日期", "系列", "收益率", "总权益", "指数点位"])
        self.assertEqual(frame.iloc[0]["总权益"], 100100.0)
        self.assertEqual(frame.iloc[0]["收益率"], 0)

    def test_comparison_returns_use_common_baseline_and_exclude_cash_flows(self) -> None:
        report = SimpleNamespace(valuation_points=tuple(
            SimpleNamespace(valuation_date=day, equity=Decimal(equity), external_net_flow=Decimal(flow))
            for day, equity, flow in [("2026-09-07", "100", "0"), ("2026-09-08", "210", "100"), ("2026-09-09", "181", "50")]
        ))
        frame = trade_ui._valuation_chart_frame(report, {
            "上证指数": {"2026-09-07": Decimal("4000"), "2026-09-08": Decimal("4040"), "2026-09-09": Decimal("4080")},
            "缺少基准": {"2026-09-08": Decimal("100")},
        })
        self.assertEqual(list(frame[frame["系列"] == "本项目"]["收益率"]), [0, 0.1, 0.21])
        self.assertEqual(list(frame[frame["系列"] == "上证指数"]["收益率"]), [0, 0.01, 0.02])
        self.assertNotIn("缺少基准", set(frame["系列"]))

    def test_comparison_skips_partial_and_zero_baseline(self) -> None:
        point = SimpleNamespace(valuation_date="2026-09-07", equity=Decimal("0"))
        self.assertTrue(trade_ui._valuation_chart_frame(SimpleNamespace(valuation_points=(point,))).empty)
        point.equity = Decimal("100")
        point.is_partial = True
        self.assertTrue(trade_ui._valuation_chart_frame(SimpleNamespace(valuation_points=(point,))).empty)

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
