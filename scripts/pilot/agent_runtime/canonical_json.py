"""The single schema-neutral canonical JSON implementation for Agent runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, TypeAlias


MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64

CanonicalJSONValue: TypeAlias = (
    None
    | bool
    | int
    | str
    | Mapping[str, "CanonicalJSONValue"]
    | Sequence["CanonicalJSONValue"]
)

__all__ = [
    "CanonicalJSONValue",
    "EvidenceError",
    "canonical_json_bytes",
    "canonical_json_digest",
    "parse_canonical_json",
]


class EvidenceError(ValueError):
    """Canonical JSON or typed public evidence violates its closed contract."""


class _FrozenMapping(Mapping[str, Any]):
    """Recursively immutable mapping with an explicit mutable-copy escape hatch."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("canonical JSON mappings are immutable")

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        import copy

        return {key: copy.deepcopy(value, memo) for key, value in self._values.items()}


class _FrozenSequence(tuple):
    """Immutable canonical JSON array with explicit mutable-copy support."""

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        import copy

        return [copy.deepcopy(value, memo) for value in self]


def _freeze(value: Any) -> CanonicalJSONValue:
    if isinstance(value, Mapping):
        return _FrozenMapping({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenSequence(_freeze(item) for item in value)
    return value


def _snapshot(value: Any) -> CanonicalJSONValue:
    try:
        return _freeze(value)
    except (RecursionError, RuntimeError, TypeError) as exc:
        raise EvidenceError("value cannot be snapshotted as canonical JSON") from exc


def _reject_number(_: str) -> None:
    raise EvidenceError("JSON numbers must be signed 64-bit integers")


def _parse_integer(raw: str) -> int:
    value = int(raw)
    if not -(2**63) <= value < 2**63:
        raise EvidenceError("JSON integer is outside signed 64-bit range")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _validate_json_value(value: Any) -> None:
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, Mapping):
            if depth > MAX_JSON_DEPTH:
                raise EvidenceError("JSON nesting depth exceeds limit")
            for key, child in item.items():
                if not isinstance(key, str) or not key.isascii():
                    raise EvidenceError("JSON object keys must be ASCII strings")
                stack.append((child, depth + 1))
        elif isinstance(item, (list, tuple)):
            if depth > MAX_JSON_DEPTH:
                raise EvidenceError("JSON nesting depth exceeds limit")
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if not item.isascii():
                raise EvidenceError("JSON string values must be ASCII")
        elif isinstance(item, bool) or item is None:
            continue
        elif isinstance(item, int):
            if not -(2**63) <= item < 2**63:
                raise EvidenceError("JSON integer is outside signed 64-bit range")
        else:
            raise EvidenceError("value is not canonical JSON")


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def parse_canonical_json(payload: bytes) -> CanonicalJSONValue:
    """Parse one schema-neutral value under the closed canonical JSON grammar."""

    if not isinstance(payload, bytes):
        raise EvidenceError("payload must be bytes")
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise EvidenceError("document exceeds byte limit")
    try:
        if payload.startswith(b"\xef\xbb\xbf"):
            raise EvidenceError("byte-order mark is forbidden")
        stored = payload[:-1] if payload.endswith(b"\n") else payload
        if stored.endswith(b"\n"):
            raise EvidenceError("only one trailing newline is permitted")
        value = json.loads(
            stored.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_number,
            parse_int=_parse_integer,
            parse_constant=_reject_number,
        )
    except EvidenceError:
        raise
    except RecursionError as exc:
        raise EvidenceError("JSON nesting depth exceeds limit") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid JSON payload") from exc
    frozen = _snapshot(value)
    if canonical_json_bytes(frozen) != stored:
        raise EvidenceError("non-canonical JSON encoding")
    return frozen


def canonical_json_bytes(value: CanonicalJSONValue) -> bytes:
    """Encode one schema-neutral canonical JSON value after closed validation."""

    frozen = _snapshot(value)
    _validate_json_value(frozen)
    try:
        encoded = json.dumps(
            _plain(frozen),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (UnicodeEncodeError, TypeError, ValueError) as exc:
        raise EvidenceError("value is not canonical JSON") from exc
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise EvidenceError("document exceeds byte limit")
    return encoded


def canonical_json_digest(value: CanonicalJSONValue) -> str:
    """Digest only the single canonical encoding of a schema-neutral value."""

    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
