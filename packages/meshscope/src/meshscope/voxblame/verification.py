"""Non-publishing Observable Geometry verification for final rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any
import uuid

from meshscope.voxblame.contracts import validate_measurement_contract
from meshscope.voxblame.errors import OctreeError
from meshscope.voxblame.measurement import measure_step


VERIFICATION_SCHEMA = "voxblame.verification/1"


@dataclass(frozen=True)
class VerifyStepResult:
    """Independent comparison with one already-published Measured Step."""

    verification: dict[str, Any]
    published: bool


def verify_step(
    canonical_reference: str | Path,
    candidate_mesh: str | Path,
    workspace: str | Path,
    *,
    against_step: int,
    output: str | Path | None = None,
) -> VerifyStepResult:
    """Verify a rebuilt mesh without adding a Measured Step to ``workspace``."""

    if (
        not isinstance(against_step, int)
        or isinstance(against_step, bool)
        or against_step < 0
    ):
        raise OctreeError("against_step must be a non-negative integer")
    workspace_path = Path(workspace)
    selected_path = (
        workspace_path / "steps" / f"{against_step:06d}" / "measurement.json"
    )
    try:
        selected = json.loads(selected_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OctreeError(
            f"against_step {against_step} is not a valid published measurement"
        ) from exc
    validate_measurement_contract(selected)
    if selected["step"] != against_step:
        raise OctreeError("against_step conflicts with the published measurement")

    with tempfile.TemporaryDirectory(prefix="voxblame-verify-") as temporary:
        probe_root = Path(temporary) / "probe"
        measure_step(
            canonical_reference,
            candidate_mesh,
            probe_root,
            step=0,
        )
        rebuilt = json.loads(
            (probe_root / "steps/000000/measurement.json").read_text(
                encoding="utf-8"
            )
        )

    selected_identity = selected["measurement"]
    rebuilt_identity = rebuilt["measurement"]
    equality = {
        "interior": (
            rebuilt_identity["interior_tree_sha256"]
            == selected_identity["interior_tree_sha256"]
        ),
        "exterior": (
            rebuilt_identity["exterior_snapshot_sha256"]
            == selected_identity["exterior_snapshot_sha256"]
        ),
        "observable": (
            rebuilt_identity["observable_sha256"]
            == selected_identity["observable_sha256"]
        ),
        "errors_by_depth": rebuilt["errors_by_depth"] == selected["errors_by_depth"],
    }
    verification: dict[str, Any] = {
        "schema": VERIFICATION_SCHEMA,
        "against_step": against_step,
        "canonical_reference": selected["canonical_reference"],
        "selected_measurement": selected_identity,
        "rebuilt_measurement": rebuilt_identity,
        "equality": equality,
        "verified": all(equality.values()),
    }
    verification["verification_sha256"] = hashlib.sha256(
        b"voxblame.verification/1\0" + _json_bytes(verification)
    ).hexdigest()

    published = False
    if output is not None and verification["verified"]:
        output_path = Path(output)
        body = _json_bytes(verification)
        if output_path.exists():
            if output_path.read_bytes() != body:
                raise OctreeError(
                    "verification output already exists with a different identity"
                )
        else:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            stage = output_path.parent / f".tmp-verification-{uuid.uuid4().hex}"
            try:
                stage.write_bytes(body)
                stage.rename(output_path)
            except Exception:
                if stage.exists():
                    stage.unlink()
                raise
        published = True
    return VerifyStepResult(verification=verification, published=published)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")


__all__ = ["VERIFICATION_SCHEMA", "VerifyStepResult", "verify_step"]
