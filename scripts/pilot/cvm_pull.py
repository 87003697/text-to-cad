#!/usr/bin/env python3
"""Publish terminal CVM pilot outputs to S3 and reclaim verified CVM data."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.pilot import provider_free_agent_surface_mcp_injection as mcp_injection
from scripts.pilot import provider_free_agent_surface_mcp_direct_injection as mcp_direct
from scripts.pilot import provider_free_agent_surface_mcp_ephemeral_differential as mcp_differential
from scripts.pilot import provider_free_installed_plugin as installed_plugin

S3_PREFIX = "s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
S3_REMOTE = "threed-code:arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
MATERIALIZED_ROOT = REPO_ROOT / "tmp/cvm-pull/outputs"
ARCHIVE_DIRNAME = ".cvm-pull-archives"
ARCHIVE_SCHEMA = "text-to-cad.cvm-pull-archive/1"
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
    provider_free_success: bool = False


@dataclass(frozen=True)
class PullPlan:
    """The complete read-only plan produced before the first S3 write."""

    publish: tuple[str, ...]
    preserve: tuple[ExpInspection, ...]
    handoffless_postmortems: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ArchiveReceipt:
    """Metadata for one experiment archive built on CVM."""

    handle: str
    size: int
    file_count: int
    key: str

    @classmethod
    def parse(cls, value: object) -> "ArchiveReceipt":
        if not isinstance(value, dict) or set(value) != {
            "schema", "handle", "size", "file_count", "key"
        }:
            raise PullError("Invalid archive receipt", 5)
        handle = value["handle"]
        size = value["size"]
        count = value["file_count"]
        key = value["key"]
        if (
            value["schema"] != ARCHIVE_SCHEMA
            or not isinstance(handle, str)
            or not is_safe_exp(handle)
            or type(size) is not int
            or size <= 0
            or type(count) is not int
            or count < 0
            or key != f"{handle.split('/', 1)[0]}/{ARCHIVE_DIRNAME}/{handle.split('/', 1)[1]}.tar.gz"
        ):
            raise PullError("Invalid archive receipt", 5)
        return cls(handle, size, count, key)


@dataclass(frozen=True)
class PublishResult:
    """Experiments durably published and experiments preserved on CVM."""

    uploaded: tuple[str, ...]
    preserved: tuple[ExpInspection, ...]
    retained_source: tuple[str, ...] = ()
    archives: tuple[ArchiveReceipt, ...] = ()


def is_safe_component(value: str) -> bool:
    """Return whether a path component is safe for remote interpolation."""

    return bool(COMPONENT.fullmatch(value)) and value not in {".", ".."}


def is_safe_exp(value: str) -> bool:
    """Return whether value is exactly one safe ``group/exp`` path."""

    parts = value.split("/")
    return len(parts) == 2 and all(is_safe_component(part) for part in parts)


def _provider_free_record(source: Path, manifest: object) -> dict[str, object] | None:
    if not isinstance(manifest, dict) or not isinstance(manifest.get("identity"), dict):
        return None
    identity = manifest["identity"]
    job = f"{source.parent.name}/{source.name}"
    if identity.get("job") != job or not isinstance(identity.get("authority"), dict):
        return None
    scenario = identity.get("scenario")
    if scenario not in {installed_plugin.SCENARIO, mcp_injection.SCENARIO, mcp_direct.SCENARIO, mcp_differential.SCENARIO}:
        return None
    return {
        "provider_free": True,
        "scenario": scenario,
        "object": scenario,
        "token_slot": None,
        "job": job,
        "exp_dir": f"outputs/{job}",
        "plugin_authority": identity["authority"],
    }


def is_valid_provider_free_success(source: Path, manifest: object) -> bool:
    """Recognize a closed validated provider-free success without Workspace authority."""

    record = _provider_free_record(source, manifest)
    if record is None:
        return False
    repo_root = source.parents[2]
    try:
        if record["scenario"] == mcp_injection.SCENARIO:
            mcp_injection.validate_artifacts(repo_root, record)
        elif record["scenario"] == mcp_direct.SCENARIO:
            mcp_direct.validate_artifacts(repo_root, record)
        elif record["scenario"] == mcp_differential.SCENARIO:
            mcp_differential.validate_artifacts(repo_root, record)
        else:
            installed_plugin.validate_artifacts(
                repo_root,
                record,
                verify_evidence_digest=False,
            )
    except (installed_plugin.ProviderFreeError, mcp_injection.ProviderFreeError, mcp_direct.ProviderFreeError, mcp_differential.ProviderFreeError):
        return False
    return True


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
    """Orchestrate Plan -> Qualify -> Publish -> Reclaim -> Expose."""

    def __init__(self, request: PullRequest, runner: CommandRunner) -> None:
        self.request = request
        self.runner = runner
        self.log_path = Path(
            os.environ.get("TMPDIR", "/tmp")
        ) / f"cvm-pull-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.excludes = self._load_excludes()

    def _require_rclone(self) -> None:
        result = self.runner.run(
            [
                "rclone",
                "version",
            ],
            check=False,
        )
        if result.returncode != 0:
            raise PullError("rclone is unavailable. Aborting.", 4)

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

    def discover_candidates(self) -> tuple[str, ...]:
        """Module 2: discover scoped CVM experiments."""

        self._require_rclone()
        return self._discover_cvm_exps()

    def _inspect_exp(self, exp: str) -> ExpInspection:
        script = """
