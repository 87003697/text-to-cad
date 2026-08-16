"""Run one review-only Codex engineering task in an unprivileged CVM copy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence

from scripts.pilot.venus_retry_proxy import RetryProxy


REPO_ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT = REPO_ROOT / ".cvm-agent-jobs"
SCRATCH_ROOT = Path("/tmp/text-to-cad-cvm-agent")
PROMPT_PATH = REPO_ROOT / "scripts/pilot/cvm_agent_surface_prompt.md"
MODEL = "gpt-5.6-sol"
TASKS = frozenset({"surface-adaptation"})
HANDLE = re.compile(r"^cvma-[0-9a-f]{24}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
MAX_SECONDS = 45 * 60
NOBODY_UID = 65534
NOBODY_GID = 65534
ALLOWED_CHANGED_PREFIXES = (
    "scripts/pilot/",
    "tests/python/global/",
    "docs/specs/",
)
SOURCE_INPUT_PREFIXES = (
    "docs/specs",
    "packages/meshshot",
    "scripts/pilot",
    "tests/python/global",
)
EXCLUDED_DIRS = frozenset(
    {
        ".agents",
        ".claude",
        ".codex",
        ".cvm-agent-jobs",
        ".cvm-jobs",
        ".git",
        ".mypy_cache",
        ".next",
        ".pytest_cache",
        ".venv",
        "__pycache__",
        "models",
        "node_modules",
        "outputs",
        "tmp",
    }
)


class AgentError(RuntimeError):
    """The bounded remote engineering transaction failed closed."""


def _sha256(path: Path) -> str:
    """Return one file digest without publishing its contents."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_digest() -> str:
    """Bind the task's complete readable source surface after deployment."""

    digest = hashlib.sha256()
    for prefix in SOURCE_INPUT_PREFIXES:
        root = REPO_ROOT / prefix
        if not root.is_dir() or root.is_symlink():
            raise AgentError("source input tree is unavailable")
        for path in sorted(root.rglob("*")):
            if "__pycache__" in path.parts or path.name == ".DS_Store":
                continue
            metadata = path.lstat()
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise AgentError("source input tree contains a special file")
            relative = path.relative_to(REPO_ROOT).as_posix().encode()
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            file_digest = bytes.fromhex(_sha256(path))
            digest.update(file_digest)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Publish one durable state or report file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _state_path(handle: str) -> Path:
    if not HANDLE.fullmatch(handle):
        raise AgentError("invalid CVM agent handle")
    return STATE_ROOT / f"{handle}.json"


