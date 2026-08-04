"""Canonical compression and integrity checks for Trace payloads."""

from __future__ import annotations

import hashlib
import json
import zlib
from dataclasses import dataclass
from typing import Any

from .errors import ExperimentValidationError
from .hashing import canonical_json


TRACE_PAYLOAD_TYPE = "TRACE"
TRACE_COMPRESSION = "zlib"


@dataclass(frozen=True, slots=True)
class EncodedPayload:
    payload_type: str
    compression: str
    data: bytes
    uncompressed_size: int
    payload_sha256: str


def encode_trace(document: Any) -> EncodedPayload:
    raw = canonical_json(document).encode("utf-8")
    return EncodedPayload(
        payload_type=TRACE_PAYLOAD_TYPE,
        compression=TRACE_COMPRESSION,
        data=zlib.compress(raw, level=9),
        uncompressed_size=len(raw),
        payload_sha256=hashlib.sha256(raw).hexdigest(),
    )


def decode_trace(payload: EncodedPayload) -> dict[str, object]:
    if payload.payload_type != TRACE_PAYLOAD_TYPE:
        raise ExperimentValidationError(
            f"unsupported payload type {payload.payload_type!r}"
        )
    if payload.compression != TRACE_COMPRESSION:
        raise ExperimentValidationError(
            f"unsupported compression {payload.compression!r}"
        )
    try:
        raw = zlib.decompress(payload.data)
    except zlib.error as exc:
        raise ExperimentValidationError(
            "Trace payload decompression failed"
        ) from exc
    if len(raw) != payload.uncompressed_size:
        raise ExperimentValidationError(
            "Trace payload size check failed"
        )
    if hashlib.sha256(raw).hexdigest() != payload.payload_sha256:
        raise ExperimentValidationError(
            "Trace payload checksum failed"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExperimentValidationError(
            "Trace payload is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(document, dict):
        raise ExperimentValidationError(
            "Trace payload root must be an object"
        )
    return document
