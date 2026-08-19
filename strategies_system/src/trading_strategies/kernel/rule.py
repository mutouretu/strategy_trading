"""Common abstract contract implemented by concrete trading rules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from .definition import TradingRuleDefinition
from .transition import RuleTransition


RuleConfigT = TypeVar("RuleConfigT")
InputT = TypeVar("InputT")
StateT = TypeVar("StateT")
ContextT = TypeVar("ContextT")


class TradingRule(ABC, Generic[RuleConfigT, InputT, StateT, ContextT]):
    """A stateless ``(state, input; config) -> transition`` algorithm.

    Per-run mutable state belongs to TradingRuleInstance. Concrete
    rules declare their accepted input set through TradingRuleDefinition and
    may receive only pure calculation capabilities through the context.
    """

    rule_type: ClassVar[str]

    @classmethod
    @abstractmethod
    def definition(cls) -> TradingRuleDefinition:
        ...

    @abstractmethod
    def initial_state(
        self,
        rule_config: RuleConfigT,
        context: ContextT,
    ) -> StateT:
        ...

    @abstractmethod
    def transition(
        self,
        rule_config: RuleConfigT,
        state: StateT,
        input: InputT,
        context: ContextT,
    ) -> RuleTransition[StateT]:
        ...
