"""Selector resolution across a component package's INSTANCE TREE.

An assembly is described by two artifacts that answer different questions, and until this module
they disagreed about what an occurrence is:

* ``assembly.json`` -- the package descriptor. Knows the instance tree: 160 occurrences for
  ``tom_v2``, ids ``o1.1.1``/``o1.12``/``o1.11.7.1.1.1.2``, each naming a component and carrying
  its placement. This is what ``snapshot --mode list`` enumerates and what the CAD Viewer hands
  the user when they click a part.
* ``topology.glb`` -- the whole-assembly selector sidecar, extracted from the COMPOSED compound.
  A compound has no instance tree, so it flattens to ONE occurrence (``o1``) holding every solid:
  162 shapes, 13866 faces, in a single namespace ``o1.s1``/``o1.f19``.

``lookup.build_selector_index`` reads the second. So every ref the first hands out -- every ref a
user can actually pick -- resolved to an error, and ``inspect refs --facts`` reported
``occurrenceCount: 1`` for a 160-part assembly (issue 0b in tom-cad's FEEDBACK.md). The user's
workaround was to re-identify each face by area and position in the owning part's own namespace,
which is guesswork whenever a part appears at more than one occurrence.

The fix needs no new data. Each occurrence names a component, and each ``components/<hash>.glb``
already carries that part's own complete topology -- the yoke's component has its 66 faces sitting
there unused. So this module ADDS the instance-tree occurrences to the flat index rather than
replacing it: ``#o1.f19`` keeps resolving exactly as before, and ``#o1.12`` starts resolving.

Coordinates are WORLD AT REST: occurrence transforms applied, parameter-sidecar poses not.
That is the frame the viewer shows when the ref is picked and the frame ``snapshot --mode list``
already reports its bounds in, so the two tools now agree. Reporting component-local coordinates
instead would recreate the frame confusion that FEEDBACK.md P2 documents costing a wrong edit and
a full revert.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from cadgen.lookup import SelectorIndex

COMPONENTS_DIRNAME = "components"


def _identity() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _matrix(raw: object) -> list[float]:
    """A row-major 4x4 as 16 floats, or identity when the descriptor omits/mangles it."""
    if not isinstance(raw, (list, tuple)) or len(raw) < 16:
        return _identity()
    try:
        return [float(value) for value in raw[:16]]
    except (TypeError, ValueError):
        return _identity()


def transform_point(matrix: list[float], point: object) -> list[float] | None:
    if not isinstance(point, (list, tuple)) or len(point) < 3:
        return None
    try:
        x, y, z = (float(point[0]), float(point[1]), float(point[2]))
    except (TypeError, ValueError):
        return None
    return [
        matrix[0] * x + matrix[1] * y + matrix[2] * z + matrix[3],
        matrix[4] * x + matrix[5] * y + matrix[6] * z + matrix[7],
        matrix[8] * x + matrix[9] * y + matrix[10] * z + matrix[11],
    ]


def transform_bbox(matrix: list[float], bbox: object) -> dict[str, list[float]] | None:
    """Transform an axis-aligned box by transforming its eight CORNERS and re-deriving min/max.

    Transforming min and max alone is wrong under any rotation: it produces a box with the right
    diagonal and the wrong extent, and every occurrence in a posed assembly is rotated.
    """
    if not isinstance(bbox, Mapping):
        return None
    low = bbox.get("min")
    high = bbox.get("max")
    if not isinstance(low, (list, tuple)) or not isinstance(high, (list, tuple)):
        return None
    if len(low) < 3 or len(high) < 3:
        return None
    try:
        lows = [float(value) for value in low[:3]]
        highs = [float(value) for value in high[:3]]
    except (TypeError, ValueError):
        return None
    corners = [
        transform_point(matrix, [x, y, z])
        for x in (lows[0], highs[0])
        for y in (lows[1], highs[1])
        for z in (lows[2], highs[2])
    ]
    placed = [corner for corner in corners if corner is not None]
    if not placed:
        return None
    return {
        "min": [min(corner[axis] for corner in placed) for axis in range(3)],
        "max": [max(corner[axis] for corner in placed) for axis in range(3)],
    }


def _component_bundle_reader():
    """Imported lazily: cadgen._internal.glb pulls in the GLB machinery, and a part entry
    never reaches this module."""
    from cadgen._internal.glb import read_step_topology_bundle_from_glb

    return read_step_topology_bundle_from_glb


def _component_occurrence_bbox(bundle: object) -> object:
    """The component's own root-occurrence bbox, in component-local coordinates."""
    manifest = getattr(bundle, "manifest", None)
    if not isinstance(manifest, Mapping):
        return None
    columns = manifest.get("tables")
    columns = columns.get("occurrenceColumns") if isinstance(columns, Mapping) else None
    rows = manifest.get("occurrences")
    if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
        return None
    first = rows[0]
    if not isinstance(first, list):
        return None
    row = {str(columns[index]): first[index] for index in range(min(len(columns), len(first)))}
    return row.get("bbox")


