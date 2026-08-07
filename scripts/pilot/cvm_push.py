#!/usr/bin/env python3
"""Build, transfer, and verify a physical production deployment on CVM."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
REMOTE_ROOT = "~/text-to-cad"
REMOTE_DESTINATION = f"cvm:{REMOTE_ROOT}/"
IMPLICIT_NODE_MODULES_INCLUDE = (
    "/skills/implicit-cad/scripts/packages/implicitjs/node_modules/***"
)

# Source -> staging is intentionally different from staging -> CVM. The
# staging copy keeps build-only inputs such as viewer/ and packages/
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


PRODUCTION_RUNTIME = RuntimeContract(
    physical_directories=(
        "skills/cad-viewer/scripts/viewer",
        "skills/implicit-cad/scripts/packages/implicitjs",
    ),
    required_files=(
        "skills/cad-viewer/scripts/viewer/package.json",
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
        "skills/cad-viewer/scripts/viewer/dist/index.html",
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
    ),
    hash_files=(
        "skills/cad-viewer/scripts/viewer/backend/server.mjs",
        "skills/cad-viewer/scripts/viewer/scripts/start-agent-viewer.mjs",
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "scripts/snapshot.mjs"
        ),
        (
            "skills/implicit-cad/scripts/packages/implicitjs/"
            "node_modules/playwright-core/browsers.json"
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
    ) -> None:
        self.runner = runner
        self.repo_root = repo_root.resolve()
        self.environ = dict(os.environ if environ is None else environ)
        self.log_path = Path(
            self.environ.get("TMPDIR", "/tmp")
        ) / f"cvm-push-{time.strftime('%Y%m%d-%H%M%S')}.log"

    def _log(self, message: str, *, stderr: bool = False) -> None:
        print(message, file=sys.stderr if stderr else sys.stdout)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as log:
            print(message, file=log)

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
            print(
                f"WARN: CVM disk low: {free_gb}G free (threshold 10G).",
                file=sys.stderr,
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
        )

    def transfer_stage(self, stage: Path) -> None:
        """Perform the one and only remote rsync for a complete stage."""

        argv = [
            "rsync",
            "-avz",
            "--progress",
            f"--include={IMPLICIT_NODE_MODULES_INCLUDE}",
            f"--exclude-from={self.repo_root / '.cvmignore'}",
            f"{stage}/",
            REMOTE_DESTINATION,
        ]
        status = self.runner.stream(
            argv,
            cwd=self.repo_root,
            log_path=self.log_path,
            echo=True,
        )
        if status != 0:
            raise PushError(
                f"rsync failed with status {status}",
                status,
                transferred=True,
            )

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

        self.preflight_local()
        self.preflight_remote()
        print(f"Log: {self.log_path}")

        source = self.inspect_source()
        self._log(
            "Source: "
            f"branch={source.branch} head={source.head} state={source.state}"
        )
        self._log("Building physical CAD runtimes in an isolated stage...")

        try:
            inputs = self.resolve_build_inputs()
            with self.deployment_stage() as stage:
                self.copy_source_to_stage(stage)
                self.copy_build_inputs(stage, inputs)
                self.bundle_stage(stage)
                self.validate_stage(stage)
                attestation = self.attest_stage(stage)
                self.transfer_stage(stage)
                self.remove_legacy_plugin_tree()
                self.verify_remote(attestation)
        except PushError as exc:
            if exc.status == 4 and not exc.transferred:
                self._log(
                    "CVM production staging failed; no files transferred.",
                    stderr=True,
                )
            raise

        self._log(
            "CVM runtime verified: physical Viewer + implicit runtime, "
            "matching hashes, and Playwright browser revision"
        )
        remote_head = self.remote_git_base()
        self._log(
            f"Remote Git base: {remote_head} "
            "(rsync overlay; not deployment identity)"
        )


def main() -> int:
    workflow = CvmPush(CommandRunner())
    try:
        workflow.run()
    except PushError as exc:
        print(str(exc), file=sys.stderr)
        return exc.status
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
