import unittest
from decimal import Decimal

from gridtrader.price_format import format_price, infer_price_precision


class PriceFormatTests(unittest.TestCase):
    def test_ake_grid_infers_seven_decimal_places(self) -> None:
        precision = infer_price_precision("0.0015", "0.0011138", "0.0014705")

        self.assertEqual(precision, 7)
        self.assertEqual(format_price("0.00132662", precision), "0.0013266")
        self.assertEqual(format_price("0.0011138", precision), "0.0011138")

    def test_home_grid_ignores_insignificant_trailing_zero(self) -> None:
        precision = infer_price_precision("0.0070", "0.0094110")

        self.assertEqual(precision, 6)
        self.assertEqual(format_price(Decimal("0.00805277"), precision), "0.008053")

    def test_large_prices_keep_compact_existing_display(self) -> None:
        precision = infer_price_precision("118000", "120000")

        self.assertEqual(precision, 4)
        self.assertEqual(format_price("118000.0000", precision), "118,000")

    def test_missing_price_uses_placeholder(self) -> None:
        self.assertEqual(format_price(None, 7), "-")


if __name__ == "__main__":
    unittest.main()
