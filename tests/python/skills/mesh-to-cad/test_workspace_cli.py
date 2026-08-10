"""Public mesh-to-cad Workspace helper contract tests."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_PATH = (
    REPO_ROOT
    / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace/cli.py"
)
MESH_COMPARE_PATH = REPO_ROOT / "skills/mesh-compare/scripts/mesh-compare"
CAD_BUILD_PATH = REPO_ROOT / "skills/cad/scripts/canonical-build"
IMPLICIT_BUILD_PATH = REPO_ROOT / "skills/implicit-cad/scripts/canonical-build.mjs"
MESH_COMPARE_ENTRYPOINT = MESH_COMPARE_PATH / "cli.py"
CAD_BUILD_ENTRYPOINT = CAD_BUILD_PATH / "__main__.py"
PREVIEW_PROFILE_PATH = (
    REPO_ROOT
    / "packages/meshshot/src/meshshot/profiles/cadena_residual_eight_view_v1.json"
)
PILOT_RUNNER_PATH = REPO_ROOT / "scripts/pilot/runner.py"
PILOT_REVIEW_PATH = REPO_ROOT / ".claude/skills/pilot-review/scripts/review.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("mesh_to_cad_workspace_cli", CLI_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
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
        _write_json(
            prepared / "setup/route.json",
            {"schema": "mesh-to-cad.route/1", "route": "cad"},
        )
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
                "route": "cad",
            },
        )
        return prepared

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
        (root / "source/model.py").write_text("# synthetic source\n", encoding="utf-8")
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
            built = subprocess.run(
                (
                    sys.executable,
                    str(CAD_BUILD_PATH),
                    "build",
                    "--source",
                    "source/model.py",
                    "--input",
                    "config.txt",
                    "--output-dir",
                    "built",
                ),
                cwd=candidate,
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
        _write_json(
            prepared / "setup/route.json",
            {"schema": "mesh-to-cad.route/1", "route": "cad"},
        )
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
                "route": "cad",
            },
        )
        return prepared, candidate

    def canonical_implicit_flow(self, *, accepted: bool) -> tuple[Path, Path]:
        candidate = self.workspace / "work" / "canonical-implicit"
        (candidate / "source").mkdir(parents=True)
        source = candidate / "source/model.implicit.js"

        def write_source(half_x: float) -> None:
            source.write_text(
                "\n".join(
                    (
                        "export default {",
                        '  schema: "implicit.js/0.1.0",',
                        '  name: "canonical thin box",',
                        '  units: "unitless",',
                        "  bounds: [[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],",
                        f"  glsl: `float sdf(vec3 p) {{ vec3 q = abs(p) - vec3({half_x}, 0.005, 0.005); return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0); }}`",
                        "};",
                        "",
                    )
                ),
                encoding="utf-8",
            )

        def build() -> None:
            built = subprocess.run(
                (
                    "node",
                    str(IMPLICIT_BUILD_PATH),
                    "--source",
                    "source/model.implicit.js",
                    "--output-dir",
                    "built",
                    "--json",
                ),
                cwd=candidate,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(0, built.returncode, built.stderr)

        write_source(0.5)
        build()
        candidate_mesh = candidate / "built/artifacts/model.glb"
        prepared = self.root / "prepared-canonical-implicit"
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
            write_source(0.4)
            build()
        input_document = json.loads(
            (reference / "input.json").read_text(encoding="utf-8")
        )
        self.reference_sha = input_document["canonical_reference_sha256"]
        self.profile_sha = _sha(PREVIEW_PROFILE_PATH.read_bytes())
        _write_json(
            prepared / "setup/route.json",
            {"schema": "mesh-to-cad.route/1", "route": "implicit"},
        )
        _write_json(
            prepared / "experiment.json",
            {
                "schema": "mesh-to-cad.experiment/1",
                "workspace_id": "canonical-implicit-final",
                "coordinate_contract": "trellis2_canonical/1",
                "canonical_reference_sha256": self.reference_sha,
                "preview_profile": {
                    "name": "cadena_residual_eight_view/1",
                    "sha256": self.profile_sha,
                },
                "route": "implicit",
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
                    "## Input and Route\nSynthetic canonical CAD route.",
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
        route = json.loads(
            (self.workspace / "setup/route.json").read_text(encoding="utf-8")
        )["route"]
        rebuild = CAD_BUILD_ENTRYPOINT if route == "cad" else IMPLICIT_BUILD_PATH
        registry = self.root / f"{route}-tool-registry.json"
        registry_value = {
            "schema": "mesh-to-cad.tool-registry/1",
            "rebuild": {
                "id": (
                    "cad.canonical-build/1"
                    if route == "cad"
                    else "implicit-cad.canonical-build/1"
                ),
                "entrypoint_sha256": _sha(rebuild.read_bytes()),
            },
            "geometry": {
                "id": "mesh-compare.voxblame/1",
                "entrypoint_sha256": _sha(MESH_COMPARE_ENTRYPOINT.read_bytes()),
            },
        }
        registry_value["identity_sha256"] = _identity(
            "mesh-to-cad.tool-registry/1", registry_value
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
            str(self.preview("route-preview", candidate_sha)),
        )
        self.assertEqual(0, status, stderr)
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

    def test_finalize_rebuilds_verifies_without_upgrading_unaccepted_implicit(
        self,
    ) -> None:
        prepared, candidate = self.canonical_implicit_flow(accepted=False)
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
        candidate_mesh = candidate / "built/artifacts/model.glb"
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
        self.assertFalse(measurement_document["objective_facts"]["global_depth_8_zero"])
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
            "built/artifacts/model.glb",
            "--measurement",
            str(measurement),
            "--preview",
            str(self.preview("canonical-implicit-preview", candidate_sha)),
        )
        self.assertEqual(0, status, stderr)

        status, finalized, stderr = self.invoke(
            "finalize",
            "--workspace",
            str(self.workspace),
            "--selection",
            str(self.final_selection(accepted=False)),
            "--notes",
            str(self.final_notes()),
            *self.final_tool_arguments(),
        )

        self.assertEqual(0, status, stderr)
        self.assertFalse(finalized["final"]["accepted"])
        selection = json.loads(
            (self.workspace / "final/selection.json").read_text(encoding="utf-8")
        )
        self.assertFalse(selection["accepted"])
        self.assertEqual("cycle_limit", selection["stop_reason"])
        self.assertTrue(
            json.loads(
                (self.workspace / "final/verification.json").read_text(encoding="utf-8")
            )["verified"]
        )

    def test_finalize_covers_unaccepted_cad_recipe(self) -> None:
        prepared, candidate = self.canonical_cad_flow(accepted=False)

        final = self.execute_final_case(
            prepared=prepared,
            candidate=candidate,
            candidate_mesh_relative="built/measurement.glb",
            accepted=False,
        )

        self.assertEqual("cad", final["route"])
        self.assertEqual("cycle_limit", final["stop_reason"])

    def test_finalize_covers_accepted_implicit_recipe(self) -> None:
        prepared, candidate = self.canonical_implicit_flow(accepted=True)

        final = self.execute_final_case(
            prepared=prepared,
            candidate=candidate,
            candidate_mesh_relative="built/artifacts/model.glb",
            accepted=True,
        )

        self.assertEqual("implicit", final["route"])
        self.assertEqual("acceptance_satisfied", final["stop_reason"])

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
        self.workspace = self.root / "runner-driven-experiment"
        runner.prepare_exp(self.workspace)
        prepared, candidate = self.canonical_cad_flow()
        final = self.execute_final_case(
            prepared=prepared,
            candidate=candidate,
            candidate_mesh_relative="built/measurement.glb",
            accepted=True,
        )
        rollout = (
            self.workspace
            / "run/.codex-upper/sessions/a/b/c/rollout-synthetic.jsonl"
        )
        rollout.parent.mkdir(parents=True)
        rollout.write_text("{}\n", encoding="utf-8")

        runner_status = runner.finalize_pilot(self.workspace, 0, {})
        reviewed = subprocess.run(
            (sys.executable, str(PILOT_REVIEW_PATH), str(self.workspace)),
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(0, runner_status)
        self.assertEqual(0, reviewed.returncode, reviewed.stderr)
        self.assertFalse((self.workspace / "reviews").exists())
        self.assertTrue((self.workspace / "run/rollout.jsonl").is_file())
        review = json.loads(
            (self.workspace / "review.json").read_text(encoding="utf-8")
        )
        self.assertEqual("pass", review["verdicts"]["runner_completion"])
        self.assertEqual("pass", review["verdicts"]["workspace_protocol"])
        self.assertEqual("accepted", review["verdicts"]["reconstruction_quality"])
        self.assertIn(
            final["identity_sha256"],
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
                    "build",
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

        status, third, stderr = self.invoke(
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
        third_id = third["attempt"]["attempt"]
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            self.assertEqual(
                10,
                self.cli.main(
                    [
                        "run",
                        "--workspace",
                        str(self.workspace),
                        "--attempt",
                        str(third_id),
                        "--phase",
                        "preview",
                        "--",
                        sys.executable,
                        "-c",
                        "raise SystemExit(10)",
                    ]
                ),
            )
        status, rejected, _stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(third_id),
            "--result",
            "tool_failure",
            "--classification",
            "third_tool_failure",
        )
        self.assertEqual(2, status)
        self.assertEqual("budget_violation", rejected["error"]["classification"])
        status, _recorded, stderr = self.invoke(
            "record-attempt",
            "--workspace",
            str(self.workspace),
            "--attempt",
            str(third_id),
            "--result",
            "strategy_changed",
            "--classification",
            "tool_path_abandoned",
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
                        "build",
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
        status, valid, stderr = self.invoke(
            "validate", "--workspace", str(self.workspace)
        )
        self.assertEqual(0, status, stderr)
        self.assertTrue(valid["valid"])

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
            "build",
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


if __name__ == "__main__":
    unittest.main()