def _load(handle: str) -> dict[str, Any]:
    try:
        value = json.loads(_state_path(handle).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError("CVM agent state is unavailable") from exc
    if not isinstance(value, dict) or value.get("handle") != handle:
        raise AgentError("CVM agent state is invalid")
    return value


def _transition(handle: str, state: str, **updates: Any) -> dict[str, Any]:
    value = _load(handle)
    previous = value.get("state")
    allowed = {
        "submitted": {"running", "failed"},
        "running": {"succeeded", "failed"},
        "succeeded": set(),
        "failed": set(),
    }
    if state != previous and state not in allowed.get(str(previous), set()):
        raise AgentError("invalid CVM agent transition")
    value.update(updates)
    value["state"] = state
    value["updatedAt"] = time.time()
    _atomic_json(_state_path(handle), value)
    return value


def _public(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema",
        "handle",
        "state",
        "task",
        "model",
        "sourceRevision",
        "sourceDigest",
        "group",
        "exp",
        "resultStatus",
        "processExitCode",
        "usage",
        "changedPaths",
        "errorCheck",
        "retryAllowed",
    )
    return {key: value.get(key) for key in keys}


def _verify_workflow(module_sha256: str, prompt_sha256: str) -> None:
    """Bind submission to the exact locally reviewed workflow bytes."""

    if not SHA256.fullmatch(module_sha256) or not SHA256.fullmatch(prompt_sha256):
        raise AgentError("invalid workflow digest")
    if _sha256(Path(__file__).resolve()) != module_sha256:
        raise AgentError("deployed module digest mismatch")
    if _sha256(PROMPT_PATH) != prompt_sha256:
        raise AgentError("deployed prompt digest mismatch")


def submit(
    task: str,
    source_revision: str,
    module_sha256: str,
    prompt_sha256: str,
    source_digest: str,
    *,
    detach: Callable[[Sequence[str]], int] | None = None,
) -> dict[str, Any]:
    """Allocate and detach exactly one remote Codex engineering attempt."""

    if task not in TASKS or not REVISION.fullmatch(source_revision):
        raise AgentError("invalid CVM agent submission")
    _verify_workflow(module_sha256, prompt_sha256)
    if not SHA256.fullmatch(source_digest) or _source_digest() != source_digest:
        raise AgentError("deployed source digest mismatch")
    handle = "cvma-" + secrets.token_hex(12)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    group = f"{stamp}-cvm-agent"
    exp = f"{stamp}-{task}-{handle.removeprefix('cvma-')}"
    now = time.time()
    value = {
        "schema": "text-to-cad.cvm-agent-job/1",
        "handle": handle,
        "state": "submitted",
        "task": task,
        "model": MODEL,
        "sourceRevision": source_revision,
        "sourceDigest": source_digest,
        "moduleSha256": module_sha256,
        "promptSha256": prompt_sha256,
        "group": group,
        "exp": exp,
        "submittedAt": now,
        "updatedAt": now,
        "resultStatus": None,
        "processExitCode": None,
        "usage": None,
        "changedPaths": [],
        "errorCheck": None,
        "retryAllowed": False,
    }
    _atomic_json(_state_path(handle), value)
    command = [sys.executable, "-m", "scripts.pilot.cvm_agent", "supervise", handle]
    try:
        if detach is None:
            log = STATE_ROOT / "logs" / f"{handle}.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("ab", buffering=0) as stream:
                subprocess.Popen(
                    command,
                    cwd=REPO_ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
        else:
            detach(command)
    except Exception:
        return _public(_transition(handle, "failed", errorCheck="supervisor-launch"))
    return _public(_load(handle))


def _copy_source(destination: Path) -> None:
    """Copy deployed source without private, state, artifact, or dependency trees."""

    destination.mkdir(parents=True, mode=0o700)
    for root, directories, files in os.walk(REPO_ROOT, followlinks=False):
        root_path = Path(root)
        relative_root = root_path.relative_to(REPO_ROOT)
        kept_directories: list[str] = []
        for name in sorted(directories):
            path = root_path / name
            if name in EXCLUDED_DIRS:
                continue
            if path.is_symlink():
                raise AgentError("deployed source contains a symlink")
            kept_directories.append(name)
        directories[:] = kept_directories
        target_root = destination / relative_root
        target_root.mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            source = root_path / name
            if source.is_symlink():
                raise AgentError("deployed source contains a symlink")
            shutil.copy2(source, target_root / name)


def _chown_tree(path: Path, uid: int, gid: int) -> None:
    for root, directories, files in os.walk(path):
        os.chown(root, uid, gid)
        for name in [*directories, *files]:
            os.chown(Path(root) / name, uid, gid, follow_symlinks=False)


def _response_schema(path: Path) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "diagnosis",
            "changed_paths",
            "tests",
            "risks",
            "review_request",
        ],
        "properties": {
            "summary": {"type": "string"},
            "diagnosis": {"type": "string"},
            "changed_paths": {"type": "array", "items": {"type": "string"}},
            "tests": {"type": "array", "items": {"type": "string"}},
            "risks": {"type": "array", "items": {"type": "string"}},
            "review_request": {"type": "string"},
        },
    }
    path.write_text(json.dumps(schema, separators=(",", ":")), encoding="utf-8")


