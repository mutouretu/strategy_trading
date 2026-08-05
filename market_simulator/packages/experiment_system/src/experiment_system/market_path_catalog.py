"""Read-only catalog for content-locked synthetic market path sets."""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from market_protocol import MarketFrame

from .errors import (
    ExperimentAccessError,
    ExperimentRepositoryError,
    ExperimentValidationError,
)
from .market_data import (
    MARKET_DATASET_SCHEMA_VERSION,
    MarketReference,
    ParquetMarketStore,
)


MARKET_PATH_SET_MANIFEST_SCHEMA_VERSION = (
    "synthetic-market-path-set-manifest/v1"
)
VISIBLE_PATH_ROLES = ("TRAIN", "VALIDATION")
ALL_PATH_ROLES = (*VISIBLE_PATH_ROLES, "HOLDOUT")
CHART_INTERVALS = ("1w", "1m")


def _read_json(path: Path) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExperimentRepositoryError(
            f"cannot read market environment document: {path}"
        ) from exc
    if not isinstance(document, dict):
        raise ExperimentRepositoryError(
            f"market environment document must be an object: {path}"
        )
    return document


def _string(document: dict[str, object], name: str, *, context: str) -> str:
    value = document.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ExperimentRepositoryError(
            f"{context}.{name} must be a non-empty string"
        )
    return value


def _integer(document: dict[str, object], name: str, *, context: str) -> int:
    value = document.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExperimentRepositoryError(
            f"{context}.{name} must be an integer"
        )
    return value


def _mapping(
    document: dict[str, object],
    name: str,
    *,
    context: str,
) -> dict[str, object]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ExperimentRepositoryError(
            f"{context}.{name} must be an object"
        )
    return value


def _objects(
    document: dict[str, object],
    name: str,
    *,
    context: str,
) -> tuple[dict[str, object], ...]:
    value = document.get(name)
    if not isinstance(value, list) or any(
        not isinstance(item, dict) for item in value
    ):
        raise ExperimentRepositoryError(
            f"{context}.{name} must be an array of objects"
        )
    return tuple(value)


def _role_order(value: str) -> int:
    try:
        return ALL_PATH_ROLES.index(value)
    except ValueError as exc:
        raise ExperimentRepositoryError(
            f"unsupported market path role {value!r}"
        ) from exc


def _public_path(entry: dict[str, object]) -> dict[str, object]:
    context = "manifest.paths[]"
    role = _string(entry, "role", context=context)
    _role_order(role)
    result: dict[str, object] = {
        "path_key": _string(entry, "path_key", context=context),
        "scenario_id": _string(entry, "scenario_id", context=context),
        "role": role,
        "market_seed": _integer(entry, "market_seed", context=context),
        "market_path_id": _string(
            entry,
            "market_path_id",
            context=context,
        ),
        "frame_count": _integer(entry, "frame_count", context=context),
        "first_timestamp": _integer(
            entry,
            "first_timestamp",
            context=context,
        ),
        "last_timestamp": _integer(
            entry,
            "last_timestamp",
            context=context,
        ),
        "availability": "LOCKED" if role == "HOLDOUT" else "AVAILABLE",
    }
    if role in VISIBLE_PATH_ROLES:
        result.update(
            {
                "market_profile": _mapping(
                    entry,
                    "market_profile",
                    context=context,
                ),
                "resolved_anchors": list(
                    _objects(
                        entry,
                        "resolved_anchors",
                        context=context,
                    )
                ),
                "resolved_volatility_regimes": list(
                    _objects(
                        entry,
                        "resolved_volatility_regimes",
                        context=context,
                    )
                ),
            }
        )
    return result


def _scenario_definition(document: dict[str, object]) -> dict[str, object]:
    return {
        key: document[key]
        for key in (
            "scenario_id",
            "name",
            "description",
            "origin",
            "instrument",
            "horizon",
            "interval",
            "model",
            "anchors",
            "volatility_regimes",
            "status",
            "metadata",
        )
        if key in document
    }


def _bucket_start(timestamp: int, interval: str) -> tuple[str, int]:
    current = datetime.fromtimestamp(timestamp / 1000, tz=UTC)
    if interval == "1w":
        start = datetime.combine(
            current.date() - timedelta(days=current.weekday()),
            datetime.min.time(),
            tzinfo=UTC,
        )
    else:
        start = datetime(current.year, current.month, 1, tzinfo=UTC)
    return start.date().isoformat(), int(start.timestamp() * 1000)


