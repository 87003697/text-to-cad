"""Direct facade integration checks for Agent candidate publication."""

from __future__ import annotations

import hashlib
import importlib.util
from contextlib import contextmanager
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sys
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_FIXTURE = REPO_ROOT / "tests/python/skills/mesh-to-cad/test_workspace_cli.py"
FACADE_ROOT = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"


def _load_fixture():
    spec = importlib.util.spec_from_file_location("workspace_cli_agent_fixture", CLI_FIXTURE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace CLI fixture")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_facade():
    if str(FACADE_ROOT) not in sys.path:
        sys.path.insert(0, str(FACADE_ROOT))
    spec = importlib.util.spec_from_file_location(
        "workspace_agent_facade", FACADE_ROOT / "workspace.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace facade")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _isolated_package_import(package_name: str):
    """Load one provider package from the path selected by its importer.

    The full skill suite imports editable ``meshscope`` for Agent Surface tests
    before this file exercises the physically materialized shipped runtime. Keep
    that ambient module state out of the provider call, then restore it so the
    test remains a good citizen for later modules.
    """
    prefix = f"{package_name}."
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == package_name or name.startswith(prefix)
    }
    saved_path = sys.path[:]
    try:
        for name in saved:
            sys.modules.pop(name, None)
        yield
    finally:
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(prefix):
                sys.modules.pop(name, None)
        sys.modules.update(saved)
        sys.path[:] = saved_path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )


def _write_step_measurement(
    root: Path,
    *,
    step: int,
    compare_to: int | None,
    reference_sha: str,
    candidate_sha: str,
    observable_sha: str,
    accepted: bool,
) -> Path:
    step_root = root / "steps" / f"{step:06d}"
    step_root.mkdir(parents=True)
    summary = {
        "schema": "voxblame.summary/1",
        "coordinate_contract": "trellis2_canonical/1",
        "max_depth": 8,
        "step": step,
        "compare_to": compare_to,
        "report": f"voxblame/steps/{step:06d}/report.json",
        "canonical_reference": {
            "canonical_reference_sha256": reference_sha,
            "reference_ply_sha256": "3" * 64,
            "triangle_set_sha256": "4" * 64,
            "interior_tree_sha256": "5" * 64,
        },
        "measurement": {
            "candidate_mesh_sha256": candidate_sha,
            "interior_tree_sha256": "6" * 64,
            "exterior_snapshot_sha256": "7" * 64,
            "observable_sha256": observable_sha,
        },
        "errors_by_depth": [
            {
                "depth": depth,
                "reference_surface_count": 1,
                "candidate_surface_count": 1 if accepted else 0,
                "missing_surface_count": 0 if accepted else 1,
                "excess_surface_count": 0,
                "union_surface_count": 1,
                "surface_error_count": 0 if accepted else 1,
                "surface_error_rate": 0.0 if accepted else 1.0,
            }
            for depth in range(1, 9)
        ],
        "exterior_surface": {
            "storage_schema": "voxblame.exterior-snapshot/1",
            "path": f"voxblame/steps/{step:06d}/exterior.vbexterior",
            "logical_sha256": "7" * 64,
            "surface_present": False,
            "surface_cell_count": 0,
            "bounds_canonical": None,
            "centroid_canonical": None,
            "nearest_overrun": None,
            "farthest_overrun": None,
            "outside_directions": [],
            "diagnostic_grid_depth": 8,
            "coarsened": False,
        },
        "repair_targets": {
            "ordering_profile": "repair_target_display/1",
            "total": 0,
            "returned": 0,
            "remaining": 0,
            "offset": 0,
            "next_offset": None,
            "items": [],
        },
        "objective_facts": {
            "global_depth_8_zero": accepted,
            "out_of_frame_clear": True,
            "no_evidence_conflict": True,
        },
        "no_observable_geometry_change": False,
    }
    _write_json(step_root / "summary.json", summary)
    if step == 0:
        _write_json(
            root / "session.json",
            {
                "schema": "voxblame.session/2",
                "canonical_reference": {"canonical_reference_sha256": reference_sha},
            },
        )
        (root / "reference.vbsvo").write_bytes(b"vbsvo")
    return step_root / "summary.json"


