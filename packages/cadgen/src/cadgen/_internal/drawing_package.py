"""Drawing-package render artifact for generated DXF entries.

The DXF analog of the component-GLB package: a `<name>.dxf.py` generator's build
product is a self-contained package directory under the model folder's
``__cadcache__/models/``, keyed by the generator filename:

    <model-folder>/__cadcache__/models/<name>.dxf.py/
      drawing.json    # descriptor: provenance + freshness metadata
      drawing.dxf     # the built DXF (the viewer renders this directly)

Unlike STEP, DXF needs no derived render format — the ``.dxf`` itself is cheap to
parse client-side — so the package holds the built DXF plus a descriptor carrying
the same python-source provenance the assembly package records
(``sourceKind``/``sourcePath``/``sourceHash``/``sourceClosureHash``/
``sourceClosureFiles``/``generatedAt``). The viewer's freshness gate reads the
descriptor exactly as it reads ``assembly.json``.
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path

from cadgen._internal.file_metadata import text_to_cad_identity_metadata, write_dxf_text_to_cad_metadata
from cadgen._internal.source_hash import (
    PythonSourceClosure,
    closure_hash_from_files,
    python_source_hash,
)
from cadgen.catalog import drawing_package_path_for_source
from cadgen.render import relative_to_file, sha256_file

DRAWING_PACKAGE_KIND = "drawing-package"
DRAWING_PACKAGE_SCHEMA_VERSION = 1
DRAWING_DESCRIPTOR_NAME = "drawing.json"
DRAWING_DXF_NAME = "drawing.dxf"


def drawing_descriptor_path(package_dir: Path) -> Path:
    return Path(package_dir) / DRAWING_DESCRIPTOR_NAME


def drawing_dxf_path(package_dir: Path) -> Path:
    return Path(package_dir) / DRAWING_DXF_NAME


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
    previous_marker = ezdxf_document.ezdxf_marker_string
    fixed_marker = f"{ezdxf.__version__} @ 2000-01-01T00:00:00+00:00"
    options.write_fixed_meta_data_for_testing = True
    ezdxf_document.ezdxf_marker_string = lambda: fixed_marker
    try:
        metadata_reader = getattr(document, "ezdxf_metadata", None)
        if callable(metadata_reader):
            metadata = metadata_reader()
            # The created-by marker was stamped when the generator constructed the
            # document (before this save path had any control); pin it too.
            metadata[ezdxf_document.CREATED_BY_EZDXF] = fixed_marker
        yield
    finally:
        options.write_fixed_meta_data_for_testing = previous_fixed
        ezdxf_document.ezdxf_marker_string = previous_marker


def write_drawing_package(
    document: object,
    *,
    script_path: Path,
    source_closure: PythonSourceClosure | None,
) -> dict[str, object]:
    """Save ``document`` (an ezdxf document) into the generator's drawing package and
    write the descriptor. Returns the descriptor dict."""
    saveas = getattr(document, "saveas", None)
    if not callable(saveas):
        raise TypeError(
            f"gen_dxf() envelope field 'document' must be a DXF document, got {type(document).__name__}"
        )
    resolved_script = script_path.resolve()
    package_dir = drawing_package_path_for_source(resolved_script)
    package_dir.mkdir(parents=True, exist_ok=True)
    dxf_path = drawing_dxf_path(package_dir)
    with _deterministic_dxf_output(document):
        saveas(str(dxf_path))
    source_identity = python_source_hash(resolved_script)
    # The identity comment inside the cached DXF records the generator relative to the
    # DXF file itself (inside __cadcache__/models/<key>/), so the package stays portable.
    write_dxf_text_to_cad_metadata(
        dxf_path,
        text_to_cad_identity_metadata(
            source_path=relative_to_file(resolved_script, dxf_path),
            source_hash=source_identity.source_hash,
        ),
    )
    descriptor: dict[str, object] = {
        "kind": DRAWING_PACKAGE_KIND,
        "packageSchemaVersion": DRAWING_PACKAGE_SCHEMA_VERSION,
        "sourceKind": "python",
        # Like the assembly package, sourcePath/sourceClosureFiles are relative to the
        # MODEL folder (the directory holding the .dxf.py), not the __cadcache__ dir.
        "sourcePath": resolved_script.name,
        "sourceHash": source_identity.source_hash,
        "dxf": DRAWING_DXF_NAME,
        "dxfHash": sha256_file(dxf_path),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if source_closure is not None and source_closure.files:
        descriptor["sourceClosureHash"] = source_closure.closure_hash
        descriptor["sourceClosureFiles"] = list(source_closure.files)
    descriptor_path = drawing_descriptor_path(package_dir)
    with descriptor_path.open("w", encoding="utf-8") as handle:
        json.dump(descriptor, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return descriptor


def drawing_package_current(script_path: Path) -> bool:
    """True when the generator's drawing package exists and its recorded source
    closure still hashes to the descriptor's closure hash (the CLI no-op fast path,
    mirroring the STEP `_assembly_is_current` gate)."""
    resolved_script = script_path.resolve()
    package_dir = drawing_package_path_for_source(resolved_script)
    descriptor = load_drawing_descriptor(package_dir)
    if descriptor is None:
        return False
    dxf_ref = str(descriptor.get("dxf") or "").strip()
    dxf_path = (package_dir / dxf_ref) if dxf_ref else drawing_dxf_path(package_dir)
    if not dxf_path.is_file():
        return False
    closure_hash = str(descriptor.get("sourceClosureHash") or "").strip()
    closure_files = descriptor.get("sourceClosureFiles")
    if not closure_hash or not isinstance(closure_files, list) or not closure_files:
        return False
    recomputed = closure_hash_from_files(closure_files, base=resolved_script.parent)
    return recomputed is not None and recomputed == closure_hash
