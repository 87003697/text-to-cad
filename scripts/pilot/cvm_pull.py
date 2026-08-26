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
    "*/run/playwright/*",
    "*/run/playwright-browsers/*",
    "work/playwright/*",
    "work/playwright-browsers/*",
    "*/work/playwright/*",
    "*/work/playwright-browsers/*",
    ".git/lfs/*",
    "*/.git/lfs/*",
)
TERMINAL_LOCATOR_RELATIVE = "run/terminal-validation-locator.json"
INTERNAL_TERMINAL_DIR = ".internal-terminal-validation"
TERMINAL_HANDOFF_FILENAME = "terminal-validation.json"


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
    has_terminal_handoff: bool = False


@dataclass(frozen=True)
class PullPlan:
    """The complete read-only plan produced before the first S3 write."""

    cvm_exps: tuple[str, ...]
    candidates: tuple[str, ...]
    s3_exps: frozenset[str]
    publish: tuple[str, ...]
    preserve: tuple[ExpInspection, ...]
    handoffless_postmortems: frozenset[str] = frozenset()


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
        values = tuple(
            sorted(
                line
                for line in raw.splitlines()
                if line
                and not (
                    len(line.split("/")) == 2
                    and line.split("/", 1)[1] == INTERNAL_TERMINAL_DIR
                )
            )
        )
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
                "! -name '.internal-terminal-validation' "
                f"-printf '{self.request.group}/%f\\n' 2>/dev/null"
            )
        else:
            command = (
                "find ~/text-to-cad/outputs/ -mindepth 2 -maxdepth 2 "
                "-type d ! -name '.internal-terminal-validation' "
                '-printf "%P\\n" 2>/dev/null'
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
        "has_terminal_handoff": False,
    }, separators=(",", ":")))
    raise SystemExit(0)
try:
    manifest = json.loads((exp / "artifact_manifest.json").read_text())
    value = manifest.get("final_status") if isinstance(manifest, dict) else None
except (OSError, json.JSONDecodeError):
    value = None
complete = type(value) is int
handoff = (
    root / group / ".internal-terminal-validation" / child
    / "terminal-validation.json"
)
try:
    handoff.lstat()
except FileNotFoundError:
    has_terminal_handoff = False
except OSError:
    raise SystemExit("cannot inspect terminal handoff")
else:
    has_terminal_handoff = True
