"""Narrow Workspace facade and provider-free terminal evidence compiler.

``workspace_core`` remains the implementation of the existing Workspace
protocol.  This module is the caller boundary and adds only an in-memory,
closed terminal evidence bundle.  Persistence and crash recovery belong to the
outer runner that owns the returned identity handoff.
"""

from __future__ import annotations

import json
import hashlib
import ctypes
import errno
import os
import platform
from pathlib import Path, PurePosixPath
import shutil
import secrets
import stat
import tempfile
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

import workspace_core as _core


# Preserve the existing public function objects and their stable error behavior
# while moving callers to this module's import boundary.
DEFAULT_COMMAND_SECONDS = _core.DEFAULT_COMMAND_SECONDS
MAX_ATTEMPTS_PER_STEP = _core.MAX_ATTEMPTS_PER_STEP
MAX_REPAIR_CYCLES = _core.MAX_REPAIR_CYCLES
MAX_TOOL_FAILURES_PER_STEP = _core.MAX_TOOL_FAILURES_PER_STEP
FAILED_ATTEMPT_RESULTS = _core.FAILED_ATTEMPT_RESULTS
WorkspaceError = _core.WorkspaceError
ValidationResult = _core.ValidationResult
begin_attempt = _core.begin_attempt
finalize_workspace = _core.finalize_workspace
initialize_workspace = _core.initialize_workspace
publish_step_zero = _core.publish_step_zero
publish_cycle = _core.publish_cycle
record_attempt = _core.record_attempt
recover_workspace = _core.recover_workspace
rebuild_index = _core.rebuild_index
run_attempt_command = _core.run_attempt_command
run_canonical_build = _core.run_canonical_build
validate_workspace = _core.validate_workspace
workspace_status = _core.workspace_status
cancel_active_commands = _core.cancel_active_commands
ExecutionScope = _core.ExecutionScope

REPAIR_EVIDENCE_FAILURE_SCHEMA = _core.REPAIR_EVIDENCE_FAILURE_SCHEMA
_REPAIR_PROVIDER_SUBTYPES = {
    "measurement_failed": "voxblame_output_invalid",
    "region_diff_failed": "region_diff_invalid",
    "preview_failed": "preview_output_invalid",
    "preview_profile_mismatch": "preview_output_invalid",
    "source_changes_failed": "source_changes_invalid",
}


def _repair_evidence_subtype(error: Exception) -> str:
    return _REPAIR_PROVIDER_SUBTYPES.get(
        getattr(error, "classification", None), "provider_execution_failed"
    )


def _write_repair_evidence_failure(active_root: Path, subtype: str) -> None:
    (active_root / "repair-evidence-failure.json").write_text(
        json.dumps(
            {"schema": REPAIR_EVIDENCE_FAILURE_SCHEMA, "subtype": subtype},
            separators=(",", ":"), sort_keys=True
        ) + "\n", encoding="utf-8"
    )


def _repair_evidence_failure(active_root: Path, subtype: str) -> dict[str, str]:
    _write_repair_evidence_failure(active_root, subtype)
    return {
        "state": "failed",
        "classification": "repair_evidence_failed",
        "subtype": subtype,
    }


def workspace_initialized(workspace: Path) -> bool:
    """Return whether the Workspace authority document is readable."""

    workspace = Path(workspace).resolve()
    try:
        _read_workspace_document(workspace)
    except WorkspaceError as error:
        if error.classification in {"invalid_workspace", "incomplete_transaction"}:
            return False
        raise
    return True


def read_canonical_reference_binding(workspace: Path) -> dict[str, Any]:
    """Return the trusted Reference Binding for one initialized Workspace.

    The binding names one absolute Canonical Reference path together with the
    two published digests that identify it — the reference PLY content digest
    and the canonical-reference identity that the Workspace document commits
    to.  Callers use this to bind a Reference Capability without accepting an
    ambient path or an external identity claim.
    """

    workspace = Path(workspace).resolve()
    workspace_document = _read_workspace_document(workspace)
    canonical_reference_sha256 = workspace_document["canonical_reference_sha256"]
    input_manifest = _read_authority_json(
        workspace, workspace / "input/input.json", "$.input.input.json"
    )
    if input_manifest.get("canonical_reference_sha256") != canonical_reference_sha256:
        _fail(
            "identity_conflict",
            "input manifest disagrees with Workspace Canonical Reference identity",
            "$.input.input.json.canonical_reference_sha256",
        )
    reference_ply = input_manifest.get("reference_ply")
    if not isinstance(reference_ply, Mapping):
        _fail(
            "invalid_workspace_path",
            "input manifest has no reference PLY record",
            "$.input.input.json.reference_ply",
        )
    ply_relative = reference_ply.get("path")
    ply_sha256 = reference_ply.get("sha256")
    if ply_relative != "reference.ply":
        _fail(
            "invalid_workspace_path",
            "Canonical Reference must be published at the fixed workspace path",
            "$.input.input.json.reference_ply.path",
        )
    _sha256(ply_sha256, "$.input.input.json.reference_ply.sha256")
    reference_path = workspace / "input" / "reference.ply"
    _safe_relative(workspace, reference_path)
    if reference_path.is_symlink() or not reference_path.is_file():
        _fail(
            "invalid_workspace_path",
            "Canonical Reference is not a regular file",
            "$.input.reference.ply",
        )
    digest = hashlib.sha256()
    with reference_path.open("rb") as stream:
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    if digest.hexdigest() != ply_sha256:
        _fail(
            "identity_conflict",
            "Canonical Reference bytes do not match the published digest",
            "$.input.reference.ply",
        )
    return {
        "path": reference_path,
        "reference_ply_sha256": ply_sha256,
        "canonical_reference_sha256": canonical_reference_sha256,
    }


def _candidate_staging_path(workspace: Path, attempt: int) -> Path:
    """Return the active Attempt's ignored candidate staging directory."""

    workspace = Path(workspace).resolve()
    root, _active, _plan = _core._load_active_attempt(workspace, attempt)
    target = root / "candidate"
    if target.is_symlink():
        _fail("invalid_workspace_path", "candidate staging is a symlink", "$.candidate")
    return target


def _ingest_candidate(
    workspace: Path,
    attempt: int,
    source: Path,
) -> None:
    """Own secure candidate ingestion from one external source capability."""

    target = _candidate_staging_path(workspace, attempt)
    if target.is_symlink():
        _fail("invalid_workspace_path", "candidate staging is a symlink", "$.candidate")
    if target.exists():
        if not target.is_dir():
            _fail("invalid_workspace_path", "candidate staging is not a directory", "$.candidate")
        shutil.rmtree(target)
    try:
        _copy_agent_tree(Path(source), target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


# The trusted candidate tree must expose the fixed producer filename below at
# its root; the Agent never names or selects it.  For Step 0 the trusted
# provider seam (``StepZeroEvidenceProvider``) alone produces measurement and
# formal preview evidence — the candidate tree must NOT carry
# ``measurement.json`` or ``preview/``.  For a Repair Cycle the trusted
# ``RepairEvidenceProvider`` alone produces canonical measurement, formal
# preview, Region Diff, and source-change evidence — the candidate tree must
# NOT carry ``measurement.json``, ``preview/``, ``region-diff.json``, or
# ``source-changes.json``.  W1 rejects submissions that do.
# ``assessment.json`` remains an Agent-authored semantic value (a preview
# observation and a short summary) that W1 rebinds through
# ``mesh-to-cad.assessment/1`` to the actual attempt and current target/batch;
# it never overrides measured facts and cannot originate anywhere else.
CANDIDATE_MESH_RELATIVE = "candidate.glb"
CANDIDATE_ASSESSMENT_RELATIVE = "assessment.json"
_REJECTED_STEP_ZERO_CANDIDATE_NAMES = ("measurement.json", "preview")
_REJECTED_REPAIR_CANDIDATE_NAMES = (
    "measurement.json",
    "preview",
    "region-diff.json",
    "source-changes.json",
)


_MAX_STEP_ZERO_STAGE_FILE_BYTES = 512 * 1024 * 1024
_MAX_REPAIR_STAGE_FILE_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class StepZeroEvidenceRequest:
    """Read-only inputs and W1-owned stage outputs for the Step 0 provider.

    The provider must:
      * Read only ``canonical_reference`` and ``candidate_mesh``.
      * Write only into ``voxblame_output`` and ``preview_output``.
      * Not interpret path locations relative to Workspace authority.

    ``preview_profile`` is the closed ``{name, sha256}`` identity the
    Workspace has already committed for the experiment.
    """

    canonical_reference: Path
    candidate_mesh: Path
    voxblame_output: Path
    preview_output: Path
    preview_profile: Mapping[str, Any]


class StepZeroEvidenceProvider(Protocol):
    """Small internal seam that produces trusted Step 0 evidence bytes.

    Runner-assembled and fixed; the Agent Surface never registers,
    configures, or selects a provider.
    """

    def __call__(self, request: StepZeroEvidenceRequest) -> None: ...


@dataclass(frozen=True)
class RepairEvidenceRequest:
    """Read-only inputs and W1-owned stage outputs for the Repair provider.

    The provider must:
      * Read only ``canonical_reference``, ``candidate_mesh``,
        ``candidate_source``, ``parent_voxblame``, and ``parent_source``.
      * Write only into ``voxblame_output``, ``preview_output``,
        ``region_diff_output``, and ``source_changes_output``.
      * Not interpret path locations relative to Workspace authority.

    ``plan`` is the closed active-Attempt Repair Batch document;
    ``preview_profile`` is the closed ``{name, sha256}`` identity value
    the Workspace has already committed for the experiment;
    ``from_step``/``to_step``/``parent_observable_sha256``/
    ``parent_selected_summary_sha256`` are parent-binding facts already
    committed by W1.
    """

    canonical_reference: Path
    candidate_mesh: Path
    candidate_source: Path
    parent_voxblame: Path
    parent_source: Path
    voxblame_output: Path
    preview_output: Path
    region_diff_output: Path
    source_changes_output: Path
    plan: Mapping[str, Any]
    plan_digest: str
    preview_profile: Mapping[str, Any]
    from_step: int
    to_step: int
    parent_observable_sha256: str
    parent_selected_summary_sha256: str


class RepairEvidenceProvider(Protocol):
    """Small internal seam that produces trusted Repair Cycle evidence.

    Runner-assembled and fixed; the Agent Surface never registers,
    configures, or selects a provider.
    """

    def __call__(self, request: RepairEvidenceRequest) -> None: ...


def _reject_candidate_authored_step_zero_evidence(source: Path) -> None:
    """Fail closed if the trusted candidate tree carries measurement/preview names."""

    try:
        entries = {entry.name for entry in source.iterdir()}
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path",
            "trusted candidate source is unavailable",
        ) from error
    forbidden = tuple(name for name in _REJECTED_STEP_ZERO_CANDIDATE_NAMES if name in entries)
    if forbidden:
        raise WorkspaceError(
            "invalid_step_zero_candidate",
            f"trusted candidate tree must not author {forbidden[0]} for Step 0",
        )


def _reject_candidate_authored_repair_evidence(source: Path) -> None:
    """Fail closed if the trusted candidate tree carries repair evidence names."""

    try:
        entries = {entry.name for entry in source.iterdir()}
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path",
            "trusted candidate source is unavailable",
        ) from error
    forbidden = tuple(
        name for name in _REJECTED_REPAIR_CANDIDATE_NAMES if name in entries
    )
    if forbidden:
        raise WorkspaceError(
            "invalid_repair_candidate",
            f"trusted candidate tree must not author {forbidden[0]} for a Repair Cycle",
        )


def _remove_stage_tree(root: Path) -> None:
    """Remove a private W1 stage even after read-only chmod hardening.

    The provider stage installs read-only permissions on hydrated input
    files/directories to reduce the chance the provider mutates them.
    Cleanup must succeed regardless: relax every directory back to
    writable before running :func:`shutil.rmtree`.
    """

    if not root.exists():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        try:
            os.chmod(dirpath, 0o700)
        except OSError:
            pass
        for name in filenames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o600)
            except OSError:
                pass
        for name in dirnames:
            try:
                os.chmod(os.path.join(dirpath, name), 0o700)
            except OSError:
                pass
    shutil.rmtree(root, ignore_errors=True)


def _open_step_zero_external_stage(workspace: Path) -> Path:
    """Create a private provider stage outside the Workspace tree entirely.

    The trusted Step 0 evidence provider receives only paths from within
    this stage: it cannot observe or interpret any Workspace-relative
    location.  The stage is a fresh directory created by :mod:`tempfile`
    outside the Workspace root, contains ``inputs/`` (populated by W1
    with descriptor-safe copies of the canonical reference and the
    candidate mesh) and an ``outputs/`` parent.  Neither output leaf
    exists before invocation: canonical measurement uses the absence of
    ``outputs/voxblame`` to distinguish a fresh session from a resume,
    and canonical preview publication atomically creates
    ``outputs/preview``.  W1 cleans the stage on every outcome.
    """

    workspace = Path(workspace).resolve()
    stage = Path(
        tempfile.mkdtemp(
            prefix=f"voxblame-step-zero-{secrets.token_hex(6)}-",
        )
    ).resolve()
    try:
        stage.relative_to(workspace)
    except ValueError:
        pass
    else:
        shutil.rmtree(stage, ignore_errors=True)
        raise WorkspaceError(
            "invalid_workspace_path",
            "external provider stage resolved beneath Workspace authority",
        )
    (stage / "inputs").mkdir(mode=0o700)
    (stage / "outputs").mkdir(mode=0o700)
    return stage


def _open_step_zero_internal_promotion_stage(workspace: Path) -> Path:
    """Create a W1-only same-filesystem stage used for atomic promotion.

    The provider never learns this path.  W1 descriptor-copies the
    validated external provider outputs into this stage and then renames
    the voxblame subtree onto ``workspace/voxblame`` atomically.  Same
    filesystem is a hard requirement for atomic rename, which is why
    this stage lives inside the Workspace tree; it is a transient W1
    detail that never crosses the provider seam.
    """

    workspace = Path(workspace).resolve()
    stage = workspace / f".voxblame-promotion-{secrets.token_hex(12)}"
    if stage.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path",
            "internal promotion stage path is a symlink",
        )
    try:
        stage.mkdir(parents=False, exist_ok=False, mode=0o700)
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path",
            "internal promotion stage is unavailable",
        ) from error
    return stage


