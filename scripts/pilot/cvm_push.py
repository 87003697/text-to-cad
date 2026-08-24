#!/usr/bin/env python3
"""Build, transfer, and verify a physical production deployment on CVM."""

from __future__ import annotations

import argparse
import base64
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


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
REMOTE_ROOT = "~/text-to-cad"
REMOTE_DESTINATION = f"cvm:{REMOTE_ROOT}/"
RSYNC_SUMMARY_PATTERN = re.compile(
    r"sent ([\d,]+) bytes\s+received ([\d,]+) bytes\s+"
    r"([\d,]+(?:\.\d+)?) bytes/sec"
)

from scripts.pilot import plugin_deployment as _plugin_deployment  # noqa: E402

# Mac-side staging hygiene only. This keeps ``.venv/`` symlinks, ``.agents/``,
# editor scratch, and other local state from ever entering the stage in the
# first place. It is NOT a substitute for exact remote snapshot identity —
# the CVM ``~/text-to-cad`` overlay is persistent and non-deleting, so files
# a prior push wrote there that later disappeared from the Mac stage will
# linger regardless of what we exclude here. The stage manifest written by
# :func:`plugin_deployment.write_stage_manifest` and validated by
# :func:`plugin_deployment.materialize_from_stage_manifest` is what enforces
# identity end-to-end.
STAGE_SOURCE_EXCLUDES = _plugin_deployment.DEPLOYMENT_EXCLUDE_PATTERNS
TRANSFER_TREE_DIRNAME = ".cvm-transfer-tree"

# Remote helper that produces the installed-plugin authority receipt after
# verify_remote has already confirmed the transferred bytes are healthy.
REMOTE_INSTALL_HELPER_REL = "scripts/pilot/cvm_install_plugin.py"
REMOTE_AUTHORITY_HOME_ROOT = "$HOME"
INSTALL_EXIT_CODE = 7
VERIFY_EXIT_CODE = 8

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
    "three",
    "gifenc",
)
CAD_REQUIRED_EXECUTABLES = ("node_modules/.bin/esbuild",)
CAD_SNAPSHOT_ESBUILD_VERSION = "0.27.7"
CAD_LOCKED_PACKAGES = ("three", "gifenc", "meshoptimizer")
MESHSHOT_REQUIRED_EXECUTABLES = (".bin/esbuild",)
MESHSHOT_REQUIRED_PACKAGES = ("esbuild", "three")


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
    meshshot_node_modules: Path


@dataclass(frozen=True)
class RuntimeContract:
    """One production contract shared by local and remote validation."""

    physical_directories: tuple[str, ...]
    required_files: tuple[str, ...]
    hash_files: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeAttestation:
    """Local runtime hashes expected after remote transfer."""

    hashes: Mapping[str, str]


@dataclass(frozen=True)
class TransferSummary:
    """Compact rsync totals retained after detailed progress stays in the log."""

    sent_bytes: int | None = None
    received_bytes: int | None = None
    bytes_per_second: float | None = None


