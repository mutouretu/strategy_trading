import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from trading_ledger.infrastructure.quotes import PublicQuoteProvider, RealtimeQuote


class QuoteTimestampTests(unittest.TestCase):
    def test_missing_timestamp_uses_fallback_instead_of_fabricating_current_time(self):
        response = Mock()
        response.json.return_value = {"data": {"f57": "600000", "f43": 10, "f58": "浦发银行"}}
        fallback = RealtimeQuote(
            "600000.SH", "浦发银行", Decimal("10"),
            datetime(2026, 9, 4, 15, tzinfo=ZoneInfo("Asia/Shanghai")), "腾讯行情",
        )
        provider = PublicQuoteProvider()
        with (
            patch("trading_ledger.infrastructure.quotes.requests.get", return_value=response),
            patch("trading_ledger.infrastructure.quotes.time.sleep"),
            patch.object(provider, "_tencent", return_value=fallback) as tencent,
        ):
            self.assertEqual(provider.fetch("600000.SH"), fallback)
        tencent.assert_called_once()
