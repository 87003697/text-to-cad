"""Direct facade integration checks for Agent candidate publication."""

from __future__ import annotations

import importlib.util
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
    spec.loader.exec_module(module)
    return module


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
        measurement = case.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        preview = case.preview("agent-step-preview", candidate_sha)
        source = case.root / "agent-step-source"
        (source / "candidate").mkdir(parents=True)
        shutil.copytree(candidate, source / "candidate", dirs_exist_ok=True)
        # A-A1 internal producer boundary: the trusted candidate tree exposes
        # the fixed filenames the W1 facade discovers.  A real trusted
        # provider will replace these fixture copies in A-A2.
        shutil.copy2(
            source / "candidate/artifacts/model.glb", source / "candidate.glb"
        )
        (source / "measurement.json").write_bytes(measurement.read_bytes())
        shutil.copytree(preview, source / "preview")
        measurement.unlink()

        published = facade.publish_step_zero_from_candidate(
            case.workspace,
            attempt=1,
            source=source,
        )
        self.assertEqual({"step": 0}, published)
        self.assertTrue((case.workspace / "steps/000000").is_dir())
        self.assertTrue((case.workspace / "voxblame/steps/000000/summary.json").is_file())

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


if __name__ == "__main__":
    unittest.main()
