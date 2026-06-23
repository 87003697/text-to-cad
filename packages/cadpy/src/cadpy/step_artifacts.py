from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cadpy.assembly_spec import REPO_ROOT
from cadpy.catalog import source_from_path
from cadpy.cli_logging import CliLogger
from cadpy.generation import (
    EntrySpec,
    _entry_spec_from_source,
    _existing_topology_artifact_matches_spec_without_scene,
    _generate_part_outputs,
    run_script_generator,
)
from cadpy.metadata import DEFAULT_MESH_ANGULAR_TOLERANCE, DEFAULT_MESH_TOLERANCE
from cadpy.render import part_glb_path
from cadpy.step_metadata import read_text_to_cad_step_metadata
from cadpy.step_scene import LoadedStepScene, load_step_scene
from cadpy.step_targets import (
    REGENERATE_STEP_COMMAND,
    REGENERATE_STEP_PROMPT,
    ResolvedStepTarget,
    StepTopologyArtifact,
    StepTopologyArtifactError,
    validate_step_topology_artifact,
)


def cad_ref_for_step_path(repo_root: Path, step_path: Path) -> str:
    relative = _repo_relative(repo_root, step_path)
    suffix = step_path.suffix
    return relative[: -len(suffix)] if suffix else relative


def ensure_step_topology_artifact(
    target: ResolvedStepTarget,
    *,
    glb_path: Path | None = None,
    require_selector: bool = False,
    force: bool = False,
    logger: CliLogger | None = None,
    mesh_tolerance: float | None = None,
    mesh_angular_tolerance: float | None = None,
    owner: str = "cadpy-step-artifact",
) -> StepTopologyArtifact:
    spec = _entry_spec_for_target(
        target,
        mesh_tolerance=mesh_tolerance,
        mesh_angular_tolerance=mesh_angular_tolerance,
    )
    resolved_glb_path = glb_path or part_glb_path(spec.step_path)

    # The canonical render artifact for a generated assembly is a component-GLB package
    # directory, which carries no whole-assembly selector topology (faces/edges). inspect
    # needs that full manifest, so extract it on demand from the scene (the build-time
    # win is precisely that this 29.5s extraction is no longer in the build path).
    from cadpy.component_package import is_assembly_package

    if glb_path is None and is_assembly_package(resolved_glb_path):
        try:
            return _assembly_topology_artifact(
                spec, require_selector=require_selector, logger=logger, force=force
            )
        except StepTopologyArtifactError:
            raise
        except Exception as exc:
            raise StepTopologyArtifactError(
                code="glb_regeneration_failed",
                cad_path=spec.cad_ref,
                step_path=spec.step_path,
                glb_path=resolved_glb_path,
                regenerate_command=REGENERATE_STEP_COMMAND,
                message=(
                    f"Failed to extract assembly topology for {spec.cad_ref}: {exc}.\n"
                    f"{REGENERATE_STEP_PROMPT}"
                ),
            ) from exc

    if not force:
        artifact = _current_artifact_for_spec(spec, glb_path=resolved_glb_path, require_selector=require_selector)
        if artifact is not None:
            return artifact

    try:
        spec, scene = _scene_for_regeneration(spec, logger=logger, force=force)
        _generate_part_outputs(
            spec,
            entries_by_step_path={spec.step_path: spec},
            preloaded_scene=scene,
            require_step_file=(spec.source != "generated"),
            force=True,
            logger=logger,
        )
    except StepTopologyArtifactError:
        raise
    except Exception as exc:
        raise StepTopologyArtifactError(
            code="glb_regeneration_failed",
            cad_path=spec.cad_ref,
            step_path=spec.step_path,
            glb_path=resolved_glb_path,
            regenerate_command=REGENERATE_STEP_COMMAND,
            message=(
                f"Failed to regenerate GLB/topology artifact for {spec.cad_ref}: {exc}.\n"
                f"{REGENERATE_STEP_PROMPT}"
            ),
        ) from exc
    # The build just produced a component-GLB package directory. Return its topology the
    # same way the fast path above does (cheap descriptor for renders, on-demand selector
    # extraction otherwise) rather than the monolith file-validator, which requires a GLB
    # *file* and would report the package directory as missing.
    if glb_path is None and is_assembly_package(resolved_glb_path):
        return _assembly_topology_artifact(
            spec, require_selector=require_selector, logger=logger, force=False
        )
    return validate_step_topology_artifact(
        ResolvedStepTarget(
            cad_path=spec.cad_ref,
            kind=spec.kind,
            source_path=spec.source_path,
            step_path=spec.step_path,
        ),
        glb_path=resolved_glb_path,
        require_selector=require_selector,
    )


