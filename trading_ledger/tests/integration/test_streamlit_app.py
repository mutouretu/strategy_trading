from __future__ import annotations

import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from trading_ledger.application.contracts import (
    AddTrackedInstrumentCommand,
    CreateProjectCommand,
)
from trading_ledger.bootstrap import get_application, get_settings


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
        self.assertTrue(any(button.label == "记录今日估值" for button in app.button))

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
        self.assertIn(f"tracking_close_{tracking.tracking_id}", button_keys)
        self.assertIn(f"tracking_archive_{tracking.tracking_id}", button_keys)
        self.assertNotIn("清理到期观察", [button.label for button in app.button])
        self.assertEqual(app.button[-1].key, "tracking_add_instrument")


if __name__ == "__main__":
    unittest.main()