import json
import pathlib
import stat
import sys
from scripts.pilot.cvm_pull import is_valid_provider_free_success

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
        "provider_free_success": False,
    }, separators=(",", ":")))
    raise SystemExit(0)
try:
    manifest = json.loads((exp / "artifact_manifest.json").read_text())
    value = manifest.get("final_status") if isinstance(manifest, dict) else None
except (OSError, json.JSONDecodeError):
    value = None
complete = type(value) is int
provider_free_success = is_valid_provider_free_success(exp, manifest) if complete else False
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
    "provider_free_success": provider_free_success,
}, separators=(",", ":")))
""".strip()
        command = " ".join(
            (
                "cd ~/text-to-cad && python3",
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
            provider_free_success=payload.get("provider_free_success") is True,
        )

    def qualify(
        self,
        candidates: tuple[str, ...],
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
                if (
                    self.request.postmortem_policy
                    is PostmortemPolicy.INCLUDE_RETAIN
                    and item.final_status != 0
                    and not item.has_terminal_handoff
                ) or (
                    item.provider_free_success
                    and item.final_status == 0
                    and not item.has_terminal_handoff
                ):
                    handoffless_postmortems.add(item.exp)
                publish.append(item.exp)
        return PullPlan(
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

    def _build_archive(self, exp: str, *, allow_missing_handoff: bool) -> ArchiveReceipt:
        """Build one archive on CVM and return its transfer metadata."""

        script = r'''
import fnmatch, gzip, io, json, os, pathlib, stat, sys, tarfile
from scripts.pilot.cvm_pull import is_valid_provider_free_success

exp, excludes_json, allow_missing_raw = sys.argv[1:]
excludes = json.loads(excludes_json)
allow_missing = allow_missing_raw == "1"
group, child = exp.split("/", 1)
root = pathlib.Path.home() / "text-to-cad/outputs"
source = root / group / child
manifest = json.loads((source / "artifact_manifest.json").read_text())
if type(manifest.get("final_status")) is not int:
    raise SystemExit(9)
handoff = root / group / ".internal-terminal-validation" / child / "terminal-validation.json"
if not handoff.is_file() and not allow_missing:
    raise SystemExit("terminal handoff missing")
if allow_missing and manifest["final_status"] == 0 and not is_valid_provider_free_success(source, manifest):
    raise SystemExit("successful experiment requires terminal handoff")
members = []
for directory, dirnames, filenames in os.walk(source, followlinks=False):
    directory_path = pathlib.Path(directory)
    dirnames[:] = sorted(name for name in dirnames if not (directory_path / name).is_symlink())
    for filename in sorted(filenames):
        path = directory_path / filename
        relative = path.relative_to(source).as_posix()
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        if relative == ".cvm-pull" or relative.startswith(".cvm-pull/"):
            raise SystemExit("reserved archive namespace")
        if relative != "run/terminal-validation-locator.json" and any(
            fnmatch.fnmatch(relative, pattern) for pattern in excludes
        ):
            continue
        members.append((relative, path, info.st_size))
if handoff.is_file():
    locator = json.loads((source / "run/terminal-validation-locator.json").read_text())
    if locator != {
        "schema": "mesh-to-cad.terminal-validation-locator/2",
        "handoff_layout": "external-sibling-namespace/1",
    }:
        raise SystemExit("terminal locator invalid")
    authority = json.loads(handoff.read_text())
    if (
        not isinstance(authority, dict)
        or set(authority) != {"schema", "terminal_identity_sha256", "bundle"}
        or authority.get("schema") != "mesh-to-cad.terminal-validation-handoff/1"
        or not isinstance(authority.get("terminal_identity_sha256"), str)
        or not isinstance(authority.get("bundle"), dict)
        or set(authority["bundle"]) != {"schema", "result", "manifest"}
        or authority["bundle"].get("schema")
           != "mesh-to-cad.terminal-validation-bundle/1"
        or not isinstance(authority["bundle"].get("result"), dict)
        or not isinstance(authority["bundle"].get("manifest"), dict)
    ):
        raise SystemExit("terminal handoff invalid")
    info = handoff.stat()
    members.append((".cvm-pull/terminal-validation.json", handoff, info.st_size))
members.sort(key=lambda item: item[0])
archive_manifest = json.dumps({
    "schema": "text-to-cad.cvm-pull-archive/1",
    "handle": exp,
    "members": [[name, size] for name, _path, size in members],
}, sort_keys=True, separators=(",", ":")).encode() + b"\n"
spool = pathlib.Path.home() / "text-to-cad/tmp/cvm-pull-archives" / group / child
spool.mkdir(parents=True, exist_ok=True)
archive_path = spool / "archive.tar.gz"
def info(name, size):
    value = tarfile.TarInfo(name)
    value.size = size; value.mode = 0o644; value.uid = value.gid = 0
    value.uname = value.gname = ""; value.mtime = 0
    return value
with archive_path.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, path, size in members:
                with path.open("rb") as payload:
                    archive.addfile(info(name, size), payload)
            archive.addfile(info(".cvm-pull/archive-manifest.json", len(archive_manifest)),
                            io.BytesIO(archive_manifest))
key = f"{group}/.cvm-pull-archives/{child}.tar.gz"
receipt = {
    "schema": "text-to-cad.cvm-pull-archive/1", "handle": exp,
    "size": archive_path.stat().st_size,
    "file_count": len(members), "key": key,
}
print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
'''.strip()
        command = " ".join((
            "cd ~/text-to-cad && python3", "-c", shlex.quote(script), shlex.quote(exp),
            shlex.quote(json.dumps(self.excludes)),
            "1" if allow_missing_handoff else "0",
        ))
        result = self.runner.remote(command, check=False)
        if result.returncode != 0:
            status = 9 if result.returncode == 9 else 5
            raise PullError(f"Cannot build CVM archive: {exp}", status)
        try:
            return ArchiveReceipt.parse(json.loads(result.stdout))
        except json.JSONDecodeError as error:
            raise PullError(f"Invalid CVM archive receipt: {exp}", 5) from error

    @staticmethod
    def _remote_archive_path(receipt: ArchiveReceipt) -> str:
        group, child = receipt.handle.split("/", 1)
        return f"~/text-to-cad/tmp/cvm-pull-archives/{group}/{child}/archive.tar.gz"

    def _upload_archive(self, receipt: ArchiveReceipt) -> None:
        command = (
            f"aws s3 cp {self._remote_archive_path(receipt)} "
            f"{S3_PREFIX}/{receipt.key} --no-progress"
        )
        status = self.runner.remote_tee(command, self.log_path)
        if status != 0:
            raise PullError(f"Archive upload failed: {receipt.handle}", 4)

    def _materialize_archive(self, receipt: ArchiveReceipt) -> Path:
        cache = REPO_ROOT / "tmp/cvm-pull/archives" / receipt.key
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise PullError(
                f"Cannot prepare archive cache: {receipt.handle}", 5
            ) from error
        result = self.runner.run(
            ["rclone", "copyto", f"{S3_REMOTE}/{receipt.key}", os.fspath(cache)],
            check=False,
        )
        if result.returncode != 0:
            raise PullError(f"Cannot download archive: {receipt.handle}", 4)
        staging: Path | None = None
        try:
            if cache.stat().st_size != receipt.size:
                raise PullError(
                    f"Downloaded archive size mismatch: {receipt.handle}", 5
                )
            group, child = receipt.handle.split("/", 1)
            group_root = MATERIALIZED_ROOT / group
            group_root.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=f".{child}.", dir=group_root)
            )
            with tarfile.open(cache, "r:gz") as archive:
                manifest_member = archive.getmember(".cvm-pull/archive-manifest.json")
                manifest_stream = archive.extractfile(manifest_member)
                if manifest_stream is None:
                    raise PullError(f"Archive manifest missing: {receipt.handle}", 5)
                manifest = json.loads(manifest_stream.read())
                expected = {name: size for name, size in manifest["members"]}
                if manifest.get("schema") != ARCHIVE_SCHEMA or manifest.get("handle") != receipt.handle:
                    raise PullError(f"Archive manifest mismatch: {receipt.handle}", 5)
                if len(expected) != receipt.file_count:
                    raise PullError(f"Archive member count mismatch: {receipt.handle}", 5)
                actual: dict[str, int] = {}
                for member in archive.getmembers():
                    if member.name == ".cvm-pull/archive-manifest.json":
                        continue
                    path = Path(member.name)
                    if not member.isfile() or path.is_absolute() or ".." in path.parts or member.name not in expected:
                        raise PullError(f"Unsafe archive member: {member.name}", 7)
                    payload = archive.extractfile(member)
                    if payload is None:
                        raise PullError(f"Unreadable archive member: {member.name}", 5)
                    output = staging / path
                    output.parent.mkdir(parents=True, exist_ok=True)
                    size = 0
                    with output.open("xb") as stream:
                        for block in iter(lambda: payload.read(1024 * 1024), b""):
                            stream.write(block)
                            size += len(block)
                    actual[member.name] = size
                if actual != expected:
                    raise PullError(f"Archive member mismatch: {receipt.handle}", 5)
            target = group_root / child
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
            handoff = target / ".cvm-pull/terminal-validation.json"
            if handoff.is_file():
                external = group_root / INTERNAL_TERMINAL_DIR / child / TERMINAL_HANDOFF_FILENAME
                external.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(handoff, external)
                handoff.unlink()
            controller = target / ".cvm-pull"
            if controller.exists():
                shutil.rmtree(controller)
            return target
        except PullError:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            raise
        except (OSError, tarfile.TarError, KeyError, TypeError, ValueError) as error:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            raise PullError(
                f"Cannot materialize archive: {receipt.handle}", 5
            ) from error

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
            raise PullError(f"CVM cleanup failed: {exp}", 4)

    def _cleanup_archive(self, receipt: ArchiveReceipt) -> None:
        result = self.runner.remote(
            f"rm -f -- {self._remote_archive_path(receipt)}",
            check=False,
        )
        if result.returncode != 0:
            raise PullError(
                f"CVM archive cleanup failed: {receipt.handle}",
                4,
            )

    def publish(self, plan: PullPlan) -> PublishResult:
        """Module 4: archive -> upload -> materialize -> cleanup."""

        uploaded: list[str] = []
        retained_source: list[str] = []
        archives: list[ArchiveReceipt] = []
        retain_source = (
            self.request.postmortem_policy is PostmortemPolicy.INCLUDE_RETAIN
        )
        for index, exp in enumerate(plan.publish, start=1):
            self._log(f"=== [{index}/{len(plan.publish)}] {exp} ===")
            handoffless = exp in plan.handoffless_postmortems
            receipt = self._build_archive(
                exp, allow_missing_handoff=handoffless
            )
            self._upload_archive(receipt)
            materialized = self._materialize_archive(receipt)
            self._cleanup_archive(receipt)
            archives.append(receipt)
            uploaded.append(exp)
            self._log(
                f"  archive materialized files={receipt.file_count} "
                f"bytes={receipt.size} path={materialized}"
            )
            if retain_source:
                self._log("  retaining CVM source")
                retained_source.append(exp)
            else:
                self._log("  cleaning CVM local...")
                self._cleanup_exp(exp)
        return PublishResult(
            uploaded=tuple(uploaded),
            preserved=plan.preserve,
            retained_source=tuple(retained_source),
            archives=tuple(archives),
        )

    def expose(self, result: PublishResult) -> None:
        """Module 5: prove every uploaded archive is materialized locally."""

        missing = [
            receipt.handle
            for receipt in result.archives
            if not (MATERIALIZED_ROOT / receipt.handle).is_dir()
        ]
        if missing:
            raise PullError(
                "Archive uploaded but local materialization is missing:\n"
                + "\n".join(f"  {handle}" for handle in missing),
                6,
            )

    def _log(self, message: str) -> None:
        print(message)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"{message}\n")

    def report(self, result: PublishResult) -> None:
        """Module 6: print the concise result and audit handoff."""

        if not result.uploaded:
            self._log("Done. No exp uploaded; preserved postmortem:")
        else:
            if result.uploaded:
                self._log("Done. Archive uploaded + materialized:")
                for exp in result.uploaded:
                    self._log(f"  {exp}")
            if result.retained_source:
                self._log("Retained CVM source:")
                for exp in result.retained_source:
                    self._log(f"  {exp}")
            else:
                self._log("Cleaned CVM source:")
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

    def run(self) -> None:
        """Compose the six modules into the complete pull workflow."""

        cvm_exps = self.discover_candidates()
        if not cvm_exps:
            print("No exp on CVM. Nothing to do.")
            return

        print("Planning scoped CVM experiments and checking terminal eligibility:")
        for exp in cvm_exps:
            print(f"  {exp}")
        plan = self.qualify(cvm_exps)
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