def _entry_spec_for_target(
    target: ResolvedStepTarget,
    *,
    mesh_tolerance: float | None,
    mesh_angular_tolerance: float | None,
) -> EntrySpec:
    python_source = _python_source_for_target(target)
    if python_source is not None:
        source = source_from_path(python_source)
        if source is None:
            raise RuntimeError(f"Python generator is not a gen_step() CAD source: {python_source}")
        spec = _entry_spec_from_source(source)
        return _with_mesh_overrides(spec, mesh_tolerance=mesh_tolerance, mesh_angular_tolerance=mesh_angular_tolerance)

    if not target.step_path.is_file():
        raise FileNotFoundError(f"STEP file does not exist: {target.step_path}")
    return EntrySpec(
        source_ref=_repo_relative(REPO_ROOT, target.step_path),
        cad_ref=target.cad_path,
        kind=target.kind if target.kind in {"part", "assembly"} else "part",
        source_path=target.step_path,
        display_name=target.step_path.stem,
        source="imported",
        step_path=target.step_path,
        mesh_tolerance=mesh_tolerance if mesh_tolerance is not None else DEFAULT_MESH_TOLERANCE,
        mesh_angular_tolerance=(
            mesh_angular_tolerance
            if mesh_angular_tolerance is not None
            else DEFAULT_MESH_ANGULAR_TOLERANCE
        ),
        mesh_tolerance_explicit=mesh_tolerance is not None,
        mesh_angular_tolerance_explicit=mesh_angular_tolerance is not None,
    )


def _assembly_topology_artifact(
    spec: EntrySpec,
    *,
    require_selector: bool,
    logger: CliLogger | None,
    force: bool,
) -> StepTopologyArtifact:
    """The topology artifact for a component-GLB package, which carries no embedded
    whole-assembly topology.

    When the caller does not need selectors (a plain render reads the package's render
    meshes directly), return a cheap descriptor-only artifact. When selectors ARE needed
    (inspect, selection-based renders), re-mesh + re-extract the full manifest on demand
    and return the bundle in memory — the build-time win is precisely that this ~29.5s
    extraction is no longer in the build path. TODO: cache to a ``topology.glb`` sidecar
    inside the package to avoid re-extraction on repeated selector queries."""
    from cadpy.component_package import assembly_package_dir, read_package_descriptor

    if not require_selector:
        descriptor = read_package_descriptor(assembly_package_dir(spec.step_path))
        if descriptor is not None:
            return StepTopologyArtifact(
                cad_path=spec.cad_ref,
                kind="assembly",
                source_path=spec.source_path,
                step_path=spec.step_path,
                glb_path=part_glb_path(spec.step_path),
                manifest=descriptor,
                selector_bundle=None,
            )

    from cadpy.generation import (
        _effective_step_spec_for_scene,
        _selector_options_for_part,
    )
    from cadpy.step_scene import (
        SelectorProfile,
        extract_selectors_from_scene,
        mesh_step_scene,
    )

    spec, scene = _scene_for_regeneration(spec, logger=logger, force=force)
    spec = _effective_step_spec_for_scene(spec, scene)
    options = _selector_options_for_part(spec, scene=scene)
    mesh_step_scene(
        scene,
        linear_deflection=options.linear_deflection,
        angular_deflection=options.angular_deflection,
        relative=options.relative,
    )
    bundle = extract_selectors_from_scene(
        scene,
        cad_ref=spec.cad_ref,
        profile=SelectorProfile.ARTIFACT,
        options=options,
        color=spec.color,
    )
    return StepTopologyArtifact(
        cad_path=spec.cad_ref,
        kind="assembly",
        source_path=spec.source_path,
        step_path=spec.step_path,
        glb_path=part_glb_path(spec.step_path),
        manifest=bundle.manifest,
        selector_bundle=bundle,
    )


