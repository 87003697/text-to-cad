"""Component-GLB assembly package emit (design/component-glb-artifacts.md).

Replaces the monolithic ``.{model}.step.glb`` for an assembly with a package
directory of the same name holding one content-addressed component GLB per unique
part (mesh + embedded local topology) plus an ``assembly.json`` descriptor mapping
occurrences -> component + world transform. Components are keyed by their source
STEP content hash, so editing one part rebuilds only that component; the rest are
reused from the package's content-addressed cache.

This is the build side only; consumers (viewer/snapshot/inspect) compose the
assembly manifest from the package at read time (see the design doc, sec. 6/8).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from cadpy.generation import (
    DEFAULT_MESH_ANGULAR_TOLERANCE,
    DEFAULT_MESH_TOLERANCE,
)
from cadpy.glb import export_assembly_glb_from_scene
from cadpy.step_export import build_build123d_step_scene
from cadpy.step_scene import (
    extract_selectors_from_scene,
    mesh_step_scene,
)

PACKAGE_KIND = "assembly-package"
# Descriptor (assembly.json) layout version, independent of STEP_TOPOLOGY_SCHEMA_VERSION
# (which versions each component GLB's embedded topology). Bumped to 2 for the unified
# part+assembly rearchitecture: one descriptor+components representation, durable
# ``entryKind``, clean content-addressed components, and per-folder ``__cadcache__`` refs.
PACKAGE_SCHEMA_VERSION = 2
# Self-contained content-addressed packages: each model's components live INSIDE its own package
# at <folder>/__cadcache__/models/<step-filename>/components/<geomHash>.glb, referenced by the
# descriptor via the flat relative ref components/<geomHash>.glb. Within-model dedup (repeated
# parts share one cid) is preserved; there is no shared per-folder store, so the package
# directory is a complete, relocatable unit.
CACHE_DIRNAME = "__cadcache__"
COMPONENT_DIRNAME = "components"
DESCRIPTOR_NAME = "assembly.json"
# Source-provenance keys stripped from a component GLB's embedded STEP_TOPOLOGY so the
# component is a pure function of geometry+tolerances (content-addressable). All of this
# is model-level and lives on the descriptor (assembly.json), not the reusable leaf.
COMPONENT_PROVENANCE_KEYS = (
    "sourceKind",
    "sourcePath",
    "sourceHash",
    "sourceClosureHash",
    "sourceClosureFiles",
    "stepPath",
    "stepHash",
    "generatedAt",
    "assemblyMates",
)
# Lazy full-manifest sidecar inside the package, built on demand by inspect_refs
# (faces/edges/selectors) — the build itself never produces it (that is the win).
TOPOLOGY_GLB_NAME = "topology.glb"


def assembly_package_dir(step_path: Path) -> Path:
    """Canonical package directory for an assembly — the same path the monolithic GLB
    used (``.{model}.step.glb``), now a directory. For assemblies the package replaces
    the monolithic file; parts keep emitting a single ``.{model}.step.glb`` file."""
    from cadpy.render import part_glb_path

    return part_glb_path(step_path)


def assembly_topology_glb_path(step_path: Path) -> Path:
    """Path to the lazy full-manifest single GLB inside an assembly package. inspect's
    selector queries build + cache it here on first use; renders never need it."""
    return assembly_package_dir(step_path) / TOPOLOGY_GLB_NAME


def is_assembly_package(path: Path) -> bool:
    """True when ``path`` is a component-package directory (has assembly.json)."""
    return path.is_dir() and (path / DESCRIPTOR_NAME).is_file()


def read_package_descriptor(path: Path) -> dict[str, Any] | None:
    """Load a package descriptor from a package dir (or its assembly.json path).

    Returns None for anything that is not a package — notably a legacy monolithic GLB
    *file* sitting at the canonical artifact path (do not try to JSON-parse it)."""
    if path.is_dir():
        descriptor_path = path / DESCRIPTOR_NAME
    elif path.name == DESCRIPTOR_NAME:
        descriptor_path = path
    else:
        return None
    if not descriptor_path.is_file():
        return None
    try:
        descriptor = json.loads(descriptor_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return descriptor if isinstance(descriptor, dict) else None


def assembly_package_current(step_path: Path) -> bool:
    """True when a package descriptor exists and every referenced component GLB is
    present. Source-change detection is left to the descriptor's provenance
    (stepHash/sourceClosure) read through the package-aware manifest reader; this only
    guards the package's own existence so a missing/partial package forces a rebuild."""
    package_dir = assembly_package_dir(step_path)
    descriptor = read_package_descriptor(package_dir)
    if descriptor is None or descriptor.get("kind") != PACKAGE_KIND:
        return False
    components = descriptor.get("components") or {}
    if not components:
        return False
    return all(
        (package_dir / str(entry.get("glb", ""))).is_file()
        for entry in components.values()
    )


