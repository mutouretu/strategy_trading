"""Canonical JSON and stable identifiers for experiments."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from .json_values import to_plain_json
from .models import CodeRevision, ScenarioConfiguration


def canonical_json(document: Any) -> str:
    return json.dumps(
        to_plain_json(document),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_document(document: Any) -> str:
    payload = canonical_json(document).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def scenario_hash(configuration: ScenarioConfiguration) -> str:
    return sha256_document(configuration.semantic_document())


def run_configuration_hash(
    configuration: ScenarioConfiguration,
    seed: int,
) -> str:
    return sha256_document(
        {
            **configuration.semantic_document(),
            "seed": seed,
        }
    )


def run_fingerprint(
    configuration_hash: str,
    code_revisions: Mapping[str, CodeRevision],
) -> str:
    return sha256_document(
        {
            "configuration_hash": configuration_hash,
            "code_revisions": {
                name: revision.fingerprint_document()
                for name, revision in sorted(code_revisions.items())
            },
        }
    )
