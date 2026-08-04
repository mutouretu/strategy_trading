"""Explicit market model registry."""

from __future__ import annotations

from typing import Protocol

from market_protocol import MarketFrame

from .models import AssetProfile, MarketScenario


class MarketModel(Protocol):
    model_type: str

    def generate(
        self,
        scenario: MarketScenario,
        asset_profile: AssetProfile,
        *,
        seed: int,
    ) -> "GeneratedMarketPath": ...


class GeneratedMarketPath(Protocol):
    scenario_id: str
    seed: int
    frames: tuple[MarketFrame, ...]


class DuplicateMarketModelError(ValueError):
    pass


class UnknownMarketModelError(ValueError):
    pass


class MarketModelRegistry:
    def __init__(self) -> None:
        self._models: dict[str, MarketModel] = {}

    def register(self, model: MarketModel) -> None:
        model_type = model.model_type
        if not model_type.strip():
            raise ValueError("market model type must not be empty")
        if model_type in self._models:
            raise DuplicateMarketModelError(
                f"market model {model_type!r} is already registered"
            )
        self._models[model_type] = model

    def get(self, model_type: str) -> MarketModel:
        try:
            return self._models[model_type]
        except KeyError as exc:
            raise UnknownMarketModelError(
                f"unknown market model {model_type!r}"
            ) from exc

    @property
    def model_types(self) -> tuple[str, ...]:
        return tuple(sorted(self._models))
