"""Installed-plugin smoke test driver and audit helpers.

Prepares a symlink-free production publish tree from a source checkout, installs it
through the real Codex plugin CLI into an isolated CODEX_HOME, and asserts the
installed cache matches the prepared tree exactly. It resolves the installed
canonical CAD adapter through the installed Workspace's trusted-tool registry
and runs a provider-free build with the repo checkout hidden from Python's
import path, so a silent fallback to source cannot mask a missing runtime.

Pure validation logic (manifest / digest / fail-closed assertions / receipt
construction) is exposed as functions and covered by tests/python/global/
test_smoke_installed_plugin.py. The `main` entrypoint is the thin driver used
by scripts/release/smoke-installed-plugin.sh.

The smoke is the durable proof that the transformation in
scripts/release/finalize-publish-tree.sh actually survives a real
`codex plugin add` install. Release automation and this smoke share that
transformation script so they cannot drift.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


# The nine skill runtime paths that develop tracks as symlinks and that a raw
# `codex plugin add` on develop silently drops (see AGENTS.md and CONTEXT.md).
# The smoke fails closed if any of these is missing or empty in the installed
# cache. Each entry names one file inside the runtime whose presence proves the
# vendored package actually materialized (rather than a stub directory).
CRITICAL_RUNTIME_PATHS: tuple[tuple[str, str], ...] = (
    ("skills/cad-viewer/scripts/viewer", "package.json"),
    ("skills/cad/scripts/packages/cadgen", "src/cadgen/__init__.py"),
    ("skills/dxf/scripts/packages/cadgen", "src/cadgen/__init__.py"),
    ("skills/mesh-compare/scripts/packages/meshscope", "src/meshscope/__init__.py"),
    ("skills/mesh-compare/scripts/packages/meshshot", "src/meshshot/__init__.py"),
    ("skills/mesh-inspect/scripts/packages/meshscope", "src/meshscope/__init__.py"),
    ("skills/sdf/scripts/packages/cadgen", "src/cadgen/__init__.py"),
    ("skills/srdf/scripts/packages/cadgen", "src/cadgen/__init__.py"),
    ("skills/urdf/scripts/packages/cadgen", "src/cadgen/__init__.py"),
)


RECEIPT_SCHEMA = "text-to-cad.installed-plugin-smoke.receipt/1"


class SmokeError(RuntimeError):
    """A fail-closed assertion in the installed-plugin smoke."""


@dataclass
class ManifestEntry:
    path: str
    sha256: str
    mode: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256, "mode": self.mode}


@dataclass
class Manifest:
    root: Path
    entries: list[ManifestEntry] = field(default_factory=list)

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        for entry in self.entries:
            h.update(entry.path.encode("utf-8"))
            h.update(b"\0")
            h.update(entry.sha256.encode("ascii"))
            h.update(b"\0")
            h.update(entry.mode.encode("ascii"))
            h.update(b"\n")
        return h.hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "file_count": len(self.entries),
            "digest_sha256": self.digest,
            "entries": [entry.as_dict() for entry in self.entries],
        }

    def paths(self) -> set[str]:
        return {entry.path for entry in self.entries}


def compute_manifest(
    root: Path,
    *,
    private_paths: Sequence[str] = (),
) -> Manifest:
    """Deterministic manifest of relative regular-file paths + content sha256.

    Fails closed on any symlink so the caller can rely on Manifest as evidence
    of a fully-materialized tree.
    """

    root = root.resolve()
    if not root.is_dir():
        raise SmokeError(f"manifest root is not a directory: {root}")
    root_mode = stat.S_IMODE(root.stat().st_mode)
    if root_mode not in {0o700, 0o755}:
        raise SmokeError(f"unsafe root directory mode {root_mode:04o}: {root}")
    entries: list[ManifestEntry] = []
    private = set(private_paths)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        # os.walk yields directories that are symlinks with followlinks=False
        # but does not descend into them; still, no directory in the publish
        # tree may be a symlink.
        for name in list(dirnames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise SmokeError(f"symlink in tree: {full.relative_to(root)}")
            mode = stat.S_IMODE(full.stat().st_mode)
            if mode != 0o755:
                raise SmokeError(
                    f"unsafe directory mode {mode:04o} in tree: "
                    f"{full.relative_to(root)}"
                )
        for name in sorted(filenames):
            full = Path(dirpath) / name
            if full.is_symlink():
                raise SmokeError(f"symlink in tree: {full.relative_to(root)}")
            rel = full.relative_to(root).as_posix()
            metadata = full.stat()
            mode = f"{stat.S_IMODE(metadata.st_mode):04o}"
            allowed_modes = {"0600"} if rel in private else {"0644", "0755"}
            if mode not in allowed_modes:
                raise SmokeError(
                    f"unsafe permission mode {mode} in tree: {rel}"
                )
            entries.append(ManifestEntry(rel, _hash_file(full), mode))
    entries.sort(key=lambda e: e.path)
    return Manifest(root=root, entries=entries)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def assert_manifests_equal(
    prepared: Manifest,
    installed: Manifest,
    *,
    ignore_paths: Sequence[str] = (),
) -> None:
    """Fail closed if the installed cache diverges from the prepared tree.

    Codex has not been observed to write provider-owned metadata inside the
    plugin cache directory, so the default policy is exact parity. If a
    future CLI release starts adding a manifest file, extend ignore_paths and
    document each entry there.
    """

    ignored = set(ignore_paths)
    prepared_by_path = {
        e.path: (e.sha256, e.mode) for e in prepared.entries if e.path not in ignored
    }
    installed_by_path = {
        e.path: (e.sha256, e.mode) for e in installed.entries if e.path not in ignored
    }
    missing = sorted(set(prepared_by_path) - set(installed_by_path))
    extra = sorted(set(installed_by_path) - set(prepared_by_path))
    mismatched = sorted(
        path for path in set(prepared_by_path) & set(installed_by_path)
        if prepared_by_path[path] != installed_by_path[path]
    )
    if missing or extra or mismatched:
        raise SmokeError(_format_manifest_diff(missing, extra, mismatched))


def _format_manifest_diff(
    missing: Sequence[str],
    extra: Sequence[str],
    mismatched: Sequence[str],
) -> str:
    lines = ["installed cache does not match prepared publish tree"]
    for label, values in (
        ("dropped by installer", missing),
        ("added by installer", extra),
        ("content mismatch", mismatched),
    ):
        if values:
            lines.append(f"  {label} ({len(values)}):")
            for value in values[:20]:
                lines.append(f"    - {value}")
            if len(values) > 20:
                lines.append(f"    ... ({len(values) - 20} more)")
    return "\n".join(lines)


def assert_critical_runtimes(root: Path) -> list[dict[str, str]]:
    """Assert every formerly-omitted symlink runtime is present under root.

    Returns a receipt fragment listing the verified paths and probe files.
    """

    verified: list[dict[str, str]] = []
    for runtime_dir, probe_rel in CRITICAL_RUNTIME_PATHS:
        runtime_path = root / runtime_dir
        if not runtime_path.is_dir():
            raise SmokeError(
                f"critical runtime is missing from installed tree: {runtime_dir}"
            )
        probe_path = runtime_path / probe_rel
        if not probe_path.is_file():
            raise SmokeError(
                f"critical runtime materialized empty: {runtime_dir} lacks {probe_rel}"
            )
        verified.append({
            "runtime": runtime_dir,
            "probe": f"{runtime_dir}/{probe_rel}",
            "probe_sha256": _hash_file(probe_path),
        })
    return verified


def sanitize_env_for_installed_run(
    installed_root: Path,
    source_root: Path,
    *,
    python_executable: Path,
) -> dict[str, str]:
    """Return an environment that cannot silently fall back to source paths.

    The caller runs an installed skill entrypoint through this environment; the
    entrypoint's own sys.path insertion must land inside the installed cache
    or the invocation fails closed rather than reading the developer checkout.
    """

    base = {k: v for k, v in os.environ.items() if not _is_python_path_var(k)}
    # PATH is scrubbed of anything under source_root so subprocesses cannot
    # resolve the source checkout by accident. The Python executable is invoked
    # by absolute path so PATH does not need to include its directory.
    base["PATH"] = _sanitize_path_value(base.get("PATH", ""), source_root)
    base["PYTHONNOUSERSITE"] = "1"
    base["PYTHONDONTWRITEBYTECODE"] = "1"
    base["PYTHONSAFEPATH"] = "1"
    base["TEXT_TO_CAD_INSTALLED_ROOT"] = str(installed_root)
    return base


_PYTHON_PATH_VARS = frozenset({"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"})


def _is_python_path_var(name: str) -> bool:
    return name in _PYTHON_PATH_VARS


def _sanitize_path_value(value: str, source_root: Path) -> str:
    if not value:
        return value
    source_prefix = str(source_root.resolve())
    kept: list[str] = []
    for segment in value.split(os.pathsep):
        if not segment:
            continue
        try:
            resolved = str(Path(segment).resolve())
        except OSError:
            resolved = segment
        if resolved == source_prefix or resolved.startswith(source_prefix + os.sep):
            continue
        kept.append(segment)
    return os.pathsep.join(kept)


def assert_entrypoint_under(root: Path, entrypoint: Path) -> Path:
    """Fail closed if the resolved entrypoint is not under the installed root."""

    resolved = entrypoint.resolve()
    installed = root.resolve()
    try:
        resolved.relative_to(installed)
    except ValueError as exc:
        raise SmokeError(
            f"entrypoint escaped installed cache: {resolved} not under {installed}"
        ) from exc
    return resolved


def assert_installed_path_is_isolated(codex_home: Path, installed_path: Path) -> Path:
    """Require the CLI-reported cache path to stay inside task-private state."""

    isolated_root = codex_home.resolve()
    resolved = installed_path.resolve()
    try:
        resolved.relative_to(isolated_root)
    except ValueError as exc:
        raise SmokeError(
            "codex plugin add escaped isolated state: "
            f"{resolved} not under {isolated_root}"
        ) from exc
    return resolved


def assert_sys_path_source_free(
    sys_path: Sequence[str], source_root: Path
) -> list[str]:
    """Reject an interpreter whose effective imports can reach the checkout."""

    source = source_root.resolve()
    canonical: list[str] = []
    escaped: list[str] = []
    for value in sys_path:
        if not value:
            continue
        resolved = Path(value).resolve()
        canonical.append(str(resolved))
        try:
            resolved.relative_to(source)
        except ValueError:
            continue
        escaped.append(str(resolved))
    if escaped:
        raise SmokeError(
            "installed-run Python sys.path reaches the source checkout: "
            + ", ".join(escaped)
        )
    return canonical


def prepare_isolated_probe_python(
    python_executable: Path,
    source_root: Path,
    destination: Path,
    *,
    timeout_seconds: float = 180.0,
) -> tuple[Path, dict[str, Any]]:
    """Create an offline probe venv with no editable/source-root .pth files.

    Installed third-party packages are hard-linked into task-private state, so
    the probe uses the same dependency versions without network access or a
    source-checkout path on its effective sys.path. Bytecode writes are disabled
    by the caller, keeping the shared file inodes read-only in practice.
    """

    _run_checked(
        [str(python_executable), "-m", "venv", "--without-pip", str(destination)],
        cwd=destination.parent,
        env=os.environ,
        label="isolated probe Python creation",
        timeout_seconds=timeout_seconds,
    )
    isolated_python = destination / "bin/python"
    if not isolated_python.is_file():
        isolated_python = destination / "Scripts/python.exe"
    if not isolated_python.is_file():
        raise SmokeError(f"isolated probe Python is missing: {isolated_python}")

    source_site = Path(
        _run_checked(
            [
                str(python_executable),
                "-I",
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ],
            cwd=destination.parent,
            env=os.environ,
            label="source Python site-packages discovery",
            timeout_seconds=timeout_seconds,
        ).stdout.strip()
    ).resolve()
    target_site = Path(
        _run_checked(
            [
                str(isolated_python),
                "-I",
                "-c",
                "import sysconfig; print(sysconfig.get_paths()['purelib'])",
            ],
            cwd=destination.parent,
            env=os.environ,
            label="isolated Python site-packages discovery",
            timeout_seconds=timeout_seconds,
        ).stdout.strip()
    ).resolve()
    _run_checked(
        [
            "rsync",
            "-a",
            "--delete",
            f"--link-dest={source_site}/",
            "--exclude=*.pth",
            "--exclude=__editable__*",
            f"{source_site}/",
            f"{target_site}/",
        ],
        cwd=destination.parent,
        env=os.environ,
        label="isolated Python dependency projection",
        timeout_seconds=timeout_seconds,
    )
    path_probe = _run_checked(
        [
            str(isolated_python),
            "-I",
            "-c",
            "import json, sys; print(json.dumps(sys.path))",
        ],
        cwd=destination.parent,
        env=os.environ,
        label="isolated Python sys.path audit",
        timeout_seconds=timeout_seconds,
    )
    effective_path = assert_sys_path_source_free(
        json.loads(path_probe.stdout.strip()), source_root
    )
    isolated_executable = isolated_python.absolute()
    return isolated_executable, {
        "executable": str(isolated_executable),
        "effective_sys_path": effective_path,
        "source_checkout_paths": [],
        "editable_path_files_excluded": True,
        "dependency_projection": "hardlink",
    }


def run_installed_registered_build(
    installed_root: Path,
    source_root: Path,
    *,
    python_executable: Path,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Resolve and run a tiny build through the installed trusted registry.

    This exercises the installed mesh-to-cad Workspace's registered-tool
    resolver before invoking the installed canonical adapter. The fixture is
    provider-free and produces the full canonical artifact set.
    """

    canonical_entrypoint = assert_entrypoint_under(
        installed_root,
        installed_root / "skills/cad/scripts/canonical-build/__main__.py",
    )
    geometry_entrypoint = assert_entrypoint_under(
        installed_root,
        installed_root / "skills/mesh-compare/scripts/mesh-compare/__main__.py",
    )
    workspace_core = assert_entrypoint_under(
        installed_root,
        installed_root
        / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace_core.py",
    )
    workspace_entrypoint = assert_entrypoint_under(
        installed_root,
        installed_root
        / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/__main__.py",
    )
    env = sanitize_env_for_installed_run(
        installed_root, source_root, python_executable=python_executable
    )
    with tempfile.TemporaryDirectory(prefix="installed-plugin-registered-build-") as temp_text:
        fixture_root = Path(temp_text)
        registry_path = fixture_root / "trusted-tool-registry.json"
        registry = _tool_registry(canonical_entrypoint, geometry_entrypoint)
        registry_path.write_text(
            json.dumps(registry, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
        )

        workspace = fixture_root / "workspace"
        workspace.mkdir()
        _run_checked(
            ["git", "init", "-q", "-b", "develop"],
            cwd=workspace,
            env=env,
            label="registered-build fixture git init",
            timeout_seconds=timeout_seconds,
        )
        _run_checked(
            ["git", "config", "user.name", "Installed Plugin Smoke"],
            cwd=workspace,
            env=env,
            label="registered-build fixture git user",
            timeout_seconds=timeout_seconds,
        )
        _run_checked(
            ["git", "config", "user.email", "smoke@example.invalid"],
            cwd=workspace,
            env=env,
            label="registered-build fixture git email",
            timeout_seconds=timeout_seconds,
        )

        prepared = fixture_root / "prepared"
        reference = b"ply\ninstalled plugin smoke\n"
        (prepared / "input").mkdir(parents=True)
        (prepared / "input/reference.ply").write_bytes(reference)
        reference_identity = "1" * 64
        _write_json_document(
            prepared / "input/input.json",
            {
                "schema": "voxblame.canonical-reference/1",
                "canonical_reference_sha256": reference_identity,
                "reference_ply": {
                    "path": "input/reference.ply",
                    "sha256": hashlib.sha256(reference).hexdigest(),
                },
            },
        )
        (prepared / "setup").mkdir()
        _write_json_document(
            prepared / "experiment.json",
            {
                "schema": "mesh-to-cad.experiment/1",
                "workspace_id": "installed-plugin-smoke",
                "coordinate_contract": "trellis2_canonical/1",
                "canonical_reference_sha256": reference_identity,
                "preview_profile": {
                    "name": "cadena_residual_eight_view/1",
                    "sha256": "2" * 64,
                },
            },
        )
        plan = fixture_root / "initial-plan.json"
        _write_json_document(
            plan,
            {
                "schema": "mesh-to-cad.initial-plan/1",
                "summary": "Build a provider-free installed-plugin fixture.",
            },
        )
        _run_workspace_command(
            python_executable,
            workspace_entrypoint,
            ["init", "--workspace", str(workspace), "--prepared", str(prepared)],
            cwd=fixture_root,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        _run_workspace_command(
            python_executable,
            workspace_entrypoint,
            [
                "begin-attempt",
                "--workspace",
                str(workspace),
                "--plan",
                str(plan),
                "--intended-step",
                "0",
            ],
            cwd=fixture_root,
            env=env,
            timeout_seconds=timeout_seconds,
        )

        source = workspace / "work/attempts/000001/candidate/source/model.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            "from build123d import Box\n\n"
            "def gen_step():\n"
            "    return Box(2, 3, 4)\n"
        )
        source_relative = source.relative_to(workspace).as_posix()
        output_relative = "work/attempts/000001/candidate/artifacts"
        build_args = [
            "build",
            "--workspace",
            str(workspace),
            "--attempt",
            "1",
            "--source",
            source_relative,
            "--output-dir",
            output_relative,
            "--tool-registry",
            str(registry_path),
            "--timeout-seconds",
            str(int(timeout_seconds)),
        ]
        build_result = _run_workspace_command(
            python_executable,
            workspace_entrypoint,
            build_args,
            cwd=fixture_root,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        command_argv = build_result.get("command", {}).get("argv", [])
        if len(command_argv) < 2 or Path(command_argv[1]).resolve() != canonical_entrypoint:
            raise SmokeError(
                "installed Workspace build did not dispatch the registered canonical entrypoint: "
                f"{command_argv!r}"
            )
        required_outputs = (
            "build.json",
            "profile.json",
            "rebuild.json",
            "canonical.step",
            "measurement.glb",
        )
        missing = [
            name
            for name in required_outputs
            if not (workspace / output_relative / name).is_file()
        ]
        if missing:
            raise SmokeError(f"installed registered build omitted outputs: {missing}")

    return {
        "workspace_registry_resolver": str(workspace_core),
        "workspace_entrypoint": str(workspace_entrypoint),
        "registry_path_entrypoint": str(canonical_entrypoint),
        "geometry_entrypoint": str(geometry_entrypoint),
        "resolved_entrypoint": str(Path(command_argv[1]).resolve()),
        "workspace_build_argv": [
            str(python_executable), str(workspace_entrypoint), *build_args
        ],
        "dispatched_build_argv": command_argv,
        "build_exit_code": 0,
        "required_outputs": list(required_outputs),
    }


def _write_json_document(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    )


def _run_checked(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    label: str,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(argv),
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
    )
    if result.returncode != 0:
        raise SmokeError(
            f"{label} failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def _run_workspace_command(
    python_executable: Path,
    workspace_entrypoint: Path,
    arguments: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    result = _run_checked(
        [str(python_executable), "-I", str(workspace_entrypoint), *arguments],
        cwd=cwd,
        env=env,
        label=f"installed Workspace {arguments[0]}",
        timeout_seconds=timeout_seconds,
    )
    try:
        document = json.loads(result.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise SmokeError(
            f"installed Workspace {arguments[0]} returned invalid JSON: {result.stdout!r}"
        ) from exc
    if not document.get("ok"):
        raise SmokeError(
            f"installed Workspace {arguments[0]} returned failure: {document!r}"
        )
    return document


def _tool_registry(canonical_entrypoint: Path, geometry_entrypoint: Path) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "mesh-to-cad.tool-registry/2",
        "rebuild": {
            "id": "cad.canonical-build/1",
            "entrypoint": str(canonical_entrypoint),
            "entrypoint_sha256": _hash_file(canonical_entrypoint),
        },
        "geometry": {
            "id": "mesh-compare.voxblame/1",
            "entrypoint": str(geometry_entrypoint),
            "entrypoint_sha256": _hash_file(geometry_entrypoint),
        },
    }
    body = (json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n").encode()
    value["identity_sha256"] = hashlib.sha256(
        b"mesh-to-cad.tool-registry/2\0" + body
    ).hexdigest()
    return value


def install_plugin_isolated(
    prepared_tree: Path,
    codex_home: Path,
    codex_executable: str = "codex",
    plugin_selector: str = "cad@text-to-cad",
) -> dict[str, Any]:
    """Run the real Codex plugin CLI against an isolated CODEX_HOME.

    Returns the JSON install result. Does not mutate the caller's real state
    because CODEX_HOME points at a task-private directory.
    """

    codex_home.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["CODEX_HOME"] = str(codex_home)
    env.pop("CODEX_PROFILE", None)
    add_market = subprocess.run(
        [codex_executable, "plugin", "marketplace", "add", str(prepared_tree), "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_market.returncode != 0:
        raise SmokeError(
            "codex plugin marketplace add failed:\n"
            f"stdout:\n{add_market.stdout}\n"
            f"stderr:\n{add_market.stderr}"
        )
    marketplace = json.loads(add_market.stdout)
    add_plugin = subprocess.run(
        [codex_executable, "plugin", "add", plugin_selector, "--json"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if add_plugin.returncode != 0:
        raise SmokeError(
            "codex plugin add failed:\n"
            f"stdout:\n{add_plugin.stdout}\n"
            f"stderr:\n{add_plugin.stderr}"
        )
    install = json.loads(add_plugin.stdout)
    installed_path = install.get("installedPath")
    if not installed_path:
        raise SmokeError(f"codex plugin add returned no installedPath: {install!r}")
    isolated_installed_path = assert_installed_path_is_isolated(
        codex_home, Path(installed_path)
    )
    return {
        "marketplace": marketplace,
        "install": install,
        "installed_path": str(isolated_installed_path),
    }


def codex_version(codex_executable: str = "codex") -> str:
    probe = subprocess.run(
        [codex_executable, "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise SmokeError(
            "codex --version failed:\n"
            f"stdout:\n{probe.stdout}\n"
            f"stderr:\n{probe.stderr}"
        )
    return probe.stdout.strip() or probe.stderr.strip()


def git_head_sha(source_root: Path) -> str:
    probe = subprocess.run(
        ["git", "-C", str(source_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        raise SmokeError(
            "git rev-parse HEAD failed under source root:\n"
            f"stdout:\n{probe.stdout}\n"
            f"stderr:\n{probe.stderr}"
        )
    return probe.stdout.strip()


def build_receipt(
    *,
    source_root: Path,
    source_sha: str,
    prepared_tree: Path,
    prepared_manifest: Manifest,
    codex_version_string: str,
    install_result: Mapping[str, Any],
    installed_root: Path,
    installed_manifest: Manifest,
    critical_runtimes: Sequence[Mapping[str, str]],
    registered_build_probe: Mapping[str, Any],
    argv: Sequence[str],
    codex_home: Path,
) -> dict[str, Any]:
    return {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {
            "root": str(source_root.resolve()),
            "git_sha": source_sha,
        },
        "prepared_tree": {
            "root": str(prepared_tree.resolve()),
            "file_count": len(prepared_manifest.entries),
            "digest_sha256": prepared_manifest.digest,
        },
        "codex": {
            "version": codex_version_string,
            "home": str(codex_home.resolve()),
        },
        "install": dict(install_result.get("install", {})),
        "marketplace": dict(install_result.get("marketplace", {})),
        "installed": {
            "root": str(installed_root.resolve()),
            "file_count": len(installed_manifest.entries),
            "digest_sha256": installed_manifest.digest,
        },
        "assertions": {
            "prepared_tree_symlink_free": True,
            "installed_tree_symlink_free": True,
            "manifest_parity": True,
            "critical_runtimes_present": True,
            "entrypoint_under_installed_root": True,
            "source_checkout_hidden_from_installed_run": True,
            "isolated_python_sys_path_source_free": True,
            "registered_build_completed": True,
        },
        "critical_runtimes": [dict(item) for item in critical_runtimes],
        "registered_build_probe": dict(registered_build_probe),
        "argv": list(argv),
        "success": True,
    }


def build_failure_receipt(
    *,
    source_root: Path,
    argv: Sequence[str],
    detail: str,
    partial: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"root": str(source_root.resolve())},
        "argv": list(argv),
        "success": False,
        "error": detail,
    }
    if partial:
        receipt["partial"] = dict(partial)
    return receipt


# ---------------------------------------------------------------------------
# CLI driver
# ---------------------------------------------------------------------------


def _rmtree_force(path: Path) -> None:
    """Remove path even when subtrees are read-only (e.g. node_modules)."""

    def onerror(func, target, exc_info):
        exc = exc_info[1]
        if isinstance(exc, FileNotFoundError):
            return
        try:
            os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        try:
            func(target)
        except OSError:
            pass

    shutil.rmtree(path, onerror=onerror)


def _install_verify(
    *,
    source_root: Path,
    prepared_tree: Path,
    codex_home: Path,
    codex_executable: str,
    python_executable: Path,
    argv: Sequence[str],
) -> dict[str, Any]:
    prepared_manifest = compute_manifest(prepared_tree)
    version_string = codex_version(codex_executable)
    install_result = install_plugin_isolated(
        prepared_tree, codex_home, codex_executable=codex_executable
    )
    installed_root = Path(install_result["installed_path"])
    installed_manifest = compute_manifest(installed_root)
    assert_manifests_equal(prepared_manifest, installed_manifest)
    critical_runtimes = assert_critical_runtimes(installed_root)
    with tempfile.TemporaryDirectory(prefix="installed-plugin-probe-python-") as python_temp:
        isolated_python, python_audit = prepare_isolated_probe_python(
            python_executable,
            source_root,
            Path(python_temp) / "venv",
        )
        registered_probe = run_installed_registered_build(
            installed_root, source_root, python_executable=isolated_python
        )
        registered_probe["python_environment"] = python_audit
    return build_receipt(
        source_root=source_root,
        source_sha=git_head_sha(source_root),
        prepared_tree=prepared_tree,
        prepared_manifest=prepared_manifest,
        codex_version_string=version_string,
        install_result=install_result,
        installed_root=installed_root,
        installed_manifest=installed_manifest,
        critical_runtimes=critical_runtimes,
        registered_build_probe=registered_probe,
        argv=argv,
        codex_home=codex_home,
    )


def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Install a prepared publish tree with the real Codex plugin CLI in "
            "an isolated CODEX_HOME and verify the installed cache matches, "
            "including the previously-omitted symlink runtimes."
        ),
    )
    parser.add_argument(
        "--source-root",
        required=True,
        type=Path,
        help="Repository checkout the smoke is auditing (git rev-parse HEAD source).",
    )
    parser.add_argument(
        "--prepared-tree",
        required=True,
        type=Path,
        help="Symlink-free publish tree produced by finalize-publish-tree.sh.",
    )
    parser.add_argument(
        "--receipt",
        required=True,
        type=Path,
        help="Path to write the auditable JSON receipt.",
    )
    parser.add_argument(
        "--codex",
        default="codex",
        help="Codex CLI executable (default: codex on PATH).",
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        type=Path,
        help="Python interpreter used to invoke the installed CAD entrypoint.",
    )
    parser.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help=(
            "Isolated CODEX_HOME (default: a task-private tempdir removed on exit)."
        ),
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parse_args(argv)
    source_root = args.source_root.resolve()
    prepared_tree = args.prepared_tree.resolve()
    receipt_path = args.receipt.resolve()
    codex_home_arg = args.codex_home.resolve() if args.codex_home is not None else None

    if not source_root.is_dir():
        raise SystemExit(f"source root does not exist: {source_root}")
    if not prepared_tree.is_dir():
        raise SystemExit(f"prepared tree does not exist: {prepared_tree}")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)

    tempdir: Path | None = None
    codex_home: Path
    if codex_home_arg is not None:
        codex_home_arg.mkdir(parents=True, exist_ok=True)
        codex_home = codex_home_arg
    else:
        tempdir = Path(tempfile.mkdtemp(prefix="installed-plugin-smoke-codex-"))
        codex_home = tempdir

    try:
        receipt = _install_verify(
            source_root=source_root,
            prepared_tree=prepared_tree,
            codex_home=codex_home,
            codex_executable=args.codex,
            python_executable=args.python.absolute(),
            argv=argv,
        )
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(f"Installed-plugin smoke receipt: {receipt_path}")
        return 0
    except SmokeError as exc:
        receipt = build_failure_receipt(
            source_root=source_root, argv=argv, detail=str(exc)
        )
        receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        print(str(exc), file=sys.stderr)
        print(f"Failure receipt written: {receipt_path}", file=sys.stderr)
        return 1
    finally:
        if tempdir is not None:
            _rmtree_force(tempdir)


if __name__ == "__main__":
    raise SystemExit(main())
