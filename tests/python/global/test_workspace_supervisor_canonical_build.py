"""Tests for the trusted canonical-build execution path in WorkspaceSupervisor.

These tests exercise trusted candidate execution: the fixed argv against the
four-file-root shipped manifest and read-only ``/builder`` mounts, the fresh
tool-owned candidate-relative output directory, the pre-run and
post-run publication of ``candidate.glb``, and the failure hygiene
that removes partial output and rejects source/output tampering.

The tests inject a command runner that simulates the trusted tool by
producing the fixed five-file canonical output tree.  A separate
higher-cost integration test in ``test_workspace_supervisor.py`` runs
the real canonical build under bwrap.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

from scripts.pilot.workspace_supervisor import (
    CANDIDATE_PUBLISHED_MEASUREMENT_NAME,
    SupervisorError,
    WorkspaceSupervisor,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.completed_cycles = 0
        self.total_attempts = 0
        self.tool_failures = 0
        self.remaining_attempts = 3
        self.remaining_tool_failures = 2
        self.final_delivery_present = False
        self.published: list[dict] = []

    def workspace_status(self, _workspace: Path) -> dict:
        return {
            "completed_cycles": self.completed_cycles,
            "next_intended_step": 0 if not self.completed_cycles else self.completed_cycles + 1,
            "total_attempts": self.total_attempts,
            "tool_failures": self.tool_failures,
            "head_steps": [0] if self.completed_cycles else [],
            "final_delivery_present": self.final_delivery_present,
            "remaining_attempts": self.remaining_attempts,
            "remaining_tool_failures": self.remaining_tool_failures,
        }

    def workspace_initialized(self, _workspace: Path) -> bool:
        return True

    def begin_attempt(self, _workspace: Path, _plan: Path, **kwargs) -> dict:
        self.total_attempts += 1
        return {"attempt": self.total_attempts, "intended_step": kwargs["intended_step"]}

    def publish_step_zero_from_candidate(self, *_a, **_k) -> dict:
        return {"step": 0}

    def publish_cycle_from_candidate(self, *_a, **_k) -> dict:
        self.completed_cycles += 1
        return {"step": self.completed_cycles, "cycle": self.completed_cycles}

    def seed_repair_source_from_parent_step(self, *_a, **_k) -> None:
        return None

    def run_attempt_command(self, *_a, **_k):
        raise AssertionError("candidate execution must use the supervisor operation port")


def _write_canonical_outputs(output_dir: Path, source_digests: dict[str, str]) -> None:
    """Produce a plausible canonical output tree for tests."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "canonical.step").write_bytes(b"ISO-10303-21;\nENDSEC;\nEND-ISO-10303-21;\n")
    (output_dir / "measurement.glb").write_bytes(b"glTF\x02\x00\x00\x00" + b"\x00" * 32)
    (output_dir / "profile.json").write_text(
        json.dumps({"schema": "mesh-to-cad.cad-build-profile/1"}) + "\n"
    )
    recipe_inputs = [{"path": path, "sha256": digest} for path, digest in source_digests.items()]
    recipe = {
        "schema": "mesh-to-cad.rebuild-recipe/1",
        "executable": "cad.canonical-build/1",
        "inputs": recipe_inputs,
    }
    (output_dir / "rebuild.json").write_text(json.dumps(recipe) + "\n")
    files = []
    for name in ("canonical.step", "measurement.glb", "profile.json", "rebuild.json"):
        payload = (output_dir / name).read_bytes()
        files.append({"path": name, "size": len(payload), "sha256": _sha256(payload)})
    manifest = {
        "schema": "mesh-to-cad.build/1",
        "adapter": {"id": "cad.canonical-build/1", "version": 1},
        "primaryArtifact": "canonical.step",
        "measurementGlb": "measurement.glb",
        "files": files,
    }
    (output_dir / "build.json").write_text(json.dumps(manifest) + "\n")


class CanonicalBuildExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace_root = self.root / "workspace"
        self.workspace_root.mkdir()
        self.workspace = _Workspace(self.workspace_root)
        self.sup = WorkspaceSupervisor(
            self.workspace_root,
            candidate_root=self.root / "candidate",
            staging_dir=self.root / "staging",
            workspace_api=self.workspace,
            trusted_tools_root=REPO_ROOT,
        )

    def tearDown(self) -> None:
        try:
            self.sup.close()
        finally:
            self.temp.cleanup()

    def _start(self, parent_step_handle: str | None = None) -> tuple[str, str, str, Path]:
        plan = self.sup.candidate_root / "plan.json"
        plan.write_text("{}", encoding="utf-8")
        plan_handle = self.sup.register_plan(plan)
        result = self.sup.start_attempt(
            self.sup.workspace_handle, plan_handle, parent_step_handle
        )
        work = self.sup.candidate_root / "work"
        (work / "source").mkdir()
        (work / "source/model.py").write_text(
            "def gen_step():\n    return None\n", encoding="utf-8"
        )
        return (
            result["attempt_handle"],
            result["candidate_handle"],
            result["capability_bundle_handle"],
            work,
        )

    def _install_runner(
        self, produce_outputs: bool = True, *, manifest_adapter: object = None
    ) -> dict:
        observed: dict = {}

        def runner(argv, **kwargs):
            observed["argv"] = list(argv)
            observed["cwd"] = Path(kwargs["cwd"])
            observed["env"] = dict(kwargs["env"])
            if produce_outputs:
                output_index = argv.index("--output-dir") + 1
                output_relative = argv[output_index]
                source_index = argv.index("--source") + 1
                source_relative = argv[source_index]
                cwd = Path(kwargs["cwd"])
                digests = {
                    source_relative: _sha256((cwd / source_relative).read_bytes())
                }
                cursor = 0
                while cursor < len(argv):
                    if argv[cursor] == "--input":
                        rel = argv[cursor + 1]
                        digests[rel] = _sha256((cwd / rel).read_bytes())
                        cursor += 2
                        continue
                    cursor += 1
                output_dir = cwd / output_relative
                _write_canonical_outputs(output_dir, digests)
                if manifest_adapter is not None:
                    manifest_path = output_dir / "build.json"
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    manifest["adapter"] = manifest_adapter
                    manifest_path.write_text(
                        json.dumps(manifest) + "\n", encoding="utf-8"
                    )
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = runner
        return observed

    def test_capability_bundle_runs_fixed_trusted_argv_and_publishes_candidate_glb(self) -> None:
        attempt, candidate, bundle, work = self._start()
        observed = self._install_runner()
        result = self.sup.run_candidate_tool(
            self.sup.workspace_handle, attempt, candidate, bundle
        )
        self.assertEqual("completed", result["state"])
        self.assertEqual(
            ["submit_step_zero", "workspace_status"],
            result["permitted_next_intents"],
        )
        # Fixed trusted argv: /runtime/bin/python + /builder entrypoint +
        # build subcommand + fixed source + fresh output-dir.
        argv = observed["argv"]
        self.assertEqual("/runtime/bin/python", argv[0])
        self.assertEqual("/builder/canonical-build", argv[1])
        self.assertEqual("build", argv[2])
        self.assertEqual("--source", argv[3])
        self.assertEqual("source/model.py", argv[4])
        self.assertEqual("--output-dir", argv[5])
        output_relative = argv[6]
        self.assertTrue(output_relative.startswith(".trusted-out-"))
        # Fresh output directory was created below the current work tree.
        self.assertTrue((work / output_relative).is_dir())
        # candidate.glb was descriptor-copied from measurement.glb.
        published = work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME
        self.assertTrue(published.is_file())
        self.assertFalse(published.is_symlink())
        self.assertEqual(
            (work / output_relative / "measurement.glb").read_bytes(),
            published.read_bytes(),
        )
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("candidate_glb_preexisting", raised.exception.classification)

    def test_successful_repair_build_only_advertises_submit_repair(self) -> None:
        self.workspace.completed_cycles = 1
        parent_step_handle = self.sup.registry.issue("step", 0)
        attempt, candidate, bundle, _work = self._start(parent_step_handle)
        self._install_runner()
        result = self.sup.run_candidate_tool(
            self.sup.workspace_handle, attempt, candidate, bundle
        )
        self.assertEqual("completed", result["state"])
        self.assertEqual(
            ["submit_repair", "workspace_status"],
            result["permitted_next_intents"],
        )

    def test_preexisting_candidate_glb_rejected_and_partial_output_removed(self) -> None:
        attempt, candidate, bundle, work = self._start()
        observed = self._install_runner()
        (work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).write_bytes(b"forged glb")
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("candidate_glb_preexisting", raised.exception.classification)
        # No trusted tool was invoked; no output dir was created.
        for entry in work.iterdir():
            if entry.name == "source":
                continue
            self.assertEqual(CANDIDATE_PUBLISHED_MEASUREMENT_NAME, entry.name)

    def test_missing_source_module_rejected(self) -> None:
        attempt, candidate, bundle, work = self._start()
        (work / "source/model.py").unlink()
        self._install_runner()
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("candidate_source_missing", raised.exception.classification)

    def test_output_tree_extra_file_rejected_and_no_candidate_glb_published(self) -> None:
        attempt, candidate, bundle, work = self._start()
        observed: dict = {}

        def runner(argv, **kwargs):
            observed["argv"] = list(argv)
            output_relative = argv[argv.index("--output-dir") + 1]
            source_relative = argv[argv.index("--source") + 1]
            cwd = Path(kwargs["cwd"])
            digests = {source_relative: _sha256((cwd / source_relative).read_bytes())}
            _write_canonical_outputs(cwd / output_relative, digests)
            # Extra file breaks the closed output contract.
            (cwd / output_relative / "leftover.txt").write_bytes(b"extra")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = runner
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("canonical_output_invalid", raised.exception.classification)
        self.assertFalse((work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).exists())
        # Partial output tree removed on failure.
        residual = [
            p.name
            for p in work.iterdir()
            if p.name not in {"source", ".home", ".tmp"}
        ]
        self.assertEqual([], residual)

    def test_output_symlink_rejected(self) -> None:
        attempt, candidate, bundle, work = self._start()

        def runner(argv, **kwargs):
            output_relative = argv[argv.index("--output-dir") + 1]
            source_relative = argv[argv.index("--source") + 1]
            cwd = Path(kwargs["cwd"])
            digests = {source_relative: _sha256((cwd / source_relative).read_bytes())}
            _write_canonical_outputs(cwd / output_relative, digests)
            # Replace measurement.glb with a symlink out.
            outside = cwd / "outside.glb"
            outside.write_bytes(b"attacker-controlled")
            target = cwd / output_relative / "measurement.glb"
            target.unlink()
            target.symlink_to(outside)
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = runner
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("canonical_output_invalid", raised.exception.classification)
        self.assertFalse((work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).exists())

    def test_source_mutation_between_build_and_validation_detected(self) -> None:
        attempt, candidate, bundle, work = self._start()

        def runner(argv, **kwargs):
            output_relative = argv[argv.index("--output-dir") + 1]
            source_relative = argv[argv.index("--source") + 1]
            cwd = Path(kwargs["cwd"])
            source_path = cwd / source_relative
            digests = {source_relative: _sha256(source_path.read_bytes())}
            _write_canonical_outputs(cwd / output_relative, digests)
            # Mutate the source after the tool has produced its output.
            source_path.write_text(
                "def gen_step():\n    return object()\n", encoding="utf-8"
            )
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = runner
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("candidate_source_mutated", raised.exception.classification)
        self.assertFalse((work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).exists())

    def test_manifest_digest_mismatch_rejected(self) -> None:
        attempt, candidate, bundle, work = self._start()

        def runner(argv, **kwargs):
            output_relative = argv[argv.index("--output-dir") + 1]
            source_relative = argv[argv.index("--source") + 1]
            cwd = Path(kwargs["cwd"])
            digests = {source_relative: _sha256((cwd / source_relative).read_bytes())}
            _write_canonical_outputs(cwd / output_relative, digests)
            manifest_path = cwd / output_relative / "build.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"][0]["sha256"] = "0" * 64  # tampered digest
            manifest_path.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, b"", b"")

        self.sup._command_runner = runner
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("canonical_output_invalid", raised.exception.classification)

    def test_manifest_adapter_requires_closed_production_shape(self) -> None:
        attempt, candidate, bundle, work = self._start()
        adapters = (
            {"id": "cad.canonical-build/1", "version": 1, "extra": True},
            ["cad.canonical-build/1", 1],
            {"name": "cad.canonical-build/1", "version": 1},
            {"id": "cad.canonical-build/1", "version": 2},
            {"id": 1, "version": 1},
            {"id": "cad.canonical-build/1", "version": True},
        )

        for adapter in adapters:
            with self.subTest(adapter=adapter):
                self._install_runner(manifest_adapter=adapter)
                with self.assertRaises(SupervisorError) as raised:
                    self.sup.run_candidate_tool(
                        self.sup.workspace_handle, attempt, candidate, bundle
                    )
                self.assertEqual(
                    "canonical_output_invalid", raised.exception.classification
                )
                self.assertFalse(
                    (work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).exists()
                )

    def test_tool_nonzero_exit_removes_partial_output(self) -> None:
        attempt, candidate, bundle, work = self._start()

        def runner(argv, **kwargs):
            output_relative = argv[argv.index("--output-dir") + 1]
            source_relative = argv[argv.index("--source") + 1]
            cwd = Path(kwargs["cwd"])
            digests = {source_relative: _sha256((cwd / source_relative).read_bytes())}
            _write_canonical_outputs(cwd / output_relative, digests)
            return subprocess.CompletedProcess(argv, 3, b"", b"tool failed\n")

        self.sup._command_runner = runner
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("candidate_tool_failed", raised.exception.classification)
        self.assertFalse((work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).exists())
        # Partial output removed.
        residual = [
            p.name
            for p in work.iterdir()
            if p.name not in {"source", ".home", ".tmp"}
        ]
        self.assertEqual([], residual)

    def test_capability_bundle_budget_bounded_per_attempt(self) -> None:
        attempt, candidate, bundle, work = self._start()
        self._install_runner()
        # First success is accepted.  Then we re-run to force many
        # invocations; the per-Attempt budget is bounded above 0.
        for _ in range(8):
            (work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).unlink(missing_ok=True)
            for entry in list(work.iterdir()):
                if entry.name.startswith(".trusted-out-"):
                    shutil.rmtree(entry)
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        (work / CANDIDATE_PUBLISHED_MEASUREMENT_NAME).unlink(missing_ok=True)
        with self.assertRaises(SupervisorError) as raised:
            self.sup.run_candidate_tool(
                self.sup.workspace_handle, attempt, candidate, bundle
            )
        self.assertEqual("budget_violation", raised.exception.classification)

    def test_sandbox_argv_mounts_only_fixed_builder_paths_read_only(self) -> None:
        from scripts.pilot import workspace_supervisor as supervisor_module
        from unittest import mock

        operation = supervisor_module._CandidateOperation(
            ("/runtime/bin/python", "/builder/canonical-build", "build"),
            60,
            (),
        )
        with mock.patch.object(supervisor_module.shutil, "which", return_value="/fake/bwrap"):
            argv = supervisor_module._candidate_sandbox_argv(
                operation,
                self.sup.candidate_root,
                self.root / "fake-runtime",
                canonical_build_root=self.sup.canonical_build_root,
                cadgen_runtime_root=self.sup.cadgen_runtime_root,
            )
        canonical_index = argv.index(os.fspath(self.sup.canonical_build_root))
        cadgen_index = argv.index(os.fspath(self.sup.cadgen_runtime_root))
        self.assertEqual(
            ["--ro-bind", os.fspath(self.sup.canonical_build_root), "/builder/canonical-build"],
            argv[canonical_index - 1 : canonical_index + 2],
        )
        self.assertEqual(
            ["--ro-bind", os.fspath(self.sup.cadgen_runtime_root), "/builder/packages/cadgen"],
            argv[cadgen_index - 1 : cadgen_index + 2],
        )
        self.assertNotIn(os.fspath(REPO_ROOT), argv)


if __name__ == "__main__":
    unittest.main()
