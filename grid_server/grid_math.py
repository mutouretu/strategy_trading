"""Compatibility import for pure grid calculations."""

from .domain.grid import (
    build_cells,
    decimal_text,
    next_long_cell,
    next_short_cell,
    round_down,
    stable_cell_id,
)

__all__ = [
    "build_cells",
    "decimal_text",
    "next_long_cell",
    "next_short_cell",
    "round_down",
    "stable_cell_id",
]
