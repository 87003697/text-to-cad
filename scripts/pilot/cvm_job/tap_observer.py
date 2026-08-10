from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote


SUPPORTED_SCHEMA = 4
_CLASSIFIERS = (
    (
        "canonical_preparation",
        ("voxblame-prepare-reference", "canonical reference", "prepare-reference"),
    ),
    ("preview", ("voxblame-preview", "preview.png", "preview.json")),
    ("measurement", ("voxblame-measure", "measured step", "measurement.json")),
    (
        "repair",
        ("voxblame-region-diff", "region-diff", "repair target", "repair batch", "publish-cycle"),
    ),
    (
        "verification",
        ("voxblame-verify", "verification.json", "observable geometry verification"),
    ),
    (
        "final_rebuild",
        ("mesh-to-cad-workspace finalize", "final delivery", "canonical-build"),
    ),
    ("workspace", ("mesh-to-cad-workspace", "workspace.json", "step_index.json")),
    ("voxblame", ("voxblame-",)),
    ("review", ("pilot-review", "review.png", "review.gif", "reviews/")),
    ("export", ("snapshot", "export", ".glb", ".step", ".stp")),
    ("checkpoint", ("git commit",)),
    ("reconstruct", ("build123d", "freecad", "implicit-cad", "cadquery")),
    ("inspect", ("mesh-inspect", "mesh_stats", "trimesh", "file ")),
)


