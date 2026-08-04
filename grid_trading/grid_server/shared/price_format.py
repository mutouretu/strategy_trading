from __future__ import annotations

from decimal import Decimal, InvalidOperation


DEFAULT_PRICE_PRECISION = 4
MAX_PRICE_PRECISION = 12


def infer_price_precision(*values: object, default: int = DEFAULT_PRICE_PRECISION) -> int:
    """Infer display precision from meaningful decimals in persisted grid prices."""

    precision = max(0, int(default))
    for value in values:
        if value in (None, ""):
            continue
        try:
            text = format(Decimal(str(value)).normalize(), "f")
        except (InvalidOperation, ValueError):
            continue
        fractional = text.partition(".")[2]
        precision = max(precision, len(fractional.rstrip("0")))
    return min(precision, MAX_PRICE_PRECISION)


def format_price(value: object | None, precision: int = DEFAULT_PRICE_PRECISION) -> str:
    if value in (None, ""):
        return "-"
    number = Decimal(str(value))
    rendered = f"{number:,.{max(0, int(precision))}f}"
    return rendered.rstrip("0").rstrip(".")
