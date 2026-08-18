#!/usr/bin/env python3
"""Provider-free two-job concurrency smoke test for ``BrowserRuntimeJob``.

Starts N per-job browser runtime containers in parallel, drives a real MCP
session in each (initialize -> navigate to a per-job data: URL ->
screenshot), and asserts isolation + clean teardown. No LLM calls.

Usage::

    python3 scripts/pilot/tests/browser_runtime_smoke.py \
        --jobs 2 --out /tmp/br-smoke-<ts>/
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_SRC = REPO_ROOT / "packages" / "browser_runtime" / "src"
if str(PACKAGE_SRC) not in sys.path:
    sys.path.insert(0, str(PACKAGE_SRC))

from browser_runtime.job import BrowserRuntimeJob  # noqa: E402

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MCP_INIT_MAX_ATTEMPTS = 30
MCP_INIT_RETRY_SLEEP_S = 1.0


# -- MCP helpers ------------------------------------------------------------


def _parse_mcp_body(raw: bytes, content_type: str) -> dict[str, Any]:
    """Return the JSON-RPC payload from an MCP HTTP response body.

    The Streamable HTTP MCP server may reply as ``application/json`` or as
    a single ``text/event-stream`` frame; accept both.
    """

    text = raw.decode("utf-8", errors="replace").strip()
    if "text/event-stream" in content_type:
        for line in text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise RuntimeError(f"no SSE data frame in body: {text!r}")
    if not text:
        return {}
    return json.loads(text)


def _mcp_post(
    url: str,
    port: int,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
    expect_response: bool = True,
    timeout_s: float = 30.0,
) -> tuple[dict[str, Any], dict[str, str]]:
    """POST a JSON-RPC payload to the MCP endpoint. Returns (body, headers)."""

    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Host": f"localhost:{port}",
    }
    if session_id is not None:
        headers["mcp-session-id"] = session_id
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        content_type = resp_headers.get("content-type", "")
    if not expect_response:
        return {}, resp_headers
    return _parse_mcp_body(raw, content_type), resp_headers


def _mcp_initialize(url: str, port: int) -> str:
    """Retry ``initialize`` until Chromium is ready; return the session id."""

    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "1.0"},
        },
    }
    last_err: Exception | None = None
    for _ in range(MCP_INIT_MAX_ATTEMPTS):
        try:
            _, headers = _mcp_post(url, port, payload, expect_response=True)
        except urllib.error.HTTPError as exc:
            last_err = RuntimeError(
                f"HTTP {exc.code} during initialize: {exc.read()!r}"
            )
            time.sleep(MCP_INIT_RETRY_SLEEP_S)
            continue
        except (urllib.error.URLError, ConnectionError, TimeoutError) as exc:
            last_err = exc
            time.sleep(MCP_INIT_RETRY_SLEEP_S)
            continue
        session_id = headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError(f"initialize returned no mcp-session-id: {headers!r}")
        return session_id
    raise RuntimeError(f"initialize never succeeded on {url}: {last_err!r}")


def _mcp_call_tool(
    url: str,
    port: int,
    session_id: str,
    call_id: int,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "jsonrpc": "2.0",
        "id": call_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    }
    body, _ = _mcp_post(url, port, payload, session_id=session_id)
    if "error" in body:
        raise RuntimeError(f"tools/call {name} failed: {body['error']!r}")
    result = body.get("result") or {}
    if result.get("isError"):
        raise RuntimeError(f"tools/call {name} returned isError: {result!r}")
    return result


def _extract_screenshot_png(result: dict[str, Any]) -> bytes:
    """Pull the base64 PNG payload out of a browser_take_screenshot result."""

    for item in result.get("content", []) or []:
        if item.get("type") == "image" and item.get("mimeType") == "image/png":
            return base64.b64decode(item["data"])
    raise RuntimeError(f"no image/png content in screenshot result: {result!r}")


def _drive_mcp_session(job: BrowserRuntimeJob, index: int, out_png: Path) -> None:
    url = job.mcp_url
    port = job.published_port
    session_id = _mcp_initialize(url, port)
    # Fire-and-forget notification (no id, no response body expected).
    _mcp_post(
        url,
        port,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session_id=session_id,
        expect_response=False,
    )
    nav_html = f"data:text/html,<h1>job-{index}</h1>"
    _mcp_call_tool(
        url,
        port,
        session_id,
        call_id=100,
        name="browser_navigate",
        arguments={"url": nav_html},
    )
    shot = _mcp_call_tool(
        url,
        port,
        session_id,
        call_id=101,
        name="browser_take_screenshot",
        arguments={"type": "png", "fullPage": False},
    )
    png = _extract_screenshot_png(shot)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    out_png.write_bytes(png)


# -- lifecycle helpers ------------------------------------------------------


def _parallel(fn, items: list[Any]) -> list[Exception | None]:
    errs: list[Exception | None] = [None] * len(items)

    def _worker(i: int, arg: Any) -> None:
        try:
            fn(arg)
        except Exception as exc:  # noqa: BLE001 - collect for later re-raise
            errs[i] = exc

    threads = [
        threading.Thread(target=_worker, args=(i, arg))
        for i, arg in enumerate(items)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errs


def _stop_all(jobs: list[BrowserRuntimeJob]) -> None:
    _parallel(lambda j: j.stop(), jobs)


def _residue_for(names: list[str], kind: str) -> list[str]:
    """Return whichever of ``names`` still show up in ``docker {kind} ls``.

    kind is ``"ps"`` for containers or ``"network"`` for networks.
    """

    if kind == "ps":
        argv = [
            "docker", "ps", "-a",
            "--filter", "name=ttc-br-",
            "--format", "{{.Names}}",
        ]
    elif kind == "network":
        argv = [
            "docker", "network", "ls",
            "--filter", "name=ttc-br-",
            "--format", "{{.Name}}",
        ]
    else:
        raise ValueError(kind)
    result = subprocess.run(argv, check=True, capture_output=True, text=True)
    present = set(result.stdout.split())
    return sorted(n for n in names if n in present)


def _make_job(exp_dir: Path) -> BrowserRuntimeJob:
    return BrowserRuntimeJob.create(exp_dir=exp_dir)


# -- phases -----------------------------------------------------------------


def _main_phase(out_root: Path, n_jobs: int) -> dict[str, Any]:
    jobs = [_make_job(out_root / f"job-{i}") for i in range(n_jobs)]

    start_errs = _parallel(lambda j: j.start(), jobs)
    for i, err in enumerate(start_errs):
        if err is not None:
            _stop_all(jobs)
            raise RuntimeError(f"job {i} failed to start: {err!r}") from err

    per_job: list[dict[str, Any]] = []
    per_job_lock = threading.Lock()

    def _session(i: int) -> None:
        t0 = time.monotonic()
        png_path = out_root / f"job-{i}" / "screenshot.png"
        _drive_mcp_session(jobs[i], i, png_path)
        digest = hashlib.sha256(png_path.read_bytes()).hexdigest()
        record = {
            "index": i,
            "container": jobs[i].container_name,
            "network": jobs[i].network_name,
            "port": jobs[i].published_port,
            "png_path": str(png_path),
            "png_sha256": digest,
            "png_bytes": png_path.stat().st_size,
            "wall_time_s": round(time.monotonic() - t0, 3),
        }
        with per_job_lock:
            per_job.append(record)

    session_errs = _parallel(_session, list(range(n_jobs)))
    for i, err in enumerate(session_errs):
        if err is not None:
            _stop_all(jobs)
            raise RuntimeError(f"job {i} MCP session failed: {err!r}") from err

    per_job.sort(key=lambda r: r["index"])

    # Isolation assertions (before teardown).
    ports = [r["port"] for r in per_job]
    if len(set(ports)) != n_jobs:
        raise AssertionError(f"published ports not distinct: {ports}")
    containers = [r["container"] for r in per_job]
    if len(set(containers)) != n_jobs:
        raise AssertionError(f"container names not distinct: {containers}")
    networks = [r["network"] for r in per_job]
    if len(set(networks)) != n_jobs:
        raise AssertionError(f"network names not distinct: {networks}")

    for r in per_job:
        blob = Path(r["png_path"]).read_bytes()
        if not blob:
            raise AssertionError(f"PNG is empty: {r['png_path']}")
        if not blob.startswith(PNG_MAGIC):
            raise AssertionError(
                f"PNG magic mismatch at {r['png_path']}: {blob[:8]!r}"
            )
    hashes = [r["png_sha256"] for r in per_job]
    if len(set(hashes)) != n_jobs:
        raise AssertionError(
            f"PNG sha256 collisions across jobs (should differ per URL): {hashes}"
        )

    _stop_all(jobs)

    residue_c = _residue_for(containers, "ps")
    residue_n = _residue_for(networks, "network")
    if residue_c or residue_n:
        raise AssertionError(
            f"cleanup leaked: containers={residue_c} networks={residue_n}"
        )

    return {
        "jobs": per_job,
        "image_ref": jobs[0].image_ref,
        "residue": {"containers": residue_c, "networks": residue_n},
        "cleanup_ok": True,
    }


def _kill_safety_phase(out_root: Path) -> dict[str, Any]:
    jobs = [_make_job(out_root / f"ksafety-job-{i}") for i in range(2)]
    start_errs = _parallel(lambda j: j.start(), jobs)
    for i, err in enumerate(start_errs):
        if err is not None:
            _stop_all(jobs)
            raise RuntimeError(f"kill-safety job {i} failed to start: {err!r}") from err

    subprocess.run(
        ["docker", "kill", jobs[0].container_name],
        check=True, capture_output=True, text=True,
    )
    # Give docker a beat to update container state.
    time.sleep(0.5)

    if not jobs[0].poll_failed():
        _stop_all(jobs)
        raise AssertionError("poll_failed() should be True for killed container")
    if jobs[1].poll_failed():
        _stop_all(jobs)
        raise AssertionError("poll_failed() should be False for healthy container")

    healthy_png = out_root / "ksafety-job-1" / "screenshot.png"
    _drive_mcp_session(jobs[1], 1, healthy_png)
    blob = healthy_png.read_bytes()
    healthy_ok = bool(blob) and blob.startswith(PNG_MAGIC)
    if not healthy_ok:
        _stop_all(jobs)
        raise AssertionError("healthy job could not complete MCP screenshot after peer kill")

    # stop() must be idempotent on the already-killed container.
    _stop_all(jobs)
    return {"passed": True, "healthy_job_screenshot_ok": healthy_ok}


# -- entrypoint -------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--skip-kill-safety", action="store_true")
    args = parser.parse_args(argv)

    out_root: Path = args.out
    out_root.mkdir(parents=True, exist_ok=True)

    total_t0 = time.monotonic()
    main_result = _main_phase(out_root, args.jobs)
    main_wall = round(time.monotonic() - total_t0, 3)

    kill_result: dict[str, Any] = {"passed": None, "skipped": True}
    kill_wall = 0.0
    if not args.skip_kill_safety:
        k_t0 = time.monotonic()
        kill_result = _kill_safety_phase(out_root)
        kill_wall = round(time.monotonic() - k_t0, 3)

    total_s = round(time.monotonic() - total_t0, 3)
    report = {
        "image_ref": main_result["image_ref"],
        "jobs": [
            {
                "container": r["container"],
                "network": r["network"],
                "port": r["port"],
                "png_sha256": r["png_sha256"],
                "png_bytes": r["png_bytes"],
                "wall_time_s": r["wall_time_s"],
            }
            for r in main_result["jobs"]
        ],
        "kill_safety": kill_result,
        "cleanup_ok": main_result["cleanup_ok"],
        "residue": main_result["residue"],
        "main_phase_wall_time_s": main_wall,
        "kill_safety_wall_time_s": kill_wall,
        "total_wall_time_s": total_s,
    }
    report_path = out_root / "smoke-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"OK jobs={args.jobs} main_phase_s={main_wall} total_s={total_s}")
    print(f"report: {report_path}")
    for r in main_result["jobs"]:
        print(
            f"  job {r['index']}: port={r['port']} "
            f"png_bytes={r['png_bytes']} "
            f"sha256={r['png_sha256'][:16]}... "
            f"wall={r['wall_time_s']}s"
        )
    if not args.skip_kill_safety:
        print(
            f"kill-safety: passed={kill_result['passed']} "
            f"healthy_screenshot_ok={kill_result['healthy_job_screenshot_ok']} "
            f"wall={kill_wall}s"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
