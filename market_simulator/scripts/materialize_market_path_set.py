#!/usr/bin/env python3
"""Materialize and content-lock a versioned synthetic Market Path Set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from experiment_system import ParquetMarketStore, collect_git_revision
from market_simulator.market_environment import (
    MarketPathRole,
    aggregate_market_profiles,
    build_market_model_registry,
    document_sha256,
    load_asset_profile,
    load_market_path_set,
    load_market_scenario,
    profile_market_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH_SET = (
    PROJECT_ROOT
    / "market_environments"
    / "path_sets"
    / "btc-three-year-market-baseline-v1.json"
)


def _write_json(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _relative(path: str | Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(resolved)


def _path_entry(
    *,
    scenario,
    role: MarketPathRole,
    seed: int,
    generated,
    reference,
    profile,
) -> dict[str, object]:
    return {
        "path_key": f"{scenario.scenario_id}:{role.value}:{seed}",
        "scenario_id": scenario.scenario_id,
        "scenario_name": scenario.name,
        "scenario_fingerprint": scenario.fingerprint,
        "role": role.value,
        "market_seed": seed,
        "origin": scenario.origin.value,
        "instrument": scenario.instrument,
        "interval": scenario.interval,
        "model_type": scenario.model.type,
        "market_path_id": reference.market_path_id,
        "content_sha256": reference.content_hash,
        "file_sha256": reference.file_sha256,
        "storage_path": _relative(reference.storage_path),
        "frame_count": reference.frame_count,
        "first_timestamp": generated.frames[0].timestamp,
        "last_timestamp": generated.frames[-1].timestamp,
        "resolved_anchors": [
            item.to_document() for item in generated.resolved_anchors
        ],
        "resolved_volatility_regimes": [
            item.to_document()
            for item in generated.resolved_volatility_regimes
        ],
        "market_profile": profile.to_document(),
    }


def materialize_path_set(
    path_set_path: Path,
    *,
    output_root: Path,
    manifest_path: Path,
    refresh_lock: bool = False,
    progress=print,
) -> tuple[dict[str, object], bool]:
    path_set_path = path_set_path.resolve()
    path_set = load_market_path_set(path_set_path)
    base = path_set_path.parent
    asset_path = (base / path_set.asset_profile_path).resolve()
    asset_profile = load_asset_profile(asset_path)
    scenarios = []
    for reference in path_set.scenarios:
        scenario_path = (base / reference.path).resolve()
        scenario = load_market_scenario(scenario_path)
        if scenario.scenario_id != reference.scenario_id:
            raise ValueError(
                f"scenario reference {reference.scenario_id!r} does not match "
                f"{scenario.scenario_id!r}"
            )
        if scenario.asset_profile_id != asset_profile.profile_id:
            raise ValueError(
                f"scenario {scenario.scenario_id!r} uses a different asset profile"
            )
        scenarios.append(scenario)

    registry = build_market_model_registry()
    store = ParquetMarketStore(output_root.resolve())
    entries: list[dict[str, object]] = []
    profiles_by_scenario = defaultdict(list)
    profiles_by_scenario_role = defaultdict(list)
    completed = 0
    for scenario in scenarios:
        model = registry.get(scenario.model.type)
        for role in MarketPathRole:
            for seed in path_set.role_seeds[role]:
                generated = model.generate(
                    scenario,
                    asset_profile,
                    seed=seed,
                )
                profile = profile_market_path(generated.frames, scenario)
                reference = store.persist(generated.frames)
                entries.append(
                    _path_entry(
                        scenario=scenario,
                        role=role,
                        seed=seed,
                        generated=generated,
                        reference=reference,
                        profile=profile,
                    )
                )
                profiles_by_scenario[scenario.scenario_id].append(profile)
                profiles_by_scenario_role[(scenario.scenario_id, role)].append(
                    profile
                )
                completed += 1
                if progress is not None:
                    progress(
                        f"[{completed}/{path_set.path_count}] "
                        f"{scenario.scenario_id} {role.value} seed={seed} "
                        f"path={reference.market_path_id}"
                    )

    entries.sort(
        key=lambda item: (
            str(item["scenario_id"]),
            list(MarketPathRole).index(MarketPathRole(str(item["role"]))),
            int(item["market_seed"]),
        )
    )
    aggregates: dict[str, object] = {}
    for scenario in scenarios:
        aggregates[scenario.scenario_id] = {
            "all": aggregate_market_profiles(
                tuple(profiles_by_scenario[scenario.scenario_id])
            ),
            "roles": {
                role.value: aggregate_market_profiles(
                    tuple(profiles_by_scenario_role[(scenario.scenario_id, role)])
                )
                for role in MarketPathRole
            },
        }
    lock_document: dict[str, object] = {
        "schema_version": "synthetic-market-path-set-manifest/v1",
        "path_set_id": path_set.path_set_id,
        "path_set_fingerprint": path_set.fingerprint,
        "status": "CONTENT_LOCKED",
        "asset_profile": {
            "profile_id": asset_profile.profile_id,
            "fingerprint": asset_profile.fingerprint,
            "path": _relative(asset_path),
        },
        "scenario_count": len(scenarios),
        "path_count": len(entries),
        "holdout_materialized": True,
        "holdout_strategy_execution_allowed": False,
        "paths": entries,
        "scenario_aggregates": aggregates,
    }
    lock_fingerprint = document_sha256(lock_document)
    existing: dict[str, object] | None = None
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("existing market path manifest must be an object")
        existing = loaded
        if existing.get("lock_fingerprint") == lock_fingerprint:
            return existing, False
        if not refresh_lock:
            raise ValueError(
                "generated paths differ from the existing content lock; "
                "create a new version or pass --refresh-lock after review"
            )

    revision = collect_git_revision(PROJECT_ROOT)
    manifest = {
        **lock_document,
        "lock_fingerprint": lock_fingerprint,
        "materialized_at": datetime.now(timezone.utc).isoformat(),
        "code_revision": revision.to_document(),
        "reproducible": not revision.dirty,
    }
    _write_json(manifest_path, manifest)
    return manifest, True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("path_set", nargs="?", type=Path, default=DEFAULT_PATH_SET)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--refresh-lock", action="store_true")
    arguments = parser.parse_args()
    path_set_path = arguments.path_set.resolve()
    path_set = load_market_path_set(path_set_path)
    output_root = (
        arguments.output_root
        or PROJECT_ROOT / "market_environments" / "generated" / path_set.path_set_id
    )
    manifest_path = (
        arguments.manifest
        or PROJECT_ROOT
        / "market_environments"
        / "manifests"
        / f"{path_set.path_set_id}.json"
    )
    manifest, created = materialize_path_set(
        path_set_path,
        output_root=output_root,
        manifest_path=manifest_path,
        refresh_lock=arguments.refresh_lock,
    )
    print(
        json.dumps(
            {
                "created": created,
                "path_set_id": manifest["path_set_id"],
                "path_count": manifest["path_count"],
                "status": manifest["status"],
                "lock_fingerprint": manifest["lock_fingerprint"],
                "manifest": _relative(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
