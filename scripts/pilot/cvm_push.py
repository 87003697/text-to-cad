#!/usr/bin/env python3
"""Build, transfer, and verify a physical production deployment on CVM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from scripts.pilot import deployment_authority


REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = "~/text-to-cad"
REMOTE_DESTINATION = f"cvm:{REMOTE_ROOT}/"
IMPLICIT_NODE_MODULES_INCLUDE = (
    "/skills/implicit-cad/scripts/packages/implicitjs/node_modules/***"
)
NATIVE_MESHSCOPE_RUNTIME = (
    "skills/mesh-compare/scripts/packages/meshscope"
)
VIEWER_RUNTIME_IDENTITY = (
    "skills/cad-viewer/scripts/viewer/runtime-identity.json"
)
VIEWER_ARTIFACT_ROUTES = (
    (
        "launcher",
        "viewer/scripts/start-agent-viewer.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
    ),
    (
        "server",
        "viewer/src/server/server.mjs",
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
    ),
    (
        "client",
        "viewer/src/client/main.jsx",
        "skills/cad-viewer/scripts/viewer/dist/index.html",
    ),
)
VIEWER_SOURCE_TRANSFER_FILTERS = (
    "--include=/viewer/",
    "--include=/viewer/scripts/",
    "--include=/viewer/scripts/start-agent-viewer.mjs",
    "--include=/viewer/src/",
    "--include=/viewer/src/client/",
    "--include=/viewer/src/client/main.jsx",
    "--include=/viewer/src/server/",
    "--include=/viewer/src/server/server.mjs",
    "--exclude=/viewer/***",
)
PROVIDER_FREE_EXECUTION_FILES = (
    "scripts/pilot/cvm-submit.sh",
    "scripts/pilot/cvm_job/protocol.py",
    "scripts/pilot/cvm_job/runtime.py",
    "scripts/pilot/deployment_authority.py",
    "scripts/pilot/provider_free_runner.py",
    "scripts/pilot/provider_free_scenarios.py",
    "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/__main__.py",
    "skills/mesh-compare/scripts/mesh-compare/cli.py",
    "skills/cad/scripts/canonical-build/__main__.py",
    "models/simple/rectangular_clamp_block.py",
    "models/simple/simple_model_library.py",
)
RSYNC_SUMMARY_PATTERN = re.compile(
    r"sent ([\d,]+) bytes\s+received ([\d,]+) bytes\s+"
    r"([\d,]+(?:\.\d+)?) bytes/sec"
)

# Source -> staging is intentionally different from staging -> CVM. The
# staging copy keeps build-only inputs such as viewer/, packages/, and plugins/
# while excluding local state that must never become deployment material.
STAGE_SOURCE_EXCLUDES = (
    "/.git",
    "/.venv/",
    "/.agents/",
    "/.claude/",
    "/.codex/",
    "/.DS_Store",
    "/.cvm-jobs/",
    "/outputs/",
    "/models/",
    "/docs/",
    "/tmp/",
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    "*.swp",
    "*.tmp",
    "/viewer/dist/",
)
STAGE_BUILD_ONLY_INPUTS = (
    "docs/package.json",
    "docs/package-lock.json",
)

VIEWER_REQUIRED_EXECUTABLES = (
    ".bin/esbuild",
    ".bin/vite",
)
VIEWER_REQUIRED_PACKAGES = (
    "esbuild",
    "vite",
    "react",
    "react-dom",
    "@vitejs/plugin-react",
    "tailwindcss",
    "playwright",
    "playwright-core",
    "three",
    "gifenc",
)
CAD_REQUIRED_EXECUTABLES = ("node_modules/.bin/esbuild",)
CAD_REQUIRED_PACKAGE_VERSIONS = {
    "esbuild": "0.27.7",
    "three": "0.160.0",
    "gifenc": "1.0.3",
}


class PushError(RuntimeError):
    """A user-facing deployment failure with a stable process exit status."""

    def __init__(
        self,
        message: str,
        status: int,
        *,
        transferred: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.transferred = transferred


@dataclass(frozen=True)
class SourceProvenance:
    """Git identity of the dirty or clean checkout used as deployment source."""

    branch: str
    head: str
    state: str


@dataclass(frozen=True)
class RemotePreflight:
    """Remote facts collected before any expensive local staging work."""

    free_gb: int


@dataclass(frozen=True)
class BuildInputs:
    """Complete dependency trees copied into the isolated build stage."""

    viewer_node_modules: Path
    cad_build_dependencies: Path


@dataclass(frozen=True)
class RuntimeContract:
    """One production contract shared by local and remote validation."""

    physical_directories: tuple[str, ...]
    required_files: tuple[str, ...]
    hash_files: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeAttestation:
    """Local hashes and browser revision expected after remote transfer."""

    hashes: Mapping[str, str]
    chromium_revision: str
    viewer_identity: Mapping[str, object] | None = None


@dataclass(frozen=True)
class TransferSummary:
    """Compact rsync totals retained after detailed progress stays in the log."""

    sent_bytes: int | None = None
    received_bytes: int | None = None
    bytes_per_second: float | None = None


@dataclass(frozen=True)
class OutputReporter:
    """Keep manual and agent output policy behind one internal seam."""

    agent: bool

    @property
    def echo_stream(self) -> bool:
        return not self.agent

    def human(self, message: str, *, stderr: bool = False) -> None:
        if not self.agent:
            print(message, file=sys.stderr if stderr else sys.stdout)

    def phase(self, phase: str) -> None:
        if self.agent:
            print(
                json.dumps(
                    {
                        "schema": "cvm-push.event/1",
                        "type": "phase",
                        "phase": phase,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )

    def terminal_receipt(
        self,
        receipt: Mapping[str, object],
        *,
        written: bool,
    ) -> None:
        if self.agent:
            print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
        elif written:
            print(f"Receipt: {receipt['receipt_path']}")
        else:
            print(receipt["receipt_write_error"], file=sys.stderr)


PRODUCTION_RUNTIME = RuntimeContract(
    physical_directories=(
        "skills/cad-viewer/scripts/viewer",
        "skills/implicit-cad/scripts/packages/implicitjs",
        NATIVE_MESHSCOPE_RUNTIME,
    ),
    required_files=(
        *PROVIDER_FREE_EXECUTION_FILES,
        "skills/cad-viewer/scripts/viewer/package.json",
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
        "skills/cad-viewer/scripts/viewer/dist/index.html",
        VIEWER_RUNTIME_IDENTITY,
        "skills/implicit-cad/scripts/packages/implicitjs/scripts/snapshot.mjs",
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright/package.json"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright-core/package.json"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright-core/browsers.json"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/three/package.json"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/gifenc/package.json"
        ),
        f"{NATIVE_MESHSCOPE_RUNTIME}/setup.py",
        (
            f"{NATIVE_MESHSCOPE_RUNTIME}/src/meshscope/voxblame/"
            "_native.cpp"
        ),
    ),
    hash_files=(
        *PROVIDER_FREE_EXECUTION_FILES,
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
        "skills/cad-viewer/scripts/viewer/dist/index.html",
        VIEWER_RUNTIME_IDENTITY,
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "scripts/snapshot.mjs"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright-core/browsers.json"
        ),
        f"{NATIVE_MESHSCOPE_RUNTIME}/setup.py",
        (
            f"{NATIVE_MESHSCOPE_RUNTIME}/src/meshscope/voxblame/"
            "_native.cpp"
        ),
    ),
)


class CommandRunner:
    """Execute local argv and the approved ``ssh -n cvm`` transport."""

    @staticmethod
    def run(
        argv: Sequence[str],
        *,
        cwd: Path,
        check: bool = True,
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run one local command without invoking a shell."""

        return subprocess.run(
            list(argv),
            cwd=cwd,
            check=check,
            env=None if env is None else dict(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def remote(
        self,
        command: str,
        *,
        cwd: Path,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Run one remote command without allowing SSH to consume stdin."""

        return self.run(
            ["ssh", "-n", "cvm", command],
            cwd=cwd,
            check=check,
        )

    @staticmethod
    def stream(
        argv: Sequence[str],
        *,
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str] | None = None,
        echo: bool,
    ) -> int:
        """Stream merged output into a log, optionally mirroring it to stdout."""

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                env=None if env is None else dict(env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert process.stdout is not None
            for line in process.stdout:
                if echo:
                    print(line, end="")
                log.write(line)
                log.flush()
            return process.wait()


def _read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PushError(f"Cannot read {label}: {path}: {exc}", 4) from exc


def _package_path(root: Path, package: str) -> Path:
    return root.joinpath(*package.split("/"), "package.json")


def _package_version(path: Path) -> str | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    version = payload.get("version")
    return version if isinstance(version, str) else None


def viewer_dependency_errors(candidate: Path, repo_root: Path) -> tuple[str, ...]:
    """Return every reason a Viewer dependency candidate is incomplete."""

    errors: list[str] = []
    for relative in VIEWER_REQUIRED_EXECUTABLES:
        path = candidate / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            errors.append(f"missing executable {relative}")

    lock = _read_json(repo_root / "viewer/package-lock.json", "Viewer lockfile")
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise PushError("Viewer package-lock.json has no packages object", 4)

    for package in VIEWER_REQUIRED_PACKAGES:
        relative = f"node_modules/{package}"
        locked = packages.get(relative)
        expected = locked.get("version") if isinstance(locked, dict) else None
        path = _package_path(candidate, package)
        actual = _package_version(path)
        if not isinstance(expected, str):
            errors.append(f"lockfile has no version for {package}")
        elif actual is None:
            errors.append(f"missing package {package}")
        elif actual != expected:
            errors.append(
                f"{package} version {actual} does not match lockfile {expected}"
            )
    return tuple(errors)


def cad_dependency_errors(candidate: Path) -> tuple[str, ...]:
    """Return every reason a CAD snapshot dependency candidate is incomplete."""

    errors: list[str] = []
    for relative in CAD_REQUIRED_EXECUTABLES:
        path = candidate / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            errors.append(f"missing executable {relative}")
    for package, expected in CAD_REQUIRED_PACKAGE_VERSIONS.items():
        path = _package_path(candidate / "node_modules", package)
        actual = _package_version(path)
        if actual is None:
            errors.append(f"missing package {package}")
        elif actual != expected:
            errors.append(f"{package} version {actual} does not match {expected}")
    return tuple(errors)


class CvmPush:
    """Orchestrate Preflight -> Stage -> Attest -> Transfer -> Verify."""

    def __init__(
        self,
        runner: CommandRunner,
        *,
        repo_root: Path = REPO_ROOT,
        environ: Mapping[str, str] | None = None,
        agent: bool = False,
    ) -> None:
        self.runner = runner
        self.repo_root = repo_root.resolve()
        self.environ = dict(os.environ if environ is None else environ)
        self.output = OutputReporter(agent)
        self.run_id = (
            f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{time.time_ns()}"
        )
        output_root = Path(self.environ.get("TMPDIR", "/tmp"))
        self.log_path = output_root / f"cvm-push-{self.run_id}.log"
        self.receipt_path = output_root / f"cvm-push-{self.run_id}.receipt.json"
        self.phase = "not_started"
        self.source: SourceProvenance | None = None
        self.transfer_summary = TransferSummary()
        self.remote_head: str | None = None
        self.deployed_source_authority: Mapping[str, object] | None = None
        self.viewer_deployment: Mapping[str, object] | None = None

    def _log(self, message: str, *, stderr: bool = False) -> None:
        self.output.human(message, stderr=stderr)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log:
            print(message, file=log)

    def _log_best_effort(self, message: str, *, stderr: bool = False) -> str | None:
        try:
            self._log(message, stderr=stderr)
        except OSError as exc:
            return f"{type(exc).__name__}: {exc}"
        return None

    def _enter_phase(self, phase: str) -> None:
        self.phase = phase
        self.output.phase(phase)

    def preflight_local(self) -> None:
        """Gate 1a: require a repository source and local workflow tools."""

        if not (self.repo_root / "AGENTS.md").is_file():
            raise PushError("Not at repo root (AGENTS.md not found)", 1)
        for command in ("git", "node", "npm", "rsync", "ssh"):
            if shutil.which(command, path=self.environ.get("PATH")) is None:
                raise PushError(f"Required command not found: {command}", 1)
        if not (self.repo_root / ".cvmignore").is_file():
            raise PushError("Missing deployment filter: .cvmignore", 1)

    def preflight_remote(self) -> RemotePreflight:
        """Gate 1b: check target existence and disk space with one SSH."""

        result = self.runner.remote(
            (
                f"test -d {REMOTE_ROOT} || exit 2\n"
                'df --output=avail -BG / | tail -1 | tr -dc "0-9"'
            ),
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode == 2:
            raise PushError("CVM target ~/text-to-cad/ not found", 2)
        raw = result.stdout.strip()
        if result.returncode != 0 or not raw.isdigit():
            raise PushError("CVM preflight failed", 2)
        free_gb = int(raw)
        if free_gb < 3:
            raise PushError(
                f"CVM disk too full: {free_gb}G free, need ≥3G. Aborting.",
                3,
            )
        if free_gb < 10:
            self._log(
                f"WARN: CVM disk low: {free_gb}G free (threshold 10G).",
                stderr=True,
            )
        return RemotePreflight(free_gb=free_gb)

    def inspect_source(self) -> SourceProvenance:
        """Record deployment identity independently from remote Git HEAD."""

        def git(*args: str) -> subprocess.CompletedProcess[str]:
            return self.runner.run(
                ["git", *args],
                cwd=self.repo_root,
                check=False,
            )

        head_result = git("rev-parse", "HEAD")
        branch_result = git("symbolic-ref", "--quiet", "--short", "HEAD")
        status_result = git(
            "status",
            "--porcelain",
            "--untracked-files=normal",
        )
        return SourceProvenance(
            branch=(
                branch_result.stdout.strip()
                if branch_result.returncode == 0
                else "detached"
            ),
            head=(
                head_result.stdout.strip()
                if head_result.returncode == 0
                else "no-git"
            ),
            state="dirty" if status_result.stdout else "clean",
        )

    def _primary_checkout(self) -> Path | None:
        result = self.runner.run(
            [
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode != 0:
            return None
        raw = result.stdout.strip()
        if not raw:
            return None
        common_dir = Path(raw).resolve()
        return common_dir.parent if common_dir.name == ".git" else None

    def _explicit_path(self, name: str) -> Path | None:
        raw = self.environ.get(name)
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.repo_root / path
        return path.resolve()

    def _resolve_source(
        self,
        *,
        label: str,
        explicit_name: str,
        candidates: Sequence[Path],
        validate,
    ) -> Path:
        explicit = self._explicit_path(explicit_name)
        if explicit is not None:
            errors = validate(explicit)
            if errors:
                raise PushError(
                    f"Incomplete explicit {label} source {explicit}: "
                    + "; ".join(errors),
                    4,
                )
            return explicit

        seen: set[Path] = set()
        rejected: list[str] = []
        for candidate in candidates:
            resolved = candidate.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            errors = validate(resolved)
            if not errors:
                return resolved
            rejected.append(f"{resolved}: {'; '.join(errors)}")
        detail = "\n  ".join(rejected)
        raise PushError(
            f"Missing complete {label} source."
            + (f"\n  {detail}" if detail else ""),
            4,
        )

    def resolve_build_inputs(self) -> BuildInputs:
        """Gate 1c: select complete build-only dependency sources."""

        primary = self._primary_checkout()
        viewer_candidates = [self.repo_root / "viewer/node_modules"]
        cad_candidates = [self.repo_root / "tmp/cad-snapshot-build"]
        if primary is not None:
            viewer_candidates.append(primary / "viewer/node_modules")
            cad_candidates.append(primary / "tmp/cad-snapshot-build")
        return BuildInputs(
            viewer_node_modules=self._resolve_source(
                label="Viewer dependencies",
                explicit_name="CVM_PUSH_VIEWER_NODE_MODULES_SOURCE",
                candidates=viewer_candidates,
                validate=lambda path: viewer_dependency_errors(
                    path,
                    self.repo_root,
                ),
            ),
            cad_build_dependencies=self._resolve_source(
                label="CAD snapshot dependencies",
                explicit_name="CVM_PUSH_CAD_BUILD_DEPS_SOURCE",
                candidates=cad_candidates,
                validate=cad_dependency_errors,
            ),
        )

    @contextmanager
    def deployment_stage(self) -> Iterator[Path]:
        """Create and always clean one isolated deployment stage."""

        tmp_root = Path(self.environ.get("TMPDIR", "/tmp")).resolve()
        tmp_root.mkdir(parents=True, exist_ok=True)
        stage = Path(
            tempfile.mkdtemp(prefix="cvm-push-stage.", dir=tmp_root)
        ).resolve()
        try:
            if stage.parent != tmp_root or not stage.name.startswith(
                "cvm-push-stage."
            ):
                raise PushError(f"Unsafe CVM staging path: {stage}", 4)
            yield stage
        finally:
            if (
                stage.parent == tmp_root
                and stage.name.startswith("cvm-push-stage.")
            ):
                shutil.rmtree(stage, ignore_errors=False)

    def copy_source_to_stage(self, stage: Path) -> None:
        """Copy the dirty worktree while excluding local/private state."""

        argv = ["rsync", "-a"]
        for pattern in STAGE_SOURCE_EXCLUDES:
            argv.append(f"--exclude={pattern}")
        argv.extend([f"{self.repo_root}/", f"{stage}/"])
        result = self.runner.run(argv, cwd=self.repo_root, check=False)
        if result.returncode != 0:
            raise PushError(
                f"Cannot copy source into CVM stage: {result.stderr.strip()}",
                4,
            )
        for relative in STAGE_BUILD_ONLY_INPUTS:
            source = self.repo_root / relative
            if not source.is_file():
                raise PushError(f"Missing CVM stage build input: {relative}", 4)
        for relative in STAGE_BUILD_ONLY_INPUTS:
            source = self.repo_root / relative
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        for relative in (
            "models/simple/rectangular_clamp_block.py",
            "models/simple/simple_model_library.py",
        ):
            source = self.repo_root / relative
            destination = stage / relative
            if not source.is_file():
                raise PushError(f"Missing provider-free fixture: {relative}", 4)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    def copy_build_inputs(
        self,
        stage: Path,
        inputs: BuildInputs,
    ) -> None:
        """Copy dependency trees; never link a checkout cache into staging."""

        destinations = (
            (
                inputs.viewer_node_modules,
                stage / "viewer/node_modules",
                ("cadjs", "implicitjs"),
            ),
            (
                inputs.cad_build_dependencies,
                stage / "tmp/cad-snapshot-build",
                (),
            ),
        )
        for source, destination, excluded in destinations:
            destination.mkdir(parents=True, exist_ok=True)
            argv = ["rsync", "-a"]
            for name in excluded:
                argv.append(f"--exclude=/{name}")
            argv.extend([f"{source}/", f"{destination}/"])
            result = self.runner.run(
                argv,
                cwd=self.repo_root,
                check=False,
            )
            if result.returncode != 0:
                raise PushError(
                    f"Cannot copy build input {source}: "
                    f"{result.stderr.strip()}",
                    4,
                )
        viewer_modules = stage / "viewer/node_modules"
        for package in ("cadjs", "implicitjs"):
            link = viewer_modules / package
            if link.exists() or link.is_symlink():
                link.unlink()
            # These are stage-internal package links. They never point back to
            # the dependency source or primary checkout.
            os.symlink(f"../packages/{package}", link)

    def bundle_stage(self, stage: Path) -> None:
        """Materialize every production skill runtime inside staging."""

        env = {
            **self.environ,
            "IMPLICITJS_RUNTIME_NODE_MODULES_SOURCE": str(
                stage / "viewer/node_modules"
            ),
            "CAD_SNAPSHOT_BUILD_DEPS_DIR": str(
                stage / "tmp/cad-snapshot-build"
            ),
        }
        status = self.runner.stream(
            ["scripts/bundle/bundle.sh"],
            cwd=stage,
            log_path=self.log_path,
            env=env,
            echo=False,
        )
        if status != 0:
            raise PushError(
                f"Production bundle command failed with status {status}",
                4,
            )

    def materialize_skill_symlinks(self, stage: Path) -> None:
        """Replace stage-internal development skill links with physical copies."""

        stage_root = stage.resolve()
        previous_passes: set[tuple[str, ...]] = set()
        while links := self._skill_symlinks(stage):
            signature = tuple(str(link.relative_to(stage_root)) for link in links)
            if signature in previous_passes:
                raise PushError(
                    "CVM production stage skill symlink materialization "
                    "did not converge: "
                    f"{signature[0]}",
                    4,
                )
            previous_passes.add(signature)

            for link in links:
                if not link.is_symlink():
                    continue
                source = self._stage_link_target(link, stage_root)
                if self._contains(source, link):
                    raise PushError(
                        "CVM production stage has a cyclic skill symlink: "
                        f"{link.relative_to(stage_root)}",
                        4,
                    )

                temporary = link.with_name(f".{link.name}.cvm-materialize")
                if temporary.exists() or temporary.is_symlink():
                    raise PushError(
                        "CVM production stage has a materialization collision: "
                        f"{temporary.relative_to(stage_root)}",
                        4,
                    )
                try:
                    self._copy_stage_entry(
                        source,
                        temporary,
                        stage_root=stage_root,
                        active_sources=(),
                    )
                except PushError:
                    raise
                except (OSError, RuntimeError) as exc:
                    raise PushError(
                        "CVM production stage could not materialize skill symlink: "
                        f"{link.relative_to(stage_root)}",
                        4,
                    ) from exc
                link.unlink()
                temporary.rename(link)

    @staticmethod
    def _contains(parent: Path, child: Path) -> bool:
        try:
            child.relative_to(parent)
        except ValueError:
            return False
        return True

    @classmethod
    def _stage_link_target(cls, link: Path, stage_root: Path) -> Path:
        try:
            source = link.resolve(strict=True)
            source.relative_to(stage_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise PushError(
                "CVM production stage has an unsafe skill symlink: "
                f"{link.relative_to(stage_root)}",
                4,
            ) from exc
        return source

    @classmethod
    def _copy_stage_entry(
        cls,
        source: Path,
        destination: Path,
        *,
        stage_root: Path,
        active_sources: tuple[Path, ...],
    ) -> None:
        original = source
        if source.is_symlink():
            collision = source.with_name(f".{source.name}.cvm-materialize")
            if collision.exists() or collision.is_symlink():
                raise PushError(
                    "CVM production stage has a materialization collision: "
                    f"{collision.relative_to(stage_root)}",
                    4,
                )
            source = cls._stage_link_target(source, stage_root)
            if cls._contains(source, original):
                raise PushError(
                    "CVM production stage has a cyclic skill symlink: "
                    f"{original.relative_to(stage_root)}",
                    4,
                )
        else:
            try:
                source = source.resolve(strict=True)
                source.relative_to(stage_root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise PushError(
                    "CVM production stage has an unsafe skill source: "
                    f"{original.relative_to(stage_root)}",
                    4,
                ) from exc

        if source in active_sources or cls._contains(source, destination):
            raise PushError(
                "CVM production stage has a cyclic skill symlink: "
                f"{original.relative_to(stage_root)}",
                4,
            )
        if source.is_dir():
            destination.mkdir()
            active_source_ancestors = (*active_sources, source)
            for child in sorted(source.iterdir(), key=lambda path: path.name):
                cls._copy_stage_entry(
                    child,
                    destination / child.name,
                    stage_root=stage_root,
                    active_sources=active_source_ancestors,
                )
            shutil.copystat(source, destination, follow_symlinks=False)
        elif source.is_file():
            shutil.copy2(source, destination, follow_symlinks=False)
        else:
            raise PushError(
                "CVM production stage skill symlink has unsupported target: "
                f"{original.relative_to(stage_root)}",
                4,
            )

    @staticmethod
    def _skill_symlinks(stage: Path) -> tuple[Path, ...]:
        links: list[Path] = []
        stage_root = stage.resolve()
        skill_roots = (
            stage_root / "skills",
            stage_root / "plugins/cad/skills",
        )
        for skills in skill_roots:
            for root, directories, files in os.walk(skills, followlinks=False):
                directories.sort()
                files.sort()
                root_path = Path(root).resolve()
                for name in (*directories, *files):
                    path = root_path / name
                    if path.is_symlink():
                        links.append(path)
        return tuple(sorted(links, key=lambda link: link.relative_to(stage_root)))

    def validate_stage(self, stage: Path) -> None:
        """Gate 2: validate one production contract before transfer."""

        links = self._skill_symlinks(stage)
        if links:
            raise PushError(
                "CVM production stage still contains a skill symlink: "
                f"{links[0].relative_to(stage.resolve())}",
                4,
            )
        for relative in PRODUCTION_RUNTIME.physical_directories:
            path = stage / relative
            if path.is_symlink() or not path.is_dir():
                raise PushError(
                    f"CVM production stage has no physical directory: {relative}",
                    4,
                )
        for relative in PRODUCTION_RUNTIME.required_files:
            if not (stage / relative).is_file():
                raise PushError(
                    f"CVM production stage is missing: {relative}",
                    4,
                )

    def attest_stage(self, stage: Path) -> RuntimeAttestation:
        """Hash key runtime files and parse the required browser revision."""

        viewer_identity = self._validate_viewer_identity(stage)
        hashes = {
            relative: hashlib.sha256((stage / relative).read_bytes()).hexdigest()
            for relative in PRODUCTION_RUNTIME.hash_files
        }
        browser_manifest = (
            stage
            / "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright-core/browsers.json"
        )
        payload = _read_json(browser_manifest, "Playwright browser manifest")
        browsers = payload.get("browsers") if isinstance(payload, dict) else None
        if not isinstance(browsers, list):
            raise PushError("Playwright browser manifest has no browsers list", 4)
        revision: str | None = None
        for entry in browsers:
            if (
                isinstance(entry, dict)
                and entry.get("name") == "chromium-headless-shell"
            ):
                value = str(entry.get("revision", ""))
                if value.isdigit():
                    revision = value
                break
        if revision is None:
            raise PushError(
                "Playwright browser manifest has no numeric "
                "chromium-headless-shell revision",
                4,
            )
        return RuntimeAttestation(
            hashes=hashes,
            chromium_revision=revision,
            viewer_identity=viewer_identity,
        )

    @staticmethod
    def _validate_viewer_identity(stage: Path) -> Mapping[str, object]:
        payload = _read_json(
            stage / VIEWER_RUNTIME_IDENTITY,
            "Viewer runtime identity",
        )
        if not isinstance(payload, dict) or payload.get("schema") != (
            "cad-viewer.runtime-identity/1"
        ):
            raise PushError("Viewer runtime identity has an unsupported schema", 4)
        artifacts = payload.get("artifacts")
        actual_routes = []
        if isinstance(artifacts, list):
            for item in artifacts:
                actual_routes.append(
                    (
                        item.get("role") if isinstance(item, dict) else None,
                        (
                            item.get("source", {}).get("path")
                            if isinstance(item, dict)
                            and isinstance(item.get("source"), dict)
                            else None
                        ),
                        (
                            item.get("bundle", {}).get("path")
                            if isinstance(item, dict)
                            and isinstance(item.get("bundle"), dict)
                            else None
                        ),
                    )
                )
        if actual_routes != list(VIEWER_ARTIFACT_ROUTES):
            raise PushError("Viewer runtime identity has invalid artifacts", 4)
        if not isinstance(payload.get("viewer_version"), str) or not payload[
            "viewer_version"
        ].strip():
            raise PushError("Viewer runtime identity has no viewer version", 4)
        for artifact in artifacts:
            for kind in ("source", "bundle"):
                identity = artifact.get(kind)
                if not isinstance(identity, dict):
                    raise PushError(
                        f"Viewer runtime identity has invalid {kind} entry",
                        4,
                    )
                relative = identity.get("path")
                expected = identity.get("sha256")
                if (
                    not isinstance(relative, str)
                    or relative.startswith("/")
                    or ".." in Path(relative).parts
                    or not isinstance(expected, str)
                    or len(expected) != 64
                ):
                    raise PushError(
                        f"Viewer runtime identity has invalid {kind} identity",
                        4,
                    )
                path = stage / relative
                if not path.is_file():
                    raise PushError(
                        f"Viewer runtime identity is missing {kind}: {relative}",
                        4,
                    )
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                if actual != expected:
                    raise PushError(
                        f"Viewer runtime identity has stale {kind} digest: {relative}",
                        4,
                    )
        return payload

    def transfer_stage(self, stage: Path) -> None:
        """Perform the one and only remote rsync for a complete stage."""

        try:
            log_offset = self.log_path.stat().st_size
        except OSError:
            log_offset = 0
        argv = [
            "rsync",
            "-avz",
            "--progress",
            f"--include={IMPLICIT_NODE_MODULES_INCLUDE}",
            *VIEWER_SOURCE_TRANSFER_FILTERS,
            "--include=/models/",
            "--include=/models/simple/",
            "--include=/models/simple/rectangular_clamp_block.py",
            "--include=/models/simple/simple_model_library.py",
            f"--exclude-from={self.repo_root / '.cvmignore'}",
            f"{stage}/",
            REMOTE_DESTINATION,
        ]
        status = self.runner.stream(
            argv,
            cwd=self.repo_root,
            log_path=self.log_path,
            echo=self.output.echo_stream,
        )
        if status != 0:
            raise PushError(
                f"rsync failed with status {status}",
                status,
                transferred=True,
            )
        self.transfer_summary = self._read_transfer_summary(log_offset)

    def _read_transfer_summary(self, log_offset: int) -> TransferSummary:
        try:
            with self.log_path.open("rb") as log_file:
                log_file.seek(log_offset)
                log = log_file.read().decode("utf-8", errors="replace")
        except OSError:
            return TransferSummary()
        matches = tuple(RSYNC_SUMMARY_PATTERN.finditer(log))
        if not matches:
            return TransferSummary()
        sent, received, rate = matches[-1].groups()
        try:
            return TransferSummary(
                sent_bytes=int(sent.replace(",", "")),
                received_bytes=int(received.replace(",", "")),
                bytes_per_second=float(rate.replace(",", "")),
            )
        except ValueError:
            return TransferSummary()

    def build_remote_native_runtime(self) -> None:
        """Build the target-ABI VoxBlame extension in the physical bundle."""

        command = "\n".join(
            (
                "set -eu",
                f"cd {REMOTE_ROOT}",
                'repo_root="$PWD"',
                (
                    "build_root=$(mktemp -d "
                    "/tmp/text-to-cad-meshscope-build.XXXXXX)"
                ),
                (
                    'case "$build_root" in '
                    "/tmp/text-to-cad-meshscope-build.*) ;; *) exit 4 ;; esac"
                ),
                (
                    "cleanup() { test ! -d \"$build_root\" || "
                    "rm -rf -- \"${build_root:?}\"; }"
                ),
                "trap cleanup EXIT HUP INT TERM",
                f"cd {shlex.quote(NATIVE_MESHSCOPE_RUNTIME)}",
                (
                    '"$repo_root/.venv/bin/python" setup.py build_ext '
                    '--inplace --force --build-temp "$build_root/temp" '
                    '--build-lib "$build_root/lib"'
                ),
            )
        )
        result = self.runner.remote(
            command,
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode != 0:
            raise PushError(
                "CVM native meshscope build failed",
                5,
                transferred=True,
            )

    def publish_remote_deployment_authority(
        self,
        source_head: str,
        chromium_revision: str,
    ) -> dict[str, object]:
        """Create and independently check the complete deployed execution tree."""

        command = "\n".join(
            (
                "set -eu",
                f"cd {REMOTE_ROOT}",
                (
                    "python3 scripts/pilot/deployment_authority.py write . "
                    f"--source-head {shlex.quote(source_head)} "
                    f"--chromium-revision {shlex.quote(chromium_revision)} >/dev/null"
                ),
                "python3 scripts/pilot/deployment_authority.py check .",
            )
        )
        result = self.runner.remote(command, cwd=self.repo_root, check=False)
        if result.returncode != 0:
            raise PushError(
                "CVM deployed source authority publication failed",
                5,
                transferred=True,
            )
        try:
            receipt = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PushError("CVM deployed source authority output is invalid", 5) from exc
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema") != "cvm.deployed-source-authority/1"
            or receipt.get("source_head") != source_head
            or receipt.get("runtime_identity", {}).get("bwrap", {}).get("path")
            != "/usr/bin/bwrap"
            or receipt.get("runtime_identity", {}).get("chromium", {}).get(
                "revision"
            )
            != chromium_revision
            or receipt.get("runtime_identity", {}).get("cadpy", {}).get("path")
            != deployment_authority.CADPY_RUNTIME_PATH
        ):
            raise PushError("CVM deployed source authority identity conflicts", 5)
        return receipt

    @staticmethod
    def _remote_runtime_command() -> str:
        lines = ["set -eu", f"cd {REMOTE_ROOT}"]
        for relative in PRODUCTION_RUNTIME.physical_directories:
            quoted = shlex.quote(relative)
            lines.extend([f"test -d {quoted}", f"test ! -L {quoted}"])
        for relative in PRODUCTION_RUNTIME.required_files:
            lines.append(f"test -f {shlex.quote(relative)}")
        for relative in PRODUCTION_RUNTIME.hash_files:
            quoted = shlex.quote(relative)
            lines.append(
                f"printf '%s\\t' {quoted}; "
                f"sha256sum {quoted} | awk '{{print $1}}'"
            )
        native_source = f"{NATIVE_MESHSCOPE_RUNTIME}/src"
        probe = (
            "import importlib.util, pathlib, sys; "
            f"root = pathlib.Path({native_source!r}).resolve(); "
            "sys.path.insert(0, str(root)); "
            "spec = importlib.util.find_spec('meshscope.voxblame._native'); "
            "assert spec is not None and spec.origin is not None; "
            "assert pathlib.Path(spec.origin).resolve().is_relative_to(root); "
            "from meshscope.voxblame import _native; "
            "assert _native.BACKEND_ID == "
            "'meshscope.voxblame.native-sat/1'"
        )
        lines.append(f"./.venv/bin/python -I -c {shlex.quote(probe)}")
        return "\n".join(lines)

    @staticmethod
    def _parse_remote_hashes(raw: str) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for line in raw.splitlines():
            parts = line.split("\t")
            if len(parts) != 2:
                raise PushError(
                    f"Malformed CVM runtime verification output: {line}",
                    5,
                )
            relative, digest = parts
            if relative in hashes or len(digest) != 64:
                raise PushError(
                    f"Malformed CVM runtime hash: {line}",
                    5,
                )
            try:
                int(digest, 16)
            except ValueError as exc:
                raise PushError(
                    f"Malformed CVM runtime hash: {line}",
                    5,
                ) from exc
            hashes[relative] = digest
        return hashes

    def verify_remote(self, attestation: RuntimeAttestation) -> dict[str, object]:
        """Gate 3: verify physical files, hashes, and browser cache on CVM."""

        result = self.runner.remote(
            self._remote_runtime_command(),
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode != 0:
            raise PushError(
                "CVM runtime verification failed: "
                "missing physical runtime file",
                5,
            )
        remote_hashes = self._parse_remote_hashes(result.stdout)
        if remote_hashes != dict(attestation.hashes):
            raise PushError(
                "CVM runtime verification failed: runtime hash mismatch",
                5,
            )

        revision = attestation.chromium_revision
        browser = (
            f'$HOME/.cache/ms-playwright/chromium_headless_shell-{revision}/'
            "chrome-headless-shell-linux64/chrome-headless-shell"
        )
        browser_result = self.runner.remote(
            f'test -x "{browser}"',
            cwd=self.repo_root,
            check=False,
        )
        if browser_result.returncode != 0:
            raise PushError(
                f"CVM Playwright browser revision {revision} is missing.",
                6,
            )
        identity = attestation.viewer_identity
        if not isinstance(identity, Mapping):
            raise PushError("Viewer runtime deployment has no source identity", 5)
        receipt_artifacts = []
        for artifact in identity["artifacts"]:
            bundle = artifact["bundle"]
            deployed_digest = remote_hashes.get(bundle["path"])
            if deployed_digest != bundle["sha256"]:
                raise PushError(
                    "CVM runtime verification failed: Viewer deployment digest mismatch",
                    5,
                )
            receipt_artifacts.append(
                {
                    "role": artifact["role"],
                    "source": artifact["source"],
                    "bundle": bundle,
                    "deployed": {
                        "path": bundle["path"],
                        "sha256": deployed_digest,
                    },
                }
            )
        return {
            "schema": "cvm.viewer-runtime-deployment/1",
            "viewer_version": identity["viewer_version"],
            "artifacts": receipt_artifacts,
        }

    def remote_git_base(self) -> str:
        result = self.runner.remote(
            (
                f"cd {REMOTE_ROOT} && "
                "git rev-parse --verify HEAD 2>/dev/null || echo no-git"
            ),
            cwd=self.repo_root,
            check=False,
        )
        return result.stdout.strip() or "no-git"

    def run(self) -> None:
        """Run the complete production deployment workflow."""

        self._enter_phase("preflight")
        self.preflight_local()
        self.preflight_remote()
        self._log(f"Log: {self.log_path}")

        self.source = self.inspect_source()
        self._log(
            "Source: "
            f"branch={self.source.branch} head={self.source.head} "
            f"state={self.source.state}"
        )
        self._log("Building physical CAD runtimes in an isolated stage...")

        try:
            self._enter_phase("stage")
            inputs = self.resolve_build_inputs()
            with self.deployment_stage() as stage:
                self.copy_source_to_stage(stage)
                self.copy_build_inputs(stage, inputs)
                self.materialize_skill_symlinks(stage)
                self.bundle_stage(stage)
                self.validate_stage(stage)
                attestation = self.attest_stage(stage)
                self._enter_phase("transfer")
                self.transfer_stage(stage)
                self._enter_phase("verify")
                self.build_remote_native_runtime()
                self.deployed_source_authority = (
                    self.publish_remote_deployment_authority(
                        self.source.head,
                        attestation.chromium_revision,
                    )
                )
                self.viewer_deployment = self.verify_remote(attestation)
        except PushError as exc:
            if exc.status == 4 and not exc.transferred:
                self._log_best_effort(
                    "CVM production staging failed; no files transferred.",
                    stderr=True,
                )
            raise

        self._log(
            "CVM runtime verified: physical Viewer + implicit + native "
            "meshscope runtime, matching hashes, and Playwright browser revision"
        )
        self._log(
            "Viewer deployment receipt: "
            + json.dumps(
                self.viewer_deployment,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        self._log(
            "Deployed source authority: "
            + json.dumps(
                self.deployed_source_authority,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        self.remote_head = self.remote_git_base()
        self._log(
            f"Remote Git base: {self.remote_head} "
            "(rsync overlay; not deployment identity)"
        )
        self.phase = "complete"

    def receipt(self, *, status: str, exit_code: int, error: str | None) -> dict:
        source = None
        if self.source is not None:
            source = {
                "branch": self.source.branch,
                "head": self.source.head,
                "state": self.source.state,
            }
        return {
            "schema": "cvm-push.receipt/1",
            "type": "receipt",
            "status": status,
            "exit_code": exit_code,
            "phase": self.phase,
            "error": error,
            "log_path": str(self.log_path),
            "receipt_path": str(self.receipt_path),
            "source": source,
            "transfer": {
                "sent_bytes": self.transfer_summary.sent_bytes,
                "received_bytes": self.transfer_summary.received_bytes,
                "bytes_per_second": self.transfer_summary.bytes_per_second,
            },
            "remote_git_base": self.remote_head,
            "deployed_source_authority": self.deployed_source_authority,
            "viewer_deployment": self.viewer_deployment,
        }

    def write_receipt(self, receipt: Mapping[str, object]) -> None:
        self.receipt_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.receipt_path.with_name(f".{self.receipt_path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(receipt, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.receipt_path)
        finally:
            temporary.unlink(missing_ok=True)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build, transfer, and verify a production CVM deployment."
    )
    parser.add_argument(
        "--agent",
        action="store_true",
        help="emit compact machine-readable events while detailed progress stays in the log",
    )
    return parser.parse_args(argv)


def execute(workflow: CvmPush) -> int:
    log_write_error = None
    try:
        workflow.run()
    except PushError as exc:
        exit_code = exc.status
        error = str(exc)
        log_write_error = workflow._log_best_effort(error, stderr=True)
    except KeyboardInterrupt:
        exit_code = 130
        error = "interrupted"
        log_write_error = workflow._log_best_effort(error, stderr=True)
    except Exception as exc:
        exit_code = 1
        error = str(exc) or type(exc).__name__
        log_write_error = workflow._log_best_effort(
            traceback.format_exc().rstrip(), stderr=True
        )
    else:
        exit_code = 0
        error = None
    receipt = workflow.receipt(
        status="succeeded" if exit_code == 0 else "failed",
        exit_code=exit_code,
        error=error,
    )
    if log_write_error is not None:
        receipt["log_write_error"] = log_write_error
    receipt_written = False
    try:
        workflow.write_receipt(receipt)
        receipt_written = True
    except OSError as exc:
        receipt["receipt_write_error"] = f"{type(exc).__name__}: {exc}"
        if exit_code == 0:
            exit_code = 1
            receipt.update(
                status="failed",
                exit_code=exit_code,
                error="receipt write failed",
            )
    workflow.output.terminal_receipt(receipt, written=receipt_written)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return execute(CvmPush(CommandRunner(), agent=args.agent))


if __name__ == "__main__":
    raise SystemExit(main())
