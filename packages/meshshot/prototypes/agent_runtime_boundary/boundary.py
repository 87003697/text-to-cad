#!/usr/bin/env python3
"""THROWAWAY SAR-003 outer-authority contract and RED/GREEN model."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Iterable


SCHEMA = "meshshot.agent-boundary/1"
RECEIPT_SCHEMA = "meshshot.agent-boundary.prototype-matrix/1"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^[0-9a-f]{64}$")
JOB_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,47}$")
OWNER_LABEL = "io.text-to-cad.agent-boundary-owner"
JOB_LABEL = "io.text-to-cad.agent-boundary-job"


class BoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobPaths:
    source: Path
    input: Path
    control: Path
    home: Path
    cache: Path
    tmp: Path
    work: Path
    output: Path

    def private_writable_paths(self) -> tuple[Path, ...]:
        return (self.home, self.cache, self.tmp, self.work, self.output)


def require_digest(value: str, field: str) -> str:
    if not DIGEST_RE.fullmatch(value):
        raise BoundaryError(f"{field} must be one full sha256 digest")
    return value


def require_job(value: str) -> str:
    if not JOB_RE.fullmatch(value):
        raise BoundaryError("job_id must be bounded lowercase identity")
    return value


def require_resource_id(value: str) -> str:
    if not ID_RE.fullmatch(value):
        raise BoundaryError("container create must return one exact 64-hex ID")
    return value


def canonical_tree_digest(root: Path) -> str:
    """Hash a closed regular-file tree; links and special files fail closed."""
    root = root.resolve(strict=True)
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise BoundaryError(f"snapshot contains unsupported entry: {relative}")
        kind = b"d" if path.is_dir() else b"f"
        mode = stat.S_IMODE(path.lstat().st_mode)
        digest.update(kind + b"\0" + relative.encode("utf-8") + b"\0" + oct(mode).encode("ascii") + b"\0")
        if path.is_file():
            digest.update(str(path.stat().st_size).encode("ascii") + b"\0")
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _mount(source: Path, destination: str, readonly: bool) -> list[str]:
    spec = f"type=bind,src={source.resolve(strict=True)},dst={destination}"
    if readonly:
        spec += ",readonly"
    return ["--mount", spec]


def _volume(name: str, destination: str, readonly: bool) -> list[str]:
    spec = f"type=volume,src={name},dst={destination},volume-nocopy"
    if readonly:
        spec += ",readonly"
    return ["--mount", spec]


def build_create_argv(
    *,
    docker_host: str,
    image_digest: str,
    job_id: str,
    owner_nonce: str,
    name: str,
    broker_volume: str,
    paths: JobPaths,
) -> list[str]:
    """Build the only admitted inert Agent container configuration."""
    require_digest(image_digest, "image_digest")
    require_job(job_id)
    if not re.fullmatch(r"[0-9a-f]{32}", owner_nonce):
        raise BoundaryError("owner_nonce must be 128-bit lowercase hex")
    resource_stem = f"meshshot-agent-boundary-prototype-{job_id}-{owner_nonce[:12]}"
    if name != resource_stem:
        raise BoundaryError("container name must bind the job and owner nonce")
    if broker_volume != f"{resource_stem}-broker":
        raise BoundaryError("Broker capability volume must be job-private")
    all_paths = (
        paths.source, paths.input, paths.control, *paths.private_writable_paths(),
    )
    resolved_paths = [path.resolve(strict=True) for path in all_paths]
    job_root = resolved_paths[0].parent
    if job_root.name != resource_stem or any(path.parent != job_root for path in resolved_paths):
        raise BoundaryError("all job paths must be exact children of the job-private root")
    if job_root.stat().st_uid != os.getuid() or stat.S_IMODE(job_root.stat().st_mode) != 0o700:
        raise BoundaryError("job-private root must be caller-owned mode 0700")
    expected_names = ("source", "input", "control", "home", "cache", "tmp", "work", "output")
    if tuple(path.name for path in resolved_paths) != expected_names:
        raise BoundaryError("job paths must use the fixed public layout")
    writable = [path.resolve(strict=True) for path in paths.private_writable_paths()]
    if len(set(writable)) != len(writable):
        raise BoundaryError("job writable roots must be distinct")
    if any(left == right or left in right.parents or right in left.parents
           for index, left in enumerate(writable) for right in writable[index + 1:]):
        raise BoundaryError("job writable roots must not overlap")

    argv = [
        "docker", "--host", docker_host, "create", "--name", name,
        "--label", f"{OWNER_LABEL}={owner_nonce}",
        "--label", f"{JOB_LABEL}={job_id}",
        "--read-only", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "256",
        "--memory", "4g", "--memory-swap", "4g", "--cpus", "2",
        "--user", "1000:1000",
        "--env", "HOME=/run/agent-job/home",
        "--env", "CODEX_HOME=/run/agent-job/home/.codex",
        "--env", "XDG_CACHE_HOME=/run/agent-job/cache",
        "--env", "TMPDIR=/run/agent-job/tmp",
        "--env", "LANG=C.UTF-8", "--env", "LC_ALL=C.UTF-8", "--env", "TZ=UTC",
        "--env", "GIT_TERMINAL_PROMPT=0", "--env", "PYTHONDONTWRITEBYTECODE=1",
    ]
    argv += _mount(paths.source, "/run/agent-job/source", True)
    argv += _mount(paths.input, "/run/agent-job/input", True)
    argv += _mount(paths.control, "/run/agent-boundary", True)
    argv += _volume(broker_volume, "/run/meshshot-browser", True)
    for source, destination in zip(
        paths.private_writable_paths(),
        ("/run/agent-job/home", "/run/agent-job/cache", "/run/agent-job/tmp",
         "/run/agent-job/work", "/run/agent-job/output"),
        strict=True,
    ):
        argv += _mount(source, destination, False)
    argv += [image_digest]
    return argv


CASES = (
    "wrong_image_digest", "missing_image_digest",
    "wrong_source_digest", "missing_source_digest",
    "writable_root", "writable_source", "writable_input",
    "docker_socket_exposure", "extra_network_route",
    "shared_job_home", "shared_job_socket", "shared_job_output",
    "partial_startup_before_verification", "entrypoint_failure",
    "terminal_publication_failure", "cleanup_residue",
    "cross_job_authority_substitution",
)

PRECREATE_REJECTIONS = {
    "wrong_image_digest", "missing_image_digest",
    "wrong_source_digest", "missing_source_digest",
    "shared_job_home", "shared_job_socket", "shared_job_output",
}
INERT_REJECTIONS = {
    "writable_root", "writable_source", "writable_input",
    "docker_socket_exposure", "extra_network_route",
    "partial_startup_before_verification",
}
PREFLIGHT_REJECTIONS = {"entrypoint_failure", "cross_job_authority_substitution"}
TERMINAL_REJECTIONS = {"terminal_publication_failure"}


def legacy_red(case: str) -> dict[str, object]:
    """Unsafe comparison: host wrapper starts before it closes the boundary."""
    return {
        "workloadStarted": True,
        "terminalStatus": "incorrect-success" if case in {"terminal_publication_failure", "cleanup_residue"} else "late-failure",
        "absenceProved": case != "cleanup_residue",
    }


def proposed_green(case: str) -> dict[str, object]:
    events = ["admit-job"]
    workload_started = False
    if case in PRECREATE_REJECTIONS:
        events += ["reject-before-create", "prove-no-owned-resource"]
    else:
        events += ["create-inert", "verify-returned-id-owner-and-config"]
        if case in INERT_REJECTIONS:
            events += ["reject-before-start", "remove-exact-id", "prove-absence"]
        else:
            events += ["start-fixed-entrypoint", "await-preflight-proof"]
            if case in PREFLIGHT_REJECTIONS:
                events += ["withhold-workload-release", "remove-exact-id", "prove-absence"]
            else:
                events += ["accept-bound-preflight", "release-workload", "await-terminal-proof"]
                workload_started = True
                if case in TERMINAL_REJECTIONS:
                    events += ["terminal-publication-failed", "remove-exact-id", "prove-absence"]
                elif case == "cleanup_residue":
                    events += ["accept-terminal-proof", "cleanup-retained-resource"]
                else:
                    raise AssertionError(f"unexpected adversarial case: {case}")
    absence = case != "cleanup_residue"
    return {
        "workloadStarted": workload_started,
        "status": "failed",
        "absenceProved": absence,
        "retainedResource": case == "cleanup_residue",
        "events": events,
    }


def matrix() -> dict[str, object]:
    rows = []
    for case in CASES:
        red = legacy_red(case)
        green = proposed_green(case)
        expect_workload = case in TERMINAL_REJECTIONS or case == "cleanup_residue"
        passed = (
            red["workloadStarted"] is True
            and green["workloadStarted"] is expect_workload
            and green["status"] == "failed"
            and green["absenceProved"] is (case != "cleanup_residue")
            and green["retainedResource"] is (case == "cleanup_residue")
        )
        rows.append({"case": case, "red": red, "green": green, "pass": passed})
    return {
        "schema": RECEIPT_SCHEMA,
        "question": "Can the two-stage OCI seam preserve the formal Gate boundary?",
        "verdict": "ADOPT_WITH_FORMAL_VERIFICATION_GATES" if all(row["pass"] for row in rows) else "REJECT",
        "cases": rows,
        "passCount": sum(bool(row["pass"]) for row in rows),
        "caseCount": len(rows),
        "realOciRun": "NOT_RUN",
        "agentRuntimeVerified": False,
    }


def _main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("matrix",))
    args = parser.parse_args(argv)
    if args.command == "matrix":
        print(json.dumps(matrix(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
