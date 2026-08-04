"""Versioned domain models for reproducible market environments."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping


ASSET_PROFILE_SCHEMA_VERSION = "asset-profile/v1"
MARKET_SCENARIO_SCHEMA_VERSION = "market-scenario/v1"
MARKET_PATH_SET_SCHEMA_VERSION = "market-path-set/v1"
MARKET_PROFILE_SCHEMA_VERSION = "market-profile/v1"


class ScenarioOrigin(str, Enum):
    HISTORICAL = "HISTORICAL"
    SYNTHETIC = "SYNTHETIC"


class ScenarioStatus(str, Enum):
    DRAFT = "DRAFT"
    LOCKED = "LOCKED"
    RETIRED = "RETIRED"


class AnchorTargetType(str, Enum):
    HARD = "HARD"
    BAND = "BAND"


class MarketPathRole(str, Enum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    HOLDOUT = "HOLDOUT"


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {
            str(key): _plain(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def canonical_json(document: Mapping[str, object]) -> str:
    return json.dumps(
        _plain(document),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def document_sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AssetProfile:
    profile_id: str
    name: str
    calendar: str
    periods_per_year: int
    price_quantum: Decimal
    default_interval: str
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = ASSET_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ASSET_PROFILE_SCHEMA_VERSION:
            raise ValueError("unsupported asset profile schema version")
        if not self.profile_id.strip() or not self.name.strip():
            raise ValueError("asset profile identity and name must not be empty")
        if self.calendar != "24x7":
            raise ValueError("market environment v1 supports only 24x7 assets")
        if self.periods_per_year <= 0 or self.price_quantum <= 0:
            raise ValueError("asset profile numeric values must be > 0")
        if self.default_interval != "1h":
            raise ValueError("market environment v1 requires default_interval=1h")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "name": self.name,
            "calendar": self.calendar,
            "periods_per_year": self.periods_per_year,
            "price_quantum": str(self.price_quantum),
            "default_interval": self.default_interval,
            "metadata": dict(self.metadata),
        }

    @property
    def fingerprint(self) -> str:
        return document_sha256(self.to_document())


@dataclass(frozen=True, slots=True)
class AnchorTarget:
    type: AnchorTargetType
    price: Decimal | None = None
    minimum: Decimal | None = None
    maximum: Decimal | None = None

    def __post_init__(self) -> None:
        if self.type is AnchorTargetType.HARD:
            if self.price is None or self.price <= 0:
                raise ValueError("HARD anchor price must be > 0")
            if self.minimum is not None or self.maximum is not None:
                raise ValueError("HARD anchor cannot contain a band")
            return
        if self.price is not None:
            raise ValueError("BAND anchor cannot contain an exact price")
        if (
            self.minimum is None
            or self.maximum is None
            or self.minimum <= 0
            or self.minimum > self.maximum
        ):
            raise ValueError("BAND anchor requires 0 < minimum <= maximum")

    def to_document(self) -> dict[str, object]:
        if self.type is AnchorTargetType.HARD:
            return {"type": self.type.value, "price": str(self.price)}
        return {
            "type": self.type.value,
            "minimum": str(self.minimum),
            "maximum": str(self.maximum),
        }


@dataclass(frozen=True, slots=True)
class ScenarioAnchor:
    date: date
    target: AnchorTarget

    def to_document(self) -> dict[str, object]:
        return {"date": self.date.isoformat(), "target": self.target.to_document()}


@dataclass(frozen=True, slots=True)
class VolatilityRegime:
    start: date
    end_exclusive: date
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        if self.start >= self.end_exclusive:
            raise ValueError("volatility regime must have positive duration")
        if self.minimum < 0 or self.minimum > self.maximum:
            raise ValueError("volatility requires 0 <= minimum <= maximum")

    def to_document(self) -> dict[str, object]:
        return {
            "start": self.start.isoformat(),
            "end_exclusive": self.end_exclusive.isoformat(),
            "annual_volatility": {
                "minimum": str(self.minimum),
                "maximum": str(self.maximum),
            },
        }


@dataclass(frozen=True, slots=True)
class MarketModelSpec:
    type: str
    price_quantum: Decimal
    periods_per_year: int
    substeps_per_bar: int

    def __post_init__(self) -> None:
        if not self.type.strip():
            raise ValueError("market model type must not be empty")
        if self.price_quantum <= 0 or self.periods_per_year <= 0:
            raise ValueError("market model numeric values must be > 0")
        if self.substeps_per_bar < 2:
            raise ValueError("substeps_per_bar must be >= 2")

    def to_document(self) -> dict[str, object]:
        return {
            "type": self.type,
            "price_quantum": str(self.price_quantum),
            "periods_per_year": self.periods_per_year,
            "substeps_per_bar": self.substeps_per_bar,
        }


@dataclass(frozen=True, slots=True)
class MarketScenario:
    scenario_id: str
    name: str
    description: str
    origin: ScenarioOrigin
    asset_profile_id: str
    instrument: str
    start: date
    end: date
    interval: str
    model: MarketModelSpec
    anchors: tuple[ScenarioAnchor, ...]
    volatility_regimes: tuple[VolatilityRegime, ...]
    status: ScenarioStatus
    metadata: Mapping[str, object] = field(default_factory=dict)
    schema_version: str = MARKET_SCENARIO_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MARKET_SCENARIO_SCHEMA_VERSION:
            raise ValueError("unsupported market scenario schema version")
        if self.origin is not ScenarioOrigin.SYNTHETIC:
            raise ValueError("market scenario v1 generator requires SYNTHETIC origin")
        if not all(
            value.strip()
            for value in (
                self.scenario_id,
                self.name,
                self.description,
                self.asset_profile_id,
                self.instrument,
            )
        ):
            raise ValueError("market scenario string fields must not be empty")
        if self.start >= self.end or self.interval != "1h":
            raise ValueError("market scenario requires a positive 1h horizon")
        if len(self.anchors) < 2:
            raise ValueError("market scenario requires at least two anchors")
        if self.anchors[0].date != self.start or self.anchors[-1].date != self.end:
            raise ValueError("first and last anchors must equal scenario horizon")
        if any(
            current.date >= following.date
            for current, following in zip(self.anchors, self.anchors[1:])
        ):
            raise ValueError("scenario anchors must be strictly increasing")
        if len(self.volatility_regimes) != len(self.anchors) - 1:
            raise ValueError("one volatility regime is required per anchor segment")
        for regime, start, end in zip(
            self.volatility_regimes,
            self.anchors,
            self.anchors[1:],
        ):
            if regime.start != start.date or regime.end_exclusive != end.date:
                raise ValueError("volatility regimes must exactly cover anchor segments")
        if self.model.price_quantum <= 0:
            raise ValueError("scenario model price quantum must be > 0")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "origin": self.origin.value,
            "asset_profile_id": self.asset_profile_id,
            "instrument": self.instrument,
            "horizon": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "interval": self.interval,
            "model": self.model.to_document(),
            "anchors": [anchor.to_document() for anchor in self.anchors],
            "volatility_regimes": [
                regime.to_document() for regime in self.volatility_regimes
            ],
            "status": self.status.value,
            "metadata": dict(self.metadata),
        }

    @property
    def fingerprint(self) -> str:
        return document_sha256(self.to_document())


@dataclass(frozen=True, slots=True)
class ScenarioReference:
    scenario_id: str
    path: str

    def to_document(self) -> dict[str, str]:
        return {"scenario_id": self.scenario_id, "path": self.path}


@dataclass(frozen=True, slots=True)
class MarketPathSet:
    path_set_id: str
    description: str
    asset_profile_path: str
    scenarios: tuple[ScenarioReference, ...]
    role_seeds: Mapping[MarketPathRole, tuple[int, ...]]
    status: ScenarioStatus
    schema_version: str = MARKET_PATH_SET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != MARKET_PATH_SET_SCHEMA_VERSION:
            raise ValueError("unsupported market path set schema version")
        if not self.path_set_id.strip() or not self.description.strip():
            raise ValueError("market path set identity and description are required")
        if not self.asset_profile_path.strip() or not self.scenarios:
            raise ValueError("market path set requires profile and scenarios")
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("market path set scenario ids must be unique")
        if set(self.role_seeds) != set(MarketPathRole):
            raise ValueError("market path set requires all three roles")
        all_seeds: list[int] = []
        for role, seeds in self.role_seeds.items():
            if not seeds or any(isinstance(seed, bool) or seed < 0 for seed in seeds):
                raise ValueError(f"{role.value} seeds must be non-empty integers >= 0")
            if len(seeds) != len(set(seeds)):
                raise ValueError(f"{role.value} seeds must be unique")
            all_seeds.extend(seeds)
        if len(all_seeds) != len(set(all_seeds)):
            raise ValueError("market path seeds must not overlap across roles")
        object.__setattr__(
            self,
            "role_seeds",
            MappingProxyType({role: tuple(seeds) for role, seeds in self.role_seeds.items()}),
        )

    @property
    def path_count(self) -> int:
        return len(self.scenarios) * sum(len(seeds) for seeds in self.role_seeds.values())

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "path_set_id": self.path_set_id,
            "description": self.description,
            "asset_profile_path": self.asset_profile_path,
            "scenarios": [item.to_document() for item in self.scenarios],
            "roles": {
                role.value: list(self.role_seeds[role]) for role in MarketPathRole
            },
            "status": self.status.value,
        }

    @property
    def fingerprint(self) -> str:
        return document_sha256(self.to_document())
