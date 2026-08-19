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
from enum import Enum
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
S3_PREFIX = "s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
S3_REMOTE = "threed-code:arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
RCLONE_RC_ADDR = "127.0.0.1:5572"
MOUNT_PATH = (
    Path.home() / "threed-code/ericzyma/text-to-cad/outputs"
)
COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
DISPOSABLE_RUNTIME_EXCLUDES = (
    "run/playwright/*",
    "run/playwright-browsers/*",
)


class PullError(RuntimeError):
    """A user-facing pull failure with a stable process exit status."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


class PostmortemPolicy(Enum):
    """One validated publication and cleanup policy for terminal postmortems."""

    DEFAULT = "default"
    INCLUDE_CLEAN = "include-clean"
    INCLUDE_RETAIN = "include-retain"
    DISCARD_CLEAN = "discard-clean"


@dataclass(frozen=True)
class PullRequest:
    """Validated scope and postmortem policy supplied by the caller."""

    exp: str | None
    group: str | None
    postmortem_policy: PostmortemPolicy


@dataclass(frozen=True)
class ExpInspection:
    """Terminal and postmortem facts read from one immutable CVM exp."""

    exp: str
    complete: bool
    final_status: int | None
    has_postmortem: bool


@dataclass(frozen=True)
class PullPlan:
    """The complete read-only plan produced before the first S3 write."""

    cvm_exps: tuple[str, ...]
    candidates: tuple[str, ...]
    s3_exps: frozenset[str]
    publish: tuple[str, ...]
    preserve: tuple[ExpInspection, ...]


@dataclass(frozen=True)
class PublishResult:
    """Experiments durably published and experiments preserved on CVM."""

    uploaded: tuple[str, ...]
    preserved: tuple[ExpInspection, ...]
    verified_existing: tuple[str, ...] = ()
    retained_source: tuple[str, ...] = ()

    @property
    def mount_targets(self) -> tuple[str, ...]:
        """Return every verified S3 experiment that must be mount-visible."""

        return self.uploaded + self.verified_existing


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
            "[--include-byproducts [--retain-cvm-source] "
            "| --discard-postmortem]"
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--exp")
    scope.add_argument("--group")
    policy = parser.add_mutually_exclusive_group()
    policy.add_argument("--include-byproducts", action="store_true")
    policy.add_argument("--discard-postmortem", action="store_true")
    parser.add_argument("--retain-cvm-source", action="store_true")
    args = parser.parse_args(argv)
    if args.exp is not None and not is_safe_exp(args.exp):
        raise PullError(f"Unsafe --exp handle: {args.exp}", 7)
    if args.group is not None and not is_safe_component(args.group):
        raise PullError(f"Unsafe --group: {args.group}", 7)
    if args.retain_cvm_source and not args.include_byproducts:
        raise PullError(
            "--retain-cvm-source requires --include-byproducts",
            2,
        )
    if args.include_byproducts:
        postmortem_policy = (
            PostmortemPolicy.INCLUDE_RETAIN
            if args.retain_cvm_source
            else PostmortemPolicy.INCLUDE_CLEAN
        )
    elif args.discard_postmortem:
        postmortem_policy = PostmortemPolicy.DISCARD_CLEAN
    else:
        postmortem_policy = PostmortemPolicy.DEFAULT
    return PullRequest(
        exp=args.exp,
        group=args.group,
        postmortem_policy=postmortem_policy,
    )


class CommandRunner:
    """Execute local commands and the approved ``ssh -n cvm`` transport."""

    @staticmethod
    def run(
        argv: Sequence[str],
        *,
        check: bool = True,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one command without invoking a local shell."""

        return subprocess.run(
            list(argv),
            cwd=REPO_ROOT,
            check=check,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
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

    def __init__(self, request: PullRequest, runner: CommandRunner) -> None:
        self.request = request
        self.runner = runner
        self.mount_path = MOUNT_PATH
        self.log_path = Path(
            os.environ.get("TMPDIR", "/tmp")
        ) / f"cvm-pull-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.refresh_warning = False
        self.excludes = self._load_excludes()

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
import pathlib
import stat
import sys

root = pathlib.Path.home() / "text-to-cad/outputs"
group, child = sys.argv[1].split("/", 1)
exp = root / group / child
try:
    path_safe = all(
        stat.S_ISDIR(path.lstat().st_mode)
        for path in (root, root / group, exp)
    )
except OSError:
    path_safe = False
if not path_safe:
    print(json.dumps({
        "path_safe": False,
        "complete": False,
        "final_status": None,
        "has_postmortem": False,
    }, separators=(",", ":")))
    raise SystemExit(0)
try:
    manifest = json.loads((exp / "artifact_manifest.json").read_text())
    value = manifest.get("final_status") if isinstance(manifest, dict) else None
except (OSError, json.JSONDecodeError):
    value = None
complete = type(value) is int
print(json.dumps({
    "path_safe": True,
    "complete": complete,
    "final_status": value if complete else None,
    "has_postmortem": (exp / "run/.codex-upper").is_dir(),
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
        if payload.get("path_safe") is not True:
            raise PullError(f"Unsafe CVM exp path: {exp}", 7)
        return ExpInspection(
            exp=exp,
            complete=payload.get("complete") is True,
            final_status=(
                payload.get("final_status")
                if type(payload.get("final_status")) is int
                else None
            ),
            has_postmortem=payload.get("has_postmortem") is True,
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

        publish: list[str] = []
        preserve: list[ExpInspection] = []
        for item in inspections:
            should_preserve = (
                self.request.postmortem_policy is PostmortemPolicy.DEFAULT
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
        )

    def _load_excludes(self) -> tuple[str, ...]:
        if self.request.postmortem_policy in {
            PostmortemPolicy.INCLUDE_CLEAN,
            PostmortemPolicy.INCLUDE_RETAIN,
        }:
            return DISPOSABLE_RUNTIME_EXCLUDES
        path = REPO_ROOT / ".cvmignore.pull"
        if not path.is_file():
            return DISPOSABLE_RUNTIME_EXCLUDES
        configured = tuple(
            line
            for raw in path.read_text(encoding="utf-8").splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        )
        return tuple(dict.fromkeys((*DISPOSABLE_RUNTIME_EXCLUDES, *configured)))

    def _upload_exp(self, exp: str) -> None:
        exclude_args = " ".join(
            f"--exclude {shlex.quote(pattern)}" for pattern in self.excludes
        )
        command = (
            "aws s3 cp --recursive --no-follow-symlinks "
            f"~/text-to-cad/outputs/{exp}/ {S3_PREFIX}/{exp}/"
        )
        if exclude_args:
            command = f"{command} {exclude_args}"
        status = self.runner.remote_tee(command, self.log_path)
        if status not in {0, 2}:
            raise PullError(f"aws s3 cp fatal (exit={status}) — aborting", status)

    @staticmethod
    def _decode_file_list(stdout: str, label: str) -> tuple[str, ...]:
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise PullError(f"Invalid {label} file list", 5) from error
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            raise PullError(f"Invalid {label} file list", 5)
        return tuple(sorted(value))

    def _list_local_files(self, exp: str) -> tuple[str, ...]:
        script = """
import fnmatch
import json
import os
import pathlib
import stat
import sys

root = pathlib.Path.home() / "text-to-cad/outputs" / sys.argv[1]
patterns = json.loads(sys.argv[2])
files = []
for directory, _dirnames, filenames in os.walk(root, followlinks=False):
    parent = pathlib.Path(directory)
    for name in filenames:
        path = parent / name
        if not stat.S_ISREG(path.lstat().st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
            continue
        files.append(relative)
print(json.dumps(sorted(files)))
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
            raise PullError(f"Cannot list CVM files: {exp}", 5)
        return self._decode_file_list(result.stdout, "CVM")

    def _list_s3_files(self, exp: str) -> tuple[str, ...]:
        script = """
import fnmatch
import json
import subprocess
import sys

bucket = sys.argv[1]
prefix = sys.argv[2]
patterns = json.loads(sys.argv[3])
files = []
token = None
while True:
    command = [
        "aws", "s3api", "list-objects-v2",
        "--bucket", bucket,
        "--prefix", prefix,
        "--output", "json",
    ]
    if token is not None:
        command.extend(["--continuation-token", token])
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    payload = json.loads(completed.stdout)
    for item in payload.get("Contents", []):
        key = item.get("Key")
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        relative = key[len(prefix):]
        if not relative or any(
            fnmatch.fnmatch(relative, pattern) for pattern in patterns
        ):
            continue
        files.append(relative)
    if not payload.get("IsTruncated"):
        break
    token = payload.get("NextContinuationToken")
    if not isinstance(token, str) or not token:
        raise SystemExit("S3 listing omitted its continuation token")
print(json.dumps(sorted(files)))
""".strip()
        prefix = f"ericzyma/text-to-cad/outputs/{exp}/"
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote("arcwm-code-us-west-2"),
                shlex.quote(prefix),
                shlex.quote(json.dumps(self.excludes)),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(f"Cannot list S3 files: {exp}", 5)
        return self._decode_file_list(result.stdout, "S3")

    def _count_local_files(self, exp: str) -> int:
        return len(self._list_local_files(exp))

    def _count_s3_files(self, exp: str) -> int:
        return len(self._list_s3_files(exp))

    def _verify_exp(self, exp: str) -> int:
        local_files = self._list_local_files(exp)
        s3_files = self._list_s3_files(exp)
        if local_files != s3_files:
            missing = sorted(set(local_files) - set(s3_files))[:5]
            extra = sorted(set(s3_files) - set(local_files))[:5]
            raise PullError(
                "VERIFY FAILED "
                f"(local={len(local_files)} s3={len(s3_files)} "
                f"missing={missing} extra={extra}); "
                "keeping CVM local. Investigate.",
                5,
            )
        return len(local_files)

    def _existing_s3_is_complete(self, exp: str) -> tuple[bool, int, int]:
        """Compare an existing S3 prefix with its immutable CVM source."""

        local_files = self._list_local_files(exp)
        s3_files = self._list_s3_files(exp)
        return (
            local_files == s3_files,
            len(local_files),
            len(s3_files),
        )

    def _cleanup_exp(self, exp: str) -> None:
        if not is_safe_exp(exp):
            raise PullError(f"Refusing unsafe cleanup target: {exp}", 7)
        result = self.runner.remote(
            f"rm -rf -- ~/text-to-cad/outputs/{exp}",
            check=False,
        )
        if result.returncode != 0:
            raise PullError(f"CVM cleanup failed: {exp}", result.returncode)

    def publish(self, plan: PullPlan) -> PublishResult:
        """Module 4: run upload -> verify -> precise cleanup per exp."""

        uploaded: list[str] = []
        verified_existing: list[str] = []
        retained_source: list[str] = []
        retain_source = (
            self.request.postmortem_policy is PostmortemPolicy.INCLUDE_RETAIN
        )
        for index, exp in enumerate(plan.publish, start=1):
            self._log(f"=== [{index}/{len(plan.publish)}] {exp} ===")
            count: int
            if exp in plan.s3_exps:
                complete, local_count, s3_count = self._existing_s3_is_complete(exp)
                if complete:
                    count = local_count
                    verified_existing.append(exp)
                    self._log(
                        f"  existing S3 prefix verified ({count} files)"
                    )
                elif s3_count <= local_count:
                    self._log(
                        "  existing S3 prefix is incomplete "
                        f"(local={local_count} s3={s3_count}); retrying upload"
                    )
                    self._upload_exp(exp)
                    count = self._verify_exp(exp)
                    uploaded.append(exp)
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
                uploaded.append(exp)
            if retain_source:
                self._log(f"  verify OK ({count} files); retaining CVM source")
                retained_source.append(exp)
            else:
                self._log(f"  verify OK ({count} files); cleaning CVM local...")
                self._cleanup_exp(exp)
        return PublishResult(
            uploaded=tuple(uploaded),
            preserved=plan.preserve,
            verified_existing=tuple(verified_existing),
            retained_source=tuple(retained_source),
        )

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

        targets = result.mount_targets
        if not targets:
            return
        self._refresh_dir("ericzyma/text-to-cad/outputs")
        for group in sorted({exp.split("/", 1)[0] for exp in targets}):
            self._refresh_dir(f"ericzyma/text-to-cad/outputs/{group}")
        for exp in targets:
            self._refresh_dir(f"ericzyma/text-to-cad/outputs/{exp}")

        invisible: list[str] = []
        for exp in targets:
            for _attempt in range(5):
                if (self.mount_path / exp).is_dir():
                    break
                time.sleep(1)
            else:
                invisible.append(exp)
        if invisible:
            joined = "\n".join(f"  {exp}" for exp in invisible)
            source_state = (
                "CVM source retained"
                if result.retained_source
                else "CVM source cleaned"
            )
            raise PullError(
                f"S3 upload verified and {source_state}, "
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

        if not result.mount_targets:
            self._log("Done. No exp uploaded; preserved postmortem:")
        else:
            if result.uploaded:
                self._log("Done. Uploaded + verified + mount-visible:")
                for exp in result.uploaded:
                    self._log(f"  {exp}")
            if result.verified_existing:
                self._log(
                    "Existing S3 prefix verified + mount-visible:"
                )
                for exp in result.verified_existing:
                    self._log(f"  {exp}")
            if result.retained_source:
                self._log("Retained CVM source:")
                for exp in result.retained_source:
                    self._log(f"  {exp}")
            else:
                self._log("Cleaned CVM source:")
                for exp in result.mount_targets:
                    self._log(f"  {exp}")
        if result.preserved:
            if result.mount_targets:
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
