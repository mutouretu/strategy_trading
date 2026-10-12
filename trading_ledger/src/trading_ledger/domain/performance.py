"""Period-local linked returns and drawdown, assuming end-of-day cash flows."""
from decimal import Decimal


def calculate_performance(opening_equity, opening_flow, points):
    if not points or opening_equity <= 0:
        return None, None
    previous_equity, previous_flow = opening_equity, opening_flow
    value = peak = Decimal("1")
    drawdown = Decimal("0")
    for equity, cumulative_flow in points:
        if previous_equity <= 0:
            return None, None
        value *= (equity - (cumulative_flow - previous_flow)) / previous_equity
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
        previous_equity, previous_flow = equity, cumulative_flow
    return value - 1, drawdown
