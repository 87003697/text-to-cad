"""Python port of cadDirectoryScanner.mjs — produces the raw catalog.

The raw catalog entry shape is ``{file, kind, url, hash, bytes, ...}`` where
``file`` is root-relative, ``url`` is repo-relative (``/seg/seg?v=<token>``) and
gets rewritten to the ``/__cad/asset?file=...`` form later by the backend's
``absolutize_catalog``. ``hash`` is sha256 hex; ``bytes`` is the byte size; the
``?v=`` token is ``base36(size)-base36(mtime_ns)`` (see encoding.file_version).

Verified against Node golden output (tests/catalog_golden.json) for the
single-asset path (implicit/mesh/stl/3mf/glb/gcode/dxf). STEP entries
(``create_step_entry``) and python-generated source status delegate to cadpy
and are completed in the cadpy-integration phase — they are marked below.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re

from .encoding import base36, encode_uri_component

DEFAULT_VIEWER_ROOT_DIR = ""
CAD_CATALOG_SCHEMA_VERSION = 4

SOURCE_EXTENSIONS = frozenset(
    [".step", ".stp", ".stl", ".3mf", ".glb", ".gcode", ".dxf", ".urdf", ".srdf", ".sdf"]
)
IMPLICIT_CAD_EXTENSIONS = (".implicit.js", ".implicit.mjs")
VIEWER_SKIPPED_DIRECTORIES = frozenset(
    [
        ".agents", ".cache", ".viewer", ".git", ".venv", "__cadcache__",
        "__pycache__", "build", "coverage", "dist", "node_modules", "viewer",
    ]
)
PYTHON_GENERATOR_BY_KIND = {
    "dxf": "gen_dxf", "step": "gen_step", "stp": "gen_step",
    "urdf": "gen_urdf", "srdf": "gen_srdf", "sdf": "gen_sdf",
}
_SRDF_URDF_METADATA_PATTERN = re.compile(
    r"""<\s*(?:tcad|explorer):urdf\b[^>]*\bpath\s*=\s*["']([^"']+)["'][^>]*>""",
    re.IGNORECASE,
)

# --- stepSidecars.mjs ---
CACHE_DIRNAME = "__cadcache__"
CACHE_MODELS_DIRNAME = "models"


def is_inside_cad_cache(file_path: str) -> bool:
    return CACHE_DIRNAME in str(file_path or "").split(os.sep)


def is_inline_step_glb_artifact_path(file_path: str) -> bool:
    p = str(file_path or "")
    if not os.path.basename(p):
        return False
    return (
        os.path.basename(os.path.dirname(p)) == CACHE_MODELS_DIRNAME
        and os.path.basename(os.path.dirname(os.path.dirname(p))) == CACHE_DIRNAME
    )


def inline_step_glb_artifact_path_for_source(entry_path: str) -> str:
    return os.path.join(
        os.path.dirname(entry_path), CACHE_DIRNAME, CACHE_MODELS_DIRNAME,
        os.path.basename(entry_path),
    )


def source_path_for_inline_step_glb_artifact(glb_path: str):
    if not is_inline_step_glb_artifact_path(glb_path):
        return None
    folder = os.path.dirname(os.path.dirname(os.path.dirname(glb_path)))
    return os.path.join(folder, os.path.basename(glb_path))


def is_per_step_viewer_directory_name(name: str) -> bool:
    n = str(name or "").lower()
    return n.startswith(".") and (n.endswith(".step") or n.endswith(".stp"))


def is_path_inside_per_step_viewer_directory(file_path: str) -> bool:
    return any(is_per_step_viewer_directory_name(p) for p in str(file_path or "").split(os.sep))


# --- path / ref helpers ---
def to_posix_path(value: str) -> str:
    return str(value or "").replace(os.sep, "/")


def relative_path_stays_inside_root(relative_path: str) -> bool:
    return relative_path == "" or (
        relative_path != ".."
        and not relative_path.startswith(".." + os.sep)
        and not os.path.isabs(relative_path)
    )


def repo_relative_path(repo_root: str, file_path: str) -> str:
    return to_posix_path(os.path.relpath(os.path.abspath(file_path), os.path.abspath(repo_root)))


def scan_relative_path(root_path: str, file_path: str) -> str:
    return to_posix_path(os.path.relpath(os.path.abspath(file_path), os.path.abspath(root_path)))


def path_is_inside(file_path: str, root_path: str) -> bool:
    return relative_path_stays_inside_root(
        os.path.relpath(os.path.abspath(file_path), os.path.abspath(root_path))
    )


def path_is_implicit_cad_source(value: str = "") -> bool:
    pathname = re.split(r"[?#]", str(value or ""), maxsplit=1)[0].lower()
    return any(pathname.endswith(ext) for ext in IMPLICIT_CAD_EXTENSIONS)


def normalize_viewer_root_dir(value: str = DEFAULT_VIEWER_ROOT_DIR) -> str:
    raw = str(value if value is not None else "").strip()
    slash = raw.replace("\\", "/")
    normalized = posixpath.normpath(slash) if slash else ""
    if not normalized or normalized == ".":
        return DEFAULT_VIEWER_ROOT_DIR
    if normalized == ".." or normalized.startswith("../"):
        raise ValueError(f"CAD Viewer root directory must stay inside the directory root: {raw}")
    return normalized.rstrip("/") if normalized != "/" else normalized


def resolve_viewer_root(repo_root: str, root_dir: str = DEFAULT_VIEWER_ROOT_DIR) -> dict:
    normalized_dir = normalize_viewer_root_dir(root_dir)
    resolved_repo_root = os.path.abspath(repo_root)
    root_path = os.path.abspath(os.path.join(resolved_repo_root, normalized_dir)) if normalized_dir else resolved_repo_root
    relative = os.path.relpath(root_path, resolved_repo_root)
    if not relative_path_stays_inside_root(relative):
        raise ValueError(f"CAD Viewer root directory must stay inside the directory root: {normalized_dir}")
    return {
        "dir": normalized_dir,
        "rootPath": root_path,
        "rootName": os.path.basename(root_path) if normalized_dir else os.path.basename(resolved_repo_root),
    }


# --- file stats / hashing / urls ---
def _file_stats(file_path: str):
    try:
        st = os.stat(file_path)
    except OSError:
        return None
    return st if os.path.isfile(file_path) else None


def _sha256_file(file_path: str) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _encode_url_path(repo_relative: str) -> str:
    return "/" + "/".join(encode_uri_component(part) for part in repo_relative.split("/"))


def _package_descriptor_stats(glb_path: str):
    return _file_stats(os.path.join(glb_path, "assembly.json"))


def _step_render_artifact_present(glb_path: str) -> bool:
    return bool(_file_stats(glb_path) or _package_descriptor_stats(glb_path))


def asset_for_path(repo_root: str, file_path: str):
    st = _file_stats(file_path)
    if not st:
        descriptor = _package_descriptor_stats(file_path)
        if descriptor:
            version = f"{base36(descriptor.st_size)}-{base36(descriptor.st_mtime_ns)}"
            repo_path = repo_relative_path(repo_root, file_path)
            return {
                "url": f"{_encode_url_path(repo_path)}?v={encode_uri_component(version)}",
                "hash": _sha256_file(os.path.join(file_path, "assembly.json")),
                "bytes": int(descriptor.st_size),
            }
        return None
    version = f"{base36(st.st_size)}-{base36(st.st_mtime_ns)}"
    repo_path = repo_relative_path(repo_root, file_path)
    return {
        "url": f"{_encode_url_path(repo_path)}?v={encode_uri_component(version)}",
        "hash": _sha256_file(file_path),
        "bytes": int(st.st_size),
    }


def _asset_url_for_path(repo_root: str, file_path: str) -> str:
    return _encode_url_path(repo_relative_path(repo_root, file_path))


# --- classification ---
def _source_format_from_extension(extension: str) -> str:
    normalized = extension.lower().lstrip(".")
    return "stp" if normalized == "stp" else normalized


def source_format_for_path(source_path: str, extension: str | None = None) -> str:
    if extension is None:
        extension = os.path.splitext(source_path)[1]
    return "implicit" if path_is_implicit_cad_source(source_path) else _source_format_from_extension(extension)


# --- directory scan helpers ---
def _is_hidden_directory_name(name: str) -> bool:
    return str(name or "").startswith(".")


def _is_per_urdf_viewer_directory_name(name: str) -> bool:
    n = str(name or "").lower()
    return n.startswith(".") and n.endswith(".urdf")


def _is_path_inside_per_urdf_viewer_directory(file_path: str) -> bool:
    return any(_is_per_urdf_viewer_directory_name(p) for p in str(file_path or "").split(os.sep))


def _should_skip_directory(name: str) -> bool:
    return (
        name in VIEWER_SKIPPED_DIRECTORIES
        or _is_hidden_directory_name(name)
        or is_per_step_viewer_directory_name(name)
        or _is_per_urdf_viewer_directory_name(name)
    )


def _path_has_skipped_directory(root_path: str, file_path: str) -> bool:
    relative = scan_relative_path(root_path, file_path)
    if not relative_path_stays_inside_root(relative):
        return True
    parts = [p for p in relative.split("/") if p]
    return any(_should_skip_directory(part) for part in parts[:-1])


def _collect_cad_source_files(root_path: str, result: list) -> list:
    try:
        entries = sorted(os.scandir(root_path), key=lambda e: e.name)
    except OSError:
        return result
    for entry in entries:
        entry_path = os.path.join(root_path, entry.name)
        if entry.is_dir():
            if not _should_skip_directory(entry.name):
                _collect_cad_source_files(entry_path, result)
            continue
        if not entry.is_file():
            continue
        lower_name = entry.name.lower()
        if lower_name.startswith(".") and (lower_name.endswith(".step.glb") or lower_name.endswith(".stp.glb")):
            continue
        extension = os.path.splitext(entry.name)[1].lower()
        if lower_name.endswith(".step.py"):
            if _step_render_artifact_present(inline_step_glb_artifact_path_for_source(entry_path)):
                result.append(entry_path)
            continue
        if extension in SOURCE_EXTENSIONS or path_is_implicit_cad_source(entry_path):
            result.append(entry_path)
    return result


# --- generated-source status (python-generated dxf/urdf/srdf/sdf) ---
_CADPY_COMMENT_METADATA_RE = re.compile(r"<!--\s*cadpy:([A-Za-z][A-Za-z0-9]*)=(.*?)-->", re.DOTALL)


def _read_xml_textto_cad_metadata(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return {}
    return {m.group(1).strip(): m.group(2).strip() for m in _CADPY_COMMENT_METADATA_RE.finditer(text)}


def _read_dxf_textto_cad_metadata(file_path):
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return {}
    metadata = {}
    for index in range(len(lines) - 1):
        if lines[index].strip() != "999":
            continue
        value = lines[index + 1].strip()
        if not value.startswith("cadpy:"):
            continue
        key, sep, rest = value[len("cadpy:"):].partition("=")
        key = key.strip()
        if sep and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", key):
            metadata[key] = rest.strip()
    return metadata


def _read_generated_file_metadata(file_path, kind):
    normalized = str(kind or "").strip().lower()
    if normalized == "dxf":
        return _read_dxf_textto_cad_metadata(file_path)
    if normalized in ("urdf", "srdf", "sdf"):
        return _read_xml_textto_cad_metadata(file_path)
    # step/stp embedded metadata (readTextToCadStepMetadataFile) is a TODO.
    return {}


def _normalize_manifest_path(value):
    text = str(value or "").strip()
    if not text or "\0" in text:
        return ""
    return text.replace("\\", "/")


def _resolve_manifest_source_path(repo_root, manifest_path, base_dir):
    value = _normalize_manifest_path(manifest_path)
    if not value:
        return None
    resolved = os.path.abspath(value) if os.path.isabs(value) else os.path.abspath(os.path.join(base_dir, value))
    return resolved if path_is_inside(resolved, repo_root) else None


def _file_has_python_generator(file_path, generator_name):
    if not generator_name:
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return re.search(r"\b" + re.escape(generator_name) + r"\s*\(", handle.read()) is not None
    except OSError:
        return False


def _source_path_from_manifest(repo_root, manifest_path, base_dir=None):
    value = _normalize_manifest_path(manifest_path)
    resolved_repo_root = os.path.abspath(repo_root)
    if not value:
        return {"sourcePath": "", "filePath": None}
    roots = []
    seen = set()
    for raw in [base_dir, resolved_repo_root]:
        if not raw:
            continue
        r = os.path.abspath(raw)
        if r not in seen:
            seen.add(r)
            roots.append(r)
    candidates = []
    for root in roots:
        file_path = _resolve_manifest_source_path(resolved_repo_root, value, root)
        if file_path:
            candidates.append({"sourcePath": repo_relative_path(resolved_repo_root, file_path), "filePath": file_path})
    candidate = next((c for c in candidates if _file_stats(c["filePath"])), candidates[0] if candidates else None)
    if candidate:
        return candidate
    return {"sourcePath": value, "filePath": _resolve_manifest_source_path(resolved_repo_root, value, resolved_repo_root)}


def _generator_source_path_from_metadata(repo_root, manifest_path, generator_name, base_dir):
    candidate = _source_path_from_manifest(repo_root, manifest_path, base_dir=base_dir)
    fp = candidate.get("filePath")
    if (
        candidate.get("sourcePath")
        and fp
        and os.path.splitext(fp)[1].lower() == ".py"
        and os.path.basename(fp) != "__init__.py"
        and _file_has_python_generator(fp, generator_name)
    ):
        return candidate
    return {"sourcePath": "", "filePath": None}


def _generated_source_status_for_file(repo_root, source_path, kind):
    generator_name = PYTHON_GENERATOR_BY_KIND.get(str(kind or "").strip().lower(), "")
    if not generator_name:
        return None
    metadata = _read_generated_file_metadata(source_path, str(kind or "").strip().lower())
    metadata_source_path = str(metadata.get("sourcePath", "")).strip()
    if not metadata_source_path:
        return None
    identity = _generator_source_path_from_metadata(repo_root, metadata_source_path, generator_name, os.path.dirname(source_path))
    base_source = {
        "file": identity["sourcePath"] or metadata_source_path,
        "sourcePath": identity["sourcePath"] or metadata_source_path,
    }
    if metadata.get("sourceHash"):
        base_source["sourceHash"] = str(metadata["sourceHash"])
    if not identity["filePath"]:
        return {
            "sourceKind": "python",
            "source": base_source,
            "sourceStatus": {
                "ok": False,
                "status": "missing",
                "stale": False,
                "sourceKind": "python",
                "sourcePath": metadata_source_path,
                "message": "Python generator source is unavailable.",
            },
        }
    return {
        "sourceKind": "python",
        "source": {
            "file": identity["sourcePath"],
            "sourcePath": identity["sourcePath"],
            "sourceHash": str(metadata.get("sourceHash", "")),
        },
    }


def _linked_urdf_path_for_srdf(source_path, repo_root):
    try:
        with open(source_path, "r", encoding="utf-8") as handle:
            xml_text = handle.read()
    except OSError:
        return None
    match = _SRDF_URDF_METADATA_PATTERN.search(xml_text)
    raw_ref = (match.group(1) if match else "").strip()
    if not raw_ref or "\\" in raw_ref or raw_ref.startswith("/"):
        return None
    resolved = os.path.abspath(os.path.join(os.path.dirname(source_path), raw_ref))
    relative_to_repo = os.path.relpath(resolved, os.path.abspath(repo_root))
    if not relative_path_stays_inside_root(relative_to_repo) or os.path.splitext(resolved)[1].lower() != ".urdf":
        return None
    return resolved if _file_stats(resolved) else None


# --- entry builders ---
def create_single_asset_entry(repo_root, root_path, source_path, extension):
    kind = source_format_for_path(source_path, extension)
    asset = asset_for_path(repo_root, source_path)
    file = scan_relative_path(root_path, source_path)
    generated = _generated_source_status_for_file(repo_root=repo_root, source_path=source_path, kind=kind)
    entry = {
        "file": file,
        "kind": kind,
        "url": (asset or {}).get("url") or _asset_url_for_path(repo_root, source_path),
        "hash": (asset or {}).get("hash") or "",
        "bytes": (asset or {}).get("bytes") or 0,
    }
    if generated and generated.get("sourceKind") == "python":
        entry["sourceKind"] = "python"
    if generated and generated.get("source"):
        entry["source"] = generated["source"]
    if generated and generated.get("sourceStatus"):
        entry["sourceStatus"] = generated["sourceStatus"]
    if kind == "srdf":
        linked_urdf = _linked_urdf_path_for_srdf(source_path, repo_root)
        if linked_urdf:
            urdf_asset = asset_for_path(repo_root, linked_urdf)
            if urdf_asset:
                entry["relations"] = {
                    "urdf": {"file": scan_relative_path(root_path, linked_urdf), **urdf_asset}
                }
    return entry


def step_kind_from_topology(topology):
    if not topology:
        return "part"
    index = topology.get("index") if isinstance(topology.get("index"), dict) else topology
    if topology.get("entryKind") == "assembly" or (isinstance(index, dict) and index.get("entryKind") == "assembly"):
        return "assembly"
    assembly = index.get("assembly") if isinstance(index, dict) else None
    if isinstance(assembly, dict) and isinstance(assembly.get("root"), dict):
        return "assembly"
    return "part"


def read_step_catalog_metadata(glb_path):
    # Component-GLB package: assembly.json IS the index manifest. Read it
    # directly (pure-Python; no OCP). The monolith-GLB embedded-topology path is
    # a TODO — current models use component packages.
    if not (_package_descriptor_stats(glb_path) and not _file_stats(glb_path)):
        return {}
    try:
        with open(os.path.join(glb_path, "assembly.json"), "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not descriptor or descriptor.get("kind") != "assembly-package":
        return {}
    source_kind = "python" if str(descriptor.get("sourceKind", "step")).strip().lower() == "python" else "step"
    return {
        "topology": {
            "index": descriptor,
            "entryKind": str(descriptor.get("entryKind", "")).strip().lower(),
            "hasSelector": False,
            "hasDisplayEdges": False,
        },
        "sourceKind": source_kind,
        "sourceHash": str(descriptor.get("sourceHash", "")),
        "stepHash": str(descriptor.get("stepHash", "")),
    }


def create_step_entry(repo_root, root_path, source_path, extension, include_artifact_status=True):
    # Catalog path runs with include_artifact_status=False (refreshCatalog), so
    # the entry is built purely from the committed __cadcache__ descriptor — no
    # OCP. The include_artifact_status=True path (the /__cad/artifact status
    # route) delegates to cadpy and is a later phase.
    if include_artifact_status:
        raise NotImplementedError(
            "STEP artifact-status path pending cadpy delegation (artifact-route phase)"
        )
    entry_is_python = source_path.lower().endswith(".py")
    glb_path = inline_step_glb_artifact_path_for_source(source_path)
    metadata = read_step_catalog_metadata(glb_path)
    topology = metadata.get("topology")
    glb_asset = asset_for_path(repo_root, glb_path)
    topology_index = topology.get("index") if (topology and isinstance(topology.get("index"), dict)) else topology
    params_path_rel = str((topology_index or {}).get("paramsPath", "")).strip()
    step_module_asset = (
        asset_for_path(repo_root, os.path.abspath(os.path.join(os.path.dirname(source_path), params_path_rel)))
        if params_path_rel else None
    )
    entry_ref = scan_relative_path(root_path, source_path)
    source_hash = str(metadata.get("sourceHash", "")).strip()
    entry = {
        "file": entry_ref,
        "kind": step_kind_from_topology(topology),
        "url": (glb_asset or {}).get("url") or _asset_url_for_path(repo_root, glb_path),
        "hash": (glb_asset or {}).get("hash") or "",
        "bytes": (glb_asset or {}).get("bytes") or 0,
        "sourceKind": "python" if entry_is_python else "step",
    }
    if entry_is_python:
        source = {"file": entry_ref, "sourcePath": entry_ref}
        if source_hash:
            source["sourceHash"] = source_hash
        entry["source"] = source
    if step_module_asset:
        entry["moduleUrl"] = step_module_asset["url"]
    return entry


# --- served-asset gate (security: which on-disk files /__cad/asset may serve) ---
def _is_declared_params_sidecar(file_path: str) -> bool:
    model_dir = os.path.dirname(file_path)
    resolved = os.path.abspath(file_path)
    packages_dir = os.path.join(model_dir, CACHE_DIRNAME, CACHE_MODELS_DIRNAME)
    try:
        names = os.listdir(packages_dir)
    except OSError:
        return False
    for name in names:
        descriptor_path = os.path.join(packages_dir, name, "assembly.json")
        if not os.path.isdir(os.path.join(packages_dir, name)):
            continue
        try:
            with open(descriptor_path, "r", encoding="utf-8") as handle:
                descriptor = json.load(handle)
        except (OSError, ValueError):
            continue
        params_path = str((descriptor or {}).get("paramsPath") or "").strip()
        if params_path and os.path.abspath(os.path.join(model_dir, params_path)) == resolved:
            return True
    return False


def is_served_cad_asset(file_path: str) -> bool:
    extension = os.path.splitext(file_path)[1].lower()
    name = os.path.basename(file_path)
    if name.startswith(".") and name.endswith(".generation.lock.json"):
        return False
    if is_inside_cad_cache(file_path):
        return True
    if extension in (".js", ".mjs") and _is_declared_params_sidecar(file_path):
        return True
    if is_path_inside_per_step_viewer_directory(file_path):
        return False
    if _is_path_inside_per_urdf_viewer_directory(file_path):
        return False
    if extension in SOURCE_EXTENSIONS or path_is_implicit_cad_source(file_path):
        return True
    return False


# --- public scan API ---
def scan_cad_directory(repo_root, root_dir=DEFAULT_VIEWER_ROOT_DIR, include_artifact_status=True):
    if not repo_root:
        raise ValueError("repo_root is required")
    resolved = resolve_viewer_root(repo_root, root_dir)
    root_path = resolved["rootPath"]
    source_files = _collect_cad_source_files(root_path, [])
    entries = []
    for source_path in source_files:
        logical = source_path_for_inline_step_glb_artifact(source_path) if is_inline_step_glb_artifact_path(source_path) else source_path
        extension = os.path.splitext(logical)[1].lower()
        if extension in (".step", ".stp") or logical.lower().endswith(".step.py"):
            entries.append(create_step_entry(repo_root, root_path, logical, extension, include_artifact_status))
        else:
            entries.append(create_single_asset_entry(repo_root, root_path, source_path, extension))
    from .natural_sort import sort_catalog_entries
    return {"schemaVersion": CAD_CATALOG_SCHEMA_VERSION, "entries": sort_catalog_entries(entries)}