def _write_step_preview(
    root: Path,
    *,
    reference_sha: str,
    profile_sha: str,
    candidate_sha: str,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    png = b"synthetic png bytes"
    (root / "preview.png").write_bytes(png)
    metadata = {
        "schema": "voxblame.preview/1",
        "render_variant": "step",
        "canonical_frame": {"coordinate_contract": "trellis2_canonical/1"},
        "profile": {
            "name": "cadena_residual_eight_view/1",
            "experiment_identity": {
                "name": "cadena_residual_eight_view/1",
                "sha256": profile_sha,
            },
        },
        "reference": {"canonical_reference_sha256": reference_sha},
        "candidate": {"mesh_sha256": candidate_sha},
        "image": {"path": "preview.png", "sha256": _sha(png)},
    }
    metadata["preview_identity_sha256"] = hashlib.sha256(
        b"voxblame.preview/1\0"
        + (
            json.dumps(metadata, indent=2, sort_keys=True, separators=(",", ": "))
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    _write_json(root / "preview.json", metadata)
    return root


def _write_region_diff(
    output: Path,
    *,
    plan: dict,
    from_step: int,
    to_step: int,
    before_observable: str,
    after_observable: str,
) -> None:
    plan_bytes = (
        json.dumps(plan, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")
    document = {
        "schema": "voxblame.region-diff/1",
        "coordinate_contract": "trellis2_canonical/1",
        "max_depth": 8,
        "from_step": from_step,
        "to_step": to_step,
        "repair_batch": {
            "schema": "voxblame.repair-batch/1",
            "plan_sha256": hashlib.sha256(
                b"voxblame.repair-batch/1\0" + plan_bytes
            ).hexdigest(),
            "from_step": from_step,
            "selected_targets": [],
            "planned_edits": [],
        },
        "measurement_trajectory": {
            "steps": [from_step, to_step],
            "observable_geometry": {
                "before_sha256": before_observable,
                "after_sha256": after_observable,
                "changed": before_observable != after_observable,
            },
        },
    }
    identity_bytes = (
        json.dumps(document, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")
    document["identity"] = {
        "region_diff_sha256": hashlib.sha256(
            b"voxblame.region-diff/1\0" + identity_bytes
        ).hexdigest()
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(output, document)


def _write_source_changes(
    output: Path, *, from_step: int, to_step: int, path: str = "source/model.py"
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        output,
        {
            "schema": "mesh-to-cad.source-changes/1",
            "from_step": from_step,
            "to_step": to_step,
            "files": [
                {
                    "path": path,
                    "before_sha256": "c" * 64,
                    "after_sha256": "d" * 64,
                }
            ],
        },
    )


def _make_stub_provider(
    *, reference_sha: str, profile_sha: str, candidate_sha: str
):
    """Return a stub Step 0 evidence provider bound to test-known digests."""

    def _stub(request) -> None:
        _write_step_measurement(
            request.voxblame_output,
            step=0,
            compare_to=None,
            reference_sha=reference_sha,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        _write_step_preview(
            request.preview_output,
            reference_sha=reference_sha,
            profile_sha=profile_sha,
            candidate_sha=candidate_sha,
        )

    return _stub


def _make_repair_stub(
    *,
    reference_sha: str,
    profile_sha: str,
    candidate_sha: str,
    observable_sha: str,
    before_observable: str,
):
    """Return a stub Repair evidence provider bound to test-known digests.

    The stub mirrors what the production ``RepairEvidenceProvider``
    would do: hydrate the parent voxblame session/reference into the
    stage's ``voxblame_output`` root, publish the new Measured Step
    subtree (``summary.json`` + ``measurement.json``), publish the
    formal step preview, and write a schema-valid Region Diff and
    source-change delta for the current attempt's plan.
    """

    def _stub(request) -> None:
        shutil.copy2(
            request.parent_voxblame / "session.json",
            request.voxblame_output / "session.json",
        )
        shutil.copy2(
            request.parent_voxblame / "reference.vbsvo",
            request.voxblame_output / "reference.vbsvo",
        )
        _write_step_measurement(
            request.voxblame_output,
            step=request.to_step,
            compare_to=request.from_step,
            reference_sha=reference_sha,
            candidate_sha=candidate_sha,
            observable_sha=observable_sha,
            accepted=False,
        )
        # Provider-internal ``measurement.json`` is required at the
        # stage layer so the shape-check catches truncation/symlinks;
        # ``publish_cycle`` only reads ``summary.json`` from the
        # promoted voxblame subtree.
        (
            request.voxblame_output
            / "steps"
            / f"{request.to_step:06d}"
            / "measurement.json"
        ).write_text("{}\n", encoding="utf-8")
        _write_step_preview(
            request.preview_output,
            reference_sha=reference_sha,
            profile_sha=profile_sha,
            candidate_sha=candidate_sha,
        )
        _write_region_diff(
            request.region_diff_output,
            plan=dict(request.plan),
            from_step=request.from_step,
            to_step=request.to_step,
            before_observable=before_observable,
            after_observable=observable_sha,
        )
        _write_source_changes(
            request.source_changes_output,
            from_step=request.from_step,
            to_step=request.to_step,
        )

    return _stub


class WorkspaceFacadeAgentTests(unittest.TestCase):
    def _publish_step_zero(self, case, facade):
        """Publish one Step 0 through the trusted stub provider."""

        case.invoke(
            "init", "--workspace", str(case.workspace), "--prepared", str(case.prepared_setup())
        )
        case.invoke(
            "begin-attempt",
            "--workspace",
            str(case.workspace),
            "--plan",
            str(case.initial_plan()),
            "--intended-step",
            "0",
        )
        facade.run_attempt_command(
            case.workspace,
            attempt=1,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        candidate, candidate_sha = case.candidate("agent-step", b"agent step")
        source = case.root / "agent-step-source"
        source.mkdir(parents=True)
        shutil.copytree(candidate / "source", source / "source")
        shutil.copy2(candidate / "artifacts/model.glb", source / "candidate.glb")
        stub_provider = _make_stub_provider(
            reference_sha=case.reference_sha,
            profile_sha=case.profile_sha,
            candidate_sha=candidate_sha,
        )
        published = facade.publish_step_zero_from_candidate(
            case.workspace,
            attempt=1,
            source=source,
            evidence_provider=stub_provider,
        )
        return published, candidate_sha

    def _prepare_repair_source(self, case, cycle_candidate):
        """Build a cycle candidate source tree with only Agent-authored bytes."""

        cycle_source = case.root / "agent-cycle-source"
        cycle_source.mkdir(parents=True)
        shutil.copytree(cycle_candidate / "source", cycle_source / "source")
        shutil.copy2(
            cycle_candidate / "artifacts/model.glb",
            cycle_source / "candidate.glb",
        )
        return cycle_source

    def _prepare_repair_cycle(self, case, facade, *, before_observable: str = "9" * 64):
        """Publish Step 0 and stage a Repair Cycle attempt with a candidate tree."""

        self._publish_step_zero(case, facade)
        plan = case.repair_plan("agent-cycle-plan", from_step=0)
        facade.begin_attempt(case.workspace, plan, intended_step=1, from_step=0)
        facade.run_attempt_command(
            case.workspace,
            attempt=2,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        cycle_candidate, cycle_sha = case.candidate("agent-cycle", b"agent cycle")
        cycle_source = self._prepare_repair_source(case, cycle_candidate)
        assessment = case.assessment("agent-cycle", from_step=0, to_step=1)
        shutil.copy2(assessment, cycle_source / "assessment.json")
        repair_stub = _make_repair_stub(
            reference_sha=case.reference_sha,
            profile_sha=case.profile_sha,
            candidate_sha=cycle_sha,
            observable_sha="b" * 64,
            before_observable=before_observable,
        )
        return cycle_source, repair_stub

    def test_step_and_cycle_ingestion_uses_one_real_facade_target_owner(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, repair_stub = self._prepare_repair_cycle(case, facade)
        self.assertTrue((case.workspace / "steps/000000").is_dir())
        self.assertTrue((case.workspace / "voxblame/steps/000000/summary.json").is_file())

        repaired = facade.publish_cycle_from_candidate(
            case.workspace,
            attempt=2,
            source=cycle_source,
            evidence_provider=repair_stub,
        )
        self.assertEqual({"step": {"step": 1}, "cycle": 1}, repaired)
        self.assertTrue((case.workspace / "steps/000001").is_dir())
        self.assertTrue((case.workspace / "cycles/000001").is_dir())
        self.assertTrue(
            (case.workspace / "voxblame/steps/000001/summary.json").is_file()
        )
        # W1 owns the stage lifecycle: no external or internal stage
        # residue survives publication.
        residue = [
            entry.name
            for entry in case.workspace.iterdir()
            if entry.name.startswith(".voxblame-promotion-")
        ]
        self.assertEqual([], residue)

    def test_cycle_rejects_candidate_authored_evidence(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, repair_stub = self._prepare_repair_cycle(case, facade)
        for forbidden in (
            "measurement.json",
            "preview",
            "region-diff.json",
            "source-changes.json",
        ):
            candidate_bytes_source = cycle_source
            if forbidden == "preview":
                (candidate_bytes_source / forbidden).mkdir()
            else:
                (candidate_bytes_source / forbidden).write_text("{}", encoding="utf-8")
            with self.assertRaises(facade.WorkspaceError) as raised:
                facade.publish_cycle_from_candidate(
                    case.workspace,
                    attempt=2,
                    source=cycle_source,
                    evidence_provider=repair_stub,
                )
            self.assertEqual(
                "invalid_repair_candidate", raised.exception.classification
            )
            if forbidden == "preview":
                shutil.rmtree(candidate_bytes_source / forbidden)
            else:
                (candidate_bytes_source / forbidden).unlink()
        # Rejection never leaves partial Repair Cycle authority.
        self.assertFalse((case.workspace / "steps/000001").exists())
        self.assertFalse((case.workspace / "cycles/000001").exists())
        self.assertFalse((case.workspace / "voxblame/steps/000001").exists())

    def test_cycle_provider_request_paths_are_outside_workspace_authority(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, _repair_stub = self._prepare_repair_cycle(case, facade)
        workspace_root = case.workspace.resolve()

        observed: dict = {}
        cycle_sha = _sha(b"agent cycle")

        def capturing_provider(request):
            self.assertTrue(request.voxblame_output.is_dir())
            self.assertFalse(request.preview_output.exists())
            observed["paths"] = {}
            for label, path in (
                ("canonical_reference", request.canonical_reference),
                ("candidate_mesh", request.candidate_mesh),
                ("candidate_source", request.candidate_source),
                ("parent_voxblame", request.parent_voxblame),
                ("parent_source", request.parent_source),
                ("voxblame_output", request.voxblame_output),
                ("preview_output", request.preview_output),
                ("region_diff_output", request.region_diff_output),
                ("source_changes_output", request.source_changes_output),
            ):
                resolved = Path(path).resolve()
                observed["paths"][label] = resolved
                try:
                    resolved.relative_to(workspace_root)
                except ValueError:
                    continue
                raise AssertionError(
                    f"provider {label} path is inside Workspace: {resolved}"
                )
            observed["candidate_bytes"] = request.candidate_mesh.read_bytes()
            observed["candidate_source_files"] = sorted(
                str(child.relative_to(request.candidate_source))
                for child in request.candidate_source.rglob("*")
                if child.is_file()
            )
            observed["parent_source_files"] = sorted(
                str(child.relative_to(request.parent_source))
                for child in request.parent_source.rglob("*")
                if child.is_file()
            )
            observed["parent_voxblame_files"] = sorted(
                str(child.relative_to(request.parent_voxblame))
                for child in request.parent_voxblame.rglob("*")
                if child.is_file()
            )
            _make_repair_stub(
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=cycle_sha,
                observable_sha="b" * 64,
                before_observable="9" * 64,
            )(request)

        facade.publish_cycle_from_candidate(
            case.workspace, attempt=2, source=cycle_source, evidence_provider=capturing_provider
        )
        # All nine request paths must be outside the Workspace tree.
        for label, path in observed["paths"].items():
            with self.assertRaises(ValueError, msg=f"{label} escaped Workspace"):
                path.relative_to(workspace_root)
        # The provider actually read real hydrated bytes.
        self.assertTrue(observed["candidate_bytes"])
        self.assertIn("model.py", observed["candidate_source_files"])
        self.assertIn("model.py", observed["parent_source_files"])
        self.assertIn("session.json", observed["parent_voxblame_files"])
        self.assertIn("reference.vbsvo", observed["parent_voxblame_files"])

    def test_cycle_provider_symlink_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, base_stub = self._prepare_repair_cycle(case, facade)

        def symlink_provider(request):
            base_stub(request)
            summary = (
                request.voxblame_output
                / "steps"
                / f"{request.to_step:06d}"
                / "summary.json"
            )
            replacement = summary.with_name("summary-real.json")
            summary.rename(replacement)
            summary.symlink_to(replacement)

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_cycle_from_candidate(
                case.workspace,
                attempt=2,
                source=cycle_source,
                evidence_provider=symlink_provider,
            )
        self.assertIn(
            raised.exception.classification,
            {"invalid_repair_evidence", "invalid_workspace_path"},
        )
        self.assertFalse((case.workspace / "voxblame/steps/000001").exists())
        self.assertFalse((case.workspace / "steps/000001").exists())

    def test_cycle_provider_hardlink_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, base_stub = self._prepare_repair_cycle(case, facade)

        def hardlink_provider(request):
            base_stub(request)
            summary = (
                request.voxblame_output
                / "steps"
                / f"{request.to_step:06d}"
                / "summary.json"
            )
            twin = summary.with_name("twin.json")
            os.link(summary, twin)

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_cycle_from_candidate(
                case.workspace,
                attempt=2,
                source=cycle_source,
                evidence_provider=hardlink_provider,
            )
        self.assertIn(
            raised.exception.classification,
            {"invalid_repair_evidence", "invalid_workspace_path"},
        )
        self.assertFalse((case.workspace / "voxblame/steps/000001").exists())

    def test_cycle_provider_oversized_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, base_stub = self._prepare_repair_cycle(case, facade)

        original = facade._MAX_REPAIR_STAGE_FILE_BYTES
        facade._MAX_REPAIR_STAGE_FILE_BYTES = 32
        self.addCleanup(setattr, facade, "_MAX_REPAIR_STAGE_FILE_BYTES", original)

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_cycle_from_candidate(
                case.workspace,
                attempt=2,
                source=cycle_source,
                evidence_provider=base_stub,
            )
        self.assertEqual(
            "invalid_repair_evidence", raised.exception.classification
        )
        self.assertFalse((case.workspace / "voxblame/steps/000001").exists())

    def test_cycle_provider_failure_rolls_back_promotion(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        cycle_source, base_stub = self._prepare_repair_cycle(case, facade)

        def bad_source_changes_provider(request):
            base_stub(request)
            # Break the source-changes edge so ``publish_cycle`` fails
            # after W1 has already promoted the voxblame step subtree.
            _write_json(
                request.source_changes_output,
                {
                    "schema": "mesh-to-cad.source-changes/1",
                    "from_step": request.from_step + 100,
                    "to_step": request.to_step,
                    "files": [
                        {
                            "path": "source/model.py",
                            "before_sha256": "c" * 64,
                            "after_sha256": "d" * 64,
                        }
                    ],
                },
            )

        with self.assertRaises(facade.WorkspaceError):
            facade.publish_cycle_from_candidate(
                case.workspace,
                attempt=2,
                source=cycle_source,
                evidence_provider=bad_source_changes_provider,
            )
        # The promoted voxblame step was rolled back and no cycle or
        # step authority landed on disk.
        self.assertFalse((case.workspace / "steps/000001").exists())
        self.assertFalse((case.workspace / "cycles/000001").exists())
        self.assertFalse((case.workspace / "voxblame/steps/000001").exists())

    def test_step_zero_rejects_candidate_authored_evidence(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        case.invoke(
            "init", "--workspace", str(case.workspace), "--prepared", str(case.prepared_setup())
        )
        case.invoke(
            "begin-attempt",
            "--workspace",
            str(case.workspace),
            "--plan",
            str(case.initial_plan()),
            "--intended-step",
            "0",
        )
        facade.run_attempt_command(
            case.workspace,
            attempt=1,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        candidate, candidate_sha = case.candidate("agent-step", b"agent step")
        source = case.root / "agent-step-source"
        source.mkdir(parents=True)
        shutil.copytree(candidate / "source", source / "source")
        shutil.copy2(candidate / "artifacts/model.glb", source / "candidate.glb")
        (source / "measurement.json").write_bytes(b"{}")

        stub_provider = _make_stub_provider(
            reference_sha=case.reference_sha,
            profile_sha=case.profile_sha,
            candidate_sha=candidate_sha,
        )
        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_step_zero_from_candidate(
                case.workspace,
                attempt=1,
                source=source,
                evidence_provider=stub_provider,
            )
        self.assertEqual(
            "invalid_step_zero_candidate", raised.exception.classification
        )

    def _prepare_ready_workspace(self, case, *, candidate_bytes: bytes = b"agent step"):
        """Initialize, begin an attempt, and stage one candidate GLB source tree."""

        case.invoke(
            "init", "--workspace", str(case.workspace), "--prepared", str(case.prepared_setup())
        )
        case.invoke(
            "begin-attempt",
            "--workspace",
            str(case.workspace),
            "--plan",
            str(case.initial_plan()),
            "--intended-step",
            "0",
        )
        facade = _load_facade()
        facade.run_attempt_command(
            case.workspace,
            attempt=1,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        candidate, candidate_sha = case.candidate("agent-step", candidate_bytes)
        source = case.root / "agent-step-source"
        source.mkdir(parents=True)
        shutil.copytree(candidate / "source", source / "source")
        shutil.copy2(candidate / "artifacts/model.glb", source / "candidate.glb")
        return facade, source, candidate_sha

    def test_real_step_zero_provider_starts_a_fresh_session(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        prepared, candidate = case.canonical_cad_flow(accepted=False)
        status, _payload, stderr = case.invoke(
            "init", "--workspace", str(case.workspace), "--prepared", str(prepared)
        )
        self.assertEqual(0, status, stderr)
        case.invoke(
            "begin-attempt",
            "--workspace",
            str(case.workspace),
            "--plan",
            str(case.initial_plan()),
            "--intended-step",
            "0",
        )
        facade = _load_facade()
        facade.run_attempt_command(
            case.workspace,
            attempt=1,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        source = case.root / "real-provider-source"
        shutil.copytree(candidate / "source", source / "source")
        shutil.copy2(candidate / "built/measurement.glb", source / "candidate.glb")

        from PIL import Image
        from scripts.pilot import step_zero_evidence

        _MeshGeometry, load_profile, _render = step_zero_evidence._import_meshshot()
        from meshshot import RenderedPreview

        loaded = load_profile()

        def renderer(_reference, _candidate, *, variant="step", exterior_directions=()):
            pixels = tuple(loaded.profile["variants"][variant]["image_pixels"])
            image = Image.new("RGB", pixels, (0, 0, 0))
            encoded = BytesIO()
            image.save(encoded, format="PNG")
            marker = exterior_directions[0] if exterior_directions else None
            views = tuple(
                {
                    **view,
                    "framing": {
                        "projection": (
                            "orthographic"
                            if view["kind"] == "axial_depth"
                            else "perspective"
                        )
                    },
                    "markers": ([{"direction": marker}] if marker is not None else []),
                }
                for view in loaded.profile["views"]
            )
            return RenderedPreview(
                png_bytes=encoded.getvalue(),
                variant=variant,
                profile_sha256=loaded.sha256,
                views=views,
            )

        observed_stage_paths: list[Path] = []

        def real_provider(request):
            observed_stage_paths.extend(
                [request.voxblame_output, request.preview_output]
            )
            self.assertFalse(request.voxblame_output.exists())
            self.assertFalse(request.preview_output.exists())

            # The canonical provider treats an existing output root as a
            # resume.  An incomplete pre-existing root must still fail;
            # removing it must select the fresh-session path and succeed.
            request.voxblame_output.mkdir()
            with self.assertRaises(
                step_zero_evidence.StepZeroEvidenceError
            ) as raised:
                step_zero_evidence.real_step_zero_evidence_provider(
                    request, renderer=renderer
                )
            self.assertEqual("measurement_failed", raised.exception.classification)
            request.voxblame_output.rmdir()
            step_zero_evidence.real_step_zero_evidence_provider(
                request, renderer=renderer
            )

        with _isolated_package_import("meshscope"):
            published = facade.publish_step_zero_from_candidate(
                case.workspace,
                attempt=1,
                source=source,
                evidence_provider=real_provider,
            )

        self.assertEqual({"step": 0}, published)
        self.assertTrue(
            (case.workspace / "voxblame/steps/000000/summary.json").is_file()
        )
        for path in observed_stage_paths:
            self.assertFalse(path.exists(), f"stage residue survived: {path}")

    def test_provider_request_paths_are_outside_workspace_authority(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        facade, source, candidate_sha = self._prepare_ready_workspace(case)

        observed: dict = {}
        workspace_root = case.workspace.resolve()

        def capturing_provider(request):
            observed["request"] = request
            observed["candidate_bytes"] = request.candidate_mesh.read_bytes()
            observed["reference_entries"] = sorted(
                str(child.relative_to(request.canonical_reference))
                for child in request.canonical_reference.rglob("*")
                if child.is_file()
            )
            for label, path in (
                ("canonical_reference", request.canonical_reference),
                ("candidate_mesh", request.candidate_mesh),
                ("voxblame_output", request.voxblame_output),
                ("preview_output", request.preview_output),
            ):
                resolved = Path(path).resolve()
                observed.setdefault("paths", {})[label] = resolved
                try:
                    resolved.relative_to(workspace_root)
                except ValueError:
                    continue
                raise AssertionError(
                    f"provider {label} path is inside Workspace: {resolved}"
                )
            _write_step_measurement(
                request.voxblame_output,
                step=0,
                compare_to=None,
                reference_sha=case.reference_sha,
                candidate_sha=candidate_sha,
                observable_sha="9" * 64,
                accepted=False,
            )
            _write_step_preview(
                request.preview_output,
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=candidate_sha,
            )

        published = facade.publish_step_zero_from_candidate(
            case.workspace, attempt=1, source=source, evidence_provider=capturing_provider
        )
        self.assertEqual({"step": 0}, published)
        # All four request paths must be outside the Workspace tree.
        for label, path in observed["paths"].items():
            with self.assertRaises(ValueError, msg=f"{label} escaped Workspace"):
                path.relative_to(workspace_root)
        # The provider actually read the private canonical/candidate copies,
        # confirming they are hydrated with real bytes rather than empty stubs.
        self.assertTrue(observed["candidate_bytes"])
        self.assertIn("reference.ply", observed["reference_entries"])

    def test_provider_stage_leaks_no_paths_and_cleans_up(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        facade, source, candidate_sha = self._prepare_ready_workspace(case)

        seen_stage_dirs: list[Path] = []

        def observing_provider(request):
            seen_stage_dirs.extend(
                [request.voxblame_output, request.preview_output,
                 request.canonical_reference, request.candidate_mesh]
            )
            _write_step_measurement(
                request.voxblame_output,
                step=0,
                compare_to=None,
                reference_sha=case.reference_sha,
                candidate_sha=candidate_sha,
                observable_sha="9" * 64,
                accepted=False,
            )
            _write_step_preview(
                request.preview_output,
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=candidate_sha,
            )

        facade.publish_step_zero_from_candidate(
            case.workspace, attempt=1, source=source, evidence_provider=observing_provider
        )
        # External stage cleanup: nothing the provider saw survives.
        for path in seen_stage_dirs:
            self.assertFalse(path.exists(), f"stage residue survived: {path}")
        # No promotion-stage residue inside Workspace.
        self.assertEqual(
            [],
            [
                child.name
                for child in case.workspace.iterdir()
                if child.name.startswith(".voxblame-promotion-")
            ],
        )

    def test_provider_symlink_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        facade, source, candidate_sha = self._prepare_ready_workspace(case)

        def symlink_provider(request):
            _write_step_measurement(
                request.voxblame_output,
                step=0,
                compare_to=None,
                reference_sha=case.reference_sha,
                candidate_sha=candidate_sha,
                observable_sha="9" * 64,
                accepted=False,
            )
            _write_step_preview(
                request.preview_output,
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=candidate_sha,
            )
            # Turn one required voxblame artifact into a symlink after
            # writing legitimate bytes: an ordinary content check would
            # not catch this alone.
            summary = request.voxblame_output / "steps/000000/summary.json"
            replacement = request.voxblame_output / "steps/000000/summary-real.json"
            summary.rename(replacement)
            summary.symlink_to(replacement)

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_step_zero_from_candidate(
                case.workspace,
                attempt=1,
                source=source,
                evidence_provider=symlink_provider,
            )
        self.assertIn(
            raised.exception.classification,
            {"invalid_step_zero_evidence", "invalid_workspace_path"},
        )
        # Failed evidence never lands under Workspace authority.
        self.assertFalse((case.workspace / "voxblame").exists())
        self.assertFalse((case.workspace / "steps/000000").exists())

    def test_provider_hardlink_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        facade, source, candidate_sha = self._prepare_ready_workspace(case)

        def hardlink_provider(request):
            _write_step_measurement(
                request.voxblame_output,
                step=0,
                compare_to=None,
                reference_sha=case.reference_sha,
                candidate_sha=candidate_sha,
                observable_sha="9" * 64,
                accepted=False,
            )
            _write_step_preview(
                request.preview_output,
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=candidate_sha,
            )
            summary = request.voxblame_output / "steps/000000/summary.json"
            twin = summary.with_name("twin.json")
            os.link(summary, twin)

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_step_zero_from_candidate(
                case.workspace,
                attempt=1,
                source=source,
                evidence_provider=hardlink_provider,
            )
        self.assertIn(
            raised.exception.classification,
            {"invalid_step_zero_evidence", "invalid_workspace_path"},
        )
        self.assertFalse((case.workspace / "voxblame").exists())

    def test_provider_oversized_output_fails_closed(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)

        facade, source, candidate_sha = self._prepare_ready_workspace(case)

        # Coax a very small allowed size so the test does not need to write
        # a real 512 MiB file: a small ceiling still exercises the fail-closed
        # code path W1 uses to bound stage artifacts.
        original = facade._MAX_STEP_ZERO_STAGE_FILE_BYTES
        facade._MAX_STEP_ZERO_STAGE_FILE_BYTES = 32
        self.addCleanup(setattr, facade, "_MAX_STEP_ZERO_STAGE_FILE_BYTES", original)

        def large_provider(request):
            _write_step_measurement(
                request.voxblame_output,
                step=0,
                compare_to=None,
                reference_sha=case.reference_sha,
                candidate_sha=candidate_sha,
                observable_sha="9" * 64,
                accepted=False,
            )
            _write_step_preview(
                request.preview_output,
                reference_sha=case.reference_sha,
                profile_sha=case.profile_sha,
                candidate_sha=candidate_sha,
            )

        with self.assertRaises(facade.WorkspaceError) as raised:
            facade.publish_step_zero_from_candidate(
                case.workspace,
                attempt=1,
                source=source,
                evidence_provider=large_provider,
            )
        self.assertEqual(
            "invalid_step_zero_evidence", raised.exception.classification
        )
        self.assertFalse((case.workspace / "voxblame").exists())


class WorkspaceDecisionFactsTests(WorkspaceFacadeAgentTests):
    """Focused coverage for the closed W1 decision-facts projection.

    The projection reuses the same real-facade fixtures used by the
    Step 0 and Repair Cycle ingestion tests so no mock evidence bypasses
    the trusted measurement / preview / step manifest bindings that a
    real Workspace commits to.
    """

    _FORBIDDEN_TOKENS = (
        "voxblame",
        "steps/000000",
        "steps/000001",
        "work/attempts",
        "attempt-",
        "candidate.glb",
        "measurement.json",
        "voxblame.summary",
        "logical_sha256",
        "observable_sha256",
        "canonical_reference_sha256",
    )

    def _assert_closed_decision_facts(self, facts, *, step_ordinal, parent, accepted):
        self.assertEqual("mesh-to-cad.decision-facts/1", facts["schema"])
        self.assertEqual(step_ordinal, facts["step_ordinal"])
        self.assertEqual(parent, facts["parent_step_ordinal"])
        self.assertEqual(accepted, facts["accepted"])
        self.assertEqual(
            "acceptance_satisfied" if accepted else "unaccepted",
            facts["acceptance_state"],
        )
        # Every top-level key is closed to the fixed public schema.
        self.assertEqual(
            {
                "schema",
                "step_ordinal",
                "parent_step_ordinal",
                "accepted",
                "acceptance_state",
                "residual_summary",
                "repair_targets",
                "preview",
                "change_from_parent",
            },
            set(facts),
        )
        summary = facts["residual_summary"]
        self.assertEqual(
            {
                "objective_facts",
                "depth_8_missing_surface_count",
                "depth_8_excess_surface_count",
                "depth_8_surface_error_count",
                "depth_8_surface_error_rate",
            },
            set(summary),
        )
        self.assertEqual(
            {"global_depth_8_zero", "out_of_frame_clear", "no_evidence_conflict"},
            set(summary["objective_facts"]),
        )
        self.assertEqual({"identity_sha256", "render_variant"}, set(facts["preview"]))
        self.assertEqual("step", facts["preview"]["render_variant"])
        # The serialized document must not leak Workspace-relative or
        # authority-attempt tokens back to the Agent surface.
        serialized = json.dumps(facts, sort_keys=True)
        for token in self._FORBIDDEN_TOKENS:
            self.assertNotIn(token, serialized)

    def test_step_zero_decision_facts_expose_unaccepted_state_and_targets(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()
        self._publish_step_zero(case, facade)

        facts = facade.read_current_step_decision_facts(case.workspace, step=0)
        self._assert_closed_decision_facts(
            facts, step_ordinal=0, parent=None, accepted=False
        )
        # Step 0 has no parent — the parent-change comparison is absent.
        self.assertIsNone(facts["change_from_parent"])
        # Unaccepted Step 0 has depth-8 residuals; the fixture may or
        # may not emit a repair-target page, but if present it obeys the
        # closed bounded shape.
        summary = facts["residual_summary"]
        self.assertFalse(summary["objective_facts"]["global_depth_8_zero"])
        self.assertGreaterEqual(summary["depth_8_surface_error_count"], 1)
        targets = facts["repair_targets"]
        if targets is not None:
            self.assertLessEqual(targets["returned"], 8)
            self.assertEqual(targets["returned"], len(targets["items"]))
            for item in targets["items"]:
                self.assertEqual(
                    {
                        "target_key",
                        "mask_sha256",
                        "rank",
                        "kind",
                        "missing_surface_count",
                        "excess_surface_count",
                        "surface_error_count",
                    },
                    set(item),
                )
                self.assertIn(item["kind"], {"interior", "exterior"})
                self.assertTrue(item["target_key"].startswith("step-000000:"))
                self.assertRegex(item["mask_sha256"], r"^[0-9a-f]{64}$")

    def test_repair_decision_facts_expose_parent_change_comparison(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()
        cycle_source, repair_stub = self._prepare_repair_cycle(case, facade)
        facade.publish_cycle_from_candidate(
            case.workspace,
            attempt=2,
            source=cycle_source,
            evidence_provider=repair_stub,
        )

        facts = facade.read_current_step_decision_facts(case.workspace, step=1)
        self._assert_closed_decision_facts(
            facts,
            step_ordinal=1,
            parent=0,
            accepted=facts["accepted"],
        )
        change = facts["change_from_parent"]
        self.assertEqual(
            {"no_observable_geometry_change", "parent_accepted"}, set(change)
        )
        self.assertIsInstance(change["no_observable_geometry_change"], bool)
        self.assertIsInstance(change["parent_accepted"], bool)

    def test_decision_facts_reject_out_of_range_step(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()
        self._publish_step_zero(case, facade)

        with self.assertRaises(facade.WorkspaceError):
            facade.read_current_step_decision_facts(case.workspace, step=-1)
        with self.assertRaises(facade.WorkspaceError):
            facade.read_current_step_decision_facts(case.workspace, step=6)
        with self.assertRaises(facade.WorkspaceError):
            facade.read_current_step_decision_facts(case.workspace, step=True)  # type: ignore[arg-type]

    def test_decision_facts_fail_closed_on_corrupt_measurement(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()
        self._publish_step_zero(case, facade)

        measurement_path = case.workspace / "voxblame/steps/000000/summary.json"
        original = measurement_path.read_bytes()
        corrupted = json.loads(original)
        corrupted["errors_by_depth"][7]["surface_error_rate"] = float("inf")
        measurement_path.write_text(json.dumps(corrupted), encoding="utf-8")
        try:
            with self.assertRaises(facade.WorkspaceError):
                facade.read_current_step_decision_facts(case.workspace, step=0)
        finally:
            measurement_path.write_bytes(original)

    def test_decision_facts_reject_invalid_target_selection_identity(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()
        self._publish_step_zero(case, facade)

        measurement_path = case.workspace / "voxblame/steps/000000/summary.json"
        original = measurement_path.read_bytes()
        measurement = json.loads(original)
        measurement["repair_targets"] = {
            "total": 1,
            "returned": 1,
            "remaining": 0,
            "items": [
                {
                    "target_key": "step-000000:target-0123456789abcdef",
                    "mask": {"logical_sha256": "c" * 64},
                    "display_rank": 0,
                    "kind": "interior",
                    "error_profile": {
                        "missing_surface_count": 1,
                        "excess_surface_count": 0,
                        "surface_error_count": 1,
                    },
                }
            ],
        }
        valid = json.dumps(measurement).encode()
        measurement_path.write_bytes(valid)
        item = facade.read_current_step_decision_facts(case.workspace, step=0)[
            "repair_targets"
        ]["items"][0]
        self.assertEqual("step-000000:target-0123456789abcdef", item["target_key"])
        self.assertEqual("c" * 64, item["mask_sha256"])
        try:
            for field, invalid in (
                ("target_key", "../secret"),
                ("target_key", "step-000000:target-0123456789abcdef:extra"),
                ("mask", None),
            ):
                with self.subTest(field=field):
                    corrupted = json.loads(valid)
                    target = corrupted["repair_targets"]["items"][0]
                    if field == "mask":
                        target["mask"]["logical_sha256"] = "A" * 64
                    else:
                        target[field] = invalid
                    measurement_path.write_text(json.dumps(corrupted), encoding="utf-8")
                    with self.assertRaises(facade.WorkspaceError):
                        facade.read_current_step_decision_facts(case.workspace, step=0)
        finally:
            measurement_path.write_bytes(original)


if __name__ == "__main__":
    unittest.main()
