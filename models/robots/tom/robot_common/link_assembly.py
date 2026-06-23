"""Re-express a link's flat instance placements as an AssemblyHelper sub-assembly.

A tom link is currently a flat list of ``{path, name, transform}`` instances. This
helper rebuilds the same geometry as an AssemblyHelper ``Compound`` whose parts are
connected by explicit rigid mates (a root part, with every other part rigid-mated at
its transform relative to the root) instead of independent baked transforms. Geometry
is identical to placing the instances directly; parts import through the cached
``__cadcache__`` loader. This is the design-coordinate joint approach (juno-style):
the mate frames carry the design placement, validated against the imported topology.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from build123d import Compound, Location

from cadpy.assembly import AssemblyHelper
from cadpy.step_scene import import_step


def location_from_transform(transform: Sequence[float]) -> Location:
    """Build a build123d ``Location`` from a 16-element row-major transform."""
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    trsf.SetValues(*[float(value) for value in transform[:12]])
    return Location(trsf)


def _resolve(path: object, *, base_dir: Path) -> Path:
    text = str(path)
    candidate = Path(text)
    if candidate.is_absolute():
        return candidate
    return (base_dir / text).resolve()


def compound_from_instances(
    name: str,
    instances: Sequence[dict[str, Any]],
    *,
    base_dir: Path,
    assembly_mates: Sequence[dict[str, Any]] | None = None,
) -> Compound:
    """Bake a flat world-placed instance list into a single labeled ``Compound``.

    Each instance's ``transform`` is its absolute (world) placement — the tom
    composition resolves every part into world coordinates — so parts are imported
    through the cached ``__cadcache__`` loader, moved to their placement, and labeled
    with the instance name. There are no joints: the geometry is baked. This is the
    ``shape`` contract for a whole-robot assembly, which trades the assembly path's
    prototype instancing for a self-contained build123d ``Compound``.

    ``assembly_mates`` (semantic joint metadata, the same dicts the legacy envelope
    carried) are attached directly to the returned compound via ``.assembly_mates``.
    The STEP export collector (``cadpy.step_export._collect_assembly_mates``) reads
    that attribute off the root compound and normalizes the ids, so the mates reach
    the scene/manifest exactly as the deprecated ``assembly_mates`` envelope field
    used to — no separate envelope channel required.
    """
    if not instances:
        raise RuntimeError(f"assembly {name!r} has no instances")
    children: list[Compound] = []
    for inst in instances:
        part = import_step(_resolve(inst["path"], base_dir=base_dir))
        placed = part.moved(location_from_transform(inst["transform"]))
        placed.label = str(inst["name"])
        children.append(placed)
    compound = Compound(children=children, label=name)
    if assembly_mates:
        compound.assembly_mates = [dict(mate) for mate in assembly_mates]
    return compound


def link_assembly_from_instances(
    name: str,
    instances: Sequence[dict[str, Any]],
    *,
    base_dir: Path,
) -> Compound:
    """Build an AssemblyHelper ``Compound`` from a link's flat instance list.

    ``base_dir`` is the directory the instance ``path`` values are relative to
    (typically the link module's own directory). The first instance is the rigid
    root; each remaining instance is rigid-mated to it at its relative transform.
    """
    asm = AssemblyHelper(name)
    if not instances:
        raise RuntimeError(f"link {name!r} has no instances")
    root = instances[0]
    root_shape = asm.add(import_step(_resolve(root["path"], base_dir=base_dir)), str(root["name"]))
    root_loc = location_from_transform(root["transform"])
    for inst in instances[1:]:
        child = asm.add(import_step(_resolve(inst["path"], base_dir=base_dir)), str(inst["name"]))
        relative = root_loc.inverse() * location_from_transform(inst["transform"])
        seat = asm.rigid_frame(root_shape, f"{inst['name']}_seat", relative)
        origin = asm.rigid_frame(child, f"{inst['name']}_origin", Location())
        asm.connect(seat, origin, relation="rigid", label=f"{inst['name']}_to_{root['name']}")
    return asm.build().move(root_loc)