def _assert_request_paths_outside(
    request: "StepZeroEvidenceRequest", workspace: Path
) -> None:
    """Fail closed if any provider request path resolves inside the Workspace tree.

    A defence-in-depth invariant for the trusted Step 0 seam: the
    provider may only ever observe paths in the private external stage.
    Any attempt to hand it a Workspace-relative location — however
    innocuous — is a policy failure caught here rather than after the
    fact.
    """

    workspace = Path(workspace).resolve()
    for label, path in (
        ("canonical_reference", request.canonical_reference),
        ("candidate_mesh", request.candidate_mesh),
        ("voxblame_output", request.voxblame_output),
        ("preview_output", request.preview_output),
    ):
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError:
            continue
        raise WorkspaceError(
            "invalid_workspace_path",
            f"provider request {label} resolves beneath Workspace authority",
        )


def _assert_stage_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise WorkspaceError("invalid_step_zero_evidence", f"{label} is a symlink")
    try:
        metadata = path.stat()
    except OSError as error:
        raise WorkspaceError(
            "invalid_step_zero_evidence",
            f"{label} is unavailable",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceError(
            "invalid_step_zero_evidence",
            f"{label} is not a regular file",
        )
    if metadata.st_nlink != 1:
        raise WorkspaceError(
            "invalid_step_zero_evidence",
            f"{label} has an unexpected link count",
        )
    if metadata.st_size > _MAX_STEP_ZERO_STAGE_FILE_BYTES:
        raise WorkspaceError(
            "invalid_step_zero_evidence",
            f"{label} exceeds the allowed size",
        )


def _validate_step_zero_stage(voxblame_output: Path, preview_output: Path) -> None:
    """Verify the provider populated the expected file shapes.

    Deep schema/identity validation is performed by
    :func:`publish_step_zero` through the shared closed-schema boundary
    checks; W1's role here is to fail closed on missing files, wrong
    types, symlink/hardlink shapes, or oversized artifacts before the
    stage is promoted into any Workspace authority path.
    """

    required_voxblame = (
        voxblame_output / "session.json",
        voxblame_output / "reference.vbsvo",
        voxblame_output / "steps/000000/summary.json",
    )
    for path in required_voxblame:
        _assert_stage_regular_file(path, f"voxblame stage {path.name}")
    for name in ("preview.json", "preview.png"):
        _assert_stage_regular_file(preview_output / name, f"preview stage {name}")


def _promote_step_zero_voxblame(workspace: Path, internal_voxblame: Path) -> Path:
    """Atomically publish the W1-owned internal voxblame stage into authority.

    ``internal_voxblame`` must already contain descriptor-safe copies of
    the provider's validated voxblame outputs.  Fails closed if the
    target already exists to preserve the single-writer invariant.
    Returns the promoted ``summary.json`` path suitable for
    :func:`publish_step_zero`.
    """

    workspace = Path(workspace).resolve()
    target = workspace / "voxblame"
    if target.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path",
            "voxblame authority path is a symlink",
        )
    if target.exists():
        raise WorkspaceError(
            "workspace_conflict",
            "voxblame authority path already exists before Step 0 publication",
        )
    try:
        os.rename(internal_voxblame, target)
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path",
            "cannot atomically promote step zero voxblame stage",
        ) from error
    return target / "steps/000000/summary.json"


def _rollback_step_zero_voxblame(workspace: Path) -> None:
    """Best-effort cleanup for a failed Step 0 publication.

    Removes any Workspace-owned ``voxblame/`` bytes W1 promoted from the
    stage before :func:`publish_step_zero` completed its commit.  If a
    committed step already exists this is left untouched.
    """

    workspace = Path(workspace).resolve()
    committed_step_zero = workspace / "steps/000000"
    if committed_step_zero.exists():
        return
    voxblame = workspace / "voxblame"
    if voxblame.is_symlink():
        return
    if voxblame.is_dir():
        shutil.rmtree(voxblame, ignore_errors=True)


def _open_repair_external_stage(workspace: Path) -> Path:
    """Create a private Repair provider stage outside the Workspace tree.

    The trusted Repair evidence provider receives only paths from within
    this stage: it cannot observe or interpret any Workspace-relative
    location.  The stage is a fresh directory created by :mod:`tempfile`
    outside the Workspace root, contains ``inputs/`` (populated by W1
    with descriptor-safe copies of the canonical reference, the current
    candidate mesh and source subtree, the parent Measured Step's
    voxblame subtree and the parent selected candidate source), and
    ``outputs/`` (with a precreated ``voxblame/`` session root plus absent
    ``preview/``, ``region-diff.json`` and ``source-changes.json`` publication
    targets) the provider must fill.  W1 cleans the stage on every outcome.
    """

    workspace = Path(workspace).resolve()
    stage = Path(
        tempfile.mkdtemp(
            prefix=f"voxblame-repair-{secrets.token_hex(6)}-",
        )
    ).resolve()
    try:
        stage.relative_to(workspace)
    except ValueError:
        pass
    else:
        shutil.rmtree(stage, ignore_errors=True)
        raise WorkspaceError(
            "invalid_workspace_path",
            "external provider stage resolved beneath Workspace authority",
        )
    (stage / "inputs").mkdir(mode=0o700)
    (stage / "outputs").mkdir(mode=0o700)
    (stage / "outputs/voxblame").mkdir(mode=0o700)
    return stage


def _assert_repair_request_paths_outside(
    request: "RepairEvidenceRequest", workspace: Path
) -> None:
    """Fail closed if any provider request path resolves inside the Workspace tree."""

    workspace = Path(workspace).resolve()
    labels = (
        ("canonical_reference", request.canonical_reference),
        ("candidate_mesh", request.candidate_mesh),
        ("candidate_source", request.candidate_source),
        ("parent_voxblame", request.parent_voxblame),
        ("parent_source", request.parent_source),
        ("voxblame_output", request.voxblame_output),
        ("preview_output", request.preview_output),
        ("region_diff_output", request.region_diff_output),
        ("source_changes_output", request.source_changes_output),
    )
    for label, path in labels:
        resolved = Path(path).resolve()
        try:
            resolved.relative_to(workspace)
        except ValueError:
            continue
        raise WorkspaceError(
            "invalid_workspace_path",
            f"provider request {label} resolves beneath Workspace authority",
        )


def _assert_repair_stage_regular_file(path: Path, label: str) -> None:
    """Fail closed unless ``path`` is a bounded regular file suitable for promotion."""

    if path.is_symlink():
        raise WorkspaceError("invalid_repair_evidence", f"{label} is a symlink")
    try:
        metadata = path.stat()
    except OSError as error:
        raise WorkspaceError(
            "invalid_repair_evidence",
            f"{label} is unavailable",
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise WorkspaceError(
            "invalid_repair_evidence",
            f"{label} is not a regular file",
        )
    if metadata.st_nlink != 1:
        raise WorkspaceError(
            "invalid_repair_evidence",
            f"{label} has an unexpected link count",
        )
    if metadata.st_size > _MAX_REPAIR_STAGE_FILE_BYTES:
        raise WorkspaceError(
            "invalid_repair_evidence",
            f"{label} exceeds the allowed size",
        )


def _validate_repair_stage(
    voxblame_output: Path,
    preview_output: Path,
    region_diff_output: Path,
    source_changes_output: Path,
    *,
    intended_step: int,
) -> None:
    """Verify the provider populated the expected file shapes.

    Deep schema/identity validation is performed by
    :func:`publish_cycle` through the shared closed-schema boundary
    checks; W1's role here is to fail closed on missing files, wrong
    types, symlink/hardlink shapes, or oversized artifacts before the
    stage is promoted into any Workspace authority path.
    """

    step_root = voxblame_output / "steps" / f"{intended_step:06d}"
    required_voxblame = (
        voxblame_output / "session.json",
        voxblame_output / "reference.vbsvo",
        step_root / "summary.json",
        step_root / "measurement.json",
    )
    try:
        for path in required_voxblame:
            _assert_repair_stage_regular_file(path, f"voxblame stage {path.name}")
    except WorkspaceError as error:
        raise _core.RepairEvidenceFailure("voxblame_output_invalid") from error
    try:
        for name in ("preview.json", "preview.png"):
            _assert_repair_stage_regular_file(preview_output / name, f"preview stage {name}")
    except WorkspaceError as error:
        raise _core.RepairEvidenceFailure("preview_output_invalid") from error
    try:
        _assert_repair_stage_regular_file(region_diff_output, "region-diff stage")
    except WorkspaceError as error:
        raise _core.RepairEvidenceFailure("region_diff_invalid") from error
    try:
        _assert_repair_stage_regular_file(source_changes_output, "source-changes stage")
    except WorkspaceError as error:
        raise _core.RepairEvidenceFailure("source_changes_invalid") from error


def _promote_repair_voxblame_step(
    workspace: Path, internal_voxblame_step: Path, intended_step: int
) -> Path:
    """Atomically publish one voxblame step subtree into workspace authority.

    Fails closed if the target already exists to preserve the
    single-writer invariant.  Returns the promoted ``summary.json``
    path suitable for :func:`publish_cycle`.
    """

    workspace = Path(workspace).resolve()
    steps_root = workspace / "voxblame/steps"
    steps_root.mkdir(parents=True, exist_ok=True)
    if steps_root.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path",
            "voxblame steps authority path is a symlink",
        )
    target = steps_root / f"{intended_step:06d}"
    if target.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path",
            "voxblame step authority path is a symlink",
        )
    if target.exists():
        raise WorkspaceError(
            "workspace_conflict",
            "voxblame step authority path already exists before Repair Cycle publication",
        )
    try:
        os.rename(internal_voxblame_step, target)
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path",
            "cannot atomically promote repair voxblame step",
        ) from error
    return target / "summary.json"


def _rollback_repair_voxblame_step(workspace: Path, intended_step: int) -> None:
    """Best-effort cleanup for a failed Repair Cycle publication.

    Removes the ``voxblame/steps/{intended_step}`` bytes W1 promoted
    from the stage before :func:`publish_cycle` completed its commit.
    If a committed step already exists this is left untouched.
    """

    workspace = Path(workspace).resolve()
    committed_step = workspace / "steps" / f"{intended_step:06d}"
    if committed_step.exists():
        return
    step_root = workspace / "voxblame/steps" / f"{intended_step:06d}"
    if step_root.is_symlink():
        return
    if step_root.is_dir():
        shutil.rmtree(step_root, ignore_errors=True)


def publish_step_zero_from_candidate(
    workspace: Path,
    *,
    attempt: int,
    source: Path,
    evidence_provider: StepZeroEvidenceProvider,
) -> dict[str, Any]:
    """Ingest one trusted candidate tree, produce trusted evidence, publish Step 0.

    W1 owns every mutation.  The trusted candidate tree carries only its
    Agent-authored source and the fixed ``candidate.glb`` output; W1
    rejects candidate-authored ``measurement.json`` or ``preview/``.  W1
    then opens an opaque stage outside Workspace authority, invokes the
    fixed :class:`StepZeroEvidenceProvider` supplied by the runner, and
    validates the produced canonical measurement and formal preview
    bytes before atomically promoting the measurement bytes into
    ``voxblame/`` and calling :func:`publish_step_zero`.  The stage is
    cleaned on every outcome and the promoted bytes are rolled back on
    failure.
    """

    workspace = Path(workspace).resolve()
    raw_source = Path(source)
    if raw_source.is_symlink() or not raw_source.is_dir():
        raise WorkspaceError("invalid_workspace_path", "trusted candidate source is unavailable")
    source = raw_source.resolve()
    _reject_candidate_authored_step_zero_evidence(source)

    _ingest_candidate(workspace, attempt, source)
    authority = _core._load_active_attempt(workspace, attempt)[0] / "candidate"
    workspace_document = _read_workspace_document(workspace)
    preview_profile = workspace_document.get("preview_profile")
    if not isinstance(preview_profile, Mapping):
        raise WorkspaceError(
            "corrupt_workspace",
            "Workspace document is missing the preview profile",
        )

    external_stage = _open_step_zero_external_stage(workspace)
    internal_stage: Path | None = None
    promoted = False
    try:
        # W1 owns descriptor-safe input hydration: only bytes copied from
        # authority land in the external stage; the provider never sees
        # any Workspace path.
        candidate_mesh_authority = _agent_source_file(
            authority, CANDIDATE_MESH_RELATIVE
        )
        external_candidate_mesh = external_stage / "inputs/candidate.glb"
        _copy_agent_file(candidate_mesh_authority, external_candidate_mesh)
        external_reference = external_stage / "inputs/reference"
        _copy_agent_tree(workspace / "input", external_reference)
        os.chmod(external_reference, 0o500)
        os.chmod(external_candidate_mesh, 0o400)

        external_voxblame_output = external_stage / "outputs/voxblame"
        external_preview_output = external_stage / "outputs/preview"
        request = StepZeroEvidenceRequest(
            canonical_reference=external_reference,
            candidate_mesh=external_candidate_mesh,
            voxblame_output=external_voxblame_output,
            preview_output=external_preview_output,
            preview_profile={
                "name": preview_profile.get("name"),
                "sha256": preview_profile.get("sha256"),
            },
        )
        _assert_request_paths_outside(request, workspace)
        try:
            evidence_provider(request)
        except WorkspaceError:
            raise
        except Exception as error:
            raise WorkspaceError(
                "step_zero_evidence_failed",
                f"trusted Step 0 evidence provider failed: {error.__class__.__name__}",
            ) from error
        _validate_step_zero_stage(external_voxblame_output, external_preview_output)

        # Descriptor-copy the validated external outputs into a private
        # same-filesystem W1 stage.  The provider never learns this path;
        # only after the copy succeeds is the voxblame subtree renamed
        # onto workspace/voxblame atomically.
        internal_stage = _open_step_zero_internal_promotion_stage(workspace)
        internal_voxblame = internal_stage / "voxblame"
        internal_preview = internal_stage / "preview"
        _copy_agent_tree(external_voxblame_output, internal_voxblame)
        _copy_agent_tree(external_preview_output, internal_preview)

        measurement_target = _promote_step_zero_voxblame(workspace, internal_voxblame)
        promoted = True
        try:
            result = publish_step_zero(
                workspace,
                attempt=attempt,
                candidate=authority,
                candidate_mesh=_agent_relative(authority, CANDIDATE_MESH_RELATIVE),
                measurement=measurement_target,
                preview=internal_preview,
                _decision_facts_factory=_decision_facts_factory(workspace),
            )
        except Exception:
            _rollback_step_zero_voxblame(workspace)
            promoted = False
            raise
    except Exception:
        if promoted:
            _rollback_step_zero_voxblame(workspace)
        raise
    finally:
        _remove_stage_tree(external_stage)
        if internal_stage is not None:
            _remove_stage_tree(internal_stage)

    step_value = result.get("step", 0)
    if isinstance(step_value, Mapping):
        step_value = step_value.get("step", 0)
    decision_facts = result.get("_decision_facts")
    if not isinstance(decision_facts, Mapping):
        raise WorkspaceError(
            "corrupt_workspace",
            "publication did not return decision facts",
        )
    return {"step": int(step_value), "decision_facts": dict(decision_facts)}


