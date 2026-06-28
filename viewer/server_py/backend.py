"""Python port of localAssetBackend.mjs (the parts the catalog/asset path needs).

Reproduces root resolution, catalog absolutization (raw scanner URLs ->
``/__cad/asset?file=...`` form the client consumes verbatim), and the guarded
asset-path resolver. STEP artifact generation + export delegate to cadpy and
land in the cadpy-integration phase.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit, parse_qs, unquote

IMPLICIT_EXPORT_FORMATS = ("glb", "stl", "3mf")


def normalize_implicit_export_format(value: str) -> str:
    fmt = str(value or "").strip().lower().lstrip(".")
    if fmt in IMPLICIT_EXPORT_FORMATS:
        return fmt
    raise ValueError(f"Unsupported implicit CAD export format: {value or '(missing)'}")

from . import artifact as artifact_mod
from . import cadpy_bridge
from . import scanner
from .content_types import content_type_for_path
from .save_dialog import pick_save_destination
from .urls import local_asset_url_for_path

_STEP_EXPORT_FORMAT_SUFFIX = {"step": "step", "stl": "stl", "3mf": "3mf", "glb": "glb"}


def _to_posix(value: str) -> str:
    return str(value or "").replace(os.sep, "/")


def absolute_file_ref(file_path: str) -> str:
    return _to_posix(os.path.abspath(file_path))


def relative_file_ref(root_path: str, file_path: str) -> str:
    return _to_posix(os.path.relpath(os.path.abspath(file_path), os.path.abspath(root_path)))


def _path_is_inside_or_equal(child: str, parent: str) -> bool:
    rel = os.path.relpath(os.path.abspath(child), os.path.abspath(parent))
    return scanner.relative_path_stays_inside_root(rel)


def normalized_file_ref(value: str) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    if "\0" in raw:
        raise ValueError("File path contains an invalid null byte")
    return absolute_file_ref(raw) if os.path.isabs(raw) else raw.lstrip("/")


def normalized_root_dir(value: str, base_root: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "\0" in raw:
        raise ValueError("CAD Viewer directory contains an invalid null byte")
    return os.path.abspath(raw) if os.path.isabs(raw) else os.path.abspath(os.path.join(base_root, raw))


def require_directory(root_path: str) -> None:
    if not os.path.isdir(root_path):
        raise ValueError(f"CAD Viewer directory not found: {root_path}")


class ForbiddenAssetError(Exception):
    status_code = 403


def _query_value(raw_url: str, name: str) -> str:
    try:
        params = parse_qs(urlsplit(str(raw_url or "")).query)
        return (params.get(name) or [""])[0]
    except ValueError:
        return ""


def _asset_path_from_catalog_url(scan_repo_root: str, raw_url: str) -> str:
    text = str(raw_url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        explicit_file = (parse_qs(parts.query).get("file") or [""])[0]
        if explicit_file:
            return os.path.abspath(explicit_file)
        return os.path.abspath(os.path.join(scan_repo_root, unquote(parts.path).lstrip("/")))
    except ValueError:
        cleaned = text.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        return os.path.abspath(os.path.join(scan_repo_root, cleaned))


def _absolute_path_from_catalog_value(scan_repo_root: str, value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        return os.path.abspath(text)
    return os.path.abspath(os.path.join(scan_repo_root, text))


def _absolutize_keyed(obj, scan_repo_root: str, keys):
    if not isinstance(obj, dict):
        return obj
    nxt = dict(obj)
    for key in keys:
        if nxt.get(key):
            nxt[key] = absolute_file_ref(_absolute_path_from_catalog_value(scan_repo_root, nxt[key]))
    return nxt


def _absolutize_source(source, scan_repo_root):
    return _absolutize_keyed(source, scan_repo_root, ("file", "path", "sourcePath"))


def _absolutize_source_status(status, scan_repo_root):
    return _absolutize_keyed(status, scan_repo_root, ("sourcePath", "stepPath", "glbPath"))


def _absolutize_artifact(artifact, scan_repo_root):
    return _absolutize_keyed(artifact, scan_repo_root, ("stepPath", "glbPath", "sourcePath", "cadPath"))


def _absolutize_entry(entry: dict, *, root_path: str, scan_repo_root: str, root_dir: str) -> dict:
    output_path = os.path.abspath(os.path.join(root_path, str(entry.get("file") or "")))
    nxt = dict(entry)
    nxt["file"] = absolute_file_ref(output_path)
    nxt["rootRelativeFile"] = relative_file_ref(root_path, output_path)
    if entry.get("url"):
        asset_path = _asset_path_from_catalog_url(scan_repo_root, entry["url"])
        nxt["url"] = local_asset_url_for_path(asset_path, version=_query_value(entry["url"], "v"), root_dir=root_dir)
        nxt["assetFile"] = absolute_file_ref(asset_path)
    if entry.get("moduleUrl"):
        module_path = _asset_path_from_catalog_url(scan_repo_root, entry["moduleUrl"])
        nxt["moduleUrl"] = local_asset_url_for_path(module_path, version=_query_value(entry["moduleUrl"], "v"), root_dir=root_dir)
        nxt["moduleFile"] = absolute_file_ref(module_path)
    if entry.get("source"):
        nxt["source"] = _absolutize_source(entry["source"], scan_repo_root)
    if entry.get("sourceStatus"):
        nxt["sourceStatus"] = _absolutize_source_status(entry["sourceStatus"], scan_repo_root)
    if entry.get("artifact"):
        nxt["artifact"] = _absolutize_artifact(entry["artifact"], scan_repo_root)
    relations = entry.get("relations")
    if isinstance(relations, dict):
        nxt_relations = {}
        for key, relation in relations.items():
            if not isinstance(relation, dict):
                nxt_relations[key] = relation
                continue
            relation_path = os.path.abspath(os.path.join(root_path, str(relation.get("file") or "")))
            nxt_relation = dict(relation)
            nxt_relation["file"] = absolute_file_ref(relation_path)
            nxt_relation["rootRelativeFile"] = relative_file_ref(root_path, relation_path)
            if relation.get("url"):
                rel_asset = _asset_path_from_catalog_url(scan_repo_root, relation["url"])
                nxt_relation["url"] = local_asset_url_for_path(rel_asset, version=_query_value(relation["url"], "v"), root_dir=root_dir)
                nxt_relation["assetFile"] = absolute_file_ref(rel_asset)
            nxt_relations[key] = nxt_relation
        nxt["relations"] = nxt_relations
    return nxt


class LocalAssetBackend:
    kind = "local-fs"

    def __init__(self, directory_root: str, root_dir: str = ""):
        self.base_directory_root = os.path.abspath(directory_root or os.getcwd())
        self.default_root_dir = (
            absolute_file_ref(normalized_root_dir(root_dir, self.base_directory_root))
            if root_dir else absolute_file_ref(self.base_directory_root)
        )

    def _effective_root_dir(self, root_dir: str = "") -> str:
        return root_dir or self.default_root_dir

    def resolve_root(self, root_dir: str = "") -> dict:
        root_path = normalized_root_dir(root_dir or self.default_root_dir, self.base_directory_root)
        if not root_path:
            raise ValueError("CAD Viewer local filesystem requests must include a ?dir= path")
        require_directory(root_path)
        return {"dir": absolute_file_ref(root_path), "rootPath": root_path, "rootName": os.path.basename(root_path)}

    def resolve_request_root(self, root_dir: str = "") -> dict:
        return self.resolve_root(self._effective_root_dir(root_dir))

    def _scan_context(self, resolved_root: dict) -> dict:
        root_path = os.path.abspath(resolved_root["rootPath"])
        inside = _path_is_inside_or_equal(root_path, self.base_directory_root)
        scan_repo_root = self.base_directory_root if inside else root_path
        scan_root_dir = "" if scan_repo_root == root_path else _to_posix(os.path.relpath(root_path, scan_repo_root))
        return {"rootDir": resolved_root["dir"], "rootPath": root_path, "scanRepoRoot": scan_repo_root, "scanRootDir": scan_root_dir}

    def read_catalog(self, root_dir: str = "", file_ref: str = "") -> dict:
        resolved = self.resolve_root(self._effective_root_dir(root_dir))
        ctx = self._scan_context(resolved)
        raw = scanner.scan_cad_directory(ctx["scanRepoRoot"], ctx["scanRootDir"], include_artifact_status=False)
        entries = [
            _absolutize_entry(entry, root_path=ctx["rootPath"], scan_repo_root=ctx["scanRepoRoot"], root_dir=resolved["dir"])
            for entry in raw["entries"]
        ]
        return {"schemaVersion": scanner.CAD_CATALOG_SCHEMA_VERSION, "entries": entries}

    def asset_path_for_file_ref(self, file_ref: str, resolved_root: dict | None = None, root_dir: str = "") -> str | None:
        normalized = normalized_file_ref(file_ref)
        if not normalized or not os.path.isabs(normalized):
            return None
        candidate = os.path.abspath(normalized)
        if not scanner.is_served_cad_asset(candidate):
            return None
        active = resolved_root or (self.resolve_root(root_dir) if root_dir else None)
        if active and not (candidate == active["rootPath"] or scanner.path_is_inside(candidate, active["rootPath"])):
            raise ForbiddenAssetError("Forbidden")
        return candidate

    def content_type_for_path(self, file_path: str) -> str:
        return content_type_for_path(file_path)

    def catalog_entry_for_file_ref(self, catalog, file_ref):
        norm = normalized_file_ref(file_ref)
        if not norm or not isinstance(catalog, dict):
            return None
        for entry in catalog.get("entries", []):
            if normalized_file_ref(entry.get("file")) == norm or normalized_file_ref(entry.get("rootRelativeFile")) == norm:
                return entry
        return None

    def resolve_step_source(self, file_ref, resolved_root):
        normalized = normalized_file_ref(file_ref)
        if not normalized:
            raise ValueError("Missing STEP file")
        if os.path.isabs(normalized):
            candidates = [os.path.abspath(normalized), os.path.abspath(os.path.join(resolved_root["rootPath"], normalized.lstrip("/")))]
        else:
            candidates = [os.path.abspath(os.path.join(resolved_root["rootPath"], normalized))]
        seen = []
        for c in candidates:
            if c in seen:
                continue
            seen.append(c)
            inside = c == resolved_root["rootPath"] or scanner.path_is_inside(c, resolved_root["rootPath"])
            if inside and os.path.exists(c):
                ext = os.path.splitext(c)[1].lower()
                if ext == ".py":
                    stem = os.path.basename(c)[: -len(".py")]
                    step_base = stem if re.search(r"\.(step|stp)$", stem, re.IGNORECASE) else stem + ".step"
                    return {"stepPath": os.path.join(os.path.dirname(c), step_base), "sourcePath": c, "skipStepWrite": True}
                if ext not in (".step", ".stp"):
                    raise ValueError("Only STEP/STP sources or same-stem Python generators can generate STEP topology artifacts")
                return {"stepPath": c, "sourcePath": "", "skipStepWrite": False}
        raise ValueError(f"STEP file not found: {normalized}")

    def artifact_status(self, file_ref, resolved_root, catalog):
        entry = self.catalog_entry_for_file_ref(catalog, file_ref)
        ref = str((entry or {}).get("url") or "")
        if not artifact_mod.owns_entry(entry):
            return {"state": artifact_mod.ARTIFACT_STATE_READY, "ref": ref}
        ctx = self._scan_context(resolved_root)
        try:
            resolved = self.resolve_step_source(file_ref, resolved_root)
        except ValueError as exc:
            return {"state": artifact_mod.ARTIFACT_STATE_ERROR, "error": str(exc)}
        # The render package is keyed by the SOURCE path: the .step.py for a
        # generated model, the .step for an imported one (sourcePath is "" for
        # imported, so this falls back to stepPath).
        artifact_source = resolved.get("sourcePath") or resolved["stepPath"]
        lock = artifact_mod.generation_lock_path(scanner.inline_step_glb_artifact_path_for_source(artifact_source))
        if artifact_mod.generation_lock_active(lock):
            return {"state": artifact_mod.ARTIFACT_STATE_GENERATING, "ref": ref}
        ok, code = artifact_mod.validate_step_freshness(ctx["scanRepoRoot"], artifact_source)
        if ok:
            return {"state": artifact_mod.ARTIFACT_STATE_READY, "ref": ref}
        if code in artifact_mod.BUILDABLE_STEP_ARTIFACT_CODES:
            return {"state": artifact_mod.ARTIFACT_STATE_NEEDS_BUILD, "reason": code, "ref": ref}
        return {"state": artifact_mod.ARTIFACT_STATE_ERROR, "reason": code, "error": code, "ref": ref}

    def _same_stem_python_generator_path(self, step_path):
        ext = os.path.splitext(step_path)[1].lower()
        if ext not in (".step", ".stp"):
            return ""
        candidate = os.path.join(os.path.dirname(step_path), os.path.basename(step_path) + ".py")
        return candidate if scanner._file_has_python_generator(candidate, "gen_step") else ""

    # POST /__cad/artifact build — subprocess cadpy.step_artifact (OCP stays out of
    # the server process), then bump the descriptor mtime so a no-op rebuild clears
    # the scanner's source-mtime staleness trigger (mirrors generateStepArtifact).
    def generate_step_artifact(self, file_ref, force, resolved_root, catalog):
        resolved = self.resolve_step_source(file_ref, resolved_root)
        step_path = resolved["stepPath"]
        ext = os.path.splitext(step_path)[1].lower()
        has_step = ext in (".step", ".stp") and os.path.isfile(step_path)
        generator = "" if has_step else self._same_stem_python_generator_path(step_path)
        has_generator = bool(generator) and os.path.isfile(generator)
        if not has_step and not has_generator:
            raise ValueError("CAD Viewer regenerates GLB artifacts only for existing STEP/STP files or their same-stem Python generators.")
        ctx = self._scan_context(resolved_root)
        args = ["--repo-root", ctx["scanRepoRoot"], "--step", step_path]
        if has_generator:
            # Generated models keep no .step on disk — run the generator in-process and
            # write only the render package (the logical --step path never exists).
            args += ["--source-path", generator, "--skip-step-write"]
        if force:
            args += ["--force"]
        if os.environ.get("VIEWER_STEP_ARTIFACT_VERBOSE") == "1":
            args += ["--verbose"]
        result = cadpy_bridge.run_cadpy("cadpy.step_artifact", args, ctx["scanRepoRoot"])
        if result.get("ok") and result.get("glbPath"):
            glb = result["glbPath"]
            glb_abs = glb if os.path.isabs(glb) else os.path.join(ctx["scanRepoRoot"], glb)
            descriptor = os.path.join(glb_abs, "assembly.json")
            try:
                if os.path.isfile(descriptor):
                    os.utime(descriptor, None)
            except OSError:
                pass
        error = "" if result.get("ok") else str(result.get("error") or "STEP render artifact build failed")
        return {"ok": bool(result.get("ok")), "error": error, "result": result, "stepPath": step_path}

    def resolve_artifact(self, file_ref, force, resolved_root, catalog):
        entry = self.catalog_entry_for_file_ref(catalog, file_ref)
        ref = str((entry or {}).get("url") or "")
        if not artifact_mod.owns_entry(entry):
            return {"ok": True, "state": artifact_mod.ARTIFACT_STATE_READY, "ref": ref}
        try:
            resolved = self.resolve_step_source(file_ref, resolved_root)
        except ValueError as exc:
            return {"ok": False, "state": artifact_mod.ARTIFACT_STATE_ERROR, "error": str(exc)}
        artifact_source = resolved.get("sourcePath") or resolved["stepPath"]
        lock = artifact_mod.generation_lock_path(scanner.inline_step_glb_artifact_path_for_source(artifact_source))
        if not force and artifact_mod.generation_lock_active(lock):
            artifact_mod.await_generation_lock(lock)
            ctx = self._scan_context(resolved_root)
            ok, _code = artifact_mod.validate_step_freshness(ctx["scanRepoRoot"], artifact_source)
            if ok:
                return {"ok": True, "state": artifact_mod.ARTIFACT_STATE_READY, "ref": ref}
        built = self.generate_step_artifact(file_ref, force, resolved_root, catalog)
        if built["ok"]:
            return {"ok": True, "state": artifact_mod.ARTIFACT_STATE_READY, "ref": ref}
        return {"ok": False, "state": artifact_mod.ARTIFACT_STATE_ERROR, "error": built["error"]}

    # POST /__cad/step-export — native Save dialog (subprocess) + cadpy.step_export_target
    # (subprocess). Headless fallback writes beside the source + a download URL.
    def generate_step_export(self, file_ref, fmt, resolved_root, catalog):
        normalized = str(fmt or "").strip().lower()
        if normalized not in _STEP_EXPORT_FORMAT_SUFFIX:
            raise ValueError(f"Unsupported export format: {fmt}")
        resolved = self.resolve_step_source(file_ref, resolved_root)
        step_path = resolved["stepPath"]
        source_path = resolved["sourcePath"]
        if not (step_path == resolved_root["rootPath"] or scanner.path_is_inside(step_path, resolved_root["rootPath"])):
            raise ValueError("Requested file is outside the active CAD Viewer root")
        ctx = self._scan_context(resolved_root)
        base_name = re.sub(r"\.(step|stp)$", "", os.path.basename(step_path), flags=re.IGNORECASE)
        suggested_name = f"{base_name}.{_STEP_EXPORT_FORMAT_SUFFIX[normalized]}"
        default_dir = os.path.dirname(step_path)
        destination = pick_save_destination(suggested_name=suggested_name, default_dir=default_dir,
                                            prompt=f"Export {base_name} as {normalized.upper()}")
        if destination.get("cancelled"):
            return {"ok": False, "cancelled": True}

        def _export(out_path):
            args = ["--repo-root", ctx["scanRepoRoot"], "--step", step_path, "--format", normalized, "--out", out_path]
            if source_path:
                args += ["--source-path", source_path]
            return cadpy_bridge.run_cadpy("cadpy.step_export_target", args, ctx["scanRepoRoot"])

        if destination.get("path"):
            result = _export(os.path.abspath(destination["path"]))
            if not result.get("ok"):
                return {"ok": False, "error": str(result.get("error") or "STEP export failed")}
            out_path = os.path.abspath(result.get("path") or destination["path"])
            inside = out_path == resolved_root["rootPath"] or scanner.path_is_inside(out_path, resolved_root["rootPath"])
            payload = {"ok": True, "path": out_path, "filename": result.get("filename") or os.path.basename(out_path), "format": normalized, "catalogChanged": inside}
            return payload

        # Headless fallback: write beside the source, hand to the browser via /__cad/download.
        output_path = os.path.join(os.path.dirname(step_path), suggested_name)
        if not (output_path == resolved_root["rootPath"] or scanner.path_is_inside(output_path, resolved_root["rootPath"])):
            raise ValueError("Requested file is outside the active CAD Viewer root")
        result = _export(output_path)
        if not result.get("ok"):
            return {"ok": False, "error": str(result.get("error") or "STEP export failed")}
        output_file_ref = _to_posix(os.path.relpath(output_path, resolved_root["rootPath"]))
        return {"ok": True, "fallback": True, "path": output_path, "filename": os.path.basename(output_path),
                "format": normalized, "catalogChanged": True, "outputFileRef": output_file_ref}

    def file_path_from_ref(self, file_ref: str, resolved_root: dict) -> str:
        normalized = normalized_file_ref(file_ref)
        if os.path.isabs(normalized):
            return os.path.abspath(normalized)
        return os.path.abspath(os.path.join(resolved_root["rootPath"], normalized))

    # Geometry runs client-side; this only writes the uploaded bytes beside the
    # source and refreshes the catalog (mirrors the Node generateImplicitExport
    # after the client-side move).
    def generate_implicit_export(self, *, file_ref: str, fmt: str, data: bytes, resolved_root: dict, root_dir: str = "") -> dict:
        export_format = normalize_implicit_export_format(fmt)
        if not data:
            raise ValueError("Missing implicit CAD export payload")
        input_path = self.file_path_from_ref(file_ref, resolved_root)
        out_name = re.sub(r"\.implicit\.(?:mjs|js)$", f".{export_format}", os.path.basename(input_path), flags=re.IGNORECASE)
        out_name = re.sub(r"\.(?:mjs|js)$", f".{export_format}", out_name, flags=re.IGNORECASE)
        output_path = os.path.join(os.path.dirname(input_path), out_name)
        if not (output_path == resolved_root["rootPath"] or scanner.path_is_inside(output_path, resolved_root["rootPath"])):
            raise ValueError("Requested file is outside the active CAD Viewer root")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "wb") as handle:
            handle.write(data)
        catalog = self.read_catalog(root_dir=resolved_root["dir"])
        output_file_ref = _to_posix(os.path.relpath(output_path, resolved_root["rootPath"]))
        entry = next((e for e in catalog["entries"] if e.get("rootRelativeFile") == output_file_ref), None)
        return {
            "ok": True,
            "output": output_path,
            "format": export_format,
            "bytes": len(data),
            "outputFileRef": output_file_ref,
            "filename": os.path.basename(output_path),
            "catalog": catalog,
            "entry": entry,
        }
