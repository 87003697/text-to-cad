from __future__ import annotations

import argparse
import ast
import builtins
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import sys
from typing import Any

from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer

from cadpy.catalog import source_from_path
from cadpy.generation import _entry_spec_from_source, run_script_generator
from cadpy.glb import export_canonical_measurement_glb_from_scene
from cadpy.step_export import write_xcaf_doc_step_file
from cadpy.step_scene import load_step_scene, mesh_step_scene, scene_export_shape


BUILD_SCHEMA = "mesh-to-cad.build/1"
RECIPE_SCHEMA = "mesh-to-cad.rebuild-recipe/1"
PROFILE_SCHEMA = "mesh-to-cad.cad-build-profile/1"
ADAPTER_ID = "cad.canonical-build/1"
PROFILE_ID = "cad.trellis2-canonical.voxblame-depth8/1"
LINEAR_DEFLECTION = 2**-11
ANGULAR_DEFLECTION = 0.6
OUTPUT_FILES = {
    "primary": "canonical.step",
    "measurement": "measurement.glb",
    "profile": "profile.json",
    "manifest": "build.json",
    "recipe": "rebuild.json",
}


@dataclass(frozen=True)
class _SourceExecutionPolicy:
    root: Path
    declared_inputs: frozenset[Path]
    declared_import_directories: frozenset[Path]
    output_dir: Path


