from __future__ import annotations

import unittest
from pathlib import Path

from trading_ledger.config import MODULE_ROOT, Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_are_module_relative_and_local_only(self) -> None:
        settings = Settings.from_env({})
        self.assertEqual(
            settings.database_path,
            (MODULE_ROOT / "data" / "trading_ledger.sqlite3").resolve(),
        )
        self.assertEqual(settings.api_host, "127.0.0.1")
        self.assertEqual(settings.api_port, 8787)
        self.assertEqual(settings.business_timezone, "Asia/Shanghai")
        self.assertIsNone(settings.operator)

    def test_relative_database_override_is_still_module_relative(self) -> None:
        settings = Settings.from_env(
            {"TRADING_LEDGER_DB_PATH": "runtime/custom.sqlite3"}
        )
        self.assertEqual(
            settings.database_path,
            (MODULE_ROOT / "runtime" / "custom.sqlite3").resolve(),
        )

    def test_absolute_database_override_is_preserved(self) -> None:
        path = Path("/tmp/trading-ledger-contract-test.sqlite3")
        settings = Settings.from_env({"TRADING_LEDGER_DB_PATH": str(path)})
        self.assertEqual(settings.database_path, path.resolve())

    def test_token_is_not_in_repr(self) -> None:
        settings = Settings.from_env({"TRADING_LEDGER_API_TOKEN": "secret-value"})
        self.assertNotIn("secret-value", repr(settings))

    def test_invalid_port_is_rejected(self) -> None:
        for port in ("not-a-port", "0", "65536"):
            with self.subTest(port=port), self.assertRaises(ValueError):
                Settings.from_env({"TRADING_LEDGER_API_PORT": port})

if __name__ == "__main__":
    unittest.main()