PRODUCTION_RUNTIME = RuntimeContract(
    physical_directories=(
        "skills/cad-viewer/scripts/viewer",
    ),
    required_files=(
        "skills/cad-viewer/scripts/viewer/package.json",
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
        "skills/cad-viewer/scripts/viewer/dist/index.html",
    ),
    hash_files=(
        *_plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS,
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


def cad_required_package_versions(repo_root: Path) -> Mapping[str, str]:
    """Resolve the snapshot toolchain contract from its canonical lockfile."""

    lock = _read_json(
        repo_root / "packages/cadjs/package-lock.json",
        "cadjs lockfile",
    )
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise PushError("cadjs package-lock.json has no packages object", 4)

    versions = {"esbuild": CAD_SNAPSHOT_ESBUILD_VERSION}
    for package in CAD_LOCKED_PACKAGES:
        locked = packages.get(f"node_modules/{package}")
        version = locked.get("version") if isinstance(locked, dict) else None
        if not isinstance(version, str):
            raise PushError(
                f"cadjs package-lock.json has no version for {package}",
                4,
            )
        versions[package] = version
    return versions


def cad_dependency_errors(candidate: Path, repo_root: Path) -> tuple[str, ...]:
    """Return every reason a CAD snapshot dependency candidate is incomplete."""

    errors: list[str] = []
    for relative in CAD_REQUIRED_EXECUTABLES:
        path = candidate / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            errors.append(f"missing executable {relative}")
    for package, expected in cad_required_package_versions(repo_root).items():
        path = _package_path(candidate / "node_modules", package)
        actual = _package_version(path)
        if actual is None:
            errors.append(f"missing package {package}")
        elif actual != expected:
            errors.append(f"{package} version {actual} does not match {expected}")
    return tuple(errors)


def meshshot_dependency_errors(candidate: Path, repo_root: Path) -> tuple[str, ...]:
    """Return every reason a meshshot browser-build candidate is incomplete."""

    errors: list[str] = []
    for relative in MESHSHOT_REQUIRED_EXECUTABLES:
        path = candidate / relative
        if not path.is_file() or not os.access(path, os.X_OK):
            errors.append(f"missing executable {relative}")
    lock = _read_json(
        repo_root / "packages/meshshot/package-lock.json",
        "meshshot lockfile",
    )
    packages = lock.get("packages") if isinstance(lock, dict) else None
    if not isinstance(packages, dict):
        raise PushError("meshshot package-lock.json has no packages object", 4)
    for package in MESHSHOT_REQUIRED_PACKAGES:
        locked = packages.get(f"node_modules/{package}")
        expected = locked.get("version") if isinstance(locked, dict) else None
        actual = _package_version(_package_path(candidate, package))
        if not isinstance(expected, str):
            errors.append(f"lockfile has no version for {package}")
        elif actual is None:
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
        self.agent = agent
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
        self.plugin_authority: dict | None = None
        self.stage_manifest_digest: str | None = None

    def _log(self, message: str, *, stderr: bool = False) -> None:
        if not self.agent:
            print(message, file=sys.stderr if stderr else sys.stdout)
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
        meshshot_candidates = [self.repo_root / "packages/meshshot/node_modules"]
        if primary is not None:
            viewer_candidates.append(primary / "viewer/node_modules")
            cad_candidates.append(primary / "tmp/cad-snapshot-build")
            meshshot_candidates.append(primary / "packages/meshshot/node_modules")
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
                validate=lambda path: cad_dependency_errors(
                    path,
                    self.repo_root,
                ),
            ),
            meshshot_node_modules=self._resolve_source(
                label="meshshot build dependencies",
                explicit_name="CVM_PUSH_MESHSHOT_NODE_MODULES_SOURCE",
                candidates=meshshot_candidates,
                validate=lambda path: meshshot_dependency_errors(
                    path,
                    self.repo_root,
                ),
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
                ("cadjs",),
            ),
            (
                inputs.cad_build_dependencies,
                stage / "tmp/cad-snapshot-build",
                (),
            ),
            (
                inputs.meshshot_node_modules,
                stage / "packages/meshshot/node_modules",
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
        for package in ("cadjs",):
            link = viewer_modules / package
            if link.exists() or link.is_symlink():
                link.unlink()
            # These are stage-internal package links. They never point back to
            # the dependency source or primary checkout.
            os.symlink(f"../packages/{package}", link)

    def bundle_stage(self, stage: Path) -> None:
        """Materialize every production skill runtime inside staging."""

        snapshot_build_deps = str(stage / "tmp/cad-snapshot-build")
        env = {
            **self.environ,
            "CAD_SNAPSHOT_BUILD_DEPS_DIR": snapshot_build_deps,
            "DXF_SNAPSHOT_BUILD_DEPS_DIR": snapshot_build_deps,
            "SDF_SNAPSHOT_BUILD_DEPS_DIR": snapshot_build_deps,
            "SRDF_SNAPSHOT_BUILD_DEPS_DIR": snapshot_build_deps,
            "URDF_SNAPSHOT_BUILD_DEPS_DIR": snapshot_build_deps,
            "NODE_BUILDER_BUILD_DEPS_DIR": snapshot_build_deps,
        }
        status = self.runner.stream(
            ["scripts/bundle/bundle-skill.sh", "--all"],
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
        for link in self._skill_symlinks(stage):
            try:
                source = link.resolve(strict=True)
                source.relative_to(stage_root)
            except (FileNotFoundError, ValueError) as exc:
                raise PushError(
                    "CVM production stage has an unsafe skill symlink: "
                    f"{link.relative_to(stage)}",
                    4,
                ) from exc

            temporary = link.with_name(f".{link.name}.cvm-materialize")
            if temporary.exists() or temporary.is_symlink():
                raise PushError(
                    "CVM production stage has a materialization collision: "
                    f"{temporary.relative_to(stage)}",
                    4,
                )
            if source.is_dir():
                shutil.copytree(source, temporary, symlinks=True)
            elif source.is_file():
                shutil.copy2(source, temporary, follow_symlinks=False)
            else:
                raise PushError(
                    "CVM production stage skill symlink has unsupported target: "
                    f"{link.relative_to(stage)}",
                    4,
                )
            link.unlink()
            temporary.rename(link)

    @staticmethod
    def _skill_symlinks(stage: Path) -> tuple[Path, ...]:
        links: list[Path] = []
        skills = stage / "skills"
        for root, directories, files in os.walk(skills, followlinks=False):
            root_path = Path(root)
            for name in (*directories, *files):
                path = root_path / name
                if path.is_symlink():
                    links.append(path)
        return tuple(links)

    def validate_stage(self, stage: Path) -> None:
        """Gate 2: validate one production contract before transfer."""

        links = self._skill_symlinks(stage)
        if links:
            raise PushError(
                "CVM production stage still contains a skill symlink: "
                f"{links[0].relative_to(stage)}",
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

        hashes = {
            relative: hashlib.sha256((stage / relative).read_bytes()).hexdigest()
            for relative in PRODUCTION_RUNTIME.hash_files
        }
        return RuntimeAttestation(hashes=hashes)

    def prepare_transfer_tree(self, stage: Path) -> Path:
        """Filter the build stage once, then bind exactly what rsync sends."""

        transfer_tree = stage / TRANSFER_TREE_DIRNAME
        if transfer_tree.exists() or transfer_tree.is_symlink():
            raise PushError(
                f"CVM transfer tree already exists: {transfer_tree}", 4
            )
        transfer_tree.mkdir()
        result = self.runner.run(
            [
                "rsync",
                "-a",
                f"--exclude=/{TRANSFER_TREE_DIRNAME}/",
                f"--exclude-from={self.repo_root / '.cvmignore'}",
                f"{stage}/",
                f"{transfer_tree}/",
            ],
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode != 0:
            raise PushError(
                f"Cannot prepare exact CVM transfer tree: {result.stderr.strip()}",
                4,
            )
        try:
            _plugin_deployment.normalize_stage_permissions(transfer_tree)
            digest = _plugin_deployment.write_stage_manifest(transfer_tree)
        except _plugin_deployment.PluginAuthorityError as exc:
            raise PushError(f"CVM transfer tree is not manifestable: {exc}", 4) from exc
        self.stage_manifest_digest = digest
        return transfer_tree

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
            f"{stage}/",
            REMOTE_DESTINATION,
        ]
        status = self.runner.stream(
            argv,
            cwd=self.repo_root,
            log_path=self.log_path,
            echo=not self.agent,
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

    def remove_legacy_plugin_tree(self) -> None:
        """Remove only the obsolete pre-repo-root plugin package on CVM."""

        result = self.runner.remote(
            (
                "set -eu\n"
                f"cd {REMOTE_ROOT}\n"
                "if [ -e plugins ]; then rm -rf -- plugins; fi\n"
                "test ! -e plugins"
            ),
            cwd=self.repo_root,
            check=False,
        )
        if result.returncode != 0:
            raise PushError(
                "CVM repo-root plugin migration failed to remove legacy plugins/",
                5,
                transferred=True,
            )

    @staticmethod
    def _remote_runtime_command() -> str:
        lines = ["set -eu", f"cd {REMOTE_ROOT}", "test ! -e plugins"]
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

    def verify_remote(self, attestation: RuntimeAttestation) -> None:
        """Gate 3: verify transferred files and prepare the pilot runtime."""

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

        command = (
            "set -eu\n"
            f"cd {REMOTE_ROOT}\n"
            "test -x .venv/bin/python\n"
            ".venv/bin/python -m pip install "
            "--disable-pip-version-check --no-input --no-build-isolation "
            "--no-deps --force-reinstall --editable packages/meshscope\n"
            ".venv/bin/python -c 'from meshscope.voxblame import _native; "
            "assert callable(_native.build)'"
        )
        result = self.runner.remote(command, cwd=self.repo_root, check=False)
        if result.returncode != 0:
            raise PushError(
                "CVM pilot Python runtime provisioning failed: "
                "meshscope native backend is unavailable",
                5,
                transferred=True,
            )


    def _build_push_provenance(
        self, attestation: RuntimeAttestation
    ) -> dict[str, object]:
        """Assemble the Mac-side provenance document bound into the receipt.

        The publisher on the CVM never consults its own local git checkout —
        that would just tell us the CVM received the bytes, not who sent them.
        The Mac-observed branch/head/dirty flag plus the exact runtime
        attestation hashes computed *before* the rsync are what a downstream
        auditor uses to answer "which Mac working tree produced this
        authority?".
        """

        if self.source is None:
            raise PushError(
                "cannot install plugin authority before source is inspected",
                INSTALL_EXIT_CODE,
                transferred=True,
            )
        if self.stage_manifest_digest is None:
            raise PushError(
                "cannot install plugin authority before stage manifest is written",
                INSTALL_EXIT_CODE,
                transferred=True,
            )
        return {
            "schema": _plugin_deployment.PROVENANCE_SCHEMA,
            "mac_branch": self.source.branch,
            "mac_head": self.source.head,
            "mac_state": self.source.state,
            "stage_manifest_digest": self.stage_manifest_digest,
            "transfer_summary": {
                "sent_bytes": self.transfer_summary.sent_bytes,
                "received_bytes": self.transfer_summary.received_bytes,
                "bytes_per_second": self.transfer_summary.bytes_per_second,
            },
            "runtime_attestation": dict(attestation.hashes),
        }

    @staticmethod
    def _encode_provenance(document: Mapping[str, object]) -> str:
        canonical = json.dumps(
            document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        return base64.urlsafe_b64encode(canonical).decode("ascii")

    def install_plugin_authority(self, attestation: RuntimeAttestation) -> dict:
        """Gate 4: finalize + install + verify + publish the plugin authority.

        Invokes the shipped ``cvm_install_plugin.py`` helper over ``ssh -n cvm``
        (the same transport the rest of this workflow uses) so the transferred
        bytes are finalized into a symlink-free publish tree, installed through
        the real Codex plugin CLI in an isolated CODEX_HOME, verified against
        the prepared tree, and atomically published via ``current.json``. The
        Mac-side push provenance (branch, head, dirty flag, transfer totals,
        runtime attestation hashes) is transported as a strict base64url
        canonical JSON blob on the SSH command line so we do not need a second
        control-plane transport and so a hostile receipt content cannot escape
        shell quoting. On failure the previous authority pointer is untouched.
        Install failures surface as exit code 7; verify failures surface as
        exit code 8.
        """

        provenance = self._build_push_provenance(attestation)
        encoded = self._encode_provenance(provenance)
        # The helper reads paths from argv, not from a shell-composed string,
        # and every argument value is either a fixed constant or the strict
        # base64url provenance blob shell-quoted below.
        command = (
            "set -eu\n"
            'remote_root="$HOME/text-to-cad"\n'
            f'python3 "$remote_root/{REMOTE_INSTALL_HELPER_REL}" '
            '--transferred-source "$remote_root" '
            f'--codex-home-root "{REMOTE_AUTHORITY_HOME_ROOT}" '
            f"--provenance-b64 {shlex.quote(encoded)}"
        )
        result = self.runner.remote(
            command,
            cwd=self.repo_root,
            check=False,
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        payload: dict | None = None
        if stdout:
            try:
                document = json.loads(stdout.splitlines()[-1])
            except json.JSONDecodeError:
                document = None
            if isinstance(document, dict):
                payload = document
        if result.returncode != 0 or (
            isinstance(payload, dict)
            and payload.get("schema") == "text-to-cad.plugin-authority-error/1"
        ):
            stage = "install"
            detail = stderr or stdout or "unknown remote install failure"
            if isinstance(payload, dict):
                stage_value = payload.get("stage")
                if stage_value in {"install", "verify"}:
                    stage = str(stage_value)
                error_value = payload.get("error")
                if isinstance(error_value, str) and error_value:
                    detail = error_value
            status = INSTALL_EXIT_CODE if stage == "install" else VERIFY_EXIT_CODE
            message = (
                f"CVM plugin {stage} failed: {detail}"
            )
            raise PushError(message, status, transferred=True)
        if payload is None or payload.get("schema") != _plugin_deployment.RECEIPT_SCHEMA:
            raise PushError(
                "CVM plugin install returned no authority receipt on stdout",
                VERIFY_EXIT_CODE,
                transferred=True,
            )
        self.plugin_authority = payload
        return payload

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
                transfer_tree = self.prepare_transfer_tree(stage)
                self._enter_phase("transfer")
                self.transfer_stage(transfer_tree)
                self.remove_legacy_plugin_tree()
                self._enter_phase("verify")
                self.verify_remote(attestation)
                self._enter_phase("install")
                self.install_plugin_authority(attestation)
        except PushError as exc:
            if exc.status == 4 and not exc.transferred:
                self._log_best_effort(
                    "CVM production staging failed; no files transferred.",
                    stderr=True,
                )
            raise

        self._log(
            "CVM runtime verified: physical Viewer runtime, "
            "and matching hashes"
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
            "plugin_authority": self.plugin_authority,
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
    if workflow.agent:
        print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    elif receipt_written:
        print(f"Receipt: {workflow.receipt_path}")
    else:
        print(receipt["receipt_write_error"], file=sys.stderr)
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    return execute(CvmPush(CommandRunner(), agent=args.agent))


if __name__ == "__main__":
    raise SystemExit(main())
