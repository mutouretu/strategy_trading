"""High-level policies that configure and coordinate grid rules.

Strategy cores may depend on :mod:`grid_rule`, but remain independent of the
live server and simulation runtime. Runtime-specific translation belongs in
``grid_strategies.adapters``.
"""

from .single_following_grid import (
    SingleFollowingGridStrategy,
    SingleFollowingGridStrategyConfig,
)
from .layered_following_grid import (
    FollowingGridLayerSnapshot,
    LayeredFollowingGridStrategy,
    LayeredFollowingGridStrategyConfig,
)

__all__ = [
    "FollowingGridLayerSnapshot",
    "LayeredFollowingGridStrategy",
    "LayeredFollowingGridStrategyConfig",
    "SingleFollowingGridStrategy",
    "SingleFollowingGridStrategyConfig",
]