def publish_cycle_from_candidate(
    workspace: Path,
    *,
    attempt: int,
    source: Path,
    evidence_provider: RepairEvidenceProvider,
) -> dict[str, Any]:
    """Ingest one trusted candidate tree, produce trusted evidence, publish a Repair Cycle.

    W1 owns every mutation.  The trusted candidate tree carries only
    its Agent-authored source, the fixed ``candidate.glb`` output, and
    an Agent-authored ``assessment.json`` semantic value; W1 rejects
    candidate-authored ``measurement.json``, ``preview/``,
    ``region-diff.json`` or ``source-changes.json``.  W1 then opens an
    opaque stage outside Workspace authority, invokes the fixed
    :class:`RepairEvidenceProvider` supplied by the runner, and
    validates the produced canonical measurement, formal preview,
    Region Diff, and source-change evidence before atomically
    promoting the voxblame step subtree into
    ``voxblame/steps/{step}/`` and calling :func:`publish_cycle`.  The
    stage is cleaned on every outcome and the promoted bytes are
    rolled back on failure.
    """

    workspace = Path(workspace).resolve()
    raw_source = Path(source)
    if raw_source.is_symlink() or not raw_source.is_dir():
        raise WorkspaceError(
            "invalid_workspace_path", "trusted candidate source is unavailable"
        )
    source = raw_source.resolve()
    _reject_candidate_authored_repair_evidence(source)

    _ingest_candidate(workspace, attempt, source)
    active_root, active, plan = _core._load_active_attempt(workspace, attempt)
    authority = active_root / "candidate"
    intended_step = int(active["intended_step"])
    from_step_value = active.get("from_step")
    if not isinstance(from_step_value, int) or isinstance(from_step_value, bool):
        raise WorkspaceError(
            "invalid_attempt", "Repair Cycle attempt has no parent Measured Step"
        )
    from_step = int(from_step_value)

    workspace_document = _read_workspace_document(workspace)
    preview_profile = workspace_document.get("preview_profile")
    if not isinstance(preview_profile, Mapping):
        raise WorkspaceError(
            "corrupt_workspace",
            "Workspace document is missing the preview profile",
        )

    parent_step_manifest = _read_authority_json(
        workspace,
        workspace / "steps" / f"{from_step:06d}" / "step.json",
        f"$.steps[{from_step}]",
    )
    parent_observable_sha256 = parent_step_manifest.get("observable_sha256")
    parent_preview_identity = parent_step_manifest.get("preview_identity_sha256")
    if not isinstance(parent_observable_sha256, str) or not isinstance(
        parent_preview_identity, str
    ):
        raise WorkspaceError(
            "corrupt_workspace",
            "parent Measured Step manifest is missing required identity facts",
        )

    parent_voxblame_authority = workspace / "voxblame"
    parent_candidate_source_authority = (
        workspace / "steps" / f"{from_step:06d}" / "candidate" / "source"
    )

    external_stage = _open_repair_external_stage(workspace)
    internal_stage: Path | None = None
    promoted = False
    try:
        # W1 owns descriptor-safe input hydration: only bytes copied
        # from authority land in the external stage; the provider never
        # sees any Workspace path.
        candidate_mesh_authority = _agent_source_file(
            authority, CANDIDATE_MESH_RELATIVE
        )
        external_candidate_mesh = external_stage / "inputs/candidate.glb"
        _copy_agent_file(candidate_mesh_authority, external_candidate_mesh)
        external_candidate_source = external_stage / "inputs/candidate-source"
        _copy_agent_tree(authority / "source", external_candidate_source)
        external_reference = external_stage / "inputs/reference"
        _copy_agent_tree(workspace / "input", external_reference)
        external_parent_voxblame = external_stage / "inputs/parent-voxblame"
        _copy_agent_tree(parent_voxblame_authority, external_parent_voxblame)
        external_parent_source = external_stage / "inputs/parent-source"
        _copy_agent_tree(parent_candidate_source_authority, external_parent_source)
        for readonly_root in (
            external_reference,
            external_parent_voxblame,
            external_parent_source,
            external_candidate_source,
        ):
            os.chmod(readonly_root, 0o500)
        os.chmod(external_candidate_mesh, 0o400)

        external_voxblame_output = external_stage / "outputs/voxblame"
        external_preview_output = external_stage / "outputs/preview"
        external_region_diff_output = external_stage / "outputs/region-diff.json"
        external_source_changes_output = external_stage / "outputs/source-changes.json"

        request = RepairEvidenceRequest(
            canonical_reference=external_reference,
            candidate_mesh=external_candidate_mesh,
            candidate_source=external_candidate_source,
            parent_voxblame=external_parent_voxblame,
            parent_source=external_parent_source,
            voxblame_output=external_voxblame_output,
            preview_output=external_preview_output,
            region_diff_output=external_region_diff_output,
            source_changes_output=external_source_changes_output,
            plan=_resolve_repair_provider_plan(workspace, plan, from_step=from_step),
            plan_digest=active["plan_digest"],
            preview_profile={
                "name": preview_profile.get("name"),
                "sha256": preview_profile.get("sha256"),
            },
            from_step=from_step,
            to_step=intended_step,
            parent_observable_sha256=parent_observable_sha256,
            parent_selected_summary_sha256=parent_preview_identity,
        )
        _assert_repair_request_paths_outside(request, workspace)
        try:
            evidence_provider(request)
            _validate_repair_stage(
                external_voxblame_output,
                external_preview_output,
                external_region_diff_output,
                external_source_changes_output,
                intended_step=intended_step,
            )
        except _core.RepairEvidenceFailure as error:
            return _repair_evidence_failure(active_root, error.subtype)
        except WorkspaceError:
            raise
        except Exception as error:
            return _repair_evidence_failure(
                active_root, _repair_evidence_subtype(error)
            )

        # Descriptor-copy the validated external outputs into a private
        # same-filesystem W1 stage.  The provider never learns this
        # path; only after the copy succeeds is the voxblame step
        # subtree renamed onto workspace/voxblame/steps/{step}/
        # atomically.  Preview, Region Diff, and source-change bytes
        # live in the internal stage during publication and are
        # copied by ``publish_cycle`` into the cycle stage.
        internal_stage = _open_step_zero_internal_promotion_stage(workspace)
        internal_voxblame_step = internal_stage / "voxblame-step"
        internal_preview = internal_stage / "preview"
        internal_region_diff = internal_stage / "region-diff.json"
        internal_source_changes = internal_stage / "source-changes.json"
        _copy_agent_tree(
            external_voxblame_output / "steps" / f"{intended_step:06d}",
            internal_voxblame_step,
        )
        _copy_agent_tree(external_preview_output, internal_preview)
        _copy_agent_file(external_region_diff_output, internal_region_diff)
        _copy_agent_file(external_source_changes_output, internal_source_changes)

        measurement_target = _promote_repair_voxblame_step(
            workspace, internal_voxblame_step, intended_step
        )
        promoted = True
        try:
            result = publish_cycle(
                workspace,
                attempt=attempt,
                candidate=authority,
                candidate_mesh=_agent_relative(authority, CANDIDATE_MESH_RELATIVE),
                measurement=measurement_target,
                preview=internal_preview,
                region_diff=internal_region_diff,
                assessment=authority
                / _agent_relative(authority, CANDIDATE_ASSESSMENT_RELATIVE),
                source_changes=internal_source_changes,
                _decision_facts_factory=_decision_facts_factory(workspace),
                _repair_evidence_failures=True,
            )
        except _core.RepairEvidenceFailure as error:
            _rollback_repair_voxblame_step(workspace, intended_step)
            promoted = False
            return _repair_evidence_failure(active_root, error.subtype)
        except Exception:
            _rollback_repair_voxblame_step(workspace, intended_step)
            promoted = False
            raise
    except Exception:
        if promoted:
            _rollback_repair_voxblame_step(workspace, intended_step)
        raise
    finally:
        _remove_stage_tree(external_stage)
        if internal_stage is not None:
            _remove_stage_tree(internal_stage)

    step_value = result.get("step", 0)
    if isinstance(step_value, Mapping):
        step_value = step_value.get("step", 0)
    cycle_value = result.get("cycle", step_value)
    if isinstance(cycle_value, Mapping):
        cycle_value = cycle_value.get("cycle", 0)
    decision_facts = result.get("_decision_facts")
    if not isinstance(decision_facts, Mapping):
        raise WorkspaceError(
            "corrupt_workspace",
            "publication did not return decision facts",
        )
    return {
        "step": {"step": int(step_value)},
        "cycle": int(cycle_value),
        "decision_facts": dict(decision_facts),
    }


def seed_repair_source_from_parent_step(
    workspace: Path,
    *,
    attempt: int,
    from_step: int,
    destination: Path,
) -> None:
    """Descriptor-safely seed one external empty work tree with parent Step source.

    The supervisor gives W1 an already-created, empty, external work tree
    ``destination`` and the ``attempt``/``from_step`` pair that names the
    parent Measured Step in the active Attempt document.  W1 verifies the
    parent binding against the active Attempt, opens the parent Measured
    Step's committed ``candidate/source/`` subtree internally, and copies
    its bytes through the descriptor-safe :func:`_copy_agent_tree` guard
    into ``destination/source/``.  The supervisor never learns, forwards,
    or names an authority path.  A symlink, hardlink, FIFO, oversized, or
    growing source aborts; a partial ``destination/source/`` is removed
    on failure so the fresh work tree stays empty for retry.
    """

    workspace = Path(workspace).resolve()
    if type(attempt) is not int or attempt < 1:
        raise WorkspaceError(
            "invalid_attempt", "attempt identifier is invalid"
        )
    if type(from_step) is not int or from_step < 0:
        raise WorkspaceError(
            "invalid_attempt", "from_step is not a valid parent Step index"
        )

    raw_destination = Path(destination)
    if raw_destination.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path", "seed destination is a symlink"
        )
    if not raw_destination.is_dir():
        raise WorkspaceError(
            "invalid_workspace_path", "seed destination is not a directory"
        )
    destination = raw_destination.resolve()
    try:
        destination.relative_to(workspace)
    except ValueError:
        pass
    else:
        raise WorkspaceError(
            "invalid_workspace_path",
            "seed destination resolves beneath Workspace authority",
        )
    with os.scandir(destination) as entries:
        for _ in entries:
            raise WorkspaceError(
                "invalid_workspace_path", "seed destination is not empty"
            )

    active_root, active, _plan = _core._load_active_attempt(workspace, attempt)
    active_from_step = active.get("from_step")
    if not isinstance(active_from_step, int) or isinstance(active_from_step, bool):
        raise WorkspaceError(
            "invalid_attempt",
            "active Attempt has no parent Measured Step",
        )
    if active_from_step != from_step:
        raise WorkspaceError(
            "invalid_attempt",
            "from_step disagrees with the active Attempt parent binding",
        )
    intended_step = active.get("intended_step")
    if not isinstance(intended_step, int) or intended_step <= from_step:
        raise WorkspaceError(
            "invalid_attempt",
            "active Attempt intended step is not greater than from_step",
        )

    # Reading the parent step manifest through the authority-JSON guard
    # ensures the Step is committed and rejects a forged directory that
    # lacks a valid manifest.
    _read_authority_json(
        workspace,
        workspace / "steps" / f"{from_step:06d}" / "step.json",
        f"$.steps[{from_step}]",
    )
    authority_source = workspace / "steps" / f"{from_step:06d}" / "candidate" / "source"
    if authority_source.is_symlink():
        raise WorkspaceError(
            "invalid_workspace_path",
            "parent Step source authority is a symlink",
        )
    if not authority_source.is_dir():
        raise WorkspaceError(
            "invalid_workspace_path",
            "parent Step source authority is unavailable",
        )

    seeded = destination / "source"
    try:
        _copy_agent_tree(authority_source, seeded)
    except Exception:
        if seeded.exists() and not seeded.is_symlink():
            shutil.rmtree(seeded, ignore_errors=True)
        elif seeded.is_symlink():
            seeded.unlink()
        raise


DECISION_FACTS_SCHEMA = "mesh-to-cad.decision-facts/1"
_MAX_DECISION_FACT_TARGETS = 8
_MAX_DEPTH = 8
_MAX_FORMAL_PREVIEW_PNG_BYTES = 16 * 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_OBJECTIVE_FACT_KEYS = ("global_depth_8_zero", "out_of_frame_clear", "no_evidence_conflict")


def _decision_facts_fail(detail: str) -> None:
    _fail("corrupt_workspace", detail)


def _fact_int(value: Any, detail: str) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 0:
        _decision_facts_fail(detail)
    return value


def _fact_bool(value: Any, detail: str) -> bool:
    if type(value) is not bool:
        _decision_facts_fail(detail)
    return value


def _fact_rate(value: Any, detail: str) -> float:
    if type(value) is bool or type(value) not in {int, float}:
        _decision_facts_fail(detail)
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        _decision_facts_fail(detail)
    if number < 0.0 or number > 1.0:
        _decision_facts_fail(detail)
    return number


def _fact_number(value: Any, detail: str) -> int | float:
    if type(value) not in {int, float} or isinstance(value, bool):
        _decision_facts_fail(detail)
    if value != value or value in {float("inf"), float("-inf")}:
        _decision_facts_fail(detail)
    return value