def _codex_command(workspace: Path, last_message: Path, schema: Path) -> list[str]:
    codex = shutil.which("codex")
    if not codex:
        raise AgentError("codex executable is unavailable")
    return [
        codex,
        "-m", MODEL,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--approve-for-me",
        "--sandbox", "workspace-write",
        "--json",
        "--output-schema", os.fspath(schema),
        "--output-last-message", os.fspath(last_message),
        "--cd", os.fspath(workspace),
        "-",
    ]


def _require_closed_docker_socket(path: Path = Path("/var/run/docker.sock")) -> None:
    """Refuse a worker identity that could write the host Docker authority."""

    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AgentError("Docker socket access cannot be classified") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISSOCK(metadata.st_mode):
        raise AgentError("Docker authority path is unexpected")
    owner_writable = metadata.st_uid == NOBODY_UID and metadata.st_mode & stat.S_IWUSR
    group_writable = metadata.st_gid == NOBODY_GID and metadata.st_mode & stat.S_IWGRP
    world_writable = bool(metadata.st_mode & stat.S_IWOTH)
    if owner_writable or group_writable or world_writable:
        raise AgentError("Docker socket is exposed to the worker identity")


def _run_process_group(
    command: Sequence[str],
    *,
    cwd: Path,
    prompt: bytes,
    stream: Any,
    environment: dict[str, str],
) -> int:
    """Run and reap the complete unprivileged Codex process group."""

    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=stream,
        stderr=subprocess.STDOUT,
        env=environment,
        start_new_session=True,
        user=NOBODY_UID,
        group=NOBODY_GID,
        extra_groups=[],
    )
    try:
        process.communicate(input=prompt, timeout=MAX_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        raise
    return process.returncode


def _run_codex(
    workspace: Path,
    control: Path,
    events: Path,
    prompt: bytes,
) -> int:
    _require_closed_docker_socket()
    last_message = control / "last-message.json"
    schema = control / "response-schema.json"
    home = control / "home"
    codex_home = home / ".codex"
    home.mkdir()
    codex_home.mkdir()
    token = os.environ.get("VENUS_TOKEN")
    if not token:
        raise AgentError("Venus token is unavailable")
    _response_schema(schema)
    _chown_tree(control, NOBODY_UID, NOBODY_GID)
    _chown_tree(workspace, NOBODY_UID, NOBODY_GID)
    os.chown(workspace.parent, NOBODY_UID, NOBODY_GID)
    audit = control / "venus-proxy-audit.jsonl"
    client_token = secrets.token_urlsafe(32)
    with RetryProxy(
        "http://v2.open.venus.oa.com/llmproxy/v1",
        audit,
        upstream_bearer_token=token,
        required_client_bearer_token=client_token,
    ) as proxy:
        config = (
            'model_provider = "venus"\n'
            "[model_providers.venus]\n"
            'name = "Venus GPT-5.6-sol"\n'
            f"base_url = {json.dumps(proxy.url)}\n"
            'wire_api = "responses"\n'
            f"experimental_bearer_token = {json.dumps(client_token)}\n"
        )
        config_path = codex_home / "config.toml"
        config_path.write_text(config, encoding="utf-8")
        config_path.chmod(0o600)
        environment = {
            "HOME": os.fspath(home),
            "CODEX_HOME": os.fspath(codex_home),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        with events.open("wb") as stream:
            try:
                return _run_process_group(
                    _codex_command(workspace, last_message, schema),
                    cwd=workspace,
                    prompt=prompt,
                    stream=stream,
                    environment=environment,
                )
            finally:
                config_path.unlink(missing_ok=True)


def _usage(events: Path) -> dict[str, int] | None:
    latest: dict[str, int] | None = None
    try:
        lines = events.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        candidates: list[Any] = [value]
        while candidates:
            current = candidates.pop()
            if isinstance(current, dict):
                usage = current.get("usage")
                if isinstance(usage, dict):
                    normalized = {
                        str(key): item
                        for key, item in usage.items()
                        if isinstance(item, int) and not isinstance(item, bool)
                    }
                    if normalized:
                        latest = normalized
                candidates.extend(current.values())
            elif isinstance(current, list):
                candidates.extend(current)
    return latest


def _candidate_patch(baseline: Path, workspace: Path, destination: Path) -> list[str]:
    for path in workspace.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise AgentError("candidate workspace contains a special file")
    with destination.open("wb") as stream:
        completed = subprocess.run(
            [
                "git", "diff", "--no-index", "--binary", "--no-ext-diff",
                "--src-prefix=a/", "--dst-prefix=b/", os.fspath(baseline),
                os.fspath(workspace),
            ],
            stdout=stream,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    if completed.returncode not in {0, 1}:
        raise AgentError("candidate diff failed")
    if destination.stat().st_size > 16 * 1024 * 1024:
        raise AgentError("candidate patch exceeds the review bound")
    patch = destination.read_bytes()
    changed: set[str] = set()
    for raw in patch.splitlines():
        if not raw.startswith(b"+++ b/"):
            continue
        text = raw[len(b"+++ b/") :].decode("utf-8", errors="strict")
        marker = "/workspace/"
        changed.add(text.split(marker, 1)[-1] if marker in text else text)
    if any(not path.startswith(ALLOWED_CHANGED_PREFIXES) for path in changed):
        raise AgentError("candidate changed an unsafe path")
    return sorted(changed)


def _validate_last_message(path: Path) -> dict[str, Any]:
    """Require the exact parent-review response shape."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentError("structured Codex response is invalid") from exc
    keys = {
        "summary",
        "diagnosis",
        "changed_paths",
        "tests",
        "risks",
        "review_request",
    }
    if (
        not isinstance(value, dict)
        or set(value) != keys
        or not all(isinstance(value[name], str) for name in ("summary", "diagnosis", "review_request"))
        or not all(
            isinstance(value[name], list)
            and all(isinstance(item, str) for item in value[name])
            for name in ("changed_paths", "tests", "risks")
        )
    ):
        raise AgentError("structured Codex response is invalid")
    return value


def _artifact_manifest(exp_dir: Path, final_status: int) -> None:
    files = []
    for path in sorted(exp_dir.rglob("*")):
        if path.is_symlink():
            raise AgentError("agent output contains a symlink")
        if path.is_file() and path.name != "artifact_manifest.json":
            files.append(
                {
                    "path": path.relative_to(exp_dir).as_posix(),
                    "size_bytes": path.stat().st_size,
                }
            )
    _atomic_json(
        exp_dir / "artifact_manifest.json",
        {
            "schema_version": 1,
            "workload_status": final_status,
            "final_status": final_status,
            "files": files,
        },
    )


def supervise(
    handle: str,
    *,
    runner: Callable[[Path, Path, Path, bytes], int] = _run_codex,
) -> dict[str, Any]:
    _state_path(handle)
    claim = STATE_ROOT / "claims" / handle
    claim.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(claim, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise AgentError("CVM agent handle is already claimed") from exc
    else:
        os.close(descriptor)
    value = _load(handle)
    if value.get("state") != "submitted":
        raise AgentError("CVM agent handle is already claimed")
    _transition(handle, "running")
    private_scratch = SCRATCH_ROOT / f"{handle}.private"
    worker_scratch = SCRATCH_ROOT / f"{handle}.worker"
    baseline = private_scratch / "baseline"
    workspace = worker_scratch / "workspace"
    control = worker_scratch / "control"
    exp_dir = REPO_ROOT / "outputs" / value["group"] / value["exp"]
    run_dir = exp_dir / "run"
    final_status = 1
    error_check: str | None = None
    process_status: int | None = None
    changed: list[str] = []
    usage: dict[str, int] | None = None
    result_status: str | None = None
    try:
        private_scratch.mkdir(parents=True, mode=0o700)
        worker_scratch.mkdir(mode=0o700)
        control.mkdir(mode=0o700)
        exp_dir.mkdir(parents=True)
        run_dir.mkdir()
        _copy_source(baseline)
        shutil.copytree(baseline, workspace)
        prompt = PROMPT_PATH.read_bytes()
        (run_dir / "prompt.txt").write_bytes(prompt)
        events = control / "codex-events.jsonl"
        process_status = runner(workspace, control, events, prompt)
        shutil.copy2(events, run_dir / "codex-events.jsonl")
        last_message = control / "last-message.json"
        if not last_message.is_file():
            raise AgentError("structured Codex response is missing")
        response = _validate_last_message(last_message)
        shutil.copy2(last_message, run_dir / "last-message.json")
        usage = _usage(events)
        if usage is None:
            raise AgentError("Codex usage evidence is missing")
        changed = _candidate_patch(baseline, workspace, exp_dir / "candidate.patch")
        if sorted(response["changed_paths"]) != changed:
            raise AgentError("Codex changed-path claim does not match the patch")
        if process_status != 0:
            raise AgentError("Codex process failed")
        result_status = "proposed-change" if changed else "diagnosis-only"
        final_status = 0
    except subprocess.TimeoutExpired:
        error_check = "codex-timeout"
    except AgentError as exc:
        error_check = str(exc)
    except Exception:
        error_check = "agent-operation"
    report = {
        "schema": "text-to-cad.cvm-agent-report/1",
        "status": "succeeded" if final_status == 0 else "failed",
        "task": value["task"],
        "model": MODEL,
        "sourceRevision": value["sourceRevision"],
        "sourceDigest": value["sourceDigest"],
        "moduleSha256": value["moduleSha256"],
        "promptSha256": value["promptSha256"],
        "resultStatus": result_status,
        "processExitCode": process_status,
        "usage": usage,
        "changedPaths": changed,
        "errorCheck": error_check,
        "retryAllowed": False,
    }
    exp_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(exp_dir / "report.json", report)
    _artifact_manifest(exp_dir, final_status)
    state_name = "succeeded" if final_status == 0 else "failed"
    updated = _transition(
        handle,
        state_name,
        resultStatus=result_status,
        processExitCode=process_status,
        usage=usage,
        changedPaths=changed,
        errorCheck=error_check,
    )
    if final_status == 0:
        shutil.rmtree(private_scratch)
        shutil.rmtree(worker_scratch)
    return _public(updated)


def monitor(handle: str, *, wait: bool) -> tuple[dict[str, Any], int]:
    deadline = time.monotonic() + MAX_SECONDS + 60
    while True:
        value = _load(handle)
        if value.get("state") in {"succeeded", "failed"}:
            public = _public(value)
            public["output"] = f"{value['group']}/{value['exp']}"
            return public, 0 if value["state"] == "succeeded" else 1
        if not wait:
            return _public(value), 0
        if time.monotonic() >= deadline:
            result = _public(value)
            result["wait"] = "timeout"
            return result, 4
        time.sleep(2)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python3 -m scripts.pilot.cvm_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    submit_parser = subparsers.add_parser("submit")
    submit_parser.add_argument("task", choices=sorted(TASKS))
    submit_parser.add_argument("--source-revision", required=True)
    submit_parser.add_argument("--module-sha256", required=True)
    submit_parser.add_argument("--prompt-sha256", required=True)
    submit_parser.add_argument("--source-digest", required=True)
    subparsers.add_parser("source-digest")
    supervise_parser = subparsers.add_parser("supervise")
    supervise_parser.add_argument("handle")
    monitor_parser = subparsers.add_parser("monitor")
    monitor_parser.add_argument("--wait", action="store_true")
    monitor_parser.add_argument("handle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "source-digest":
            print(_source_digest())
            return 0
        if args.command == "submit":
            result = submit(
                args.task,
                args.source_revision,
                args.module_sha256,
                args.prompt_sha256,
                args.source_digest,
            )
            status = 0
        elif args.command == "supervise":
            result = supervise(args.handle)
            status = 0 if result["state"] == "succeeded" else 1
        else:
            result, status = monitor(args.handle, wait=args.wait)
    except AgentError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, separators=(",", ":")))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