_ACTIVE_SOURCE_POLICY: ContextVar[_SourceExecutionPolicy | None] = ContextVar(
    "cadpy_canonical_build_source_policy",
    default=None,
)
_AUDIT_HOOK_INSTALLED = False
_SEMANTIC_UNIT_IDENTIFIER = re.compile(
    r"(?:^|_)(?:mm|cm|meters?|metres?|inch(?:es)?|feet|foot|ft)$",
    re.IGNORECASE,
)
_NONDETERMINISTIC_IMPORTS = frozenset(
    {"builtins", "ctypes", "datetime", "importlib", "random", "secrets", "sys", "tempfile", "time", "uuid"}
)
_NONDETERMINISTIC_ATTRIBUTES = frozenset(
    {
        "absolute",
        "argv",
        "cwd",
        "environ",
        "exists",
        "expanduser",
        "getctime",
        "getenv",
        "getmtime",
        "getpid",
        "getppid",
        "getsize",
        "home",
        "hash",
        "id",
        "input",
        "is_dir",
        "is_file",
        "is_symlink",
        "lstat",
        "monotonic",
        "monotonic_ns",
        "perf_counter",
        "perf_counter_ns",
        "process_time",
        "process_time_ns",
        "random",
        "readlink",
        "resolve",
        "samefile",
        "stat",
        "statvfs",
        "time",
        "time_ns",
        "times",
        "uname",
        "urandom",
    }
)
_NONDETERMINISTIC_BUILTINS = frozenset(
    {
        "__builtins__",
        "__import__",
        "compile",
        "delattr",
        "eval",
        "exec",
        "getattr",
        "globals",
        "hash",
        "id",
        "input",
        "locals",
        "setattr",
        "vars",
    }
)
_AUDITED_MUTATION_PATH_INDEXES = {
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.mkfifo": (0,),
    "os.mknod": (0,),
    "os.rename": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (1,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.truncate": (0,),
    "os.utime": (0,),
    "os.setxattr": (0,),
    "os.removexattr": (0,),
}
_SOURCE_MUTATION_FUNCTIONS = tuple(
    sorted(
        {event.removeprefix("os.") for event in _AUDITED_MUTATION_PATH_INDEXES}
        | {
            "fchmod",
            "fchown",
            "ftruncate",
            "lchmod",
            "lchown",
            "removedirs",
            "renames",
            "replace",
            "unlink",
        }
    )
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _canonical_build_audit_hook(event: str, args: tuple[object, ...]) -> None:
    policy = _ACTIVE_SOURCE_POLICY.get()
    if policy is None:
        return
    if event.startswith("socket."):
        raise RuntimeError("canonical build forbids network access during source execution")
    if event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn"}:
        raise RuntimeError("canonical build forbids child processes during source execution")
    if event in {"os.putenv", "os.unsetenv", "os.chdir", "os.fchdir"}:
        raise PermissionError(f"canonical build forbids ambient process mutation: {event}")
    if event in _AUDITED_MUTATION_PATH_INDEXES:
        for index in _AUDITED_MUTATION_PATH_INDEXES[event]:
            if index >= len(args) or not isinstance(args[index], (str, bytes, os.PathLike)):
                raise PermissionError(f"canonical build cannot validate filesystem mutation: {event}")
            raw_path = os.fsdecode(args[index])
            path = Path(raw_path)
            resolved = path.resolve() if path.is_absolute() else (policy.root / path).resolve()
            if not _is_within(resolved, policy.output_dir):
                raise PermissionError(
                    f"canonical build forbids writes outside the output directory: {raw_path}"
                )
        return
    if event in {"os.listdir", "os.scandir"}:
        raw_path = os.fsdecode(args[0] if args else ".")
        path = Path(raw_path)
        resolved = path.resolve() if path.is_absolute() else (policy.root / path).resolve()
        if (
            resolved not in policy.declared_import_directories
            and not _is_within(resolved, policy.output_dir)
        ):
            raise PermissionError(f"canonical build attempted to read undeclared directory: {raw_path}")
        return
    if event.startswith("os."):
        raise PermissionError(f"canonical build forbids unsupported OS operation: {event}")
    if event != "open" or not args or not isinstance(args[0], (str, bytes, os.PathLike)):
        return
    raw_path = os.fsdecode(args[0])
    path = Path(raw_path)
    resolved = path.resolve() if path.is_absolute() else (policy.root / path).resolve()
    raw_mode = args[1] if len(args) > 1 else None
    raw_flags = args[2] if len(args) > 2 else None
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    writes = (
        isinstance(raw_mode, str) and any(character in raw_mode for character in "wax+")
    ) or (
        isinstance(raw_flags, int) and bool(raw_flags & write_flags)
    )
    if writes and not _is_within(resolved, policy.output_dir):
        raise PermissionError(f"canonical build forbids writes outside the output directory: {raw_path}")
    if resolved in policy.declared_inputs or _is_within(resolved, policy.output_dir):
        return
    if resolved == Path("/dev/null"):
        return
    raise PermissionError(f"canonical build attempted to read undeclared input: {raw_path}")


def _install_audit_hook() -> None:
    global _AUDIT_HOOK_INSTALLED
    if not _AUDIT_HOOK_INSTALLED:
        sys.addaudithook(_canonical_build_audit_hook)
        _AUDIT_HOOK_INSTALLED = True


@contextmanager
def _source_execution_policy(*, root: Path, declared_inputs: set[Path], output_dir: Path):
    _install_audit_hook()
    resolved_inputs = frozenset(path.resolve() for path in declared_inputs)
    resolved_input_names = frozenset(str(path) for path in resolved_inputs)
    # Importlib enumerates a module directory before opening a helper. Admit
    # that metadata read only when every physical sibling is a declared input.
    inputs_by_parent: dict[Path, set[Path]] = {}
    for path in resolved_inputs:
        inputs_by_parent.setdefault(path.parent, set()).add(path)
    declared_import_directories: set[Path] = set()
    for parent, sibling_inputs in inputs_by_parent.items():
        try:
            siblings = tuple(parent.iterdir())
        except OSError:
            continue
        if (
            all(not sibling.is_symlink() and sibling.is_file() for sibling in siblings)
            and {sibling.resolve() for sibling in siblings} == sibling_inputs
        ):
            declared_import_directories.add(parent)
    original_environment = dict(os.environ)
    original_hash = builtins.hash
    original_mutation_functions = {
        name: getattr(os, name)
        for name in _SOURCE_MUTATION_FUNCTIONS
        if hasattr(os, name)
    }

    def source_called() -> bool:
        caller_path = os.path.realpath(sys._getframe(2).f_code.co_filename)
        return caller_path in resolved_input_names

    def guarded_hash(value: object) -> int:
        if source_called():
            raise ValueError("canonical CAD source uses forbidden ambient nondeterministic input: hash")
        return original_hash(value)

    def guard_mutation(name: str, function: object):
        def guarded(*args: object, **kwargs: object):
            if source_called():
                raise PermissionError(
                    f"canonical build forbids writes outside the output directory via source API: os.{name}"
                )
            return function(*args, **kwargs)

        return guarded

    os.environ.clear()
    builtins.hash = guarded_hash
    for mutation_name, mutation_function in original_mutation_functions.items():
        setattr(os, mutation_name, guard_mutation(mutation_name, mutation_function))
    token = _ACTIVE_SOURCE_POLICY.set(
        _SourceExecutionPolicy(
            root=root,
            declared_inputs=resolved_inputs,
            declared_import_directories=frozenset(declared_import_directories),
            output_dir=output_dir.resolve(),
        )
    )
    previous_dont_write_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        yield
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode
        _ACTIVE_SOURCE_POLICY.reset(token)
        builtins.hash = original_hash
        for mutation_name, mutation_function in original_mutation_functions.items():
            setattr(os, mutation_name, mutation_function)
        os.environ.clear()
        os.environ.update(original_environment)


def _relative_path(raw_path: str, *, root: Path, label: str, must_exist: bool = False) -> tuple[Path, str]:
    value = str(raw_path or "").strip()
    if not value or "\\" in value:
        raise ValueError(f"{label} must be a non-empty POSIX relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label} must be a confined relative path")
    path = root.joinpath(*pure.parts)
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError(f"{label} escapes the build root")
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent != root.parent):
        raise ValueError(f"{label} must not traverse a symlink")
    if must_exist and not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist: {value}")
    return resolved, pure.as_posix()


def _profile() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": PROFILE_SCHEMA,
        "id": PROFILE_ID,
        "coordinateProfile": "trellis2-canonical",
        "coordinateBounds": [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
        "candidateNormalization": False,
        "candidateAlignment": False,
        "semanticUnitScaling": False,
        "tessellationProfile": "voxblame-depth8",
        "linearDeflection": LINEAR_DEFLECTION,
        "angularDeflection": ANGULAR_DEFLECTION,
        "relativeDeflection": False,
        "stepNominalUnitContext": {
            "unit": "millimetre",
            "meaning": "non-semantic",
            "coordinateScaleApplied": False,
        },
    }
    return {**payload, "digest": _json_digest(payload)}


def _validate_unitless_source_parameters(source_path: Path) -> None:
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path.name)
    except SyntaxError as exc:
        raise ValueError("--source must contain valid Python") from exc
    identifiers = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
    }
    identifiers.update(
        node.arg
        for node in ast.walk(tree)
        if isinstance(node, ast.arg)
    )
    semantic_names = sorted(name for name in identifiers if _SEMANTIC_UNIT_IDENTIFIER.search(name))
    if semantic_names:
        joined = ", ".join(semantic_names)
        raise ValueError(f"canonical CAD source must use unitless parameter names; found: {joined}")

    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        str(node.module).split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    if "socket" in imported_roots:
        raise RuntimeError("canonical build forbids network access during source execution")
    if imported_roots & {"multiprocessing", "subprocess"}:
        raise RuntimeError("canonical build forbids child processes during source execution")

    nondeterministic_imports = sorted(
        imported_roots & _NONDETERMINISTIC_IMPORTS
    )
    ambient_attributes = sorted(
        {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in _NONDETERMINISTIC_ATTRIBUTES
        }
        | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.name in _NONDETERMINISTIC_ATTRIBUTES
        }
    )
    ambient_builtins = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in _NONDETERMINISTIC_BUILTINS
        }
    )
    ambient_specials = sorted(
        {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id in {"__file__", "__name__"}
        }
    )
    nondeterministic_references = [
        *nondeterministic_imports,
        *ambient_attributes,
        *ambient_builtins,
        *ambient_specials,
    ]
    if nondeterministic_references:
        joined = ", ".join(nondeterministic_references)
        raise ValueError(f"canonical CAD source uses forbidden ambient nondeterministic input: {joined}")


