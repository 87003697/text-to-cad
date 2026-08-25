"""Public mesh-to-cad Workspace helper contract tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_PATH = (
    REPO_ROOT
    / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/cli.py"
)
WORKSPACE_HELPER_PATH = (
    REPO_ROOT
    / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/workspace.py"
)
MESH_COMPARE_PATH = REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare"
CAD_BUILD_PATH = REPO_ROOT / "skills/cad/scripts/canonical-build"
MESH_COMPARE_ENTRYPOINT = MESH_COMPARE_PATH / "cli.py"
CAD_BUILD_ENTRYPOINT = CAD_BUILD_PATH / "__main__.py"
PREVIEW_PROFILE_PATH = (
    REPO_ROOT
    / "packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json"
)
PILOT_RUNNER_PATH = REPO_ROOT / "scripts/pilot/runner.py"
PILOT_REVIEW_PATH = (
    REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-review/__main__.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("mesh_to_cad_workspace_cli", CLI_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_workspace_helper():
    module_name = "mesh_to_cad_workspace_helper"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    helper_dir = str(WORKSPACE_HELPER_PATH.parent)
    if helper_dir not in sys.path:
        sys.path.insert(0, helper_dir)
    spec = importlib.util.spec_from_file_location(
        module_name, WORKSPACE_HELPER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )


def _identity(schema: str, value: dict) -> str:
    body = (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")
    return hashlib.sha256(schema.encode("utf-8") + b"\0" + body).hexdigest()


class WorkspaceCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "experiment"
        self.workspace.mkdir()
        self.git("init", "-b", "develop")
        self.git("config", "user.name", "Workspace Test")
        self.git("config", "user.email", "workspace@example.invalid")
        self.cli = _load_cli()
        self.reference_sha = "1" * 64
        self.profile_sha = "2" * 64

    def git(self, *args: str) -> str:
        result = subprocess.run(
            ("git", *args),
            cwd=self.workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        return result.stdout.strip()

    def test_run_command_defaults_to_thirty_minute_workspace_budget(self) -> None:
        parsed = self.cli._parser().parse_args(
            [
                "run",
                "--workspace",
                str(self.workspace),
                "--attempt",
                "1",
                "--phase",
                "preview",
                "--",
                "true",
            ]
        )

        self.assertEqual(1800, parsed.timeout_seconds)
        self.assertEqual(1800, self.cli.run_attempt_command.__globals__["MAX_COMMAND_SECONDS"])

    def test_generic_run_rejects_canonical_build_phase(self) -> None:
        status, _initialized, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)

        status, result, _stderr = self.invoke(
            "run",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--phase",
            "build",
            "--",
            "true",
        )

        self.assertEqual(2, status)
        self.assertEqual("invalid_command", result["error"]["classification"])
        self.assertFalse((self.workspace / "work/attempts/000001/commands").exists())

    def test_workspace_build_preflights_registered_entrypoint(self) -> None:
        status, _initialized, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)

        candidate = self.workspace / "work/attempts/000001/candidate"
        source = candidate / "source/model.py"
        sidecar = candidate / "input/params.json"
        source.parent.mkdir(parents=True)
        sidecar.parent.mkdir(parents=True)
        source.write_text(
            "\n".join(
                (
                    "import json",
                    "from pathlib import Path",
                    "from build123d import Box",
                    "",
                    "def gen_step():",
                    "    params = json.loads(Path('input/params.json').read_text())",
                    "    return Box(params['length'], 1, 1)",
                    "",
                )
            ),
            encoding="utf-8",
        )
        _write_json(sidecar, {"length": 2})
        source_relative = source.relative_to(self.workspace).as_posix()
        sidecar_relative = sidecar.relative_to(self.workspace).as_posix()
        output_relative = "work/attempts/000001/candidate/artifacts"

        registry = self.final_tool_arguments()[-1]
        status, command, stderr = self.invoke(
            "build",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--source",
            source_relative,
            "--input",
            sidecar_relative,
            "--output-dir",
            output_relative,
            "--tool-registry",
            registry,
        )

        self.assertEqual(0, status, (stderr, command))
        command_stderr = (
            self.workspace
            / "work/attempts/000001/commands/000001/stderr.log"
        ).read_text(encoding="utf-8")
        self.assertEqual("", command_stderr)
        self.assertEqual(0, command["command"]["exit_code"])
        self.assertEqual(sys.executable, command["command"]["argv"][0])
        self.assertEqual(str(CAD_BUILD_ENTRYPOINT), command["command"]["argv"][1])
        output = self.workspace / output_relative
        self.assertTrue((output / "build.json").is_file())
        self.assertTrue((output / "measurement.glb").is_file())

        isolated = self.root / "isolated"
        shutil.copytree(candidate / "source", isolated / "source")
        shutil.copytree(candidate / "input", isolated / "input")
        shutil.copy2(output / "rebuild.json", isolated / "rebuild.json")
        rebuilt = subprocess.run(
            (
                sys.executable,
                str(CAD_BUILD_ENTRYPOINT),
                "rebuild",
                "--recipe",
                "rebuild.json",
                "--output-dir",
                "rebuilt",
            ),
            cwd=isolated,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, rebuilt.returncode, rebuilt.stderr)
        self.assertTrue((isolated / "rebuilt/build.json").is_file())
        self.assertTrue((isolated / "rebuilt/measurement.glb").is_file())

    def test_workspace_build_preflight_failure_does_not_spend_command(self) -> None:
        status, _initialized, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)

        candidate = self.workspace / "work/attempts/000001/candidate"
        source = candidate / "source/model.py"
        source.parent.mkdir(parents=True)
        source.write_text(
            "from build123d import Compound\n\n"
            "def gen_step():\n"
            "    return Compound.make_compound([])\n",
            encoding="utf-8",
        )
        output_relative = "work/attempts/000001/candidate/artifacts"

        status, result, _stderr = self.invoke(
            "build",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--source",
            source.relative_to(self.workspace).as_posix(),
            "--output-dir",
            output_relative,
            "--tool-registry",
            self.final_tool_arguments()[-1],
        )

        self.assertEqual(2, status)
        self.assertEqual("build_preflight_failed", result["error"]["classification"])
        self.assertFalse(
            (self.workspace / "work/attempts/000001/commands").exists(),
            "a provider-free source error must not spend the Attempt command budget",
        )
        self.assertFalse((self.workspace / output_relative).exists())

    def test_incomplete_transaction_scan_skips_runtime_telemetry_tree(self) -> None:
        runtime_stage = self.workspace / "run/playwright/.tmp-browser-cache"
        authority_stage = self.workspace / "work/attempts/000001/.tmp-command"
        runtime_stage.mkdir(parents=True)
        authority_stage.mkdir(parents=True)
        scanned_roots: list[Path] = []
        original_rglob = Path.rglob

        def tracked_rglob(root: Path, pattern: str):
            scanned_roots.append(root)
            return original_rglob(root, pattern)

        finder = self.cli.validate_workspace.__globals__[
            "_find_incomplete_transactions"
        ]
        with mock.patch.object(Path, "rglob", tracked_rglob):
            recovery = finder(self.workspace)

        self.assertEqual(
            ["work/attempts/000001/.tmp-command"],
            [item["path"] for item in recovery],
        )
        self.assertNotIn(self.workspace, scanned_roots)
        self.assertNotIn(self.workspace / "run", scanned_roots)

    def invoke(self, *arguments: str) -> tuple[int, dict, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = self.cli.main(list(arguments))
        return status, json.loads(stdout.getvalue()), stderr.getvalue()

    def prepared_setup(self) -> Path:
        prepared = self.root / "prepared"
        reference = b"ply\nsynthetic canonical reference\n"
        (prepared / "input").mkdir(parents=True)
        (prepared / "input/reference.ply").write_bytes(reference)
        _write_json(
            prepared / "input/input.json",
            {
                "schema": "voxblame.canonical-reference/1",
                "canonical_reference_sha256": self.reference_sha,
                "reference_ply": {
                    "path": "input/reference.ply",
                    "sha256": _sha(reference),
                },
            },
        )
        (prepared / "setup").mkdir()
        _write_json(
            prepared / "experiment.json",
            {
                "schema": "mesh-to-cad.experiment/1",
                "workspace_id": "synthetic-workspace",
                "coordinate_contract": "trellis2_canonical/1",
                "canonical_reference_sha256": self.reference_sha,
                "preview_profile": {
                    "name": "cadena_residual_eight_view/1",
                    "sha256": self.profile_sha,
                },
            },
        )
        return prepared

    def test_init_rejects_retired_implicit_metadata(self) -> None:
        prepared = self.prepared_setup()
        experiment_path = prepared / "experiment.json"
        experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
        experiment["route"] = "implicit"
        _write_json(experiment_path, experiment)

        status, rejected, _stderr = self.invoke(
            "init", "--workspace", str(self.workspace), "--prepared", str(prepared)
        )

        self.assertEqual(2, status)
        self.assertEqual("invalid_setup", rejected["error"]["classification"])
        self.assertIn("implicit CAD experiment is unsupported", rejected["error"]["detail"])

    def test_registered_rebuild_recipe_rejects_retired_route_metadata(self) -> None:
        workspace_core = sys.modules["workspace_core"]
        recipe = {
            "schema": "mesh-to-cad.rebuild-recipe/1",
            "executable": "cad.canonical-build/1",
            "workingDirectory": ".",
            "network": "forbidden",
            "ambientInputs": "forbidden",
            "inputs": [{"id": "source"}],
            "route": "cad",
        }

        with self.assertRaises(self.cli.WorkspaceError) as raised:
            workspace_core._validate_registered_recipe_document(recipe)

        self.assertEqual("invalid_rebuild_recipe", raised.exception.classification)
        self.assertEqual("$.rebuild_recipe.route", raised.exception.path)

    def initial_plan(self) -> Path:
        plan = self.root / "initial-plan.json"
        _write_json(
            plan,
            {
                "schema": "mesh-to-cad.initial-plan/1",
                "summary": "Build the synthetic candidate in canonical coordinates.",
            },
        )
        return plan

    def candidate(self, name: str, mesh_bytes: bytes) -> tuple[Path, str]:
        root = self.workspace / "work" / name
        (root / "source").mkdir(parents=True)
        (root / "artifacts").mkdir()
        # Include the candidate ``name`` in the synthetic source so
        # distinct candidates carry distinct source bytes.  This lets
        # Repair Cycle tests exercise the source-change delta the
        # trusted Repair evidence provider derives from the parent
        # selected candidate and the current candidate.
        (root / "source/model.py").write_text(
            f"# synthetic source for {name}\n", encoding="utf-8"
        )
        (root / "artifacts/model.glb").write_bytes(mesh_bytes)
        return root, _sha(mesh_bytes)

    def measurement(
        self,
        *,
        step: int,
        compare_to: int | None,
        candidate_sha: str,
        observable_sha: str,
        accepted: bool,
        no_op: bool = False,
    ) -> Path:
        root = self.workspace / "voxblame" / "steps" / f"{step:06d}"
        root.mkdir(parents=True)
        summary = {
            "schema": "voxblame.summary/1",
            "coordinate_contract": "trellis2_canonical/1",
            "max_depth": 8,
            "step": step,
            "compare_to": compare_to,
            "report": f"voxblame/steps/{step:06d}/report.json",
            "canonical_reference": {
                "canonical_reference_sha256": self.reference_sha,
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
            "no_observable_geometry_change": no_op,
        }
        _write_json(root / "summary.json", summary)
        if step == 0:
            _write_json(
                self.workspace / "voxblame/session.json",
                {
                    "schema": "voxblame.session/2",
                    "canonical_reference": {
                        "canonical_reference_sha256": self.reference_sha
                    },
                },
            )
            (self.workspace / "voxblame/reference.vbsvo").write_bytes(b"vbsvo")
        return root / "summary.json"

    def preview(self, name: str, candidate_sha: str) -> Path:
        root = self.workspace / "work" / name
        root.mkdir(parents=True)
        png = b"synthetic png bytes"
        (root / "preview.png").write_bytes(png)
        metadata = {
                "schema": "voxblame.preview/1",
                "render_variant": "step",
                "canonical_frame": {
                    "coordinate_contract": "trellis2_canonical/1",
                },
                "profile": {
                    "name": "cadena_residual_eight_view/1",
                    "experiment_identity": {
                        "name": "cadena_residual_eight_view/1",
                        "sha256": self.profile_sha,
                    },
                },
                "reference": {
                    "canonical_reference_sha256": self.reference_sha,
                },
                "candidate": {"mesh_sha256": candidate_sha},
                "image": {
                    "path": "preview.png",
                    "sha256": _sha(png),
                },
            }
        metadata["preview_identity_sha256"] = hashlib.sha256(
            b"voxblame.preview/1\0"
            + (
                json.dumps(
                    metadata,
                    indent=2,
                    sort_keys=True,
                    separators=(",", ": "),
                )
                + "\n"
            ).encode("utf-8")
        ).hexdigest()
        _write_json(root / "preview.json", metadata)
        return root

    def repair_plan(self, name: str, *, from_step: int) -> Path:
        path = self.root / f"{name}.json"
        _write_json(
            path,
            {
                "schema": "voxblame.repair-batch/1",
                "from_step": from_step,
                "selected_targets": [
                    {"target_key": "missing:0", "mask_sha256": "a" * 64}
                ],
                "planned_edits": [
                    {
                        "edit_key": "edit-missing",
                        "target_keys": ["missing:0"],
                        "description": "Repair the selected synthetic region.",
                    }
                ],
                "rationale": "Exercise the bounded repair flow.",
                "preview_observation": "One residual is visible.",
            },
        )
        return path

    def region_diff(
        self,
        name: str,
        *,
        plan: Path,
        from_step: int,
        to_step: int,
        before_observable: str,
        after_observable: str,
    ) -> Path:
        plan_value = json.loads(plan.read_text(encoding="utf-8"))
        plan_bytes = (
            json.dumps(
                plan_value,
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n"
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
            json.dumps(
                document,
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n"
        ).encode("utf-8")
        document["identity"] = {
            "region_diff_sha256": hashlib.sha256(
                b"voxblame.region-diff/1\0" + identity_bytes
            ).hexdigest()
        }
        path = self.root / f"{name}.json"
        _write_json(path, document)
        return path

    def assessment(self, name: str, *, from_step: int, to_step: int) -> Path:
        path = self.root / f"{name}-assessment.json"
        _write_json(
            path,
            {
                "schema": "mesh-to-cad.assessment/1",
                "from_step": from_step,
                "to_step": to_step,
                "preview_observation": "The formal preview was inspected.",
                "summary": "Synthetic objective evidence recorded.",
            },
        )
        return path

    def source_changes(self, name: str, *, from_step: int, to_step: int) -> Path:
        path = self.root / f"{name}-source-changes.json"
        _write_json(
            path,
            {
                "schema": "mesh-to-cad.source-changes/1",
                "from_step": from_step,
                "to_step": to_step,
                "files": [
                    {
                        "path": "source/model.py",
                        "before_sha256": "c" * 64,
                        "after_sha256": "d" * 64,
                    }
                ],
            },
        )
        return path

    def publish_initial_flow(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate("candidate-0", b"candidate zero")
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        preview = self.preview("preview-0", candidate_sha)
        status, _published, stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(preview),
        )
        self.assertEqual(0, status, stderr)

    def publish_one_cycle(self) -> dict:
        plan = self.repair_plan("recover-plan", from_step=0)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate(
            "recover-candidate", b"recover candidate"
        )
        measurement = self.measurement(
            step=1,
            compare_to=0,
            candidate_sha=candidate_sha,
            observable_sha="b" * 64,
            accepted=False,
        )
        preview = self.preview("recover-preview", candidate_sha)
        diff = self.region_diff(
            "recover-diff",
            plan=plan,
            from_step=0,
            to_step=1,
            before_observable="9" * 64,
            after_observable="b" * 64,
        )
        status, published, stderr = self.invoke(
            "publish-cycle",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(preview),
            "--region-diff",
            str(diff),
            "--assessment",
            str(self.assessment("recover", from_step=0, to_step=1)),
            "--source-changes",
            str(self.source_changes("recover", from_step=0, to_step=1)),
        )
        self.assertEqual(0, status, stderr)
        return published["cycle"]

    def canonical_cad_flow(self, *, accepted: bool = True) -> tuple[Path, Path]:
        candidate = self.workspace / "work" / "canonical-cad"
        (candidate / "source").mkdir(parents=True)
        (candidate / "config.txt").write_text("declared rebuild input\n", encoding="utf-8")

        def write_source(length: float) -> None:
            (candidate / "source/model.py").write_text(
                "\n".join(
                    (
                        "from build123d import Align, Box",
                        "",
                        "def gen_step():",
                        f"    return Box({length}, 0.01, 0.01, align=(Align.CENTER, Align.CENTER, Align.CENTER))",
                        "",
                    )
                ),
                encoding="utf-8",
            )

        def build() -> None:
            candidate_relative = candidate.relative_to(self.workspace).as_posix()
            built = subprocess.run(
                (
                    sys.executable,
                    str(CAD_BUILD_PATH),
                    "build",
                    "--source",
                    f"{candidate_relative}/source/model.py",
                    "--input",
                    f"{candidate_relative}/config.txt",
                    "--output-dir",
                    f"{candidate_relative}/built",
                ),
                cwd=self.workspace,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(0, built.returncode, built.stderr)

        write_source(1.0)
        build()
        candidate_mesh = candidate / "built/measurement.glb"

        prepared = self.root / "prepared-canonical-cad"
        reference = prepared / "input"
        prepared.mkdir()
        created = subprocess.run(
            (
                sys.executable,
                str(MESH_COMPARE_PATH),
                "voxblame-prepare-reference",
                str(candidate_mesh),
                "--output",
                str(reference),
            ),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, created.returncode, created.stderr)
        if not accepted:
            shutil.rmtree(candidate / "built")
            write_source(0.8)
            build()
        input_document = json.loads(
            (reference / "input.json").read_text(encoding="utf-8")
        )
        self.reference_sha = input_document["canonical_reference_sha256"]
        self.profile_sha = _sha(PREVIEW_PROFILE_PATH.read_bytes())
        (prepared / "setup").mkdir(exist_ok=True)
        _write_json(
            prepared / "experiment.json",
            {
                "schema": "mesh-to-cad.experiment/1",
                "workspace_id": "canonical-cad-final",
                "coordinate_contract": "trellis2_canonical/1",
                "canonical_reference_sha256": self.reference_sha,
                "preview_profile": {
                    "name": "cadena_residual_eight_view/1",
                    "sha256": self.profile_sha,
                },
            },
        )
        return prepared, candidate

    def final_selection(self, *, accepted: bool) -> Path:
        preview = json.loads(
            (self.workspace / "steps/000000/preview/preview.json").read_text(
                encoding="utf-8"
            )
        )
        path = self.root / "final-selection.json"
        _write_json(
            path,
            {
                "schema": "mesh-to-cad.final-selection/1",
                "considered_steps": [0],
                "selected_step": 0,
                "preview": {
                    "identity_sha256": preview["preview_identity_sha256"],
                    "observation": "The selected synthetic box matches the reference.",
                    "evidence_conflict": False,
                    "conflict_details": None,
                },
                "accepted": accepted,
                "stop_reason": "acceptance_satisfied" if accepted else "cycle_limit",
                "evidence": [
                    {
                        "kind": "measured_step",
                        "path": "steps/000000/measurement.json",
                        "sha256": _sha(
                            (self.workspace / "steps/000000/measurement.json").read_bytes()
                        ),
                    }
                ],
            },
        )
        return path

    def final_notes(self) -> Path:
        path = self.root / "notes.md"
        path.write_text(
            "\n\n".join(
                (
                    "## Input\nSynthetic canonical CAD input.",
                    "## Modeling Intent\nRebuild the measured box.",
                    "## Preserved Structural Features\nOne centered box.",
                    "## Omitted Surface Details\nNone.",
                    "## Repair Trajectory\nStep 0 only.",
                    "## Final Selection\nSelected Step 0.",
                    "## Verification\nIndependent final verification required.",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def final_tool_arguments(self) -> list[str]:
        rebuild = CAD_BUILD_ENTRYPOINT
        registry = self.root / "cad-tool-registry.json"
        registry_value = {
            "schema": "mesh-to-cad.tool-registry/2",
            "rebuild": {
                "id": "cad.canonical-build/1",
                "entrypoint": str(rebuild),
                "entrypoint_sha256": _sha(rebuild.read_bytes()),
            },
            "geometry": {
                "id": "mesh-compare.voxblame/1",
                "entrypoint": str(MESH_COMPARE_ENTRYPOINT),
                "entrypoint_sha256": _sha(MESH_COMPARE_ENTRYPOINT.read_bytes()),
            },
        }
        registry_value["identity_sha256"] = _identity(
            "mesh-to-cad.tool-registry/2", registry_value
        )
        _write_json(registry, registry_value)
        return [
            "--rebuild-entrypoint",
            str(rebuild),
            "--geometry-entrypoint",
            str(MESH_COMPARE_ENTRYPOINT),
            "--tool-registry",
            str(registry),
        ]

    def test_tool_registry_requires_matching_absolute_entrypoint(self) -> None:
        arguments = self.final_tool_arguments()
        registry = Path(arguments[-1])
        value = json.loads(registry.read_text(encoding="utf-8"))
        value["rebuild"]["entrypoint"] = "skills/cad/scripts/canonical-build"
        value_without_identity = dict(value)
        value_without_identity.pop("identity_sha256")
        value["identity_sha256"] = _identity(
            "mesh-to-cad.tool-registry/2", value_without_identity
        )
        _write_json(registry, value)

        load_registry = self.cli.finalize_workspace.__globals__["_load_tool_registry"]
        with self.assertRaises(self.cli.WorkspaceError) as raised:
            load_registry(
                registry,
                rebuild_entrypoint=CAD_BUILD_ENTRYPOINT,
                geometry_entrypoint=MESH_COMPARE_ENTRYPOINT,
            )
        self.assertEqual("untrusted_tool", raised.exception.classification)

    def test_tool_registry_rejects_double_slash_entrypoint(self) -> None:
        arguments = self.final_tool_arguments()
        registry = Path(arguments[-1])
        value = json.loads(registry.read_text(encoding="utf-8"))
        value["rebuild"]["entrypoint"] = "//" + value["rebuild"][
            "entrypoint"
        ].lstrip("/")
        value_without_identity = dict(value)
        value_without_identity.pop("identity_sha256")
        value["identity_sha256"] = _identity(
            "mesh-to-cad.tool-registry/2", value_without_identity
        )
        _write_json(registry, value)

        load_registry = self.cli.finalize_workspace.__globals__["_load_tool_registry"]
        with self.assertRaises(self.cli.WorkspaceError) as raised:
            load_registry(
                registry,
                rebuild_entrypoint=CAD_BUILD_ENTRYPOINT,
                geometry_entrypoint=MESH_COMPARE_ENTRYPOINT,
            )
        self.assertEqual("untrusted_tool", raised.exception.classification)

    def test_tool_registry_reads_legacy_v1_without_entrypoint(self) -> None:
        registry = self.root / "legacy-tool-registry.json"
        value = {
            "schema": "mesh-to-cad.tool-registry/1",
            "rebuild": {
                "id": "cad.canonical-build/1",
                "entrypoint_sha256": _sha(CAD_BUILD_ENTRYPOINT.read_bytes()),
            },
            "geometry": {
                "id": "mesh-compare.voxblame/1",
                "entrypoint_sha256": _sha(MESH_COMPARE_ENTRYPOINT.read_bytes()),
            },
        }
        value["identity_sha256"] = _identity(
            "mesh-to-cad.tool-registry/1", value
        )
        _write_json(registry, value)

        load_registry = self.cli.finalize_workspace.__globals__["_load_tool_registry"]
        loaded = load_registry(
            registry,
            rebuild_entrypoint=CAD_BUILD_ENTRYPOINT,
            geometry_entrypoint=MESH_COMPARE_ENTRYPOINT,
        )
        self.assertEqual("mesh-to-cad.tool-registry/1", loaded["schema"])

    @staticmethod
    def write_provider_free_final_preview(
        workspace: Path,
        candidate: Path,
        *,
        selected_step: int,
        selected_summary: Path,
        output: Path,
        entrypoint: Path,
    ) -> None:
        del entrypoint
        output.mkdir()
        image = b"provider-free-final-preview\n"
        (output / "preview.png").write_bytes(image)
        experiment = json.loads((workspace / "experiment.json").read_text(encoding="utf-8"))
        preview = {
            "schema": "voxblame.preview/1",
            "render_variant": "final",
            "selected_step": selected_step,
            "selected_summary_sha256": _sha(selected_summary.read_bytes()),
            "reference": {
                "canonical_reference_sha256": experiment["canonical_reference_sha256"]
            },
            "profile": {"experiment_identity": experiment["preview_profile"]},
            "candidate": {"mesh_sha256": _sha(candidate.read_bytes())},
            "image": {"path": "preview.png", "sha256": _sha(image)},
        }
        preview["preview_identity_sha256"] = _identity("voxblame.preview/1", preview)
        _write_json(output / "preview.json", preview)

    def execute_final_case(
        self,
        *,
        prepared: Path,
        candidate: Path,
        candidate_mesh_relative: str,
        accepted: bool,
    ) -> dict:
        status, _initialized, stderr = self.invoke(
            "init", "--workspace", str(self.workspace), "--prepared", str(prepared)
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate_mesh = candidate / candidate_mesh_relative
        measured = subprocess.run(
            (
                sys.executable,
                str(MESH_COMPARE_PATH),
                "voxblame-measure",
                str(candidate_mesh),
                "--reference",
                str(self.workspace / "input"),
                "--output",
                str(self.workspace / "voxblame"),
                "--step",
                "0",
            ),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, measured.returncode, measured.stderr)
        measurement = self.workspace / "voxblame/steps/000000/summary.json"
        objective = json.loads(measurement.read_text(encoding="utf-8"))[
            "objective_facts"
        ]
        self.assertIs(accepted, all(objective.values()))
        candidate_sha = _sha(candidate_mesh.read_bytes())
        status, _published, stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            candidate_mesh_relative,
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("candidate-preview", candidate_sha)),
        )
        self.assertEqual(0, status, stderr)
        with mock.patch.dict(
            self.cli.finalize_workspace.__globals__,
            {"_run_final_preview": self.write_provider_free_final_preview},
        ):
            status, finalized, stderr = self.invoke(
                "finalize",
                "--workspace",
                str(self.workspace),
                "--selection",
                str(self.final_selection(accepted=accepted)),
                "--notes",
                str(self.final_notes()),
                *self.final_tool_arguments(),
            )
        self.assertEqual(0, status, stderr)
        self.assertIs(accepted, finalized["final"]["accepted"])
        return finalized["final"]

    def test_init_and_step_zero_publish_cross_checked_immutable_state(self) -> None:
        status, payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)

        self.assertTrue(payload["ok"])
        self.assertEqual("mesh-to-cad.workspace/1", payload["workspace"]["schema"])

        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate("candidate-0", b"candidate zero")
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        preview = self.preview("preview-0", candidate_sha)

        status, published, stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(preview),
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(published["ok"])
        step = json.loads(
            (self.workspace / "steps/000000/step.json").read_text(encoding="utf-8")
        )
        self.assertEqual("mesh-to-cad.measured-step/1", step["schema"])
        self.assertEqual(0, step["step"])
        self.assertIsNone(step["parent_step"])
        self.assertEqual(candidate_sha, step["candidate_mesh_sha256"])
        self.assertFalse(step["accepted"])

        status, validation, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(validation["valid"])
        self.assertEqual([0], [item["step"] for item in validation["graph"]["steps"]])
        self.assertFalse(any(path.name.startswith(".tmp-") for path in self.workspace.iterdir()))

        tracked = set(self.git("ls-files").splitlines())
        self.assertIn("steps/000000/candidate/artifacts/model.glb", tracked)
        self.assertNotIn("work/candidate-0/artifacts/model.glb", tracked)
        self.assertEqual(
            "steps/000000/candidate/artifacts/model.glb: filter: lfs",
            self.git("check-attr", "filter", "--", "steps/000000/candidate/artifacts/model.glb"),
        )
        message = self.git("log", "-1", "--format=%B")
        self.assertIn("Workspace-Step: 0", message)
        self.assertIn(f"Candidate-SHA256: {candidate_sha}", message)

    def test_step_zero_rejects_recipe_input_outside_candidate_bundle(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate("candidate-external-recipe", b"candidate")
        source = candidate / "source/model.py"
        _write_json(
            candidate / "artifacts/rebuild.json",
            {
                "schema": "mesh-to-cad.rebuild-recipe/1",
                "executable": "cad.canonical-build/1",
                "workingDirectory": ".",
                "network": "forbidden",
                "ambientInputs": "forbidden",
                "inputs": [
                    {
                        "id": "source",
                        "role": "canonical-cad-source",
                        "path": "work/attempts/000004/candidate/source/model.py",
                        "sha256": _sha(source.read_bytes()),
                    }
                ],
            },
        )
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )

        status, rejected, _stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("preview-external-recipe", candidate_sha)),
        )

        self.assertEqual(2, status)
        self.assertEqual("invalid_workspace_path", rejected["error"]["classification"])
        self.assertEqual("$.rebuild_recipe.inputs[0].path", rejected["error"]["path"])

    def test_step_zero_revalidates_recipe_inputs_after_candidate_copy(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate("candidate-copy-race", b"candidate")
        source = candidate / "source/model.py"
        _write_json(
            candidate / "artifacts/rebuild.json",
            {
                "schema": "mesh-to-cad.rebuild-recipe/1",
                "executable": "cad.canonical-build/1",
                "workingDirectory": ".",
                "network": "forbidden",
                "ambientInputs": "forbidden",
                "inputs": [
                    {
                        "id": "source",
                        "role": "canonical-cad-source",
                        "path": "source/model.py",
                        "sha256": _sha(source.read_bytes()),
                    }
                ],
            },
        )
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        original_copytree = shutil.copytree

        def mutate_before_copy(src, dst, *args, **kwargs):
            if Path(src).resolve() == candidate.resolve():
                source.write_text("# changed during publication\n", encoding="utf-8")
            return original_copytree(src, dst, *args, **kwargs)

        workspace_core = self.cli.publish_step_zero.__globals__
        with mock.patch.object(workspace_core["shutil"], "copytree", side_effect=mutate_before_copy):
            status, rejected, _stderr = self.invoke(
                "publish-step-zero",
                "--workspace",
                str(self.workspace),
                "--attempt",
                str(attempt["attempt"]["attempt"]),
                "--candidate",
                str(candidate),
                "--candidate-mesh",
                "artifacts/model.glb",
                "--measurement",
                str(measurement),
                "--preview",
                str(self.preview("preview-copy-race", candidate_sha)),
            )

        self.assertEqual(2, status)
        self.assertEqual("source_mutation", rejected["error"]["classification"])
        self.assertFalse((self.workspace / "steps/000000").exists())
        self.assertFalse(any((self.workspace / "work").glob(".tmp-step-zero-*")))
        status, _workspace_status, stderr = self.invoke(
            "status", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)

    def test_finalize_rebuilds_verifies_and_atomically_publishes_accepted_cad(
        self,
    ) -> None:
        prepared, candidate = self.canonical_cad_flow()
        status, _initialized, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(prepared),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate_mesh = candidate / "built/measurement.glb"
        measured = subprocess.run(
            (
                sys.executable,
                str(MESH_COMPARE_PATH),
                "voxblame-measure",
                str(candidate_mesh),
                "--reference",
                str(self.workspace / "input"),
                "--output",
                str(self.workspace / "voxblame"),
                "--step",
                "0",
            ),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(0, measured.returncode, measured.stderr)
        measurement = self.workspace / "voxblame/steps/000000/summary.json"
        measurement_document = json.loads(measurement.read_text(encoding="utf-8"))
        self.assertTrue(measurement_document["objective_facts"]["global_depth_8_zero"])
        candidate_sha = _sha(candidate_mesh.read_bytes())
        status, _published, stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "built/measurement.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("canonical-cad-preview", candidate_sha)),
        )
        self.assertEqual(0, status, stderr)

        with mock.patch.dict(
            self.cli.finalize_workspace.__globals__,
            {"_run_final_preview": self.write_provider_free_final_preview},
        ):
            status, finalized, stderr = self.invoke(
                "finalize",
                "--workspace",
                str(self.workspace),
                "--selection",
                str(self.final_selection(accepted=True)),
                "--notes",
                str(self.final_notes()),
                *self.final_tool_arguments(),
            )

        self.assertEqual(0, status, stderr)
        self.assertTrue(finalized["final"]["accepted"])
        final = self.workspace / "final"
        for relative in (
            "source",
            "artifacts",
            "build.json",
            "rebuild.json",
            "measurement.json",
            "verification.json",
            "preview.png",
            "preview.json",
            "selection.json",
            "tool-registry.json",
            "manifest.json",
        ):
            self.assertTrue((final / relative).exists(), relative)
        self.assertEqual(
            (self.workspace / "steps/000000/measurement.json").read_bytes(),
            (final / "measurement.json").read_bytes(),
        )
        verification = json.loads(
            (final / "verification.json").read_text(encoding="utf-8")
        )
        self.assertTrue(verification["verified"])
        self.assertTrue(all(verification["equality"].values()))
        self.assertEqual([], list((self.workspace / "work").iterdir()))
        self.assertIn("Final-Selected-Step: 0", self.git("log", "-1", "--format=%B"))
        self.assertTrue((final / "source/source/model.py").is_file())
        self.assertTrue((final / "source/config.txt").is_file())

        build_path = final / "build.json"
        build = json.loads(build_path.read_text(encoding="utf-8"))
        build["derivation"] = build["derivation"][1:]
        _write_json(build_path, build)
        manifest_path = final / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["build_sha256"] = _sha(build_path.read_bytes())
        for item in manifest["files"]:
            if item["path"] == "build.json":
                item["sha256"] = manifest["build_sha256"]
                item["size_bytes"] = build_path.stat().st_size
        manifest_without_identity = dict(manifest)
        manifest_without_identity.pop("identity_sha256")
        manifest["identity_sha256"] = _identity(
            "mesh-to-cad.final-delivery/1", manifest_without_identity
        )
        _write_json(manifest_path, manifest)
        status, rejected, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual(
            "build_provenance_conflict", rejected["error"]["classification"]
        )

    def test_finalize_covers_unaccepted_cad_recipe(self) -> None:
        prepared, candidate = self.canonical_cad_flow(accepted=False)

        final = self.execute_final_case(
            prepared=prepared,
            candidate=candidate,
            candidate_mesh_relative="built/measurement.glb",
            accepted=False,
        )

        self.assertNotIn("route", final)
        self.assertEqual("cycle_limit", final["stop_reason"])

    def test_runner_accepts_and_reviewer_audits_real_synthetic_delivery(
        self,
    ) -> None:
        runner_spec = importlib.util.spec_from_file_location(
            "synthetic_pilot_runner",
            PILOT_RUNNER_PATH,
        )
        self.assertIsNotNone(runner_spec)
        self.assertIsNotNone(runner_spec.loader)
        runner = importlib.util.module_from_spec(runner_spec)
        runner_spec.loader.exec_module(runner)
        runner_temporary = tempfile.TemporaryDirectory(
            prefix="workspace-cli-runner-test-"
        )
        self.addCleanup(runner_temporary.cleanup)
        runner_root = Path(runner_temporary.name)
        self.workspace = runner_root / "outputs/runner-driven-experiment"
        workload = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--synthetic-runner-workload",
            str(self.workspace),
        ]

        class SyntheticTap:
            def __init__(self) -> None:
                self.stopped = False

            def poll(self) -> int | None:
                return 0 if self.stopped else None

            def send_signal(self, _signum: int) -> None:
                self.stopped = True

            def wait(self, timeout: float | None = None) -> int:
                del timeout
                self.stopped = True
                return 0

            terminate = send_signal
            kill = send_signal

        def start_synthetic_tap(_tap_bin, exp_dir, _environ, _retry_url):
            run_dir = exp_dir / "run"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / ".claude-tap.log").write_text(
                "listening on http://127.0.0.1:43123\n",
                encoding="utf-8",
            )
            with sqlite3.connect(run_dir / "traces.sqlite3") as connection:
                connection.execute(
                    "CREATE TABLE sessions "
                    "(id TEXT, status TEXT, record_count INTEGER, started_at TEXT)"
                )
                connection.execute(
                    "INSERT INTO sessions VALUES (?,?,?,?)",
                    ("synthetic-session", "complete", 1, "2026-08-10T00:00:00Z"),
                )
            return SyntheticTap()

        def export_synthetic_trace(_tap_bin, exp_dir, _session_id, _environ):
            (exp_dir / "run/trace.html").write_text(
                "<html>synthetic trace</html>\n",
                encoding="utf-8",
            )

        class SyntheticBrowserRuntime:
            @classmethod
            def create(cls, *args, **kwargs):
                return cls()

            def __init__(self) -> None:
                self.capability_dir = Path("/tmp/synthetic-browser-runtime")
                self.mcp_url = "http://127.0.0.1:43111/mcp"

            def start(self):
                return None

            def preflight(self):
                return None

            def preflight_mcp(self):
                return None

            def stop(self):
                return None

            def poll_failed(self):
                return False

        with (
            mock.patch.object(runner, "BrowserRuntimeJob", SyntheticBrowserRuntime),
            mock.patch.object(runner, "resolve_tap", return_value="synthetic-tap"),
            mock.patch.object(
                runner,
                "build_bwrap_argv",
                return_value=workload,
            ),
            mock.patch.object(
                runner,
                "build_sandbox_environment",
                return_value=dict(os.environ),
            ),
            mock.patch.object(
                runner,
                "REPO_ROOT",
                runner_root,
            ),
            mock.patch.object(
                runner,
                "start_tap",
                side_effect=start_synthetic_tap,
            ),
            mock.patch.object(
                runner,
                "export_html",
                side_effect=export_synthetic_trace,
            ),
        ):
            runner_status = runner.run_pilot(
                self.workspace,
                [
                    REPO_ROOT
                    / "models/agent-runtime/cup_cup_033/input/cup_cup_033.ply"
                ],
                workload,
                dict(os.environ),
            )
        reviewed = subprocess.run(
            (
                sys.executable,
                str(PILOT_REVIEW_PATH),
                str(self.workspace),
                "--workspace-helper",
                str(CLI_PATH.parent),
            ),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(0, runner_status)
        self.assertEqual(0, reviewed.returncode, reviewed.stdout + reviewed.stderr)
        self.assertFalse((self.workspace / "reviews").exists())
        self.assertTrue((self.workspace / "run/rollout.jsonl").is_file())
        self.assertTrue((self.workspace / "run/traces.sqlite3").is_file())
        self.assertTrue((self.workspace / "run/trace.html").is_file())
        review = json.loads(
            (self.workspace / "run/review/review.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pass", review["verdicts"]["runner_completion"])
        self.assertEqual("pass", review["verdicts"]["workspace_protocol"])
        self.assertEqual("accepted", review["verdicts"]["reconstruction_quality"])
        self.assertIn(
            json.loads(
                (self.workspace / "final/manifest.json").read_text(encoding="utf-8")
            )["identity_sha256"],
            {
                node.get("identity_sha256")
                for node in review["graph"]["nodes"]
                if node["type"] == "final_delivery"
            },
        )

    def test_finalize_conflict_publishes_no_final_delivery(self) -> None:
        self.publish_initial_flow()
        selection_path = self.final_selection(accepted=False)
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        selection["preview"]["evidence_conflict"] = True
        selection["preview"]["conflict_details"] = "Material silhouette contradiction."
        _write_json(selection_path, selection)

        status, rejected, _stderr = self.invoke(
            "finalize",
            "--workspace",
            str(self.workspace),
            "--selection",
            str(selection_path),
            "--notes",
            str(self.final_notes()),
            *self.final_tool_arguments(),
        )

        self.assertEqual(2, status)
        self.assertEqual(
            "agent_semantic_conflict", rejected["error"]["classification"]
        )
        self.assertFalse((self.workspace / "final").exists())
        self.assertFalse(list((self.workspace / "work").glob(".tmp-final-*")))

    def test_recover_rolls_back_interrupted_uncommitted_final_delivery(self) -> None:
        prepared, candidate = self.canonical_cad_flow()
        final = self.execute_final_case(
            prepared=prepared,
            candidate=candidate,
            candidate_mesh_relative="built/measurement.glb",
            accepted=True,
        )
        prior_index = subprocess.run(
            ("git", "show", "HEAD^:step_index.json"),
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            check=True,
        ).stdout
        prior_notes = subprocess.run(
            ("git", "show", "HEAD^:notes.md"),
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        transaction = self.workspace / "work/.tmp-final-interrupted"
        transaction.mkdir()
        (transaction / "previous-step-index.json").write_bytes(prior_index)
        if prior_notes.returncode == 0:
            (transaction / "previous-notes.md").write_bytes(prior_notes.stdout)
        _write_json(
            transaction / "transaction.json",
            {
                "schema": "mesh-to-cad.transaction/1",
                "kind": "final_delivery",
                "selected_step": 0,
                "final_delivery_sha256": final["identity_sha256"],
                "previous_notes_exists": prior_notes.returncode == 0,
            },
        )
        subprocess.run(
            ("git", "reset", "--mixed", "HEAD^"),
            cwd=self.workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )

        status, invalid, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("incomplete_transaction", invalid["error"]["classification"])
        status, recovered, stderr = self.invoke(
            "recover", "--workspace", str(self.workspace)
        )

        self.assertEqual(0, status, stderr)
        self.assertEqual(["rolled_back"], recovered["recovery"]["recovered_final"])
        self.assertFalse((self.workspace / "final").exists())
        self.assertFalse(transaction.exists())
        self.assertEqual("", self.git("status", "--short"))

    @unittest.skipUnless(os.name == "posix", "process-group cancellation requires POSIX")
    def test_timed_out_command_terminates_descendant_processes(self) -> None:
        self.publish_initial_flow()
        plan = self.repair_plan("timeout-plan", from_step=0)
        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)

        child_pid = self.root / "child.pid"
        child_script = self.root / "timeout-child.py"
        child_script.write_text(
            """import os
