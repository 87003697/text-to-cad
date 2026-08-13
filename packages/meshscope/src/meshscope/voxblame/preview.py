"""Canonical residual-preview input, identity, and atomic publication."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
from pathlib import Path
import shutil
from typing import Any
import uuid

import numpy as np
from PIL import Image

from meshscope.voxblame.canonical_artifacts import (
    load_canonical_reference,
    load_mesh_bytes,
    read_artifact_bytes,
)
from meshscope.voxblame.contracts import (
    BOUNDARY_EPSILON,
    validate_summary_contract,
)
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.exterior import (
    ExteriorMeasurement,
    measure_exterior_surface,
    validate_exterior_measurement,
)


PREVIEW_SCHEMA = "voxblame.preview/1"
_PREVIEW_IDENTITY_DOMAIN = b"voxblame.preview/1\0"


@dataclass(frozen=True)
class PreviewScene:
    """Renderer geometry and identity facts owned by meshscope."""

    reference_geometry: dict[str, list[list[float]] | list[list[int]]]
    candidate_geometry: dict[str, list[list[float]] | list[list[int]]]
    reference: dict[str, Any]
    candidate: dict[str, Any]
    exterior: ExteriorMeasurement


@dataclass(frozen=True)
class PublishPreviewResult:
    """Published preview metadata and idempotency status."""

    metadata: dict[str, Any]
    idempotent: bool


@dataclass(frozen=True)
class ValidatedPreviewIdentity:
    """Experiment and Selected Step identities proven before rendering."""

    profile_name: str
    profile_sha256: str
    variant: str
    canonical_reference_sha256: str
    candidate_mesh_sha256: str
    selected_step: int | None
    selected_summary_sha256: str | None


def prepare_preview_scene(
    canonical_reference: str | Path,
    candidate_mesh: str | Path,
) -> PreviewScene:
    """Load the exact canonical pair and freeze channel/identity semantics."""

    reference_root = Path(canonical_reference)
    candidate_path = Path(candidate_mesh)
    manifest, _normalization, reference_bytes = load_canonical_reference(
        reference_root
    )
    reference_mesh = load_mesh_bytes(
        reference_bytes,
        suffix=Path(manifest["reference_ply"]["path"]).suffix,
        label="reference",
    )
    candidate_bytes = read_artifact_bytes(candidate_path)
    candidate = load_mesh_bytes(
        candidate_bytes,
        suffix=candidate_path.suffix,
        label="candidate",
    )
    exterior = measure_exterior_surface(
        np.asarray(candidate.triangles, dtype=np.float64)
    )
    validate_exterior_measurement(exterior)
    return PreviewScene(
        reference_geometry=_geometry_json(reference_mesh),
        candidate_geometry=_geometry_json(candidate),
        reference={
            "canonical_reference_sha256": manifest["canonical_reference_sha256"],
            "triangle_set_sha256": manifest["triangle_set_sha256"],
            "mesh_sha256": manifest["reference_ply"]["sha256"],
            "normalization_sha256": manifest["normalization_json"]["sha256"],
        },
        candidate={
            "mesh_sha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "size_bytes": len(candidate_bytes),
            "source_name": candidate_path.name,
        },
        exterior=exterior,
    )


def publish_preview(
    scene: PreviewScene,
    *,
    png_bytes: bytes,
    output: str | Path,
    profile: dict[str, Any],
    ordered_views: list[dict[str, Any]],
    browser_runtime: dict[str, Any],
    identity: ValidatedPreviewIdentity,
) -> PublishPreviewResult:
    """Validate and atomically publish one self-describing formal preview."""

    if (
        profile.get("name") != "cadena_residual_eight_view/1"
        or profile.get("name") != identity.profile_name
    ):
        raise OctreeError("preview profile is unsupported")
    if (
        scene.reference["canonical_reference_sha256"]
        != identity.canonical_reference_sha256
        or scene.candidate["mesh_sha256"] != identity.candidate_mesh_sha256
    ):
        raise OctreeError("validated preview identity conflicts with render scene")
    variant = identity.variant
    expected_views = profile.get("views")
    if not isinstance(expected_views, list) or [
        item.get("name") for item in expected_views
    ] != [item.get("name") for item in ordered_views]:
        raise OctreeError("preview view order conflicts with the profile")
    for expected, actual in zip(expected_views, ordered_views, strict=True):
        if any(
            expected.get(field) != actual.get(field)
            for field in ("name", "kind", "direction", "up", "horizontal_flip")
        ):
            raise OctreeError("preview view semantics conflict with the profile")
        expected_projection = (
            "orthographic"
            if expected["kind"] == "axial_depth"
            else "perspective"
        )
        if actual.get("framing", {}).get("projection") != expected_projection:
            raise OctreeError("preview camera semantics conflict with the profile")

    image = _validate_png(png_bytes, profile, variant)
    image_sha256 = hashlib.sha256(png_bytes).hexdigest()
    if (
        not isinstance(browser_runtime, dict)
        or set(browser_runtime)
        != {"schema", "adapter_profile", "browser_identity", "result"}
        or browser_runtime.get("schema") != "meshshot.prelaunched-cdp-runtime/1"
        or browser_runtime.get("result") != "passed"
        or set(browser_runtime.get("adapter_profile", {})) != {"name", "sha256"}
        or set(browser_runtime.get("browser_identity", {}))
        != {"playwright", "browser", "revision", "version", "sha256"}
    ):
        raise OctreeError("browser runtime evidence is incomplete")
    exterior_exact = scene.exterior.exact
    exterior_cells = scene.exterior.snapshot["cells"]
    metadata: dict[str, Any] = {
        "schema": PREVIEW_SCHEMA,
        "render_variant": variant,
        "canonical_frame": {
            **profile["canonical_frame"],
            "boundary_epsilon": BOUNDARY_EPSILON,
        },
        "profile": {
            "name": profile["name"],
            "sha256": identity.profile_sha256,
            "renderer": profile["renderer"],
            "variants": profile["variants"],
            "camera": profile["camera"],
            "padding": profile["padding"],
            "depth": profile["depth"],
            "lighting": profile["lighting"],
            "composition": profile["composition"],
            "downsampling": profile["downsampling"],
            "experiment_identity": {
                "name": identity.profile_name,
                "sha256": identity.profile_sha256,
            },
        },
        "browser_runtime": browser_runtime,
        "reference": scene.reference,
        "candidate": scene.candidate,
        "ordered_views": ordered_views,
        "exterior_surface": {
            "out_of_frame": exterior_exact["surface_present"],
            "measurement_snapshot_sha256": scene.exterior.logical_sha256,
            "component_count": _component_count(exterior_cells),
            "nearest_overrun": exterior_exact["nearest_overrun"],
            "farthest_overrun": exterior_exact["farthest_overrun"],
            "outside_directions": exterior_exact["outside_directions"],
            "edge_direction_markers": [
                {"view": view["name"], "markers": view.get("markers", [])}
                for view in ordered_views
            ],
        },
        "image": {
            "path": "preview.png",
            "width": image.width,
            "height": image.height,
            "mode": "RGB",
            "sha256": image_sha256,
            "size_bytes": len(png_bytes),
        },
    }
    if variant == "final":
        metadata["selected_step"] = identity.selected_step
        metadata["selected_summary_sha256"] = identity.selected_summary_sha256
    identity_source = _json_bytes(metadata)
    metadata["preview_identity_sha256"] = hashlib.sha256(
        _PREVIEW_IDENTITY_DOMAIN + identity_source
    ).hexdigest()
    metadata_bytes = _json_bytes(metadata)
    output_path = Path(output)
    if output_path.exists():
        if _published_matches(output_path, metadata, png_bytes):
            return PublishPreviewResult(metadata=metadata, idempotent=True)
        raise OctreeError("preview output already exists with a different identity")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    stage = output_path.parent / f".tmp-preview-{uuid.uuid4().hex}"
    try:
        stage.mkdir(parents=False, exist_ok=False)
        (stage / "preview.png").write_bytes(png_bytes)
        (stage / "preview.json").write_bytes(metadata_bytes)
        if not _published_matches(stage, metadata, png_bytes):
            raise OctreeError("staged preview identity mismatch")
        stage.rename(output_path)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return PublishPreviewResult(metadata=metadata, idempotent=False)


def validate_preview_identity(
    scene: PreviewScene,
    *,
    profile_name: str,
    profile_sha256: str,
    experiment_profile: dict[str, Any],
    variant: str,
    selected_step: int | None = None,
    selected_summary: dict[str, Any] | None = None,
    selected_summary_sha256: str | None = None,
) -> ValidatedPreviewIdentity:
    """Fail closed before rendering when frozen workflow identities conflict."""

    if variant not in {"step", "final"}:
        raise OctreeError("preview render variant must be step or final")
    if set(experiment_profile) != {"name", "sha256"}:
        raise OctreeError(
            "experiment preview profile must contain only name and sha256"
        )
    if experiment_profile.get("name") != profile_name:
        raise OctreeError("experiment preview profile name conflicts with renderer")
    if experiment_profile.get("sha256") != profile_sha256:
        raise OctreeError("experiment preview profile digest conflicts with renderer")

    if variant == "step":
        if selected_step is not None or selected_summary is not None:
            raise OctreeError("step preview must not declare a selected step")
        if selected_summary_sha256 is not None:
            raise OctreeError("step preview must not bind a selected summary")
        return _validated_identity(
            scene,
            profile_name=profile_name,
            profile_sha256=profile_sha256,
            variant=variant,
        )

    if (
        not isinstance(selected_step, int)
        or isinstance(selected_step, bool)
        or selected_step < 0
    ):
        raise OctreeError("final preview requires a non-negative selected step")
    if selected_summary is None or selected_summary_sha256 is None:
        raise OctreeError("final preview requires the selected summary")
    if not _is_sha256(selected_summary_sha256):
        raise OctreeError("selected summary identity must be a SHA-256 digest")
    try:
        validate_summary_contract(selected_summary)
    except Exception as exc:
        raise OctreeError("selected summary is not canonical") from exc
    if selected_summary["step"] != selected_step:
        raise OctreeError("selected summary step conflicts with selected step")
    if (
        selected_summary["measurement"]["candidate_mesh_sha256"]
        != scene.candidate["mesh_sha256"]
    ):
        raise OctreeError("selected summary candidate conflicts with preview")
    if (
        selected_summary["canonical_reference"][
            "canonical_reference_sha256"
        ]
        != scene.reference["canonical_reference_sha256"]
    ):
        raise OctreeError("selected summary reference conflicts with preview")
    return _validated_identity(
        scene,
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        variant=variant,
        selected_step=selected_step,
        selected_summary_sha256=selected_summary_sha256,
    )


def _validated_identity(
    scene: PreviewScene,
    *,
    profile_name: str,
    profile_sha256: str,
    variant: str,
    selected_step: int | None = None,
    selected_summary_sha256: str | None = None,
) -> ValidatedPreviewIdentity:
    return ValidatedPreviewIdentity(
        profile_name=profile_name,
        profile_sha256=profile_sha256,
        variant=variant,
        canonical_reference_sha256=scene.reference[
            "canonical_reference_sha256"
        ],
        candidate_mesh_sha256=scene.candidate["mesh_sha256"],
        selected_step=selected_step,
        selected_summary_sha256=selected_summary_sha256,
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _geometry_json(mesh: Any) -> dict[str, list[list[float]] | list[list[int]]]:
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    return {
        "vertices": vertices.tolist(),
        "faces": faces.tolist(),
    }


def _validate_png(
    png_bytes: bytes,
    profile: dict[str, Any],
    variant: str,
) -> Image.Image:
    try:
        with Image.open(BytesIO(png_bytes)) as loaded:
            loaded.load()
            image = loaded.copy()
    except Exception as exc:
        raise OctreeError("preview PNG is unreadable") from exc
    expected = tuple(profile["variants"][variant]["image_pixels"])
    if image.format not in {None, "PNG"} or image.mode != "RGB" or image.size != expected:
        raise OctreeError(
            f"preview PNG must be opaque RGB {expected[0]}x{expected[1]}"
        )
    return image


def _component_count(cells: list[list[int]]) -> int:
    remaining = {tuple(int(value) for value in cell) for cell in cells}
    count = 0
    while remaining:
        count += 1
        stack = [remaining.pop()]
        while stack:
            x, y, z = stack.pop()
            for neighbor in (
                (x - 1, y, z),
                (x + 1, y, z),
                (x, y - 1, z),
                (x, y + 1, z),
                (x, y, z - 1),
                (x, y, z + 1),
            ):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    stack.append(neighbor)
    return count


def _published_matches(
    root: Path,
    metadata: dict[str, Any],
    png_bytes: bytes,
) -> bool:
    try:
        return (
            root.is_dir()
            and {path.name for path in root.iterdir()}
            == {"preview.json", "preview.png"}
            and json.loads((root / "preview.json").read_text(encoding="utf-8"))
            == metadata
            and (root / "preview.png").read_bytes() == png_bytes
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")