def _component_id(source_hash: str) -> str:
    return source_hash[:16]


def _content_hash_shape(shape: Any) -> str:
    """sha256 of a shape's BREP bytes in its *local* (unlocated) frame.

    Two occurrences of the same part share an underlying ``TShape`` (``.moved()``
    only swaps the location), so stripping the location and serializing yields an
    identical digest for every repeat — the content-addressing that dedups the
    components. Stable across builds/processes (unlike Python ``hash``).

    Triangulation and normals are excluded from the serialization so the digest is
    geometry-only: meshing a part attaches a triangulation to its shared ``TShape``,
    and a triangulation-sensitive hash would change after the first component is
    built, breaking the content-addressed cache on re-hash."""
    import io

    from OCP.BinTools import BinTools, BinTools_FormatVersion
    from OCP.TopLoc import TopLoc_Location

    unlocated = shape.wrapped.Located(TopLoc_Location())
    stream = io.BytesIO()
    BinTools.Write_s(
        unlocated,
        stream,
        False,  # theWithTriangles
        False,  # theWithNormals
        BinTools_FormatVersion.BinTools_FormatVersion_CURRENT,
    )
    return hashlib.sha256(stream.getvalue()).hexdigest()


def _transform_from_location(location: Any) -> list[float]:
    """Flatten a build123d ``Location`` to a 16-float row-major 4x4 matrix."""
    trsf = location.wrapped.Transformation()
    rows = [trsf.Value(r, c) for r in range(1, 4) for c in range(1, 5)]
    return [
        rows[0], rows[1], rows[2], rows[3],
        rows[4], rows[5], rows[6], rows[7],
        rows[8], rows[9], rows[10], rows[11],
        0.0, 0.0, 0.0, 1.0,
    ]


