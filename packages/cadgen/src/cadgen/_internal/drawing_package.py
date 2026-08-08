"""Drawing-package render artifact for DXF entries.

The DXF analog of the component-GLB package: a `<name>.dxf.py` generator's build
product — or an imported `<name>.dxf`'s — is a self-contained package directory under
the model folder's ``__cadgen__/models/``, keyed by the entry filename:

    <model-folder>/__cadgen__/models/<name>.dxf.py/
      drawing.json    # descriptor: provenance + freshness metadata
      drawing.dxf     # the drawing itself (exchange artifact)
      preview.glb     # the baked 3D flat pattern (render artifact)

**Two payloads with different jobs.** ``drawing.dxf`` is the exchange artifact (exported,
downloaded, re-imported); ``preview.glb`` is what the viewport renders. The GLB is not an
optimization: with the 2D SVG view deleted, the 3D mesh is the ONLY DXF view, and baking it
here is what lets the browser stop carrying ~1,800 lines of DXF parsing and extrusion
(design/unified-glb-render-artifacts.md §7.4.2). It is built by a Node child of whichever
process holds this package's generation lock, because the mesher is JS and is reused verbatim
rather than reimplemented.

The descriptor carries the same provenance the assembly package records
(``sourceKind``/``sourcePath``/``sourceHash``/``sourceClosureHash``/``sourceClosureFiles``/
``generatedAt``) plus the preview's ``bake``/``bakeHash``. The viewer's freshness gate reads it
exactly as it reads ``assembly.json``.

**Write order is load-bearing:** the descriptor is removed first, the payloads are written,
and the descriptor is written LAST. A reader validates the descriptor *and* every payload it
names, so a failed build leaves a package with no descriptor (``needs-build``) rather than one
claiming a preview that is missing or half-written.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from cadgen._internal.file_metadata import text_to_cad_identity_metadata, write_dxf_text_to_cad_metadata
from cadgen._internal.node_runtime import node_builder_script, run_node_builder
from cadgen._internal.package_freshness import (
    bake_hash_matches,
    canonical_bake_hash,
    schema_version_matches,
)
from cadgen._internal.source_hash import (
    PythonSourceClosure,
    closure_hash_matches,
    python_source_hash,
)
from cadgen.catalog import render_package_dir
from cadgen.render import relative_to_file, sha256_file

DRAWING_PACKAGE_KIND = "drawing-package"
# Bumped from 1 when the package gained ``preview.glb``. This is the stack's single
# invalidation channel (viewer/server_py/artifact.py, package_freshness): every drawing
# package written before the preview reports unsupported and rebuilds once, lazily.
DRAWING_PACKAGE_SCHEMA_VERSION = 2
DRAWING_DESCRIPTOR_NAME = "drawing.json"
DRAWING_DXF_NAME = "drawing.dxf"
DRAWING_PREVIEW_NAME = "preview.glb"

# The Node builder that turns drawing.dxf into preview.glb.
DRAWING_PREVIEW_BUILDER = "dxf-artifact.mjs"

# --- the preview bake -----------------------------------------------------------------
# What the build FROZE into preview.glb. Nothing else in the freshness stack can see these:
# the source is unchanged, the payloads are present, the closure still hashes. So they are
# canonicalized into the descriptor's ``bakeHash`` and compared by BOTH freshness
# authorities -- this module's ``drawing_package_current`` (which decides a build no-ops) and
# the viewer's spec table (which decides what a status GET answers). Changing any value here
# invalidates every drawing package, exactly once, on next open.
#
# The thickness is the producer's, not the drawing's: ``parseDxf`` reports
# ``defaultThicknessMm`` and the mesher falls back to this when it is 0, which it always is
# today. Owning the number here is what makes it hashable at all -- the viewer's validator
# must not parse a DXF to answer a status request.
DEFAULT_PREVIEW_THICKNESS_MM = 2.0
# Mirrors DXF_PREVIEW_BAKE_FORMAT in packages/cadjs/src/lib/dxf/previewGlb.js. Bump it there
# and here together when the baked geometry's contract changes.
# v2: CAD Z-up positions (see previewGlb.js). Bumping invalidates every v1 package, which
# is the point: a v1 preview.glb is geometrically wrong, not merely old.
DRAWING_PREVIEW_BAKE_FORMAT = "dxf-preview-glb-v2"
# The bend state the preview is baked in. Bend angles were a live client control over a mesh
# rebuilt in the browser; baked, the flat (unfolded) pattern is what ships (§7.4.3).
DRAWING_PREVIEW_STATE = "flat"


def drawing_preview_bake_settings() -> dict[str, object]:
    """The preview settings this producer bakes, for :func:`canonical_bake_hash`.

    A function rather than a constant so both authorities read the CURRENT values at call
    time -- the viewer's spec table stores this callable, and this module calls it -- which is
    what keeps them from drifting apart by one edit.
    """
    return {
        "defaultThicknessMm": DEFAULT_PREVIEW_THICKNESS_MM,
        "format": DRAWING_PREVIEW_BAKE_FORMAT,
        "state": DRAWING_PREVIEW_STATE,
    }


def drawing_descriptor_path(package_dir: Path) -> Path:
    return Path(package_dir) / DRAWING_DESCRIPTOR_NAME


def drawing_dxf_path(package_dir: Path) -> Path:
    return Path(package_dir) / DRAWING_DXF_NAME


def drawing_preview_path(package_dir: Path) -> Path:
    return Path(package_dir) / DRAWING_PREVIEW_NAME


def load_drawing_descriptor(package_dir: Path) -> dict[str, object] | None:
    try:
        with drawing_descriptor_path(package_dir).open("r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
    except (OSError, ValueError):
        return None
    if not isinstance(descriptor, dict) or descriptor.get("kind") != DRAWING_PACKAGE_KIND:
        return None
    return descriptor


@contextlib.contextmanager
def _deterministic_dxf_output(document: object):
    """Make ezdxf output a pure function of the drawing content while saving.

    ezdxf stamps volatile metadata into every file (julian-date headers, random
    fingerprint/version GUIDs, created/written-by markers with timestamps), which
    would churn the package's dxfHash on every rebuild. The cached drawing is a
    content-addressed render artifact — provenance lives in drawing.json — so it
    is saved with ezdxf's fixed-metadata mode plus a pinned ezdxf marker string
    (the written-by marker is stamped unconditionally on export and is not
    covered by the fixed-metadata option): identical geometry produces identical
    bytes."""
    try:
        import ezdxf
        from ezdxf import document as ezdxf_document
    except ImportError:
        yield
        return
    options = ezdxf.options
    previous_fixed = bool(getattr(options, "write_fixed_meta_data_for_testing", False))
    # Tolerate ezdxf API drift: if the marker hook disappears, output degrades to
    # "written-by marker not pinned" instead of every build crashing.
    previous_marker = getattr(ezdxf_document, "ezdxf_marker_string", None)
    fixed_marker = f"{ezdxf.__version__} @ 2000-01-01T00:00:00+00:00"
    previous_created: str | None = None
    metadata = None
    options.write_fixed_meta_data_for_testing = True
    try:
        if previous_marker is not None:
            ezdxf_document.ezdxf_marker_string = lambda: fixed_marker
        metadata_reader = getattr(document, "ezdxf_metadata", None)
        if callable(metadata_reader):
            metadata = metadata_reader()
            # The created-by marker was stamped when the generator constructed the
            # document (before this save path had any control); pin it for the
            # cached package write and RESTORE it afterwards so later saves of the
            # same document (e.g. a --dxf export) keep real provenance.
            try:
                previous_created = metadata[ezdxf_document.CREATED_BY_EZDXF]
            except Exception:
                previous_created = None
            metadata[ezdxf_document.CREATED_BY_EZDXF] = fixed_marker
        yield
    finally:
        options.write_fixed_meta_data_for_testing = previous_fixed
        if previous_marker is not None:
            ezdxf_document.ezdxf_marker_string = previous_marker
        if metadata is not None and previous_created is not None:
            metadata[ezdxf_document.CREATED_BY_EZDXF] = previous_created


def _open_package(package_dir: Path) -> Path:
    """Create ``package_dir`` and REMOVE its descriptor.

    From here until the descriptor is rewritten the package reports needs-build to every
    reader, which is exactly right: its payloads are being replaced. Dropping it first is
    what makes "a failed build leaves no descriptor" true of a REBUILD too -- otherwise a
    Node-side failure would leave the previous descriptor standing over a freshly written
    ``drawing.dxf`` and a stale (or absent) ``preview.glb``.
    """
    package_dir.mkdir(parents=True, exist_ok=True)
    drawing_descriptor_path(package_dir).unlink(missing_ok=True)
    return package_dir


def build_drawing_preview(
    package_dir: Path,
    *,
    run: object,
    name: str = "",
) -> dict[str, object]:
    """Build ``preview.glb`` from the package's ``drawing.dxf``, in a Node child.

    ``run`` is the :class:`~cadgen.coordination.BuildRun` holding this package's write lock.
    Its run id is handed to the child, which checks it against the lock sentinel before
    writing anything -- so ONE run id, one status record and one progress bar span both
    runtimes, and a builder started outside the lock throws (see
    ``implicitjs/glb/assertWriteLock.js``).

    Raises on any Node-side failure; the caller must then leave no descriptor behind.
    """
    package_dir = Path(package_dir)
    dxf_path = drawing_dxf_path(package_dir)
    if not dxf_path.is_file():
        raise RuntimeError(f"Drawing package has no DXF to build a preview from: {package_dir}")
    run_id = str(getattr(run, "run_id", "") or "")
    if not run_id:
        # No run id means no lock was taken, and the child would refuse the write anyway.
        # Failing here says WHY, in the language of the producer rather than the sentinel.
        raise RuntimeError(
            "Building a drawing package requires the artifact_build run that holds its write "
            f"lock; got run={run!r} for {package_dir}"
        )
    payload = run_node_builder(
        node_builder_script(DRAWING_PREVIEW_BUILDER),
        [
            "--package-dir", str(package_dir),
            "--run-id", run_id,
            "--thickness-mm", repr(float(DEFAULT_PREVIEW_THICKNESS_MM)),
            "--name", name or package_dir.name,
        ],
        run=run,
    )
    if not payload.get("ok"):
        raise RuntimeError(f"DXF preview build failed: {package_dir}")
    if str(payload.get("runId") or "") != run_id:
        # The child echoes the id it verified against the sentinel. A mismatch means the
        # process that wrote the payload was not the one holding the lock.
        raise RuntimeError(
            f"DXF preview builder reported a different run id than the lock holder: {package_dir}"
        )
    if not drawing_preview_path(package_dir).is_file():
        raise RuntimeError(f"DXF preview builder wrote no {DRAWING_PREVIEW_NAME}: {package_dir}")
    return payload


def _write_descriptor(package_dir: Path, descriptor: dict[str, object]) -> dict[str, object]:
    """Write ``drawing.json`` LAST, atomically. Temp + replace so a reader sees the old
    descriptor or the new one, never a partial parse."""
    descriptor_path = drawing_descriptor_path(package_dir)
    temp_path = descriptor_path.with_name(f".{descriptor_path.name}.tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(descriptor, handle, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp_path, descriptor_path)
    return descriptor


def write_drawing_package(
    document: object,
    *,
    script_path: Path,
    source_closure: PythonSourceClosure | None,
    run: object = None,
) -> dict[str, object]:
    """Save ``document`` (an ezdxf document) into the generator's drawing package, bake its
    3D preview, and write the descriptor. Returns the descriptor dict.

    Called from inside ``run_script_generator``, which every drawing producer -- the ``dxf``
    gen CLI and ``cadgen.dxf_artifact`` alike -- runs under ``artifact_build``. Building the
    preview HERE rather than in each producer is what stops a second producer from writing a
    package that can never be current."""
    saveas = getattr(document, "saveas", None)
    if not callable(saveas):
        raise TypeError(
            f"gen_dxf() envelope field 'document' must be a DXF document, got {type(document).__name__}"
        )
    resolved_script = script_path.resolve()
    package_dir = _open_package(render_package_dir(resolved_script))
    dxf_path = drawing_dxf_path(package_dir)
    with _deterministic_dxf_output(document):
        saveas(str(dxf_path))
    source_identity = python_source_hash(resolved_script)
    # The identity comment inside the cached DXF records the generator relative to the
    # DXF file itself (inside __cadgen__/models/<key>/), so the package stays portable.
    write_dxf_text_to_cad_metadata(
        dxf_path,
        text_to_cad_identity_metadata(
            source_path=relative_to_file(resolved_script, dxf_path),
            source_hash=source_identity.source_hash,
        ),
    )
    preview = build_drawing_preview(package_dir, run=run, name=resolved_script.name)
    descriptor: dict[str, object] = {
        "kind": DRAWING_PACKAGE_KIND,
        "packageSchemaVersion": DRAWING_PACKAGE_SCHEMA_VERSION,
        "sourceKind": "python",
        # Like the assembly package, sourcePath/sourceClosureFiles are relative to the
        # MODEL folder (the directory holding the .dxf.py), not the __cadgen__ dir.
        "sourcePath": resolved_script.name,
        "sourceHash": source_identity.source_hash,
        "dxf": DRAWING_DXF_NAME,
        "dxfHash": sha256_file(dxf_path),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **_preview_descriptor_fields(preview),
    }
    if source_closure is not None and source_closure.files:
        descriptor["sourceClosureHash"] = source_closure.closure_hash
        descriptor["sourceClosureFiles"] = list(source_closure.files)
    return _write_descriptor(package_dir, descriptor)


def write_imported_drawing_package(
    dxf_path: Path,
    *,
    run: object = None,
) -> dict[str, object]:
    """Build the drawing package for an IMPORTED ``.dxf`` and return the descriptor.

    Same package, same payloads, same validator; the only differences are that there is no
    generator to run (the DXF is copied in rather than saved out) and that freshness is the
    imported kind -- a plain content digest of the source file rather than a source closure.
    This mirrors an imported ``.step`` exactly: the VIEWER owns and builds it on demand, while
    the gen CLI stays ``.dxf.py``-only (design §0.1)."""
    resolved_dxf = Path(dxf_path).expanduser().resolve()
    if not resolved_dxf.is_file():
        raise FileNotFoundError(f"DXF file does not exist: {resolved_dxf}")
    package_dir = _open_package(render_package_dir(resolved_dxf))
    cached_dxf = drawing_dxf_path(package_dir)
    # Copied, not referenced: the descriptor names its payloads and every one of them must be
    # inside the package for the validator (and for a package that outlives a moved source).
    shutil.copyfile(resolved_dxf, cached_dxf)
    preview = build_drawing_preview(package_dir, run=run, name=resolved_dxf.name)
    descriptor: dict[str, object] = {
        "kind": DRAWING_PACKAGE_KIND,
        "packageSchemaVersion": DRAWING_PACKAGE_SCHEMA_VERSION,
        "sourceKind": "dxf",
        "sourcePath": resolved_dxf.name,
        # The imported digest the spec table names (`source_digest_field`). Fails closed:
        # a descriptor with no digest for a file that is right there is not current.
        "sourceDigest": sha256_file(resolved_dxf),
        "dxf": DRAWING_DXF_NAME,
        "dxfHash": sha256_file(cached_dxf),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **_preview_descriptor_fields(preview),
    }
    return _write_descriptor(package_dir, descriptor)


def _preview_descriptor_fields(preview: dict[str, object]) -> dict[str, object]:
    """The preview payload ref plus the bake block and its hash."""
    bake = drawing_preview_bake_settings()
    fields: dict[str, object] = {
        "preview": DRAWING_PREVIEW_NAME,
        "bake": bake,
        "bakeHash": canonical_bake_hash(bake),
    }
    stats = {
        key: preview[key]
        for key in ("triangleCount", "vertexCount", "bytes")
        if isinstance(preview.get(key), (int, float)) and not isinstance(preview.get(key), bool)
    }
    if stats:
        fields["previewStats"] = stats
    return fields


def export_drawing_dxf(script_path: Path, export_path: Path) -> Path:
    """Copy the (already fresh) cached drawing DXF to ``export_path``, re-pointing
    its embedded identity comment at the generator relative to the destination.
    Raises when the package has no built DXF."""
    import shutil

    resolved_script = script_path.resolve()
    package_dir = render_package_dir(resolved_script)
    descriptor = load_drawing_descriptor(package_dir) or {}
    dxf_ref = str(descriptor.get("dxf") or "").strip()
    cached_dxf = (package_dir / dxf_ref) if dxf_ref else drawing_dxf_path(package_dir)
    if not cached_dxf.is_file():
        raise RuntimeError(f"Drawing package has no built DXF to export: {package_dir}")
    export_path = Path(export_path).expanduser().resolve()
    export_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(cached_dxf, export_path)
    write_dxf_text_to_cad_metadata(
        export_path,
        text_to_cad_identity_metadata(
            source_path=relative_to_file(resolved_script, export_path),
            source_hash=str(descriptor.get("sourceHash") or "").strip(),
        ),
    )
    return export_path


def drawing_package_current(source_path: Path) -> bool:
    """True when ``source_path``'s drawing package exists, records the current schema and
    bake, holds every payload it names, and still matches its source (the CLI no-op fast path
    and the producer's under-lock re-check, mirroring the STEP `_assembly_is_current` gate).

    ``source_path`` is the ``.dxf.py`` generator for a generated drawing, or the ``.dxf``
    itself for an imported one; the descriptor's ``sourceKind`` decides which provenance
    question is asked, exactly as the viewer's validator does.

    Every gate here mirrors the viewer's validator (``viewer/server_py/artifact.py``) check
    for check. They have to: this predicate is what decides a build no-ops, so a check the
    viewer makes and this one does not turns a stale package into a silent ``ready`` rather
    than a rebuild — and a check this one makes and the viewer does not rebuilds forever.
    """
    resolved_source = Path(source_path).resolve()
    package_dir = render_package_dir(resolved_source)
    descriptor = load_drawing_descriptor(package_dir)
    if descriptor is None:
        return False
    if not schema_version_matches(descriptor, DRAWING_PACKAGE_SCHEMA_VERSION):
        return False
    # The preview settings this build froze into preview.glb. No other signal can see a
    # change to them, so without this a thickness edit would leave every built package
    # rendering its old bake, silently.
    if not bake_hash_matches(descriptor, canonical_bake_hash(drawing_preview_bake_settings())):
        return False
    # BOTH payloads, named by the descriptor. A missing preview.glb has to be stale here as
    # well as in the viewer, or the CLI would report "current" over a package the viewer
    # cannot render.
    for key in ("dxf", "preview"):
        ref = str(descriptor.get(key) or "").strip()
        if not ref or not (package_dir / ref).is_file():
            return False
    if str(descriptor.get("sourceKind") or "").strip().lower() == "python":
        closure_hash = str(descriptor.get("sourceClosureHash") or "").strip()
        closure_files = descriptor.get("sourceClosureFiles")
        if not closure_hash or not isinstance(closure_files, list) or not closure_files:
            return False
        return closure_hash_matches(closure_hash, closure_files, base=resolved_source.parent)
    # Imported: the file on disk must still hash to the digest the descriptor recorded.
    # Fails closed on a missing digest, matching the viewer and cadgen's own producer gate.
    recorded = str(descriptor.get("sourceDigest") or "").strip()
    if not resolved_source.is_file():
        return False
    return bool(recorded) and recorded == sha256_file(resolved_source)