def _availability(value: str, **extra: Any) -> dict[str, Any]:
    return {"tap": {"availability": value, **extra}}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _safe_json(raw: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        raise ValueError("record payload is not JSON")
    return json.loads(raw)


def _walk(value: Any) -> Iterable[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _last_usage(payloads: Iterable[Any]) -> dict[str, int]:
    empty = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
    }
    latest = empty.copy()
    aliases = {
        "input_tokens": "input_tokens",
        "output_tokens": "output_tokens",
        "cached_input_tokens": "cache_read_tokens",
        "cache_read_input_tokens": "cache_read_tokens",
        "cache_creation_input_tokens": "cache_create_tokens",
        "cache_create_input_tokens": "cache_create_tokens",
    }
    for payload in payloads:
        for value in _walk(payload):
            if not isinstance(value, dict):
                continue
            if not any(key in value for key in aliases):
                continue
            candidate = empty.copy()
            for source, target in aliases.items():
                number = value.get(source)
                if isinstance(number, int) and number >= 0:
                    candidate[target] = number
            latest = candidate
    return latest


def _find_first(payloads: Iterable[Any], keys: tuple[str, ...]) -> Any:
    for payload in reversed(list(payloads)):
        for value in _walk(payload):
            if not isinstance(value, dict):
                continue
            for key in keys:
                if value.get(key) is not None:
                    return value[key]
    return None


def classify_activity(tool: str, arguments: Any) -> str:
    if isinstance(arguments, (dict, list)):
        rendered = json.dumps(arguments, sort_keys=True)
    else:
        rendered = str(arguments or "")
    evidence = f"{tool} {rendered}".lower()
    for classification, needles in _CLASSIFIERS:
        if any(needle in evidence for needle in needles):
            return classification
    return "other_tool"


def _function_evidence(payloads: list[Any]) -> dict[str, Any] | None:
    calls: list[tuple[str, str, Any]] = []
    outputs: set[str] = set()
    for payload in payloads:
        for value in _walk(payload):
            if not isinstance(value, dict):
                continue
            item_type = value.get("type")
            if item_type in {"function_call", "tool_call"}:
                call_id = str(value.get("call_id") or value.get("id") or "")
                name = str(value.get("name") or value.get("tool") or "unknown")
                calls.append((call_id, name, value.get("arguments") or value.get("input")))
            elif item_type in {"function_call_output", "tool_result"}:
                output_id = value.get("call_id") or value.get("tool_call_id")
                if output_id:
                    outputs.add(str(output_id))
    if not calls:
        return None
    call_id, tool, arguments = calls[-1]
    pending = bool(call_id) and call_id not in outputs
    return {
        "kind": "awaiting_tool_result" if pending else "tool_completed",
        "tool": tool[:80],
        "classification": classify_activity(tool, arguments),
        "source": "tap.function_call",
        "confidence": "high",
    }


def _load_blob(
    connection: sqlite3.Connection,
    session_id: str,
    record_index: int,
    marker: dict[str, Any],
) -> Any:
    if marker.get("type") != "compact-record-v1":
        if str(marker.get("type", "")).startswith("compact-record"):
            raise ValueError("unsupported compact record version")
        return marker
    columns = _columns(connection, "record_blobs")
    if not columns:
        raise ValueError("record blob table missing")
    payload_column = "payload_json" if "payload_json" in columns else "payload"
    if payload_column not in columns:
        raise ValueError("record blob payload column missing")
    if "blob_id" in columns and marker.get("blob_id") is not None:
        row = connection.execute(
            f"SELECT {payload_column} FROM record_blobs WHERE blob_id = ?",
            (marker["blob_id"],),
        ).fetchone()
    else:
        row = connection.execute(
            f"SELECT {payload_column} FROM record_blobs "
            "WHERE session_id = ? AND record_index = ? ORDER BY rowid DESC LIMIT 1",
            (session_id, record_index),
        ).fetchone()
    if row is None:
        raise ValueError("record blob missing")
    return _safe_json(row[0])


def observe(db_path: Path) -> dict[str, Any]:
    db_path = Path(db_path)
    if not db_path.is_file():
        return _availability("pending")
    uri = f"file:{quote(str(db_path.resolve()))}?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=0.05)
    except sqlite3.Error:
        return _availability("unavailable")
    try:
        connection.execute("PRAGMA query_only=ON")
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != SUPPORTED_SCHEMA:
            return _availability("unsupported", schema_version=version)
        session_columns = _columns(connection, "sessions")
        required = {"id", "status", "record_count"}
        if not required.issubset(session_columns):
            return _availability("degraded")
        timestamp_column = "updated_at" if "updated_at" in session_columns else "started_at"
        summary_column = "summary_json" if "summary_json" in session_columns else "NULL"
        session = connection.execute(
            f"SELECT id,status,record_count,{timestamp_column},{summary_column} "
            "FROM sessions ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if session is None:
            return _availability("pending")
        session_id, status, record_count, last_activity, summary_raw = session
        payloads: list[Any] = []
        if summary_raw:
            try:
                payloads.append(_safe_json(summary_raw))
            except (ValueError, json.JSONDecodeError):
                pass
        record_columns = _columns(connection, "records")
        if {"session_id", "record_index", "payload_json"}.issubset(record_columns):
            timestamp = "timestamp" if "timestamp" in record_columns else "NULL"
            rows = connection.execute(
                f"SELECT record_index,payload_json,{timestamp} FROM records "
                "WHERE session_id = ? ORDER BY record_index DESC LIMIT 2",
                (session_id,),
            ).fetchall()
            for record_index, raw, record_time in reversed(rows):
                payload = _safe_json(raw)
                if isinstance(payload, dict):
                    payload = _load_blob(connection, str(session_id), int(record_index), payload)
                payloads.append(payload)
                if record_time:
                    last_activity = record_time
        proxy_columns = _columns(connection, "proxy_logs")
        if {"session_id", "timestamp", "payload_json"}.issubset(proxy_columns):
            proxy_rows = connection.execute(
                "SELECT timestamp,payload_json FROM proxy_logs "
                "WHERE session_id = ? ORDER BY rowid DESC LIMIT 2",
                (session_id,),
            ).fetchall()
            for proxy_time, raw in reversed(proxy_rows):
                payloads.append(_safe_json(raw))
                if proxy_time:
                    last_activity = proxy_time
        tap = {
            "availability": "ready",
            "session_status": str(status),
            "turn_count": int(record_count or 0),
            "last_activity_at": last_activity,
        }
        last_usage = _last_usage(payloads)
        if any(last_usage.values()):
            tap["last_usage"] = last_usage
        model = _find_first(payloads, ("model",))
        api_status = _find_first(payloads, ("status_code", "http_status"))
        duration = _find_first(payloads, ("duration_ms",))
        if model is not None or api_status is not None or duration is not None:
            tap["last_api"] = {
                key: value
                for key, value in {
                    "model": str(model)[:80] if model is not None else None,
                    "status": api_status if isinstance(api_status, int) else None,
                    "duration_ms": duration if isinstance(duration, (int, float)) else None,
                }.items()
                if value is not None
            }
        result: dict[str, Any] = {"tap": tap}
        activity = _function_evidence(payloads)
        if activity:
            result["activity"] = activity
        return result
    except (sqlite3.Error, ValueError, TypeError, json.JSONDecodeError):
        return _availability("degraded")
    finally:
        connection.close()


def observe_exp(exp_dir: Path) -> dict[str, Any]:
    return observe(Path(exp_dir) / "run/traces.sqlite3")
