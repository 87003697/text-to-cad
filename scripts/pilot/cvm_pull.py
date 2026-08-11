#!/usr/bin/env python3
"""Publish terminal CVM pilot outputs to S3 and reclaim verified CVM data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
S3_PREFIX = "s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
S3_REMOTE = "threed-code:arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
RCLONE_RC_ADDR = "127.0.0.1:5572"
MOUNT_PATH = (
    Path.home() / "threed-code/ericzyma/text-to-cad/outputs"
)
WORKSPACE_AUTHORITY_HELPER = (
    REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-authority"
)
WORKSPACE_HELPER = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
AUTHORITY_TIMEOUT_GRACE_SECONDS = 5.0


class PullError(RuntimeError):
    """A user-facing pull failure with a stable process exit status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class PullRequest:
    """Validated scope and postmortem policy supplied by the caller."""

    exp: str | None
    group: str | None
    include_byproducts: bool
    discard_postmortem: bool
    authority_timeout_seconds: float = 120.0
    authority_max_files: int = 20_000
    authority_max_bytes: int = 5 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class AuthorityFile:
    """One exact authority artifact identity from the CVM terminal manifest."""

    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class AuthorityExpectation:
    """Exact authority identities that a mounted transfer must preserve."""

    exp: str
    files: tuple[AuthorityFile, ...]


@dataclass(frozen=True)
class ExpInspection:
    """Terminal and postmortem facts read from one immutable CVM exp."""

    exp: str
    complete: bool
    final_status: int | None
    has_postmortem: bool
    authority_complete: bool
    authority_classification: str
    authority_files: tuple[AuthorityFile, ...]


@dataclass(frozen=True)
class PullPlan:
    """The complete read-only plan produced before the first S3 write."""

    cvm_exps: tuple[str, ...]
    candidates: tuple[str, ...]
    s3_exps: frozenset[str]
    publish: tuple[str, ...]
    preserve: tuple[ExpInspection, ...]
    authority: tuple[AuthorityExpectation, ...]


@dataclass(frozen=True)
class PublishResult:
    """Experiments durably published and experiments preserved on CVM."""

    uploaded: tuple[str, ...]
    preserved: tuple[ExpInspection, ...]


def is_safe_component(value: str) -> bool:
    """Return whether a path component is safe for remote interpolation."""

    return bool(COMPONENT.fullmatch(value)) and value not in {".", ".."}


def is_safe_exp(value: str) -> bool:
    """Return whether value is exactly one safe ``group/exp`` path."""

    parts = value.split("/")
    return len(parts) == 2 and all(is_safe_component(part) for part in parts)


