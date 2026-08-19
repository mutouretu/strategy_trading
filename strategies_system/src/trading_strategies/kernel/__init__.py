"""Pure contracts shared by concrete trading-rule implementations."""

from .definition import (
    RuleConfigFieldDefinition,
    TradingRuleDefinition,
)
from .events import (
    TradingEvent,
    TradingEventKind,
    TradingFillEvent,
    TradingMarketEvent,
    TradingPositionChangedEvent,
    TradingSignalEvent,
    TradingStartEvent,
    TradingStopEvent,
)
from .intents import (
    PositionEffect,
    RuleIntentMode,
    RuleIntentProposal,
    TradeSide,
)
from .registry import TradingRuleRegistry
from .rule import TradingRule
from .spec import TradingRuleSpec
from .transition import RuleLifecycleRequest, RuleTransition

__all__ = [
    "PositionEffect",
    "RuleConfigFieldDefinition",
    "RuleIntentMode",
    "RuleIntentProposal",
    "RuleLifecycleRequest",
    "RuleTransition",
    "TradeSide",
    "TradingEvent",
    "TradingEventKind",
    "TradingFillEvent",
    "TradingMarketEvent",
    "TradingPositionChangedEvent",
    "TradingRule",
    "TradingRuleDefinition",
    "TradingRuleRegistry",
    "TradingRuleSpec",
    "TradingSignalEvent",
    "TradingStartEvent",
    "TradingStopEvent",
]
