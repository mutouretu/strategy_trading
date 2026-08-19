"""Pure, runtime-independent trading strategies with lazy public exports."""

from __future__ import annotations

from importlib import import_module


_EXPORT_MODULES = {
    "CoinMLongPositionSizer": ".btc_accumulation",
    "CoinMLongTakeProfitLadderConfig": ".btc_accumulation",
    "CoinMLongTakeProfitLadderStrategyDefinition": ".btc_accumulation",
    "ENTRY_THEN_LADDER_EXIT_V1": ".entry_then_ladder_exit",
    "EntryPlan": ".btc_accumulation",
    "EntrySizingMode": ".btc_accumulation",
    "EntryThenLadderExitParameters": ".entry_then_ladder_exit",
    "EntryThenLadderExitStrategyDefinition": ".entry_then_ladder_exit",
    "HoldBtcConfig": ".baselines",
    "HoldBtcStrategy": ".baselines",
    "LadderState": ".btc_accumulation",
    "PositionEffect": ".kernel",
    "PositionPlan": ".btc_accumulation",
    "RuleConfigFieldDefinition": ".kernel",
    "RuleIntentMode": ".kernel",
    "RuleIntentProposal": ".kernel",
    "RuleLifecycleRequest": ".kernel",
    "RuleTransition": ".kernel",
    "StrategyFill": ".btc_accumulation",
    "StrategyOrderSide": ".btc_accumulation",
    "StrategyRole": ".btc_accumulation",
    "StrategyDefinitionDescriptor": ".strategy",
    "StrategyDefinitionRegistry": ".strategy",
    "StrategyParameterDefinition": ".strategy",
    "StrategyRuleCompositionDefinition": ".strategy",
    "TakeProfitLevel": ".btc_accumulation",
    "TradeSide": ".kernel",
    "TradingEvent": ".kernel",
    "TradingEventKind": ".kernel",
    "TradingFillEvent": ".kernel",
    "TradingMarketEvent": ".kernel",
    "TradingPositionChangedEvent": ".kernel",
    "TradingRule": ".kernel",
    "TradingRuleDefinition": ".kernel",
    "TradingRuleRegistry": ".kernel",
    "TradingRuleSpec": ".kernel",
    "TradingSignalEvent": ".kernel",
    "TradingStartEvent": ".kernel",
    "TradingStopEvent": ".kernel",
    "build_take_profit_schedule": ".btc_accumulation",
    "build_strategy_definition_registry": ".catalog",
    "build_trading_rule_registry": ".rules",
}

__all__ = sorted(_EXPORT_MODULES)


def __getattr__(name: str) -> object:
    try:
        module_name = _EXPORT_MODULES[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    value = getattr(import_module(module_name, __name__), name)
    globals()[name] = value
    return value
