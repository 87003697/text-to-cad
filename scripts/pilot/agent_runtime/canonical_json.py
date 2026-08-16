"""The single schema-neutral canonical JSON implementation for Agent runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any, TypeAlias


MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_JSON_DEPTH = 64

__all__ = [
    "CanonicalJSONInput",
    "CanonicalJSONValue",
    "EvidenceError",
    "canonical_json_bytes",
    "canonical_json_digest",
    "parse_canonical_json",
]


class EvidenceError(ValueError):
    """Canonical JSON or typed public evidence violates its closed contract."""


class _FrozenJSONMapping(Mapping[str, Any]):
    """Recursively immutable mapping with an explicit mutable-copy escape hatch."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("canonical JSON mappings are immutable")

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        import copy

        return {key: copy.deepcopy(value, memo) for key, value in self._values.items()}


class _FrozenJSONSequence(tuple):
    """Immutable canonical JSON array with explicit mutable-copy support."""

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        import copy

        return [copy.deepcopy(value, memo) for value in self]


CanonicalJSONValue: TypeAlias = (
    None | bool | int | str | _FrozenJSONMapping | _FrozenJSONSequence
)
CanonicalJSONInput: TypeAlias = (
    None
    | bool
    | int
    | str
    | dict[str, "CanonicalJSONInput"]
    | list["CanonicalJSONInput"]
    | tuple["CanonicalJSONInput", ...]
    | CanonicalJSONValue
)


def _string_size(value: str, budget: int) -> int:
    size = 2
    if size > budget:
        raise EvidenceError("document exceeds byte limit")
    for character in value:
        codepoint = ord(character)
        if codepoint > 0x7F:
            raise EvidenceError("JSON string values must be ASCII")
        if character in {'"', "\\", "\b", "\f", "\n", "\r", "\t"}:
            size += 2
        elif codepoint < 0x20:
            size += 6
        else:
            size += 1
        if size > budget:
            raise EvidenceError("document exceeds byte limit")
    return size


def _snapshot_item(value: Any, budget: int, depth: int) -> tuple[CanonicalJSONValue, int]:
    value_type = type(value)
    if value_type is dict or value_type is _FrozenJSONMapping:
        if depth > MAX_JSON_DEPTH:
            raise EvidenceError("JSON nesting depth exceeds limit")
        frozen: dict[str, CanonicalJSONValue] = {}
        size = 2
        if size > budget:
            raise EvidenceError("document exceeds byte limit")
        for index, (key, child) in enumerate(value.items()):
            if type(key) is not str or not key.isascii():
                raise EvidenceError("JSON object keys must be ASCII strings")
            separator_size = 1 if index else 0
            key_size = _string_size(key, budget - size - separator_size)
            size += separator_size + key_size + 1
            if size > budget:
                raise EvidenceError("document exceeds byte limit")
            frozen_child, child_size = _snapshot_item(child, budget - size, depth + 1)
            size += child_size
            frozen[key] = frozen_child
        return _FrozenJSONMapping(frozen), size
    if value_type in {list, tuple, _FrozenJSONSequence}:
        if depth > MAX_JSON_DEPTH:
            raise EvidenceError("JSON nesting depth exceeds limit")
        frozen_items: list[CanonicalJSONValue] = []
        size = 2
        if size > budget:
            raise EvidenceError("document exceeds byte limit")
        for index, child in enumerate(value):
            size += 1 if index else 0
            if size > budget:
                raise EvidenceError("document exceeds byte limit")
            frozen_child, child_size = _snapshot_item(child, budget - size, depth + 1)
            size += child_size
            frozen_items.append(frozen_child)
        return _FrozenJSONSequence(frozen_items), size
    if value_type is str:
        return value, _string_size(value, budget)
    if value is None:
        size = 4
    elif value_type is bool:
        size = 4 if value else 5
    elif value_type is int:
        if not -(2**63) <= value < 2**63:
            raise EvidenceError("JSON integer is outside signed 64-bit range")
        size = len(str(value))
    else:
        raise EvidenceError("value is not canonical JSON")
    if size > budget:
        raise EvidenceError("document exceeds byte limit")
    return value, size


def _snapshot(value: Any) -> tuple[CanonicalJSONValue, int]:
    try:
        return _snapshot_item(value, MAX_DOCUMENT_BYTES, 1)
    except EvidenceError:
        raise
    except MemoryError:
        raise
    except Exception as exc:
        raise EvidenceError("value cannot be snapshotted as canonical JSON") from exc


def _freeze(value: Any) -> CanonicalJSONValue:
    return _snapshot(value)[0]


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
    frozen, _ = _snapshot(value)
    if canonical_json_bytes(frozen) != stored:
        raise EvidenceError("non-canonical JSON encoding")
    return frozen


def canonical_json_bytes(value: CanonicalJSONInput | CanonicalJSONValue) -> bytes:
    """Encode one schema-neutral canonical JSON value after closed validation."""

    frozen, expected_size = _snapshot(value)
    _validate_json_value(frozen)
    try:
        encoded = json.dumps(
            _plain(frozen),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except MemoryError:
        raise
    except Exception as exc:
        raise EvidenceError("value is not canonical JSON") from exc
    if len(encoded) != expected_size:
        raise EvidenceError("canonical JSON size accounting mismatch")
    return encoded


def canonical_json_digest(value: CanonicalJSONInput | CanonicalJSONValue) -> str:
    """Digest only the single canonical encoding of a schema-neutral value."""

    return "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
