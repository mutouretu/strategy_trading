from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gridtrader.config import api_base_url, binance_credentials, load_environment


class ConfigTests(unittest.TestCase):
    def test_env_file_is_loaded_without_overwriting_exported_values(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_file = Path(tempdir) / ".env"
            env_file.write_text(
                "BINANCE_API_KEY=file-key\nBINANCE_API_SECRET=file-secret\nGRID_API_URL=http://api:8100\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"BINANCE_API_KEY": "exported-key"}, clear=True):
                loaded = load_environment(env_file)
                self.assertEqual(loaded, env_file)
                self.assertEqual(os.environ["BINANCE_API_KEY"], "exported-key")
                self.assertEqual(os.environ["BINANCE_API_SECRET"], "file-secret")
                self.assertEqual(os.environ["GRID_API_URL"], "http://api:8100")

    def test_missing_credentials_error_never_contains_values(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "BINANCE_API_KEY/BINANCE_API_SECRET are required"):
                binance_credentials(required=True)

    def test_frontend_reads_only_api_url_without_importing_binance_secrets(self):
        with tempfile.TemporaryDirectory() as tempdir:
            env_file = Path(tempdir) / ".env"
            env_file.write_text(
                "GRID_API_URL=http://frontend-api:9000\nBINANCE_API_SECRET=must-not-enter-process-env\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GRID_ENV_FILE": str(env_file)}, clear=True):
                self.assertEqual(api_base_url(), "http://frontend-api:9000")
                self.assertNotIn("BINANCE_API_SECRET", os.environ)


if __name__ == "__main__":
    unittest.main()