from pathlib import Path
import signal
import sys
import time

pid_path = Path(sys.argv[1])
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pid_path.write_text(str(os.getpid()), encoding="utf-8")
time.sleep(60)
""",
            encoding="utf-8",
        )
        parent_script = self.root / "timeout-parent.py"
        parent_script.write_text(
            """import subprocess
import sys
import time

subprocess.Popen(
    [sys.executable, sys.argv[1], sys.argv[2]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(60)
""",
            encoding="utf-8",
        )

        stdout = io.StringIO()
        command_stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(command_stderr):
            command_status = self.cli.main(
                [
                    "run",
                    "--workspace",
                    str(self.workspace),
                    "--attempt",
                    str(started["attempt"]["attempt"]),
                    "--phase",
                    "preview",
                    "--timeout-seconds",
                    "1",
                    "--",
                    sys.executable,
                    str(parent_script),
                    str(child_script),
                    str(child_pid),
                ]
            )

        command = json.loads(stdout.getvalue())
        self.assertEqual(124, command_status, command_stderr.getvalue())
        self.assertTrue(command["command"]["timed_out"])
        self.assertTrue(child_pid.is_file())
        pid = int(child_pid.read_text(encoding="utf-8"))

        def kill_surviving_child() -> None:
            try:
                os.kill(pid, 9)
            except ProcessLookupError:
                pass

        self.addCleanup(kill_surviving_child)
        deadline = time.monotonic() + 2
        while True:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            if time.monotonic() >= deadline:
                self.fail("the SIGTERM-ignoring command descendant is still running")
            time.sleep(0.05)

    def test_failed_attempt_is_auditable_but_does_not_consume_cycle_budget(self) -> None:
        self.publish_initial_flow()
        plan = self.repair_plan("repair-plan", from_step=0)

        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt = started["attempt"]["attempt"]
        status, rejected, _stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(2, status)
        self.assertEqual("workspace_conflict", rejected["error"]["classification"])
        stdout = io.StringIO()
        command_stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(command_stderr):
            command_status = self.cli.main(
                [
                    "run",
                    "--workspace",
                    str(self.workspace),
                    "--attempt",
                    str(attempt),
                    "--phase",
                    "preview",
                    "--",
                    sys.executable,
                    "-c",
                    "import sys; print('x' * 70000); print('boom', file=sys.stderr); sys.exit(7)",
                    "--token",
                    "TOP-SECRET-VALUE",
                ]
            )
        command = json.loads(stdout.getvalue())
        self.assertEqual(7, command_status, command_stderr.getvalue())
        self.assertEqual(7, command["command"]["exit_code"])
        self.assertTrue(command["command"]["stdout"]["truncated"])

        status, recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt),
            "--result",
            "tool_failure",
            "--classification",
            "synthetic_build_failed",
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual("tool_failure", recorded["attempt"]["result"])
        stored_command = json.loads(
            (
                self.workspace
                / f"attempts/{attempt:06d}/commands/000001/command.json"
            ).read_text(encoding="utf-8")
        )
        self.assertNotIn("TOP-SECRET-VALUE", json.dumps(stored_command))
        self.assertIn("<redacted>", stored_command["argv"])

        status, payload, stderr = self.invoke(
            "status", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        objective = payload["status"]
        self.assertEqual(0, objective["completed_cycles"])
        self.assertEqual(5, objective["remaining_cycles"])
        self.assertEqual(1, objective["tool_failures"])

        status, second, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        second_id = second["attempt"]["attempt"]
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            self.assertEqual(
                9,
                self.cli.main(
                    [
                        "run",
                        "--workspace",
                        str(self.workspace),
                        "--attempt",
                        str(second_id),
                        "--phase",
                        "measure",
                        "--",
                        sys.executable,
                        "-c",
                        "raise SystemExit(9)",
                    ]
                ),
            )
        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(second_id),
            "--result",
            "tool_failure",
            "--classification",
            "synthetic_measure_failed",
        )
        self.assertEqual(0, status, stderr)

        status, rejected, _stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(2, status)
        self.assertEqual("budget_violation", rejected["error"]["classification"])

    def test_failing_command_cannot_publish_a_successful_step(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt = started["attempt"]["attempt"]

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            command_status = self.cli.main(
                [
                    "run",
                    "--workspace",
                    str(self.workspace),
                    "--attempt",
                    str(attempt),
                    "--phase",
                    "preview",
                    "--",
                    sys.executable,
                    "-c",
                    "raise SystemExit(7)",
                ]
            )
        self.assertEqual(7, command_status)

        candidate, candidate_sha = self.candidate("failed-candidate", b"failed candidate")
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        preview = self.preview("failed-preview", candidate_sha)
        status, rejected, _stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(preview),
        )
        self.assertEqual(2, status)
        self.assertEqual("invalid_attempt", rejected["error"]["classification"])

        shutil.rmtree(self.workspace / "voxblame")
        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt),
            "--result",
            "tool_failure",
            "--classification",
            "synthetic_build_failed",
        )
        self.assertEqual(0, status, stderr)
        status, payload, stderr = self.invoke(
            "status", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual(1, payload["status"]["tool_failures"])

    def test_failing_command_cannot_publish_a_successful_cycle(self) -> None:
        self.publish_initial_flow()
        plan = self.repair_plan("failed-cycle-plan", from_step=0)
        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt = started["attempt"]["attempt"]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            command_status = self.cli.main(
                [
                    "run",
                    "--workspace",
                    str(self.workspace),
                    "--attempt",
                    str(attempt),
                    "--phase",
                    "preview",
                    "--",
                    sys.executable,
                    "-c",
                    "raise SystemExit(8)",
                ]
            )
        self.assertEqual(8, command_status)

        candidate, candidate_sha = self.candidate("failed-cycle-candidate", b"cycle")
        measurement = self.measurement(
            step=1,
            compare_to=0,
            candidate_sha=candidate_sha,
            observable_sha="a" * 64,
            accepted=False,
        )
        status, rejected, _stderr = self.invoke(
            "publish-cycle",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("failed-cycle-preview", candidate_sha)),
            "--region-diff",
            str(
                self.region_diff(
                    "failed-cycle-diff",
                    plan=plan,
                    from_step=0,
                    to_step=1,
                    before_observable="9" * 64,
                    after_observable="a" * 64,
                )
            ),
            "--assessment",
            str(self.assessment("failed-cycle", from_step=0, to_step=1)),
            "--source-changes",
            str(self.source_changes("failed-cycle", from_step=0, to_step=1)),
        )
        self.assertEqual(2, status)
        self.assertEqual("invalid_attempt", rejected["error"]["classification"])

    def test_synthetic_end_to_end_publishes_a_branched_immutable_graph(self) -> None:
        self.publish_initial_flow()
        plan_one = self.repair_plan("cycle-one-plan", from_step=0)

        status, failed, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan_one),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        failed_id = failed["attempt"]["attempt"]
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            self.assertEqual(
                6,
                self.cli.main(
                    [
                        "run",
                        "--workspace",
                        str(self.workspace),
                        "--attempt",
                        str(failed_id),
                        "--phase",
                        "preview",
                        "--",
                        sys.executable,
                        "-c",
                        "raise SystemExit(6)",
                    ]
                ),
            )
        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(failed_id),
            "--result",
            "tool_failure",
            "--classification",
            "first_build_failed",
        )
        self.assertEqual(0, status, stderr)

        status, successful, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan_one),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate_one, candidate_one_sha = self.candidate(
            "candidate-1", b"candidate one"
        )
        measurement_one = self.measurement(
            step=1,
            compare_to=0,
            candidate_sha=candidate_one_sha,
            observable_sha="b" * 64,
            accepted=False,
        )
        preview_one = self.preview("preview-1", candidate_one_sha)
        diff_one = self.region_diff(
            "diff-one",
            plan=plan_one,
            from_step=0,
            to_step=1,
            before_observable="9" * 64,
            after_observable="b" * 64,
        )
        status, cycle_one, stderr = self.invoke(
            "publish-cycle",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(successful["attempt"]["attempt"]),
            "--candidate",
            str(candidate_one),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement_one),
            "--preview",
            str(preview_one),
            "--region-diff",
            str(diff_one),
            "--assessment",
            str(self.assessment("one", from_step=0, to_step=1)),
            "--source-changes",
            str(self.source_changes("one", from_step=0, to_step=1)),
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual([failed_id, successful["attempt"]["attempt"]], cycle_one["cycle"]["attempt_ids"])

        plan_two = self.repair_plan("branch-plan", from_step=0)
        status, branch_attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan_two),
            "--intended-step",
            "2",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate_two, candidate_two_sha = self.candidate(
            "candidate-2", b"bytes-different geometric no-op"
        )
        measurement_two = self.measurement(
            step=2,
            compare_to=0,
            candidate_sha=candidate_two_sha,
            observable_sha="9" * 64,
            accepted=False,
            no_op=True,
        )
        preview_two = self.preview("preview-2", candidate_two_sha)
        diff_two = self.region_diff(
            "diff-two",
            plan=plan_two,
            from_step=0,
            to_step=2,
            before_observable="9" * 64,
            after_observable="9" * 64,
        )
        status, cycle_two, stderr = self.invoke(
            "publish-cycle",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(branch_attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate_two),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement_two),
            "--preview",
            str(preview_two),
            "--region-diff",
            str(diff_two),
            "--assessment",
            str(self.assessment("two", from_step=0, to_step=2)),
            "--source-changes",
            str(self.source_changes("two", from_step=0, to_step=2)),
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(cycle_two["cycle"]["no_observable_geometry_change"])

        status, validation, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        graph = validation["graph"]
        self.assertEqual(
            [(0, None), (1, 0), (2, 0)],
            [(item["step"], item["parent_step"]) for item in graph["steps"]],
        )
        self.assertEqual(2, graph["budget"]["completed_cycles"])
        self.assertEqual(3, graph["budget"]["remaining_cycles"])
        self.assertEqual(1, graph["budget"]["tool_failures"])

        (self.workspace / "step_index.json").write_text("{}\n", encoding="utf-8")
        status, invalid, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("derived_index_conflict", invalid["error"]["classification"])
        status, _rebuilt, stderr = self.invoke(
            "rebuild-index", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        status, validation, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual([1, 2], validation["graph"]["heads"])

    def test_interrupted_marker_last_cycle_is_invalid_then_recoverable(self) -> None:
        self.publish_initial_flow()
        published = self.publish_one_cycle()
        transaction = self.workspace / "work/.tmp-cycle-000001-test-recovery"
        transaction.mkdir(parents=True)
        shutil.move(
            str(self.workspace / "cycles/000001"),
            str(transaction / "cycle"),
        )
        _write_json(
            transaction / "transaction.json",
            {
                "schema": "mesh-to-cad.transaction/1",
                "kind": "repair_cycle",
                "cycle": 1,
                "step_identity_sha256": published["step"]["identity_sha256"],
                "cycle_identity_sha256": published["identity_sha256"],
            },
        )

        status, invalid, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("incomplete_transaction", invalid["error"]["classification"])

        status, recovered, stderr = self.invoke(
            "recover", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual([1], recovered["recovery"]["recovered_cycles"])
        self.assertTrue((self.workspace / "cycles/000001/cycle.json").is_file())
        self.assertFalse(transaction.exists())
        status, valid, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(valid["valid"])

    def test_validation_rejects_corruption_unknown_stage_and_legacy_layout(self) -> None:
        self.publish_initial_flow()
        candidate = self.workspace / "steps/000000/candidate/artifacts/model.glb"
        candidate.write_bytes(b"corrupt")
        status, corrupt, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("corrupt_workspace", corrupt["error"]["classification"])
        candidate.write_bytes(b"candidate zero")
        target = candidate.resolve()
        read_bytes = Path.read_bytes

        def fail_target(path: Path) -> bytes:
            if path.resolve() == target:
                raise OSError("synthetic unreadable artifact")
            return read_bytes(path)

        with mock.patch.object(Path, "read_bytes", fail_target):
            status, unreadable, _stderr = self.invoke(
                "validate", "--workspace", str(self.workspace)
            )
        self.assertEqual(2, status)
        self.assertEqual("corrupt_workspace", unreadable["error"]["classification"])

        unknown_stage = self.workspace / "steps/.tmp-unknown"
        unknown_stage.mkdir()
        status, staged, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("incomplete_transaction", staged["error"]["classification"])
        unknown_stage.rmdir()

        (self.workspace / "run").mkdir(exist_ok=True)
        (self.workspace / "run/fake-authority.json").write_text(
            '{"accepted":true}\n', encoding="utf-8"
        )
        (self.workspace / "run/playwright/.tmp-browser-cache").mkdir(
            parents=True
        )
        status, valid, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(valid["valid"])

        nested_stage = self.workspace / "work/attempts/000001/.tmp-command"
        nested_stage.mkdir(parents=True)
        status, staged, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("incomplete_transaction", staged["error"]["classification"])
        nested_stage.rmdir()

        legacy = self.root / "legacy"
        legacy.mkdir()
        subprocess.run(("git", "init"), cwd=legacy, check=True, stdout=subprocess.PIPE)
        (legacy / "compare_metrics.json").write_text("{}\n", encoding="utf-8")
        status, rejected, _stderr = self.invoke(
            "validate", "--workspace", str(legacy)
        )
        self.assertEqual(2, status)
        self.assertEqual(
            "unsupported_legacy_workspace",
            rejected["error"]["classification"],
        )

    def test_init_refuses_unrelated_pre_staged_paths(self) -> None:
        (self.workspace / "unrelated.txt").write_text("user-owned\n", encoding="utf-8")
        self.git("add", "--", "unrelated.txt")

        status, rejected, _stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )

        self.assertEqual(2, status)
        self.assertEqual("git_scope_violation", rejected["error"]["classification"])
        self.assertEqual("unrelated.txt", self.git("diff", "--cached", "--name-only"))
        self.assertFalse((self.workspace / "workspace.json").exists())

    def test_cli_and_formal_evidence_fail_closed(self) -> None:
        status, help_payload, stderr = self.invoke("--help")
        self.assertEqual(0, status, stderr)
        self.assertTrue(help_payload["ok"])
        self.assertEqual("mesh-to-cad-workspace", help_payload["help"]["program"])

        status, payload, _stderr = self.invoke("validate")
        self.assertEqual(2, status)
        self.assertEqual("invalid_arguments", payload["error"]["classification"])

        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt_id = attempt["attempt"]["attempt"]

        status, missing, _stderr = self.invoke(
            "run",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt_id),
            "--phase",
            "preview",
            "--",
            "/definitely/missing/workspace-command",
        )
        self.assertEqual(127, status)
        self.assertEqual(127, missing["command"]["exit_code"])

        status, inline, _stderr = self.invoke(
            "run",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt_id),
            "--phase",
            "upload",
            "--",
            sys.executable,
            "-c",
            "pass",
            "--header=Authorization: Bearer INLINE-SECRET",
            "-HAuthorization: Bearer SECOND-SECRET",
        )
        self.assertEqual(0, status)
        serialized = json.dumps(inline["command"]["argv"])
        self.assertNotIn("INLINE-SECRET", serialized)
        self.assertNotIn("SECOND-SECRET", serialized)

        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt_id),
            "--result",
            "tool_failure",
            "--classification",
            "command_launch_failed",
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt_id = attempt["attempt"]["attempt"]

        candidate, candidate_sha = self.candidate("bad-evidence", b"bad evidence")
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="e" * 64,
            accepted=False,
        )
        summary = json.loads(measurement.read_text(encoding="utf-8"))
        summary["objective_facts"]["global_depth_8_zero"] = True
        _write_json(measurement, summary)
        status, rejected, _stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt_id),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("bad-preview", candidate_sha)),
        )
        self.assertEqual(2, status)
        self.assertEqual("identity_conflict", rejected["error"]["classification"])
        self.assertFalse((self.workspace / "steps/000000").exists())

    def test_step_zero_candidate_mesh_ignores_nested_cadgen_glb(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate(
            "candidate-with-cadgen-cache",
            b"authoritative measurement mesh",
        )
        cadgen_cache = candidate / "artifacts/__cadgen__"
        cadgen_cache.mkdir()
        (cadgen_cache / "topology.glb").write_bytes(b"derived viewer mesh")
        measurement = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="9" * 64,
            accepted=False,
        )
        preview = self.preview("preview-with-cadgen-cache", candidate_sha)

        status, published, stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(preview),
        )

        self.assertEqual(0, status, stderr)
        self.assertTrue(published["ok"])
        status, validation, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(validation["valid"])

    def test_validation_binds_setup_and_current_git_commit_identities(self) -> None:
        self.publish_initial_flow()
        reference = self.workspace / "input/reference.ply"
        original = reference.read_bytes()
        reference.write_bytes(b"tampered reference")
        status, corrupt, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("corrupt_workspace", corrupt["error"]["classification"])
        reference.write_bytes(original)

        self.git("commit", "--amend", "-m", "step 0: stripped identity trailers")
        status, missing, _stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(2, status)
        self.assertEqual("missing_git_evidence", missing["error"]["classification"])

    def test_cycle_attempts_are_scoped_to_parent_and_frozen_plan(self) -> None:
        self.publish_initial_flow()
        self.publish_one_cycle()

        unrelated_plan = self.repair_plan("unrelated-branch", from_step=0)
        status, unrelated, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(unrelated_plan),
            "--intended-step",
            "2",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        unrelated_id = unrelated["attempt"]["attempt"]
        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(unrelated_id),
            "--result",
            "strategy_changed",
            "--classification",
            "branch_abandoned",
        )
        self.assertEqual(0, status, stderr)

        plan = self.repair_plan("parent-one", from_step=1)
        status, successful, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "2",
            "--from-step",
            "1",
        )
        self.assertEqual(0, status, stderr)
        successful_id = successful["attempt"]["attempt"]
        candidate, candidate_sha = self.candidate("parent-one-candidate", b"parent one")
        measurement = self.measurement(
            step=2,
            compare_to=1,
            candidate_sha=candidate_sha,
            observable_sha="c" * 64,
            accepted=False,
        )
        status, published, stderr = self.invoke(
            "publish-cycle",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(successful_id),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("parent-one-preview", candidate_sha)),
            "--region-diff",
            str(
                self.region_diff(
                    "parent-one-diff",
                    plan=plan,
                    from_step=1,
                    to_step=2,
                    before_observable="b" * 64,
                    after_observable="c" * 64,
                )
            ),
            "--assessment",
            str(self.assessment("parent-one", from_step=1, to_step=2)),
            "--source-changes",
            str(self.source_changes("parent-one", from_step=1, to_step=2)),
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual([successful_id], published["cycle"]["attempt_ids"])
        status, valid, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(valid["valid"])

    def test_failed_attempt_commit_interruption_is_recoverable(self) -> None:
        self.publish_initial_flow()
        plan = self.repair_plan("attempt-recovery", from_step=0)
        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        attempt = started["attempt"]["attempt"]
        hook = self.workspace / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        status, failed, _stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(attempt),
            "--result",
            "strategy_changed",
            "--classification",
            "transient_commit_failure",
        )
        self.assertEqual(2, status)
        self.assertEqual("git_operation_failed", failed["error"]["classification"])
        self.assertTrue((self.workspace / f"attempts/{attempt:06d}").is_dir())
        self.assertTrue(list((self.workspace / "work").glob(".tmp-attempt-*")))

        hook.unlink()
        status, recovered, stderr = self.invoke(
            "recover", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertEqual([attempt], recovered["recovery"]["recovered_attempts"])
        status, valid, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(valid["valid"])

    def _seed_prepared_repair(self) -> tuple[object, int]:
        """Set up Step 0 published + one active repair Attempt bound to it."""

        self.publish_initial_flow()
        plan = self.repair_plan("seed-plan", from_step=0)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "1",
            "--from-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        helper = _load_workspace_helper()
        return helper, attempt["attempt"]["attempt"]

    def test_seed_rejects_unknown_parent_step_index(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        destination = self.root / "ext-work-unknown"
        destination.mkdir()
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt_id,
                from_step=99,
                destination=destination,
            )
        self.assertEqual("invalid_attempt", ctx.exception.classification)
        self.assertFalse((destination / "source").exists())

    def test_seed_rejects_from_step_mismatch_against_active_attempt(self) -> None:
        # A second published cycle so a step index other than the parent
        # actually exists and yet still fails the parent-binding check.
        self.publish_initial_flow()
        self.publish_one_cycle()
        plan = self.repair_plan("seed-plan-mismatch", from_step=1)
        status, attempt, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(plan),
            "--intended-step",
            "2",
            "--from-step",
            "1",
        )
        self.assertEqual(0, status, stderr)
        helper = _load_workspace_helper()
        destination = self.root / "ext-work-mismatch"
        destination.mkdir()
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt["attempt"]["attempt"],
                from_step=0,
                destination=destination,
            )
        self.assertEqual("invalid_attempt", ctx.exception.classification)
        self.assertFalse((destination / "source").exists())

    def test_seed_rejects_destination_that_is_a_symlink(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        real = self.root / "real-empty"
        real.mkdir()
        destination = self.root / "ext-work-symlink"
        os.symlink(real, destination)
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt_id,
                from_step=0,
                destination=destination,
            )
        self.assertEqual("invalid_workspace_path", ctx.exception.classification)
        self.assertFalse((real / "source").exists())

    def test_seed_rejects_destination_that_is_not_empty(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        destination = self.root / "ext-work-nonempty"
        destination.mkdir()
        (destination / "stowaway.txt").write_text("stow\n", encoding="utf-8")
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt_id,
                from_step=0,
                destination=destination,
            )
        self.assertEqual("invalid_workspace_path", ctx.exception.classification)
        self.assertFalse((destination / "source").exists())
        self.assertTrue((destination / "stowaway.txt").exists())

    def test_seed_rejects_destination_inside_the_workspace(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        destination = self.workspace / "work" / "internal-seed"
        destination.mkdir(parents=True)
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt_id,
                from_step=0,
                destination=destination,
            )
        self.assertEqual("invalid_workspace_path", ctx.exception.classification)
        self.assertFalse((destination / "source").exists())

    def test_seed_rejects_parent_authority_source_that_is_a_symlink(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        authority_source = (
            self.workspace / "steps" / "000000" / "candidate" / "source"
        )
        replacement = self.workspace / "steps" / "000000" / "candidate" / "source.real"
        shutil.move(str(authority_source), str(replacement))
        os.symlink(replacement, authority_source)
        destination = self.root / "ext-work-authority-symlink"
        destination.mkdir()
        with self.assertRaises(helper.WorkspaceError) as ctx:
            helper.seed_repair_source_from_parent_step(
                self.workspace,
                attempt=attempt_id,
                from_step=0,
                destination=destination,
            )
        self.assertEqual("invalid_workspace_path", ctx.exception.classification)
        self.assertFalse((destination / "source").exists())

    def test_seed_happy_path_copies_committed_parent_source_into_empty_destination(
        self,
    ) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        destination = self.root / "ext-work-happy"
        destination.mkdir()
        helper.seed_repair_source_from_parent_step(
            self.workspace,
            attempt=attempt_id,
            from_step=0,
            destination=destination,
        )
        seeded = destination / "source" / "model.py"
        self.assertTrue(seeded.is_file())
        expected = (
            self.workspace / "steps" / "000000" / "candidate" / "source" / "model.py"
        ).read_bytes()
        self.assertEqual(expected, seeded.read_bytes())

    def test_seed_rollback_leaves_no_partial_work_tree_when_copy_fails(self) -> None:
        helper, attempt_id = self._seed_prepared_repair()
        destination = self.root / "ext-work-rollback"
        destination.mkdir()
        with mock.patch.object(
            helper,
            "_copy_agent_tree",
            side_effect=helper.WorkspaceError(
                "invalid_workspace_path", "simulated descriptor-safe failure"
            ),
        ):
            with self.assertRaises(helper.WorkspaceError):
                helper.seed_repair_source_from_parent_step(
                    self.workspace,
                    attempt=attempt_id,
                    from_step=0,
                    destination=destination,
                )
        self.assertFalse((destination / "source").exists())
        with os.scandir(destination) as entries:
            self.assertEqual([], list(entries))

    def test_voxblame_placement_is_checked_before_step_authority_rename(self) -> None:
        status, _payload, stderr = self.invoke(
            "init",
            "--workspace",
            str(self.workspace),
            "--prepared",
            str(self.prepared_setup()),
        )
        self.assertEqual(0, status, stderr)
        status, started, stderr = self.invoke(
            "begin-attempt",
            "--workspace",
            str(self.workspace),
            "--plan",
            str(self.initial_plan()),
            "--intended-step",
            "0",
        )
        self.assertEqual(0, status, stderr)
        candidate, candidate_sha = self.candidate("misplaced", b"misplaced")
        summary = self.measurement(
            step=0,
            compare_to=None,
            candidate_sha=candidate_sha,
            observable_sha="f" * 64,
            accepted=False,
        )
        misplaced = self.workspace / "formal-summary.json"
        shutil.copy2(summary, misplaced)

        status, rejected, _stderr = self.invoke(
            "publish-step-zero",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(started["attempt"]["attempt"]),
            "--candidate",
            str(candidate),
            "--candidate-mesh",
            "artifacts/model.glb",
            "--measurement",
            str(misplaced),
            "--preview",
            str(self.preview("misplaced-preview", candidate_sha)),
        )
        self.assertEqual(2, status)
        self.assertEqual("invalid_workspace_path", rejected["error"]["classification"])
        self.assertFalse((self.workspace / "steps/000000").exists())
        self.assertFalse(list((self.workspace / "work").glob(".tmp-step-zero-*")))


def _run_synthetic_runner_workload(workspace: Path) -> int:
    case = WorkspaceCliTests(
        methodName="test_runner_accepts_and_reviewer_audits_real_synthetic_delivery"
    )
    case.root = workspace.parent / "synthetic-workload"
    case.root.mkdir(parents=True, exist_ok=True)
    case.workspace = workspace
    case.cli = _load_cli()
    case.reference_sha = "1" * 64
    case.profile_sha = "2" * 64
    prepared, candidate = case.canonical_cad_flow()
    case.execute_final_case(
        prepared=prepared,
        candidate=candidate,
        candidate_mesh_relative="built/measurement.glb",
        accepted=True,
    )
    rollout = workspace / "run/.codex-home/sessions/a/b/c/rollout-synthetic.jsonl"
    rollout.parent.mkdir(parents=True, exist_ok=True)
    rollout.write_text("{}\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--synthetic-runner-workload":
        raise SystemExit(_run_synthetic_runner_workload(Path(sys.argv[2])))
    unittest.main()