def _aggregate_frames(
    frames: tuple[MarketFrame, ...],
    interval: str,
) -> list[dict[str, object]]:
    bars: list[dict[str, object]] = []
    current_key: str | None = None
    current: dict[str, object] | None = None
    for frame in frames:
        key, timestamp = _bucket_start(frame.timestamp, interval)
        if key != current_key:
            current = {
                "sequence": len(bars),
                "timestamp": timestamp,
                "date": key,
                "instrument": frame.instrument,
                "open": str(frame.open),
                "high": str(frame.high),
                "low": str(frame.low),
                "close": str(frame.close),
            }
            bars.append(current)
            current_key = key
            continue
        assert current is not None
        current["high"] = str(
            max(Decimal(str(current["high"])), frame.high)
        )
        current["low"] = str(
            min(Decimal(str(current["low"])), frame.low)
        )
        current["close"] = str(frame.close)
    return bars


class MarketPathSetCatalog:
    """Discover locked PathSets and disclose only research-safe paths."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).resolve() if root is not None else None
        if self.root is not None and not self.root.is_dir():
            raise ExperimentValidationError(
                f"market environment root does not exist: {self.root}"
            )
        self._cache: dict[
            tuple[str, str, str, str],
            dict[str, object],
        ] = {}
        self._cache_lock = threading.Lock()

    def _manifest_paths(self) -> tuple[Path, ...]:
        if self.root is None:
            return ()
        manifest_root = self.root / "manifests"
        if not manifest_root.is_dir():
            return ()
        return tuple(sorted(manifest_root.glob("*.json")))

    def _manifest(self, path_set_id: str) -> tuple[Path, dict[str, object]]:
        for path in self._manifest_paths():
            document = _read_json(path)
            if document.get("schema_version") != (
                MARKET_PATH_SET_MANIFEST_SCHEMA_VERSION
            ):
                continue
            if document.get("path_set_id") == path_set_id:
                return path, document
        raise ExperimentRepositoryError(
            f"market path set {path_set_id!r} does not exist"
        )

    def _path_set_spec(self, path_set_id: str) -> dict[str, object]:
        if self.root is None:
            return {}
        path = self.root / "path_sets" / f"{path_set_id}.json"
        return _read_json(path) if path.is_file() else {}

    def _scenario_documents(
        self,
        path_set_id: str,
    ) -> dict[str, dict[str, object]]:
        if self.root is None:
            return {}
        spec = self._path_set_spec(path_set_id)
        documents: dict[str, dict[str, object]] = {}
        raw_references = spec.get("scenarios", [])
        if not isinstance(raw_references, list):
            return documents
        base = self.root / "path_sets"
        for reference in raw_references:
            if not isinstance(reference, dict):
                continue
            scenario_id = reference.get("scenario_id")
            raw_path = reference.get("path")
            if not isinstance(scenario_id, str) or not isinstance(raw_path, str):
                continue
            path = (base / raw_path).resolve()
            try:
                path.relative_to(self.root)
            except ValueError as exc:
                raise ExperimentRepositoryError(
                    "scenario reference escapes market environment root"
                ) from exc
            documents[scenario_id] = _read_json(path)
        return documents

    def _catalog_document(
        self,
        manifest: dict[str, object],
    ) -> dict[str, object]:
        context = "market path set manifest"
        path_set_id = _string(manifest, "path_set_id", context=context)
        if manifest.get("schema_version") != (
            MARKET_PATH_SET_MANIFEST_SCHEMA_VERSION
        ):
            raise ExperimentRepositoryError(
                f"unsupported market path set manifest {path_set_id!r}"
            )
        spec = self._path_set_spec(path_set_id)
        scenario_documents = self._scenario_documents(path_set_id)
        entries = _objects(manifest, "paths", context=context)
        grouped: dict[str, list[dict[str, object]]] = {}
        for entry in entries:
            scenario_id = _string(
                entry,
                "scenario_id",
                context="manifest.paths[]",
            )
            grouped.setdefault(scenario_id, []).append(entry)

        scenarios: list[dict[str, object]] = []
        for scenario_id, paths in grouped.items():
            paths.sort(
                key=lambda item: (
                    _role_order(str(item.get("role"))),
                    int(item.get("market_seed", -1)),
                )
            )
            first = paths[0]
            definition = _scenario_definition(
                scenario_documents.get(scenario_id, {})
            )
            roles = {
                role: sum(1 for item in paths if item.get("role") == role)
                for role in ALL_PATH_ROLES
            }
            scenarios.append(
                {
                    "scenario_id": scenario_id,
                    "name": definition.get("name")
                    or first.get("scenario_name")
                    or scenario_id,
                    "description": definition.get("description", ""),
                    "origin": definition.get("origin")
                    or first.get("origin"),
                    "instrument": definition.get("instrument")
                    or first.get("instrument"),
                    "interval": definition.get("interval")
                    or first.get("interval"),
                    "model": definition.get("model")
                    or {"type": first.get("model_type")},
                    "horizon": definition.get("horizon"),
                    "anchors": definition.get("anchors", []),
                    "volatility_regimes": definition.get(
                        "volatility_regimes",
                        [],
                    ),
                    "metadata": definition.get("metadata", {}),
                    "role_counts": roles,
                    "paths": [_public_path(item) for item in paths],
                }
            )
        scenarios.sort(key=lambda item: str(item["scenario_id"]))
        role_counts = {
            role: sum(
                int(scenario["role_counts"][role])
                for scenario in scenarios
            )
            for role in ALL_PATH_ROLES
        }
        return {
            "path_set_id": path_set_id,
            "description": spec.get("description", ""),
            "status": manifest.get("status"),
            "reproducible": manifest.get("reproducible"),
            "scenario_count": len(scenarios),
            "path_count": len(entries),
            "role_counts": role_counts,
            "holdout_policy": {
                "materialized": bool(
                    manifest.get("holdout_materialized")
                ),
                "strategy_execution_allowed": bool(
                    manifest.get("holdout_strategy_execution_allowed")
                ),
                "price_disclosure_allowed": False,
            },
            "lock_fingerprint": manifest.get("lock_fingerprint"),
            "scenarios": scenarios,
        }

    def path_sets(self) -> tuple[dict[str, object], ...]:
        documents: list[dict[str, object]] = []
        for path in self._manifest_paths():
            manifest = _read_json(path)
            if manifest.get("schema_version") != (
                MARKET_PATH_SET_MANIFEST_SCHEMA_VERSION
            ):
                continue
            documents.append(self._catalog_document(manifest))
        documents.sort(key=lambda item: str(item["path_set_id"]))
        return tuple(documents)

    def _storage_path(self, entry: dict[str, object]) -> Path:
        if self.root is None:
            raise ExperimentRepositoryError(
                "market environment catalog is not configured"
            )
        raw = Path(
            _string(
                entry,
                "storage_path",
                context="manifest.paths[]",
            )
        )
        path = raw.resolve() if raw.is_absolute() else (
            self.root.parent / raw
        ).resolve()
        generated_root = (self.root / "generated").resolve()
        try:
            path.relative_to(generated_root)
        except ValueError as exc:
            raise ExperimentRepositoryError(
                "market path storage escapes generated data root"
            ) from exc
        if not path.is_file():
            raise ExperimentRepositoryError(
                f"market path data does not exist: {path.name}"
            )
        return path

    def path_document(
        self,
        path_set_id: str,
        market_path_id: str,
        *,
        interval: str,
    ) -> dict[str, object]:
        if interval not in CHART_INTERVALS:
            raise ExperimentValidationError(
                "market path interval must be '1w' or '1m'"
            )
        _, manifest = self._manifest(path_set_id)
        matches = [
            entry
            for entry in _objects(
                manifest,
                "paths",
                context="market path set manifest",
            )
            if entry.get("market_path_id") == market_path_id
        ]
        if not matches:
            raise ExperimentRepositoryError(
                f"market path {market_path_id!r} does not exist in "
                f"{path_set_id!r}"
            )
        if len(matches) != 1:
            raise ExperimentRepositoryError(
                f"market path {market_path_id!r} is not unique"
            )
        entry = matches[0]
        role = _string(entry, "role", context="manifest.paths[]")
        if role == "HOLDOUT":
            raise ExperimentAccessError(
                "HOLDOUT price paths are locked until final out-of-sample "
                "acceptance"
            )
        if role not in VISIBLE_PATH_ROLES:
            raise ExperimentRepositoryError(
                f"unsupported market path role {role!r}"
            )
        content_hash = _string(
            entry,
            "content_sha256",
            context="manifest.paths[]",
        )
        cache_key = (
            path_set_id,
            market_path_id,
            interval,
            content_hash,
        )
        with self._cache_lock:
            cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        path = self._storage_path(entry)
        reference = MarketReference(
            market_path_id=market_path_id,
            content_hash=content_hash,
            file_sha256=_string(
                entry,
                "file_sha256",
                context="manifest.paths[]",
            ),
            storage_path=str(path),
            schema_version=MARKET_DATASET_SCHEMA_VERSION,
            frame_count=_integer(
                entry,
                "frame_count",
                context="manifest.paths[]",
            ),
            instrument=_string(
                entry,
                "instrument",
                context="manifest.paths[]",
            ),
        )
        frames = ParquetMarketStore(path.parent).load(reference)
        document = {
            "path_set_id": path_set_id,
            "path": _public_path(entry),
            "source_interval": entry.get("interval"),
            "aggregation_interval": interval,
            "source_frame_count": len(frames),
            "market": _aggregate_frames(frames, interval),
        }
        with self._cache_lock:
            self._cache[cache_key] = document
        return document


__all__ = [
    "ALL_PATH_ROLES",
    "CHART_INTERVALS",
    "MARKET_PATH_SET_MANIFEST_SCHEMA_VERSION",
    "MarketPathSetCatalog",
    "VISIBLE_PATH_ROLES",
]
