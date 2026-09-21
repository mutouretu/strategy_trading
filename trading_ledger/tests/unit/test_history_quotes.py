import unittest
from datetime import date
from decimal import Decimal
from trading_ledger.infrastructure.history_quotes import parse_closes
from trading_ledger.infrastructure.quotes import QuoteError


class HistoricalQuoteTests(unittest.TestCase):
    def test_unadjusted_close_is_third_field_and_dates_are_filtered(self):
        payload = {"code": 0, "data": {"sh000001": {"day": [
            ["2026-09-04", "1", "2"], ["2026-09-07", "3", "4"], ["2026-09-08", "5", "6"]]}}}
        self.assertEqual(parse_closes(payload, "sh000001", date(2026, 9, 7), date(2026, 9, 7)), {"2026-09-07": Decimal("4")})

    def test_adjusted_or_wrong_symbol_or_invalid_price_is_rejected(self):
        for payload in [
            {"code": 0, "data": {"sz000001": {"day": [["2026-09-07", "1", "4"]]}}},
            {"code": 0, "data": {"sh000001": {"qfqday": [["2026-09-07", "1", "4"]]}}},
            {"code": 0, "data": {"sh000001": {"day": [["2026-09-07", "1", "NaN"]]}}},
        ]:
            with self.assertRaises(QuoteError):
                parse_closes(payload, "sh000001", date(2026, 9, 7), date(2026, 9, 8))