print(json.dumps({
    "path_safe": True,
    "complete": complete,
    "final_status": value if complete else None,
    "has_postmortem": (exp / "run/.codex-home").is_dir(),
    "has_terminal_handoff": has_terminal_handoff,
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
            has_terminal_handoff=payload.get("has_terminal_handoff") is True,
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
        handoffless_postmortems: set[str] = set()
        for item in inspections:
            should_preserve = (
                self.request.postmortem_policy is PostmortemPolicy.DEFAULT
                and (item.final_status != 0 or item.has_postmortem)
            )
            if should_preserve:
                preserve.append(item)
            else:
                publish.append(item.exp)
                if item.final_status != 0 and not item.has_terminal_handoff:
                    handoffless_postmortems.add(item.exp)
        return PullPlan(
            cvm_exps=cvm_exps,
            candidates=candidates,
            s3_exps=s3_exps,
            publish=tuple(publish),
            preserve=tuple(preserve),
            handoffless_postmortems=frozenset(handoffless_postmortems),
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
        # The locator is a minimal Workspace-local discovery marker; it never
        # authenticates the terminal bundle but downstream review still
        # consults it to confirm the fixed external handoff layout.
        command = f"{command} --include {TERMINAL_LOCATOR_RELATIVE}"
        status = self.runner.remote_tee(command, self.log_path)
        if status not in {0, 2}:
            raise PullError(f"aws s3 cp fatal (exit={status}) — aborting", status)

    def _upload_exp_handoff(self, exp: str) -> None:
        """Publish the runner-owned external Terminal Validation Handoff.

        The handoff lives at the fixed sibling namespace
        ``<group>/.internal-terminal-validation/<child>`` alongside the exp
        tree.  It is the sole authentication lineage for the transferred
        bundle, so we transfer it as an independent step and let verification
        gate cleanup on the bytes and digest of the file it contains.
        """

        group, child = exp.split("/", 1)
        source = (
            f"~/text-to-cad/outputs/{group}/{INTERNAL_TERMINAL_DIR}/{child}/"
        )
        destination = f"{S3_PREFIX}/{group}/{INTERNAL_TERMINAL_DIR}/{child}/"
        command = (
            "aws s3 cp --recursive --no-follow-symlinks "
            f"{source} {destination}"
        )
        status = self.runner.remote_tee(command, self.log_path)
        if status not in {0, 2}:
            raise PullError(
                f"aws s3 cp fatal (exit={status}) — aborting", status
            )

    def _verify_exp_without_handoff(self, exp: str) -> int:
        """Verify an early failed experiment without terminal evidence."""

        expected, local, s3 = self._terminal_content_inventories(
            exp, allow_missing_handoff=True
        )
        local_mismatch = self._content_mismatch(expected, local)
        s3_mismatch = self._content_mismatch(expected, s3)
        if any(local_mismatch) or any(s3_mismatch):
            raise PullError(
                "VERIFY FAILED transfer content "
                f"(local={len(local)} s3={len(s3)} "
                f"local_missing={local_mismatch[0]} "
                f"local_extra={local_mismatch[1]} "
                f"local_changed={local_mismatch[2]} "
                f"s3_missing={s3_mismatch[0]} s3_extra={s3_mismatch[1]} "
                f"s3_changed={s3_mismatch[2]}); "
                "keeping CVM local. Investigate.",
                5,
            )
        return len(local)

    def _existing_s3_is_complete_without_handoff(
        self, exp: str
    ) -> tuple[bool, int, int]:
        """Compare a handoffless prefix with its immutable CVM source."""

        expected, local, s3 = self._terminal_content_inventories(
            exp, allow_missing_handoff=True
        )
        return (
            expected == local == s3,
            len(local),
            len(s3),
        )

    def _terminal_content_inventories(
        self, exp: str, *, allow_missing_handoff: bool = False
    ) -> tuple[
        dict[str, tuple[int, str]],
        dict[str, tuple[int, str]],
        dict[str, tuple[int, str]],
    ]:
        """Return expected, CVM, and S3 content inventories."""

        group, child = exp.split("/", 1)
        script = """
import fnmatch
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile

group, child, bucket, prefix, allow_missing, patterns_json = sys.argv[1:]
allow_missing_handoff = allow_missing == "1"
patterns = json.loads(patterns_json)
handoff_path = pathlib.Path.home() / (
    "text-to-cad/outputs/" + group + "/.internal-terminal-validation/"
    + child + "/terminal-validation.json"
)
try:
    info = handoff_path.lstat()
except FileNotFoundError:
    if not allow_missing_handoff:
        raise SystemExit("terminal handoff is not a regular file")
    handoff = None
else:
    if not stat.S_ISREG(info.st_mode):
        raise SystemExit("terminal handoff is not a regular file")
    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
    if (
        not isinstance(handoff, dict)
        or set(handoff) != {"schema", "terminal_identity_sha256", "bundle"}
        or handoff.get("schema") != "mesh-to-cad.terminal-validation-handoff/1"
        or not isinstance(handoff.get("bundle"), dict)
    ):
        raise SystemExit("terminal handoff schema is unsupported")

def identity(schema, value):
    body = (json.dumps(
        value, indent=2, sort_keys=True, separators=(",", ": ")
    ) + "\\n").encode("utf-8")
    return hashlib.sha256(schema.encode("utf-8") + b"\\0" + body).hexdigest()

expected = {}
if handoff is not None:
    bundle = handoff["bundle"]
    if (
        set(bundle) != {"schema", "result", "manifest"}
        or bundle.get("schema") != "mesh-to-cad.terminal-validation-bundle/1"
        or identity("mesh-to-cad.terminal-validation-handoff/1", bundle)
           != handoff.get("terminal_identity_sha256")
    ):
        raise SystemExit("terminal handoff identity mismatch")
    manifest = bundle["manifest"]
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {
            "schema", "workspace_id", "workspace_identity_sha256", "files",
            "identity_sha256",
        }
        or manifest.get("schema") != "mesh-to-cad.content-manifest/1"
        or not isinstance(manifest.get("files"), list)
    ):
        raise SystemExit("terminal content manifest is unsupported")
    manifest_body = dict(manifest)
    manifest_identity = manifest_body.pop("identity_sha256")
    if identity("mesh-to-cad.content-manifest/1", manifest_body) != manifest_identity:
        raise SystemExit("terminal content manifest identity mismatch")

    previous = ""
    for item in manifest["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size_bytes", "sha256"}:
            raise SystemExit("terminal content manifest entry is unsupported")
        name, size, digest = item["path"], item["size_bytes"], item["sha256"]
        pure = pathlib.PurePosixPath(name) if isinstance(name, str) else None
        if (
            pure is None or pure.is_absolute() or not pure.parts
            or any(part in {"", ".", ".."} for part in pure.parts)
            or name != pure.as_posix() or name <= previous
            or pure.parts[0] in {".git", "run", "work"}
            or not isinstance(size, int) or isinstance(size, bool) or size < 0
            or not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise SystemExit("terminal content manifest entry is invalid")
        expected[name] = [size, digest]
        previous = name

def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return [path.stat().st_size, digest.hexdigest()]

root = pathlib.Path.home() / "text-to-cad/outputs" / group / child
local = {}
for directory, dirnames, filenames in os.walk(root, followlinks=False):
    parent = pathlib.Path(directory)
    relative_parent = parent.relative_to(root)
    if handoff is not None and not relative_parent.parts:
        dirnames[:] = [name for name in dirnames if name not in {".git", "run", "work"}]
    for name in filenames:
        path = parent / name
        if not stat.S_ISREG(path.lstat().st_mode):
            continue
        relative = path.relative_to(root).as_posix()
        if handoff is None:
            if (
                relative != "run/terminal-validation-locator.json"
                and any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)
            ):
                continue
            local[relative] = file_digest(path)
        elif relative.split("/", 1)[0] not in {".git", "run", "work"}:
            local[relative] = file_digest(path)

if handoff is None:
    expected = dict(local)

keys = []
token = None
while True:
    command = [
        "aws", "s3api", "list-objects-v2", "--bucket", bucket,
        "--prefix", prefix, "--output", "json",
    ]
    if token is not None:
        command.extend(["--continuation-token", token])
    completed = subprocess.run(command, check=False, text=True,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        raise SystemExit(completed.returncode)
    page = json.loads(completed.stdout)
    for item in page.get("Contents", []):
        key = item.get("Key")
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        relative = key[len(prefix):]
        if not relative:
            continue
        if handoff is None:
            if (
                relative != "run/terminal-validation-locator.json"
                and any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)
            ):
                continue
            keys.append((relative, key))
        elif relative.split("/", 1)[0] not in {".git", "run", "work"}:
            keys.append((relative, key))
    if not page.get("IsTruncated"):
        break
    token = page.get("NextContinuationToken")
    if not isinstance(token, str) or not token:
        raise SystemExit("S3 listing omitted its continuation token")

s3 = {}
for relative, key in keys:
    with tempfile.NamedTemporaryFile() as target:
        completed = subprocess.run(
            ["aws", "s3api", "get-object", "--bucket", bucket,
             "--key", key, target.name],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        if completed.returncode != 0:
            sys.stderr.write(completed.stderr.decode("utf-8", errors="replace"))
            raise SystemExit(completed.returncode)
        s3[relative] = file_digest(pathlib.Path(target.name))
print(json.dumps({"expected": expected, "local": local, "s3": s3}, sort_keys=True))
""".strip()
        prefix = f"ericzyma/text-to-cad/outputs/{exp}/"
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote(group),
                shlex.quote(child),
                shlex.quote("arcwm-code-us-west-2"),
                shlex.quote(prefix),
                shlex.quote("1" if allow_missing_handoff else "0"),
                shlex.quote(json.dumps(self.excludes)),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(f"Cannot verify terminal content: {exp}", 5)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PullError(f"Invalid terminal content inventory: {exp}", 5) from error
        if not isinstance(payload, dict) or set(payload) != {"expected", "local", "s3"}:
            raise PullError(f"Invalid terminal content inventory: {exp}", 5)

        decoded: list[dict[str, tuple[int, str]]] = []
        for label in ("expected", "local", "s3"):
            value = payload[label]
            if not isinstance(value, dict):
                raise PullError(f"Invalid {label} content inventory: {exp}", 5)
            inventory: dict[str, tuple[int, str]] = {}
            for path, entry in value.items():
                if (
                    not isinstance(path, str)
                    or not isinstance(entry, list)
                    or len(entry) != 2
                    or not isinstance(entry[0], int)
                    or isinstance(entry[0], bool)
                    or entry[0] < 0
                    or not isinstance(entry[1], str)
                    or len(entry[1]) != 64
                ):
                    raise PullError(f"Invalid {label} content inventory: {exp}", 5)
                inventory[path] = (entry[0], entry[1])
            decoded.append(inventory)
        return decoded[0], decoded[1], decoded[2]

    @staticmethod
    def _content_mismatch(
        expected: dict[str, tuple[int, str]],
        actual: dict[str, tuple[int, str]],
    ) -> tuple[list[str], list[str], list[str]]:
        missing = sorted(set(expected) - set(actual))[:5]
        extra = sorted(set(actual) - set(expected))[:5]
        changed = sorted(
            path for path in set(expected) & set(actual) if expected[path] != actual[path]
        )[:5]
        return missing, extra, changed

    def _verify_exp(self, exp: str) -> int:
        expected, local, s3 = self._terminal_content_inventories(exp)
        local_mismatch = self._content_mismatch(expected, local)
        s3_mismatch = self._content_mismatch(expected, s3)
        if any(local_mismatch) or any(s3_mismatch):
            raise PullError(
                "VERIFY FAILED terminal content "
                f"(expected={len(expected)} local={len(local)} s3={len(s3)} "
                f"local_missing={local_mismatch[0]} local_extra={local_mismatch[1]} "
                f"local_changed={local_mismatch[2]} s3_missing={s3_mismatch[0]} "
                f"s3_extra={s3_mismatch[1]} s3_changed={s3_mismatch[2]}); "
                "keeping CVM local. Investigate.",
                5,
            )
        return len(expected)

    def _existing_s3_is_complete(self, exp: str) -> tuple[bool, int, int]:
        """Compare an existing S3 prefix with its immutable CVM source."""

        expected, local, s3 = self._terminal_content_inventories(exp)
        return (
            expected == local == s3,
            len(local),
            len(s3),
        )

    def _digest_local_handoff(self, exp: str) -> tuple[str, str, int]:
        """Return the local handoff SHA-256 identity + digest and file size."""

        group, child = exp.split("/", 1)
        script = """
import hashlib
import json
import pathlib
import stat
import sys

path = pathlib.Path.home() / (
    "text-to-cad/outputs/" + sys.argv[1] + "/.internal-terminal-validation/"
    + sys.argv[2] + "/terminal-validation.json"
)
info = path.lstat()
if not stat.S_ISREG(info.st_mode):
    raise SystemExit("terminal handoff is not a regular file")
data = path.read_bytes()
try:
    parsed = json.loads(data.decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"terminal handoff is not valid JSON: {exc}")
if (
    not isinstance(parsed, dict)
    or parsed.get("schema") != "mesh-to-cad.terminal-validation-handoff/1"
    or not isinstance(parsed.get("terminal_identity_sha256"), str)
    or len(parsed["terminal_identity_sha256"]) != 64
    or not isinstance(parsed.get("bundle"), dict)
):
    raise SystemExit("terminal handoff schema is unsupported")
print(json.dumps({
    "identity": parsed["terminal_identity_sha256"],
    "digest": hashlib.sha256(data).hexdigest(),
    "size": len(data),
}))
""".strip()
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote(group),
                shlex.quote(child),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(
                f"Cannot digest CVM terminal handoff: {exp}", 5
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PullError(
                f"Cannot decode CVM terminal handoff digest: {exp}", 5
            ) from error
        if not isinstance(payload, dict):
            raise PullError(
                f"Invalid CVM terminal handoff digest: {exp}", 5
            )
        identity = payload.get("identity")
        digest = payload.get("digest")
        size = payload.get("size")
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(size, int)
        ):
            raise PullError(
                f"Invalid CVM terminal handoff digest: {exp}", 5
            )
        return identity, digest, size

    def _digest_s3_handoff(self, exp: str) -> tuple[str, str, int]:
        """Return the transferred handoff SHA-256 identity + digest and size."""

        group, child = exp.split("/", 1)
        script = """
import hashlib
import json
import subprocess
import sys

bucket = sys.argv[1]
key = sys.argv[2]
process = subprocess.run(
    ["aws", "s3api", "get-object",
     "--bucket", bucket,
     "--key", key,
     "/dev/stdout"],
    check=False,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
if process.returncode != 0:
    sys.stderr.write(process.stderr.decode("utf-8", errors="replace"))
    raise SystemExit(process.returncode)
data = process.stdout
try:
    parsed = json.loads(data.decode("utf-8"))
except (UnicodeDecodeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"S3 terminal handoff is not valid JSON: {exc}")
if (
    not isinstance(parsed, dict)
    or parsed.get("schema") != "mesh-to-cad.terminal-validation-handoff/1"
    or not isinstance(parsed.get("terminal_identity_sha256"), str)
    or len(parsed["terminal_identity_sha256"]) != 64
    or not isinstance(parsed.get("bundle"), dict)
):
    raise SystemExit("S3 terminal handoff schema is unsupported")
print(json.dumps({
    "identity": parsed["terminal_identity_sha256"],
    "digest": hashlib.sha256(data).hexdigest(),
    "size": len(data),
}))
""".strip()
        key = (
            f"ericzyma/text-to-cad/outputs/{group}/"
            f"{INTERNAL_TERMINAL_DIR}/{child}/{TERMINAL_HANDOFF_FILENAME}"
        )
        command = " ".join(
            (
                "python3",
                "-c",
                shlex.quote(script),
                shlex.quote("arcwm-code-us-west-2"),
                shlex.quote(key),
            )
        )
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            raise PullError(
                f"Cannot digest S3 terminal handoff: {exp}", 5
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise PullError(
                f"Cannot decode S3 terminal handoff digest: {exp}", 5
            ) from error
        if not isinstance(payload, dict):
            raise PullError(
                f"Invalid S3 terminal handoff digest: {exp}", 5
            )
        identity = payload.get("identity")
        digest = payload.get("digest")
        size = payload.get("size")
        if (
            not isinstance(identity, str)
            or len(identity) != 64
            or not isinstance(digest, str)
            or len(digest) != 64
            or not isinstance(size, int)
        ):
            raise PullError(
                f"Invalid S3 terminal handoff digest: {exp}", 5
            )
        return identity, digest, size

    def _verify_exp_handoff(self, exp: str) -> tuple[str, str, int]:
        """Verify exact byte, digest, and identity parity of the handoff."""

        local_identity, local_digest, local_size = self._digest_local_handoff(exp)
        s3_identity, s3_digest, s3_size = self._digest_s3_handoff(exp)
        if (
            local_identity != s3_identity
            or local_digest != s3_digest
            or local_size != s3_size
        ):
            raise PullError(
                "VERIFY FAILED terminal handoff "
                f"(local_identity={local_identity[:12]} "
                f"s3_identity={s3_identity[:12]} "
                f"local_digest={local_digest[:12]} "
                f"s3_digest={s3_digest[:12]} "
                f"local_size={local_size} s3_size={s3_size}); "
                "keeping CVM local. Investigate.",
                5,
            )
        return local_identity, local_digest, local_size

    def _cleanup_exp(self, exp: str) -> None:
        if not is_safe_exp(exp):
            raise PullError(f"Refusing unsafe cleanup target: {exp}", 7)
        group, child = exp.split("/", 1)
        external_handoff = (
            f"~/text-to-cad/outputs/{group}/.internal-terminal-validation/{child}"
        )
        result = self.runner.remote(
            "rm -rf -- "
            f"~/text-to-cad/outputs/{exp} {external_handoff}",
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
            handoffless = exp in plan.handoffless_postmortems
            count: int
            if exp in plan.s3_exps:
                if handoffless:
                    complete, local_count, s3_count = (
                        self._existing_s3_is_complete_without_handoff(exp)
                    )
                else:
                    complete, local_count, s3_count = self._existing_s3_is_complete(
                        exp
                    )
                if complete:
                    count = local_count
                    verified_existing.append(exp)
                    self._log(
                        f"  existing S3 prefix verified ({count} files)"
                    )
                    if not handoffless:
                        identity, _digest, size = self._verify_exp_handoff(exp)
                        self._log(
                            f"  terminal handoff verified "
                            f"(identity={identity[:12]} size={size} bytes)"
                        )
                else:
                    raise PullError(
                        "VERIFY FAILED existing S3 terminal content "
                        f"(local={local_count} s3={s3_count}); "
                        "keeping CVM local. Investigate.",
                        5,
                    )
            else:
                self._upload_exp(exp)
                if handoffless:
                    count = self._verify_exp_without_handoff(exp)
                else:
                    self._upload_exp_handoff(exp)
                    count = self._verify_exp(exp)
                    identity, _digest, size = self._verify_exp_handoff(exp)
                    self._log(
                        f"  terminal handoff verified "
                        f"(identity={identity[:12]} size={size} bytes)"
                    )
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
