"""Strict JSON Pointer replacement for explicit parameter axes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .errors import ExperimentValidationError
from .json_values import JsonValue, to_plain_json


_ALLOWED_ROOTS = {"market", "strategy", "execution", "account"}


def _decode_token(token: str, *, pointer: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            result.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ExperimentValidationError(
                f"invalid JSON Pointer escape in {pointer!r}"
            )
        result.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(result)


def pointer_tokens(pointer: str) -> tuple[str, ...]:
    if not pointer.startswith("/"):
        raise ExperimentValidationError(
            f"JSON Pointer {pointer!r} must start with '/'"
        )
    tokens = tuple(
        _decode_token(token, pointer=pointer)
        for token in pointer[1:].split("/")
    )
    if len(tokens) < 3:
        raise ExperimentValidationError(
            f"parameter path {pointer!r} must target component parameters"
        )
    if tokens[0] not in _ALLOWED_ROOTS or tokens[1] != "parameters":
        raise ExperimentValidationError(
            f"parameter path {pointer!r} must start with "
            "/market/parameters, /strategy/parameters, "
            "/execution/parameters or /account/parameters"
        )
    return tokens


def replace_pointer(
    document: dict[str, Any],
    pointer: str,
    value: JsonValue,
) -> dict[str, Any]:
    """Return a copy with one existing JSON Pointer target replaced."""

    tokens = pointer_tokens(pointer)
    updated = deepcopy(document)
    current: Any = updated
    for token in tokens[:-1]:
        if isinstance(current, dict):
            if token not in current:
                raise ExperimentValidationError(
                    f"parameter path {pointer!r} does not exist"
                )
            current = current[token]
            continue
        if isinstance(current, list):
            try:
                list_index = int(token)
            except ValueError as exc:
                raise ExperimentValidationError(
                    f"parameter path {pointer!r} has invalid list index"
                ) from exc
            if list_index < 0 or list_index >= len(current):
                raise ExperimentValidationError(
                    f"parameter path {pointer!r} list index is out of range"
                )
            current = current[list_index]
            continue
        raise ExperimentValidationError(
            f"parameter path {pointer!r} crosses a scalar value"
        )

    final = tokens[-1]
    replacement = to_plain_json(value)
    if isinstance(current, dict):
        if final not in current:
            raise ExperimentValidationError(
                f"parameter path {pointer!r} does not exist"
            )
        current[final] = replacement
        return updated
    if isinstance(current, list):
        try:
            list_index = int(final)
        except ValueError as exc:
            raise ExperimentValidationError(
                f"parameter path {pointer!r} has invalid list index"
            ) from exc
        if list_index < 0 or list_index >= len(current):
            raise ExperimentValidationError(
                f"parameter path {pointer!r} list index is out of range"
            )
        current[list_index] = replacement
        return updated
    raise ExperimentValidationError(
        f"parameter path {pointer!r} targets a scalar parent"
    )
