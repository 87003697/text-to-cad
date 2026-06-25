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

import contextlib
import copy
import importlib.util
import io
import sys
from functools import lru_cache
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


@lru_cache(maxsize=None)
def _load_generator_module(generator_path_str: str) -> Any:
    """Import a generated child's generator script once and cache the module.

    Generators self-bootstrap their own ``sys.path`` and import their dependencies at module
    top, so executing the module here is enough to call its ``gen_step()``. Cached per path so
    a part reused across the arm loads its module a single time.
    """
    generator_path = Path(generator_path_str)
    module_name = f"_tom_child_gen_{generator_path.stem}_{abs(hash(generator_path_str))}"
    spec = importlib.util.spec_from_file_location(module_name, generator_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load generator {generator_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def _generated_child_shape(generator_path_str: str) -> Any:
    """Build a generated child's shape once via its ``gen_step()`` and cache it.

    Leaf generators can be expensive (sheet-metal bend analysis, interference checks), so a part
    reused across the arm must build a single time, not once per occurrence. Callers deepcopy the
    result for each placement, so the cached shape is never moved, labeled, or otherwise mutated.
    """
    module = _load_generator_module(generator_path_str)
    gen_step = getattr(module, "gen_step", None)
    if not callable(gen_step):
        raise RuntimeError(f"generated child {generator_path_str} has no gen_step()")
    # Leaf generators print build diagnostics to stdout when run standalone; silence them here so
    # composing a parent does not flood the build log (geometry is unaffected).
    with contextlib.redirect_stdout(io.StringIO()):
        result = gen_step()
    if isinstance(result, dict):
        if "shape" not in result:
            raise RuntimeError(f"{generator_path_str} gen_step() returned no 'shape'")
        return result["shape"]
    return result


def _resolve_shape(path: object, *, base_dir: Path) -> Any:
    """Resolve one instance's geometry by dependency kind.

    A GENERATED child — its STEP ``<name>.step`` has a sibling ``<name>.step.py`` generator — is
    composed from that generator's ``gen_step()``. This is a source-level dependency: a child edit
    flows into the parent on the next rebuild with no committed-STEP refresh and no leaf-first
    regeneration ordering. An IMPORTED child — no generator, e.g. a purchased/downloaded servo,
    extrusion, or gripper — loads through the cached ``__cadcache__`` importer. Both paths return a
    fresh, independent shape per call so repeated copies of the same part stay independent occurrences.
    """
    step_path = _resolve(path, base_dir=base_dir)
    # The generator for the logical STEP ``<name>.step`` is ``<name>.step.py`` (append .py to the
    # full step filename; ``with_suffix('.py')`` would drop the ``.step`` and miss it).
    generator_path = step_path.with_name(step_path.name + ".py")
    if generator_path.exists():
        return copy.deepcopy(_generated_child_shape(str(generator_path)))
    return import_step(step_path)


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
        part = _resolve_shape(inst["path"], base_dir=base_dir)
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
    root_shape = asm.add(_resolve_shape(root["path"], base_dir=base_dir), str(root["name"]))
    root_loc = location_from_transform(root["transform"])
    for inst in instances[1:]:
        child = asm.add(_resolve_shape(inst["path"], base_dir=base_dir), str(inst["name"]))
        relative = root_loc.inverse() * location_from_transform(inst["transform"])
        seat = asm.rigid_frame(root_shape, f"{inst['name']}_seat", relative)
        origin = asm.rigid_frame(child, f"{inst['name']}_origin", Location())
        asm.connect(seat, origin, relation="rigid", label=f"{inst['name']}_to_{root['name']}")
    return asm.build().move(root_loc)
