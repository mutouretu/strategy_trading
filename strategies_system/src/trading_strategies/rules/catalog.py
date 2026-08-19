"""Built-in Rule catalog independent of experiment result storage."""

from __future__ import annotations

from ..kernel import TradingRuleRegistry
from .initial_entry import InitialEntryRule
from .ladder_take_profit import LadderTakeProfitRule


def build_trading_rule_registry() -> TradingRuleRegistry:
    registry = TradingRuleRegistry()
    registry.register(InitialEntryRule())
    registry.register(LadderTakeProfitRule())
    return registry