def parse_request(argv: Sequence[str]) -> PullRequest:
    """Module 1: parse arguments and reject unsafe or conflicting requests."""

    parser = argparse.ArgumentParser(
        prog="scripts/pilot/cvm-pull.sh",
        usage=(
            "%(prog)s [--exp <group>/<exp> | --group <group>] "
            "[--include-byproducts | --discard-postmortem] "
            "[--authority-timeout-seconds N --authority-max-files N "
            "--authority-max-bytes N]"
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--exp")
    scope.add_argument("--group")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--include-byproducts", action="store_true")
    policy.add_argument("--discard-postmortem", action="store_true")
    parser.add_argument("--authority-timeout-seconds", type=float, default=120.0)
    parser.add_argument("--authority-max-files", type=int, default=20_000)
    parser.add_argument(
        "--authority-max-bytes",
        type=int,
        default=5 * 1024 * 1024 * 1024,
    )
    args = parser.parse_args(argv)
    if args.exp is not None and not is_safe_exp(args.exp):
        raise PullError(f"Unsafe --exp handle: {args.exp}", 7)
    if args.group is not None and not is_safe_component(args.group):
        raise PullError(f"Unsafe --group: {args.group}", 7)
    if (
        args.authority_timeout_seconds <= 0
        or args.authority_max_files <= 0
        or args.authority_max_bytes <= 0
    ):
        raise PullError("Authority staging bounds must be positive", 7)
    return PullRequest(
        exp=args.exp,
        group=args.group,
        include_byproducts=args.include_byproducts,
        discard_postmortem=args.discard_postmortem,
        authority_timeout_seconds=args.authority_timeout_seconds,
        authority_max_files=args.authority_max_files,
        authority_max_bytes=args.authority_max_bytes,
    )


class CommandRunner:
    """Execute local commands and the approved ``ssh -n cvm`` transport."""

    @staticmethod
    def run(
        argv: Sequence[str],
        *,
        check: bool = True,
        capture: bool = True,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one command without invoking a local shell."""

        return subprocess.run(
            list(argv),
            cwd=REPO_ROOT,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=timeout,
        )

    def remote(
        self,
        command: str,
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one remote command without allowing SSH to consume loop input."""

        return self.run(["ssh", "-n", "cvm", command], check=check)

    def remote_tee(self, command: str, log_path: Path) -> int:
        """Run a remote command while streaming merged output to stdout and log."""

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                ["ssh", "-n", "cvm", command],
                cwd=REPO_ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="")
                log.write(line)
                log.flush()
            return process.wait()


class CvmPull:
    """Orchestrate Plan -> Qualify -> Publish -> Verify -> Reclaim -> Expose."""

    def __init__(
        self,
        request: PullRequest,
        runner: CommandRunner,
        *,
        authority_timeout: float | None = None,
        authority_max_files: int | None = None,
        authority_max_bytes: int | None = None,
    ) -> None:
        self.request = request
        self.runner = runner
        self.mount_path = MOUNT_PATH
        self.log_path = Path(
            os.environ.get("TMPDIR", "/tmp")
        ) / f"cvm-pull-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.refresh_warning = False
        self.excludes = self._load_excludes()
        self.authority_timeout = (
            request.authority_timeout_seconds
            if authority_timeout is None
            else authority_timeout
        )
        self.authority_max_files = (
            request.authority_max_files
            if authority_max_files is None
            else authority_max_files
        )
        self.authority_max_bytes = (
            request.authority_max_bytes
            if authority_max_bytes is None
            else authority_max_bytes
        )

    def _require_rclone(self) -> None:
        result = self.runner.run(
            [
                "rclone",
                "rc",
                f"--rc-addr={RCLONE_RC_ADDR}",
                "core/version",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise PullError("rclone RC endpoint: NOT reachable. Aborting.", 4)

    def _remote_directory_state(self, relative_path: str) -> bool:
        result = self.runner.remote(
            f"test -d ~/text-to-cad/{relative_path}",
            check=False,
        )
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        raise PullError(f"Cannot inspect CVM directory: {relative_path}", 4)

    @staticmethod
    def _validated_lines(raw: str) -> tuple[str, ...]:
        values = tuple(sorted(line for line in raw.splitlines() if line))
        unsafe = [value for value in values if not is_safe_exp(value)]
        if unsafe:
            raise PullError(f"Unsafe CVM exp path: {unsafe[0]}", 7)
        return values

    def _discover_cvm_exps(self) -> tuple[str, ...]:
        if self.request.exp is not None:
            if self._remote_directory_state(f"outputs/{self.request.exp}"):
                return (self.request.exp,)
            return ()
        if self.request.group is not None:
            command = (
                f"find ~/text-to-cad/outputs/{self.request.group}/ "
                "-mindepth 1 -maxdepth 1 -type d "
                f"-printf '{self.request.group}/%f\\n' 2>/dev/null"
            )
        else:
            command = (
                "find ~/text-to-cad/outputs/ -mindepth 2 -maxdepth 2 "
                '-type d -printf "%P\\n" 2>/dev/null'
            )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            scope = f" for group: {self.request.group}" if self.request.group else ""
            raise PullError(f"Cannot list CVM experiments{scope}", 4)
        return self._validated_lines(result.stdout)

    def _discover_s3_exps(self) -> frozenset[str]:
        result = self.runner.run(
            [
                "rclone",
                "lsf",
                S3_REMOTE,
                "--dirs-only",
                "--recursive",
                "--max-depth",
                "2",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise PullError(
                "Cannot list S3 output prefixes through rclone remote",
                4,
            )
        found: set[str] = set()
        for line in result.stdout.splitlines():
            parts = line.split("/")
            if len(parts) >= 3 and parts[0] and parts[1]:
                value = f"{parts[0]}/{parts[1]}"
                if is_safe_exp(value):
                    found.add(value)
        return frozenset(found)

    def discover_candidates(
        self,
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """Module 2: discover scoped CVM experiments and existing S3 prefixes."""

        self._require_rclone()
        cvm_exps = self._discover_cvm_exps()
        if not cvm_exps:
            return (), frozenset()
        s3_exps = self._discover_s3_exps()
        return cvm_exps, s3_exps

    def _inspect_exp(self, exp: str) -> ExpInspection:
        script = """
import json
import hashlib
import pathlib
import sys

exp = pathlib.Path.home() / "text-to-cad/outputs" / sys.argv[1]
try:
    manifest = json.loads((exp / "artifact_manifest.json").read_text())
    value = manifest.get("final_status") if isinstance(manifest, dict) else None
except (OSError, json.JSONDecodeError):
    manifest = None
    value = None
complete = type(value) is int
authority_classification = "valid"
authority_complete = True
authority_files = []
entries = {
    item.get("path"): item
    for item in (manifest.get("files", []) if isinstance(manifest, dict) else [])
    if isinstance(item, dict)
}
for name in ("workspace-authority.bundle", "workspace-authority.json"):
    path = exp / name
    item = entries.get(name)
    if not path.is_file() or not isinstance(item, dict):
        authority_complete = False
        authority_classification = "authority_missing"
        break
    try:
        data = path.read_bytes()
    except OSError:
        authority_complete = False
        authority_classification = "authority_partial"
        break
    if (
        item.get("size_bytes") != len(data)
        or item.get("sha256") != hashlib.sha256(data).hexdigest()
    ):
        authority_complete = False
        authority_classification = "authority_manifest_mismatch"
        break
    authority_files.append({
        "path": name,
        "size_bytes": item["size_bytes"],
        "sha256": item["sha256"],
    })
print(json.dumps({
    "complete": complete,
    "final_status": value if complete else None,
    "has_postmortem": (exp / "run/.codex-upper").is_dir(),
    "authority_complete": authority_complete,
    "authority_classification": authority_classification,
    "authority_files": authority_files,
}, separators=(",", ":")))
""".strip()
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote(exp),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(f"Cannot inspect CVM experiment: {exp}", 4)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PullError(f"Invalid CVM inspection result: {exp}", 4) from error
        return ExpInspection(
            exp=exp,
            complete=payload.get("complete") is True,
            final_status=(
                payload.get("final_status")
                if type(payload.get("final_status")) is int
                else None
            ),
            has_postmortem=payload.get("has_postmortem") is True,
            authority_complete=payload.get("authority_complete") is True,
            authority_classification=str(
                payload.get("authority_classification") or "authority_missing"
            ),
            authority_files=tuple(
                AuthorityFile(
                    path=str(item.get("path")),
                    size_bytes=int(item.get("size_bytes")),
                    sha256=str(item.get("sha256")),
                )
                for item in payload.get("authority_files", [])
                if isinstance(item, dict)
            ),
        )

    def qualify(
        self,
        cvm_exps: tuple[str, ...],
        candidates: tuple[str, ...],
        s3_exps: frozenset[str] = frozenset(),
    ) -> PullPlan:
        """Module 3: enforce terminal manifests and classify postmortems."""

        inspections = tuple(self._inspect_exp(exp) for exp in candidates)
        incomplete = tuple(item.exp for item in inspections if not item.complete)
        if incomplete:
            joined = "\n".join(f"  {exp}" for exp in incomplete)
            raise PullError(
                f"Incomplete CVM experiment(s); return to cvm-monitor:\n{joined}",
                9,
            )

        invalid_authority = tuple(
            item
            for item in inspections
            if not item.authority_complete
            and (
                self.request.include_byproducts
                or self.request.discard_postmortem
                or (item.final_status == 0 and not item.has_postmortem)
            )
        )
        if invalid_authority:
            joined = "\n".join(
                f"  {item.exp}: {item.authority_classification}"
                for item in invalid_authority
            )
            raise PullError(
                "CVM experiment authority is not auditable; cleanup blocked:\n"
                f"{joined}",
                10,
            )

        publish: list[str] = []
        preserve: list[ExpInspection] = []
        for item in inspections:
            should_preserve = (
                not self.request.include_byproducts
                and not self.request.discard_postmortem
                and (item.final_status != 0 or item.has_postmortem)
            )
            if should_preserve:
                preserve.append(item)
            else:
                publish.append(item.exp)
        return PullPlan(
            cvm_exps=cvm_exps,
            candidates=candidates,
            s3_exps=s3_exps,
            publish=tuple(publish),
            preserve=tuple(preserve),
            authority=tuple(
                AuthorityExpectation(item.exp, item.authority_files)
                for item in inspections
                if item.exp in publish
            ),
        )

    def _load_excludes(self) -> tuple[str, ...]:
        if self.request.include_byproducts:
            return ()
        path = REPO_ROOT / ".cvmignore.pull"
        if not path.is_file():
            return ()
        return tuple(
            line
            for raw in path.read_text(encoding="utf-8").splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        )

    def _upload_exp(self, exp: str) -> None:
        exclude_args = " ".join(
            f"--exclude {shlex.quote(pattern)}" for pattern in self.excludes
        )
        command = (
            "aws s3 cp --recursive "
            f"~/text-to-cad/outputs/{exp}/ {S3_PREFIX}/{exp}/"
        )
        if exclude_args:
            command = f"{command} {exclude_args}"
        status = self.runner.remote_tee(command, self.log_path)
        if status not in {0, 2}:
            raise PullError(f"aws s3 cp fatal (exit={status}) — aborting", status)

    def _count_local_files(self, exp: str) -> int:
        script = """
import fnmatch
import json
import pathlib
import sys

root = pathlib.Path.home() / "text-to-cad/outputs" / sys.argv[1]
patterns = json.loads(sys.argv[2])
count = 0
for path in root.rglob("*"):
    if not path.is_file():
        continue
    relative = path.relative_to(root).as_posix()
    if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
        continue
    count += 1
print(count)
""".strip()
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote(exp),
                shlex.quote(json.dumps(self.excludes)),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(f"Cannot count CVM files: {exp}", 5)
        try:
            return int(result.stdout.strip())
        except ValueError as error:
            raise PullError(f"Invalid CVM file count: {exp}", 5) from error

    def _count_s3_files(self, exp: str) -> int:
        pipeline = f"aws s3 ls --recursive {S3_PREFIX}/{exp}/ | wc -l"
        command = f"bash -o pipefail -c {shlex.quote(pipeline)}"
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(f"Cannot count S3 files: {exp}", 5)
        try:
            return int(result.stdout.strip())
        except ValueError as error:
            raise PullError(f"Invalid S3 file count: {exp}", 5) from error

    def _verify_exp(self, exp: str) -> int:
        local_count = self._count_local_files(exp)
        s3_count = self._count_s3_files(exp)
        if local_count != s3_count:
            raise PullError(
                "VERIFY FAILED "
                f"(local={local_count} s3={s3_count}); "
                "keeping CVM local. Investigate.",
                5,
            )
        return local_count

    def _existing_s3_is_complete(self, exp: str) -> tuple[bool, int, int]:
        """Compare an existing S3 prefix with its immutable CVM source."""

        local_count = self._count_local_files(exp)
        s3_count = self._count_s3_files(exp)
        return local_count == s3_count, local_count, s3_count

    def _cleanup_exp(self, exp: str) -> None:
        if not is_safe_exp(exp):
            raise PullError(f"Refusing unsafe cleanup target: {exp}", 7)
        result = self.runner.remote(
            f"rm -rf -- ~/text-to-cad/outputs/{exp}",
            check=False,
        )
        if result.returncode != 0:
            raise PullError(f"CVM cleanup failed: {exp}", result.returncode)

    def _verify_mount_authority(
        self,
        exp: str,
        expected: AuthorityExpectation,
    ) -> None:
        """Stage and audit the mount-visible copy before destructive cleanup."""

        group = exp.split("/", 1)[0]
        for directory in (
            "ericzyma/text-to-cad/outputs",
            f"ericzyma/text-to-cad/outputs/{group}",
            f"ericzyma/text-to-cad/outputs/{exp}",
        ):
            self._refresh_dir(directory)
        mounted = self.mount_path / exp
        expected_json = json.dumps(
            [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in expected.files
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        argv = [
            sys.executable,
            str(WORKSPACE_AUTHORITY_HELPER),
            "audit",
            "--source",
            str(mounted),
            "--workspace-helper",
            str(WORKSPACE_HELPER),
            "--timeout-seconds",
            str(self.authority_timeout),
            "--max-files",
            str(self.authority_max_files),
            "--max-bytes",
            str(self.authority_max_bytes),
            "--expected-authority-json",
            expected_json,
        ]
        try:
            completed = self.runner.run(
                argv,
                check=False,
                timeout=self.authority_timeout + AUTHORITY_TIMEOUT_GRACE_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise PullError(
                f"authority_timeout: {exp}: authority audit exceeded outer timeout; "
                "keeping CVM local",
                10,
            ) from exc
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise PullError(f"Invalid authority audit result: {exp}", 5) from exc
        if (
            completed.returncode != 0
            or not isinstance(payload, dict)
            or payload.get("ok") is not True
        ):
            authority = payload.get("authority") if isinstance(payload, dict) else None
            classification = (
                authority.get("classification")
                if isinstance(authority, dict)
                else "authority_invalid"
            )
            detail = (
                authority.get("detail")
                if isinstance(authority, dict)
                else "portable authority audit failed"
            )
            raise PullError(
                f"{classification}: {exp}: {detail}; keeping CVM local",
                10 if classification == "authority_timeout" else 5,
            )

    def publish(self, plan: PullPlan) -> PublishResult:
        """Module 4: run upload -> verify -> precise cleanup per exp."""

        uploaded: list[str] = []
        authority = {item.exp: item for item in plan.authority}
        for index, exp in enumerate(plan.publish, start=1):
            self._log(f"=== [{index}/{len(plan.publish)}] {exp} ===")
            count: int
            if exp in plan.s3_exps:
                complete, local_count, s3_count = self._existing_s3_is_complete(exp)
                if complete:
                    count = local_count
                    self._log(
                        f"  existing S3 prefix verified ({count} files); "
                        "resuming cleanup"
                    )
                elif s3_count <= local_count:
                    self._log(
                        "  existing S3 prefix is incomplete "
                        f"(local={local_count} s3={s3_count}); retrying upload"
                    )
                    self._upload_exp(exp)
                    count = self._verify_exp(exp)
                else:
                    raise PullError(
                        "VERIFY FAILED "
                        f"(local={local_count} s3={s3_count}); "
                        "S3 contains extra objects, keeping CVM local. Investigate.",
                        5,
                    )
            else:
                self._upload_exp(exp)
                count = self._verify_exp(exp)
            expected = authority.get(exp)
            if expected is None or len(expected.files) != 2:
                raise PullError(
                    f"authority_plan_incomplete: {exp}; keeping CVM local",
                    5,
                )
            self._verify_mount_authority(exp, expected)
            self._log(f"  verify OK ({count} files); cleaning CVM local...")
            self._cleanup_exp(exp)
            uploaded.append(exp)
        return PublishResult(tuple(uploaded), plan.preserve)

    def _refresh_dir(self, directory: str) -> None:
        result = self.runner.run(
            [
                "rclone",
                "rc",
                f"--rc-addr={RCLONE_RC_ADDR}",
                "vfs/refresh",
                f"dir={directory}",
                "recursive=false",
            ],
            check=False,
        )
        if result.returncode != 0:
            self.refresh_warning = True
            self._log(f"warning: rclone refresh failed: {directory}", error=True)

    def expose(self, result: PublishResult) -> None:
        """Module 5: refresh parent -> group -> exp and prove visibility."""

        if not result.uploaded:
            return
        self._refresh_dir("ericzyma/text-to-cad/outputs")
        for group in sorted({exp.split("/", 1)[0] for exp in result.uploaded}):
            self._refresh_dir(f"ericzyma/text-to-cad/outputs/{group}")
        for exp in result.uploaded:
            self._refresh_dir(f"ericzyma/text-to-cad/outputs/{exp}")

        invisible: list[str] = []
        for exp in result.uploaded:
            for _attempt in range(5):
                if (self.mount_path / exp).is_dir():
                    break
                time.sleep(1)
            else:
                invisible.append(exp)
        if invisible:
            joined = "\n".join(f"  {exp}" for exp in invisible)
            raise PullError(
                "S3 upload verified and CVM source cleaned, "
                f"but mount visibility is pending:\n{joined}",
                6,
            )

    def _log(self, message: str, *, error: bool = False) -> None:
        print(message, file=sys.stderr if error else sys.stdout)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")

    def report(self, result: PublishResult) -> None:
        """Module 6: print the concise result and audit handoff."""

        if not result.uploaded:
            self._log("Done. No exp uploaded; preserved postmortem:")
        else:
            self._log("Done. Uploaded + verified + cleaned + mount-visible:")
            for exp in result.uploaded:
                self._log(f"  {exp}")
        if result.preserved:
            if result.uploaded:
                self._log("Preserved postmortem on CVM:")
            for item in result.preserved:
                self._log(
                    f"  {item.exp} "
                    f"(final_status={item.final_status}, "
                    f"upper={int(item.has_postmortem)})"
                )
        if self.refresh_warning:
            self._log(
                "warning: one or more refresh calls failed, "
                "but mount visibility checks passed",
                error=True,
            )

    def run(self) -> None:
        """Compose the six modules into the complete pull workflow."""

        cvm_exps, s3_exps = self.discover_candidates()
        if not cvm_exps:
            print("No exp on CVM. Nothing to do.")
            return

        print("Planning scoped CVM experiments and checking terminal eligibility:")
        for exp in cvm_exps:
            print(f"  {exp}")
        plan = self.qualify(cvm_exps, cvm_exps, s3_exps)
        for item in plan.preserve:
            print(
                "  preserving CVM postmortem "
                f"(final_status={item.final_status}, "
                f"upper={int(item.has_postmortem)}); skipped"
            )
        print(f"Log: {self.log_path}")
        result = self.publish(plan)
        self.expose(result)
        self.report(result)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint returning the documented stable exit statuses."""

    try:
        request = parse_request(sys.argv[1:] if argv is None else argv)
        CvmPull(request, CommandRunner()).run()
    except PullError as error:
        print(error, file=sys.stderr)
        return error.status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