def _scene_for_regeneration(
    spec: EntrySpec,
    *,
    logger: CliLogger | None,
    force: bool,
) -> tuple[EntrySpec, LoadedStepScene]:
    if spec.source == "generated":
        scene = run_script_generator(
            spec,
            "gen_step",
            logger=logger,
            force=force,
        )
        if scene is None:
            raise RuntimeError(f"Python generator did not produce a STEP scene: {spec.source_ref}")
        return spec, scene

    with (logger.timed(f"load STEP {spec.cad_ref}") if logger is not None else _null_context()):
        scene = load_step_scene(spec.step_path)
    inferred_kind = _infer_entry_kind(spec.step_path, scene)
    if inferred_kind != spec.kind:
        spec = replace(spec, kind=inferred_kind)
    return spec, scene


def _current_artifact_for_spec(
    spec: EntrySpec,
    *,
    glb_path: Path,
    require_selector: bool,
) -> StepTopologyArtifact | None:
    if not _existing_topology_artifact_matches_spec_without_scene(spec, require_selector=require_selector):
        return None
    try:
        return validate_step_topology_artifact(
            ResolvedStepTarget(
                cad_path=spec.cad_ref,
                kind=spec.kind,
                source_path=spec.source_path,
                step_path=spec.step_path,
            ),
            glb_path=glb_path,
            require_selector=require_selector,
        )
    except StepTopologyArtifactError:
        return None


def _python_source_for_target(target: ResolvedStepTarget) -> Path | None:
    if target.step_path.is_file():
        return None
    if target.source_path.suffix.lower() == ".py" and target.source_path.is_file():
        return target.source_path
    candidate = target.step_path.with_suffix(".py")
    return candidate if candidate.is_file() else None


def _with_mesh_overrides(
    spec: EntrySpec,
    *,
    mesh_tolerance: float | None,
    mesh_angular_tolerance: float | None,
) -> EntrySpec:
    if mesh_tolerance is None and mesh_angular_tolerance is None:
        return spec
    return replace(
        spec,
        mesh_tolerance=mesh_tolerance if mesh_tolerance is not None else spec.mesh_tolerance,
        mesh_angular_tolerance=(
            mesh_angular_tolerance
            if mesh_angular_tolerance is not None
            else spec.mesh_angular_tolerance
        ),
        mesh_tolerance_explicit=mesh_tolerance is not None,
        mesh_angular_tolerance_explicit=mesh_angular_tolerance is not None,
    )


def _scene_has_assembly_structure(scene: LoadedStepScene) -> bool:
    stack = list(scene.roots)
    if len(stack) > 1:
        return True
    while stack:
        node = stack.pop()
        if node.children:
            return True
        stack.extend(node.children)
    return False


def _infer_entry_kind(step_path: Path, scene: LoadedStepScene) -> str:
    metadata_kind = None
    try:
        metadata_kind = read_text_to_cad_step_metadata(step_path).get("entryKind")
    except Exception:
        metadata_kind = None
    if metadata_kind in {"part", "assembly"}:
        return metadata_kind
    return "assembly" if _scene_has_assembly_structure(scene) else "part"


def _repo_relative(repo_root: Path, path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


class _null_context:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_args: object) -> None:
        return None
