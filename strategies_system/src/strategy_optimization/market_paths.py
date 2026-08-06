"""Compile a locked Market Path Set selection into explicit components."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from experiment_system import (
    ComponentSpec,
    ExperimentSpec,
    ScenarioGroupSpec,
    sha256_document,
)
from market_simulator import MarketPathRole, load_market_path_set

from .errors import StudyConfigError
from .models import DatasetRole, MarketPathSelectionSpec


PATH_SET_SELECTION_COMPONENT_V1 = "market-path-set-selection/v1"
LOCKED_MARKET_PATH_COMPONENT_V1 = "locked-market-path/v1"
MANIFEST_SCHEMA_VERSION = "synthetic-market-path-set-manifest/v1"


def _read_json(path: Path, *, context: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StudyConfigError(f"cannot read {context}: {path}") from exc
    except json.JSONDecodeError as exc:
        raise StudyConfigError(
            f"invalid JSON in {context} {path}:{exc.lineno}:{exc.colno}"
        ) from exc
    if not isinstance(document, dict):
        raise StudyConfigError(f"{context} must be an object")
    return document


def _string(entry: Mapping[str, object], key: str, *, context: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise StudyConfigError(f"{context}.{key} must be a non-empty string")
    return value


def _integer(entry: Mapping[str, object], key: str, *, context: str) -> int:
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudyConfigError(f"{context}.{key} must be an integer")
    return value


def _sha256(entry: Mapping[str, object], key: str, *, context: str) -> str:
    value = _string(entry, key, context=context).lower()
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise StudyConfigError(f"{context}.{key} must be a SHA-256 digest")
    return value


def _manifest_lock_document(manifest: Mapping[str, object]) -> dict[str, object]:
    excluded = {
        "lock_fingerprint",
        "materialized_at",
        "code_revision",
        "reproducible",
    }
    return {key: value for key, value in manifest.items() if key not in excluded}


@dataclass(frozen=True, slots=True)
class LockedPathSetBinding:
    selection_id: str
    path_set_id: str
    path_set_fingerprint: str
    lock_fingerprint: str
    manifest_sha256: str
    components: tuple[ComponentSpec, ...]

    def to_document(self) -> dict[str, object]:
        return {
            "schema_version": "locked-path-set-binding/v1",
            "selection_id": self.selection_id,
            "path_set_id": self.path_set_id,
            "path_set_fingerprint": self.path_set_fingerprint,
            "lock_fingerprint": self.lock_fingerprint,
            "manifest_sha256": self.manifest_sha256,
            "selected_path_count": len(self.components),
            "paths": [
                {
                    "path_key": component.parameters["path_key"],
                    "scenario_id": component.parameters["scenario_id"],
                    "role": component.parameters["role"],
                    "market_seed": component.parameters["market_seed"],
                    "market_path_id": component.parameters["market_path_id"],
                    "content_sha256": component.parameters["content_sha256"],
                    "file_sha256": component.parameters["file_sha256"],
                    "origin": component.parameters["origin"],
                }
                for component in self.components
            ],
        }


def _selected_component(
    entry: Mapping[str, object],
    *,
    path_set_id: str,
    lock_fingerprint: str,
    manifest_sha256: str,
) -> ComponentSpec:
    context = "market path manifest.paths[]"
    role = _string(entry, "role", context=context)
    if role not in {DatasetRole.TRAIN.value, DatasetRole.VALIDATION.value}:
        raise StudyConfigError(
            "HOLDOUT market paths cannot be compiled into a development Study"
        )
    scenario_id = _string(entry, "scenario_id", context=context)
    market_seed = _integer(entry, "market_seed", context=context)
    content_sha256 = _sha256(entry, "content_sha256", context=context)
    file_sha256 = _sha256(entry, "file_sha256", context=context)
    market_path_id = _string(entry, "market_path_id", context=context)
    if market_path_id != content_sha256[:20]:
        raise StudyConfigError(
            f"{context}.market_path_id does not match content_sha256"
        )
    origin = _string(entry, "origin", context=context)
    if origin not in {"SYNTHETIC", "HISTORICAL"}:
        raise StudyConfigError(f"{context}.origin is unsupported")
    path_key = _string(entry, "path_key", context=context)
    expected_path_key = f"{scenario_id}:{role}:{market_seed}"
    if path_key != expected_path_key:
        raise StudyConfigError(
            f"{context}.path_key must be {expected_path_key!r}"
        )
    return ComponentSpec(
        key=f"{scenario_id}-{role.lower()}-{market_seed}",
        type=LOCKED_MARKET_PATH_COMPONENT_V1,
        parameters={
            "path_set_id": path_set_id,
            "path_set_lock_fingerprint": lock_fingerprint,
            "manifest_sha256": manifest_sha256,
            "path_key": path_key,
            "scenario_id": scenario_id,
            "role": role,
            "market_seed": market_seed,
            "origin": origin,
            "market_path_id": market_path_id,
            "path": _string(entry, "storage_path", context=context),
            "instrument": _string(entry, "instrument", context=context),
            "interval": _string(entry, "interval", context=context),
            "frame_count": _integer(entry, "frame_count", context=context),
            "content_sha256": content_sha256,
            "file_sha256": file_sha256,
        },
    )


def resolve_locked_path_set(
    selection: MarketPathSelectionSpec,
) -> LockedPathSetBinding:
    root = selection.resolved_environment_root
    if root is None:
        raise StudyConfigError(
            "market path selection must be loaded from a source path before compilation"
        )
    if not root.is_dir():
        raise StudyConfigError(f"market environment root does not exist: {root}")
    path_set_path = root / "path_sets" / f"{selection.path_set_id}.json"
    manifest_path = root / "manifests" / f"{selection.path_set_id}.json"
    try:
        path_set = load_market_path_set(path_set_path)
    except (OSError, ValueError) as exc:
        raise StudyConfigError(
            f"cannot load market PathSet {selection.path_set_id!r}: {exc}"
        ) from exc
    if path_set.path_set_id != selection.path_set_id:
        raise StudyConfigError("market PathSet id does not match selection")
    if path_set.status.value != "LOCKED":
        raise StudyConfigError("market PathSet must be LOCKED before Study use")
    available_scenarios = {item.scenario_id for item in path_set.scenarios}
    missing_scenarios = set(selection.scenario_ids) - available_scenarios
    if missing_scenarios:
        raise StudyConfigError(
            f"market PathSet does not contain scenarios: {sorted(missing_scenarios)}"
        )
    for role, seeds in selection.role_seeds.items():
        available_seeds = set(path_set.role_seeds[MarketPathRole(role.value)])
        missing_seeds = set(seeds) - available_seeds
        if missing_seeds:
            raise StudyConfigError(
                f"market PathSet {role.value} does not contain seeds: "
                f"{sorted(missing_seeds)}"
            )

    manifest = _read_json(manifest_path, context="market PathSet manifest")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise StudyConfigError("market PathSet manifest schema is unsupported")
    if manifest.get("path_set_id") != selection.path_set_id:
        raise StudyConfigError("market PathSet manifest id does not match selection")
    if manifest.get("status") != "CONTENT_LOCKED":
        raise StudyConfigError("market PathSet manifest must be CONTENT_LOCKED")
    if manifest.get("reproducible") is not True:
        raise StudyConfigError("market PathSet manifest must be reproducible")
    if manifest.get("holdout_strategy_execution_allowed") is not False:
        raise StudyConfigError(
            "market PathSet manifest must prohibit HOLDOUT strategy execution"
        )
    path_set_fingerprint = _sha256(
        manifest,
        "path_set_fingerprint",
        context="market PathSet manifest",
    )
    if path_set_fingerprint != path_set.fingerprint:
        raise StudyConfigError("market PathSet fingerprint does not match manifest")
    lock_fingerprint = _sha256(
        manifest,
        "lock_fingerprint",
        context="market PathSet manifest",
    )
    if sha256_document(_manifest_lock_document(manifest)) != lock_fingerprint:
        raise StudyConfigError("market PathSet manifest lock fingerprint is invalid")
    manifest_sha256 = sha256_document(manifest)
    raw_entries = manifest.get("paths")
    if not isinstance(raw_entries, list) or any(
        not isinstance(entry, Mapping) for entry in raw_entries
    ):
        raise StudyConfigError("market PathSet manifest paths must be objects")
    expected = {
        (scenario_id, role.value, seed)
        for scenario_id in selection.scenario_ids
        for role, seeds in selection.role_seeds.items()
        for seed in seeds
    }
    matched: dict[tuple[str, str, int], Mapping[str, object]] = {}
    for entry in raw_entries:
        assert isinstance(entry, Mapping)
        scenario_id = entry.get("scenario_id")
        role = entry.get("role")
        seed = entry.get("market_seed")
        key = (scenario_id, role, seed)
        if key not in expected:
            continue
        typed_key = (str(scenario_id), str(role), int(seed))
        if typed_key in matched:
            raise StudyConfigError(
                f"duplicate selected market path identity {typed_key!r}"
            )
        matched[typed_key] = entry
    missing = expected - set(matched)
    if missing:
        raise StudyConfigError(
            f"market PathSet manifest is missing selected paths: {sorted(missing)}"
        )
    components = tuple(
        _selected_component(
            matched[key],
            path_set_id=selection.path_set_id,
            lock_fingerprint=lock_fingerprint,
            manifest_sha256=manifest_sha256,
        )
        for key in sorted(
            expected,
            key=lambda item: (
                selection.scenario_ids.index(item[0]),
                0 if item[1] == DatasetRole.TRAIN.value else 1,
                item[2],
            ),
        )
    )
    return LockedPathSetBinding(
        selection_id=selection.selection_id,
        path_set_id=selection.path_set_id,
        path_set_fingerprint=path_set_fingerprint,
        lock_fingerprint=lock_fingerprint,
        manifest_sha256=manifest_sha256,
        components=components,
    )


def expand_path_set_markets(
    experiment: ExperimentSpec,
    selection: MarketPathSelectionSpec,
) -> tuple[ExperimentSpec, LockedPathSetBinding]:
    """Replace each Study placeholder with the selected immutable paths."""

    if len(experiment.seeds) != 1:
        raise StudyConfigError(
            "a locked PathSet Experiment must use exactly one Run Seed; "
            "Market Seed is already part of each market component"
        )
    binding = resolve_locked_path_set(selection)
    groups: list[ScenarioGroupSpec] = []
    for group in experiment.scenario_groups:
        placeholders = [
            component
            for component in group.markets
            if component.type == PATH_SET_SELECTION_COMPONENT_V1
        ]
        if len(placeholders) != 1 or len(group.markets) != 1:
            raise StudyConfigError(
                f"PathSet Study group {group.key!r} must contain exactly one "
                f"{PATH_SET_SELECTION_COMPONENT_V1!r} market placeholder"
            )
        placeholder = placeholders[0]
        if dict(placeholder.parameters) != {
            "selection_id": selection.selection_id
        }:
            raise StudyConfigError(
                f"PathSet Study group {group.key!r} placeholder must reference "
                f"selection_id={selection.selection_id!r}"
            )
        groups.append(replace(group, markets=binding.components))
    return replace(experiment, scenario_groups=tuple(groups)), binding