def _fact_bounds_canonical(value: Any, detail: str) -> dict[str, list[int | float]]:
    if not isinstance(value, Mapping) or set(value) != {"min", "max"}:
        _decision_facts_fail(detail)
    bounds: dict[str, list[int | float]] = {}
    for name in ("min", "max"):
        vector = value[name]
        if not isinstance(vector, list) or len(vector) != 3:
            _decision_facts_fail(detail)
        bounds[name] = [
            _fact_number(component, detail) for component in vector
        ]
    if any(low > high for low, high in zip(bounds["min"], bounds["max"])):
        _decision_facts_fail(detail)
    return bounds


def _active_depth_cells(workspace: Path, item: Mapping[str, Any], depth: int) -> tuple[tuple[int, int, int], ...]:
    """Project one host-only canonical region mask to its occupied coarse cells."""

    mask = item.get("mask")
    if not isinstance(mask, Mapping) or type(mask.get("path")) is not str:
        _decision_facts_fail("decision-facts repair target mask is malformed")
    relative = PurePosixPath(mask["path"])
    if relative.is_absolute() or ".." in relative.parts:
        _decision_facts_fail("decision-facts repair target mask path is malformed")
    try:
        snapshot = json.loads((workspace / Path(relative)).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _decision_facts_fail("decision-facts repair target mask is unavailable")
    if (
        not isinstance(snapshot, Mapping)
        or set(snapshot) != {"schema", "max_depth", "regions"}
        or snapshot["schema"] != "octree_region_set/1"
        or snapshot["max_depth"] != _MAX_DEPTH
        or not isinstance(snapshot["regions"], list)
    ):
        _decision_facts_fail("decision-facts repair target mask is malformed")
    prefixes: set[int] = set()
    for region in snapshot["regions"]:
        if not isinstance(region, Mapping) or set(region) != {"depth", "prefix"}:
            _decision_facts_fail("decision-facts repair target mask region is malformed")
        region_depth, prefix = region["depth"], region["prefix"]
        if (
            type(region_depth) is not int or isinstance(region_depth, bool)
            or type(prefix) is not int or isinstance(prefix, bool)
            or not 0 <= region_depth <= _MAX_DEPTH
            or not 0 <= prefix < 1 << (3 * region_depth)
        ):
            _decision_facts_fail("decision-facts repair target mask region is malformed")
        if region_depth >= depth:
            prefixes.add(prefix >> (3 * (region_depth - depth)))
        else:
            shift = 3 * (depth - region_depth)
            prefixes.update(range(prefix << shift, (prefix + 1) << shift))
    cells = []
    for prefix in sorted(prefixes):
        coordinates = [0, 0, 0]
        for shift in range(depth - 1, -1, -1):
            child = (prefix >> (3 * shift)) & 7
            coordinates[0] = (coordinates[0] << 1) | ((child >> 2) & 1)
            coordinates[1] = (coordinates[1] << 1) | ((child >> 1) & 1)
            coordinates[2] = (coordinates[2] << 1) | (child & 1)
        cells.append(tuple(coordinates))
    return tuple(cells)


def _active_depth_bounds(cell: tuple[int, int, int], depth: int) -> dict[str, list[float]]:
    width = 1.0 / (2 ** depth)
    return {
        "min": [round(-0.5 + index * width, 6) for index in cell],
        "max": [round(-0.5 + (index + 1) * width, 6) for index in cell],
    }


def _resolve_repair_provider_plan(
    workspace: Path, plan: Mapping[str, Any], *, from_step: int
) -> dict[str, Any]:
    """Expand Agent active-depth targets into the canonical Region Diff plan."""

    measurement = _read_authority_json(
        workspace,
        workspace / "voxblame" / "steps" / f"{from_step:06d}" / "measurement.json",
        "$.voxblame.parent.measurement",
    )
    active_depth = _project_residual_summary(measurement)["repair_frontier"][
        "active_depth"
    ]
    ordered_targets = measurement["repair_targets"]["ordered_targets"]
    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
    for target in ordered_targets:
        if target["kind"] == "exterior":
            key = ("exterior", target["target_key"])
            groups[key] = {
                "rank": target["display_rank"],
                "kind": "exterior",
                "bounds_canonical": target["bounds_canonical"],
                "targets": [target],
            }
            continue
        if active_depth is None:
            _decision_facts_fail("interior repair target has no active depth")
        for cell in _active_depth_cells(workspace, target, active_depth):
            key = ("interior", *cell)
            group = groups.get(key)
            if group is None:
                groups[key] = group = {
                    "rank": target["display_rank"],
                    "kind": "interior",
                    "bounds_canonical": _active_depth_bounds(cell, active_depth),
                    "targets": [],
                }
            group["rank"] = min(group["rank"], target["display_rank"])
            group["targets"].append(target)

    ordered_groups = sorted(
        groups.values(),
        key=lambda item: (
            item["rank"],
            item["kind"],
            item["bounds_canonical"]["min"],
        ),
    )
    for rank, group in enumerate(ordered_groups):
        group["rank"] = rank

    selected_groups: dict[int, dict[str, Any]] = {}
    for item in plan["selected_targets"]:
        matches = [
            group
            for group in ordered_groups
            if group["rank"] == item["rank"]
            and group["kind"] == item["kind"]
            and group["bounds_canonical"] == item["bounds_canonical"]
        ]
        if len(matches) != 1:
            _decision_facts_fail("repair target does not match active-depth authority")
        selected_groups[item["rank"]] = matches[0]

    resolved = dict(plan)
    selected_targets: list[dict[str, str]] = []
    selected_keys: set[str] = set()
    for item in plan["selected_targets"]:
        for target in selected_groups[item["rank"]]["targets"]:
            key = target["target_key"]
            if key not in selected_keys:
                selected_keys.add(key)
                selected_targets.append(
                    {
                        "target_key": key,
                        "mask_sha256": target["mask"]["logical_sha256"],
                    }
                )
    resolved["selected_targets"] = selected_targets
    resolved_edits: list[dict[str, Any]] = []
    for edit in plan["planned_edits"]:
        edit_targets: list[str] = []
        edit_keys: set[str] = set()
        for rank in edit["target_ranks"]:
            for target in selected_groups[rank]["targets"]:
                key = target["target_key"]
                if key not in edit_keys:
                    edit_keys.add(key)
                    edit_targets.append(key)
        resolved_edits.append(
            {
                "edit_key": edit["edit_key"],
                "target_keys": edit_targets,
                "description": edit["description"],
            }
        )
    resolved["planned_edits"] = resolved_edits
    return resolved


def _project_repair_targets(workspace: Path, value: Any, *, active_depth: int | None) -> Any:
    if not isinstance(value, Mapping):
        _decision_facts_fail("decision-facts repair targets are malformed")
    total = _fact_int(value.get("total"), "decision-facts repair target total is malformed")
    items = value.get("ordered_targets")
    if not isinstance(items, list) or len(items) != total:
        _decision_facts_fail("decision-facts repair target authority is malformed")
    if total == 0:
        return None
    grouped: dict[tuple[int, int, int], dict[str, Any]] = {}
    exterior: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            _decision_facts_fail("decision-facts repair target item is malformed")
        display_rank = item.get("display_rank")
        rank = _fact_int(display_rank, "decision-facts repair target rank is malformed")
        kind = item.get("kind")
        if kind not in ("interior", "exterior"):
            _decision_facts_fail("decision-facts repair target kind is malformed")
        bounds = _fact_bounds_canonical(item.get("bounds_canonical"), "decision-facts repair target bounds are malformed")
        if kind == "exterior":
            exterior.append({"rank": rank, "kind": kind, "bounds_canonical": bounds})
            continue
        if active_depth is None:
            _decision_facts_fail("interior repair target has no active depth")
        for cell in _active_depth_cells(workspace, item, active_depth):
            current = grouped.get(cell)
            if current is None or rank < current["rank"]:
                grouped[cell] = {
                    "rank": rank,
                    "kind": "interior",
                    "bounds_canonical": _active_depth_bounds(cell, active_depth),
                }
    ordered = sorted(
        [*grouped.values(), *exterior],
        key=lambda item: (
            item.get("rank", total),
            item["kind"],
            item["bounds_canonical"]["min"],
        ),
    )
    for rank, item in enumerate(ordered):
        item["rank"] = rank
    projected_items = ordered[:_MAX_DECISION_FACT_TARGETS]
    return {
        "total": len(ordered),
        "returned": len(projected_items),
        "remaining": len(ordered) - len(projected_items),
        "items": projected_items,
    }


def _project_residual_summary(measurement: Mapping[str, Any]) -> dict[str, Any]:
    facts = measurement.get("objective_facts")
    if not isinstance(facts, Mapping) or set(facts) != set(_OBJECTIVE_FACT_KEYS):
        _decision_facts_fail("decision-facts objective_facts are malformed")
    errors = measurement.get("errors_by_depth")
    if not isinstance(errors, list) or len(errors) != _MAX_DEPTH:
        _decision_facts_fail("decision-facts errors_by_depth is malformed")
    active_depth: int | None = None
    active_error: Mapping[str, Any] | None = None
    for depth, error in enumerate(errors, start=1):
        if not isinstance(error, Mapping) or error.get("depth") != depth:
            _decision_facts_fail("decision-facts depth evidence is malformed")
        surface_error_count = _fact_int(
            error.get("surface_error_count"),
            "decision-facts surface error count is malformed",
        )
        if active_depth is None and surface_error_count:
            active_depth = depth
            active_error = error
    if active_error is None:
        repair_frontier = {
            "active_depth": None,
            "missing_surface_count": 0,
            "excess_surface_count": 0,
            "surface_error_count": 0,
            "surface_error_rate": 0.0,
        }
    else:
        repair_frontier = {
            "active_depth": active_depth,
            "missing_surface_count": _fact_int(
                active_error.get("missing_surface_count"),
                "decision-facts repair frontier missing count is malformed",
            ),
            "excess_surface_count": _fact_int(
                active_error.get("excess_surface_count"),
                "decision-facts repair frontier excess count is malformed",
            ),
            "surface_error_count": _fact_int(
                active_error.get("surface_error_count"),
                "decision-facts repair frontier error count is malformed",
            ),
            "surface_error_rate": _fact_rate(
                active_error.get("surface_error_rate"),
                "decision-facts repair frontier error rate is malformed",
            ),
        }
    return {"repair_frontier": repair_frontier}


def _build_decision_facts(
    step_document: Mapping[str, Any],
    measurement_document: Mapping[str, Any],
    parent_document: Mapping[str, Any] | None,
    *,
    workspace: Path,
    repair_target_document: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project facts from the validated publication documents in memory."""

    step = _fact_int(step_document.get("step"), "step manifest ordinal is malformed")
    accepted = _fact_bool(
        step_document.get("accepted"), "step manifest accepted is malformed"
    )
    parent_value = step_document.get("parent_step")
    if parent_value is None:
        parent_ordinal = None
        change_from_parent = None
    else:
        parent_ordinal = _fact_int(
            parent_value, "step manifest parent ordinal is malformed"
        )
        if parent_document is None:
            _decision_facts_fail("parent step manifest is unavailable")
        change_from_parent = {
            "no_observable_geometry_change": _fact_bool(
                step_document.get("no_observable_geometry_change"),
                "step manifest no-op fact is malformed",
            ),
            "parent_accepted": _fact_bool(
                parent_document.get("accepted"),
                "parent step accepted is malformed",
            ),
        }
    residual_summary = _project_residual_summary(measurement_document)
    return {
        "schema": DECISION_FACTS_SCHEMA,
        "step_ordinal": step,
        "parent_step_ordinal": parent_ordinal,
        "accepted": accepted,
        "acceptance_state": "acceptance_satisfied" if accepted else "unaccepted",
        "residual_summary": residual_summary,
        "repair_targets": _project_repair_targets(
            workspace,
            (repair_target_document or measurement_document).get("repair_targets"),
            active_depth=residual_summary["repair_frontier"]["active_depth"],
        ),
        "change_from_parent": change_from_parent,
    }


def _decision_facts_factory(workspace: Path) -> Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any] | None], Mapping[str, Any]
]:
    """Bind publication projection to the existing full target authority."""

    def build(
        step_document: Mapping[str, Any],
        measurement_document: Mapping[str, Any],
        parent_document: Mapping[str, Any] | None,
    ) -> Mapping[str, Any]:
        step = _fact_int(step_document.get("step"), "step manifest ordinal is malformed")
        full_measurement = _read_authority_json(
            workspace,
            workspace / "voxblame" / "steps" / f"{step:06d}" / "measurement.json",
            f"$.voxblame.steps[{step}].measurement",
        )
        return _build_decision_facts(
            step_document,
            measurement_document,
            parent_document,
            workspace=workspace,
            repair_target_document=full_measurement,
        )

    return build


def read_current_step_decision_facts(
    workspace: Path, *, step: int
) -> dict[str, Any]:
    """Return closed W1-authenticated decision facts for one Measured Step.

    The projection is built from the committed step manifest, its bound
    measurement document, and — for repair steps — the parent step
    manifest.  Every value is a bounded semantic scalar or a small
    bounded list; the projection never returns Workspace-relative paths,
    Workspace attempt identifiers, provider argv, raw geometry digests
    that enable path probing, or a full measurement/graph document.

    Fails closed with :class:`WorkspaceError` when a required key is
    missing, a number is non-finite, a boolean/enumeration slot is
    corrupt, or the repair-target page exceeds the fixed bound.
    """

    workspace = Path(workspace).resolve()
    if type(step) is not int or isinstance(step, bool) or step < 0 or step > MAX_REPAIR_CYCLES:
        raise WorkspaceError(
            "invalid_workspace_path",
            "decision facts requested for an out-of-range step ordinal",
        )
    step_document = _read_authority_json(
        workspace,
        workspace / "steps" / f"{step:06d}" / "step.json",
        f"$.steps[{step}]",
    )
    if step_document.get("step") != step:
        _decision_facts_fail("step manifest ordinal conflicts with request")
    parent_step_value = step_document.get("parent_step")
    parent_ordinal: int | None
    if parent_step_value is None:
        parent_ordinal = None
    else:
        parent_ordinal = _fact_int(
            parent_step_value, "step manifest parent ordinal is malformed"
        )
    measurement_relative = step_document.get("measurement_path")
    if not isinstance(measurement_relative, str) or not measurement_relative.startswith(
        f"voxblame/steps/{step:06d}/"
    ):
        _decision_facts_fail("step manifest measurement binding is malformed")
    measurement_document = _read_authority_json(
        workspace,
        workspace / measurement_relative,
        f"$.steps[{step}].measurement",
    )
    full_measurement_document = _read_authority_json(
        workspace,
        workspace / "voxblame" / "steps" / f"{step:06d}" / "measurement.json",
        f"$.voxblame.steps[{step}].measurement",
    )
    parent_document = (
        _read_authority_json(
            workspace,
            workspace / "steps" / f"{parent_ordinal:06d}" / "step.json",
            f"$.steps[{parent_ordinal}]",
        )
        if parent_ordinal is not None
        else None
    )
    return _build_decision_facts(
        step_document,
        measurement_document,
        parent_document,
        workspace=workspace,
        repair_target_document=full_measurement_document,
    )


def read_current_step_preview_png(workspace: Path, *, step: int) -> bytes:
    """Project a committed Measured Step formal preview as bounded bytes."""

    workspace = Path(workspace).resolve()
    if type(step) is not int or isinstance(step, bool) or step < 0 or step > MAX_REPAIR_CYCLES:
        raise WorkspaceError(
            "invalid_workspace_path",
            "formal preview requested for an out-of-range step ordinal",
        )
    preview = workspace / "steps" / f"{step:06d}" / "preview" / "preview.png"
    _safe_relative(workspace, preview)
    if preview.is_symlink() or not preview.is_file():
        raise WorkspaceError("corrupt_workspace", "formal preview is unavailable")
    try:
        if preview.stat().st_size > _MAX_FORMAL_PREVIEW_PNG_BYTES:
            raise WorkspaceError("corrupt_workspace", "formal preview is invalid")
        payload = preview.read_bytes()
    except OSError as error:
        raise WorkspaceError("corrupt_workspace", "formal preview is unavailable") from error
    if len(payload) > _MAX_FORMAL_PREVIEW_PNG_BYTES or not payload.startswith(_PNG_SIGNATURE):
        raise WorkspaceError("corrupt_workspace", "formal preview is invalid")
    return payload


def _finalization_staging_path(workspace: Path) -> Path:
    """Return the ignored Workspace staging area for Agent finalization."""

    workspace = Path(workspace).resolve()
    _read_workspace_document(workspace)
    work = workspace / "work"
    if work.is_symlink():
        _fail("invalid_workspace_path", "Workspace work area is a symlink", "$.work")
    try:
        work.mkdir(exist_ok=True)
    except OSError:
        _fail("invalid_workspace_path", "Workspace work area is unavailable", "$.work")
    return work / "agent-finalization"


def _reset_finalization_staging(workspace: Path) -> Path:
    """Own creation/reset of the ignored finalization staging area."""

    target = _finalization_staging_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "finalization staging is a symlink", "$.work")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=False)
    return target


def _discard_finalization_staging(workspace: Path) -> None:
    """Own cleanup of the ignored finalization staging area."""

    target = _finalization_staging_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "finalization staging is a symlink", "$.work")
    if target.exists():
        shutil.rmtree(target)


AGENT_SELECTION_CLAIM_SCHEMA = "mesh-to-cad.agent-selection-claim/1"
_AGENT_CLAIM_MAX_TEXT = 4096


def _claim_fail(detail: str, path: str = "$.selection_claim") -> None:
    raise WorkspaceError("invalid_contract", detail, path)


def _claim_nonempty_string(value: Any, detail: str, path: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _AGENT_CLAIM_MAX_TEXT:
        _claim_fail(detail, path)
    return value


def _validate_agent_selection_claim(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "preview_observation",
        "stop_reason",
        "conflict",
        "conflict_details",
        "rationale",
    }:
        _claim_fail("Agent selection claim keys are closed and required")
    if value["schema"] != AGENT_SELECTION_CLAIM_SCHEMA:
        _claim_fail("Agent selection claim schema mismatch", "$.selection_claim.schema")
    stop_reason = value["stop_reason"]
    if stop_reason not in _core._STOP_REASONS:
        _claim_fail("Agent stop_reason is not a recognized enum", "$.selection_claim.stop_reason")
    conflict = value["conflict"]
    if not isinstance(conflict, bool):
        _claim_fail("Agent conflict flag must be boolean", "$.selection_claim.conflict")
    conflict_details = value["conflict_details"]
    if conflict:
        _claim_nonempty_string(
            conflict_details,
            "Agent conflict requires concise conflict_details",
            "$.selection_claim.conflict_details",
        )
    elif conflict_details is not None:
        _claim_fail(
            "clear preview must leave conflict_details null",
            "$.selection_claim.conflict_details",
        )
    return {
        "schema": AGENT_SELECTION_CLAIM_SCHEMA,
        "preview_observation": _claim_nonempty_string(
            value["preview_observation"],
            "Agent preview_observation must be a concise nonempty string",
            "$.selection_claim.preview_observation",
        ),
        "stop_reason": stop_reason,
        "conflict": conflict,
        "conflict_details": conflict_details,
        "rationale": _claim_nonempty_string(
            value["rationale"],
            "Agent rationale must be a concise nonempty string",
            "$.selection_claim.rationale",
        ),
    }


def finalize_from_agent_selection_claim(
    workspace: Path,
    *,
    source: Path,
    selection: str,
    notes: str,
    selected_step: int,
    rebuild_entrypoint: Path,
    geometry_entrypoint: Path,
    tool_registry: Path,
    browser_runtime_capability: Path | None = None,
    scope: _core.ExecutionScope | None = None,
) -> dict[str, Any]:
    """Finalize from an opaque Selected Step handle plus bounded semantic claim.

    W1 owns every trusted fact.  It reads the Selected Step's canonical
    manifest, measurement, and preview identity from Workspace authority,
    binds them to the Agent's semantic claim, and constructs the closed
    ``mesh-to-cad.final-selection/1`` document itself.  Agent-authored
    evidence paths, hashes, accepted facts, considered_step lists, and
    preview identity claims are refused: the Agent may only report the
    bounded observation/stop_reason/conflict/rationale fields.  Any
    contradiction with the canonical Selected Step fails closed before
    any rebuild is attempted.
    """

    raw_source = Path(source)
    if raw_source.is_symlink() or not raw_source.is_dir():
        raise WorkspaceError("invalid_workspace_path", "Agent candidate source is unavailable")
    source = raw_source.resolve()
    workspace = Path(workspace).resolve()
    if (
        type(selected_step) is not int
        or isinstance(selected_step, bool)
        or selected_step < 0
        or selected_step > _core.MAX_REPAIR_CYCLES
    ):
        raise WorkspaceError(
            "invalid_workspace_path",
            "Selected Step ordinal is out of range",
            "$.selected_step",
        )
    graph = _core._build_graph(workspace, validate_steps=True)
    existing = {item["step"]: item for item in graph["steps"]}
    if selected_step not in existing:
        raise WorkspaceError(
            "invalid_workspace_path",
            "Selected Step is not present in the canonical graph",
            "$.selected_step",
        )
    step_document = _read_authority_json(
        workspace,
        workspace / "steps" / f"{selected_step:06d}" / "step.json",
        f"$.steps[{selected_step}]",
    )
    accepted = step_document["accepted"]
    preview_identity = step_document["preview_identity_sha256"]
    staging = _reset_finalization_staging(workspace)
    try:
        selection_source = _agent_source_file(source, selection)
        notes_source = _agent_source_file(source, notes)
        _copy_agent_file(selection_source, staging / "claim.json")
        _copy_agent_file(notes_source, staging / "notes.md")
        try:
            raw_claim = json.loads((staging / "claim.json").read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkspaceError(
                "invalid_contract",
                "Agent selection claim is not readable JSON",
                "$.selection_claim",
            ) from error
        claim = _validate_agent_selection_claim(raw_claim)
        if claim["conflict"]:
            raise WorkspaceError(
                "agent_semantic_conflict",
                "Agent-reported material semantic conflict blocks Final Delivery",
                "$.selection_claim.conflict",
            )
        if accepted and claim["stop_reason"] != "acceptance_satisfied":
            raise WorkspaceError(
                "identity_conflict",
                "accepted Selected Step requires acceptance_satisfied stop_reason",
                "$.selection_claim.stop_reason",
            )
        if not accepted and claim["stop_reason"] == "acceptance_satisfied":
            raise WorkspaceError(
                "identity_conflict",
                "unaccepted Selected Step cannot claim acceptance_satisfied",
                "$.selection_claim.stop_reason",
            )
        measurement_relative = f"steps/{selected_step:06d}/measurement.json"
        measurement_path = workspace / measurement_relative
        if not measurement_path.is_file() or measurement_path.is_symlink():
            raise WorkspaceError(
                "corrupt_workspace",
                "Selected Step measurement is unavailable",
                "$.selected_step.measurement",
            )
        selection_document = {
            "schema": _core.FINAL_SELECTION_SCHEMA,
            "considered_steps": sorted(existing),
            "selected_step": selected_step,
            "preview": {
                "identity_sha256": preview_identity,
                "observation": claim["preview_observation"],
                "evidence_conflict": False,
                "conflict_details": None,
            },
            "accepted": accepted,
            "stop_reason": claim["stop_reason"],
            "evidence": [
                {
                    "kind": "measurement",
                    "path": measurement_relative,
                    "sha256": _core._file_sha256(measurement_path),
                }
            ],
        }
        _write_json(staging / "selection.json", selection_document)
        result = finalize_workspace(
            workspace,
            selection=staging / "selection.json",
            notes=staging / "notes.md",
            rebuild_entrypoint=rebuild_entrypoint,
            geometry_entrypoint=geometry_entrypoint,
            tool_registry=tool_registry,
            validate_after_publish=False,
            scope=scope,
            browser_runtime_capability=browser_runtime_capability,
        )
        return {
            "final_delivery": {
                key: result[key]
                for key in ("selected_step", "accepted", "identity_sha256")
                if key in result
            }
        }
    finally:
        _discard_finalization_staging(workspace)


def _terminal_locator_path(workspace: Path) -> Path:
    """Return the ignored transfer-sidecar path owned by the Workspace seam."""

    workspace = Path(workspace).resolve()
    _read_workspace_document(workspace)
    run = workspace / "run"
    if run.is_symlink():
        _fail("invalid_workspace_path", "Workspace run area is a symlink", "$.run")
    try:
        run.mkdir(exist_ok=True)
    except OSError:
        _fail("invalid_workspace_path", "Workspace run area is unavailable", "$.run")
    return run / "terminal-validation-locator.json"


def _locator_atomic_rename_no_replace(source: Path, target: Path) -> None:
    if source.parent != target.parent:
        raise OSError("locator quarantine paths must share a directory")
    if _locator_is_windows():
        _locator_windows_move_file_ex(source, target)
        return
    source_bytes = os.fsencode(source)
    target_bytes = os.fsencode(target)
    at_fdcwd = -2 if platform.system() == "Darwin" else -100
    if platform.system() == "Darwin":
        try:
            function = ctypes.CDLL(None, use_errno=True).renameatx_np
        except AttributeError:
            function = None
        if function is not None:
            function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            function.restype = ctypes.c_int
            if function(at_fdcwd, source_bytes, at_fdcwd, target_bytes, 0x00000004) == 0:
                return
            error = ctypes.get_errno()
            if error == errno.EEXIST:
                raise FileExistsError(target)
            if error not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
                raise OSError(error, os.strerror(error))
    try:
        function = getattr(ctypes.CDLL(None, use_errno=True), "renameat2")
    except AttributeError:
        function = None
    if function is not None:
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if function(at_fdcwd, source_bytes, at_fdcwd, target_bytes, 1) == 0:
            return
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(target)
        if error not in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise OSError(error, os.strerror(error))
    raise OSError("atomic no-replace rename is unavailable")


def _locator_is_windows() -> bool:
    return os.name == "nt" or platform.system() == "Windows"


def _locator_windows_move_file_ex(source: Path, target: Path, native=None, last_error=None) -> None:
    if native is None:
        try:
            native = ctypes.windll.kernel32.MoveFileExW
        except AttributeError as exc:  # pragma: no cover - Windows only.
            raise OSError("Windows MoveFileExW is unavailable") from exc
    native.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
    native.restype = ctypes.c_int
    if native(os.fspath(source), os.fspath(target), 0x00000008):
        return
    if last_error is None:
        last_error = getattr(ctypes, "get_last_error", lambda: 1)
    error = int(last_error())
    if error in {80, 183}:
        raise FileExistsError(target)
    raise OSError(error, f"MoveFileExW failed for {source} -> {target}")


def _locator_flush_file(descriptor: int) -> None:
    if not _locator_is_windows():
        os.fsync(descriptor)
        return
    try:
        import msvcrt
        handle = msvcrt.get_osfhandle(descriptor)
        if handle == -1 or not ctypes.windll.kernel32.FlushFileBuffers(ctypes.c_void_p(handle)):
            raise OSError("FlushFileBuffers failed")
    except (AttributeError, ImportError, OSError) as exc:
        raise OSError("Windows file durability is unavailable") from exc


def _locator_atomic_rename_available() -> bool:
    if _locator_is_windows():
        kernel = getattr(getattr(ctypes, "windll", None), "kernel32", None)
        try:
            import msvcrt  # noqa: F401
        except ImportError:
            return False
        return kernel is not None and hasattr(kernel, "MoveFileExW") and hasattr(
            kernel, "FlushFileBuffers"
        )
    if platform.system() == "Darwin":
        return hasattr(ctypes.CDLL(None), "renameatx_np")
    return hasattr(ctypes.CDLL(None), "renameat2")


def _locator_identity_and_bytes(path: Path) -> tuple[tuple[int, int], bytes]:
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        value = os.fstat(descriptor)
        if not stat.S_ISREG(value.st_mode):
            raise WorkspaceError("invalid_workspace_path", "terminal locator is not a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return (value.st_dev, value.st_ino), b"".join(chunks)
    finally:
        os.close(descriptor)


def _locator_fsync_parent(path: Path) -> None:
    if _locator_is_windows():
        return
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _locator_quarantine_delete_exact(
    path: Path,
    identity: tuple[int, int],
    expected: bytes,
) -> bool:
    if sum(1 for _ in path.parent.glob(f".{path.name}.quarantine-*")) >= 32:
        raise WorkspaceError("terminal_locator_quarantine_limit", "locator tombstone limit reached")
    slot = path.with_name(
        f".{path.name}.quarantine-{os.getpid()}-{secrets.token_hex(8)}"
    )
    try:
        _locator_atomic_rename_no_replace(path, slot)
    except (FileNotFoundError, FileExistsError, OSError):
        return False
    try:
        moved, value = _locator_identity_and_bytes(slot)
        if moved != identity or value != expected:
            try:
                _locator_atomic_rename_no_replace(slot, path)
            except FileExistsError:
                pass
            return False
        try:
            _locator_fsync_parent(path.parent)
        except OSError:
            return False
        return True
    except (OSError, WorkspaceError):
        return False


TERMINAL_LOCATOR_SCHEMA = "mesh-to-cad.terminal-validation-locator/2"
TERMINAL_HANDOFF_LAYOUT = "external-sibling-namespace/1"
_TERMINAL_LOCATOR_FIELDS = frozenset({"schema", "handoff_layout"})


def _validate_locator_payload(payload: Mapping[str, Any]) -> None:
    """Reject any locator payload that could authenticate a transferred bundle."""

    if not isinstance(payload, Mapping) or set(payload) != _TERMINAL_LOCATOR_FIELDS:
        _fail(
            "terminal_locator_conflict",
            "terminal locator payload has an unsupported closed schema",
            "$.run",
        )
    if payload["schema"] != TERMINAL_LOCATOR_SCHEMA:
        _fail(
            "terminal_locator_conflict",
            "terminal locator schema is unsupported",
            "$.run",
        )
    if payload["handoff_layout"] != TERMINAL_HANDOFF_LAYOUT:
        _fail(
            "terminal_locator_conflict",
            "terminal locator handoff layout is unsupported",
            "$.run",
        )


def write_terminal_locator(
    workspace: Path,
    payload: Mapping[str, Any],
) -> str:
    """Own atomic publication of the minimal terminal discovery marker.

    The marker only names the fixed external handoff layout; it never carries a
    bundle or expected identity that could authenticate a transferred Workspace
    on its own.
    """

    _validate_locator_payload(payload)
    target = _terminal_locator_path(workspace)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        _fail("invalid_workspace_path", "terminal locator is not a regular file", "$.run")
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _fail("terminal_locator_conflict", "existing terminal locator is unreadable", "$.run")
        if existing == dict(payload):
            return "run/terminal-validation-locator.json"
        _fail("terminal_locator_conflict", "existing terminal locator belongs to another handoff", "$.run")
    if not _locator_atomic_rename_available():
        _fail("terminal_locator_unavailable", "atomic locator publication is unavailable", "$.run")
    if sum(1 for _ in target.parent.glob(f".{target.name}.quarantine-*")) >= 32:
        _fail("terminal_locator_quarantine_limit", "locator tombstone limit reached", "$.run")
    temporary = target.with_name(
        f".{target.name}.tmp-{os.getpid()}-{hashlib.sha256(os.urandom(16)).hexdigest()[:12]}"
    )
    encoded = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("ascii")
    descriptor: int | None = None
    temporary_identity: tuple[int, int] | None = None
    target_identity: tuple[int, int] | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short terminal locator write")
            view = view[written:]
        value = os.fstat(descriptor)
        temporary_identity = value.st_dev, value.st_ino
        _locator_flush_file(descriptor)
        os.close(descriptor)
        descriptor = None
        _, temporary_bytes = _locator_identity_and_bytes(temporary)
        if temporary_bytes != encoded:
            raise OSError("terminal locator bytes changed before publication")
        _locator_atomic_rename_no_replace(temporary, target)
        target_identity, target_bytes = _locator_identity_and_bytes(target)
        if target_bytes != encoded:
            raise OSError("terminal locator bytes changed after publication")
        _locator_fsync_parent(target.parent)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        if target_identity is not None:
            _locator_quarantine_delete_exact(target, target_identity, encoded)
        if temporary_identity is not None:
            _locator_quarantine_delete_exact(temporary, temporary_identity, encoded)
        raise
    return "run/terminal-validation-locator.json"


def read_terminal_locator(workspace: Path) -> dict[str, Any] | None:
    """Read the ignored locator through the Workspace-owned sidecar seam."""

    target = _terminal_locator_path(workspace)
    if target.is_symlink():
        _fail("invalid_workspace_path", "terminal locator is a symlink", "$.run")
    if not target.exists():
        return None
    if not target.is_file():
        _fail("invalid_workspace_path", "terminal locator is not a regular file", "$.run")
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _fail("invalid_contract", "terminal locator is not valid JSON", "$.run")
    if not isinstance(value, dict):
        _fail("invalid_contract", "terminal locator is not an object", "$.run")
    return value


TERMINAL_VALIDATION_SCHEMA = "mesh-to-cad.terminal-validation/1"
CONTENT_MANIFEST_SCHEMA = "mesh-to-cad.content-manifest/1"
TERMINAL_BUNDLE_SCHEMA = "mesh-to-cad.terminal-validation-bundle/1"
TERMINAL_IDENTITY_SCHEMA = "mesh-to-cad.terminal-validation-handoff/1"
VALIDATOR_VERSION = "mesh-to-cad.workspace-validator/1"

_TERMINAL_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "validator_version",
    "graph",
    "review_graph",
    "recovery",
    "review_facts",
    "evaluation_facts",
    "content_manifest_sha256",
    "identity_sha256",
}
_BUNDLE_FIELDS = {"schema", "result", "manifest"}
_MANIFEST_FIELDS = {
    "schema",
    "workspace_id",
    "workspace_identity_sha256",
    "files",
    "identity_sha256",
}
_MANIFEST_ENTRY_FIELDS = {"path", "sha256", "size_bytes"}
_REVIEW_FACT_FIELDS = {
    "step_count",
    "cycle_count",
    "failed_attempt_count",
    "accepted_steps",
    "heads",
    "budget",
    "final_delivery",
    "step_outcomes",
}
_STEP_OUTCOME_FIELDS = {
    "step",
    "parent_step",
    "cycle",
    "accepted",
    "no_observable_geometry_change",
    "candidate_mesh_sha256",
    "observable_sha256",
}
_EVALUATION_FACT_FIELDS = {
    "accepted_step_count",
    "has_accepted_step",
    "final_delivery_present",
    "final_delivery_accepted",
    "objective_facts",
}
_OBJECTIVE_FACT_FIELDS = {
    "global_depth_8_zero",
    "out_of_frame_clear",
    "no_evidence_conflict",
}
_MANIFEST_EXCLUDED_ROOTS = frozenset({".git", "run", "work"})
_AGENT_MAX_FILE_BYTES = 512 * 1024 * 1024
_AGENT_MAX_TREE_BYTES = 1024 * 1024 * 1024
_AGENT_MAX_TREE_FILES = 4096


def _agent_source_file(root: Path, value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(
        part in {"", ".", ".."} for part in pure.parts
    ):
        raise WorkspaceError("invalid_workspace_path", "Agent path is not normalized")
    target = root.joinpath(*pure.parts)
    try:
        target.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent path escapes candidate") from error
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise WorkspaceError("invalid_workspace_path", "Agent path contains symlink")
    return target


def _agent_relative(root: Path, value: str) -> str:
    return _agent_source_file(root, value).relative_to(root).as_posix()


def _agent_is_reparse_point(metadata: os.stat_result) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
    return bool(flag and getattr(metadata, "st_file_attributes", 0) & flag)


def _agent_identity(metadata: os.stat_result) -> tuple[object, ...]:
    return tuple(
        getattr(metadata, field, None)
        for field in (
            "st_dev",
            "st_ino",
            "st_nlink",
            "st_mode",
            "st_size",
            "st_mtime_ns",
            # Python 3.12's Windows lstat() exposes creation time as
            # st_ctime_ns, while fstat() exposes metadata-change time there.
            # st_birthtime_ns is the creation-time field shared by both APIs.
            "st_birthtime_ns",
            "st_file_attributes",
        )
    )


def _agent_same_identity(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    return _agent_identity(first) == _agent_identity(second)


def _agent_same_snapshot(
    first: os.stat_result,
    second: os.stat_result,
) -> bool:
    """Compare two snapshots produced by the same stat API.

    ``st_ctime_ns`` has different meanings for Windows ``lstat`` and
    ``fstat``.  It remains useful for detecting a mutation when both
    snapshots came from the same API, so keep that check at those call sites.
    """

    return _agent_same_identity(first, second) and (
        getattr(first, "st_ctime_ns", None)
        == getattr(second, "st_ctime_ns", None)
    )


def _agent_lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as error:
        raise WorkspaceError(
            "invalid_workspace_path", "Agent artifact cannot be inspected"
        ) from error


def _agent_validate_directory_stat(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _agent_is_reparse_point(metadata)
    ):
        raise WorkspaceError("invalid_workspace_path", "Agent tree contains symlink")


def _agent_validate_file_stat(
    metadata: os.stat_result,
    *,
    max_bytes: int = _AGENT_MAX_FILE_BYTES,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _agent_is_reparse_point(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size > _AGENT_MAX_FILE_BYTES
        or metadata.st_size > max_bytes
    ):
        raise WorkspaceError(
            "invalid_workspace_path", "Agent artifact is not a regular file"
        )


def _agent_windows_platform() -> bool:
    """Private seam for the path-based implementation on Windows."""

    return os.name == "nt"


def _agent_open_windows_file(path: Path) -> tuple[int, os.stat_result]:
    """Open one file without POSIX ``dir_fd`` APIs, with identity binding."""

    expected = _agent_lstat(path)
    _agent_validate_file_stat(expected)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _agent_validate_file_stat(opened)
        current = _agent_lstat(path)
        _agent_validate_file_stat(current)
        if (
            not _agent_same_identity(expected, opened)
            or not _agent_same_snapshot(expected, current)
            or not _agent_same_identity(current, opened)
        ):
            raise WorkspaceError(
                "invalid_workspace_path", "Agent artifact changed before copy"
            )
        return descriptor, expected
    except WorkspaceError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise WorkspaceError(
            "invalid_workspace_path", "Agent artifact cannot be opened"
        ) from error


def _agent_open(path: Path, *, directory: bool = False, dir_fd: int | None = None) -> int:
    if _agent_windows_platform():
        if directory or dir_fd is not None:
            raise WorkspaceError(
                "invalid_workspace_path", "Agent directory descriptors are unsupported"
            )
        descriptor, _ = _agent_open_windows_file(path)
        return descriptor
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        return os.open(path, flags, dir_fd=dir_fd)
    except OSError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent artifact cannot be opened") from error


def _copy_agent_file(source: Path, target: Path) -> None:
    """Copy one external Agent file through the shared descriptor guard."""

    descriptor = _agent_open(source)
    try:
        _copy_agent_file_from_descriptor(descriptor, target)
    finally:
        os.close(descriptor)


def _copy_agent_tree_posix(source: Path, target: Path) -> None:
    source_fd = _agent_open(source, directory=True)
    total = 0

    def visit(directory_fd: int, relative: PurePosixPath) -> None:
        nonlocal total
        with os.scandir(directory_fd) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.is_symlink() or entry.name in {"", ".", ".."}:
                    raise WorkspaceError("invalid_workspace_path", "Agent tree contains symlink")
                if not relative.parts and entry.name == "bootstrap.json":
                    continue
                child_relative = relative / entry.name
                if entry.is_dir(follow_symlinks=False):
                    child_fd = _agent_open(Path(entry.name), directory=True, dir_fd=directory_fd)
                    try:
                        (target / child_relative).mkdir(parents=True, exist_ok=True)
                        visit(child_fd, child_relative)
                    finally:
                        os.close(child_fd)
                    continue
                child_fd = os.open(
                    entry.name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=directory_fd,
                )
                try:
                    metadata = os.fstat(child_fd)
                    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree contains special file")
                    if total + metadata.st_size > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree is too large")
                    copied = _copy_agent_file_from_descriptor(
                        child_fd,
                        target / child_relative,
                        max_bytes=_AGENT_MAX_TREE_BYTES - total,
                    )
                    total += copied
                    if total > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError("invalid_workspace_path", "Agent tree is too large")
                finally:
                    os.close(child_fd)

    target_created = False
    try:
        target.mkdir(parents=True, exist_ok=False)
        target_created = True
        visit(source_fd, PurePosixPath())
    except Exception:
        if target_created:
            shutil.rmtree(target, ignore_errors=True)
        raise
    finally:
        os.close(source_fd)


def _copy_agent_tree_windows(source: Path, target: Path) -> None:
    """Copy an Agent tree using path traversal on hosts without ``dir_fd``.

    Every directory and file is checked with no-follow ``lstat`` metadata. A
    regular file is opened and bound to that metadata before its descriptor is
    handed to the shared byte-copy guard; directory identities are checked
    again after traversal. A replacement therefore fails closed even though
    the Windows CRT cannot provide POSIX descriptor-relative opens.
    """

    total = 0
    file_count = 0

    def visit(
        directory: Path,
        destination: Path,
        relative: PurePosixPath,
        *,
        expected: os.stat_result | None = None,
    ) -> None:
        nonlocal total, file_count
        before = _agent_lstat(directory)
        _agent_validate_directory_stat(before)
        if expected is not None and not _agent_same_snapshot(expected, before):
            raise WorkspaceError(
                "invalid_workspace_path", "Agent tree changed before copy"
            )
        try:
            with os.scandir(directory) as entries:
                for entry in sorted(entries, key=lambda item: item.name):
                    if entry.name in {"", ".", ".."}:
                        raise WorkspaceError(
                            "invalid_workspace_path", "Agent tree contains symlink"
                        )
                    if not relative.parts and entry.name == "bootstrap.json":
                        continue
                    child = directory / entry.name
                    child_relative = relative / entry.name
                    metadata = _agent_lstat(child)
                    if stat.S_ISDIR(metadata.st_mode):
                        _agent_validate_directory_stat(metadata)
                        child_destination = destination / child_relative
                        child_destination.mkdir(parents=True, exist_ok=True)
                        visit(child, destination, child_relative)
                        continue
                    _agent_validate_file_stat(
                        metadata,
                        max_bytes=_AGENT_MAX_TREE_BYTES - total,
                    )
                    file_count += 1
                    if file_count > _AGENT_MAX_TREE_FILES:
                        raise WorkspaceError(
                            "invalid_workspace_path", "Agent tree has too many files"
                        )
                    if total + metadata.st_size > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError(
                            "invalid_workspace_path", "Agent tree is too large"
                        )
                    descriptor, expected = _agent_open_windows_file(child)
                    try:
                        copied = _copy_agent_file_from_descriptor(
                            descriptor,
                            destination / child_relative,
                            max_bytes=_AGENT_MAX_TREE_BYTES - total,
                            expected_before=expected,
                        )
                    finally:
                        os.close(descriptor)
                    total += copied
                    if total > _AGENT_MAX_TREE_BYTES:
                        raise WorkspaceError(
                            "invalid_workspace_path", "Agent tree is too large"
                        )
        finally:
            after = _agent_lstat(directory)
            if not _agent_same_snapshot(before, after):
                raise WorkspaceError(
                    "invalid_workspace_path", "Agent tree changed during copy"
                )

    target_created = False
    try:
        root_metadata = _agent_lstat(source)
        _agent_validate_directory_stat(root_metadata)
        target.mkdir(parents=True, exist_ok=False)
        target_created = True
        visit(source, target, PurePosixPath(), expected=root_metadata)
    except Exception:
        if target_created:
            shutil.rmtree(target, ignore_errors=True)
        raise


def _copy_agent_tree(source: Path, target: Path) -> None:
    if _agent_windows_platform():
        _copy_agent_tree_windows(source, target)
        return
    _copy_agent_tree_posix(source, target)


def _copy_agent_file_from_descriptor(
    descriptor: int,
    target: Path,
    *,
    max_bytes: int = _AGENT_MAX_FILE_BYTES,
    expected_before: os.stat_result | None = None,
) -> int:
    before = os.fstat(descriptor)
    _agent_validate_file_stat(before, max_bytes=max_bytes)
    if expected_before is not None and not _agent_same_identity(expected_before, before):
        raise WorkspaceError("invalid_workspace_path", "Agent artifact changed before copy")
    target.parent.mkdir(parents=True, exist_ok=True)
    target_created = False
    try:
        target_fd = os.open(
            target,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
    except OSError as error:
        raise WorkspaceError("invalid_workspace_path", "Agent artifact target is unavailable") from error
    target_created = True
    failed = False
    try:
        digest = hashlib.sha256()
        copied = 0
        while True:
            remaining = before.st_size - copied
            if remaining < 0 or copied > max_bytes:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            chunk = os.read(descriptor, min(1024 * 1024, remaining)) if remaining else b""
            if not chunk:
                break
            if len(chunk) > remaining or copied + len(chunk) > max_bytes:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            digest.update(chunk)
            copied += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(target_fd, view)
                if written <= 0:
                    raise OSError("short Agent artifact write")
                view = view[written:]
        copied_metadata = os.fstat(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        reread = hashlib.sha256()
        reread_bytes = 0
        while True:
            remaining = before.st_size - reread_bytes
            if remaining < 0:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            chunk = os.read(descriptor, min(1024 * 1024, remaining)) if remaining else b""
            if not chunk:
                break
            if len(chunk) > remaining:
                raise WorkspaceError("invalid_workspace_path", "Agent artifact exceeds its validated size")
            reread.update(chunk)
            reread_bytes += len(chunk)
        after = os.fstat(descriptor)
        if (
            copied != before.st_size
            or reread_bytes != before.st_size
            or before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_nlink != after.st_nlink
            or before.st_size != after.st_size
            or before.st_mode != after.st_mode
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or copied_metadata.st_dev != after.st_dev
            or copied_metadata.st_ino != after.st_ino
            or copied_metadata.st_nlink != after.st_nlink
            or copied_metadata.st_size != after.st_size
            or copied_metadata.st_mode != after.st_mode
            or copied_metadata.st_mtime_ns != after.st_mtime_ns
            or copied_metadata.st_ctime_ns != after.st_ctime_ns
            or getattr(copied_metadata, "st_file_attributes", None)
            != getattr(after, "st_file_attributes", None)
            or _agent_is_reparse_point(after)
            or digest.digest() != reread.digest()
        ):
            raise WorkspaceError("invalid_workspace_path", "Agent artifact changed during copy")
        target_stat = os.fstat(target_fd)
        if target_stat.st_size != copied:
            raise WorkspaceError("invalid_workspace_path", "Agent artifact target size is invalid")
        os.fsync(target_fd)
    except Exception:
        failed = True
        raise
    finally:
        try:
            os.close(target_fd)
        finally:
            if failed and target_created:
                try:
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
    return copied


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def compile_terminal_validation(workspace: Path) -> dict[str, Any]:
    """Compile one terminal evidence bundle without mutating the Workspace."""

    workspace = Path(workspace).resolve()
    before = _content_state(workspace)
    validation = validate_workspace(workspace)
    _require_terminal_state(validation.graph)
    after = _content_state(workspace)
    _require_stable_snapshot(before, after)

    workspace_document = _read_workspace_document(workspace)
    manifest = _build_manifest(workspace_document, after)
    result = _build_result(workspace, workspace_document, validation, manifest)
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    _require_stable_snapshot(after, _content_state(workspace))

    bundle = {
        "schema": TERMINAL_BUNDLE_SCHEMA,
        "result": result,
        "manifest": manifest,
    }
    return {
        "bundle": bundle,
        "terminal_identity_sha256": _identity(TERMINAL_IDENTITY_SCHEMA, bundle),
    }


def verify_terminal_validation(
    workspace: Path,
    bundle: Mapping[str, Any],
    expected_identity: str | None = None,
) -> dict[str, Any]:
    """Verify a caller-supplied terminal bundle without full validation or Git."""

    if expected_identity is None:
        _fail(
            "terminal_identity_required",
            "caller must supply the expected Terminal Validation identity",
            "$.expected_terminal_identity",
        )
    _sha256(expected_identity, "$.expected_terminal_identity")
    workspace = Path(workspace).resolve()
    _closed(bundle, _BUNDLE_FIELDS, "$.terminal_bundle")
    _const(bundle["schema"], TERMINAL_BUNDLE_SCHEMA, "$.terminal_bundle.schema")
    actual_identity = _identity(TERMINAL_IDENTITY_SCHEMA, bundle)
    if actual_identity != expected_identity:
        _fail(
            "terminal_identity_mismatch",
            "terminal bundle does not match the expected identity",
            "$.expected_terminal_identity",
        )
    manifest = bundle["manifest"]
    result = bundle["result"]
    _validate_manifest(manifest, workspace)
    _validate_result(result, manifest, workspace)
    return dict(result)


def _build_manifest(
    workspace_document: Mapping[str, Any],
    inventory: tuple[tuple[str, int, str], ...],
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "schema": CONTENT_MANIFEST_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "files": [
            {"path": path, "size_bytes": size, "sha256": digest}
            for path, size, digest in inventory
        ],
    }
    manifest["identity_sha256"] = _identity(CONTENT_MANIFEST_SCHEMA, manifest)
    return manifest


def _build_result(
    workspace: Path,
    workspace_document: Mapping[str, Any],
    validation: ValidationResult,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schema": TERMINAL_VALIDATION_SCHEMA,
        "workspace_id": workspace_document["workspace_id"],
        "workspace_identity_sha256": _workspace_identity(workspace_document),
        "validator_version": VALIDATOR_VERSION,
        "graph": validation.graph,
        "review_graph": _build_review_graph(workspace, validation.graph),
        "recovery": list(validation.recovery),
        "review_facts": _review_facts(validation.graph),
        "evaluation_facts": _evaluation_facts(workspace, validation.graph),
        "content_manifest_sha256": manifest["identity_sha256"],
    }
    result["identity_sha256"] = _identity(TERMINAL_VALIDATION_SCHEMA, result)
    return result


def _build_review_graph(
    workspace: Path,
    graph: Mapping[str, Any],
) -> dict[str, Any]:
    """Freeze the extra closed facts needed by review at W1 compile time."""

    steps: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    recorded_attempts: set[int] = set()
    for item in graph["steps"]:
        step_number = int(item["step"])
        step_path = workspace / "steps" / f"{step_number:06d}" / "step.json"
        step_document = _core._read_json(step_path, "$.review_graph.step")
        attempt_ids = list(step_document.get("attempt_ids", []))
        steps.append({**dict(item), "attempt_ids": attempt_ids})
        for attempt_number in attempt_ids:
            if not isinstance(attempt_number, int) or attempt_number in recorded_attempts:
                continue
            candidates = [
                workspace / "attempts" / f"{attempt_number:06d}" / "attempt.json",
                workspace / "steps" / f"{step_number:06d}" / "attempt.json",
                workspace / "cycles" / f"{step_number:06d}" / "attempt.json",
            ]
            attempt_path = next((path for path in candidates if path.is_file()), None)
            if attempt_path is None:
                raise _core.WorkspaceError(
                    "corrupt_workspace",
                    "review graph references a missing Attempt",
                    "$.review_graph.attempts",
                )
            attempts.append(
                {
                    "attempt": _core._read_json(attempt_path, "$.review_graph.attempt"),
                    "path": attempt_path.relative_to(workspace).as_posix(),
                }
            )
            recorded_attempts.add(attempt_number)
    for item in graph["failed_attempts"]:
        attempt_number = item.get("attempt")
        if not isinstance(attempt_number, int) or attempt_number in recorded_attempts:
            continue
        attempt_path = workspace / "attempts" / f"{attempt_number:06d}" / "attempt.json"
        if attempt_path.is_file():
            attempts.append(
                {
                    "attempt": _core._read_json(attempt_path, "$.review_graph.attempt"),
                    "path": attempt_path.relative_to(workspace).as_posix(),
                }
            )
            recorded_attempts.add(attempt_number)
    cycles: list[dict[str, Any]] = []
    for item in graph["cycles"]:
        number = int(item["cycle"])
        root = workspace / "cycles" / f"{number:06d}"
        cycles.append(
            {
                **dict(item),
                "plan": _core._read_json(root / "plan.json", "$.review_graph.plan"),
                "source_changes": _core._read_json(
                    root / "source_changes.json", "$.review_graph.source_changes"
                ),
                "diff_document": _core._read_json(
                    root / "diff.json", "$.review_graph.diff"
                ),
                "assessment": _core._read_json(
                    root / "assessment.json", "$.review_graph.assessment"
                ),
            }
        )
    delivery = graph.get("final_delivery")
    final: dict[str, Any] | None = None
    if isinstance(delivery, Mapping):
        def optional_final(name: str) -> dict[str, Any] | None:
            path = workspace / "final" / f"{name}.json"
            if not path.is_file():
                return None
            return _core._read_json(path, f"$.review_graph.{name}")

        final = {
            "selection": optional_final("selection"),
            "manifest": optional_final("manifest"),
            "rebuild": optional_final("rebuild"),
            "verification": optional_final("verification"),
        }
    return {
        "schema": "mesh-to-cad.review-graph/1",
        "steps": steps,
        "attempts": attempts,
        "failed_attempts": list(graph["failed_attempts"]),
        "cycles": cycles,
        "final": final,
    }


def _require_terminal_state(graph: Mapping[str, Any]) -> None:
    if graph.get("final_delivery") is None:
        _fail(
            "terminal_state_required",
            "Terminal Validation requires a complete Final Delivery",
            "$.graph.final_delivery",
        )


def _content_state(workspace: Path) -> tuple[tuple[str, int, str], ...]:
    state: list[tuple[str, int, str]] = []
    for path in _content_files(workspace):
        relative = _safe_relative(workspace, path)
        try:
            size = path.stat().st_size
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        state.append((relative, size, digest))
    return tuple(state)


def _require_stable_snapshot(
    before: tuple[tuple[str, int, str], ...],
    after: tuple[tuple[str, int, str], ...],
) -> None:
    if before != after:
        before_paths = {entry[0] for entry in before}
        after_paths = {entry[0] for entry in after}
        changed = next(
            (left[0] for left, right in zip(before, after) if left != right),
            next(
                (item[0] for item in after if item[0] not in before_paths),
                next(
                    (item[0] for item in before if item[0] not in after_paths),
                    "Workspace content",
                ),
            ),
        )
        _fail(
            "workspace_changed_during_validation",
            "Workspace authority changed during Terminal Validation",
            f"$.{changed}",
        )


def _content_files(workspace: Path) -> list[Path]:
    try:
        is_directory = workspace.is_dir()
        is_link = workspace.is_symlink()
    except OSError:
        _inventory_error("$")
    if not is_directory or is_link:
        _fail("invalid_workspace", "Workspace directory does not exist")
    files: list[Path] = []
    try:
        children = sorted(workspace.iterdir(), key=lambda path: path.name)
    except OSError:
        _inventory_error("$")
    for child in children:
        if child.name in _MANIFEST_EXCLUDED_ROOTS:
            continue
        files.extend(_walk_content(workspace, child))
    return sorted(files, key=lambda path: path.relative_to(workspace).as_posix())


def _walk_content(workspace: Path, path: Path) -> list[Path]:
    try:
        is_link = path.is_symlink()
        is_file = path.is_file()
        is_directory = path.is_dir()
    except OSError:
        _inventory_error(_safe_relative(workspace, path))
    if is_link:
        _fail(
            "invalid_workspace_path",
            "Terminal Validation content cannot contain symlinks",
            f"$.{path.name}",
        )
    if is_file:
        return [path]
    if not is_directory:
        _fail("corrupt_workspace", "Terminal Validation content is not a file or directory")
    files: list[Path] = []
    try:
        children = sorted(path.iterdir(), key=lambda item: item.name)
    except OSError:
        _inventory_error(path.name)
    for child in children:
        files.extend(_walk_content(workspace, child))
    return files


def _safe_relative(workspace: Path, path: Path) -> str:
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return "Workspace content"


def _inventory_error(relative: str) -> None:
    _fail(
        "corrupt_workspace",
        "cannot read Workspace authority content",
        f"$.{relative}" if relative != "$" else "$",
    )


def _validate_manifest(manifest: Mapping[str, Any], workspace: Path) -> None:
    _closed(manifest, _MANIFEST_FIELDS, "$.content_manifest")
    _const(manifest["schema"], CONTENT_MANIFEST_SCHEMA, "$.content_manifest.schema")
    _nonempty_string(manifest["workspace_id"], "$.content_manifest.workspace_id")
    _sha256(manifest["workspace_identity_sha256"], "$.content_manifest.workspace_identity_sha256")
    files = manifest["files"]
    if not isinstance(files, list):
        _fail("invalid_contract", "must be an array", "$.content_manifest.files")
    previous = ""
    seen: set[str] = set()
    for index, item in enumerate(files):
        path = f"$.content_manifest.files[{index}]"
        _closed(item, _MANIFEST_ENTRY_FIELDS, path)
        name = item["path"]
        if not isinstance(name, str):
            _fail("invalid_contract", "path must be a string", f"{path}.path")
        pure = PurePosixPath(name)
        if (
            not pure.parts
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or name != pure.as_posix()
            or pure.parts[0] in _MANIFEST_EXCLUDED_ROOTS
        ):
            _fail("invalid_workspace_path", "manifest path is not canonical content", f"{path}.path")
        if name in seen or name <= previous:
            _fail("invalid_contract", "manifest paths must be unique and sorted", f"{path}.path")
        seen.add(name)
        previous = name
        _sha256(item["sha256"], f"{path}.sha256")
        size = item["size_bytes"]
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            _fail("invalid_contract", "size_bytes must be a non-negative integer", f"{path}.size_bytes")
    expected_identity = _identity(
        CONTENT_MANIFEST_SCHEMA,
        {key: manifest[key] for key in _MANIFEST_FIELDS if key != "identity_sha256"},
    )
    if manifest["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "content manifest identity mismatch", "$.content_manifest.identity_sha256")
    expected_files = {
        path.relative_to(workspace).as_posix(): path for path in _content_files(workspace)
    }
    if seen != set(expected_files):
        _fail("corrupt_workspace", "content manifest file set mismatch", "$.content_manifest.files")
    for item in files:
        path = expected_files[item["path"]]
        relative = item["path"]
        try:
            actual_size = path.stat().st_size
            actual_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            _inventory_error(relative)
        if actual_size != item["size_bytes"] or actual_digest != item["sha256"]:
            _fail("corrupt_workspace", "content manifest file digest mismatch", "$.content_manifest.files")


def _validate_result(
    result: Mapping[str, Any], manifest: Mapping[str, Any], workspace: Path
) -> None:
    _closed(result, _TERMINAL_FIELDS, "$.terminal_validation")
    _const(result["schema"], TERMINAL_VALIDATION_SCHEMA, "$.terminal_validation.schema")
    _nonempty_string(result["workspace_id"], "$.terminal_validation.workspace_id")
    _sha256(result["workspace_identity_sha256"], "$.terminal_validation.workspace_identity_sha256")
    _const(result["validator_version"], VALIDATOR_VERSION, "$.terminal_validation.validator_version")
    graph = result["graph"]
    if not isinstance(graph, dict) or graph.get("schema") != _core.INDEX_SCHEMA:
        _fail("invalid_contract", "graph schema is unsupported", "$.terminal_validation.graph")
    _validate_graph_shape(graph)
    review_graph = result["review_graph"]
    if not isinstance(review_graph, dict) or review_graph.get("schema") != "mesh-to-cad.review-graph/1":
        _fail("invalid_contract", "review graph schema is unsupported", "$.terminal_validation.review_graph")
    if not isinstance(result["recovery"], list):
        _fail("invalid_contract", "recovery must be an array", "$.terminal_validation.recovery")
    _validate_review_facts(result["review_facts"])
    _validate_evaluation_facts(result["evaluation_facts"])
    try:
        expected_review = _review_facts(graph)
        expected_evaluation = _evaluation_facts(workspace, graph)
        expected_review_graph = _build_review_graph(workspace, graph)
    except (KeyError, TypeError, ValueError):
        _fail("invalid_contract", "graph facts are structurally incomplete", "$.terminal_validation.graph")
    if result["review_facts"] != expected_review:
        _fail("corrupt_workspace", "review facts are not deterministic", "$.terminal_validation.review_facts")
    if review_graph != expected_review_graph:
        _fail("corrupt_workspace", "review graph is not deterministic", "$.terminal_validation.review_graph")
    if result["evaluation_facts"] != expected_evaluation:
        _fail("corrupt_workspace", "evaluation facts are not deterministic", "$.terminal_validation.evaluation_facts")
    _sha256(result["content_manifest_sha256"], "$.terminal_validation.content_manifest_sha256")
    if result["content_manifest_sha256"] != manifest["identity_sha256"]:
        _fail("corrupt_workspace", "result is bound to another content manifest", "$.terminal_validation.content_manifest_sha256")
    expected_identity = _identity(
        TERMINAL_VALIDATION_SCHEMA,
        {key: result[key] for key in _TERMINAL_FIELDS if key != "identity_sha256"},
    )
    if result["identity_sha256"] != expected_identity:
        _fail("corrupt_workspace", "Terminal Validation result identity mismatch", "$.terminal_validation.identity_sha256")
    if (
        result["workspace_id"] != manifest["workspace_id"]
        or result["workspace_identity_sha256"] != manifest["workspace_identity_sha256"]
    ):
        _fail("corrupt_workspace", "result and manifest identities conflict", "$.terminal_validation.workspace_identity_sha256")


def _validate_graph_shape(graph: Mapping[str, Any]) -> None:
    required = {
        "schema",
        "steps",
        "cycles",
        "failed_attempts",
        "accepted_steps",
        "budget",
        "heads",
        "final_delivery",
    }
    missing = required - set(graph)
    if missing:
        _fail("invalid_contract", f"graph is missing {sorted(missing)}", "$.terminal_validation.graph")
    for key in ("steps", "cycles", "failed_attempts", "accepted_steps", "heads"):
        if not isinstance(graph[key], list):
            _fail("invalid_contract", f"graph.{key} must be an array", f"$.terminal_validation.graph.{key}")
    if not isinstance(graph["budget"], dict):
        _fail("invalid_contract", "graph.budget must be an object", "$.terminal_validation.graph.budget")
    if graph["final_delivery"] is not None and not isinstance(graph["final_delivery"], dict):
        _fail("invalid_contract", "graph.final_delivery must be an object or null", "$.terminal_validation.graph.final_delivery")


def _review_facts(graph: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "step_count": len(graph["steps"]),
        "cycle_count": len(graph["cycles"]),
        "failed_attempt_count": len(graph["failed_attempts"]),
        "accepted_steps": list(graph["accepted_steps"]),
        "heads": list(graph["heads"]),
        "budget": dict(graph["budget"]),
        "final_delivery": graph["final_delivery"],
        "step_outcomes": [
            {
                "step": item["step"],
                "parent_step": item["parent_step"],
                "cycle": item["cycle"],
                "accepted": item["accepted"],
                "no_observable_geometry_change": item["no_observable_geometry_change"],
                "candidate_mesh_sha256": item["candidate_mesh_sha256"],
                "observable_sha256": item["observable_sha256"],
            }
            for item in graph["steps"]
        ],
    }


def _evaluation_facts(workspace: Path, graph: Mapping[str, Any]) -> dict[str, Any]:
    delivery = graph["final_delivery"]
    return {
        "accepted_step_count": len(graph["accepted_steps"]),
        "has_accepted_step": bool(graph["accepted_steps"]),
        "final_delivery_present": delivery is not None,
        "final_delivery_accepted": bool(delivery["accepted"]) if delivery is not None else None,
        "objective_facts": [
            {"step": item["step"], "facts": _measurement_objective_facts(workspace, item)}
            for item in graph["steps"]
        ],
    }


def _measurement_objective_facts(workspace: Path, step: Mapping[str, Any]) -> dict[str, bool]:
    path = (workspace / step["measurement"]).resolve()
    try:
        path.relative_to(workspace.resolve())
    except ValueError:
        _fail("invalid_workspace_path", "measurement path escapes the Workspace")
    measurement = _read_authority_json(workspace, path, "$.terminal_validation.measurement")
    facts = measurement.get("objective_facts")
    _closed(facts, _OBJECTIVE_FACT_FIELDS, "$.terminal_validation.objective_facts")
    if any(not isinstance(facts[key], bool) for key in _OBJECTIVE_FACT_FIELDS):
        _fail("invalid_contract", "objective facts must be boolean", "$.terminal_validation.objective_facts")
    return {key: facts[key] for key in sorted(_OBJECTIVE_FACT_FIELDS)}


def _validate_review_facts(value: Any) -> None:
    _closed(value, _REVIEW_FACT_FIELDS, "$.terminal_validation.review_facts")
    for key in ("step_count", "cycle_count", "failed_attempt_count"):
        if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0:
            _fail("invalid_contract", f"{key} must be a non-negative integer", f"$.terminal_validation.review_facts.{key}")
    for key in ("accepted_steps", "heads"):
        if not isinstance(value[key], list) or any(not isinstance(item, int) or isinstance(item, bool) or item < 0 for item in value[key]):
            _fail("invalid_contract", f"{key} must contain non-negative integers", f"$.terminal_validation.review_facts.{key}")
    if not isinstance(value["budget"], dict) or set(value["budget"]) != {"completed_cycles", "remaining_cycles", "total_attempts", "tool_failures"}:
        _fail("invalid_contract", "budget facts are not closed", "$.terminal_validation.review_facts.budget")
    if not isinstance(value["step_outcomes"], list):
        _fail("invalid_contract", "step_outcomes must be an array", "$.terminal_validation.review_facts.step_outcomes")
    for index, item in enumerate(value["step_outcomes"]):
        path = f"$.terminal_validation.review_facts.step_outcomes[{index}]"
        _closed(item, _STEP_OUTCOME_FIELDS, path)
        for key in ("candidate_mesh_sha256", "observable_sha256"):
            _sha256(item[key], f"{path}.{key}")
        for key in ("accepted", "no_observable_geometry_change"):
            if not isinstance(item[key], bool):
                _fail("invalid_contract", f"{key} must be boolean", f"{path}.{key}")


def _validate_evaluation_facts(value: Any) -> None:
    _closed(value, _EVALUATION_FACT_FIELDS, "$.terminal_validation.evaluation_facts")
    if not isinstance(value["accepted_step_count"], int) or isinstance(value["accepted_step_count"], bool) or value["accepted_step_count"] < 0:
        _fail("invalid_contract", "accepted_step_count must be non-negative", "$.terminal_validation.evaluation_facts.accepted_step_count")
    for key in ("has_accepted_step", "final_delivery_present"):
        if not isinstance(value[key], bool):
            _fail("invalid_contract", f"{key} must be boolean", f"$.terminal_validation.evaluation_facts.{key}")
    if value["final_delivery_accepted"] is not None and not isinstance(value["final_delivery_accepted"], bool):
        _fail("invalid_contract", "final_delivery_accepted must be boolean or null", "$.terminal_validation.evaluation_facts.final_delivery_accepted")
    if not isinstance(value["objective_facts"], list):
        _fail("invalid_contract", "objective_facts must be an array", "$.terminal_validation.evaluation_facts.objective_facts")
    for index, item in enumerate(value["objective_facts"]):
        path = f"$.terminal_validation.evaluation_facts.objective_facts[{index}]"
        _closed(item, {"step", "facts"}, path)
        if not isinstance(item["step"], int) or isinstance(item["step"], bool) or item["step"] < 0:
            _fail("invalid_contract", "objective fact step must be non-negative", f"{path}.step")
        _closed(item["facts"], _OBJECTIVE_FACT_FIELDS, f"{path}.facts")
        if any(not isinstance(item["facts"][key], bool) for key in _OBJECTIVE_FACT_FIELDS):
            _fail("invalid_contract", "objective facts must be boolean", f"{path}.facts")


def _read_workspace_document(workspace: Path) -> dict[str, Any]:
    return _read_authority_json(workspace, workspace / "workspace.json", "$.workspace")


def _read_authority_json(workspace: Path, path: Path, label: str) -> dict[str, Any]:
    relative = _safe_relative(workspace, path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _inventory_error(relative)
    if not isinstance(value, dict):
        _fail("invalid_contract", "JSON artifact must contain an object", label)
    return value


def _workspace_identity(value: Mapping[str, Any]) -> str:
    return _core._identity(_core.WORKSPACE_SCHEMA, value)


_closed = _core._closed_object
_const = _core._const
_nonempty_string = _core._nonempty_string
_sha256 = _core._sha256
_identity = _core._identity


def _fail(classification: str, detail: str, path: str = "$") -> None:
    raise WorkspaceError(classification, detail, path)


__all__ = [
    "CONTENT_MANIFEST_SCHEMA",
    "DECISION_FACTS_SCHEMA",
    "DEFAULT_COMMAND_SECONDS",
    "ExecutionScope",
    "FAILED_ATTEMPT_RESULTS",
    "MAX_ATTEMPTS_PER_STEP",
    "MAX_REPAIR_CYCLES",
    "MAX_TOOL_FAILURES_PER_STEP",
    "StepZeroEvidenceProvider",
    "StepZeroEvidenceRequest",
    "TERMINAL_BUNDLE_SCHEMA",
    "TERMINAL_IDENTITY_SCHEMA",
    "TERMINAL_VALIDATION_SCHEMA",
    "VALIDATOR_VERSION",
    "WorkspaceError",
    "ValidationResult",
    "begin_attempt",
    "cancel_active_commands",
    "compile_terminal_validation",
    "AGENT_SELECTION_CLAIM_SCHEMA",
    "finalize_workspace",
    "finalize_from_agent_selection_claim",
    "initialize_workspace",
    "publish_cycle",
    "publish_cycle_from_candidate",
    "publish_step_zero",
    "publish_step_zero_from_candidate",
    "read_canonical_reference_binding",
    "read_current_step_decision_facts",
    "read_terminal_locator",
    "record_attempt",
    "recover_workspace",
    "rebuild_index",
    "run_attempt_command",
    "run_canonical_build",
    "seed_repair_source_from_parent_step",
    "validate_workspace",
    "verify_terminal_validation",
    "write_terminal_locator",
    "workspace_initialized",
    "workspace_status",
]
