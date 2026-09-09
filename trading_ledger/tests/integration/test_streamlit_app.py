from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand,
    CreateProjectCommand,
    ConfirmManualTradeCommand,
    RecordDailyValuationCommand,
)
from trading_ledger.bootstrap import get_application, get_settings
from trading_ledger.domain import TradeSide


class StreamlitAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = os.environ.get("TRADING_LEDGER_DB_PATH")
        os.environ["TRADING_LEDGER_DB_PATH"] = str(
            Path(self.temp_dir.name) / "ui.sqlite3"
        )
        get_application.cache_clear()
        get_settings.cache_clear()

    def tearDown(self) -> None:
        get_application.cache_clear()
        get_settings.cache_clear()
        if self.original_path is None:
            os.environ.pop("TRADING_LEDGER_DB_PATH", None)
        else:
            os.environ["TRADING_LEDGER_DB_PATH"] = self.original_path
        self.temp_dir.cleanup()

    def _app(self) -> AppTest:
        path = Path(__file__).resolve().parents[2] / "app.py"
        return AppTest.from_file(str(path), default_timeout=20)

    def test_empty_database_opens_project_management(self) -> None:
        app = self._app().run()
        self.assertEqual(app.exception, [])
        self.assertTrue(any("还没有项目" in item.value for item in app.info))

    def test_confirmed_pages_load_with_persisted_project(self) -> None:
        application = get_application()
        application.create_project(
            CreateProjectCommand(
                project_name="页面测试",
                initial_capital=Decimal("100000.00"),
                actor="test-user",
            )
        )
        app = self._app().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "当前跟踪")
        app.radio[0].set_value("操作历史").run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "操作历史")
        app.radio[0].set_value("交易统计").run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "交易统计")
        button = app.button(key="statistics_record_valuation")
        self.assertEqual(button.label, ":material/add_chart:")
        labels = [metric.label for metric in app.metric]
        self.assertEqual(len(labels), 8)
        self.assertNotIn("外部资金净流入", labels)
        self.assertNotIn("年化波动率", labels)
        button.click().run()
        self.assertEqual(app.exception, [])
        self.assertTrue(any("已记录" in item.value for item in app.success))
        self.assertTrue(app.get("vega_lite_chart"))

    def test_short_valuation_chart_uses_daily_ticks(self) -> None:
        application = get_application()
        project = application.create_project(
            CreateProjectCommand(
                project_name="日期轴测试",
                initial_capital=Decimal("100000.00"),
                actor="test-user",
            )
        )
        for day in (7, 8):
            with self.subTest(valuation_days=day - 6):
                application.record_daily_valuation(
                    RecordDailyValuationCommand(
                        project_key=project.project_key,
                        actor="test-user",
                        valuation_time=datetime(
                            2026, 9, day, 15, 15, tzinfo=ZoneInfo("Asia/Shanghai")
                        ),
                    )
                )
                app = self._app().run()
                app.radio[0].set_value("交易统计").run()
                app.selectbox(key="trade_statistics_month").set_value("2026-09").run()
                self.assertEqual(app.exception, [])
                chart = app.get("vega_lite_chart")[0]
                spec = json.loads(chart.proto.spec)
                axis = spec["encoding"]["x"]["axis"]
                self.assertEqual(axis["format"], "%m-%d")
                self.assertEqual(axis["tickCount"], "day")
                self.assertEqual(axis["labelOverlap"], "greedy")
                self.assertEqual(spec["encoding"]["x"]["scale"]["domain"], [
                    {"year": 2026, "month": 9, "date": 1},
                    {"year": 2026, "month": 9, "date": 30},
                ])

    def test_entering_another_project_loads_its_tracking_page(self) -> None:
        application = get_application()
        first = application.create_project(
            CreateProjectCommand(
                project_name="项目一",
                initial_capital=Decimal("100000.00"),
                actor="test-user",
            )
        )
        second = application.create_project(
            CreateProjectCommand(
                project_name="项目二",
                initial_capital=Decimal("200000.00"),
                actor="test-user",
            )
        )
        app = self._app().run()
        self.assertEqual(app.query_params["project"], [first.project_key])
        app.radio[0].set_value("项目管理").run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "项目管理")
        enter_second = next(
            button
            for button in app.button
            if button.key == f"project_management_enter_{second.project_key}"
        )
        enter_second.click().run()
        self.assertEqual(app.exception, [])
        self.assertEqual(app.radio[0].value, "当前跟踪")
        self.assertEqual(app.query_params["project"], [second.project_key])

    def test_tracking_actions_use_compact_icons_and_add_button_is_last(self) -> None:
        application = get_application()
        project = application.create_project(
            CreateProjectCommand(
                project_name="按钮测试",
                initial_capital=Decimal("100000.00"),
                actor="test-user",
            )
        )
        tracking = application.add_tracking(
            AddTrackedInstrumentCommand(
                project_key=project.project_key,
                symbol="600000.SH",
                name="浦发银行",
                source_text="观察说明",
                actor="test-user",
            )
        )
        app = self._app().run()
        self.assertEqual(app.exception, [])
        button_keys = [button.key for button in app.button]
        self.assertIn("tracking_refresh_prices", button_keys)
        self.assertNotIn(f"tracking_close_{tracking.tracking_id}", button_keys)
        self.assertIn(f"tracking_archive_{tracking.tracking_id}", button_keys)
        self.assertNotIn("清理到期观察", [button.label for button in app.button])
        self.assertEqual(app.button[-1].key, "tracking_add_instrument")

    def test_history_shows_total_equity_ratio_instead_of_cash_change(self) -> None:
        application = get_application()
        project = application.create_project(CreateProjectCommand("历史占比测试", Decimal("100000"), "test-user"))
        application.add_tracking(AddTrackedInstrumentCommand(project.project_key, "600000", "浦发银行", "观察来源", "test-user"))
        application.confirm_manual_trade(ConfirmManualTradeCommand(
            project_key=project.project_key,
            symbol="600000.SH",
            side=TradeSide.BUY,
            allocation_ratio=Decimal("0.5"),
            price=Decimal("10"),
            signal_text="测试买入",
            actor="test-user",
        ))
        application.confirm_manual_trade(ConfirmManualTradeCommand(
            project_key=project.project_key,
            symbol="600000.SH",
            side=TradeSide.SELL,
            allocation_ratio=Decimal("0.25"),
            price=Decimal("11"),
            signal_text="测试卖出",
            actor="test-user",
        ))
        app = self._app().run()
        app.radio[0].set_value("操作历史").run()
        self.assertEqual(app.exception, [])
        frame = app.dataframe[0].value
        self.assertNotIn("资金变动", frame.columns)
        self.assertNotIn("股票代码", frame.columns)
        self.assertNotIn("股票名称", frame.columns)
        self.assertTrue((frame["股票"] == "600000.SH 浦发银行").all())
        self.assertEqual(frame.loc[frame["操作"] == "买入", "交易比例"].iloc[0], 0.5)
        self.assertEqual(frame.loc[frame["操作"] == "卖出", "交易比例"].iloc[0], 0.25)
        self.assertTrue(frame.loc[frame["操作"] == "观察", "交易比例"].isna().all())
        self.assertIn("总仓占比", frame.columns)
        self.assertEqual(frame.loc[frame["操作"] == "买入", "总仓占比"].iloc[0], 0.49)

    def test_tracking_displays_buy_sell_prices_and_profit_after_liquidation(self) -> None:
        application = get_application()
        project = application.create_project(CreateProjectCommand("清仓均价测试", Decimal("100000"), "test-user"))
        application.add_tracking(AddTrackedInstrumentCommand(project.project_key, "600000", "浦发银行", "观察来源", "test-user"))
        for side, price, ratio in ((TradeSide.BUY, "10", "0.5"), (TradeSide.SELL, "11", "1")):
            application.confirm_manual_trade(ConfirmManualTradeCommand(
                project_key=project.project_key,
                symbol="600000.SH", side=side, allocation_ratio=Decimal(ratio),
                price=Decimal(price), signal_text="均价测试", actor="test-user",
            ))
        app = self._app().run()
        self.assertEqual(app.exception, [])
        markdown = [item.value for item in app.markdown]
        self.assertIn("**买入/卖出均价**", markdown)
        self.assertIn("**实盈/浮盈**", markdown)
        self.assertNotIn("**盈亏**", markdown)
        self.assertIn("¥10.00 / ¥11.00", markdown)
        self.assertIn("¥11.00", markdown)
        self.assertTrue(any("+9.9%" in value and " / " in value and "+0.0%" in value for value in markdown))
        self.assertTrue(any(item.value == "成交参考" for item in app.caption))


if __name__ == "__main__":
    unittest.main()