def _dependency_versions() -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    for name in ("cadpy", "build123d", "cadquery-ocp"):
        try:
            version = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            version = "unknown"
        dependencies.append({"name": name, "version": version})
    return dependencies


def _runtime_identity() -> dict[str, Any]:
    return {
        "direct": _dependency_versions(),
        "platform": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "machine": platform.machine(),
        },
    }


def _validate_scene(scene: object, *, step_path: Path) -> None:
    prototypes = getattr(scene, "prototype_shapes", {})
    if not prototypes:
        raise RuntimeError(f"STEP contains no CAD geometry: {step_path.name}")
    face_count = 0
    for shape in prototypes.values():
        if shape is None or shape.IsNull():
            raise RuntimeError(f"STEP contains null CAD geometry: {step_path.name}")
        if not BRepCheck_Analyzer(shape).IsValid():
            raise RuntimeError(f"STEP contains invalid BRep geometry: {step_path.name}")
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face_count += 1
            explorer.Next()
    if face_count <= 0:
        raise RuntimeError(f"STEP contains no faces: {step_path.name}")


def _recipe(inputs: list[dict[str, Any]]) -> dict[str, Any]:
    argv_template = [
        "build",
        "--source",
        "{source}",
        "--output-dir",
        "{outputDirectory}",
    ]
    for declared_input in inputs[1:]:
        argv_template.extend(("--input", f"{{input:{declared_input['id']}}}"))
    return {
        "schema": RECIPE_SCHEMA,
        "route": "cad",
        "executable": ADAPTER_ID,
        "entrypoint": "scripts/canonical-build",
        "workingDirectory": ".",
        "network": "forbidden",
        "ambientInputs": "forbidden",
        "filesystem": "declared-inputs-read-only; output-directory-write-only",
        "profile": {"id": PROFILE_ID, "digest": _profile()["digest"]},
        "runtime": _runtime_identity(),
        "inputs": inputs,
        "outputs": [
            {"id": role, "path": path}
            for role, path in OUTPUT_FILES.items()
        ],
        "argvTemplate": argv_template,
        "placeholders": {
            "source": {"kind": "input", "inputId": "source"},
            "outputDirectory": {"kind": "output-directory"},
            "manifest": {"kind": "manifest", "path": OUTPUT_FILES["manifest"]},
            **{
                f"input:{declared_input['id']}": {
                    "kind": "input",
                    "inputId": declared_input["id"],
                }
                for declared_input in inputs[1:]
            },
        },
    }


