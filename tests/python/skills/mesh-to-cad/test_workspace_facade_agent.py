"""Direct facade integration checks for Agent candidate publication."""

from __future__ import annotations

import hashlib
import importlib.util
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


class WorkspaceFacadeAgentTests(unittest.TestCase):
    def test_step_and_cycle_ingestion_uses_one_real_facade_target_owner(self) -> None:
        fixture = _load_fixture()
        case = fixture.WorkspaceCliTests(
            "test_init_and_step_zero_publish_cross_checked_immutable_state"
        )
        case.setUp()
        self.addCleanup(case.temporary.cleanup)
        facade = _load_facade()

        status, _payload, stderr = case.invoke(
            "init", "--workspace", str(case.workspace), "--prepared", str(case.prepared_setup())
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = case.invoke(
            "begin-attempt",
            "--workspace",
            str(case.workspace),
            "--plan",
            str(case.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        facade.run_attempt_command(
            case.workspace,
            attempt=1,
            phase="candidate",
            argv=[sys.executable, "-c", ""],
            timeout_seconds=60,
        )
        candidate, candidate_sha = case.candidate("agent-step", b"agent step")
        source = case.root / "agent-step-source"
        (source / "candidate").mkdir(parents=True)
        shutil.copytree(candidate, source / "candidate", dirs_exist_ok=True)
        shutil.copy2(
            source / "candidate/artifacts/model.glb", source / "candidate.glb"
        )
        # Deliberately do NOT place measurement.json or preview/ in the
        # source: A-A2 rejects candidate-authored Step 0 evidence.

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
        self.assertEqual({"step": 0}, published)
        self.assertTrue((case.workspace / "steps/000000").is_dir())
        self.assertTrue((case.workspace / "voxblame/steps/000000/summary.json").is_file())
        # W1 owns the stage lifecycle: no external or internal stage
        # residue survives publication.  The external stage lives in
        # the system temp root and is cleaned up in the finally block;
        # the internal promotion stage was renamed onto voxblame/ and
        # the empty parent removed.
        workspace_residue = [
            entry.name
            for entry in case.workspace.iterdir()
            if entry.name.startswith(".voxblame-promotion-")
        ]
        self.assertEqual([], workspace_residue)

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
        cycle_measurement = case.measurement(
            step=1,
            compare_to=0,
            candidate_sha=cycle_sha,
            observable_sha="b" * 64,
            accepted=False,
        )
        cycle_preview = case.preview("agent-cycle-preview", cycle_sha)
        diff = case.region_diff(
            "agent-cycle-diff",
            plan=plan,
            from_step=0,
            to_step=1,
            before_observable="9" * 64,
            after_observable="b" * 64,
        )
        assessment = case.assessment("agent-cycle", from_step=0, to_step=1)
        source_changes = case.source_changes("agent-cycle", from_step=0, to_step=1)
        cycle_source = case.root / "agent-cycle-source"
        (cycle_source / "candidate").mkdir(parents=True)
        shutil.copytree(cycle_candidate, cycle_source / "candidate", dirs_exist_ok=True)
        shutil.copy2(
            cycle_source / "candidate/artifacts/model.glb", cycle_source / "candidate.glb"
        )
        (cycle_source / "measurement.json").write_bytes(cycle_measurement.read_bytes())
        shutil.copytree(cycle_preview, cycle_source / "preview")
        for path, name in (
            (diff, "region-diff.json"),
            (assessment, "assessment.json"),
            (source_changes, "source-changes.json"),
        ):
            shutil.copy2(path, cycle_source / name)
        cycle_measurement.unlink()

        repaired = facade.publish_cycle_from_candidate(
            case.workspace,
            attempt=2,
            source=cycle_source,
        )
        self.assertEqual({"step": {"step": 1}, "cycle": 1}, repaired)
        self.assertTrue((case.workspace / "steps/000001").is_dir())
        self.assertTrue((case.workspace / "voxblame/steps/000001/summary.json").is_file())
        self.assertNotIn("work/", str(published))
        self.assertNotIn("work/", str(repaired))

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
        (source / "candidate").mkdir(parents=True)
        shutil.copytree(candidate, source / "candidate", dirs_exist_ok=True)
        shutil.copy2(
            source / "candidate/artifacts/model.glb", source / "candidate.glb"
        )
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
        (source / "candidate").mkdir(parents=True)
        shutil.copytree(candidate, source / "candidate", dirs_exist_ok=True)
        shutil.copy2(
            source / "candidate/artifacts/model.glb", source / "candidate.glb"
        )
        return facade, source, candidate_sha

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


if __name__ == "__main__":
    unittest.main()