def assembly_occurrence_rows(
    descriptor: Mapping[str, Any],
    package_dir: Path,
) -> list[dict[str, Any]]:
    """Instance-tree occurrences as selector-index rows, in world coordinates at rest.

    Column names mirror the topology sidecar's ``occurrenceColumns`` so that consumers written
    against that table (``entry_summary``, ``reporting``) need no special case for assemblies.
    """
    rows = descriptor.get("occurrences")
    if not isinstance(rows, list):
        return []
    read_bundle = None
    bbox_by_component: dict[str, object] = {}
    materialized: list[dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, Mapping):
            continue
        occurrence_id = str(entry.get("id") or "").strip()
        if not occurrence_id:
            continue
        matrix = _matrix(entry.get("transform"))
        component = str(entry.get("component") or "").strip()
        local_bbox: object = None
        if component:
            if component not in bbox_by_component:
                # Deduped by content hash: tom_v2 has 160 occurrences over 65 components, and
                # the whole set reads in well under a second.
                if read_bundle is None:
                    read_bundle = _component_bundle_reader()
                path = package_dir / COMPONENTS_DIRNAME / f"{component}.glb"
                bundle = read_bundle(path) if path.is_file() else None
                bbox_by_component[component] = _component_occurrence_bbox(bundle)
            local_bbox = bbox_by_component[component]
        materialized.append(
            {
                "id": occurrence_id,
                "path": occurrence_id,
                "name": str(entry.get("name") or occurrence_id),
                "sourceName": str(entry.get("name") or occurrence_id),
                "parentId": occurrence_id.rpartition(".")[0] or None,
                "transform": matrix,
                "bbox": transform_bbox(matrix, local_bbox),
                "component": component or None,
                # Entity ranges belong to phase 2; a reader must not mistake 0 for "has none".
                "shapeStart": 0,
                "shapeCount": 0,
                "faceStart": 0,
                "faceCount": 0,
                "edgeStart": 0,
                "edgeCount": 0,
            }
        )
    return materialized


def merge_assembly_occurrences(
    index: SelectorIndex,
    descriptor: Mapping[str, Any],
    package_dir: Path,
) -> SelectorIndex:
    """Add the instance-tree occurrences to a flat whole-assembly index.

    ADDITIVE on purpose. The flat namespace (``#o1.f19``, ``#o1.s3``) is what every existing
    caller and test resolves against, and ``single_occurrence_id`` is what lets a bare ``#f19``
    canonicalize; replacing the index would break both to fix a third thing.
    """
    rows = assembly_occurrence_rows(descriptor, package_dir)
    if not rows:
        return index
    occurrences = list(index.occurrences)
    occurrence_by_id = dict(index.occurrence_by_id)
    added = 0
    for row in rows:
        occurrence_id = str(row.get("id") or "")
        if not occurrence_id or occurrence_id in occurrence_by_id:
            continue
        occurrences.append(row)
        occurrence_by_id[occurrence_id] = row
        added += 1
    if not added:
        return index
    # occurrenceCount came from the sidecar's stats and said 1 for a 160-part assembly, which is
    # what `inspect refs --facts` reported. It is a count of what this index can resolve.
    manifest = dict(index.manifest)
    stats = dict(manifest.get("stats")) if isinstance(manifest.get("stats"), Mapping) else {}
    stats["occurrenceCount"] = len(occurrences)
    manifest["stats"] = stats
    return replace(
        index,
        manifest=manifest,
        occurrences=occurrences,
        occurrence_by_id=occurrence_by_id,
    )


def index_with_assembly_occurrences(index: SelectorIndex, artifact: object) -> SelectorIndex:
    """``merge_assembly_occurrences`` keyed off a ``StepTopologyArtifact``.

    Lives here rather than at either call site because there are TWO of them and they had already
    drifted: ``snapshot_cli.artifact_selector_index`` serves ``--focus``/``--hide``, while
    ``inspect refs`` builds its own index from the same bundle. Fixing one would have left the
    other reporting the bug, which is how 0b presented as three separate tool failures.

    Anything that is not an assembly package returns untouched, so part entries are unaffected.
    """
    if index is None or artifact is None:
        return index
    if str(getattr(artifact, "kind", "")) != "assembly":
        return index
    package_dir = getattr(artifact, "artifact_path", None)
    if not isinstance(package_dir, Path):
        return index
    from cadgen._internal.component_package import is_assembly_package, read_package_descriptor

    if not is_assembly_package(package_dir):
        return index
    descriptor = read_package_descriptor(package_dir)
    if not isinstance(descriptor, dict):
        return index
    return merge_assembly_occurrences(index, descriptor, package_dir)