def _bbox_from_shape(shape: Any) -> dict[str, list[float]] | None:
    """The world-frame axis-aligned bounding box of a composed shape, as the
    ``{"min": [...], "max": [...]}`` the descriptor records so a cheap whole-entry
    inspect summary does not have to re-mesh + extract full topology.

    Computed from the geometric representation (``useTriangulation=False``) so it
    never tessellates the shape — meshing would mutate the shared ``TShape`` and
    break content-addressed component dedup on a later in-process rebuild."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        box = Bnd_Box()
        BRepBndLib.Add_s(shape.wrapped, box, False)
        xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
        return {
            "min": [float(xmin), float(ymin), float(zmin)],
            "max": [float(xmax), float(ymax), float(zmax)],
        }
    except Exception:
        return None


def _occurrence_color(child: Any) -> list[float] | None:
    color = getattr(child, "color", None)
    if color is None:
        return None
    try:
        return [float(color.red), float(color.green), float(color.blue), float(color.alpha)]
    except AttributeError:
        try:
            return [float(c) for c in tuple(color)]
        except TypeError:
            return None


def _unlocated_shape(shape: Any) -> Any:
    """A copy of ``shape`` moved to the identity location (its LOCAL frame), preserving the
    ``label``/``color`` a clean component still carries. Mirrors ``_content_hash_shape``'s
    location stripping so the emitted GLB is the exact local geometry the cid addresses."""
    from build123d import Location

    local = shape.located(Location())
    label = getattr(shape, "label", "")
    if label:
        local.label = label
    color = getattr(shape, "color", None)
    if color is not None:
        local.color = color
    return local


def build_component_glb_from_shape(
    shape: Any,
    out_glb: Path,
    *,
    cad_ref: str,
    linear_deflection: float,
    angular_deflection: float,
) -> Path:
    """Mesh an in-memory part shape (in its local frame) to a *clean* component GLB.

    The part geometry comes straight off the assembly compound (no source STEP on
    disk), so it is meshed and selector-extracted directly from the shape. The embedded
    STEP_TOPOLOGY is stripped of all source provenance (see ``COMPONENT_PROVENANCE_KEYS``)
    so the component is a pure function of geometry + mesh tolerances — byte-deterministic
    and content-addressable. Provenance lives on the per-model descriptor, not the leaf."""
    out_glb.parent.mkdir(parents=True, exist_ok=True)
    placeholder = out_glb.with_suffix(".step")
    # The shape arrives LOCATED (the occurrence is ``part.moved(transform)``). Strip the location
    # so the GLB is emitted in the part's LOCAL frame with an identity node: world placement is
    # supplied solely by the descriptor occurrence transform, and content-addressed dedup needs
    # byte-identical geometry for every repeat of a part regardless of where it sits. Without
    # this the node bakes the occurrence placement, double-placing it at compose time and giving
    # a shared (deduped) component only its first occurrence's position.
    scene = build_build123d_step_scene(_unlocated_shape(shape), placeholder)
    mesh_step_scene(
        scene,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
        relative=False,
    )
    bundle = extract_selectors_from_scene(scene, cad_ref=cad_ref)
    for key in COMPONENT_PROVENANCE_KEYS:
        bundle.manifest.pop(key, None)
    # Non-deterministic build timing would defeat content-addressing — drop it; the
    # geometry counts in stats are deterministic and kept.
    stats = bundle.manifest.get("stats")
    if isinstance(stats, dict):
        stats.pop("timingMs", None)
    # Write the leaf GLB straight to ``out_glb`` (inside the package's components/ dir). Passing
    # an explicit target avoids deriving a part_glb_path() from the placeholder, which would
    # otherwise scaffold a stray __cadcache__/models/ tree next to the component.
    export_assembly_glb_from_scene(
        placeholder,
        scene,
        target_path=out_glb,
        linear_deflection=linear_deflection,
        angular_deflection=angular_deflection,
        selector_bundle=bundle,
    )
    return out_glb


def build_package_from_compound(
    compound: Any,
    *,
    package_dir: Path,
    root_name: str,
    single_component: bool = False,
    force: bool = False,
    provenance: Mapping[str, Any] | None = None,
    linear_deflection: float = DEFAULT_MESH_TOLERANCE,
    angular_deflection: float = DEFAULT_MESH_ANGULAR_TOLERANCE,
) -> dict[str, Any]:
    """Emit a ``.{model}.step.glb/`` package from a baked ``Compound`` or single shape.

    Every model — part or assembly — is one representation: a descriptor + content-
    addressed components.

    - **assembly** (``single_component=False``): the shape-only ``gen_step`` returns a baked
      compound whose direct children are the occurrences (``part.moved(transform)`` with
      ``child.label`` the name). Each child is content-addressed by its *local* (unlocated)
      geometry, so repeated parts share one component GLB; the per-child location supplies
      the world transform. Mates ride on ``compound.assembly_mates``.
    - **part** (``single_component=True``): the whole rigid shape is one occurrence at
      identity referencing one component (its full geometry) — a degenerate package. The
      part/assembly choice is made by the caller from the generator's authored kind, never
      inferred from geometry (a multi-solid part must not look like an assembly).

    ``force`` rebuilds every component even if its ``<cid>.glb`` is present (the cid hashes
    geometry, not the mesh/selector code version). Returns build stats."""
    package_dir = Path(package_dir)
    # Each package is a SELF-CONTAINED unit: the descriptor dir lives at
    # <folder>/__cadcache__/models/<key>/ and its content-addressed component GLBs live in a
    # components/ dir INSIDE that package (<key>/components/<hash>.glb), so the whole model —
    # descriptor plus every GLB it needs — uploads, caches, and deletes as one directory with
    # no cross-model references. The descriptor references them by the flat relative ref
    # components/<hash>.glb. Within-model dedup (repeated parts share one component via a
    # shared cid) is preserved; cross-model dedup is intentionally given up for
    # self-containment (the hit rate is low and a shared store complicates blob upload/GC).
    comp_dir = package_dir / COMPONENT_DIRNAME
    comp_dir.mkdir(parents=True, exist_ok=True)

    from build123d import Location

    def _component_ref(cid: str) -> str:
        return os.path.relpath(comp_dir / f"{cid}.glb", package_dir).replace(os.sep, "/")

    occurrences: list[dict[str, Any]] = []
    components: dict[str, dict[str, Any]] = {}
    shapes: dict[str, Any] = {}

    def _add_leaf(node: Any, world_loc: Any, occ_id: str) -> dict[str, Any]:
        content_hash = _content_hash_shape(node)
        cid = _component_id(content_hash)
        shapes.setdefault(cid, node)
        components.setdefault(cid, {"glb": _component_ref(cid), "contentHash": content_hash})
        name = str(getattr(node, "label", "") or f"part_{occ_id}")
        occurrence: dict[str, Any] = {
            "id": occ_id,
            "name": name,
            "component": cid,
            "transform": _transform_from_location(world_loc),
        }
        color = _occurrence_color(node)
        if color is not None:
            occurrence["color"] = color
        occurrences.append(occurrence)
        return {"id": occ_id, "name": name, "nodeType": "part", "leafPartIds": [occ_id], "children": []}

    # Recurse the composed compound so the descriptor preserves the assembly HIERARCHY (for the
    # viewer structure tree) while the rendered occurrences stay the flat LEAF placements: each
    # leaf is one content-addressed component, with its world transform accumulated down the path
    # (parent_world * node.location). A subassembly is a tree node grouping its descendant leaves,
    # NOT a single merged component, so the tree can drill into / select / isolate its parts.
    assembly_root: dict[str, Any] | None = None
    if single_component:
        _add_leaf(compound, getattr(compound, "location", None) or Location(), "o1.1")
    else:
        def _walk(node: Any, parent_world_loc: Any, path: str) -> dict[str, Any]:
            node_loc = getattr(node, "location", None)
            world_loc = (parent_world_loc * node_loc) if node_loc is not None else parent_world_loc
            child_shapes = list(getattr(node, "children", []) or [])
            if not child_shapes:
                return _add_leaf(node, world_loc, path)
            child_nodes = [
                _walk(child, world_loc, f"{path}.{index}")
                for index, child in enumerate(child_shapes, start=1)
            ]
            return {
                "id": path,
                "name": str(getattr(node, "label", "") or path),
                "nodeType": "subassembly",
                "leafPartIds": [leaf_id for cn in child_nodes for leaf_id in cn["leafPartIds"]],
                "children": child_nodes,
            }

        assembly_root = _walk(compound, Location(), "o1")
        assembly_root["nodeType"] = "assembly"

    if not occurrences:
        raise RuntimeError(f"model {root_name!r} has no geometry to package")

    # Content-addressed component cache: a present <cid>.glb is valid (cid IS the
    # local-geometry content hash), so editing one part only rebuilds that component.
    # ``force`` bypasses the cache (mesh/selector code changed, not the geometry).
    built: list[str] = []
    reused: list[str] = []
    for cid, shape in shapes.items():
        glb_path = comp_dir / f"{cid}.glb"
        if glb_path.exists() and not force:
            reused.append(cid)
            continue
        build_component_glb_from_shape(
            shape,
            glb_path,
            cad_ref=cid,
            linear_deflection=linear_deflection,
            angular_deflection=angular_deflection,
        )
        built.append(cid)

    # The descriptor IS the assembly's index manifest: the provenance block (schema/
    # source/step hashes, mesh, edgeRendering) the freshness gates read, plus the
    # package-specific component map + occurrence placements + mates. Provenance fields
    # come first; package fields override (occurrences here are placement dicts that
    # reference components, not the monolith's tabular occurrence rows).
    descriptor: dict[str, Any] = dict(provenance or {})
    descriptor.update(
        {
            "kind": PACKAGE_KIND,
            "packageSchemaVersion": PACKAGE_SCHEMA_VERSION,
            "rootName": root_name,
            "units": "mm",
            "components": components,
            "occurrences": occurrences,
            "assemblyMates": [
                dict(mate) for mate in (getattr(compound, "assembly_mates", None) or [])
            ],
        }
    )
    # The nested assembly hierarchy (subassembly grouping over the leaf occurrences) that the
    # viewer structure tree reads via assemblyRootFromTopology(descriptor.assembly.root).
    if assembly_root is not None:
        descriptor["assembly"] = {"root": assembly_root}
    # Lightweight geometry summary so whole-entry inspect/diff reads the descriptor
    # directly (faceCount/edgeCount need full topology and are filled on demand).
    bbox = _bbox_from_shape(compound)
    if bbox is not None:
        descriptor["bbox"] = bbox
    stats = dict(descriptor.get("stats") or {})
    stats.setdefault("occurrenceCount", len(occurrences))
    stats.setdefault("shapeCount", len(occurrences))
    descriptor["stats"] = stats
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / DESCRIPTOR_NAME).write_text(json.dumps(descriptor))

    # No pruning: the component cache is SHARED across the folder, so a model's stale
    # entries may still be referenced by a sibling. Content-addressed + regenerated on the
    # fly, unreferenced GLBs are harmless (a future `cadpy cache clean` can sweep orphans).
    return {
        "occurrences": len(occurrences),
        "unique_components": len(components),
        "components_built": len(built),
        "components_reused": len(reused),
    }
