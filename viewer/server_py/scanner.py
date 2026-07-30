"""CAD directory scanner — produces the raw viewer catalog.

The raw catalog entry shape is ``{file, kind, url, hash, bytes, ...}`` where
``file`` is root-relative, ``url`` is repo-relative (``/seg/seg?v=<token>``) and
gets rewritten to the ``/__cad/asset?file=...`` form later by the backend's
``absolutize_catalog``. ``hash`` is sha256 hex; ``bytes`` is the byte size; the
``?v=`` token is ``base36(size)-base36(mtime_ns)`` (see encoding.file_version).

Encoder semantics are pinned byte-for-byte by tests/golden.json (regenerate with
tests/gen_golden.mjs). Kept deliberately importable WITHOUT cadgen/OCP: the
package-path helpers below mirror ``cadgen.catalog`` (see the drift-guard test
in tests/python/global).
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from xml.etree import ElementTree

from .encoding import base36, encode_uri_component

DEFAULT_VIEWER_ROOT_DIR = ""
CAD_CATALOG_SCHEMA_VERSION = 4

SOURCE_EXTENSIONS = frozenset(
    [".step", ".stp", ".stl", ".3mf", ".glb", ".gcode", ".dxf", ".urdf", ".srdf", ".sdf"]
)
IMPLICIT_CAD_EXTENSIONS = (".implicit.js", ".implicit.mjs")
# Dot-prefixed (hidden) directories are skipped generically by _should_skip_directory;
# this set only needs the non-hidden names.
VIEWER_SKIPPED_DIRECTORIES = frozenset(
    [
        "__cadgen__", "__pycache__", "build", "coverage", "dist", "node_modules", "viewer",
    ]
)
# --- per-folder __cadgen__ render-package paths (mirrors cadgen.catalog) ---
CADGEN_DIRNAME = "__cadgen__"
CADGEN_MODELS_DIRNAME = "models"

# --- drawing package (generated DXF render artifact) ---
DXF_GENERATOR_SUFFIX = ".dxf.py"
DRAWING_DESCRIPTOR_NAME = "drawing.json"
DRAWING_PACKAGE_KIND = "drawing-package"


def is_dxf_generator_path(file_path: str) -> bool:
    return str(file_path or "").lower().endswith(DXF_GENERATOR_SUFFIX)


def is_inside_cadgen_dir(file_path: str) -> bool:
    return CADGEN_DIRNAME in str(file_path or "").split(os.sep)


def is_render_package_path(file_path: str) -> bool:
    # A render-artifact package is the descriptor directory at
    # ``<folder>/__cadgen__/models/<entry-filename>/``, recognized purely by structure.
    p = str(file_path or "")
    if not os.path.basename(p):
        return False
    return (
        os.path.basename(os.path.dirname(p)) == CADGEN_MODELS_DIRNAME
        and os.path.basename(os.path.dirname(os.path.dirname(p))) == CADGEN_DIRNAME
    )


def render_package_dir(entry_path: str) -> str:
    return os.path.join(
        os.path.dirname(entry_path), CADGEN_DIRNAME, CADGEN_MODELS_DIRNAME,
        os.path.basename(entry_path),
    )


def entry_path_for_render_package(package_path: str):
    if not is_render_package_path(package_path):
        return None
    folder = os.path.dirname(os.path.dirname(os.path.dirname(package_path)))
    return os.path.join(folder, os.path.basename(package_path))


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


def _package_descriptor_stats(package_dir: str):
    return _file_stats(os.path.join(package_dir, "assembly.json"))


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
def _is_hidden_name(name: str) -> bool:
    return str(name or "").startswith(".")


def _should_skip_directory(name: str) -> bool:
    return name in VIEWER_SKIPPED_DIRECTORIES or _is_hidden_name(name)


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
        if _is_hidden_name(entry.name):
            continue
        lower_name = entry.name.lower()
        extension = os.path.splitext(entry.name)[1].lower()
        if lower_name.endswith(".step.py") or lower_name.endswith(DXF_GENERATOR_SUFFIX):
            # List generated models/drawings whether or not their render artifact has
            # been built. An unbuilt one reports `needs-build` via /__cad/artifact and
            # is built on demand when opened, so a fresh checkout shows ALL generated
            # entries (the __cadgen__ dir is gitignored), not only the pre-built ones.
            result.append(entry_path)
            continue
        if extension in SOURCE_EXTENSIONS or path_is_implicit_cad_source(entry_path):
            result.append(entry_path)
    return result


def _normalize_manifest_path(value):
    text = str(value or "").strip()
    if not text or "\0" in text:
        return ""
    return text.replace("\\", "/")


def _file_has_python_generator(file_path, generator_name):
    if not generator_name:
        return False
    try:
        with open(file_path, "r", encoding="utf-8") as handle:
            return re.search(r"\b" + re.escape(generator_name) + r"\s*\(", handle.read()) is not None
    except OSError:
        return False


def _xml_root_name(file_path, expected_tag="robot"):
    try:
        for _event, element in ElementTree.iterparse(file_path, events=("start",)):
            if element.tag != expected_tag:
                return None
            return str(element.attrib.get("name") or "").strip()
    except (OSError, ElementTree.ParseError):
        return None
    return None


def _paired_urdf_path_for_srdf(source_path, repo_root):
    # An SRDF pairs with the same-directory URDF whose root <robot name>
    # matches; ambiguity (0 or 2+ matches) yields no pairing.
    del repo_root
    robot_name = _xml_root_name(source_path)
    if not robot_name:
        return None
    directory = os.path.dirname(source_path)
    try:
        candidates = sorted(
            os.path.join(directory, entry)
            for entry in os.listdir(directory)
            if os.path.splitext(entry)[1].lower() == ".urdf"
        )
    except OSError:
        return None
    matches = [candidate for candidate in candidates if _xml_root_name(candidate) == robot_name]
    if len(matches) != 1:
        return None
    return matches[0] if _file_stats(matches[0]) else None


# --- entry builders ---
def create_single_asset_entry(repo_root, root_path, source_path, extension):
    kind = source_format_for_path(source_path, extension)
    asset = asset_for_path(repo_root, source_path)
    file = scan_relative_path(root_path, source_path)
    entry = {
        "file": file,
        "kind": kind,
        "url": (asset or {}).get("url") or _asset_url_for_path(repo_root, source_path),
        "hash": (asset or {}).get("hash") or "",
        "bytes": (asset or {}).get("bytes") or 0,
    }
    if kind == "srdf":
        paired_urdf = _paired_urdf_path_for_srdf(source_path, repo_root)
        if paired_urdf:
            urdf_asset = asset_for_path(repo_root, paired_urdf)
            if urdf_asset:
                entry["relations"] = {
                    "urdf": {"file": scan_relative_path(root_path, paired_urdf), **urdf_asset}
                }
    return entry


def read_drawing_catalog_metadata(package_dir):
    """Read a generated-DXF drawing package descriptor (pure JSON; no cadgen import)."""
    try:
        with open(os.path.join(package_dir, DRAWING_DESCRIPTOR_NAME), "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return {}
    if not isinstance(descriptor, dict) or descriptor.get("kind") != DRAWING_PACKAGE_KIND:
        return {}
    return descriptor


def create_generated_dxf_entry(repo_root, root_path, source_path):
    # A `<name>.dxf.py` drawing generator entry. The render asset is the cached
    # drawing.dxf inside the entry-keyed __cadgen__ package; an unbuilt entry has
    # no asset yet and reports `needs-build` via /__cad/artifact when opened.
    package_dir = render_package_dir(source_path)
    descriptor = read_drawing_catalog_metadata(package_dir)
    dxf_ref = str(descriptor.get("dxf") or "").strip()
    dxf_path = os.path.join(package_dir, dxf_ref) if dxf_ref else ""
    dxf_stats = _file_stats(dxf_path) if dxf_path else None
    entry_ref = scan_relative_path(root_path, source_path)
    if dxf_stats:
        # The descriptor already carries the cached DXF's content hash (dxfHash);
        # re-hashing the file on every catalog scan would put redundant disk I/O
        # on the /__cad hot path.
        version = f"{base36(dxf_stats.st_size)}-{base36(dxf_stats.st_mtime_ns)}"
        url = f"{_asset_url_for_path(repo_root, dxf_path)}?v={encode_uri_component(version)}"
        content_hash = str(descriptor.get("dxfHash") or "").strip() or _sha256_file(dxf_path)
        entry_bytes = int(dxf_stats.st_size)
    else:
        url, content_hash, entry_bytes = "", "", 0
    entry = {
        "file": entry_ref,
        "kind": "dxf",
        "url": url,
        "hash": content_hash,
        "bytes": entry_bytes,
        "sourceKind": "python",
    }
    source = {"file": entry_ref, "sourcePath": entry_ref}
    source_hash = str(descriptor.get("sourceHash") or "").strip()
    if source_hash:
        source["sourceHash"] = source_hash
    entry["source"] = source
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


def read_step_catalog_metadata(package_dir):
    # Component-GLB package: assembly.json IS the index manifest. Read it
    # directly (pure-Python; no OCP).
    if not _package_descriptor_stats(package_dir):
        return {}
    try:
        with open(os.path.join(package_dir, "assembly.json"), "r", encoding="utf-8") as handle:
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
    # the entry is built purely from the on-disk __cadgen__ descriptor (gitignored,
    # built locally) — no OCP. The include_artifact_status=True path (the
    # /__cad/artifact status route) delegates to cadgen and is a later phase.
    if include_artifact_status:
        raise NotImplementedError(
            "STEP artifact-status path pending cadgen delegation (artifact-route phase)"
        )
    entry_is_python = source_path.lower().endswith(".py")
    package_dir = render_package_dir(source_path)
    metadata = read_step_catalog_metadata(package_dir)
    topology = metadata.get("topology")
    package_asset = asset_for_path(repo_root, package_dir)
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
        "url": (package_asset or {}).get("url") or _asset_url_for_path(repo_root, package_dir),
        "hash": (package_asset or {}).get("hash") or "",
        "bytes": (package_asset or {}).get("bytes") or 0,
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
    packages_dir = os.path.join(model_dir, CADGEN_DIRNAME, CADGEN_MODELS_DIRNAME)
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
    if _is_hidden_name(os.path.basename(file_path)):
        # Hidden (dot-prefixed) files are never served: generation locks, temp files, and
        # any leftover pre-__cadgen__ hidden artifacts. Hidden directory components below
        # the served root are rejected by the backend, which knows the root; the basename
        # check here stays root-agnostic so a model root under a hidden absolute path
        # (e.g. a worktree in a dot-directory) still serves.
        return False
    if is_inside_cadgen_dir(file_path):
        return True
    if extension in (".js", ".mjs") and _is_declared_params_sidecar(file_path):
        return True
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
        logical = entry_path_for_render_package(source_path) if is_render_package_path(source_path) else source_path
        extension = os.path.splitext(logical)[1].lower()
        if is_dxf_generator_path(logical):
            entries.append(create_generated_dxf_entry(repo_root, root_path, logical))
        elif extension in (".step", ".stp") or logical.lower().endswith(".step.py"):
            entries.append(create_step_entry(repo_root, root_path, logical, extension, include_artifact_status))
        else:
            entries.append(create_single_asset_entry(repo_root, root_path, source_path, extension))
    from .natural_sort import sort_catalog_entries
    return {"schemaVersion": CAD_CATALOG_SCHEMA_VERSION, "entries": sort_catalog_entries(entries)}