def _file_record(*, file_id: str, role: str, path: str, resolved_path: Path) -> dict[str, Any]:
    return {
        "id": file_id,
        "role": role,
        "path": path,
        "sha256": _sha256(resolved_path),
        "bytes": resolved_path.stat().st_size,
    }


def _load_recipe(*, root: Path, recipe_path: str) -> dict[str, Any]:
    resolved_path, _relative = _relative_path(recipe_path, root=root, label="--recipe", must_exist=True)
    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("--recipe must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("--recipe must contain a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs or any(not isinstance(item, dict) for item in inputs):
        raise ValueError("--recipe must declare canonical CAD inputs")
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, declared_input in enumerate(inputs):
        if set(declared_input) != {"id", "role", "path", "sha256"}:
            raise ValueError("--recipe input declaration has unsupported fields")
        input_id = declared_input.get("id")
        expected_role = "canonical-cad-source" if index == 0 else "declared-source-input"
        if not isinstance(input_id, str) or not input_id or input_id in seen_ids:
            raise ValueError("--recipe input ids must be unique")
        if index == 0 and input_id != "source":
            raise ValueError("--recipe source declaration is invalid")
        if declared_input.get("role") != expected_role:
            raise ValueError("--recipe input role is invalid")
        declared_path = declared_input.get("path")
        if not isinstance(declared_path, str) or declared_path in seen_paths:
            raise ValueError("--recipe input paths must be unique")
        resolved_input, _ = _relative_path(declared_path, root=root, label="recipe input", must_exist=True)
        if declared_input.get("sha256") != _sha256(resolved_input):
            raise ValueError("--recipe input digest does not match the declared input")
        seen_ids.add(input_id)
        seen_paths.add(declared_path)
    expected_recipe = _recipe(inputs)
    unexpected_fields = sorted(set(payload) - set(expected_recipe))
    missing_fields = sorted(set(expected_recipe) - set(payload))
    if unexpected_fields or missing_fields:
        joined = ", ".join((*unexpected_fields, *missing_fields))
        raise ValueError(f"--recipe has unsupported fields: {joined}")
    for field in (
        "schema",
        "route",
        "executable",
        "entrypoint",
        "workingDirectory",
        "network",
        "ambientInputs",
        "filesystem",
        "profile",
        "runtime",
    ):
        if payload.get(field) != expected_recipe[field]:
            raise ValueError(f"--recipe has unsupported {field}")
    if payload.get("argvTemplate") != expected_recipe["argvTemplate"]:
        raise ValueError("--recipe has an unsupported argv template")
    if payload.get("outputs") != expected_recipe["outputs"]:
        raise ValueError("--recipe output declaration is invalid")
    if payload.get("placeholders") != expected_recipe["placeholders"]:
        raise ValueError("--recipe placeholder declaration is invalid")
    return payload


def build(*, root: Path, source: str, output_dir: str, inputs: list[str] | None = None) -> dict[str, Any]:
    root = root.resolve()
    source_path, source_relative = _relative_path(source, root=root, label="--source", must_exist=True)
    if source_path.suffix.lower() != ".py":
        raise ValueError("--source must name a Python gen_step() source")
    _validate_unitless_source_parameters(source_path)
    output_path, output_relative = _relative_path(output_dir, root=root, label="--output-dir")
    if output_path == root or source_path == output_path or output_path in source_path.parents:
        raise ValueError("--output-dir must be separate from the declared source")
    if output_path.exists() and any(output_path.iterdir()):
        raise ValueError("--output-dir must not contain existing files")
    output_path.mkdir(parents=True, exist_ok=True)

    declared_inputs: list[tuple[Path, str]] = [(source_path, source_relative)]
    seen_inputs = {source_path}
    for raw_input in inputs or []:
        input_path, input_relative = _relative_path(raw_input, root=root, label="--input", must_exist=True)
        if input_path in seen_inputs:
            raise ValueError("--input paths must be unique and must not repeat --source")
        if _is_within(input_path, output_path):
            raise ValueError("--input must not be inside --output-dir")
        if input_path.suffix.lower() == ".py":
            _validate_unitless_source_parameters(input_path)
        declared_inputs.append((input_path, input_relative))
        seen_inputs.add(input_path)

    source_info = source_from_path(source_path)
    if source_info is None:
        raise RuntimeError("--source must define a supported gen_step() entrypoint")
    step_path = output_path / OUTPUT_FILES["primary"]
    spec = replace(
        _entry_spec_from_source(source_info),
        cad_ref=f"{output_relative}/canonical",
        display_name="canonical",
        step_path=step_path,
        mesh_tolerance=LINEAR_DEFLECTION,
        mesh_angular_tolerance=ANGULAR_DEFLECTION,
        mesh_tolerance_explicit=True,
        mesh_angular_tolerance_explicit=True,
    )
    # Initialize the registered CAD runtime before constraining user source
    # I/O. Some dependencies populate their own interpreter cache on import.
    import build123d  # noqa: F401

    with _source_execution_policy(
        root=root,
        declared_inputs={path for path, _relative in declared_inputs},
        output_dir=output_path,
    ):
        generated_scene = run_script_generator(
            spec,
            "gen_step",
            force=True,
            load_current_scene=False,
            skip_step_write=True,
        )
    if generated_scene is None or generated_scene.doc is None:
        raise RuntimeError("canonical CAD source did not produce an exportable XCAF scene")
    source_digest = _sha256(source_path)
    write_xcaf_doc_step_file(
        generated_scene.doc,
        step_path,
        label="canonical",
        text_to_cad_entry_kind=spec.kind,
        source_path=source_relative,
        source_hash=source_digest,
    )

    reread_scene = load_step_scene(step_path)
    _validate_scene(reread_scene, step_path=step_path)
    mesh_step_scene(
        reread_scene,
        linear_deflection=LINEAR_DEFLECTION,
        angular_deflection=ANGULAR_DEFLECTION,
        relative=False,
    )
    scene_export_shape(reread_scene)
    measurement_path = output_path / OUTPUT_FILES["measurement"]
    export_canonical_measurement_glb_from_scene(
        measurement_path,
        reread_scene,
        linear_deflection=LINEAR_DEFLECTION,
        angular_deflection=ANGULAR_DEFLECTION,
    )

    profile = _profile()
    profile_path = output_path / OUTPUT_FILES["profile"]
    _write_json(profile_path, profile)
    recipe_inputs = [
        {
            "id": "source" if index == 0 else f"input-{index}",
            "role": "canonical-cad-source" if index == 0 else "declared-source-input",
            "path": relative,
            "sha256": _sha256(path),
        }
        for index, (path, relative) in enumerate(declared_inputs)
    ]
    recipe = _recipe(recipe_inputs)
    recipe_path = output_path / OUTPUT_FILES["recipe"]
    _write_json(recipe_path, recipe)

    expected_output_paths = {
        (output_path / filename).resolve()
        for role, filename in OUTPUT_FILES.items()
        if role != "manifest"
    }
    actual_output_paths = {
        path.resolve()
        for path in output_path.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    undeclared_outputs = sorted(path.relative_to(output_path).as_posix() for path in actual_output_paths - expected_output_paths)
    if undeclared_outputs:
        raise RuntimeError(f"canonical CAD source produced undeclared output: {', '.join(undeclared_outputs)}")

    files = [
        *[
            _file_record(
                file_id=f"input:{declared_input['id']}",
                role=declared_input["role"],
                path=declared_input["path"],
                resolved_path=path,
            )
            for declared_input, (path, _relative) in zip(recipe_inputs, declared_inputs, strict=True)
        ],
        _file_record(file_id="artifact:primary", role="primary-step", path=OUTPUT_FILES["primary"], resolved_path=step_path),
        _file_record(
            file_id="artifact:measurement",
            role="measurement-glb",
            path=OUTPUT_FILES["measurement"],
            resolved_path=measurement_path,
        ),
        _file_record(file_id="profile", role="frozen-build-profile", path=OUTPUT_FILES["profile"], resolved_path=profile_path),
        _file_record(file_id="recipe", role="offline-rebuild-recipe", path=OUTPUT_FILES["recipe"], resolved_path=recipe_path),
    ]
    by_id = {record["id"]: record for record in files}
    manifest: dict[str, Any] = {
        "schema": BUILD_SCHEMA,
        "route": "cad",
        "entrypoint": "scripts/canonical-build",
        "adapter": {"id": ADAPTER_ID, "version": 1},
        "profile": {"id": PROFILE_ID, "path": OUTPUT_FILES["profile"], "digest": profile["digest"]},
        "primaryArtifact": {
            "fileId": "artifact:primary",
            "path": OUTPUT_FILES["primary"],
            "sha256": by_id["artifact:primary"]["sha256"],
        },
        "measurementGlb": {
            "fileId": "artifact:measurement",
            "path": OUTPUT_FILES["measurement"],
            "sha256": by_id["artifact:measurement"]["sha256"],
        },
        "deliveryRoots": [OUTPUT_FILES["primary"], OUTPUT_FILES["measurement"]],
        "files": files,
        "derivation": [
            {
                "from": "input:source",
                "to": "artifact:primary",
                "operation": "execute-canonical-cad-source",
            },
            {
                "from": "artifact:primary",
                "to": "artifact:measurement",
                "operation": "reread-step-and-tessellate",
                "profileDigest": profile["digest"],
            },
            *[
                {
                    "from": f"input:{declared_input['id']}",
                    "to": "artifact:primary",
                    "operation": "consume-declared-input",
                }
                for declared_input in recipe_inputs[1:]
            ],
        ],
        "dependencies": _runtime_identity(),
        "coordinateContract": {
            "profile": "trellis2-canonical",
            "sourceToStep": "identity",
            "stepToMeasurementGlb": "identity",
            "candidateNormalization": False,
            "candidateAlignment": False,
            "semanticUnitScaling": False,
        },
        "serializationUnit": {
            "format": "STEP",
            "nominal": "millimetre",
            "semantic": False,
            "coordinateScaleApplied": False,
        },
        "recipe": {"path": OUTPUT_FILES["recipe"], "sha256": by_id["recipe"]["sha256"]},
    }
    _write_json(output_path / OUTPUT_FILES["manifest"], manifest)
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="scripts/canonical-build")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser = subparsers.add_parser("build", help="Build canonical CAD delivery artifacts.")
    build_parser.add_argument("--source", required=True, help="Declared cwd-relative Python gen_step() source.")
    build_parser.add_argument("--input", action="append", default=[], help="Additional declared cwd-relative source input.")
    build_parser.add_argument("--output-dir", required=True, help="Empty cwd-relative output directory.")
    rebuild_parser = subparsers.add_parser("rebuild", help="Execute a registered offline CAD rebuild recipe.")
    rebuild_parser.add_argument("--recipe", required=True, help="Cwd-relative registered recipe path.")
    rebuild_parser.add_argument("--output-dir", required=True, help="Empty cwd-relative output directory.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "build":
        manifest = build(root=Path.cwd(), source=args.source, output_dir=args.output_dir, inputs=args.input)
        print(json.dumps(manifest, separators=(",", ":")))
        return 0
    if args.command == "rebuild":
        root = Path.cwd()
        recipe = _load_recipe(root=root, recipe_path=args.recipe)
        source = recipe["inputs"][0]["path"]
        declared_inputs = [item["path"] for item in recipe["inputs"][1:]]
        manifest = build(root=root, source=source, output_dir=args.output_dir, inputs=declared_inputs)
        print(json.dumps(manifest, separators=(",", ":")))
        return 0
    raise AssertionError(f"unsupported command: {args.command}")


__all__ = ["ADAPTER_ID", "BUILD_SCHEMA", "PROFILE_ID", "RECIPE_SCHEMA", "build", "main"]
