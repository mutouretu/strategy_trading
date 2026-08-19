"""Result of applying one typed input to one trading-rule state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar

from ._values import freeze_mapping
from .intents import RuleIntentProposal


StateT = TypeVar("StateT")


class RuleLifecycleRequest(StrEnum):
    NONE = "NONE"
    COMPLETE = "COMPLETE"
    STOP = "STOP"


@dataclass(frozen=True, slots=True)
class RuleTransition(Generic[StateT]):
    next_state: StateT
    intents: tuple[RuleIntentProposal, ...] = ()
    cancellations: tuple[str, ...] = ()
    lifecycle_request: RuleLifecycleRequest = RuleLifecycleRequest.NONE
    observations: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        intents = tuple(self.intents)
        if any(not isinstance(intent, RuleIntentProposal) for intent in intents):
            raise TypeError("intents must contain RuleIntentProposal values")
        intent_keys = [intent.intent_key for intent in intents]
        if len(set(intent_keys)) != len(intent_keys):
            raise ValueError("intent keys must be unique within a transition")
        object.__setattr__(self, "intents", intents)

        cancellations = tuple(self.cancellations)
        if any(not isinstance(key, str) or not key.strip() for key in cancellations):
            raise ValueError("cancellation keys must be non-empty strings")
        if len(set(cancellations)) != len(cancellations):
            raise ValueError("cancellation keys must be unique")
        if set(intent_keys) & set(cancellations):
            raise ValueError(
                "one transition must not issue and cancel the same intent"
            )
        object.__setattr__(self, "cancellations", cancellations)

        if not isinstance(self.lifecycle_request, RuleLifecycleRequest):
            raise TypeError("lifecycle_request must be a RuleLifecycleRequest")
        object.__setattr__(
            self,
            "observations",
            freeze_mapping(dict(self.observations)),
        )

    @classmethod
    def unchanged(cls, state: StateT) -> "RuleTransition[StateT]":
        return cls(next_state=state)
