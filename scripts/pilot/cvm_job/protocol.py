from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import LEGACY_DEFAULT_MODEL, selector_for_model


SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"succeeded", "failed"})
_TRANSITIONS = {
    "submitted": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "succeeded": set(),
    "failed": set(),
}
PILOT_STATES = frozenset(_TRANSITIONS)
_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_RESERVED_UPDATE_FIELDS = frozenset(
    {
        "schema_version",
        "kind",
        "job",
        "group",
        "exp",
        "model",
        "plugin_mode",
        "view_image",
        "reconstruction_spec",
        "state",
        "submitted_at",
        "started_at",
        "updated_at",
        "heartbeat_at",
        "finished_at",
    }
)


class ProtocolError(ValueError):
    """Invalid job handle, schema, state, or transition."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_component(value: str, label: str = "component") -> str:
    if not isinstance(value, str) or not _COMPONENT.fullmatch(value):
        raise ProtocolError(f"unsafe {label}: {value!r}")
    if value in {".", ".."}:
        raise ProtocolError(f"unsafe {label}: {value!r}")
    return value


def parse_handle(handle: str) -> dict[str, str]:
    if not isinstance(handle, str):
        raise ProtocolError("job handle must be a string")
    parts = handle.split("/")
    if len(parts) == 2 and parts[0] != "batch":
        group = validate_component(parts[0], "group")
        exp = validate_component(parts[1], "exp")
        return {
            "kind": "pilot",
            "group": group,
            "exp": exp,
            "job": f"{group}/{exp}",
        }
    raise ProtocolError(f"invalid job handle: {handle!r}")


def default_state_root(repo_root: Path | None = None) -> Path:
    override = os.environ.get("CVM_JOB_STATE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    return repo_root / ".cvm-jobs"


def state_path(root: Path, handle: str) -> Path:
    parsed = parse_handle(handle)
    return root / "pilots" / parsed["group"] / f"{parsed['exp']}.json"


def log_path(root: Path, handle: str) -> Path:
    parsed = parse_handle(handle)
    return root / "logs" / parsed["group"] / f"{parsed['exp']}.log"


def _validate_common(state: dict[str, Any]) -> None:
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported job schema")
    parsed = parse_handle(state.get("job"))
    if parsed["kind"] != state.get("kind"):
        raise ProtocolError("job kind does not match handle")
    state_name = state.get("state")
    if state_name not in PILOT_STATES:
        raise ProtocolError(f"invalid state: {state_name!r}")
    if state.get("group") != parsed["group"]:
        raise ProtocolError("group does not match handle")
    if state.get("exp") != parsed["exp"]:
        raise ProtocolError("exp does not match handle")
    requested_plugin_mode(state)
    requested_view_image(state)
    requested_reconstruction_spec(state)
    if not state.get("provider_free"):
        requested_model(state)


def requested_plugin_mode(state: dict[str, Any]) -> str:
    """Return the requested pilot mode, defaulting pre-mode records to direct."""

    value = state.get("plugin_mode", "direct")
    if not isinstance(value, str) or value not in {"direct", "e2e"}:
        raise ProtocolError(f"invalid plugin mode: {value!r}")
    return value


def requested_view_image(state: dict[str, Any]) -> bool:
    """Return the requested view-image mode.

    Records from before this field existed are historical controls because
    those pilots were explicitly prohibited from calling ``view_image``.
    """

    value = state.get("view_image", False)
    if not isinstance(value, bool):
        raise ProtocolError(f"invalid view-image flag: {value!r}")
    return value


def requested_reconstruction_spec(state: dict[str, Any]) -> bool:
    """Return the requested pilot-only Reconstruction Spec mode.

    Records from before this field existed remain historical opt-outs.
    """

    value = state.get("reconstruction_spec", False)
    if not isinstance(value, bool):
        raise ProtocolError(f"invalid reconstruction spec flag: {value!r}")
    return value


def requested_model(state: dict[str, Any]) -> str:
    """Return the resolved model, preserving pre-model historical records."""

    value = state.get("model")
    if value is None:
        # Job records created before model receipts were introduced were all
        # GPT-5.6-sol pilots.  Keep those records runnable and historically
        # truthful while new records use the GPT-5.5 default.
        return LEGACY_DEFAULT_MODEL
    if not isinstance(value, str):
        raise ProtocolError(f"invalid resolved model: {value!r}")
    try:
        selector_for_model(value)
    except ValueError as error:
        raise ProtocolError(str(error)) from error
    return value


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ProtocolError("job state must be an object")
    _validate_common(state)
    return state


def _validate_updates(updates: dict[str, Any]) -> None:
    reserved = sorted(_RESERVED_UPDATE_FIELDS.intersection(updates))
    if reserved:
        raise ProtocolError(f"reserved update fields: {', '.join(reserved)}")


def _sync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    validate_state(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        _sync_directory(path.parent)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def load_state(root: Path, handle: str) -> dict[str, Any]:
    path = state_path(root, handle)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProtocolError(f"job not found: {handle}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ProtocolError(f"invalid job record: {handle}") from error
    return validate_state(payload)


def publish_state(root: Path, state: dict[str, Any]) -> None:
    path = state_path(root, state.get("job"))
    if path.exists():
        previous = load_state(root, state["job"])
        previous_has_view_image = "view_image" in previous
        current_has_view_image = "view_image" in state
        if previous_has_view_image != current_has_view_image:
            raise ProtocolError(
                f"view_image field presence is immutable: {state['job']}"
            )
        if (
            previous_has_view_image
            and previous["view_image"] != state["view_image"]
        ):
            raise ProtocolError(
                f"view_image is immutable: {state['job']}"
            )
        old = previous["state"]
        new = state["state"]
        if old in TERMINAL_STATES:
            if state != previous:
                raise ProtocolError(f"terminal job record is immutable: {state['job']}")
            return
        if new != old and new not in _TRANSITIONS[old]:
            raise ProtocolError(f"invalid transition: {old} -> {new}")
    atomic_write_json(path, state)


def transition(
    root: Path,
    handle: str,
    state_name: str,
    **updates: Any,
) -> dict[str, Any]:
    _validate_updates(updates)
    state = load_state(root, handle)
    previous = state["state"]
    if previous in TERMINAL_STATES and state_name == previous:
        return state
    if state_name != previous and state_name not in _TRANSITIONS[previous]:
        raise ProtocolError(f"invalid transition: {previous} -> {state_name}")
    now = utc_now()
    state.update(updates)
    state.update({"state": state_name, "updated_at": now})
    if state_name == "running" and not state.get("started_at"):
        state["started_at"] = now
    if state_name in TERMINAL_STATES:
        state["finished_at"] = now
        state["heartbeat_at"] = now
    publish_state(root, state)
    return state


def heartbeat(root: Path, handle: str, **updates: Any) -> dict[str, Any]:
    _validate_updates(updates)
    state = load_state(root, handle)
    if state["state"] in TERMINAL_STATES:
        return state
    now = utc_now()
    state.update(updates)
    state.update({"heartbeat_at": now, "updated_at": now})
    publish_state(root, state)
    return state


def heartbeat_age_seconds(state: dict[str, Any], now: datetime | None = None) -> int | None:
    raw = state.get("heartbeat_at") or state.get("updated_at")
    if not raw:
        return None
    try:
        instant = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return max(0, int((current - instant).total_seconds()))


def public_state(state: dict[str, Any], stale_after: float) -> dict[str, Any]:
    age = heartbeat_age_seconds(state)
    active = state["state"] not in TERMINAL_STATES
    health = "stale" if active and age is not None and age > stale_after else "ok"
    result = {
        "job": state["job"],
        "kind": state["kind"],
        "state": state["state"],
        "health": health,
        "heartbeat_age_seconds": age,
    }
    if state.get("token_slot") is not None:
        result["token_slot"] = state["token_slot"]
    if not state.get("provider_free"):
        result["model"] = requested_model(state)
        result["plugin_mode"] = requested_plugin_mode(state)
        result["view_image"] = requested_view_image(state)
        result["reconstruction_spec"] = requested_reconstruction_spec(state)
    if state["state"] in TERMINAL_STATES:
        result.update(
            {
                "process_exit_code": state.get("process_exit_code"),
                "runner_final_status": state.get("runner_final_status"),
                "artifact_manifest": state.get("artifact_manifest"),
            }
        )
        if state.get("failure_reason"):
            result["failure_reason"] = str(state["failure_reason"])[:160]
    return result
