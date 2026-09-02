"""Provider-free, model-free Workspace repair-chain integration gate.

This scenario drives the production W1/W3/W4 assembly with a deterministic
triangle fixture.  It deliberately uses the same candidate build and evidence
providers as the pilot runner; no fake renderer or direct Workspace calls are
used for agent intents.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Mapping, Sequence

from scripts.pilot import plugin_deployment, provider_free_installed_plugin as installed, runner
from scripts.pilot.agent_surface_bridge import AgentSurfaceBridge
from scripts.pilot.cvm_job import protocol
from scripts.pilot.workspace_supervisor import SupervisorError, WorkspaceSupervisor

SCENARIO = "workspace-repair-chain"
EXHAUSTION_SCENARIO = "workspace-repair-chain-exhaustion"
EVIDENCE_SCHEMA_V1 = "text-to-cad.provider-free-workspace-repair-chain-evidence/1"
EVIDENCE_SCHEMA_V2 = "text-to-cad.provider-free-workspace-repair-chain-evidence/2"
EVIDENCE_SCHEMA_V3 = "text-to-cad.provider-free-workspace-repair-chain-evidence/3"
EVIDENCE_SCHEMA_V4 = "text-to-cad.provider-free-workspace-repair-chain-evidence/4"
EVIDENCE_SCHEMA_V5 = "text-to-cad.provider-free-workspace-repair-chain-evidence/5"
EVIDENCE_SCHEMA_V6 = "text-to-cad.provider-free-workspace-repair-chain-evidence/6"
EVIDENCE_SCHEMA_V7 = "text-to-cad.provider-free-workspace-repair-chain-evidence/7"
EVIDENCE_SCHEMA_V8 = "text-to-cad.provider-free-workspace-repair-chain-evidence/8"
EVIDENCE_SCHEMA_V9 = "text-to-cad.provider-free-workspace-repair-chain-evidence/9"
EVIDENCE_SCHEMA_V10 = "text-to-cad.provider-free-workspace-repair-chain-evidence/10"
EVIDENCE_SCHEMA_V11 = "text-to-cad.provider-free-workspace-repair-chain-evidence/11"
EVIDENCE_SCHEMA_V12 = "text-to-cad.provider-free-workspace-repair-chain-evidence/12"
EVIDENCE_SCHEMA_V13 = "text-to-cad.provider-free-workspace-repair-chain-evidence/13"
EVIDENCE_SCHEMA_V14 = "text-to-cad.provider-free-workspace-repair-chain-evidence/14"
EVIDENCE_SCHEMA_V15 = "text-to-cad.provider-free-workspace-repair-chain-evidence/15"
MANIFEST_SCHEMA = "text-to-cad.provider-free-artifact-manifest/1"
MAX_EVIDENCE_BYTES = 96 * 1024
MAX_MANIFEST_BYTES = 8 * 1024
STEP_ZERO_WIDTH = 2 / 3
REPAIR_A_WIDTH = 9 / 10
REPAIR_B_WIDTH = 1 / 8
REPAIR_C_WIDTH = 3 / 4
SPEC_FINAL_BYTES = b'{"revision":"updated","semantic_regions":[]}\n'
MCP_PERMITTED_INTENTS = frozenset({
    "workspace_status", "start_attempt", "run_candidate_tool", "submit_step_zero",
    "submit_repair", "evaluate_repair_draft", "abandon_repair_attempt", "inspect_formal_preview", "inspect_repair_targets",
    "observe_target_section", "select_and_finalize", "observe_reference",
})


class ProviderFreeError(RuntimeError):
    """The deterministic repair-chain contract was not satisfied."""


def _frontier(decision_facts: Mapping[str, Any]) -> dict[str, int]:
    summary = decision_facts.get("residual_summary")
    frontier = summary.get("repair_frontier") if isinstance(summary, dict) else None
    targets = decision_facts.get("repair_targets")
    items = targets.get("items") if isinstance(targets, dict) else None
    if not isinstance(frontier, dict) or set(frontier) != {"active_depth", "missing_surface_count", "excess_surface_count", "surface_error_count", "surface_error_rate"} or not isinstance(items, list):
        raise ProviderFreeError("invalid decision frontier")
    if not isinstance(frontier["active_depth"], int) or frontier["active_depth"] <= 0:
        raise ProviderFreeError("invalid active depth")
    if any(type(frontier[key]) is not int or frontier[key] < 0 for key in ("missing_surface_count", "excess_surface_count", "surface_error_count")) or not items:
        raise ProviderFreeError("invalid frontier counts")
    return {"active_depth": frontier["active_depth"], "missing_surface_error_count": frontier["missing_surface_count"], "excess_surface_error_count": frontier["excess_surface_count"], "surface_error_count": frontier["surface_error_count"], "target_count": len(items)}


def _frontier_order(value: Mapping[str, int]) -> tuple[int, int]:
    return (value["active_depth"], -value["surface_error_count"])


def authority_identity(receipt: plugin_deployment.DeploymentReceipt) -> dict[str, str]:
    return installed.authority_identity(receipt)


def assert_current_authority(
    record: Mapping[str, Any], host_home: Path
) -> plugin_deployment.DeploymentReceipt:
    return installed.assert_current_authority(
        {**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO},
        host_home,
    )


def build_runner_env(environ: Mapping[str, str]) -> dict[str, str]:
    return installed.build_runner_env(environ)


def expected_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    job = record.get("job")
    if not isinstance(job, str) or "/" not in job or record.get("exp_dir") != f"outputs/{job}":
        raise ProviderFreeError("invalid repair-chain job binding")
    base = installed.expected_identity({**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO})
    if record.get("provider_free") is not True or record.get("scenario") not in {SCENARIO, EXHAUSTION_SCENARIO} or record.get("object") != record.get("scenario") or record.get("token_slot") is not None:
        raise ProviderFreeError("invalid repair-chain identity")
    return {**base, "scenario": record["scenario"]}


def artifact_paths(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    exp = repo_root / str(record["exp_dir"])
    return exp, exp / "provider-free-evidence.json", exp / "artifact_manifest.json"


def authoring_python_from_evidence(
    repo_root: Path, record: Mapping[str, Any]
) -> Path | None:
    _, evidence_path, _ = artifact_paths(repo_root, record)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence.get("schema") in {
            EVIDENCE_SCHEMA_V11,
            EVIDENCE_SCHEMA_V12,
            EVIDENCE_SCHEMA_V13,
            EVIDENCE_SCHEMA_V14,
            EVIDENCE_SCHEMA_V15,
        }:
            return None
        identity = evidence["authoring_probe"]["runtime"]["identity"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ProviderFreeError("v6 authoring runtime identity is unavailable") from exc
    if (
        not isinstance(identity, str)
        or not identity
        or Path(identity).name != identity
        or identity in {".", ".."}
    ):
        raise ProviderFreeError("v6 authoring runtime identity is invalid")
    python = repo_root / ".cache/mesh-to-cad-agent-runtime" / identity / "bin/python"
    if not python.is_file():
        raise ProviderFreeError("v6 authoring observer runtime is unavailable")
    return python


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _fixture(path: Path) -> None:
    import trimesh
    mesh = trimesh.creation.box(extents=(12.0, 6.0, 3.0))
    for _ in range(3):
        mesh = mesh.subdivide()
    path.parent.mkdir(parents=True, exist_ok=True)
    mesh.export(path, file_type="ply")


def _cycle_fixture(path: Path) -> None:
    import trimesh

    path.parent.mkdir(parents=True, exist_ok=True)
    trimesh.creation.box(extents=(12.0, 6.0, 3.0)).export(path, file_type="ply")


def _source(path: Path, width: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"from build123d import Box\n\ndef gen_step():\n    return Box({width}, 0.5, 0.25)\n", encoding="utf-8")


def _exterior_source(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from build123d import Box\n\ndef gen_step():\n    return Box(8, 6, 3)\n",
        encoding="utf-8",
    )


BAD_AUTHORING_SOURCE = """\
from build123d import Align, Box, Compound, Face, Location, Polygon, extrude


def gen_step():
    body = Box(1.0, 0.2, 0.2, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    tail = Box(0.3, 0.15, 0.1, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Location((0, 0.55, 0)))
    empty_plate = extrude(Face(Polygon((-0.4, -0.25), (0.4, -0.25), (0.25, 0.25), (-0.25, 0.25), align=None)), amount=0.04).moved(Location((0, 0, 0.35)))
    return Compound([body, empty_plate, tail])
"""

SAFE_AUTHORING_SOURCE = """\
from build123d import Align, Box, Compound, Location, Polygon, extrude
from pathlib import Path


def _value(name):
    return float(Path(f"source/{name}.txt").read_text().strip())


def gen_step():
    chord = _value("chord")
    wing_z = _value("wing-z")
    body = Box(1.0, 0.2, 0.2, align=(Align.CENTER, Align.CENTER, Align.CENTER))
    tail = Box(0.3, 0.15, 0.1, align=(Align.CENTER, Align.CENTER, Align.CENTER)).moved(Location((0, 0.55, 0)))
    wing = extrude(Polygon((-chord / 2, -0.25), (chord / 2, -0.25), (chord / 3, 0.25), (-chord / 3, 0.25), align=None), amount=0.04).moved(Location((0, 0, wing_z)))
    return Compound([body, wing, tail])
"""

AUTHORING_STEP_OBSERVER = """\
import json
import os
import sys
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer

reader = STEPControl_Reader()
if reader.ReadFile(os.fspath(sys.argv[1])) != IFSelect_RetDone or not reader.TransferRoots():
    raise SystemExit("unreadable STEP")
shape = reader.OneShape()
explorer = TopExp_Explorer(shape, TopAbs_SOLID)
solid_count = 0
while explorer.More():
    solid_count += 1
    explorer.Next()
box = Bnd_Box()
BRepBndLib.Add_s(shape, box)
xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
print(json.dumps({"solid_count": solid_count, "bounds": {"min": [xmin, ymin, zmin], "max": [xmax, ymax, zmax]}}, separators=(",", ":")))
"""


def _observe_authoring_step(
    step: Path,
    *,
    python: Path,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    completed = subprocess.run(
        [os.fspath(python), "-c", AUTHORING_STEP_OBSERVER, os.fspath(step)],
        env=build_runner_env(environ),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=60,
    )
    try:
        observation = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("authoring probe STEP observation failed") from exc
    if (
        completed.returncode != 0
        or not isinstance(observation, dict)
        or set(observation) != {"solid_count", "bounds"}
        or type(observation["solid_count"]) is not int
        or observation["solid_count"] < 0
        or not isinstance(observation["bounds"], dict)
        or set(observation["bounds"]) != {"min", "max"}
        or any(
            not isinstance(values, list)
            or len(values) != 3
            or any(type(value) not in {int, float} or not math.isfinite(value) for value in values)
            for values in observation["bounds"].values()
        )
    ):
        raise ProviderFreeError("authoring probe STEP observation is invalid")
    return observation


def _run_authoring_probe(
    exp_dir: Path,
    *,
    runtime: Path,
    rebuild_entrypoint: Path,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    root = exp_dir / "run/authoring-probe"
    cases = {
        "bad_control": (BAD_AUTHORING_SOURCE, {}),
        "safe_a": (SAFE_AUTHORING_SOURCE, {"chord": "1.2\n", "wing-z": "0.35\n"}),
        "safe_b": (SAFE_AUTHORING_SOURCE, {"chord": "1.6\n", "wing-z": "0.5\n"}),
    }
    records: dict[str, Any] = {}
    for name, (source, inputs) in cases.items():
        case_root = root / name
        source_root = case_root / "source"
        source_root.mkdir(parents=True)
        (source_root / "model.py").write_text(source, encoding="utf-8")
        argv = [
            str(runtime / "bin/python"),
            str(rebuild_entrypoint),
            "build",
            "--source",
            "source/model.py",
            "--output-dir",
            "output",
        ]
        for input_name, value in inputs.items():
            (source_root / f"{input_name}.txt").write_text(value, encoding="utf-8")
            argv.extend(("--input", f"source/{input_name}.txt"))
        completed = subprocess.run(
            argv,
            cwd=case_root,
            env=build_runner_env(environ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=120,
        )
        if completed.returncode != 0:
            raise ProviderFreeError(f"authoring probe build failed: {name}")
        record = {
            "source": (source_root / "model.py").relative_to(exp_dir).as_posix(),
            "inputs": {
                input_name: (source_root / f"{input_name}.txt").relative_to(exp_dir).as_posix()
                for input_name in inputs
            },
            "step": (case_root / "output/canonical.step").relative_to(exp_dir).as_posix(),
            "build": (case_root / "output/build.json").relative_to(exp_dir).as_posix(),
        }
        record["observed"] = _read_authoring_artifacts(
            exp_dir,
            record,
            observer_python=runtime / "bin/python",
            environ=environ,
        )
        records[name] = record
    return {
        "schema": "text-to-cad.candidate-authoring-probe/1",
        "runtime": {"kind": "candidate-runtime", "identity": runtime.name},
        "cases": records,
    }


def _read_authoring_artifacts(
    exp_dir: Path,
    record: Mapping[str, Any],
    *,
    observer_python: Path,
    environ: Mapping[str, str],
) -> dict[str, Any]:
    step = exp_dir / str(record["step"])
    build_path = exp_dir / str(record["build"])
    geometry = _observe_authoring_step(
        step,
        python=observer_python,
        environ=environ,
    )
    try:
        build = json.loads(build_path.read_text(encoding="utf-8"))
        direct = build["dependencies"]["direct"]
        build123d_version = next(
            item["version"] for item in direct if item.get("name") == "build123d"
        )
        adapter = build["adapter"]
        recipe_path = build["recipe"]["path"]
        if recipe_path != "rebuild.json":
            raise ProviderFreeError("authoring probe recipe path is invalid")
        recipe = json.loads((build_path.parent / recipe_path).read_text(encoding="utf-8"))
        build_inputs = [
            {key: item[key] for key in ("id", "role", "path")}
            for item in build["files"]
            if item.get("role") in {"canonical-cad-source", "declared-source-input"}
        ]
        recipe_inputs = [
            {key: item[key] for key in ("id", "role", "path")}
            for item in recipe["inputs"]
        ]
    except (OSError, json.JSONDecodeError, KeyError, StopIteration, TypeError) as exc:
        raise ProviderFreeError("authoring probe build manifest is invalid") from exc
    return {
        **geometry,
        "build_version": {
            "adapter_id": adapter.get("id"),
            "adapter_version": adapter.get("version"),
            "build123d": build123d_version,
        },
        "recipe_binding": {
            "schema": recipe.get("schema"),
            "executable": recipe.get("executable"),
            "entrypoint": recipe.get("entrypoint"),
            "build_inputs": build_inputs,
            "recipe_inputs": recipe_inputs,
            "argv_template": recipe.get("argvTemplate"),
            "placeholders": recipe.get("placeholders"),
        },
    }


def _spec(path: Path, region_id: str, bounds: Mapping[str, Any]) -> bytes:
    value = {"components": [{"id": region_id, "bounds_canonical": bounds}], "features": [], "relations": []}
    _json(path, value)
    return path.read_bytes()


def _surface_call(socket_path: Path, request: Mapping[str, Any]) -> dict[str, Any]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(os.fspath(socket_path))
        with conn.makefile("rwb") as stream:
            stream.write(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
            stream.flush()
            response = json.loads(stream.readline())
    if not isinstance(response, dict):
        raise ProviderFreeError("Agent Surface bridge returned invalid response")
    return response


class _FailedObservationWriteBridge(AgentSurfaceBridge):
    """Coordinate one real socket write failure after a valid observation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fail_observation_write = False
        self.write_started = threading.Event()
        self.client_closed = threading.Event()
        self.write_failed = threading.Event()
        self.valid_observation_frame = False

    def arm_observation_write_failure(self) -> None:
        self._fail_observation_write = True
        self.write_started.clear()
        self.client_closed.clear()
        self.write_failed.clear()
        self.valid_observation_frame = False

    def _write(self, stream: Any, value: dict[str, Any]) -> None:
        response = value.get("response") if value.get("ok") is True else None
        should_fail = (
            self._fail_observation_write
            and isinstance(response, dict)
            and response.get("intent") == "observe_target_section"
        )
        if not should_fail:
            super()._write(stream, value)
            return
        self._fail_observation_write = False
        result = response.get("result")
        self.valid_observation_frame = (
            response.get("schema") == "mesh-to-cad.agent-response/7"
            and isinstance(result, dict)
            and result.get("schema")
            == "mesh-to-cad.target-section-observation/3"
        )
        self.write_started.set()
        if not self.client_closed.wait(timeout=10):
            raise ProviderFreeError("failed-write client did not disconnect")
        try:
            super()._write(stream, value)
        except OSError:
            self.write_failed.set()
            raise


def _fail_observation_response_write(
    bridge: _FailedObservationWriteBridge,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    bridge.arm_observation_write_failure()
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.connect(os.fspath(bridge.socket_path))
        conn.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        if not bridge.write_started.wait(timeout=10):
            raise ProviderFreeError("valid observation did not reach response write")
        conn.shutdown(socket.SHUT_RDWR)
    finally:
        conn.close()
        bridge.client_closed.set()
    if not bridge.write_failed.wait(timeout=10):
        raise ProviderFreeError("observation response write did not fail")
    if not bridge.valid_observation_frame:
        raise ProviderFreeError("failed write did not follow a valid observation response")
    return {
        "schema": "text-to-cad.target-section-failed-write-evidence/1",
        "valid_observation_response": True,
        "response_write_failed": True,
    }


class _FailedSubmitWriteBridge(AgentSurfaceBridge):
    """Coordinate one lost submit response after W4 cached publication."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._fail_submit_write = False
        self.write_started = threading.Event()
        self.client_closed = threading.Event()
        self.write_failed = threading.Event()

    def arm_submit_write_failure(self) -> None:
        self._fail_submit_write = True
        self.write_started.clear()
        self.client_closed.clear()
        self.write_failed.clear()

    def _write(self, stream: Any, value: dict[str, Any]) -> None:
        response = value.get("response") if value.get("ok") is True else None
        if not (
            self._fail_submit_write
            and isinstance(response, dict)
            and response.get("schema") == "mesh-to-cad.agent-response/7"
            and response.get("intent") == "submit_repair"
            and isinstance(response.get("result"), dict)
            and response["result"].get("state") == "published"
        ):
            super()._write(stream, value)
            return
        self._fail_submit_write = False
        self.write_started.set()
        if not self.client_closed.wait(timeout=10):
            raise ProviderFreeError("lost-submit client did not disconnect")
        try:
            super()._write(stream, value)
        except OSError:
            self.write_failed.set()
            raise


def _lose_submit_response(
    bridge: _FailedSubmitWriteBridge, request: Mapping[str, Any]
) -> None:
    bridge.arm_submit_write_failure()
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        conn.connect(os.fspath(bridge.socket_path))
        conn.sendall(json.dumps(request, separators=(",", ":")).encode("utf-8") + b"\n")
        if not bridge.write_started.wait(timeout=10):
            raise ProviderFreeError("published submit did not reach response write")
        conn.shutdown(socket.SHUT_RDWR)
    finally:
        conn.close()
        bridge.client_closed.set()
    if not bridge.write_failed.wait(timeout=10):
        raise ProviderFreeError("submit response write did not fail")


def _workspace_status_via_client(
    client_path: Path, socket_path: Path, workspace_handle: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    request = {
        "schema": "mesh-to-cad.agent-intent/1",
        "intent": "workspace_status",
        "args": {"workspace_handle": workspace_handle},
    }
    invocation = (
        'python3 "$CLIENT_PATH" <<\'JSON\'\n'
        + json.dumps(request, separators=(",", ":"))
        + "\nJSON\n"
    )
    child_env = dict(os.environ)
    child_env.update(
        {
            "CLIENT_PATH": os.fspath(client_path),
            "MESH_TO_CAD_AGENT_SURFACE_SOCKET": os.fspath(socket_path),
        }
    )
    completed = subprocess.run(
        ["bash", "-c", invocation],
        env=child_env,
        text=True,
        capture_output=True,
        check=False,
    )
    try:
        frame = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("fixed client returned invalid JSON") from exc
    _public(frame)
    response = frame.get("response") if frame.get("ok") is True else None
    if (
        completed.returncode != 0
        or not isinstance(response, dict)
        or set(response) != {"schema", "intent", "result"}
        or response.get("schema") != "mesh-to-cad.agent-response/7"
        or response.get("intent") != "workspace_status"
        or not isinstance(response.get("result"), dict)
    ):
        raise ProviderFreeError("fixed client workspace_status invocation failed")
    evidence = {
        "schema": "text-to-cad.client-transport-evidence/1",
        "transport": "stdin_heredoc",
        "exit_status": completed.returncode,
        "response_schema": response["schema"],
        "intent": response["intent"],
        "invalid_request": False,
    }
    return response, evidence


def _run_cycle_exhaustion_probe(
    probe_exp: Path,
    fixture: Path,
    *,
    trusted: Path,
    published_rebuild: Path,
    published_geometry: Path,
    registry: Path,
    sidecar: runner.BrowserRuntimeJob,
    candidate_runtime: Path,
) -> dict[str, Any]:
    """Publish ten real repairs and read the exhausted state through the socket."""

    probe_exp.mkdir(parents=True)
    runner.prepare_exp(probe_exp)
    runner.prepare_and_initialize_workspace(
        probe_exp, fixture, trusted_tools_root=trusted
    )
    candidate_root = probe_exp.parent / f".agent-candidate-cycle-limit-{os.getpid()}"
    socket_root = Path(tempfile.mkdtemp(prefix="ttc-cycle-", dir="/tmp"))
    supervisor: WorkspaceSupervisor | None = None
    bridge: AgentSurfaceBridge | None = None
    try:
        supervisor = WorkspaceSupervisor(
            probe_exp,
            bind_reference=True,
            candidate_root=candidate_root,
            rebuild_entrypoint=published_rebuild,
            geometry_entrypoint=published_geometry,
            tool_registry=registry,
            browser_runtime_capability=sidecar.capability_dir / "runtime.json",
            candidate_runtime=candidate_runtime,
            trusted_tools_root=trusted,
            trusted_product_root=trusted,
            reconstruction_spec=False,
            step_zero_evidence_provider=lambda req: runner.real_step_zero_evidence_provider(
                req,
                capability_path=sidecar.capability_dir / "runtime.json",
                meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
                meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
            ),
            repair_evidence_provider=lambda req: runner.real_repair_evidence_provider(
                req,
                capability_path=sidecar.capability_dir / "runtime.json",
                meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
                meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
            ),
        )
        bridge = AgentSurfaceBridge(
            supervisor.agent_surface(),
            socket_root / "surface.sock",
            trusted_product_root=trusted,
        )
        bridge.start()
        bootstrap = supervisor.agent_bootstrap_contract()
        workspace_handle = bootstrap["workspace_handle"]
        plan_handle = bootstrap["plan_handle"]
        plan_path = candidate_root / "plan.json"
        _json(
            plan_path,
            {
                "schema": "mesh-to-cad.initial-plan/1",
                "summary": "ten-cycle authority exhaustion probe",
            },
        )
        start = _surface_call(
            bridge.socket_path,
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "start_attempt",
                "args": {
                    "workspace_handle": workspace_handle,
                    "plan_handle": plan_handle,
                },
            },
        )["response"]["result"]
        if start.get("state") != "started":
            raise ProviderFreeError("cycle exhaustion Step 0 Attempt did not start")
        _source(candidate_root / "work/source/model.py", REPAIR_B_WIDTH)
        step_zero_run = _surface_call(
            bridge.socket_path,
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "run_candidate_tool",
                "args": {
                    "workspace_handle": workspace_handle,
                    "attempt_handle": start["attempt_handle"],
                    "candidate_handle": start["candidate_handle"],
                    "operation_handle": start["capability_bundle_handle"],
                },
            },
        )["response"]["result"]
        if step_zero_run.get("state") != "completed":
            raise ProviderFreeError("cycle exhaustion Step 0 build failed")
        current = _surface_call(
            bridge.socket_path,
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "submit_step_zero",
                "args": {
                    "workspace_handle": workspace_handle,
                    "attempt_handle": start["attempt_handle"],
                    "candidate_handle": start["candidate_handle"],
                },
            },
        )["response"]["result"]
        if current.get("state") != "published":
            raise ProviderFreeError("cycle exhaustion Step 0 did not publish")
        if current["decision_facts"].get("accepted") is not False:
            raise ProviderFreeError("cycle exhaustion Step 0 unexpectedly accepted")
        for cycle in range(1, 11):
            targets = current["decision_facts"].get("repair_targets", {}).get(
                "items", []
            )
            if not targets:
                raise ProviderFreeError("cycle exhaustion parent has no target")
            target = targets[0]
            parent_step = current["decision_facts"]["step_ordinal"]
            _json(
                plan_path,
                {
                    "schema": "voxblame.repair-batch/1",
                    "from_step": parent_step,
                    "selected_targets": [target],
                    "planned_edits": [
                        {
                            "edit_key": f"cycle-{cycle}",
                            "target_ranks": [target["rank"]],
                            "spec_region_id": "component.primary",
                            "description": "publish one bounded real repair",
                        }
                    ],
                    "rationale": "exercise the ten-cycle Workspace authority",
                    "preview_observation": "the parent remains unaccepted",
                },
            )
            attempt = _surface_call(
                bridge.socket_path,
                {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": "start_attempt",
                    "args": {
                        "workspace_handle": workspace_handle,
                        "plan_handle": plan_handle,
                        "parent_step_handle": current["step_handle"],
                    },
                },
            )["response"]["result"]
            if attempt.get("state") != "started":
                raise ProviderFreeError(
                    f"cycle exhaustion Repair {cycle} Attempt did not start"
                )
            _source(
                candidate_root / "work/source/model.py",
                REPAIR_B_WIDTH + cycle / 100,
            )
            _json(
                candidate_root / "work/assessment.json",
                {
                    "schema": "mesh-to-cad.assessment/1",
                    "from_step": parent_step,
                    "to_step": cycle,
                    "preview_observation": "the bounded candidate remains unaccepted",
                    "summary": "Published one real cycle for authority exhaustion.",
                },
            )
            evaluated = _surface_call(
                bridge.socket_path,
                {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": "evaluate_repair_draft",
                    "args": {
                        "workspace_handle": workspace_handle,
                        "attempt_handle": attempt["attempt_handle"],
                        "candidate_handle": attempt["candidate_handle"],
                        "evaluation_ticket": attempt["evaluation_ticket"],
                    },
                },
            )["response"]["result"]
            draft_handle = evaluated.get("draft_handle")
            if evaluated.get("state") != "evaluated" or not isinstance(draft_handle, str):
                raise ProviderFreeError(
                    f"cycle exhaustion Repair {cycle} draft evaluation failed"
                )
            current = _surface_call(
                bridge.socket_path,
                {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": "submit_repair",
                    "args": {
                        "workspace_handle": workspace_handle,
                        "attempt_handle": attempt["attempt_handle"],
                        "draft_handle": draft_handle,
                    },
                },
            )["response"]["result"]
            if current.get("state") != "published":
                raise ProviderFreeError(
                    "cycle exhaustion Repair "
                    f"{cycle} failed: {current.get('classification')}:"
                    f"{current.get('subtype')}"
                )
            if current["decision_facts"].get("accepted") is not False:
                raise ProviderFreeError("cycle exhaustion repair unexpectedly accepted")
        status, transport = _workspace_status_via_client(
            trusted / ".claude/agent-source-projection/agent-surface/client.py",
            bridge.socket_path,
            workspace_handle,
        )
        authority = supervisor.workspace_api.workspace_status(probe_exp)
        result = status["result"]
        if (
            result.get("budgets")
            != {
                "remaining_cycles": 0,
                "attempts_per_intended_step": 3,
                "tool_failures_per_intended_step": 2,
            }
            or "start_attempt" in result.get("permitted_next_intents", [])
            or authority.get("completed_cycles") != 10
            or authority.get("remaining_cycles") != 0
        ):
            raise ProviderFreeError("ten-cycle authority did not exhaust")
        return {
            "completed_cycles": authority["completed_cycles"],
            "remaining_cycles": result["budgets"]["remaining_cycles"],
            "attempts_per_intended_step": result["budgets"][
                "attempts_per_intended_step"
            ],
            "tool_failures_per_intended_step": result["budgets"][
                "tool_failures_per_intended_step"
            ],
            "start_attempt_permitted": False,
            "transport": transport,
        }
    finally:
        if bridge is not None:
            bridge.stop()
        if supervisor is not None:
            supervisor.close()
        shutil.rmtree(socket_root)


def _mcp_call(socket_path: Path, handle: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.connect(os.fspath(socket_path))
        stream = conn.makefile("rwb")
        def send(value: Mapping[str, Any]) -> dict[str, Any]:
            stream.write((json.dumps(value, separators=(",", ":")) + "\n").encode())
            stream.flush()
            while True:
                line = stream.readline()
                if not line:
                    raise ProviderFreeError("MCP closed")
                value = json.loads(line)
                if value.get("__notification__"):
                    continue
                return value
        initialized = send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "repair-chain", "version": "1"}}})
        stream.write(b'{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}\n'); stream.flush()
        listed = send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        called = send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "inspect_formal_preview", "arguments": {"preview_handle": handle}}})
        return initialized, listed, called


def _inspect(socket_path: Path, handle: str, expected: bytes) -> dict[str, Any]:
    initialized, listed, result = _mcp_call(socket_path, handle)
    if set(initialized) != {"jsonrpc", "id", "result"} or initialized.get("jsonrpc") != "2.0" or initialized.get("id") != 1:
        raise ProviderFreeError("MCP initialize failed")
    init_result = initialized.get("result")
    if not isinstance(init_result, dict) or set(init_result) != {"protocolVersion", "capabilities", "serverInfo"} or init_result.get("protocolVersion") != "2025-06-18":
        raise ProviderFreeError("MCP initialize result is invalid")
    if set(listed) != {"jsonrpc", "id", "result"} or listed.get("jsonrpc") != "2.0" or listed.get("id") != 2:
        raise ProviderFreeError("MCP tools/list response is invalid")
    tools = listed.get("result", {}).get("tools", [])
    if len(tools) != 1 or tools[0].get("name") != "inspect_formal_preview" or tools[0].get("annotations") != {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}:
        raise ProviderFreeError("MCP public inspector contract failed")
    if set(result) != {"jsonrpc", "id", "result"} or result.get("jsonrpc") != "2.0" or result.get("id") != 3:
        raise ProviderFreeError("MCP tools/call response is invalid")
    call_result = result.get("result")
    if not isinstance(call_result, dict) or set(call_result) != {"content", "isError", "structuredContent"} or call_result.get("isError") is not False:
        raise ProviderFreeError("MCP tools/call result is invalid")
    content = call_result["content"]
    if not isinstance(content, list) or len(content) != 2:
        raise ProviderFreeError("MCP preview content is invalid")
    text, image = content
    if not isinstance(text, dict) or set(text) != {"type", "text"} or text.get("type") != "text" or not isinstance(text.get("text"), str):
        raise ProviderFreeError("MCP preview text block is invalid")
    if not isinstance(image, dict) or set(image) != {"type", "data", "mimeType"} or image.get("type") != "image" or image.get("mimeType") != "image/png" or not isinstance(image.get("data"), str):
        raise ProviderFreeError("MCP preview image block is invalid")
    try:
        image_bytes = base64.b64decode(image["data"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProviderFreeError("MCP preview image encoding is invalid") from exc
    if image_bytes != expected:
        raise ProviderFreeError("MCP preview image mismatch")
    structured = call_result["structuredContent"]
    try:
        parsed_text = json.loads(text["text"])
    except json.JSONDecodeError as exc:
        raise ProviderFreeError("MCP preview text is invalid") from exc
    if parsed_text != structured:
        raise ProviderFreeError("MCP preview text does not match structured content")
    if not isinstance(structured, dict) or set(structured) != {"schema", "intent", "result"} or structured.get("schema") != "mesh-to-cad.agent-response/7" or structured.get("intent") != "inspect_formal_preview" or not isinstance(structured.get("result"), dict) or set(structured["result"]) != {"state", "preview_handle", "permitted_next_intents"} or structured["result"].get("state") != "available" or structured["result"].get("preview_handle") != handle or not isinstance(structured["result"].get("permitted_next_intents"), list) or any(type(item) is not str or item not in MCP_PERMITTED_INTENTS for item in structured["result"]["permitted_next_intents"]):
        raise ProviderFreeError("MCP preview envelope is invalid")
    return {"initialize_id": initialized.get("id"), "tools_list_id": listed.get("id"), "call_id": result.get("id"), "tools": 1, "content_types": [item.get("type") for item in content], "image_bytes": len(expected), "text_present": True, "handle_bound": True}


def _public(value: Any) -> str:
    text = json.dumps(value, sort_keys=True)
    forbidden = ("target_key", "mask_sha256", "depth8", "/Users/", "/home/", "Traceback", "Exception")
    if any(item in text for item in forbidden):
        raise ProviderFreeError("public response leaked forbidden detail")
    return text


def _draft_feedback_authority(
    parent_measurement: Mapping[str, Any], draft_measurement: Mapping[str, Any]
) -> dict[str, Any]:
    """Independently project the public Active-frontier draft feedback."""

    def public_kind(item: Mapping[str, Any]) -> str:
        if item.get("kind") == "exterior":
            return "exterior"
        profile = item.get("error_profile")
        if not isinstance(profile, Mapping):
            raise ProviderFreeError("draft feedback target profile is invalid")
        if profile.get("missing_surface_count") == 1 and profile.get("excess_surface_count") == 0:
            return "missing"
        if profile.get("missing_surface_count") == 0 and profile.get("excess_surface_count") == 1:
            return "excess"
        raise ProviderFreeError("draft feedback target polarity is invalid")

    def frontier(measurement: Mapping[str, Any]) -> tuple[dict[str, int], list[dict[str, Any]]]:
        errors = measurement.get("errors_by_depth")
        if not isinstance(errors, list):
            raise ProviderFreeError("draft feedback depth authority is invalid")
        active = next(
            (
                item
                for item in errors
                if isinstance(item, Mapping)
                and isinstance(item.get("surface_error_count"), int)
                and item["surface_error_count"] > 0
            ),
            None,
        )
        counts = {
            "missing_surface_count": 0 if active is None else active["missing_surface_count"],
            "excess_surface_count": 0 if active is None else active["excess_surface_count"],
        }
        target_document = measurement.get("repair_targets")
        raw_items = target_document.get("ordered_targets") if isinstance(target_document, Mapping) else None
        if not isinstance(raw_items, list):
            raise ProviderFreeError("draft feedback target authority is invalid")
        if any(not isinstance(item, Mapping) for item in raw_items):
            raise ProviderFreeError("draft feedback target item is invalid")
        items = [
            {"kind": public_kind(item), "bounds_canonical": item["bounds_canonical"]}
            for item in raw_items
            if item.get("kind") != "exterior"
        ]
        return counts, items

    before, before_items = frontier(parent_measurement)
    after, after_items = frontier(draft_measurement)

    def identity(item: Mapping[str, Any]) -> str:
        return json.dumps(item, sort_keys=True, separators=(",", ":"))

    before_ids = {identity(item) for item in before_items}
    after_ids = {identity(item) for item in after_items}

    def section(items: list[dict[str, Any]]) -> dict[str, Any]:
        returned = items[:8]
        return {"total": len(items), "returned": len(returned), "remaining": len(items) - len(returned), "items": returned}

    return {
        "schema": "mesh-to-cad.repair-draft-feedback/1",
        "before": before,
        "after": after,
        "delta": {key: after[key] - before[key] for key in before},
        "target_change_preview": {
            "resolved": section([item for item in before_items if identity(item) not in after_ids]),
            "persisted": section([item for item in before_items if identity(item) in after_ids]),
            "new": section([item for item in after_items if identity(item) not in before_ids]),
        },
    }


def _committed_authority_snapshot(workspace: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for relative in ("steps", "cycles", "final"):
        root = workspace / relative
        if root.is_dir():
            for path in sorted(item for item in root.rglob("*") if item.is_file()):
                snapshot[path.relative_to(workspace).as_posix()] = path.read_bytes()
    index = workspace / "voxblame/index.json"
    if index.is_file():
        snapshot["voxblame/index.json"] = index.read_bytes()
    return snapshot


def _read_target_page(socket_path: Path, step_handle: str, offset: int) -> dict[str, Any]:
    frame = _surface_call(
        socket_path,
        {
            "schema": "mesh-to-cad.agent-intent/1",
            "intent": "inspect_repair_targets",
            "args": {"step_handle": step_handle, "offset": offset},
        },
    )
    _public(frame)
    response = frame.get("response") if frame.get("ok") is True else None
    if (
        not isinstance(response, dict)
        or set(response) != {"schema", "intent", "result"}
        or response.get("schema") != "mesh-to-cad.agent-response/7"
        or response.get("intent") != "inspect_repair_targets"
        or not isinstance(response.get("result"), dict)
    ):
        raise ProviderFreeError("Repair Target page envelope is invalid")
    return response["result"]


def _read_target_section(socket_path: Path, step_handle: str, rank: int) -> dict[str, Any]:
    frame = _surface_call(
        socket_path,
        {
            "schema": "mesh-to-cad.agent-intent/1",
            "intent": "observe_target_section",
            "args": {"step_handle": step_handle, "rank": rank},
        },
    )
    _public(frame)
    response = frame.get("response") if frame.get("ok") is True else None
    if (
        not isinstance(response, dict)
        or set(response) != {"schema", "intent", "result"}
        or response.get("schema") != "mesh-to-cad.agent-response/7"
        or response.get("intent") != "observe_target_section"
        or not isinstance(response.get("result"), dict)
    ):
        raise ProviderFreeError("Target Section envelope is invalid")
    return response["result"]


def _reject_invalid_local_occupancy_masks(
    supervisor: WorkspaceSupervisor,
    socket_path: Path,
    step_handle: str,
    observation: Mapping[str, Any],
) -> list[str]:
    occupancy = observation.get("local_occupancy")
    if not isinstance(occupancy, dict):
        raise ProviderFreeError("null-mask rejection probe requires an interior target")
    cases: list[tuple[str, dict[str, Any]]] = []

    interior_null = json.loads(json.dumps(observation))
    interior_null["local_occupancy"] = None
    cases.append(("interior_null", interior_null))

    center_null = json.loads(json.dumps(observation))
    for side in ("reference", "candidate"):
        center_null["local_occupancy"][side][1][1][1] = None
    cases.append(("center_null", center_null))

    edge_only = json.loads(json.dumps(observation))
    edge = next(
        (
            (x, y, z)
            for x in range(3)
            for y in range(3)
            for z in range(3)
            if sum(index in (0, 2) for index in (x, y, z)) >= 2
            and occupancy["reference"][x][y][z] is not None
        ),
        None,
    )
    if edge is None:
        raise ProviderFreeError("null-mask rejection probe has no in-frame edge")
    for side in ("reference", "candidate"):
        edge_only["local_occupancy"][side][edge[0]][edge[1]][edge[2]] = None
    cases.append(("edge_only", edge_only))

    all_null = json.loads(json.dumps(observation))
    for side in ("reference", "candidate"):
        all_null["local_occupancy"][side] = [
            [[None for _z in range(3)] for _y in range(3)] for _x in range(3)
        ]
    cases.append(("all_null", all_null))

    original = supervisor.observe_target_section
    rejected: list[str] = []
    try:
        for name, result in cases:
            supervisor.observe_target_section = lambda **_args: result
            frame = _surface_call(
                socket_path,
                {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": "observe_target_section",
                    "args": {
                        "step_handle": step_handle,
                        "rank": observation["rank"],
                    },
                },
            )
            _public(frame)
            if (
                frame.get("ok") is not False
                or frame.get("error", {}).get("classification")
                != "supervisor_contract_violation"
            ):
                raise ProviderFreeError(f"invalid local occupancy was accepted: {name}")
            rejected.append(name)
    finally:
        supervisor.observe_target_section = original
    return rejected


def _non_tied_profile(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    for side_name in ("reference", "candidate"):
        side = observation.get(side_name)
        if not isinstance(side, dict):
            continue
        section = side.get("core", side)
        if not isinstance(section, dict) or section.get("triangle_count", 0) < 3:
            continue
        totals = {axis: 0.0 for axis in ("x", "y", "z")}
        for profile in section.get("profiles", []):
            for slab in profile.get("slabs", []):
                fraction = slab.get("surface_area_fraction", 0.0)
                normals = slab.get("mean_abs_normal", {})
                for axis in totals:
                    totals[axis] += fraction * normals.get(axis, 0.0)
        ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        gap = ordered[0][1] - ordered[1][1]
        if gap > 0.0:
            result = {
                "side": side_name,
                "axis": ordered[0][0],
                "gap": gap,
            }
            if "core" in side:
                result["profile"] = "core"
            return result
    return None


def _authority_public_target(exp_dir: Path, step: int, rank: int) -> dict[str, Any]:
    document = json.loads(
        (exp_dir / f"voxblame/steps/{step:06d}/measurement.json").read_text(
            encoding="utf-8"
        )
    )
    ordered = document.get("repair_targets", {}).get("ordered_targets", [])
    item = next(
        (candidate for candidate in ordered if candidate.get("display_rank") == rank),
        None,
    )
    if not isinstance(item, dict):
        raise ProviderFreeError("Target Section authority rank is unavailable")
    profile = item.get("error_profile")
    if item.get("kind") == "exterior":
        kind = "exterior"
    elif profile == {"missing_surface_count": 1, "excess_surface_count": 0, "surface_error_count": 1}:
        kind = "missing"
    elif profile == {"missing_surface_count": 0, "excess_surface_count": 1, "surface_error_count": 1}:
        kind = "excess"
    else:
        raise ProviderFreeError("Target Section authority direction is invalid")
    return {"rank": rank, "kind": kind, "bounds_canonical": item["bounds_canonical"]}


def _legacy_v12_authority_target_section(
    exp_dir: Path,
    step: int,
    rank: int,
    target_section_profile: Any,
) -> dict[str, Any]:
    """Reconstruct retired /2 only while validating historical V12 artifacts."""

    target = _authority_public_target(exp_dir, step, rank)
    core_bounds = target["bounds_canonical"]
    neighborhood_bounds = None
    if target["kind"] != "exterior":
        measurement = json.loads(
            (exp_dir / f"voxblame/steps/{step:06d}/measurement.json").read_text(
                encoding="utf-8"
            )
        )
        active_depth = next(
            (
                item.get("depth")
                for item in measurement.get("errors_by_depth", [])
                if isinstance(item, dict) and item.get("surface_error_count")
            ),
            None,
        )
        if type(active_depth) is not int or active_depth <= 0:
            raise ProviderFreeError("legacy V12 Active Depth is unavailable")
        width = 2.0 ** -active_depth
        neighborhood_bounds = {
            "min": [max(-0.5, value - width) for value in core_bounds["min"]],
            "max": [min(0.5, value + width) for value in core_bounds["max"]],
        }
    reference_path = exp_dir / "input/reference.ply"
    candidate_path = exp_dir / f"steps/{step:06d}/candidate/candidate.glb"
    return {
        "schema": "mesh-to-cad.target-section-observation/2",
        "rank": rank,
        "reference": {
            "core": target_section_profile(reference_path, core_bounds),
            "neighborhood": (
                target_section_profile(reference_path, neighborhood_bounds)
                if neighborhood_bounds is not None
                else None
            ),
        },
        "candidate": {
            "core": target_section_profile(candidate_path, core_bounds),
            "neighborhood": (
                target_section_profile(candidate_path, neighborhood_bounds)
                if neighborhood_bounds is not None
                else None
            ),
        },
    }


def _authority_cell_code(x: int, y: int, z: int, depth: int) -> int:
    code = 0
    for bit in range(depth - 1, -1, -1):
        code = (code << 3) | (
            (((x >> bit) & 1) << 2)
            | (((y >> bit) & 1) << 1)
            | ((z >> bit) & 1)
        )
    return code


def _authority_local_occupancy(
    exp_dir: Path,
    step: int,
    bounds: Mapping[str, Any],
    active_depth: int,
    read_surface_tree: Any,
) -> dict[str, Any]:
    if type(active_depth) is not int or not 1 <= active_depth <= 8:
        raise ProviderFreeError("Target Section Active Depth is unavailable")
    resolution = 1 << active_depth
    try:
        coordinates = tuple(
            int((bounds["min"][axis] + 0.5) * resolution) for axis in range(3)
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ProviderFreeError("Target Section authority bounds are invalid") from exc
    if not all(0 <= value < resolution for value in coordinates):
        raise ProviderFreeError("Target Section authority bounds leave the frame")
    expected_bounds = {
        "min": [-0.5 + value / resolution for value in coordinates],
        "max": [-0.5 + (value + 1) / resolution for value in coordinates],
    }
    if bounds != expected_bounds:
        raise ProviderFreeError("Target Section authority bounds are not one cell")

    def occupied(path: Path) -> set[int]:
        tree = read_surface_tree(path)
        if tree.max_depth != 8:
            raise ProviderFreeError("Target Section authority snapshot depth is invalid")
        shift = 3 * (8 - active_depth)
        return {int(code) >> shift for code in tree.iter_leaf_codes()}

    def cube(cells: set[int]) -> list[list[list[bool | None]]]:
        return [
            [
                [
                    (
                        _authority_cell_code(x, y, z, active_depth) in cells
                        if all(0 <= value < resolution for value in (x, y, z))
                        else None
                    )
                    for z in range(coordinates[2] - 1, coordinates[2] + 2)
                ]
                for y in range(coordinates[1] - 1, coordinates[1] + 2)
            ]
            for x in range(coordinates[0] - 1, coordinates[0] + 2)
        ]

    return {
        "target": [1, 1, 1],
        "reference": cube(occupied(exp_dir / "voxblame/reference.vbsvo")),
        "candidate": cube(
            occupied(exp_dir / f"voxblame/steps/{step:06d}/candidate.vbsvo")
        ),
    }


def _authority_target_section_v3(
    exp_dir: Path,
    step: int,
    rank: int,
    target_section_profile: Any,
    read_surface_tree: Any,
) -> dict[str, Any]:
    target = _authority_public_target(exp_dir, step, rank)
    core_bounds = target["bounds_canonical"]
    local_occupancy = None
    if target["kind"] != "exterior":
        measurement = json.loads(
            (exp_dir / f"voxblame/steps/{step:06d}/measurement.json").read_text(
                encoding="utf-8"
            )
        )
        active_depth = next(
            (
                item.get("depth")
                for item in measurement.get("errors_by_depth", [])
                if isinstance(item, dict) and item.get("surface_error_count")
            ),
            None,
        )
        if type(active_depth) is not int or active_depth <= 0:
            raise ProviderFreeError("Target Section Active Depth is unavailable")
        local_occupancy = _authority_local_occupancy(
            exp_dir,
            step,
            core_bounds,
            active_depth,
            read_surface_tree,
        )
    return {
        "schema": "mesh-to-cad.target-section-observation/3",
        "rank": rank,
        "reference": {
            "core": target_section_profile(
                exp_dir / "input/reference.ply", core_bounds
            )
        },
        "candidate": {
            "core": target_section_profile(
                exp_dir / f"steps/{step:06d}/candidate/candidate.glb",
                core_bounds,
            )
        },
        "local_occupancy": local_occupancy,
    }


def _target_center_polarity(
    observation: Mapping[str, Any], target: Mapping[str, Any]
) -> bool:
    occupancy = observation.get("local_occupancy")
    if not isinstance(occupancy, dict):
        return target.get("kind") == "exterior" and occupancy is None
    reference = occupancy["reference"][1][1][1]
    candidate = occupancy["candidate"][1][1][1]
    return (target.get("kind"), reference, candidate) in {
        ("missing", True, False),
        ("excess", False, True),
    }


def _run_exterior_target_section_probe(
    exp_dir: Path,
    fixture: Path,
    *,
    trusted: Path,
    published_rebuild: Path,
    published_geometry: Path,
    registry: Path,
    sidecar: Any,
    candidate_runtime: Any,
) -> dict[str, Any]:
    exterior_exp = exp_dir / "exterior-probe"
    candidate_root = exp_dir.parent / f".agent-candidate-exterior-{os.getpid()}"
    socket_dir = Path(tempfile.mkdtemp(prefix="ttc-e-", dir="/tmp"))
    supervisor = bridge = None
    cleanup_errors: list[str] = []
    try:
        runner.prepare_exp(exterior_exp)
        runner.prepare_and_initialize_workspace(
            exterior_exp, fixture, trusted_tools_root=trusted
        )
        supervisor = WorkspaceSupervisor(
            exterior_exp,
            bind_reference=True,
            candidate_root=candidate_root,
            rebuild_entrypoint=published_rebuild,
            geometry_entrypoint=published_geometry,
            tool_registry=registry,
            browser_runtime_capability=sidecar.capability_dir / "runtime.json",
            candidate_runtime=candidate_runtime,
            trusted_tools_root=trusted,
            trusted_product_root=trusted,
            reconstruction_spec=False,
            step_zero_evidence_provider=lambda req: runner.real_step_zero_evidence_provider(
                req,
                capability_path=sidecar.capability_dir / "runtime.json",
                meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
                meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
            ),
            repair_evidence_provider=lambda req: runner.real_repair_evidence_provider(
                req,
                capability_path=sidecar.capability_dir / "runtime.json",
                meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
                meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
            ),
        )
        bridge = AgentSurfaceBridge(
            supervisor.agent_surface(),
            socket_dir / "surface.sock",
            trusted_product_root=trusted,
        )
        bridge.start()
        bootstrap = supervisor.agent_bootstrap_contract()
        surface = supervisor.agent_surface()
        workspace_handle = bootstrap["workspace_handle"]
        plan_handle = bootstrap["plan_handle"]
        _json(
            candidate_root / "plan.json",
            {
                "schema": "mesh-to-cad.initial-plan/1",
                "summary": "fixed exterior target integration probe",
            },
        )
        attempt_response = surface.handle(
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "start_attempt",
                "args": {
                    "workspace_handle": workspace_handle,
                    "plan_handle": plan_handle,
                },
            }
        )
        _public(attempt_response)
        attempt = attempt_response["result"]
        _exterior_source(candidate_root / "work/source/model.py")
        run_response = surface.handle(
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "run_candidate_tool",
                "args": {
                    "workspace_handle": workspace_handle,
                    "attempt_handle": attempt["attempt_handle"],
                    "candidate_handle": attempt["candidate_handle"],
                    "operation_handle": attempt["capability_bundle_handle"],
                },
            }
        )
        _public(run_response)
        submit_response = surface.handle(
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "submit_step_zero",
                "args": {
                    "workspace_handle": workspace_handle,
                    "attempt_handle": attempt["attempt_handle"],
                    "candidate_handle": attempt["candidate_handle"],
                },
            }
        )
        _public(submit_response)
        step = submit_response["result"]
        step_ordinal = step["decision_facts"]["step_ordinal"]
        offset = 0
        exterior_page = None
        exterior_item = None
        while True:
            page = _read_target_page(bridge.socket_path, step["step_handle"], offset)
            exterior_item = next(
                (item for item in page["items"] if item["kind"] == "exterior"),
                None,
            )
            if exterior_item is not None:
                exterior_page = page
                break
            next_offset = page["next_offset"]
            if next_offset is None:
                break
            offset = next_offset
        if exterior_page is None or exterior_item is None:
            raise ProviderFreeError("fixed exterior probe published no exterior target")
        raw_observation = supervisor.observe_target_section(
            step["step_handle"], exterior_item["rank"]
        )
        exterior_nonnull = json.loads(json.dumps(raw_observation))
        exterior_nonnull["local_occupancy"] = {
            "target": [1, 1, 1],
            "reference": [
                [[False for _z in range(3)] for _y in range(3)]
                for _x in range(3)
            ],
            "candidate": [
                [[False for _z in range(3)] for _y in range(3)]
                for _x in range(3)
            ],
        }
        original_observer = supervisor.observe_target_section
        try:
            supervisor.observe_target_section = lambda **_args: exterior_nonnull
            malformed = _surface_call(
                bridge.socket_path,
                {
                    "schema": "mesh-to-cad.agent-intent/1",
                    "intent": "observe_target_section",
                    "args": {
                        "step_handle": step["step_handle"],
                        "rank": exterior_item["rank"],
                    },
                },
            )
            _public(malformed)
        finally:
            supervisor.observe_target_section = original_observer
        if (
            malformed.get("ok") is not False
            or malformed.get("error", {}).get("classification")
            != "supervisor_contract_violation"
        ):
            raise ProviderFreeError("exterior non-null occupancy was accepted")
        observation = _read_target_section(
            bridge.socket_path, step["step_handle"], exterior_item["rank"]
        )
        return {
            "workspace": "exterior-probe",
            "step_ordinal": step_ordinal,
            "public_page": exterior_page,
            "public_item": exterior_item,
            "observation": observation,
            "authority_recomputed": True,
            "nonnull_rejected": True,
        }
    finally:
        for resource, action in ((bridge, "stop"), (supervisor, "close")):
            if resource is None:
                continue
            try:
                getattr(resource, action)()
            except Exception as exc:
                cleanup_errors.append(f"exterior-{action}:{type(exc).__name__}")
        try:
            shutil.rmtree(socket_dir)
        except OSError as exc:
            cleanup_errors.append(f"exterior-socket:{type(exc).__name__}")
        if cleanup_errors:
            raise ProviderFreeError("exterior probe cleanup failed: " + ",".join(cleanup_errors))


def _expand_mask_prefixes(path: Path, active_depth: int) -> tuple[set[int], set[int]]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("directional mask is unavailable") from exc
    if (
        not isinstance(document, dict)
        or set(document) != {"schema", "max_depth", "regions"}
        or document.get("schema") != "octree_region_set/1"
        or document.get("max_depth") != 8
        or not isinstance(document.get("regions"), list)
    ):
        raise ProviderFreeError("directional mask schema is invalid")
    depth8: set[int] = set()
    coarse: set[int] = set()
    for region in document["regions"]:
        if not isinstance(region, dict) or set(region) != {"depth", "prefix"}:
            raise ProviderFreeError("directional mask region is invalid")
        depth = region["depth"]
        prefix = region["prefix"]
        if type(depth) is not int or type(prefix) is not int or not 0 <= depth <= 8:
            raise ProviderFreeError("directional mask region identity is invalid")
        remaining = 3 * (8 - depth)
        cells = range(prefix << remaining, (prefix + 1) << remaining)
        depth8.update(cells)
        if depth >= active_depth:
            coarse.add(prefix >> (3 * (depth - active_depth)))
        else:
            shift = 3 * (active_depth - depth)
            coarse.update(range(prefix << shift, (prefix + 1) << shift))
    return depth8, coarse


def _coarse_bounds(prefix: int, depth: int) -> dict[str, list[float]]:
    coordinates = [0, 0, 0]
    for shift in range(depth - 1, -1, -1):
        child = (prefix >> (3 * shift)) & 7
        coordinates[0] = (coordinates[0] << 1) | ((child >> 2) & 1)
        coordinates[1] = (coordinates[1] << 1) | ((child >> 1) & 1)
        coordinates[2] = (coordinates[2] << 1) | (child & 1)
    resolution = 1 << depth
    return {
        "min": [-0.5 + value / resolution for value in coordinates],
        "max": [-0.5 + (value + 1) / resolution for value in coordinates],
    }


def _directional_projection_evidence(
    exp_dir: Path,
    *,
    facts: Mapping[str, Any],
    repair_ordinal: int,
    meshscope_src: Path,
    selected_target: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    active_depth = facts["residual_summary"]["repair_frontier"]["active_depth"]
    measurement_root = exp_dir / "voxblame" / "steps/000000"
    measurement = json.loads(
        (measurement_root / "measurement.json").read_text(encoding="utf-8")
    )
    session = json.loads(
        (exp_dir / "voxblame" / "session.json").read_text(encoding="utf-8")
    )
    if session.get("profiles", {}).get("target_partition") != "repair_target_partition/3":
        raise ProviderFreeError("directional partition profile was not published")
    targets = measurement.get("repair_targets", {})
    ordered = targets.get("ordered_targets")
    if targets.get("ordering_profile") != "repair_target_display/2" or not isinstance(ordered, list):
        raise ProviderFreeError("directional target order is invalid")
    interior = [item for item in ordered if item.get("kind") == "interior"]
    active_metric = measurement["errors_by_depth"][active_depth - 1]
    profile_total = sum(item["error_profile"]["surface_error_count"] for item in interior)
    if profile_total != active_metric["surface_error_count"] or len(interior) != profile_total:
        raise ProviderFreeError("directional target totals do not match Active Depth")

    if os.fspath(meshscope_src) not in sys.path:
        sys.path.insert(0, os.fspath(meshscope_src))
    from meshscope.voxblame.codec import read_surface_tree

    missing = {
        int(code)
        for code in read_surface_tree(measurement_root / "missing-depth8.vbsvo").iter_leaf_codes()
    }
    excess = {
        int(code)
        for code in read_surface_tree(measurement_root / "excess-depth8.vbsvo").iter_leaf_codes()
    }
    reference = {
        int(code)
        for code in read_surface_tree(exp_dir / "voxblame/reference.vbsvo").iter_leaf_codes()
    }
    candidate = {
        int(code)
        for code in read_surface_tree(measurement_root / "candidate.vbsvo").iter_leaf_codes()
    }
    shift = 3 * (8 - active_depth)
    canceled = {code >> shift for code in missing} & {code >> shift for code in excess}
    if not canceled:
        raise ProviderFreeError("fixture did not prove canceled legacy prefixes")
    expected = sorted(
        [*( (prefix, "missing") for prefix in {code >> shift for code in reference} - {code >> shift for code in candidate} ),
         *( (prefix, "excess") for prefix in {code >> shift for code in candidate} - {code >> shift for code in reference} )],
        key=lambda item: (item[0], 0 if item[1] == "missing" else 1),
    )
    observed: list[tuple[int, str]] = []
    for target in interior:
        profile = target["error_profile"]
        direction = "missing" if profile["missing_surface_count"] else "excess"
        support = missing if direction == "missing" else excess
        opposite = excess if direction == "missing" else missing
        mask_cells, coarse_cells = _expand_mask_prefixes(
            exp_dir / Path(target["mask"]["path"]), active_depth
        )
        if len(coarse_cells) != 1:
            raise ProviderFreeError("directional target mask crosses Active-Depth cells")
        prefix = next(iter(coarse_cells))
        if (
            mask_cells != {code for code in support if code >> shift == prefix}
            or mask_cells & opposite
            or target["bounds_canonical"] != _coarse_bounds(prefix, active_depth)
        ):
            raise ProviderFreeError("directional target mask or bounds is invalid")
        observed.append((prefix, direction))
    if observed != expected or any(
        item.get("kind") == "interior"
        for item in ordered[len(interior):]
    ):
        raise ProviderFreeError("directional target coverage or order is invalid")

    public_targets = facts.get("repair_targets")
    public_items = public_targets.get("items") if isinstance(public_targets, dict) else None
    if (
        facts.get("schema") != "mesh-to-cad.decision-facts/2"
        or not isinstance(public_items, list)
        or not public_items
        or any(item.get("kind") not in {"missing", "excess", "exterior"} for item in public_items)
        or any(set(item) != {"rank", "kind", "bounds_canonical"} for item in public_items)
    ):
        raise ProviderFreeError("Agent projection is not closed and directional")
    selected = dict(selected_target or public_items[0])
    private_matches = [item for item in ordered if item.get("display_rank") == selected["rank"]]
    if len(private_matches) != 1:
        raise ProviderFreeError("public target does not map to one private identity")
    private = private_matches[0]
    expected_kind = "missing" if private["error_profile"]["missing_surface_count"] else "excess"
    if selected["kind"] != expected_kind or selected["bounds_canonical"] != private["bounds_canonical"]:
        raise ProviderFreeError("public target direction conflicts with private authority")
    mask_path = exp_dir / Path(private["mask"]["path"])
    mask_cells, coarse_cells = _expand_mask_prefixes(mask_path, active_depth)
    opposite = excess if expected_kind == "missing" else missing
    if len(coarse_cells) != 1 or mask_cells & opposite:
        raise ProviderFreeError("directional target mask crosses cell or direction")
    diff = json.loads(
        (exp_dir / f"cycles/{repair_ordinal:06d}/diff.json").read_text(encoding="utf-8")
    )
    resolved = diff.get("repair_batch", {}).get("selected_targets")
    if (
        not isinstance(resolved, list)
        or len(resolved) != 1
        or set(resolved[0]) != {"target_key", "kind", "mask_sha256"}
        or resolved[0].get("target_key") != private["target_key"]
        or resolved[0].get("kind") != "interior"
        or resolved[0].get("mask_sha256") != private["mask"]["logical_sha256"]
    ):
        raise ProviderFreeError("Region Diff did not bind one directional identity")
    return {
        "schema": "text-to-cad.directional-active-depth-evidence/1",
        "partition_profile": "repair_target_partition/3",
        "ordering_profile": "repair_target_display/2",
        "active_depth": active_depth,
        "exact_metric": {
            "missing": active_metric["missing_surface_count"],
            "excess": active_metric["excess_surface_count"],
            "total": active_metric["surface_error_count"],
            "published_interior_total": profile_total,
        },
        "canceled_legacy_prefixes": len(canceled),
        "public": {
            "schema": facts["schema"],
            "returned": len(public_items),
            "total": public_targets["total"],
            "kinds": sorted({item["kind"] for item in public_items}),
            "closed_fields": True,
            "items": public_items,
        },
        "selected": {
            "rank": selected["rank"],
            "kind": selected["kind"],
            "bounds_canonical": selected["bounds_canonical"],
            "private_kind": private["kind"],
            "private_identity_count": 1,
            "mask_active_cell_count": len(coarse_cells),
            "mask_opposite_support_count": len(mask_cells & opposite),
            "region_diff_identity_count": len(resolved),
        },
    }


def _validate_v1_artifacts(repo_root: Path, record: Mapping[str, Any]) -> tuple[Path, Path]:
    _, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES or artifact_manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderFreeError("artifact too large")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("evidence missing or invalid") from exc
    identity = expected_identity(record)
    required = {"schema", "identity", "scenario", "gate_passed", "sequence", "active_depth", "selected_public_target", "step_zero", "repair", "ordinals", "attempt", "region_diff", "cycle", "previews", "original_plan_digest_binding", "mcp", "browser", "workspace_validation", "module_paths"}
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schema") != EVIDENCE_SCHEMA_V1 or evidence.get("identity") != identity or evidence.get("scenario") != SCENARIO or evidence.get("gate_passed") is not True:
        raise ProviderFreeError("invalid evidence shape")
    if evidence["sequence"] != ["plan", "start_attempt", "run_candidate", "submit_step_zero", "mcp_inspect_step_zero", "start_repair", "run_candidate", "submit_repair", "mcp_inspect_repair"]:
        raise ProviderFreeError("invalid sequence")
    if not isinstance(evidence["active_depth"], int) or evidence["active_depth"] <= 0 or not isinstance(evidence["selected_public_target"], dict):
        raise ProviderFreeError("invalid active depth evidence")
    for key in ("step_zero", "repair"):
        if not isinstance(evidence[key], dict) or not evidence[key].get("handle"):
            raise ProviderFreeError("missing publication handle")
    exp_root = (repo_root / str(record["exp_dir"])).resolve()
    ordinals = evidence["ordinals"]
    if not isinstance(ordinals, dict) or set(ordinals) != {"step_zero", "repair", "repair_parent"} or any(type(ordinals[key]) is not int or ordinals[key] < 0 for key in ordinals) or ordinals["repair_parent"] != ordinals["step_zero"]:
        raise ProviderFreeError("invalid publication ordinals")
    expected_paths = {
        "attempt": f"cycles/{ordinals['repair']:06d}/attempt.json",
        "region_diff": f"cycles/{ordinals['repair']:06d}/diff.json",
        "cycle": f"cycles/{ordinals['repair']:06d}/cycle.json",
    }
    for key in ("attempt", "region_diff", "cycle"):
        item = evidence[key]
        target = (exp_root / str(item.get("path"))).resolve() if isinstance(item, dict) and isinstance(item.get("path"), str) else None
        if not isinstance(item, dict) or item.get("path") != expected_paths[key] or not isinstance(item.get("bytes"), int) or item["bytes"] <= 0 or target is None:
            raise ProviderFreeError("missing authority evidence")
        try: target.relative_to(exp_root)
        except ValueError: raise ProviderFreeError("authority path escaped exp")
        if not target.is_file() or target.stat().st_size != item["bytes"]:
            raise ProviderFreeError("missing authority evidence")
    repair_attempt_path = (exp_root / evidence["attempt"]["path"]).resolve()
    diff_path = (exp_root / evidence["region_diff"]["path"]).resolve()
    try:
        attempt = json.loads(repair_attempt_path.read_text(encoding="utf-8"))
        region_diff = json.loads(diff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("authority evidence is not json") from exc
    batch = region_diff.get("repair_batch") if isinstance(region_diff, dict) else None
    required_batch = {"schema", "from_step", "selected_targets", "planned_edits", "plan_sha256"}
    if not isinstance(batch, dict) or set(batch) != required_batch or batch.get("schema") != "voxblame.repair-batch/1" or not isinstance(batch.get("from_step"), int) or not isinstance(batch.get("selected_targets"), list) or not isinstance(batch.get("planned_edits"), list) or not isinstance(batch.get("plan_sha256"), str):
        raise ProviderFreeError("invalid repair batch schema")
    attempt_digest = attempt.get("plan_digest") if isinstance(attempt, dict) else None
    if not isinstance(attempt_digest, str) or attempt.get("intended_step") != 1 or attempt.get("from_step") != 0 or batch["from_step"] != 0 or batch["plan_sha256"] != attempt_digest:
        raise ProviderFreeError("repair batch digest mismatch")
    step_document = json.loads((exp_root / f"steps/{ordinals['step_zero']:06d}/step.json").read_text(encoding="utf-8"))
    cycle_document = json.loads((exp_root / expected_paths["cycle"]).read_text(encoding="utf-8"))
    if not isinstance(step_document, dict) or step_document.get("step") != ordinals["step_zero"] or step_document.get("parent_step") is not None or step_document.get("cycle") is not None or step_document.get("compare_to") is not None:
        raise ProviderFreeError("Step 0 manifest contract mismatch")
    if not isinstance(cycle_document, dict) or cycle_document.get("cycle") != ordinals["repair"] or cycle_document.get("from_step") != ordinals["repair_parent"] or cycle_document.get("to_step") != ordinals["repair"]:
        raise ProviderFreeError("repair cycle manifest identity mismatch")
    if cycle_document.get("from_step") != ordinals["step_zero"]:
        raise ProviderFreeError("repair parent manifest mismatch")
    previews = evidence["previews"]
    if not isinstance(previews, dict) or previews.get("distinct") is not True or not all(isinstance(previews.get(key), dict) and isinstance(previews[key].get("path"), str) and isinstance(previews[key].get("bytes"), int) and previews[key]["bytes"] > 0 for key in ("step_zero", "repair")):
        raise ProviderFreeError("invalid preview evidence")
    expected_previews = {
        "step_zero": f"steps/{ordinals['step_zero']:06d}/preview/preview.png",
        "repair": f"steps/{ordinals['repair']:06d}/preview/preview.png",
    }
    for key, expected_path in expected_previews.items():
        item = previews[key]
        if item["path"] != expected_path:
            raise ProviderFreeError("invalid preview authority path")
        preview_path = (exp_root / item["path"]).resolve()
        try:
            preview_path.relative_to(exp_root)
        except ValueError as exc:
            raise ProviderFreeError("preview path escaped exp") from exc
        if not preview_path.is_file() or preview_path.stat().st_size != item["bytes"]:
            raise ProviderFreeError("preview authority missing or changed")
    binding = evidence["original_plan_digest_binding"]
    if not isinstance(binding, dict) or binding.get("attempts", 0) < 2 or not isinstance(binding.get("repair_attempt_path"), str) or not isinstance(binding.get("attempt_plan_digest"), str) or not isinstance(binding.get("region_diff_plan_sha256"), str) or binding.get("derived_equal") is not True or binding["attempt_plan_digest"] != binding["region_diff_plan_sha256"] or not isinstance(binding.get("plan_files"), list):
        raise ProviderFreeError("invalid plan binding evidence")
    for relative in [binding["repair_attempt_path"], *binding["plan_files"]]:
        if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts or not (exp_root / relative).resolve().is_file():
            raise ProviderFreeError("invalid plan authority path")
    browser = evidence["browser"]
    if not isinstance(browser, dict) or browser.get("capability") is not True or browser.get("preflight") is not True or evidence["workspace_validation"] is not True:
        raise ProviderFreeError("invalid runtime evidence")
    modules = evidence["module_paths"]
    if not isinstance(modules, dict) or set(modules) != {"product_root", "workspace", "core", "handler", "mcp", "rebuild", "geometry"} or any(not isinstance(modules[key], str) or modules[key].startswith("/") for key in modules) or any(not modules[key].startswith("skills/") for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")):
        raise ProviderFreeError("invalid published module paths")
    if modules["product_root"] != "skills":
        raise ProviderFreeError("invalid published module root")
    for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry"):
        relative = PurePosixPath(modules[key])
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0:1] != ("skills",):
            raise ProviderFreeError("invalid published module path")
    for key in ("step_zero", "repair"):
        mcp = evidence["mcp"].get(key)
        if not isinstance(mcp, dict) or mcp.get("image_bytes") != previews[key]["bytes"]:
            raise ProviderFreeError("MCP preview size mismatch")
    _public(evidence["selected_public_target"])
    if not isinstance(manifest, dict) or manifest != {"schema": MANIFEST_SCHEMA, "final_status": 0, "identity": identity, "evidence": {"path": evidence_path.name}}:
        raise ProviderFreeError("invalid manifest")
    return evidence_path, artifact_manifest_path


def _validate_v2_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    schema: str = EVIDENCE_SCHEMA_V2,
) -> tuple[Path, Path]:
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("v2 evidence missing or invalid") from exc
    required = {"schema", "identity", "scenario", "gate_passed", "selection", "steps", "graph", "previews", "mcp", "workspace_validation", "module_paths", "runtime", "final", "cycles"}
    if schema in {EVIDENCE_SCHEMA_V3, EVIDENCE_SCHEMA_V4}:
        required.add("spec_persistence")
    if schema == EVIDENCE_SCHEMA_V4:
        required.add("spec_region_binding")
    if not isinstance(evidence, dict) or set(evidence) != required or evidence.get("schema") != schema or evidence.get("identity") != expected_identity(record) or evidence.get("scenario") != SCENARIO or evidence.get("gate_passed") is not True:
        raise ProviderFreeError("invalid v2 evidence shape")
    selection = evidence["selection"]
    if not isinstance(selection, dict) or set(selection) != {"considered", "selected", "selected_step", "repair_b_is_head"} or selection.get("selected") not in {"step_zero", "repair_a"} or selection.get("considered") != ["step_zero", "repair_a", "repair_b"]:
        raise ProviderFreeError("invalid historical selection evidence")
    steps = evidence["steps"]
    if not isinstance(steps, dict) or set(steps) != {"step_zero", "repair_a", "repair_b"}:
        raise ProviderFreeError("invalid v2 step evidence")
    cycles = evidence["cycles"]
    if not isinstance(cycles, dict) or set(cycles) != {"repair_a", "repair_b"}:
        raise ProviderFreeError("invalid v2 cycle evidence")
    for name in ("repair_a", "repair_b"):
        cycle = cycles[name]
        if not isinstance(cycle, dict) or set(cycle) != {"ordinal", "from_step", "to_step", "selected_parent_target", "artifacts"}:
            raise ProviderFreeError("invalid cycle record")
        if cycle["ordinal"] != steps[name]["ordinal"] or cycle["from_step"] != steps[name]["parent"] or cycle["to_step"] != steps[name]["ordinal"]:
            raise ProviderFreeError("cycle edge mismatch")
        target = cycle["selected_parent_target"]
        if not isinstance(target, dict) or set(target) != {"rank", "kind", "bounds_canonical"}:
            raise ProviderFreeError("invalid parent target record")
        artifacts = cycle["artifacts"]
        expected = {"plan": f"cycles/{cycle['to_step']:06d}/plan.json", "assessment": f"cycles/{cycle['to_step']:06d}/assessment.json", "diff": f"cycles/{cycle['to_step']:06d}/diff.json", "cycle": f"cycles/{cycle['to_step']:06d}/cycle.json", "attempt": f"cycles/{cycle['to_step']:06d}/attempt.json"}
        if not isinstance(artifacts, dict) or set(artifacts) != set(expected) or any(artifacts[key] != expected[key] for key in expected):
            raise ProviderFreeError("invalid cycle artifact paths")
        for relative in artifacts.values():
            target_path = (exp_dir / relative).resolve()
            try: target_path.relative_to(exp_dir.resolve())
            except ValueError as exc: raise ProviderFreeError("cycle path escaped exp") from exc
            if not target_path.is_file(): raise ProviderFreeError("cycle artifact missing")
        docs = {key: json.loads((exp_dir / relative).read_text(encoding="utf-8")) for key, relative in artifacts.items()}
        plan = docs["plan"]
        assessment = docs["assessment"]
        cycle_doc = docs["cycle"]
        attempt = docs["attempt"]
        diff = docs["diff"]
        if plan.get("from_step") != cycle["from_step"] or cycle["selected_parent_target"] not in plan.get("selected_targets", []) or not any(edit.get("target_ranks") == [cycle["selected_parent_target"]["rank"]] for edit in plan.get("planned_edits", [])):
            raise ProviderFreeError("cycle plan target binding mismatch")
        if schema == EVIDENCE_SCHEMA_V4:
            if any(edit.get("spec_region_id") != "component.primary" for edit in plan.get("planned_edits", [])):
                raise ProviderFreeError("cycle Spec Region binding mismatch")
            if any("spec_region_id" in edit for edit in diff.get("repair_batch", {}).get("planned_edits", [])):
                raise ProviderFreeError("Region Diff leaked Spec Region binding")
        if assessment.get("from_step") != cycle["from_step"] or assessment.get("to_step") != cycle["to_step"]:
            raise ProviderFreeError("cycle assessment edge mismatch")
        if cycle_doc.get("cycle") != cycle["to_step"] or cycle_doc.get("from_step") != cycle["from_step"] or cycle_doc.get("to_step") != cycle["to_step"]:
            raise ProviderFreeError("cycle manifest edge mismatch")
        if attempt.get("intended_step") != cycle["to_step"] or attempt.get("from_step") != cycle["from_step"] or attempt.get("intended_cycle") != cycle["to_step"]:
            raise ProviderFreeError("cycle attempt edge mismatch")
        batch = diff.get("repair_batch") if isinstance(diff, dict) else None
        if diff.get("from_step") != cycle["from_step"] or not isinstance(batch, dict) or batch.get("plan_sha256") != attempt.get("plan_digest") or cycle_doc.get("plan_digest") != attempt.get("plan_digest"):
            raise ProviderFreeError("cycle digest binding mismatch")
    for name, item in steps.items():
        frontier = item.get("frontier")
        if not isinstance(item, dict) or set(item) != {"step_handle", "ordinal", "parent", "cycle", "accepted", "frontier", "target_count", "manifest"} or not isinstance(item.get("ordinal"), int) or item.get("manifest") != f"steps/{item['ordinal']:06d}/step.json" or item.get("accepted") is not False or type(item.get("target_count")) is not int or item["target_count"] <= 0 or not isinstance(frontier, dict) or set(frontier) != {"active_depth", "missing_surface_error_count", "excess_surface_error_count", "surface_error_count", "target_count"} or frontier.get("target_count") != item["target_count"]:
            raise ProviderFreeError("invalid v2 step manifest record")
        step_manifest_path = (exp_dir / item["manifest"]).resolve()
        if not step_manifest_path.is_file():
            raise ProviderFreeError("step manifest missing")
        document = json.loads(step_manifest_path.read_text(encoding="utf-8"))
        if document.get("step") != item["ordinal"] or document.get("parent_step") != item.get("parent"):
            raise ProviderFreeError("step manifest identity mismatch")
    if steps["repair_a"]["parent"] != steps["step_zero"]["ordinal"] or steps["repair_b"]["parent"] != steps["repair_a"]["ordinal"]:
        raise ProviderFreeError("repair parent chain mismatch")
    expected_best = "repair_a" if _frontier_order(steps["repair_a"]["frontier"]) > _frontier_order(steps["step_zero"]["frontier"]) else "step_zero"
    if selection.get("selected") != expected_best or selection.get("selected_step") != steps[expected_best]["ordinal"] or selection.get("selected_step") == steps["repair_b"]["ordinal"]:
        raise ProviderFreeError("selected historical ordinal mismatch")
    graph = evidence["graph"]
    if not isinstance(graph, dict) or set(graph) != {"source", "heads"} or graph.get("source") != "step_parentage" or graph.get("heads") != [steps["repair_b"]["ordinal"]]:
        raise ProviderFreeError("graph head identity mismatch")
    if not _frontier_order(steps[expected_best]["frontier"]) > _frontier_order(steps["repair_b"]["frontier"]):
        raise ProviderFreeError("frontier ordering mismatch")
    previews = evidence["previews"]
    if not isinstance(previews, dict) or set(previews) != {"step_zero", "repair_a", "repair_b", "selected_reinspect"}:
        raise ProviderFreeError("invalid v2 preview evidence")
    for key, item in previews.items():
        if not isinstance(item, dict) or set(item) != {"path", "bytes"} or not isinstance(item.get("path"), str) or not isinstance(item.get("bytes"), int) or item["bytes"] <= 0:
            raise ProviderFreeError("invalid v2 preview record")
        target = (exp_dir / item["path"]).resolve()
        try:
            target.relative_to(exp_dir.resolve())
        except ValueError as exc:
            raise ProviderFreeError("v2 preview path escaped exp") from exc
        if not target.is_file() or target.stat().st_size != item["bytes"]:
            raise ProviderFreeError("v2 preview missing or changed")
    expected_preview_key = selection["selected"]
    if previews["selected_reinspect"] != previews[expected_preview_key]:
        raise ProviderFreeError("selected reinspect preview is not best step")
    if evidence.get("workspace_validation") is not True or not isinstance(evidence.get("mcp"), dict):
        raise ProviderFreeError("invalid v2 runtime evidence")
    modules = evidence["module_paths"]
    if not isinstance(modules, dict) or set(modules) != {"product_root", "workspace", "core", "handler", "mcp", "rebuild", "geometry"} or modules.get("product_root") != "skills" or any(not isinstance(modules[key], str) or not modules[key].startswith("skills/") for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")):
        raise ProviderFreeError("invalid v2 module provenance")
    runtime = evidence["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {"interpreter", "registry"} or runtime.get("interpreter") != ".venv/bin/python":
        raise ProviderFreeError("invalid v2 runtime identity")
    registry = runtime["registry"]
    if not isinstance(registry, dict) or set(registry) != {"schema", "rebuild_id", "geometry_id", "authority", "provenance"} or registry != {"schema": "mesh-to-cad.tool-registry/2", "rebuild_id": "cad.canonical-build/1", "geometry_id": "mesh-compare.voxblame/1", "authority": "installed_publish_tree", "provenance": "receipt.publish_tree"}:
        raise ProviderFreeError("invalid v2 registry provenance")
    mcp = evidence["mcp"]
    for key in ("step_zero", "repair_a", "repair_b", "selected_reinspect"):
        if not isinstance(mcp.get(key), dict) or set(mcp[key]) != {"initialize_id", "tools_list_id", "call_id", "tools", "content_types", "image_bytes", "text_present", "handle_bound"} or mcp[key].get("image_bytes") != previews[key]["bytes"]:
            raise ProviderFreeError("v2 MCP preview mismatch")
    final = evidence.get("final")
    expected_final = {"manifest": "final/manifest.json", "source": "final/source/source/model.py", "measurement": "final/measurement.json", "preview": "final/preview.json", "verification": "final/verification.json"}
    if not isinstance(final, dict) or set(final) != {"manifest", "selected_step", "source", "measurement", "preview", "verification", "identity_bound"} or final.get("selected_step") != selection.get("selected_step") or any(final.get(key) != value for key, value in expected_final.items()):
        raise ProviderFreeError("final selection identity mismatch")
    for relative in ("final/manifest.json", "final/source/source/model.py", "final/measurement.json", "final/preview.json", "final/verification.json"):
        target = (exp_dir / relative).resolve()
        try:
            target.relative_to(exp_dir.resolve())
        except ValueError as exc:
            raise ProviderFreeError("final artifact path escaped exp") from exc
        if not target.is_file():
            raise ProviderFreeError("final artifact missing")
    final_manifest = json.loads((exp_dir / "final/manifest.json").read_text(encoding="utf-8"))
    final_preview = json.loads((exp_dir / "final/preview.json").read_text(encoding="utf-8"))
    verification = json.loads((exp_dir / "final/verification.json").read_text(encoding="utf-8"))
    if final_manifest.get("selected_step") != selection.get("selected_step") or final_manifest.get("stop_reason") != "no_feasible_strategy" or final_preview.get("selected_step") != selection.get("selected_step"):
        raise ProviderFreeError("final manifest or preview is not bound to selected step")
    equality = verification.get("equality")
    if verification.get("against_step") != selection.get("selected_step") or verification.get("verified") is not True or not isinstance(equality, dict) or set(equality) != {"interior", "exterior", "observable", "errors_by_depth"} or any(value is not True for value in equality.values()):
        raise ProviderFreeError("final verification is not bound to selected step")
    selected_source = exp_dir / f"steps/{selection['selected_step']:06d}/candidate/source/model.py"
    if not selected_source.is_file() or (exp_dir / "final/source/source/model.py").read_bytes() != selected_source.read_bytes():
        raise ProviderFreeError("final source is not selected-step source")
    if (exp_dir / "final/measurement.json").read_bytes() != (exp_dir / f"steps/{selection['selected_step']:06d}/measurement.json").read_bytes():
        raise ProviderFreeError("final measurement is not selected-step measurement")
    manifest_version = 4 if schema == EVIDENCE_SCHEMA_V4 else 3 if schema == EVIDENCE_SCHEMA_V3 else 2
    expected_manifest = {"schema": f"text-to-cad.provider-free-artifact-manifest/{manifest_version}", "final_status": 0, "identity": expected_identity(record), "evidence": {"path": evidence_path.name}}
    if not isinstance(manifest, dict) or manifest != expected_manifest:
        raise ProviderFreeError("invalid v2 manifest")
    if final.get("identity_bound") is not True:
        raise ProviderFreeError("final identity binding was not derived")
    if final_manifest.get("selected_step") != selection["selected_step"] or final_manifest.get("selected_step") == steps["repair_b"]["ordinal"]:
        raise ProviderFreeError("final manifest selected wrong step")
    if schema in {EVIDENCE_SCHEMA_V3, EVIDENCE_SCHEMA_V4}:
        spec = evidence["spec_persistence"]
        expected_spec = {
            "seam": "runner.persist_agent_reconstruction_spec",
            "path": runner.RECONSTRUCTION_SPEC_RELATIVE.as_posix(),
            "enabled_status": 0,
            "updated_bytes": len(SPEC_FINAL_BYTES) if schema == EVIDENCE_SCHEMA_V3 else spec["updated_bytes"],
            "disabled_absent": True,
            "workspace_authority_absent": True,
            "missing_status": 1,
            "prior_failure_status": 17,
        }
        if spec != expected_spec:
            raise ProviderFreeError("invalid Reconstruction Spec persistence evidence")
        persisted_spec = exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE
        if schema == EVIDENCE_SCHEMA_V3 and persisted_spec.read_bytes() != SPEC_FINAL_BYTES:
            raise ProviderFreeError("persisted Reconstruction Spec bytes changed")
        if schema == EVIDENCE_SCHEMA_V4 and (not isinstance(spec["updated_bytes"], int) or spec["updated_bytes"] <= 0 or persisted_spec.stat().st_size != spec["updated_bytes"]):
            raise ProviderFreeError("persisted Reconstruction Spec size mismatch")
    if schema == EVIDENCE_SCHEMA_V4:
        binding = evidence["spec_region_binding"]
        if not isinstance(binding, dict) or set(binding) != {"region_id", "cycles", "negative_cases", "authority_absent"} or binding.get("region_id") != "component.primary" or binding.get("cycles") != 2 or binding.get("authority_absent") is not True:
            raise ProviderFreeError("invalid Spec Region binding evidence")
        negatives = binding["negative_cases"]
        if not isinstance(negatives, list) or len(negatives) != 2 or [item.get("case") for item in negatives] != ["unknown_id", "zero_overlap"] or any(item.get("error") != "supervisor_failure" or item.get("attempt_created") is not False or item.get("public_no_leak") is not True for item in negatives):
            raise ProviderFreeError("invalid Spec Region negative evidence")
    return evidence_path, artifact_manifest_path


def _validate_v5_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    schema: str = EVIDENCE_SCHEMA_V5,
    authoring_python: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES or artifact_manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProviderFreeError("v5 artifact too large")
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("v5 evidence missing or invalid") from exc
    required = {
        "schema", "identity", "scenario", "gate_passed", "selection", "steps",
        "graph", "cycles", "previews", "mcp", "workspace_validation",
        "module_paths", "runtime", "final", "spec_persistence",
        "spec_region_binding", "directional_projection",
    }
    if schema in {EVIDENCE_SCHEMA_V6, EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        required.add("authoring_probe")
    if schema in {EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        required.add("target_paging")
    if schema in {EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        required.add("target_section_observation")
    if schema in {EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        required.add("client_transport")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != required
        or evidence.get("schema") != schema
        or evidence.get("identity") != expected_identity(record)
        or evidence.get("scenario") != SCENARIO
        or evidence.get("gate_passed") is not True
    ):
        raise ProviderFreeError("invalid v5 evidence shape")
    expected_manifest = {
        "schema": f"text-to-cad.provider-free-artifact-manifest/{10 if schema == EVIDENCE_SCHEMA_V10 else 9 if schema == EVIDENCE_SCHEMA_V9 else 8 if schema == EVIDENCE_SCHEMA_V8 else 7 if schema == EVIDENCE_SCHEMA_V7 else 6 if schema == EVIDENCE_SCHEMA_V6 else 5}",
        "final_status": 0,
        "identity": expected_identity(record),
        "evidence": {"path": evidence_path.name},
    }
    if manifest != expected_manifest:
        raise ProviderFreeError("invalid v5 manifest")

    directional = evidence["directional_projection"]
    if not isinstance(directional, dict) or set(directional) != {
        "schema", "partition_profile", "ordering_profile", "active_depth",
        "exact_metric", "canceled_legacy_prefixes", "public", "selected",
    }:
        raise ProviderFreeError("invalid directional evidence shape")
    metric = directional["exact_metric"]
    public = directional["public"]
    selected = directional["selected"]
    if (
        directional.get("schema") != "text-to-cad.directional-active-depth-evidence/1"
        or directional.get("partition_profile") != "repair_target_partition/3"
        or directional.get("ordering_profile") != "repair_target_display/2"
        or type(directional.get("active_depth")) is not int
        or directional["active_depth"] <= 0
        or type(directional.get("canceled_legacy_prefixes")) is not int
        or directional["canceled_legacy_prefixes"] <= 0
        or not isinstance(metric, dict)
        or set(metric) != {"missing", "excess", "total", "published_interior_total"}
        or any(type(metric[key]) is not int or metric[key] < 0 for key in metric)
        or metric["total"] != metric["missing"] + metric["excess"]
        or metric["published_interior_total"] != metric["total"]
        or not isinstance(public, dict)
        or set(public) != {"schema", "returned", "total", "kinds", "closed_fields", "items"}
        or public.get("schema") != "mesh-to-cad.decision-facts/2"
        or public.get("closed_fields") is not True
        or type(public.get("returned")) is not int
        or type(public.get("total")) is not int
        or public["total"] != metric["total"]
        or public["returned"] != min(public["total"], 8)
        or not isinstance(public.get("kinds"), list)
        or not public["kinds"]
        or any(kind not in {"missing", "excess", "exterior"} for kind in public["kinds"])
        or not isinstance(public.get("items"), list)
        or any(not isinstance(item, dict) or set(item) != {"rank", "kind", "bounds_canonical"} for item in public["items"])
        or not isinstance(selected, dict)
        or set(selected) != {"rank", "kind", "bounds_canonical", "private_kind", "private_identity_count", "mask_active_cell_count", "mask_opposite_support_count", "region_diff_identity_count"}
        or selected.get("rank") != (8 if schema in {EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10} else 0)
        or selected.get("kind") not in {"missing", "excess"}
        or selected.get("private_kind") != "interior"
        or selected.get("private_identity_count") != 1
        or selected.get("mask_active_cell_count") != 1
        or selected.get("mask_opposite_support_count") != 0
        or selected.get("region_diff_identity_count") != 1
    ):
        raise ProviderFreeError("invalid directional evidence")

    steps = evidence["steps"]
    cycles = evidence["cycles"]
    selection = evidence["selection"]
    if not isinstance(steps, dict) or set(steps) != {"step_zero", "repair_a", "repair_b"} or not isinstance(cycles, dict) or set(cycles) != {"repair_a", "repair_b"}:
        raise ProviderFreeError("invalid v5 repair chain")
    for name in ("step_zero", "repair_a", "repair_b"):
        item = steps[name]
        if not isinstance(item, dict) or set(item) != {"step_handle", "ordinal", "parent", "cycle", "accepted", "frontier", "target_count", "manifest"} or item.get("accepted") is not False or item.get("manifest") != f"steps/{item.get('ordinal'):06d}/step.json":
            raise ProviderFreeError("invalid v5 step record")
        document = json.loads((exp_dir / item["manifest"]).read_text(encoding="utf-8"))
        if document.get("step") != item["ordinal"] or document.get("parent_step") != item["parent"]:
            raise ProviderFreeError("v5 step authority mismatch")
    if steps["repair_a"]["parent"] != steps["step_zero"]["ordinal"] or steps["repair_b"]["parent"] != steps["repair_a"]["ordinal"]:
        raise ProviderFreeError("v5 parent chain mismatch")
    if not isinstance(selection, dict) or selection.get("selected") not in {"step_zero", "repair_a"} or selection.get("selected_step") != steps[selection["selected"]]["ordinal"] or selection.get("repair_b_is_head") != steps["repair_b"]["ordinal"]:
        raise ProviderFreeError("v5 historical selection mismatch")
    if evidence.get("graph") != {"source": "step_parentage", "heads": [steps["repair_b"]["ordinal"]]}:
        raise ProviderFreeError("v5 graph mismatch")
    for name in ("repair_a", "repair_b"):
        cycle = cycles[name]
        if not isinstance(cycle, dict) or set(cycle) != {"ordinal", "from_step", "to_step", "selected_parent_target", "artifacts"} or cycle.get("to_step") != steps[name]["ordinal"] or cycle.get("from_step") != steps[name]["parent"]:
            raise ProviderFreeError("invalid v5 cycle record")
        target = cycle["selected_parent_target"]
        if not isinstance(target, dict) or set(target) != {"rank", "kind", "bounds_canonical"} or target.get("kind") not in {"missing", "excess", "exterior"}:
            raise ProviderFreeError("invalid v5 cycle target")
        artifacts = cycle["artifacts"]
        expected = {key: f"cycles/{cycle['to_step']:06d}/{key}.json" for key in ("plan", "assessment", "diff", "cycle", "attempt")}
        if artifacts != expected or any(not (exp_dir / relative).is_file() for relative in artifacts.values()):
            raise ProviderFreeError("v5 cycle artifacts are incomplete")
        plan = json.loads((exp_dir / artifacts["plan"]).read_text(encoding="utf-8"))
        diff = json.loads((exp_dir / artifacts["diff"]).read_text(encoding="utf-8"))
        attempt = json.loads((exp_dir / artifacts["attempt"]).read_text(encoding="utf-8"))
        if target not in plan.get("selected_targets", []) or plan.get("from_step") != cycle["from_step"] or diff.get("repair_batch", {}).get("plan_sha256") != attempt.get("plan_digest"):
            raise ProviderFreeError("v5 cycle binding mismatch")

    step0 = steps["step_zero"]["ordinal"]
    measurement_root = exp_dir / "voxblame" / "steps" / f"{step0:06d}"
    try:
        session = json.loads((exp_dir / "voxblame/session.json").read_text(encoding="utf-8"))
        measurement = json.loads((measurement_root / "measurement.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("v5 directional authority is unavailable") from exc
    if session.get("profiles", {}).get("target_partition") != "repair_target_partition/3" or measurement.get("repair_targets", {}).get("ordering_profile") != "repair_target_display/2":
        raise ProviderFreeError("v5 directional authority profile mismatch")
    active_depth = directional["active_depth"]
    errors = measurement.get("errors_by_depth")
    if not isinstance(errors, list) or len(errors) != 8 or active_depth != next((item.get("depth") for item in errors if item.get("surface_error_count")), None):
        raise ProviderFreeError("v5 Active Depth authority mismatch")
    active_metric = errors[active_depth - 1]
    if metric != {"missing": active_metric["missing_surface_count"], "excess": active_metric["excess_surface_count"], "total": active_metric["surface_error_count"], "published_interior_total": active_metric["surface_error_count"]}:
        raise ProviderFreeError("v5 exact metric was not authority-derived")
    meshscope_src = repo_root / "packages/meshscope/src"
    if os.fspath(meshscope_src) not in sys.path:
        sys.path.insert(0, os.fspath(meshscope_src))
    from meshscope.voxblame.codec import read_surface_tree

    reference = {int(code) for code in read_surface_tree(exp_dir / "voxblame/reference.vbsvo").iter_leaf_codes()}
    candidate = {int(code) for code in read_surface_tree(measurement_root / "candidate.vbsvo").iter_leaf_codes()}
    missing = {int(code) for code in read_surface_tree(measurement_root / "missing-depth8.vbsvo").iter_leaf_codes()}
    excess = {int(code) for code in read_surface_tree(measurement_root / "excess-depth8.vbsvo").iter_leaf_codes()}
    shift = 3 * (8 - active_depth)
    reference_prefixes = {code >> shift for code in reference}
    candidate_prefixes = {code >> shift for code in candidate}
    expected = sorted(
        [*((prefix, "missing") for prefix in reference_prefixes - candidate_prefixes), *((prefix, "excess") for prefix in candidate_prefixes - reference_prefixes)],
        key=lambda item: (item[0], 0 if item[1] == "missing" else 1),
    )
    canceled = {code >> shift for code in missing} & {code >> shift for code in excess}
    if len(canceled) != directional["canceled_legacy_prefixes"]:
        raise ProviderFreeError("v5 canceled-prefix evidence mismatch")
    ordered = measurement["repair_targets"].get("ordered_targets")
    if not isinstance(ordered, list):
        raise ProviderFreeError("v5 target authority is malformed")
    interior = [item for item in ordered if item.get("kind") == "interior"]
    observed: list[tuple[int, str]] = []
    projected: list[dict[str, Any]] = []
    for rank, target in enumerate(interior):
        profile = target.get("error_profile")
        if profile == {"missing_surface_count": 1, "excess_surface_count": 0, "surface_error_count": 1}:
            direction, support, opposite = "missing", missing, excess
        elif profile == {"missing_surface_count": 0, "excess_surface_count": 1, "surface_error_count": 1}:
            direction, support, opposite = "excess", excess, missing
        else:
            raise ProviderFreeError("v5 target direction is malformed")
        relative = PurePosixPath(target.get("mask", {}).get("path", ""))
        if relative.is_absolute() or ".." in relative.parts:
            raise ProviderFreeError("v5 target mask path is invalid")
        mask_cells, coarse_cells = _expand_mask_prefixes(exp_dir / Path(relative), active_depth)
        if len(coarse_cells) != 1:
            raise ProviderFreeError("v5 target mask crosses Active-Depth cells")
        prefix = next(iter(coarse_cells))
        if mask_cells != {code for code in support if code >> shift == prefix} or mask_cells & opposite or target.get("bounds_canonical") != _coarse_bounds(prefix, active_depth) or target.get("display_rank") != rank:
            raise ProviderFreeError("v5 target support, bounds, or rank is invalid")
        observed.append((prefix, direction))
        projected.append({"rank": rank, "kind": direction, "bounds_canonical": target["bounds_canonical"]})
    if observed != expected or any(item.get("kind") == "interior" for item in ordered[len(interior):]):
        raise ProviderFreeError("v5 target coverage or ordering is invalid")
    for rank, target in enumerate(ordered[len(interior):], start=len(interior)):
        if target.get("kind") != "exterior" or target.get("display_rank") != rank:
            raise ProviderFreeError("v5 exterior target order is invalid")
        projected.append({"rank": rank, "kind": "exterior", "bounds_canonical": target["bounds_canonical"]})
    if public["items"] != projected[:8] or public["total"] != len(projected) or public["returned"] != len(projected[:8]) or public["kinds"] != sorted({item["kind"] for item in projected[:8]}):
        raise ProviderFreeError("v5 public projection is not authority-derived")
    selected_rank = 8 if schema in {EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10} else 0
    if selected["rank"] != selected_rank or selected["kind"] != projected[selected_rank]["kind"] or selected["bounds_canonical"] != projected[selected_rank]["bounds_canonical"]:
        raise ProviderFreeError("v5 selected public tuple mismatch")
    raw = ordered[selected_rank]
    diff = json.loads((exp_dir / cycles["repair_a"]["artifacts"]["diff"]).read_text(encoding="utf-8"))
    resolved = diff.get("repair_batch", {}).get("selected_targets")
    if not isinstance(resolved, list) or len(resolved) != 1 or resolved[0] != {"target_key": raw["target_key"], "kind": "interior", "mask_sha256": raw["mask"]["logical_sha256"]}:
        raise ProviderFreeError("v5 Region Diff identity is not authority-bound")

    previews = evidence["previews"]
    mcp = evidence["mcp"]
    if not isinstance(previews, dict) or set(previews) != {"step_zero", "repair_a", "repair_b", "selected_reinspect"} or not isinstance(mcp, dict) or set(mcp) != set(previews):
        raise ProviderFreeError("invalid v5 preview evidence")
    for name, item in previews.items():
        if not isinstance(item, dict) or set(item) != {"path", "bytes"} or not (exp_dir / item["path"]).is_file() or (exp_dir / item["path"]).stat().st_size != item["bytes"] or mcp[name].get("image_bytes") != item["bytes"]:
            raise ProviderFreeError("v5 preview authority mismatch")
    if previews["selected_reinspect"] != previews[selection["selected"]]:
        raise ProviderFreeError("v5 selected preview mismatch")

    final = evidence["final"]
    if not isinstance(final, dict) or set(final) != {"manifest", "selected_step", "source", "measurement", "preview", "verification", "identity_bound"} or final.get("selected_step") != selection["selected_step"] or final.get("identity_bound") is not True:
        raise ProviderFreeError("invalid v5 final binding")
    for relative in ("final/manifest.json", "final/source/source/model.py", "final/measurement.json", "final/preview.json", "final/verification.json"):
        if not (exp_dir / relative).is_file():
            raise ProviderFreeError("v5 final artifact missing")
    if (exp_dir / "final/source/source/model.py").read_bytes() != (exp_dir / f"steps/{selection['selected_step']:06d}/candidate/source/model.py").read_bytes() or (exp_dir / "final/measurement.json").read_bytes() != (exp_dir / f"steps/{selection['selected_step']:06d}/measurement.json").read_bytes():
        raise ProviderFreeError("v5 final bytes are not selected-step bound")
    if evidence.get("workspace_validation") is not True:
        raise ProviderFreeError("v5 workspace validation missing")
    modules = evidence["module_paths"]
    if not isinstance(modules, dict) or set(modules) != {"product_root", "workspace", "core", "handler", "mcp", "rebuild", "geometry"} or modules.get("product_root") != "skills" or any(not isinstance(modules.get(key), str) or not modules[key].startswith("skills/") for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")):
        raise ProviderFreeError("invalid v5 module provenance")
    runtime = evidence["runtime"]
    if not isinstance(runtime, dict) or runtime.get("interpreter") != ".venv/bin/python" or runtime.get("registry") != {"schema": "mesh-to-cad.tool-registry/2", "rebuild_id": "cad.canonical-build/1", "geometry_id": "mesh-compare.voxblame/1", "authority": "installed_publish_tree", "provenance": "receipt.publish_tree"}:
        raise ProviderFreeError("invalid v5 runtime provenance")
    spec = evidence["spec_persistence"]
    if not isinstance(spec, dict) or set(spec) != {"seam", "path", "enabled_status", "updated_bytes", "disabled_absent", "workspace_authority_absent", "missing_status", "prior_failure_status"} or spec.get("seam") != "runner.persist_agent_reconstruction_spec" or spec.get("path") != runner.RECONSTRUCTION_SPEC_RELATIVE.as_posix() or spec.get("enabled_status") != 0 or type(spec.get("updated_bytes")) is not int or spec["updated_bytes"] <= 0 or spec.get("disabled_absent") is not True or spec.get("workspace_authority_absent") is not True or spec.get("missing_status") != 1 or spec.get("prior_failure_status") != 17 or not (exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE).is_file() or (exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE).stat().st_size != spec["updated_bytes"]:
        raise ProviderFreeError("invalid v5 Spec persistence")
    binding = evidence["spec_region_binding"]
    if not isinstance(binding, dict) or set(binding) != {"region_id", "cycles", "negative_cases", "authority_absent"} or binding.get("region_id") != "component.primary" or binding.get("cycles") != 2 or binding.get("authority_absent") is not True or [item.get("case") for item in binding.get("negative_cases", [])] != ["unknown_id", "zero_overlap"] or any(item.get("error") != "supervisor_failure" or item.get("attempt_created") is not False or item.get("public_no_leak") is not True for item in binding["negative_cases"]):
        raise ProviderFreeError("invalid v5 Spec Region binding")
    if schema in {EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        paging = evidence["target_paging"]
        if (
            not isinstance(paging, dict)
            or set(paging) != {"schema", "step_ordinal", "pages", "historical_reread", "selected"}
            or paging.get("schema") != "text-to-cad.repair-target-paging-evidence/1"
            or paging.get("step_ordinal") != step0
            or paging.get("selected") != projected[8]
            or not isinstance(paging.get("pages"), list)
        ):
            raise ProviderFreeError("invalid v7 target paging evidence")
        expected_pages = []
        for offset in range(0, len(projected), 8):
            page_items = projected[offset : offset + 8]
            next_offset = offset + len(page_items)
            expected_pages.append(
                {
                    "schema": "mesh-to-cad.repair-target-page/1",
                    "step_ordinal": step0,
                    "total": len(projected),
                    "returned": len(page_items),
                    "remaining": len(projected) - next_offset,
                    "offset": offset,
                    "next_offset": next_offset if next_offset < len(projected) else None,
                    "items": page_items,
                }
            )
        if len(projected) != 48 or paging["pages"] != expected_pages or paging.get("historical_reread") != expected_pages[1]:
            raise ProviderFreeError("v7 target pages are not authority-derived")
        if cycles["repair_a"]["selected_parent_target"] != projected[8]:
            raise ProviderFreeError("v7 second-page Repair selection mismatch")
    if schema in {EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9}:
        section = evidence["target_section_observation"]
        if (
            not isinstance(section, dict)
            or set(section) != {
                "schema", "observed_ranks", "selected_rank", "step_zero",
                "historical_reread", "repair_a",
                "authority_recomputed", "non_tied",
            }
            or section.get("schema")
            != "text-to-cad.target-section-observation-evidence/1"
            or section.get("authority_recomputed") is not True
            or section.get("historical_reread") != section.get("step_zero")
            or section.get("selected_rank") != section.get("step_zero", {}).get("rank")
            or not isinstance(section.get("observed_ranks"), list)
            or section["observed_ranks"] != list(range(section["selected_rank"] + 1))
        ):
            raise ProviderFreeError("invalid v8 Target Section evidence")
        public_text = json.dumps(
            {
                "step_zero": section["step_zero"],
                "historical_reread": section["historical_reread"],
                "repair_a": section["repair_a"],
                "exterior": section.get("exterior", {}).get("observation"),
            },
            sort_keys=True,
        ).lower()
        if any(
            token in public_text
            for token in (
                "target_key", "mask", "depth8", "component", "capability",
                "handle", '"path"', "/users/", "/home/",
            )
        ):
            raise ProviderFreeError("v8 Target Section evidence leaked private detail")
        if os.fspath(meshscope_src) not in sys.path:
            sys.path.insert(0, os.fspath(meshscope_src))
        from meshscope import target_section_profile

        for name, ordinal in (
            ("step_zero", step0),
            ("repair_a", steps["repair_a"]["ordinal"]),
        ):
            observed_section = section[name]
            if (
                not isinstance(observed_section, dict)
                or set(observed_section) != {"schema", "rank", "reference", "candidate"}
                or observed_section.get("schema")
                != "mesh-to-cad.target-section-observation/1"
                or type(observed_section.get("rank")) is not int
            ):
                raise ProviderFreeError("invalid Target Section result shape")
            target = _authority_public_target(
                exp_dir, ordinal, observed_section["rank"]
            )
            expected_observation = {
                "schema": "mesh-to-cad.target-section-observation/1",
                "rank": observed_section["rank"],
                "reference": target_section_profile(
                    exp_dir / "input/reference.ply", target["bounds_canonical"]
                ),
                "candidate": target_section_profile(
                    exp_dir / f"steps/{ordinal:06d}/candidate/candidate.glb",
                    target["bounds_canonical"],
                ),
            }
            if observed_section != expected_observation:
                raise ProviderFreeError(
                    "Target Section response differs from committed authority"
                )
        discriminator = _non_tied_profile(section["step_zero"])
        if discriminator is None:
            discriminator = _non_tied_profile(section["repair_a"])
        if discriminator is None or section.get("non_tied") != discriminator:
            raise ProviderFreeError("Target Section strict normal discriminator failed")
    if schema == EVIDENCE_SCHEMA_V10:
        section = evidence["target_section_observation"]
        if (
            not isinstance(section, dict)
            or set(section) != {
                "schema", "observed_ranks", "selected_rank", "step_zero",
                "historical_reread", "repair_a", "exterior",
                "authority_recomputed", "non_tied",
            }
            or section.get("schema")
            != "text-to-cad.target-section-observation-evidence/2"
            or section.get("authority_recomputed") is not True
            or section.get("historical_reread") != section.get("step_zero")
            or section.get("selected_rank") != section.get("step_zero", {}).get("rank")
            or not isinstance(section.get("observed_ranks"), list)
            or section["observed_ranks"] != list(range(section["selected_rank"] + 1))
        ):
            raise ProviderFreeError("invalid v10 Target Section evidence")
        public_text = json.dumps(
            {
                "step_zero": section["step_zero"],
                "historical_reread": section["historical_reread"],
                "repair_a": section["repair_a"],
                "exterior": section.get("exterior", {}).get("observation"),
            },
            sort_keys=True,
        ).lower()
        if any(
            token in public_text
            for token in (
                "target_key", "mask", "depth8", "component", "capability",
                "handle", '"path"', "/users/", "/home/", '"depth"',
                '"kind"', '"bounds_canonical"',
            )
        ):
            raise ProviderFreeError("v10 Target Section evidence leaked private detail")
        if os.fspath(meshscope_src) not in sys.path:
            sys.path.insert(0, os.fspath(meshscope_src))
        from meshscope import target_section_profile

        for name, ordinal in (
            ("step_zero", step0),
            ("repair_a", steps["repair_a"]["ordinal"]),
        ):
            observed_section = section[name]
            if (
                not isinstance(observed_section, dict)
                or set(observed_section) != {"schema", "rank", "reference", "candidate"}
                or observed_section.get("schema")
                != "mesh-to-cad.target-section-observation/2"
                or type(observed_section.get("rank")) is not int
            ):
                raise ProviderFreeError("invalid v10 Target Section result")
            target = _authority_public_target(exp_dir, ordinal, observed_section["rank"])
            for side, path in (
                ("reference", exp_dir / "input/reference.ply"),
                (
                    "candidate",
                    exp_dir / f"steps/{ordinal:06d}/candidate/candidate.glb",
                ),
            ):
                if (
                    not isinstance(observed_section[side], dict)
                    or set(observed_section[side]) != {"core", "neighborhood"}
                    or observed_section[side]["neighborhood"] is None
                    or observed_section[side]["core"]
                    != target_section_profile(path, target["bounds_canonical"])
                ):
                    raise ProviderFreeError(
                        "v10 Target Section core differs from committed authority"
                    )
        discriminator = _non_tied_profile(section["step_zero"])
        if discriminator is None:
            discriminator = _non_tied_profile(section["repair_a"])
        if discriminator is None or section.get("non_tied") != discriminator:
            raise ProviderFreeError("v10 Target Section strict normal discriminator failed")
        exterior = section["exterior"]
        if (
            not isinstance(exterior, dict)
            or set(exterior) != {
                "workspace", "step_ordinal", "public_page", "public_item",
                "observation", "authority_recomputed",
            }
            or exterior.get("workspace") != "exterior-probe"
            or exterior.get("authority_recomputed") is not True
            or not isinstance(exterior.get("public_page"), dict)
            or exterior.get("public_item") not in exterior["public_page"].get("items", [])
        ):
            raise ProviderFreeError("invalid v10 exterior Target Section evidence")
        exterior_item = exterior["public_item"]
        if (
            not isinstance(exterior_item, dict)
            or set(exterior_item) != {"rank", "kind", "bounds_canonical"}
            or exterior_item.get("kind") != "exterior"
        ):
            raise ProviderFreeError("v10 exterior public rank is invalid")
        exterior_exp = exp_dir / "exterior-probe"
        exterior_observation = exterior["observation"]
        if (
            not isinstance(exterior_observation, dict)
            or set(exterior_observation) != {"schema", "rank", "reference", "candidate"}
            or exterior_observation.get("schema")
            != "mesh-to-cad.target-section-observation/2"
            or exterior_observation.get("rank") != exterior_item["rank"]
            or any(
                not isinstance(exterior_observation.get(side), dict)
                or set(exterior_observation[side]) != {"core", "neighborhood"}
                or exterior_observation[side]["neighborhood"] is not None
                for side in ("reference", "candidate")
            )
        ):
            raise ProviderFreeError("v10 exterior core/null authority mismatch")
        for side, path in (
            ("reference", exterior_exp / "input/reference.ply"),
            (
                "candidate",
                exterior_exp
                / f"steps/{exterior['step_ordinal']:06d}/candidate/candidate.glb",
            ),
        ):
            if exterior_observation[side]["core"] != target_section_profile(
                path, exterior_item["bounds_canonical"]
            ):
                raise ProviderFreeError("v10 exterior core differs from authority")
    if schema == EVIDENCE_SCHEMA_V9:
        transport = evidence["client_transport"]
        if transport != {
            "schema": "text-to-cad.client-transport-evidence/1",
            "transport": "stdin_heredoc",
            "exit_status": 0,
            "response_schema": "mesh-to-cad.agent-response/4",
            "intent": "workspace_status",
            "invalid_request": False,
        }:
            raise ProviderFreeError("invalid v9 fixed-client transport evidence")
    if schema == EVIDENCE_SCHEMA_V10:
        transport = evidence["client_transport"]
        if transport != {
            "schema": "text-to-cad.client-transport-evidence/1",
            "transport": "stdin_heredoc",
            "exit_status": 0,
            "response_schema": "mesh-to-cad.agent-response/5",
            "intent": "workspace_status",
            "invalid_request": False,
        }:
            raise ProviderFreeError("invalid v10 fixed-client transport evidence")
    if schema in {EVIDENCE_SCHEMA_V6, EVIDENCE_SCHEMA_V7, EVIDENCE_SCHEMA_V8, EVIDENCE_SCHEMA_V9, EVIDENCE_SCHEMA_V10}:
        if authoring_python is None or environ is None:
            raise ProviderFreeError("v6 authoring observer runtime is unavailable")
        _validate_authoring_probe(
            exp_dir,
            evidence["authoring_probe"],
            observer_python=authoring_python,
            environ=environ,
        )
    return evidence_path, artifact_manifest_path


def _validate_authoring_probe(
    exp_dir: Path,
    probe: Any,
    *,
    observer_python: Path,
    environ: Mapping[str, str],
) -> None:
    if (
        not isinstance(probe, dict)
        or set(probe) != {"schema", "runtime", "cases"}
        or probe.get("schema") != "text-to-cad.candidate-authoring-probe/1"
        or not isinstance(probe.get("runtime"), dict)
        or set(probe["runtime"]) != {"kind", "identity"}
        or probe["runtime"].get("kind") != "candidate-runtime"
        or not isinstance(probe["runtime"].get("identity"), str)
        or not probe["runtime"]["identity"]
        or not isinstance(probe.get("cases"), dict)
        or set(probe["cases"]) != {"bad_control", "safe_a", "safe_b"}
    ):
        raise ProviderFreeError("invalid v6 authoring probe shape")
    cases = probe["cases"]
    expected_inputs = {
        "bad_control": {},
        "safe_a": {"chord": b"1.2\n", "wing-z": b"0.35\n"},
        "safe_b": {"chord": b"1.6\n", "wing-z": b"0.5\n"},
    }
    def expected_recipe_binding(input_names: Sequence[str]) -> dict[str, Any]:
        recipe_inputs = [
            {"id": "source", "role": "canonical-cad-source", "path": "source/model.py"}
        ]
        build_inputs = [
            {"id": "input:source", "role": "canonical-cad-source", "path": "source/model.py"}
        ]
        argv_template = [
            "build", "--source", "{source}", "--output-dir", "{outputDirectory}"
        ]
        placeholders: dict[str, Any] = {
            "source": {"kind": "input", "inputId": "source"},
            "outputDirectory": {"kind": "output-directory"},
            "manifest": {"kind": "manifest", "path": "build.json"},
        }
        for index, input_name in enumerate(input_names, start=1):
            input_id = f"input-{index}"
            input_path = f"source/{input_name}.txt"
            recipe_inputs.append(
                {"id": input_id, "role": "declared-source-input", "path": input_path}
            )
            build_inputs.append(
                {"id": f"input:{input_id}", "role": "declared-source-input", "path": input_path}
            )
            argv_template.extend(("--input", f"{{input:{input_id}}}"))
            placeholders[f"input:{input_id}"] = {"kind": "input", "inputId": input_id}
        return {
            "schema": "mesh-to-cad.rebuild-recipe/1",
            "executable": "cad.canonical-build/1",
            "entrypoint": "scripts/canonical-build",
            "build_inputs": build_inputs,
            "recipe_inputs": recipe_inputs,
            "argv_template": argv_template,
            "placeholders": placeholders,
        }

    observed: dict[str, dict[str, Any]] = {}
    for name, record in cases.items():
        if not isinstance(record, dict) or set(record) != {"source", "inputs", "step", "build", "observed"}:
            raise ProviderFreeError("invalid v6 authoring probe case")
        expected_source = BAD_AUTHORING_SOURCE if name == "bad_control" else SAFE_AUTHORING_SOURCE
        prefix = f"run/authoring-probe/{name}"
        if (
            record["source"] != f"{prefix}/source/model.py"
            or record["step"] != f"{prefix}/output/canonical.step"
            or record["build"] != f"{prefix}/output/build.json"
        ):
            raise ProviderFreeError("v6 authoring artifact path mismatch")
        source = exp_dir / str(record["source"])
        inputs = record["inputs"]
        if source.read_text(encoding="utf-8") != expected_source or not isinstance(inputs, dict) or set(inputs) != set(expected_inputs[name]):
            raise ProviderFreeError("v6 authoring source authority mismatch")
        for input_name, expected_bytes in expected_inputs[name].items():
            if inputs[input_name] != f"{prefix}/source/{input_name}.txt":
                raise ProviderFreeError("v6 authoring input path mismatch")
            if (exp_dir / str(inputs[input_name])).read_bytes() != expected_bytes:
                raise ProviderFreeError("v6 authoring input authority mismatch")
        derived = _read_authoring_artifacts(
            exp_dir,
            record,
            observer_python=observer_python,
            environ=environ,
        )
        reported = record["observed"]
        if (
            not isinstance(reported, dict)
            or set(reported) != set(derived)
            or reported.get("solid_count") != derived["solid_count"]
            or reported.get("build_version") != derived["build_version"]
            or reported.get("recipe_binding") != derived["recipe_binding"]
            or not isinstance(reported.get("bounds"), dict)
            or set(reported["bounds"]) != {"min", "max"}
            or any(
                not isinstance(reported["bounds"].get(side), list)
                or len(reported["bounds"][side]) != 3
                or any(
                    type(actual) not in {int, float}
                    or not math.isclose(
                        actual, expected, rel_tol=0.0, abs_tol=1e-12
                    )
                    for actual, expected in zip(
                        reported["bounds"][side], derived["bounds"][side]
                    )
                )
                for side in ("min", "max")
            )
        ):
            raise ProviderFreeError("v6 authoring observation was not artifact-derived")
        if derived["recipe_binding"] != expected_recipe_binding(tuple(expected_inputs[name])):
            raise ProviderFreeError("v6 authoring recipe input binding mismatch")
        observed[name] = derived
    if observed["bad_control"]["solid_count"] != 2 or any(
        observed[name]["solid_count"] != 3 for name in ("safe_a", "safe_b")
    ):
        raise ProviderFreeError("v6 authoring solid discriminator failed")
    if observed["safe_a"]["bounds"] == observed["safe_b"]["bounds"]:
        raise ProviderFreeError("v6 safe authoring edit was not observable")
    versions = {json.dumps(item["build_version"], sort_keys=True) for item in observed.values()}
    version = observed["safe_a"]["build_version"]
    if (
        len(versions) != 1
        or version.get("adapter_id") != "cad.canonical-build/1"
        or version.get("adapter_version") != 1
        or not isinstance(version.get("build123d"), str)
        or not version["build123d"]
    ):
        raise ProviderFreeError("v6 authoring build version mismatch")


def _run_draft_evaluation_probe(
    workspace: Path,
    fixture: Path,
    *,
    trusted: Path,
    published_rebuild: Path,
    published_geometry: Path,
    registry: Path,
    sidecar: Any,
    candidate_runtime: Path,
) -> dict[str, Any]:
    """Exercise the V14 Repair draft contract over the real socket."""

    runner.prepare_exp(workspace)
    runner.prepare_and_initialize_workspace(workspace, fixture, trusted_tools_root=trusted)
    candidate_root = workspace.parent / f".agent-candidate-draft-{os.getpid()}"
    counters = {"repair_builds": 0, "provider_calls": 0}
    first_evaluation_ready = threading.Event()
    release_first_evaluation = threading.Event()

    def repair_provider(request: Any) -> None:
        counters["provider_calls"] += 1
        if counters["provider_calls"] == 4:
            raise RuntimeError("admitted provider failure")
        runner.real_repair_evidence_provider(
            request,
            capability_path=sidecar.capability_dir / "runtime.json",
            meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
            meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
        )
        if counters["provider_calls"] == 1:
            first_evaluation_ready.set()
            if not release_first_evaluation.wait(timeout=10):
                raise RuntimeError("in-flight abandonment probe timed out")

    supervisor = WorkspaceSupervisor(
        workspace,
        bind_reference=True,
        candidate_root=candidate_root,
        rebuild_entrypoint=published_rebuild,
        geometry_entrypoint=published_geometry,
        tool_registry=registry,
        browser_runtime_capability=sidecar.capability_dir / "runtime.json",
        candidate_runtime=candidate_runtime,
        trusted_tools_root=trusted,
        trusted_product_root=trusted,
        reconstruction_spec=True,
        step_zero_evidence_provider=lambda request: runner.real_step_zero_evidence_provider(
            request,
            capability_path=sidecar.capability_dir / "runtime.json",
            meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
            meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
        ),
        repair_evidence_provider=repair_provider,
    )
    canonical_build = supervisor._build_canonical_candidate

    def counted_build(context: Any) -> Any:
        if context.intended_step > 0:
            counters["repair_builds"] += 1
        return canonical_build(context)

    supervisor._build_canonical_candidate = counted_build
    socket_root = Path(tempfile.mkdtemp(prefix="ttc-draft-", dir="/tmp"))
    bridge = _FailedSubmitWriteBridge(
        supervisor.agent_surface(),
        socket_root / "surface.sock",
        trusted_product_root=trusted,
    )

    def call(intent: str, args: Mapping[str, Any]) -> dict[str, Any]:
        frame = _surface_call(
            bridge.socket_path,
            {"schema": "mesh-to-cad.agent-intent/1", "intent": intent, "args": dict(args)},
        )
        _public(frame)
        response = frame.get("response") if frame.get("ok") is True else None
        if not isinstance(response, dict) or response.get("schema") != "mesh-to-cad.agent-response/7":
            raise ProviderFreeError(f"draft probe {intent} failed")
        return response["result"]

    try:
        bridge.start()
        bootstrap = supervisor.agent_bootstrap_contract()
        wh = bootstrap["workspace_handle"]
        ph = bootstrap["plan_handle"]
        plan = candidate_root / "plan.json"
        _json(plan, {"schema": "mesh-to-cad.initial-plan/1", "summary": "draft evaluation probe"})
        started0 = call("start_attempt", {"workspace_handle": wh, "plan_handle": ph})
        _source(candidate_root / "work/source/model.py", STEP_ZERO_WIDTH)
        call("run_candidate_tool", {"workspace_handle": wh, "attempt_handle": started0["attempt_handle"], "candidate_handle": started0["candidate_handle"], "operation_handle": started0["capability_bundle_handle"]})
        step0 = call("submit_step_zero", {"workspace_handle": wh, "attempt_handle": started0["attempt_handle"], "candidate_handle": started0["candidate_handle"]})
        drain = _run_draft_drain_probe(
            workspace,
            trusted=trusted,
            published_rebuild=published_rebuild,
            published_geometry=published_geometry,
            registry=registry,
            sidecar=sidecar,
            candidate_runtime=candidate_runtime,
        )
        page = _read_target_page(bridge.socket_path, step0["step_handle"], 0)
        if not page["items"]:
            raise ProviderFreeError("draft probe Step 0 has no public target")
        target = page["items"][0]
        _spec(candidate_root / "reconstruction-spec.json", "component.primary", target["bounds_canonical"])
        _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [target], "planned_edits": [{"edit_key": "draft-probe", "target_ranks": [target["rank"]], "spec_region_id": "component.primary", "description": "compare immutable Repair drafts"}], "rationale": "exercise the bounded draft evaluator", "preview_observation": "Step 0 preview and target were inspected"})
        committed_before = _committed_authority_snapshot(workspace)

        attempt1 = call("start_attempt", {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": step0["step_handle"]})
        if attempt1["draft_budget"] != {"used": 0, "remaining": 8, "maximum": 8}:
            raise ProviderFreeError("draft budget did not start at eight")
        if attempt1["permitted_next_intents"] != ["evaluate_repair_draft", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("Repair start exposed impossible intents")
        _source(candidate_root / "work/source/model.py", REPAIR_B_WIDTH)
        _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": 0, "to_step": 1, "preview_observation": "Attempt 1 draft underfits the parent.", "summary": "Attempt 1 draft probe."})
        invalid = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt1["attempt_handle"], "candidate_handle": attempt1["candidate_handle"], "evaluation_ticket": "ticket:invalid"})
        if invalid != {"state": "failed", "classification": "invalid_ticket", "permitted_next_intents": invalid["permitted_next_intents"]}:
            raise ProviderFreeError("invalid evaluation ticket was admitted")
        concurrent_results: list[dict[str, Any]] = []
        concurrent_errors: list[BaseException] = []
        barrier = threading.Barrier(3)

        def evaluate_same_ticket() -> None:
            try:
                barrier.wait()
                concurrent_results.append(call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt1["attempt_handle"], "candidate_handle": attempt1["candidate_handle"], "evaluation_ticket": attempt1["evaluation_ticket"]}))
            except BaseException as error:
                concurrent_errors.append(error)

        workers = [threading.Thread(target=evaluate_same_ticket) for _ in range(2)]
        for worker in workers:
            worker.start()
        barrier.wait()
        first_evaluation_ready.wait()
        session1 = supervisor._draft_sessions[1]
        active_attempts_before_failed_abandon = set(session1.attempts)
        original_record = supervisor.workspace_api.record_attempt
        record_failed = False

        def fail_record_once(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
            nonlocal record_failed
            if not record_failed:
                record_failed = True
                raise RuntimeError("injected record failure")
            return original_record(*args, **kwargs)

        supervisor.workspace_api.record_attempt = fail_record_once
        failed_abandon = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "abandon_repair_attempt", "args": {"workspace_handle": wh, "attempt_handle": attempt1["attempt_handle"]}})
        if (
            failed_abandon.get("ok") is not False
            or attempt1["attempt_handle"] not in supervisor.registry._records
            or set(session1.attempts) != active_attempts_before_failed_abandon
        ):
            raise ProviderFreeError("failed abandonment mutated the active Repair session")
        abandon_results: list[dict[str, Any]] = []
        abandon_errors: list[BaseException] = []

        def abandon_in_flight() -> None:
            try:
                abandon_results.append(call("abandon_repair_attempt", {"workspace_handle": wh, "attempt_handle": attempt1["attempt_handle"]}))
            except BaseException as error:
                abandon_errors.append(error)

        abandon_worker = threading.Thread(target=abandon_in_flight)
        abandon_worker.start()
        time.sleep(0.2)
        if not abandon_worker.is_alive() or not any(supervisor._staging_root.glob("draft-*")):
            raise ProviderFreeError("abandonment did not wait for the in-flight evaluation")
        release_first_evaluation.set()
        for worker in workers:
            worker.join()
        abandon_worker.join()
        supervisor.workspace_api.record_attempt = original_record
        if concurrent_errors or len(concurrent_results) != 2 or concurrent_results[0] != concurrent_results[1] or counters != {"repair_builds": 1, "provider_calls": 1}:
            raise ProviderFreeError("concurrent evaluation was not single-flight")
        if abandon_errors or abandon_results != [{"state": "abandoned", "permitted_next_intents": ["start_attempt", "inspect_repair_targets", "select_and_finalize", "workspace_status"]}]:
            raise ProviderFreeError("in-flight abandonment did not complete")
        evaluation1 = concurrent_results[0]
        if evaluation1["permitted_next_intents"] != ["evaluate_repair_draft", "submit_repair", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("successful evaluation intents are not state-derived")
        replay1 = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "evaluate_repair_draft", "args": {"workspace_handle": wh, "attempt_handle": attempt1["attempt_handle"], "candidate_handle": attempt1["candidate_handle"], "evaluation_ticket": attempt1["evaluation_ticket"]}})
        if replay1.get("ok") is not False:
            raise ProviderFreeError("abandoned evaluation handle remained callable")
        stale_ticket = evaluation1["next_evaluation_ticket"]
        if any(supervisor._staging_root.glob("draft-*")):
            raise ProviderFreeError("in-flight draft stage survived abandonment")
        try:
            supervisor.registry.resolve(evaluation1["draft_handle"], "draft")
        except SupervisorError:
            pass
        else:
            raise ProviderFreeError("in-flight draft handle survived abandonment")

        selection = candidate_root / "selection.json"
        notes = candidate_root / "notes.md"
        _json(selection, {"schema": "mesh-to-cad.agent-selection-claim/1", "preview_observation": "Step 0 remains unaccepted after draft-only evaluation.", "stop_reason": "no_feasible_strategy", "conflict": False, "conflict_details": None, "rationale": "The draft feedback alone cannot authorize an infeasible stop."})
        notes.write_text("## Input\n\nProvider-free fixture.\n## Modeling Intent\n\nDraft gate probe.\n## Preserved Structural Features\n\nPrimary box.\n## Omitted Surface Details\n\nResidual surfaces.\n## Repair Trajectory\n\nDraft feedback was evaluated.\n## Final Selection\n\nStep 0 for gate probing.\n## Verification\n\nCommitted measurement only.\n", encoding="utf-8")
        blocked = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "select_and_finalize", "args": {"workspace_handle": wh, "step_handle": step0["step_handle"], "selection_handle": bootstrap["selection_handle"], "notes_handle": bootstrap["notes_handle"]}})
        blocked_error = blocked.get("error")
        if blocked.get("ok") is not False or not isinstance(blocked_error, Mapping) or blocked_error.get("classification") != "state_conflict":
            raise ProviderFreeError("draft feedback authorized no-feasible finalization")
        occupancy = _read_target_section(bridge.socket_path, step0["step_handle"], target["rank"])

        attempt2 = call("start_attempt", {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": step0["step_handle"]})
        if attempt2["draft_budget"] != {"used": 1, "remaining": 7, "maximum": 8}:
            raise ProviderFreeError("draft budget did not survive Attempt abandonment")
        if attempt2["permitted_next_intents"] != ["evaluate_repair_draft", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("second Repair start exposed impossible intents")
        stale = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": stale_ticket})
        if stale.get("classification") != "stale_ticket":
            raise ProviderFreeError("abandoned Attempt ticket was not stale")

        _source(candidate_root / "work/source/model.py", REPAIR_A_WIDTH)
        assessment_a = {"schema": "mesh-to-cad.assessment/1", "from_step": 0, "to_step": 1, "preview_observation": "Draft A expands toward the parent reference.", "summary": "Retain deterministic Draft A."}
        _json(candidate_root / "work/assessment.json", assessment_a)
        draft_a = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": attempt2["evaluation_ticket"]})
        draft_a_counts = dict(counters)
        draft_a_replay = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": attempt2["evaluation_ticket"]})
        if draft_a_replay != draft_a or counters != draft_a_counts:
            raise ProviderFreeError("completed evaluation ticket did not replay exactly")
        if draft_a["permitted_next_intents"] != ["evaluate_repair_draft", "submit_repair", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("retained draft did not enable submission")
        prepared_a = supervisor.registry.resolve(draft_a["draft_handle"], "draft")
        if not isinstance(prepared_a, Mapping):
            raise ProviderFreeError("Draft A was not retained")
        parent_measurement = json.loads((workspace / "voxblame/steps/000000/measurement.json").read_text(encoding="utf-8"))
        draft_measurement_path = Path(prepared_a["voxblame_step"]) / "measurement.json"
        draft_measurement = json.loads(draft_measurement_path.read_text(encoding="utf-8"))
        if draft_a["feedback"] != _draft_feedback_authority(parent_measurement, draft_measurement):
            raise ProviderFreeError("draft feedback differs from independent authority")
        response_bytes = len(json.dumps(draft_a["feedback"], sort_keys=True, separators=(",", ":")).encode("utf-8"))
        frozen_a = {
            "source": (Path(prepared_a["candidate"]) / "source/model.py").read_bytes(),
            "mesh": Path(prepared_a["candidate_mesh"]).read_bytes(),
            "assessment": Path(prepared_a["assessment"]).read_bytes(),
            "measurement": draft_measurement_path.read_bytes(),
            "preview_json": (Path(prepared_a["preview"]) / "preview.json").read_bytes(),
            "preview_png": (Path(prepared_a["preview"]) / "preview.png").read_bytes(),
            "diff": Path(prepared_a["region_diff"]).read_bytes(),
            "source_changes": Path(prepared_a["source_changes"]).read_bytes(),
        }

        original_prepare = supervisor.workspace_api.prepare_repair_draft

        def prepare_with_exterior(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
            prepared = dict(original_prepare(*args, **kwargs))
            feedback = json.loads(json.dumps(prepared["feedback"]))
            feedback["target_change_preview"]["new"] = {
                "total": 1,
                "returned": 1,
                "remaining": 0,
                "items": [{"kind": "exterior", "bounds_canonical": {"min": [-0.5, -0.5, -0.5], "max": [-0.25, -0.25, -0.25]}}],
            }
            prepared["feedback"] = feedback
            return prepared

        supervisor.workspace_api.prepare_repair_draft = prepare_with_exterior
        malformed = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "evaluate_repair_draft", "args": {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": draft_a["next_evaluation_ticket"]}})
        supervisor.workspace_api.prepare_repair_draft = original_prepare
        if malformed.get("ok") is not False:
            raise ProviderFreeError("exterior draft feedback was Agent-visible")
        session2 = supervisor._draft_sessions[1]
        malformed_entry = session2.tickets[draft_a["next_evaluation_ticket"]]
        post_malformed_ticket = malformed_entry["result"]["next_evaluation_ticket"]

        _source(candidate_root / "work/source/model.py", REPAIR_C_WIDTH)
        _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": 0, "to_step": 1, "preview_observation": "Draft B differs from retained Draft A.", "summary": "Mutated current candidate B."})
        failed = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": post_malformed_ticket})
        if failed.get("classification") != "admitted_failure" or failed.get("subtype") != "provider_execution_failed":
            raise ProviderFreeError("provider failure did not consume one closed draft slot")
        failed_counts = dict(counters)
        failed_replay = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": post_malformed_ticket})
        if failed_replay != failed or counters != failed_counts:
            raise ProviderFreeError("completed evaluation failure did not replay exactly")
        if failed["permitted_next_intents"] != ["evaluate_repair_draft", "submit_repair", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("admitted failure hid a retained draft")
        ticket = failed["next_evaluation_ticket"]
        admitted = 4
        last = failed
        while admitted < 8:
            last = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": ticket})
            admitted += 1
            ticket = last["next_evaluation_ticket"]
        if ticket is not None or counters != {"repair_builds": 8, "provider_calls": 8}:
            raise ProviderFreeError("draft admission budget/count discriminator failed")
        if last["permitted_next_intents"] != ["submit_repair", "abandon_repair_attempt", "workspace_status"]:
            raise ProviderFreeError("exhausted draft budget exposed impossible intents")
        exhausted_invalid = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": "ticket:ninth"})
        if exhausted_invalid.get("classification") != "invalid_ticket":
            raise ProviderFreeError("a ninth evaluation ticket was admitted")
        if _committed_authority_snapshot(workspace) != committed_before:
            raise ProviderFreeError("draft evaluation mutated committed authority")
        before_submit_counts = dict(counters)
        original_publish_cycle = supervisor.workspace_api.publish_cycle
        publish_failed = False

        def fail_publish_cycle_once(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
            nonlocal publish_failed
            if not publish_failed:
                publish_failed = True
                raise RuntimeError("injected W1 publication failure")
            return original_publish_cycle(*args, **kwargs)

        supervisor.workspace_api.publish_cycle = fail_publish_cycle_once
        failed_publish = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_repair", "args": {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "draft_handle": draft_a["draft_handle"]}})
        retained_after_failure = {
            "source": (Path(prepared_a["candidate"]) / "source/model.py").read_bytes(),
            "mesh": Path(prepared_a["candidate_mesh"]).read_bytes(),
            "assessment": Path(prepared_a["assessment"]).read_bytes(),
            "measurement": draft_measurement_path.read_bytes(),
            "preview_json": (Path(prepared_a["preview"]) / "preview.json").read_bytes(),
            "preview_png": (Path(prepared_a["preview"]) / "preview.png").read_bytes(),
            "diff": Path(prepared_a["region_diff"]).read_bytes(),
            "source_changes": Path(prepared_a["source_changes"]).read_bytes(),
        }
        if failed_publish.get("ok") is not False or retained_after_failure != frozen_a or supervisor.registry.resolve(draft_a["draft_handle"], "draft") is not prepared_a:
            raise ProviderFreeError("failed publication did not preserve the prepared draft")
        supervisor.workspace_api.publish_cycle = original_publish_cycle
        submit_results: list[dict[str, Any]] = []
        submit_errors: list[BaseException] = []
        submit_barrier = threading.Barrier(3)

        def submit_same_draft() -> None:
            try:
                submit_barrier.wait()
                submit_results.append(call("submit_repair", {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "draft_handle": draft_a["draft_handle"]}))
            except BaseException as error:
                submit_errors.append(error)

        submit_workers = [threading.Thread(target=submit_same_draft) for _ in range(2)]
        for worker in submit_workers:
            worker.start()
        submit_barrier.wait()
        for worker in submit_workers:
            worker.join()
        if submit_errors or len(submit_results) != 2 or submit_results[0] != submit_results[1]:
            raise ProviderFreeError("concurrent draft submission was not single-flight")
        published = submit_results[0]
        submit_request = {"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_repair", "args": {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "draft_handle": draft_a["draft_handle"]}}
        _lose_submit_response(bridge, submit_request)
        lost_response_replay = call("submit_repair", submit_request["args"])
        if lost_response_replay != published:
            raise ProviderFreeError("lost draft publication response did not replay")
        if counters != before_submit_counts or published["decision_facts"]["step_ordinal"] != 1:
            raise ProviderFreeError("draft submission rebuilt or reran evidence")
        published_a = {
            "source": (workspace / "steps/000001/candidate/source/model.py").read_bytes(),
            "mesh": (workspace / "steps/000001/candidate/candidate.glb").read_bytes(),
            "assessment": (workspace / "cycles/000001/assessment.json").read_bytes(),
            "measurement": (workspace / "voxblame/steps/000001/measurement.json").read_bytes(),
            "preview_json": (workspace / "steps/000001/preview/preview.json").read_bytes(),
            "preview_png": (workspace / "steps/000001/preview/preview.png").read_bytes(),
            "diff": (workspace / "cycles/000001/diff.json").read_bytes(),
            "source_changes": (workspace / "cycles/000001/source_changes.json").read_bytes(),
        }
        if published_a != frozen_a or len(list((workspace / "cycles").glob("*/cycle.json"))) != 1:
            raise ProviderFreeError("published Repair did not equal retained Draft A")
        revoked = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "evaluate_repair_draft", "args": {"workspace_handle": wh, "attempt_handle": attempt2["attempt_handle"], "candidate_handle": attempt2["candidate_handle"], "evaluation_ticket": last.get("next_evaluation_ticket") or "ticket:spent"}})
        if revoked.get("ok") is not False:
            raise ProviderFreeError("completed Attempt capabilities were not revoked")
        if list((supervisor._staging_root).glob("draft-*")):
            raise ProviderFreeError("draft stages survived publication")
        return {
            "schema": "text-to-cad.repair-draft-evaluation-evidence/1",
            "attempts": 2,
            "admitted": 8,
            "successful": 7,
            "admitted_failures": 1,
            "invalid_ticket_consumed": False,
            "stale_ticket_consumed": False,
            "completed_ticket_replayed": True,
            "concurrent_ticket_single_flight": True,
            "ninth_ticket_issued": False,
            "repair_builds": counters["repair_builds"],
            "provider_calls": counters["provider_calls"],
            "evaluation_mutated_committed_authority": False,
            "draft_feedback_authority_equal": True,
            "draft_feedback_bytes": response_bytes,
            "draft_feedback_below_64k": response_bytes < 64 * 1024,
            "draft_feedback_authorized_no_feasible": False,
            "occupancy_schema": occupancy["schema"],
            "submitted_frozen_draft": "A",
            "submit_builds": 0,
            "submit_provider_calls": 0,
            "published_cycles": 1,
            "concurrent_submit_single_flight": True,
            "lost_submit_response_replayed": True,
            "publish_failure_preserved_draft": True,
            "abandon_failure_preserved_session": True,
            "permitted_intents_state_derived": True,
            "exterior_feedback_absent": True,
            "malformed_exterior_feedback_rejected": True,
            "stages_cleaned": True,
            "attempt_handles_revoked": True,
            "abandon_waited_for_inflight": True,
            "post_abandon_draft_absent": True,
            "inflight_abandon_builds": 1,
            "inflight_abandon_providers": 1,
            "drain": drain,
            "feedback": draft_a["feedback"],
        }
    finally:
        bridge.stop()
        supervisor.close()
        shutil.rmtree(socket_root, ignore_errors=True)


def _run_draft_drain_probe(
    workspace_template: Path,
    *,
    trusted: Path,
    published_rebuild: Path,
    published_geometry: Path,
    registry: Path,
    sidecar: Any,
    candidate_runtime: Path,
) -> dict[str, Any]:
    """Prove close drains real draft evaluation and publication calls."""

    results: dict[str, bool] = {}
    for phase in ("evaluate", "publish"):
        workspace = workspace_template.parent / f"draft-drain-{phase}-workspace"
        shutil.copytree(workspace_template, workspace)
        candidate_root = workspace.parent / f".agent-candidate-draft-drain-{phase}"
        provider_ready = threading.Event()
        release_provider = threading.Event()

        def repair_provider(request: Any) -> None:
            runner.real_repair_evidence_provider(
                request,
                capability_path=sidecar.capability_dir / "runtime.json",
                meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
                meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src",
            )
            if phase == "evaluate":
                provider_ready.set()
                if not release_provider.wait(timeout=10):
                    raise RuntimeError("evaluation drain probe timed out")

        supervisor = WorkspaceSupervisor(
            workspace,
            bind_reference=True,
            candidate_root=candidate_root,
            rebuild_entrypoint=published_rebuild,
            geometry_entrypoint=published_geometry,
            tool_registry=registry,
            browser_runtime_capability=sidecar.capability_dir / "runtime.json",
            candidate_runtime=candidate_runtime,
            trusted_tools_root=trusted,
            trusted_product_root=trusted,
            reconstruction_spec=True,
            repair_evidence_provider=repair_provider,
        )
        socket_root = Path(tempfile.mkdtemp(prefix=f"ttc-draft-drain-{phase}-", dir="/tmp"))
        bridge = AgentSurfaceBridge(
            supervisor.agent_surface(),
            socket_root / "surface.sock",
            trusted_product_root=trusted,
        )
        bridge_stop_attempted = False
        primary_error: BaseException | None = None

        def call(intent: str, args: Mapping[str, Any]) -> dict[str, Any]:
            frame = _surface_call(
                bridge.socket_path,
                {"schema": "mesh-to-cad.agent-intent/1", "intent": intent, "args": dict(args)},
            )
            if frame.get("ok") is not True:
                raise ProviderFreeError(f"draft drain {phase} {intent} failed")
            return frame["response"]["result"]

        def invoke(intent: str, args: Mapping[str, Any]) -> dict[str, Any]:
            return _surface_call(
                bridge.socket_path,
                {"schema": "mesh-to-cad.agent-intent/1", "intent": intent, "args": dict(args)},
            )

        try:
            bridge.start()
            bootstrap = supervisor.agent_bootstrap_contract()
            wh = bootstrap["workspace_handle"]
            facts = supervisor.workspace_api.read_current_step_decision_facts(
                workspace, step=0
            )
            target = facts["repair_targets"]["items"][0]
            _spec(candidate_root / "reconstruction-spec.json", "component.drain", target["bounds_canonical"])
            _json(candidate_root / "plan.json", {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [target], "planned_edits": [{"edit_key": f"drain-{phase}", "target_ranks": [target["rank"]], "spec_region_id": "component.drain", "description": f"drain {phase}"}], "rationale": "prove supervisor drain ordering", "preview_observation": "committed Step 0 remains unaccepted"})
            parent = supervisor.registry.issue("step", 0)
            attempt = call("start_attempt", {"workspace_handle": wh, "plan_handle": bootstrap["plan_handle"], "parent_step_handle": parent})
            _source(candidate_root / "work/source/model.py", REPAIR_A_WIDTH)
            _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": 0, "to_step": 1, "preview_observation": f"drain {phase}", "summary": f"drain {phase}"})

            if phase == "publish":
                draft = call("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt["attempt_handle"], "candidate_handle": attempt["candidate_handle"], "evaluation_ticket": attempt["evaluation_ticket"]})
                prepared = supervisor.registry.resolve(draft["draft_handle"], "draft")
                original_publish_cycle = supervisor.workspace_api.publish_cycle
                publish_ready = threading.Event()
                release_publish = threading.Event()

                def paused_publish_cycle(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
                    publish_ready.set()
                    if not release_publish.wait(timeout=10):
                        raise RuntimeError("publication drain probe timed out")
                    return original_publish_cycle(*args, **kwargs)

                supervisor.workspace_api.publish_cycle = paused_publish_cycle
                operation = lambda: invoke("submit_repair", {"workspace_handle": wh, "attempt_handle": attempt["attempt_handle"], "draft_handle": draft["draft_handle"]})
                ready = publish_ready
                release = release_publish
                retained_stage = Path(prepared["stage"])
            else:
                operation = lambda: invoke("evaluate_repair_draft", {"workspace_handle": wh, "attempt_handle": attempt["attempt_handle"], "candidate_handle": attempt["candidate_handle"], "evaluation_ticket": attempt["evaluation_ticket"]})
                ready = provider_ready
                release = release_provider
                retained_stage = None

            operation_results: list[dict[str, Any]] = []
            operation_errors: list[BaseException] = []

            def run_operation() -> None:
                try:
                    operation_results.append(operation())
                except BaseException as error:
                    operation_errors.append(error)

            operation_thread = threading.Thread(target=run_operation)
            operation_thread.start()
            ready.wait()
            close_errors: list[BaseException] = []

            def close_supervisor() -> None:
                try:
                    supervisor.close()
                except BaseException as error:
                    close_errors.append(error)

            close_thread = threading.Thread(target=close_supervisor)
            close_thread.start()
            time.sleep(0.2)
            stage_present = (
                retained_stage.is_dir()
                if retained_stage is not None
                else any(supervisor._staging_root.glob("draft-*"))
            )
            if not close_thread.is_alive() or not stage_present or not candidate_root.is_dir():
                raise ProviderFreeError(f"supervisor deleted private roots before {phase} drained")
            release.set()
            operation_thread.join()
            close_thread.join()
            if operation_thread.is_alive() or close_thread.is_alive() or operation_errors or close_errors or not operation_results:
                raise ProviderFreeError(f"supervisor did not drain draft {phase}")
            operation_frame = operation_results[0]
            if operation_frame.get("ok") is not True and operation_frame != {
                "ok": False,
                "schema": "mesh-to-cad.agent-error/1",
                "error": {
                    "classification": "supervisor_failure",
                    "path": "$.supervisor",
                    "detail": "supervisor_failure",
                },
            }:
                raise ProviderFreeError(f"draft {phase} returned an invalid cancellation response")
            if candidate_root.exists() or supervisor._staging_root.exists():
                raise ProviderFreeError(f"supervisor retained private roots after {phase} drain")
            if phase == "publish":
                supervisor.workspace_api.publish_cycle = original_publish_cycle
            results[f"{phase}_drained"] = True
            bridge_stop_attempted = True
            bridge.stop()
            results[f"{phase}_transport_stopped"] = True
        except BaseException as error:
            primary_error = error
            raise
        finally:
            if phase == "publish" and "original_publish_cycle" in locals():
                supervisor.workspace_api.publish_cycle = original_publish_cycle
            if not bridge_stop_attempted:
                try:
                    bridge.stop()
                except Exception:
                    if primary_error is None:
                        raise
            try:
                supervisor.close()
            except Exception:
                if primary_error is None:
                    raise
            shutil.rmtree(socket_root, ignore_errors=True)
    return {
        "schema": "text-to-cad.repair-draft-drain-evidence/1",
        "evaluate_drained": results.get("evaluate_drained") is True,
        "publish_drained": results.get("publish_drained") is True,
        "transport_threads_terminated": all(
            results.get(f"{phase}_transport_stopped") is True
            for phase in ("evaluate", "publish")
        ),
        "private_roots_preserved_until_drain": True,
        "private_roots_removed_after_drain": True,
    }


def validate_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    authoring_python: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, Path]:
    _, evidence_path, _ = artifact_paths(repo_root, record)
    try:
        schema = json.loads(evidence_path.read_text(encoding="utf-8")).get("schema")
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("evidence missing or invalid") from exc
    if schema == EVIDENCE_SCHEMA_V1:
        return _validate_v1_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V2:
        return _validate_v2_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V3:
        return _validate_v2_artifacts(repo_root, record, schema=EVIDENCE_SCHEMA_V3)
    if schema == EVIDENCE_SCHEMA_V4:
        return _validate_v2_artifacts(repo_root, record, schema=EVIDENCE_SCHEMA_V4)
    if schema == EVIDENCE_SCHEMA_V5:
        return _validate_v5_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V6:
        return _validate_v5_artifacts(
            repo_root,
            record,
            schema=EVIDENCE_SCHEMA_V6,
            authoring_python=authoring_python,
            environ=environ,
        )
    if schema == EVIDENCE_SCHEMA_V7:
        return _validate_v5_artifacts(
            repo_root,
            record,
            schema=EVIDENCE_SCHEMA_V7,
            authoring_python=authoring_python,
            environ=environ,
        )
    if schema == EVIDENCE_SCHEMA_V8:
        return _validate_v5_artifacts(
            repo_root,
            record,
            schema=EVIDENCE_SCHEMA_V8,
            authoring_python=authoring_python,
            environ=environ,
        )
    if schema == EVIDENCE_SCHEMA_V9:
        return _validate_v5_artifacts(
            repo_root,
            record,
            schema=EVIDENCE_SCHEMA_V9,
            authoring_python=authoring_python,
            environ=environ,
        )
    if schema == EVIDENCE_SCHEMA_V10:
        return _validate_v5_artifacts(
            repo_root,
            record,
            schema=EVIDENCE_SCHEMA_V10,
            authoring_python=authoring_python,
            environ=environ,
        )
    if schema == EVIDENCE_SCHEMA_V11:
        return _validate_v11_artifacts(repo_root, record)
    if schema == EVIDENCE_SCHEMA_V12:
        return _validate_v11_artifacts(
            repo_root, record, schema=EVIDENCE_SCHEMA_V12
        )
    if schema == EVIDENCE_SCHEMA_V13:
        return _validate_v11_artifacts(
            repo_root, record, schema=EVIDENCE_SCHEMA_V13
        )
    if schema == EVIDENCE_SCHEMA_V14:
        return _validate_v11_artifacts(
            repo_root, record, schema=EVIDENCE_SCHEMA_V14
        )
    if schema == EVIDENCE_SCHEMA_V15:
        return _validate_v11_artifacts(
            repo_root, record, schema=EVIDENCE_SCHEMA_V15
        )
    raise ProviderFreeError("unknown evidence schema")


def _validate_v11_artifacts(
    repo_root: Path,
    record: Mapping[str, Any],
    *,
    schema: str = EVIDENCE_SCHEMA_V11,
) -> tuple[Path, Path]:
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        manifest = json.loads(artifact_manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderFreeError("v11 evidence missing or invalid") from exc
    if evidence_path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ProviderFreeError("v11 evidence is too large")
    required = {
        "schema",
        "identity",
        "scenario",
        "gate_passed",
        "budget_truth",
        "selection",
        "steps",
        "graph",
        "failed_attempts",
        "workspace_validation",
        "module_paths",
        "final",
        "client_transport",
    }
    if schema in {EVIDENCE_SCHEMA_V12, EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
        required.add("observation_gate")
    if schema in {EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
        required.add("target_section_observation")
    if schema in {EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
        required.add("draft_evaluation")
    if (
        not isinstance(evidence, dict)
        or set(evidence) != required
        or evidence.get("schema") != schema
        or evidence.get("identity") != expected_identity(record)
        or evidence.get("scenario") != record.get("scenario")
        or evidence.get("scenario") not in {SCENARIO, EXHAUSTION_SCENARIO}
        or evidence.get("gate_passed") is not True
        or evidence.get("workspace_validation") is not True
    ):
        raise ProviderFreeError("invalid v11 evidence shape")
    if manifest != {
        "schema": f"text-to-cad.provider-free-artifact-manifest/{15 if schema == EVIDENCE_SCHEMA_V15 else 14 if schema == EVIDENCE_SCHEMA_V14 else 13 if schema == EVIDENCE_SCHEMA_V13 else 12 if schema == EVIDENCE_SCHEMA_V12 else 11}",
        "final_status": 0,
        "identity": expected_identity(record),
        "evidence": {"path": evidence_path.name},
    }:
        raise ProviderFreeError("invalid v11 manifest")
    budget = evidence["budget_truth"]
    expected_limits = {
        "remaining_cycles": 7,
        "attempts_per_intended_step": 3,
        "tool_failures_per_intended_step": 2,
    }
    after_c = budget.get("after_repair_c", {})
    local = budget.get("local_exhaustion", {})
    cycle = budget.get("cycle_exhaustion", {})
    if (
        set(budget)
        != {
            "schema",
            "bootstrap_attempt_budget_absent",
            "after_repair_c",
            "local_exhaustion",
            "cycle_exhaustion",
        }
        or budget.get("schema") != "text-to-cad.budget-truth-evidence/1"
        or budget.get("bootstrap_attempt_budget_absent") is not True
        or after_c.get("completed_cycles") != 3
        or after_c.get("total_attempts") != 4
        or after_c.get("budgets") != expected_limits
        or after_c.get("start_attempt_permitted") is not True
        or local.get("intended_step") != 4
        or local.get("attempts") != 3
        or len(local.get("failure_subtypes", [])) != 3
        or local.get("budgets") != expected_limits
        or local.get("start_attempt_permitted") is not False
        or local.get("select_and_finalize_permitted") is not True
        or schema != EVIDENCE_SCHEMA_V15 and cycle.get("completed_cycles") != 10
        or schema != EVIDENCE_SCHEMA_V15 and cycle.get("remaining_cycles") != 0
        or schema != EVIDENCE_SCHEMA_V15 and cycle.get("attempts_per_intended_step") != 3
        or schema != EVIDENCE_SCHEMA_V15 and cycle.get("tool_failures_per_intended_step") != 2
        or schema != EVIDENCE_SCHEMA_V15 and cycle.get("start_attempt_permitted") is not False
    ):
        raise ProviderFreeError("invalid v11 budget truth evidence")
    if schema == EVIDENCE_SCHEMA_V15 and cycle != {
        "skipped": True,
        "reason": "explicit exhaustion scenario only",
    }:
        raise ProviderFreeError("invalid v15 exhaustion marker")
    transports = (
        evidence["client_transport"],
        after_c.get("transport"),
        local.get("transport"),
    ) + (() if schema == EVIDENCE_SCHEMA_V15 else (cycle.get("transport"),))
    for transport in transports:
        if transport != {
            "schema": "text-to-cad.client-transport-evidence/1",
            "transport": "stdin_heredoc",
            "exit_status": 0,
            "response_schema": (
                "mesh-to-cad.agent-response/7"
                if schema in {EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}
                else "mesh-to-cad.agent-response/6"
            ),
            "intent": "workspace_status",
            "invalid_request": False,
        }:
            raise ProviderFreeError("v11 transport response schema is invalid")
    steps = evidence["steps"]
    if set(steps) != {"step_zero", "repair_a", "repair_b", "repair_c"}:
        raise ProviderFreeError("invalid v11 published steps")
    for name, expected_ordinal in (
        ("step_zero", 0),
        ("repair_a", 1),
        ("repair_b", 2),
        ("repair_c", 3),
    ):
        item = steps[name]
        manifest_path = exp_dir / f"steps/{expected_ordinal:06d}/step.json"
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            item.get("ordinal") != expected_ordinal
            or item.get("accepted") is not False
            or document.get("step") != expected_ordinal
            or document.get("parent_step") != item.get("parent")
        ):
            raise ProviderFreeError("v11 step authority mismatch")
    if evidence.get("graph") != {"source": "step_parentage", "heads": [3]}:
        raise ProviderFreeError("v11 graph mismatch")
    failed = evidence["failed_attempts"]
    failed_documents = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((exp_dir / "attempts").glob("*/attempt.json"))
        if json.loads(path.read_text(encoding="utf-8")).get("intended_step") == 4
    ]
    if (
        failed != {
            "intended_step": 4,
            "count": 3,
            "subtypes": local["failure_subtypes"],
        }
        or len(failed_documents) != 3
        or any(document.get("result") != "strategy_changed" for document in failed_documents)
    ):
        raise ProviderFreeError("v11 local Attempts are not authority-backed")
    selection = evidence["selection"]
    final = evidence["final"]
    final_manifest = json.loads((exp_dir / "final/manifest.json").read_text(encoding="utf-8"))
    final_selection = json.loads((exp_dir / "final/selection.json").read_text(encoding="utf-8"))
    if (
        selection.get("stop_reason") != "no_feasible_strategy"
        or final.get("stop_reason") != "no_feasible_strategy"
        or final_selection.get("stop_reason") != "no_feasible_strategy"
        or final.get("identity_bound") is not True
        or final_manifest.get("selected_step") != selection.get("selected_step")
    ):
        raise ProviderFreeError("v11 final stop reason or identity is invalid")
    if record.get("scenario") == EXHAUSTION_SCENARIO:
        exhaustion_root = exp_dir / "run/cycle-exhaustion-workspace"
        exhaustion_index = json.loads(
            (exhaustion_root / "step_index.json").read_text(encoding="utf-8")
        )
        if (
            exhaustion_index.get("budget", {}).get("completed_cycles") != 10
            or exhaustion_index.get("budget", {}).get("remaining_cycles") != 0
            or len(list((exhaustion_root / "cycles").glob("*/cycle.json"))) != 10
            or not runner._workspace_status_available(exhaustion_root)
        ):
            raise ProviderFreeError("v11 cycle exhaustion fixture is not authority-backed")
    modules = evidence["module_paths"]
    if (
        modules.get("product_root") != "skills"
        or any(
            not isinstance(modules.get(key), str)
            or not modules[key].startswith("skills/")
            for key in ("workspace", "core", "handler", "mcp", "rebuild", "geometry")
        )
    ):
        raise ProviderFreeError("v11 module provenance is invalid")
    if schema in {EVIDENCE_SCHEMA_V12, EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
        gate = evidence["observation_gate"]
        if (
            not isinstance(gate, dict)
            or set(gate)
            != {
                "schema",
                "selected_step",
                "historical_step",
                "pre_observation_error",
                "no_side_effects",
                "recovery",
                "historical_observation",
                "selected_page",
                "failed_write",
                "selected_observation",
                "same_claim_reused",
                "finalized",
            }
            or gate.get("schema")
            != "text-to-cad.target-section-finalization-gate-evidence/1"
            or gate.get("selected_step") != steps["repair_a"]["ordinal"]
            or gate.get("historical_step") != steps["step_zero"]["ordinal"]
            or gate["selected_step"] == gate["historical_step"]
            or gate.get("pre_observation_error")
            != {
                "schema": "mesh-to-cad.agent-error/1",
                "error": {
                    "classification": "state_conflict",
                    "path": "$.supervisor",
                    "detail": "state_conflict",
                },
            }
            or gate.get("no_side_effects")
            != {
                "final_absent": True,
                "attempts_unchanged": True,
                "authority_unchanged": True,
                "staging_unchanged": True,
            }
            or gate.get("recovery")
            != {
                "state": "preterminal",
                "observe_target_section_permitted": True,
                "select_and_finalize_permitted": True,
            }
            or gate.get("same_claim_reused") is not True
            or gate.get("finalized") is not True
            or gate.get("failed_write")
            != {
                "schema": "text-to-cad.target-section-failed-write-evidence/1",
                "valid_observation_response": True,
                "response_write_failed": True,
            }
        ):
            raise ProviderFreeError("invalid v12 observation finalization gate")
        page = gate["selected_page"]
        observation = gate["selected_observation"]
        historical_observation = gate["historical_observation"]
        observation_schema = (
            "mesh-to-cad.target-section-observation/3"
            if schema in {EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}
            else "mesh-to-cad.target-section-observation/2"
        )
        if (
            not isinstance(page, dict)
            or page.get("schema") != "mesh-to-cad.repair-target-page/1"
            or page.get("step_ordinal") != gate["selected_step"]
            or not isinstance(page.get("items"), list)
            or not page["items"]
            or not isinstance(observation, dict)
            or observation.get("schema") != observation_schema
            or observation.get("rank") != page["items"][0].get("rank")
            or not isinstance(historical_observation, dict)
            or historical_observation.get("schema") != observation_schema
        ):
            raise ProviderFreeError("invalid v12 real observation evidence")
        meshscope_src = repo_root / "packages/meshscope/src"
        if os.fspath(meshscope_src) not in sys.path:
            sys.path.insert(0, os.fspath(meshscope_src))
        from meshscope import target_section_profile

        if schema in {EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
            from meshscope.voxblame import read_surface_tree

            authority = lambda step, item: _authority_target_section_v3(
                exp_dir,
                step,
                item["rank"],
                target_section_profile,
                read_surface_tree,
            )
            if observation != authority(
                gate["selected_step"], observation
            ) or historical_observation != authority(
                gate["historical_step"], historical_observation
            ):
                raise ProviderFreeError("observation differs from committed authority")
        elif observation != _legacy_v12_authority_target_section(
            exp_dir,
            gate["selected_step"],
            observation["rank"],
            target_section_profile,
        ) or historical_observation != _legacy_v12_authority_target_section(
            exp_dir,
            gate["historical_step"],
            historical_observation["rank"],
            target_section_profile,
        ):
            raise ProviderFreeError("V12 observation differs from committed authority")
        if schema in {EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
            section = evidence["target_section_observation"]
            if (
                not isinstance(section, dict)
                or set(section)
                != {
                    "schema",
                    "observed_ranks",
                    "selected_rank",
                    "step_zero",
                    "historical_reread",
                    "repair_a",
                    "exterior",
                    "authority_recomputed",
                    "non_tied",
                    "center_polarity",
                    "payload_bytes",
                    "null_mask_rejections",
                }
                or section.get("schema")
                != "text-to-cad.target-section-observation-evidence/3"
                or section.get("historical_reread") != section.get("step_zero")
                or section.get("step_zero") != historical_observation
                or section.get("repair_a") != observation
                or section.get("authority_recomputed") is not True
                or section.get("center_polarity")
                != {"step_zero": True, "repair_a": True}
                or not _target_center_polarity(
                    section["step_zero"],
                    _authority_public_target(
                        exp_dir,
                        gate["historical_step"],
                        section["step_zero"]["rank"],
                    ),
                )
                or not _target_center_polarity(
                    section["repair_a"], gate["selected_page"]["items"][0]
                )
            ):
                raise ProviderFreeError("invalid v13 Target Section evidence")
            exterior = section["exterior"]
            exterior_observation = exterior.get("observation")
            if (
                not isinstance(exterior_observation, dict)
                or exterior.get("nonnull_rejected") is not True
                or exterior_observation.get("schema") != observation_schema
                or exterior_observation.get("local_occupancy") is not None
                or exterior_observation
                != _authority_target_section_v3(
                    exp_dir / "exterior-probe",
                    exterior["step_ordinal"],
                    exterior_observation["rank"],
                    target_section_profile,
                    read_surface_tree,
                )
            ):
                raise ProviderFreeError("invalid v13 exterior Target Section")
            sizes = section.get("payload_bytes")
            v3_bytes = len(
                json.dumps(
                    historical_observation,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if sizes != {"v3": v3_bytes}:
                raise ProviderFreeError("invalid v13 Target Section byte evidence")
            if section.get("null_mask_rejections") != [
                "interior_null",
                "center_null",
                "edge_only",
                "all_null",
            ]:
                raise ProviderFreeError("invalid v13 null-mask rejection evidence")
            public_observations = json.dumps(
                {
                    "step_zero": section["step_zero"],
                    "repair_a": section["repair_a"],
                    "exterior": exterior_observation,
                },
                sort_keys=True,
            ).lower()
            if any(
                token in public_observations
                for token in (
                    "neighborhood",
                    "target_key",
                    "mask",
                    "component",
                    "capability",
                    "depth8",
                    "depth_8",
                    "prefix",
                    "morton",
                    "handle",
                    '"path"',
                )
            ):
                raise ProviderFreeError("v13 Target Section leaked private detail")
    if schema in {EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15}:
        draft = evidence["draft_evaluation"]
        expected_keys = {
            "schema", "attempts", "admitted", "successful",
            "admitted_failures", "invalid_ticket_consumed",
            "stale_ticket_consumed", "completed_ticket_replayed",
            "concurrent_ticket_single_flight", "ninth_ticket_issued",
            "repair_builds", "provider_calls",
            "evaluation_mutated_committed_authority",
            "draft_feedback_authority_equal", "draft_feedback_bytes",
            "draft_feedback_below_64k", "draft_feedback_authorized_no_feasible",
            "occupancy_schema", "submitted_frozen_draft", "submit_builds",
            "submit_provider_calls", "published_cycles",
            "concurrent_submit_single_flight", "lost_submit_response_replayed",
            "publish_failure_preserved_draft", "abandon_failure_preserved_session",
            "permitted_intents_state_derived", "exterior_feedback_absent",
            "malformed_exterior_feedback_rejected", "stages_cleaned",
            "attempt_handles_revoked", "abandon_waited_for_inflight",
            "post_abandon_draft_absent", "inflight_abandon_builds",
            "inflight_abandon_providers", "drain", "feedback",
        }
        expected_scalars = {
            "schema": "text-to-cad.repair-draft-evaluation-evidence/1",
            "attempts": 2,
            "admitted": 8,
            "successful": 7,
            "admitted_failures": 1,
            "invalid_ticket_consumed": False,
            "stale_ticket_consumed": False,
            "completed_ticket_replayed": True,
            "concurrent_ticket_single_flight": True,
            "ninth_ticket_issued": False,
            "repair_builds": 8,
            "provider_calls": 8,
            "evaluation_mutated_committed_authority": False,
            "draft_feedback_authority_equal": True,
            "draft_feedback_below_64k": True,
            "draft_feedback_authorized_no_feasible": False,
            "occupancy_schema": "mesh-to-cad.target-section-observation/3",
            "submitted_frozen_draft": "A",
            "submit_builds": 0,
            "submit_provider_calls": 0,
            "published_cycles": 1,
            "concurrent_submit_single_flight": True,
            "lost_submit_response_replayed": True,
            "publish_failure_preserved_draft": True,
            "abandon_failure_preserved_session": True,
            "permitted_intents_state_derived": True,
            "exterior_feedback_absent": True,
            "malformed_exterior_feedback_rejected": True,
            "abandon_waited_for_inflight": True,
            "post_abandon_draft_absent": True,
            "inflight_abandon_builds": 1,
            "inflight_abandon_providers": 1,
            "stages_cleaned": True,
            "attempt_handles_revoked": True,
        }
        if (
            not isinstance(draft, dict)
            or set(draft) != expected_keys
            or any(draft.get(key) != value for key, value in expected_scalars.items())
        ):
            raise ProviderFreeError("invalid v14 draft evaluation evidence")
        if draft.get("drain") != {
            "schema": "text-to-cad.repair-draft-drain-evidence/1",
            "evaluate_drained": True,
            "publish_drained": True,
            "transport_threads_terminated": True,
            "private_roots_preserved_until_drain": True,
            "private_roots_removed_after_drain": True,
        }:
            raise ProviderFreeError("invalid v14 draft drain evidence")
        draft_workspace = exp_dir / "run/draft-evaluation-workspace"
        parent_measurement = json.loads(
            (draft_workspace / "voxblame/steps/000000/measurement.json").read_text(encoding="utf-8")
        )
        child_measurement = json.loads(
            (draft_workspace / "voxblame/steps/000001/measurement.json").read_text(encoding="utf-8")
        )
        authority_feedback = _draft_feedback_authority(parent_measurement, child_measurement)
        feedback_bytes = len(json.dumps(authority_feedback, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if (
            draft.get("feedback") != authority_feedback
            or draft.get("draft_feedback_bytes") != feedback_bytes
            or feedback_bytes >= 64 * 1024
            or len(list((draft_workspace / "cycles").glob("*/cycle.json"))) != 1
        ):
            raise ProviderFreeError("v14 draft feedback differs from committed authority")
        draft_public = json.dumps(draft["feedback"], sort_keys=True).lower()
        if any(token in draft_public for token in ("active_depth", "surface_error_count", "identity", "target_key", "mask", "depth8", "component", "handle", '"path"')):
            raise ProviderFreeError("v14 draft feedback leaked private detail")
    public_text = json.dumps(evidence, sort_keys=True).lower()
    if any(
        token in public_text
        for token in (
            "target_key",
            "mask_sha256",
            "depth8",
            "capability_path",
            '"path": "/',
            "/users/",
            "/home/",
        )
    ):
        raise ProviderFreeError("v11 evidence leaked private detail")
    if schema in {EVIDENCE_SCHEMA_V12, EVIDENCE_SCHEMA_V13, EVIDENCE_SCHEMA_V14, EVIDENCE_SCHEMA_V15} and any(
        token in public_text
        for token in (
            '"handle"',
            "target_key",
            "mask_sha256",
            "depth8",
            "capability_path",
            '"path": "/',
            "/users/",
            "/home/",
        )
    ):
        raise ProviderFreeError("v12 observation gate leaked private detail")
    return evidence_path, artifact_manifest_path


def run_job(record: Mapping[str, Any], *, repo_root: Path, host_home: Path, environ: Mapping[str, str]) -> int:
    identity = expected_identity(record)
    run_exhaustion_probe = record.get("scenario") == EXHAUSTION_SCENARIO
    try:
        receipt = installed.assert_current_authority(
            {**record, "scenario": installed.SCENARIO, "object": installed.SCENARIO}, host_home
        )
    except installed.ProviderFreeError as exc:
        raise ProviderFreeError("plugin authority changed") from exc
    exp_dir, evidence_path, artifact_manifest_path = artifact_paths(repo_root, record)
    exp_dir.mkdir(parents=True, exist_ok=False)
    (repo_root / "tmp").mkdir(exist_ok=True)
    fixture_root = Path(tempfile.mkdtemp(prefix="workspace-repair-chain-", dir=repo_root / "tmp"))
    fixture = fixture_root / "repair-chain.ply"
    cycle_fixture = fixture_root / "cycle-exhaustion.ply"
    cleanup_errors: list[str] = []
    sidecar = candidate_lease = supervisor = bridge = None
    socket_dir: Path | None = None
    try:
        runner.prepare_exp(exp_dir)
        _fixture(fixture)
        _cycle_fixture(cycle_fixture)
        trusted = receipt.publish_tree
        published_rebuild = trusted / "skills/cad/scripts/canonical-build/__main__.py"
        published_geometry = trusted / "skills/mesh-compare/scripts/mesh-compare/__main__.py"
        if not published_rebuild.is_file() or not published_geometry.is_file():
            raise ProviderFreeError("published production entrypoints unavailable")
        runner.prepare_and_initialize_workspace(exp_dir, fixture, trusted_tools_root=trusted)
        sidecar = runner.BrowserRuntimeJob.create(exp_dir, image_lock_path=runner.HOST_IMAGE_LOCK_PATH, viewer_runtime_dir=runner.VIEWER_RUNTIME_DIR)
        candidate_lease = runner.materialize_candidate_runtime(repo_root / ".venv", repo_root / ".cache" / "mesh-to-cad-agent-runtime", repo_root=repo_root)
        candidate_root = exp_dir.parent / f".agent-candidate-repair-chain-{os.getpid()}"
        registry = runner.publish_tool_registry(sidecar.capability_dir, rebuild_entrypoint=published_rebuild, geometry_entrypoint=published_geometry)
        registry_document = json.loads(registry.read_text(encoding="utf-8"))
        supervisor = WorkspaceSupervisor(exp_dir, bind_reference=True, candidate_root=candidate_root, rebuild_entrypoint=published_rebuild, geometry_entrypoint=published_geometry, tool_registry=registry, browser_runtime_capability=sidecar.capability_dir / "runtime.json", candidate_runtime=candidate_lease.runtime, trusted_tools_root=trusted, trusted_product_root=trusted, reconstruction_spec=True, step_zero_evidence_provider=lambda req: runner.real_step_zero_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"), repair_evidence_provider=lambda req: runner.real_repair_evidence_provider(req, capability_path=sidecar.capability_dir / "runtime.json", meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src", meshshot_src=trusted / runner.MESHSHOT_RUNTIME_RELATIVE / "src"))
        socket_dir = Path(tempfile.mkdtemp(prefix="ttc-a-", dir="/tmp"))
        bridge = _FailedObservationWriteBridge(
            supervisor.agent_surface(),
            socket_dir / "surface.sock",
            trusted_product_root=trusted,
        )
        bridge.start(); sidecar.start(); sidecar.preflight(); sidecar.preflight_mcp()
        bootstrap = supervisor.agent_bootstrap_contract(); surface = supervisor.agent_surface(); wh = bootstrap["workspace_handle"]; ph = bootstrap["plan_handle"]
        client_status, client_transport = _workspace_status_via_client(
            trusted / ".claude/agent-source-projection/agent-surface/client.py",
            bridge.socket_path,
            wh,
        )
        if client_status["result"].get("workspace_identity") != wh:
            raise ProviderFreeError("fixed client workspace identity mismatch")
        plan = candidate_root / "plan.json"; _json(plan, {"schema": "mesh-to-cad.initial-plan/1", "summary": "deterministic box reconstruction"})
        a0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph}}); _public(a0_response); a0 = a0_response["result"]
        work = candidate_root / "work"; _source(work / "source/model.py", STEP_ZERO_WIDTH)
        run0 = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "run_candidate_tool", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"], "operation_handle": a0["capability_bundle_handle"]}}); _public(run0)
        s0_response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_step_zero", "args": {"workspace_handle": wh, "attempt_handle": a0["attempt_handle"], "candidate_handle": a0["candidate_handle"]}}); _public(s0_response); s0 = s0_response["result"]
        step_ordinal = s0["decision_facts"]["step_ordinal"]
        step_preview_path = exp_dir / f"steps/{step_ordinal:06d}/preview/preview.png"
        step_preview_bytes = step_preview_path.read_bytes()
        png0 = step_preview_bytes; mcp0 = _inspect(bridge.socket_path, s0["preview_handle"], step_preview_bytes)
        facts = s0.get("decision_facts")
        if not isinstance(facts, dict) or facts.get("accepted") is not False:
            raise ProviderFreeError("Step 0 must remain unaccepted")
        targets = facts.get("repair_targets")
        items = targets.get("items") if isinstance(targets, dict) else None
        step_frontier = _frontier(facts)
        active_depth = step_frontier["active_depth"]
        if not isinstance(items, list) or not items:
            raise ProviderFreeError("Step 0 repair frontier is invalid")
        page_offsets = [0, 8, 16, 24, 32, 40]
        target_pages = [
            _read_target_page(bridge.socket_path, s0["step_handle"], offset)
            for offset in page_offsets
        ]
        if (
            [page.get("offset") for page in target_pages] != page_offsets
            or any(page.get("total") != 48 for page in target_pages)
            or [item.get("rank") for page in target_pages for item in page.get("items", [])]
            != list(range(48))
        ):
            raise ProviderFreeError("Step 0 Repair Target paging discriminator failed")
        active = target_pages[1]["items"][0]
        observed_ranks: list[int] = []
        step_zero_section = None
        for item in (item for page in target_pages for item in page["items"]):
            observed_ranks.append(item["rank"])
            observation = _read_target_section(
                bridge.socket_path, s0["step_handle"], item["rank"]
            )
            if _non_tied_profile(observation) is not None:
                step_zero_section = observation
                break
        if step_zero_section is None:
            raise ProviderFreeError("Step 0 has no discriminative Target Section")
        null_mask_rejections = _reject_invalid_local_occupancy_masks(
            supervisor,
            bridge.socket_path,
            s0["step_handle"],
            step_zero_section,
        )
        spec_path = candidate_root / "reconstruction-spec.json"
        spec_bytes = _spec(spec_path, "component.primary", active["bounds_canonical"])
        start_request = {"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": s0["step_handle"]}}
        def attempt_documents() -> set[str]:
            return {path.relative_to(exp_dir).as_posix() for root in (exp_dir / "attempts", exp_dir / "work/attempts") if root.exists() for path in root.glob("*/attempt.json")}
        negative_cases: list[dict[str, Any]] = []
        for case, region_id, region_bounds in (
            ("unknown_id", "component.unknown", active["bounds_canonical"]),
            ("zero_overlap", "component.primary", {"min": [active["bounds_canonical"]["max"][0], active["bounds_canonical"]["min"][1], active["bounds_canonical"]["min"][2]], "max": [active["bounds_canonical"]["max"][0] + 0.1, active["bounds_canonical"]["max"][1], active["bounds_canonical"]["max"][2]]}),
        ):
            _spec(spec_path, "component.primary", region_bounds)
            _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [active], "planned_edits": [{"edit_key": "negative", "target_ranks": [active["rank"]], "spec_region_id": region_id, "description": "negative binding probe"}], "rationale": "exercise the rejected binding", "preview_observation": "the parent preview was inspected"})
            before = attempt_documents()
            response = _surface_call(bridge.socket_path, start_request)
            public_text = _public(response)
            after = attempt_documents()
            classification = response.get("error", {}).get("classification") if isinstance(response.get("error"), dict) else response.get("error")
            if response.get("ok") is not False or classification != "supervisor_failure" or before != after:
                raise ProviderFreeError(f"Spec Region negative case failed: {case}")
            negative_cases.append({"case": case, "error": classification, "attempt_created": False, "public_no_leak": bool(public_text)})
        spec_bytes = _spec(spec_path, "component.primary", active["bounds_canonical"])
        _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": 0, "selected_targets": [active], "planned_edits": [{"edit_key": "expand-primary", "target_ranks": [active["rank"]], "spec_region_id": "component.primary", "description": "expand the primary box"}], "rationale": "expand the measured candidate", "preview_observation": "the candidate is narrower than the reference"})
        def submit_child(parent: Mapping[str, Any], parent_ordinal: int, next_ordinal: int, width: float, edit_key: str, edit_description: str, assessment_observation: str, assessment_summary: str, selected_target: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
            parent_facts = parent["decision_facts"]
            parent_targets = parent_facts.get("repair_targets", {}).get("items", [])
            if not isinstance(parent_targets, list) or not parent_targets:
                raise ProviderFreeError("child parent has no repair target")
            target = dict(selected_target or parent_targets[0])
            nonlocal spec_bytes
            spec_bytes = _spec(spec_path, "component.primary", target["bounds_canonical"])
            _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": parent_ordinal, "selected_targets": [target], "planned_edits": [{"edit_key": edit_key, "target_ranks": [target["rank"]], "spec_region_id": "component.primary", "description": edit_description}], "rationale": edit_description, "preview_observation": "the parent frontier identifies the active repair target"})
            attempt = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": parent["step_handle"]}}); _public(attempt)
            child = attempt["result"]
            _source(candidate_root / "work/source/model.py", width)
            _json(candidate_root / "work/assessment.json", {"schema": "mesh-to-cad.assessment/1", "from_step": parent_ordinal, "to_step": next_ordinal, "preview_observation": assessment_observation, "summary": assessment_summary})
            evaluated = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "evaluate_repair_draft", "args": {"workspace_handle": wh, "attempt_handle": child["attempt_handle"], "candidate_handle": child["candidate_handle"], "evaluation_ticket": child["evaluation_ticket"]}}); _public(evaluated)
            draft_handle = evaluated.get("result", {}).get("draft_handle")
            if not isinstance(draft_handle, str):
                raise ProviderFreeError("Repair draft evaluation failed")
            response = surface.handle({"schema": "mesh-to-cad.agent-intent/1", "intent": "submit_repair", "args": {"workspace_handle": wh, "attempt_handle": child["attempt_handle"], "draft_handle": draft_handle}}); _public(response)
            result = response["result"]
            ordinal = result["decision_facts"]["step_ordinal"]
            preview = exp_dir / f"steps/{ordinal:06d}/preview/preview.png"
            data = preview.read_bytes()
            return result, data, _inspect(bridge.socket_path, result["preview_handle"], data)
        published_ordinals = [step_ordinal]
        repair_a, png_a, mcp_a = submit_child(s0, step_ordinal, max(published_ordinals) + 1, REPAIR_A_WIDTH, "expand-primary", "expand toward the reference dimensions", "Repair A expands the narrow candidate toward the reference.", "Applied the bounded expansion repair hypothesis.", active)
        published_ordinals.append(repair_a["decision_facts"]["step_ordinal"])
        historical_reread = _read_target_page(
            bridge.socket_path, s0["step_handle"], 8
        )
        if historical_reread != target_pages[1]:
            raise ProviderFreeError("historical Repair Target page changed")
        historical_section = _read_target_section(
            bridge.socket_path, s0["step_handle"], step_zero_section["rank"]
        )
        repair_a_targets = repair_a["decision_facts"].get("repair_targets", {}).get("items", [])
        if not repair_a_targets:
            raise ProviderFreeError("Repair A has no Target Section discriminator")
        non_tied = _non_tied_profile(step_zero_section)
        if historical_section != step_zero_section or non_tied is None:
            raise ProviderFreeError("Target Section observation discriminator failed")
        repair_b, png_b, mcp_b = submit_child(repair_a, repair_a["decision_facts"]["step_ordinal"], max(published_ordinals) + 1, REPAIR_B_WIDTH, "shrink-primary", "shrink below the reference as a regression", "Repair B shrinks the candidate and underfits the reference.", "Applied the bounded shrink regression hypothesis.")
        published_ordinals.append(repair_b["decision_facts"]["step_ordinal"])
        repair_c, png_c, mcp_c = submit_child(repair_b, repair_b["decision_facts"]["step_ordinal"], max(published_ordinals) + 1, REPAIR_C_WIDTH, "recover-primary", "recover the primary width after the regression", "Repair C restores part of the regressed width.", "Applied the bounded recovery hypothesis.")
        published_ordinals.append(repair_c["decision_facts"]["step_ordinal"])
        if repair_a["decision_facts"].get("accepted") is not False or repair_b["decision_facts"].get("accepted") is not False:
            raise ProviderFreeError("repair discriminator must remain unaccepted")
        frontier_a = _frontier(repair_a["decision_facts"])
        frontier_b = _frontier(repair_b["decision_facts"])
        frontier_c = _frontier(repair_c["decision_facts"])
        candidates = {
            "step_zero": (s0, step_frontier),
            "repair_a": (repair_a, frontier_a),
            "repair_c": (repair_c, frontier_c),
        }
        best_label, (best, best_frontier) = max(
            candidates.items(), key=lambda item: _frontier_order(item[1][1])
        )
        if not _frontier_order(best_frontier) > _frontier_order(frontier_b):
            raise ProviderFreeError(f"Active Depth repair ordering discriminator failed: S0={step_frontier},A={frontier_a},B={frontier_b}")
        status_after_c, status_after_c_transport = _workspace_status_via_client(
            trusted / ".claude/agent-source-projection/agent-surface/client.py",
            bridge.socket_path,
            wh,
        )
        after_c_result = status_after_c["result"]
        expected_public_budgets = {
            "remaining_cycles": 7,
            "attempts_per_intended_step": 3,
            "tool_failures_per_intended_step": 2,
        }
        authority_after_c = supervisor.workspace_api.workspace_status(exp_dir)
        if (
            after_c_result.get("budgets") != expected_public_budgets
            or "start_attempt" not in after_c_result.get("permitted_next_intents", [])
            or authority_after_c.get("completed_cycles") != 3
            or authority_after_c.get("total_attempts") != 4
        ):
            raise ProviderFreeError("Repair C budget truth discriminator failed")

        failed_step = repair_c["decision_facts"]["step_ordinal"] + 1
        failed_attempt_results: list[str] = []
        failed_parent_targets = repair_c["decision_facts"].get("repair_targets", {}).get("items", [])
        if not failed_parent_targets:
            raise ProviderFreeError("Repair C has no target for local budget probe")
        failed_target = failed_parent_targets[0]
        for failure_index in range(3):
            _spec(spec_path, "component.primary", failed_target["bounds_canonical"])
            _json(plan, {"schema": "voxblame.repair-batch/1", "from_step": repair_c["decision_facts"]["step_ordinal"], "selected_targets": [failed_target], "planned_edits": [{"edit_key": f"failed-repair-{failure_index + 1}", "target_ranks": [failed_target["rank"]], "spec_region_id": "component.primary", "description": "exercise the real repair evidence failure path"}], "rationale": "exercise the bounded local Attempt allowance", "preview_observation": "the parent remains unaccepted"})
            failed_start = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "start_attempt", "args": {"workspace_handle": wh, "plan_handle": ph, "parent_step_handle": repair_c["step_handle"]}})
            _public(failed_start)
            failed_attempt = failed_start["response"]["result"]
            _source(candidate_root / "work/source/model.py", REPAIR_C_WIDTH)
            assessment_path = candidate_root / "work/assessment.json"
            assessment_path.unlink(missing_ok=True)
            failed_evaluation = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "evaluate_repair_draft", "args": {"workspace_handle": wh, "attempt_handle": failed_attempt["attempt_handle"], "candidate_handle": failed_attempt["candidate_handle"], "evaluation_ticket": failed_attempt["evaluation_ticket"]}})
            _public(failed_evaluation)
            failed_result = failed_evaluation.get("response", {}).get("result", {})
            if failed_result.get("state") != "failed" or failed_result.get("classification") != "admitted_failure":
                raise ProviderFreeError("real Repair draft failure was not admitted")
            failed_attempt_results.append(failed_result["subtype"])
            abandoned = _surface_call(bridge.socket_path, {"schema": "mesh-to-cad.agent-intent/1", "intent": "abandon_repair_attempt", "args": {"workspace_handle": wh, "attempt_handle": failed_attempt["attempt_handle"]}})
            _public(abandoned)
        exhausted_status, exhausted_transport = _workspace_status_via_client(
            trusted / ".claude/agent-source-projection/agent-surface/client.py",
            bridge.socket_path,
            wh,
        )
        exhausted_result = exhausted_status["result"]
        exhausted_authority = supervisor.workspace_api.workspace_status(exp_dir)
        if (
            exhausted_result.get("budgets") != expected_public_budgets
            or "start_attempt" in exhausted_result.get("permitted_next_intents", [])
            or "select_and_finalize" not in exhausted_result.get("permitted_next_intents", [])
            or exhausted_authority.get("current_step_attempts") != 3
            or exhausted_authority.get("next_intended_step") != failed_step
        ):
            raise ProviderFreeError("local intended-step budget exhaustion discriminator failed")
        provenance = supervisor.module_provenance(bridge)
        try:
            workspace_module = provenance["workspace"].resolve()
            workspace_module.relative_to(trusted.resolve())
        except (KeyError, OSError, ValueError) as exc:
            raise ProviderFreeError("loaded workspace module is not from installed publish tree") from exc
        if "selected_step not in graph" in workspace_module.read_text(encoding="utf-8"):
            raise ProviderFreeError("installed workspace module retains current-head selection rejection")
        a_ordinal = repair_a["decision_facts"]["step_ordinal"]
        b_ordinal = repair_b["decision_facts"]["step_ordinal"]
        c_ordinal = repair_c["decision_facts"]["step_ordinal"]
        a_preview_path = exp_dir / f"steps/{a_ordinal:06d}/preview/preview.png"
        best_png = {
            "step_zero": step_preview_bytes,
            "repair_a": png_a,
            "repair_c": png_c,
        }[best_label]
        mcp_selected_reinspect = _inspect(bridge.socket_path, best["preview_handle"], best_png)
        selection_path = candidate_root / "selection.json"
        notes_path = candidate_root / "notes.md"
        best_name = {"step_zero": "Step 0", "repair_a": "Repair A", "repair_c": "Repair C"}[best_label]
        if best_label != "repair_a":
            raise ProviderFreeError("observation gate fixture did not select Repair A")
        _json(selection_path, {"schema": "mesh-to-cad.agent-selection-claim/1", "preview_observation": f"{best_name} is the strongest measured result after the bounded recovery trajectory.", "stop_reason": "no_feasible_strategy", "conflict": False, "conflict_details": None, "rationale": f"{best_name} is the strongest returned result after comparing the bounded repair trajectory; the next intended step exhausted its local Attempts while seven Repair Cycles remained."})
        notes_path.write_text(f"## Input\n\nThe input was measured against the committed reference fixture.\n## Modeling Intent\n\nThe candidate models the bounded box reconstruction intent.\n## Preserved Structural Features\n\nThe measured candidate preserves the primary solid structure.\n## Omitted Surface Details\n\nResidual surface details remain outside this deterministic gate.\n## Repair Trajectory\n\n{best_name} is the best-so-far result; the next intended step exhausted its local Attempts while global Repair capacity remained.\n## Final Selection\n\nThe best-so-far result is {best_name}, selected from its returned opaque handle.\n## Verification\n\nFinal verification is bound to {best_name} and its committed measurement.\n", encoding="utf-8")
        final_request = {"schema": "mesh-to-cad.agent-intent/1", "intent": "select_and_finalize", "args": {"workspace_handle": wh, "step_handle": best["step_handle"], "selection_handle": bootstrap["selection_handle"], "notes_handle": bootstrap["notes_handle"]}}
        selected_page = _read_target_page(
            bridge.socket_path, repair_a["step_handle"], 0
        )
        if not selected_page["items"]:
            raise ProviderFreeError("Selected Step has no paged public target")
        selected_rank = selected_page["items"][0]["rank"]
        failed_write = _fail_observation_response_write(
            bridge,
            {
                "schema": "mesh-to-cad.agent-intent/1",
                "intent": "observe_target_section",
                "args": {
                    "step_handle": repair_a["step_handle"],
                    "rank": selected_rank,
                },
            },
        )
        authority_before_gate = supervisor.workspace_api.workspace_status(exp_dir)
        attempts_before_gate = attempt_documents()
        staging_before_gate = (exp_dir / "work/agent-finalization").exists()
        blocked_final = _surface_call(bridge.socket_path, final_request)
        _public(blocked_final)
        expected_gate_error = {
            "schema": "mesh-to-cad.agent-error/1",
            "error": {
                "classification": "state_conflict",
                "path": "$.supervisor",
                "detail": "state_conflict",
            },
        }
        if blocked_final != {"ok": False, **expected_gate_error}:
            raise ProviderFreeError("missing Target Section receipt did not fail closed")
        recovery_status, _recovery_transport = _workspace_status_via_client(
            trusted / ".claude/agent-source-projection/agent-surface/client.py",
            bridge.socket_path,
            wh,
        )
        recovery_result = recovery_status["result"]
        no_side_effects = {
            "final_absent": not (exp_dir / "final").exists(),
            "attempts_unchanged": attempt_documents() == attempts_before_gate,
            "authority_unchanged": supervisor.workspace_api.workspace_status(exp_dir)
            == authority_before_gate,
            "staging_unchanged": (exp_dir / "work/agent-finalization").exists()
            == staging_before_gate,
        }
        if (
            no_side_effects != {
                "final_absent": True,
                "attempts_unchanged": True,
                "authority_unchanged": True,
                "staging_unchanged": True,
            }
            or recovery_result.get("state") != "preterminal"
            or "observe_target_section"
            not in recovery_result.get("permitted_next_intents", [])
            or "select_and_finalize"
            not in recovery_result.get("permitted_next_intents", [])
        ):
            raise ProviderFreeError("Target Section receipt rejection changed Workspace state")
        repair_a_section = _read_target_section(
            bridge.socket_path,
            repair_a["step_handle"],
            selected_rank,
        )
        final_frame = _surface_call(bridge.socket_path, final_request)
        _public(final_frame)
        final_response = final_frame.get("response") if final_frame.get("ok") is True else None
        if (
            not isinstance(final_response, dict)
            or final_response.get("schema") != "mesh-to-cad.agent-response/7"
            or final_response.get("intent") != "select_and_finalize"
            or final_response.get("result", {}).get("state") != "finalized"
        ):
            raise ProviderFreeError("observed Selected Step did not finalize")
        observation_gate = {
            "schema": "text-to-cad.target-section-finalization-gate-evidence/1",
            "selected_step": repair_a["decision_facts"]["step_ordinal"],
            "historical_step": step_ordinal,
            "pre_observation_error": expected_gate_error,
            "no_side_effects": no_side_effects,
            "recovery": {
                "state": recovery_result["state"],
                "observe_target_section_permitted": True,
                "select_and_finalize_permitted": True,
            },
            "historical_observation": step_zero_section,
            "selected_page": selected_page,
            "failed_write": failed_write,
            "selected_observation": repair_a_section,
            "same_claim_reused": True,
            "finalized": True,
        }
        cycle_exhaustion = (
            _run_cycle_exhaustion_probe(
                exp_dir / "run/cycle-exhaustion-workspace",
                cycle_fixture,
                trusted=trusted,
                published_rebuild=published_rebuild,
                published_geometry=published_geometry,
                registry=registry,
                sidecar=sidecar,
                candidate_runtime=candidate_lease.runtime,
            )
            if run_exhaustion_probe
            else {"skipped": True, "reason": "explicit exhaustion scenario only"}
        )
        final_root = exp_dir / "final"
        final_manifest = json.loads((final_root / "manifest.json").read_text(encoding="utf-8"))
        graph = supervisor.workspace_api._core._build_graph(exp_dir, validate_steps=True)
        def cycle_record(result: Mapping[str, Any], parent: Mapping[str, Any]) -> dict[str, Any]:
            ordinal = result["decision_facts"]["step_ordinal"]
            committed_plan = json.loads(
                (exp_dir / f"cycles/{ordinal:06d}/plan.json").read_text(encoding="utf-8")
            )
            parent_target = committed_plan["selected_targets"][0]
            return {"ordinal": ordinal, "from_step": result["decision_facts"]["parent_step_ordinal"], "to_step": ordinal, "selected_parent_target": {key: parent_target[key] for key in ("rank", "kind", "bounds_canonical")}, "artifacts": {name: f"cycles/{ordinal:06d}/{name}.json" for name in ("plan", "assessment", "diff", "cycle", "attempt")}}
        cycles = {"repair_a": cycle_record(repair_a, s0), "repair_b": cycle_record(repair_b, repair_a), "repair_c": cycle_record(repair_c, repair_b)}
        def published_relative(path: Path) -> str: return path.resolve().relative_to(trusted.resolve()).as_posix()
        runner_interpreter = Path(sys.executable)
        try:
            runner_interpreter_relative = runner_interpreter.relative_to(repo_root).as_posix()
        except ValueError as exc:
            raise ProviderFreeError("runner interpreter is outside repository runtime") from exc
        if runner_interpreter_relative != ".venv/bin/python":
            raise ProviderFreeError("provider-free runner did not use repository venv")
        enabled_status = runner.persist_agent_reconstruction_spec(exp_dir, candidate_root, enabled=True, workload_status=0)
        disabled_exp = fixture_root / "disabled-exp"
        disabled_candidate = fixture_root / "disabled-candidate"
        (disabled_exp / "run").mkdir(parents=True)
        disabled_candidate.mkdir()
        (disabled_candidate / "reconstruction-spec.json").write_bytes(spec_bytes)
        runner.persist_agent_reconstruction_spec(disabled_exp, disabled_candidate, enabled=False, workload_status=0)
        missing_candidate = fixture_root / "missing-candidate"
        missing_candidate.mkdir()
        missing_status = runner.persist_agent_reconstruction_spec(exp_dir, missing_candidate, enabled=True, workload_status=0)
        prior_failure_status = runner.persist_agent_reconstruction_spec(exp_dir, missing_candidate, enabled=True, workload_status=17)
        authority_roots = (exp_dir / "setup", exp_dir / "steps", exp_dir / "cycles", exp_dir / "final")
        workspace_authority_absent = not any(
            path.name == "reconstruction-spec.json"
            for root in authority_roots
            for path in root.rglob("*")
        )
        spec_persistence = {"seam": "runner.persist_agent_reconstruction_spec", "path": runner.RECONSTRUCTION_SPEC_RELATIVE.as_posix(), "enabled_status": enabled_status, "updated_bytes": len((exp_dir / runner.RECONSTRUCTION_SPEC_RELATIVE).read_bytes()), "disabled_absent": not (disabled_exp / runner.RECONSTRUCTION_SPEC_RELATIVE).exists(), "workspace_authority_absent": workspace_authority_absent, "missing_status": missing_status, "prior_failure_status": prior_failure_status}
        spec_region_binding = {"region_id": "component.primary", "cycles": 2, "negative_cases": negative_cases, "authority_absent": workspace_authority_absent}
        directional_projection = _directional_projection_evidence(
            exp_dir,
            facts=facts,
            repair_ordinal=a_ordinal,
            meshscope_src=trusted / runner.MESHSCOPE_RUNTIME_RELATIVE / "src",
            selected_target=active,
        )
        authoring_probe = _run_authoring_probe(
            exp_dir,
            runtime=candidate_lease.runtime,
            rebuild_entrypoint=published_rebuild,
            environ=environ,
        )
        exterior_probe = _run_exterior_target_section_probe(
            exp_dir,
            fixture,
            trusted=trusted,
            published_rebuild=published_rebuild,
            published_geometry=published_geometry,
            registry=registry,
            sidecar=sidecar,
            candidate_runtime=candidate_lease.runtime,
        )
        draft_evaluation = _run_draft_evaluation_probe(
            exp_dir / "run/draft-evaluation-workspace",
            fixture,
            trusted=trusted,
            published_rebuild=published_rebuild,
            published_geometry=published_geometry,
            registry=registry,
            sidecar=sidecar,
            candidate_runtime=candidate_lease.runtime,
        )
        target_paging = {"schema": "text-to-cad.repair-target-paging-evidence/1", "step_ordinal": step_ordinal, "pages": target_pages, "historical_reread": historical_reread, "selected": active}
        step_zero_target = next(
            item
            for page in target_pages
            for item in page["items"]
            if item["rank"] == step_zero_section["rank"]
        )
        v3_bytes = len(
            json.dumps(
                step_zero_section, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        )
        target_section_observation = {
            "schema": "text-to-cad.target-section-observation-evidence/3",
            "observed_ranks": observed_ranks,
            "selected_rank": step_zero_section["rank"],
            "step_zero": step_zero_section,
            "historical_reread": historical_section,
            "repair_a": repair_a_section,
            "exterior": exterior_probe,
            "authority_recomputed": True,
            "non_tied": non_tied,
            "center_polarity": {
                "step_zero": _target_center_polarity(
                    step_zero_section, step_zero_target
                ),
                "repair_a": _target_center_polarity(
                    repair_a_section, selected_page["items"][0]
                ),
            },
            "payload_bytes": {"v3": v3_bytes},
            "null_mask_rejections": null_mask_rejections,
        }
        budget_truth = {
            "schema": "text-to-cad.budget-truth-evidence/1",
            "bootstrap_attempt_budget_absent": "attempt_budget" not in bootstrap,
            "after_repair_c": {
                "completed_cycles": authority_after_c["completed_cycles"],
                "total_attempts": authority_after_c["total_attempts"],
                "budgets": after_c_result["budgets"],
                "start_attempt_permitted": "start_attempt" in after_c_result["permitted_next_intents"],
                "transport": status_after_c_transport,
            },
            "local_exhaustion": {
                "intended_step": failed_step,
                "attempts": exhausted_authority["current_step_attempts"],
                "failure_subtypes": failed_attempt_results,
                "budgets": exhausted_result["budgets"],
                "start_attempt_permitted": "start_attempt" in exhausted_result["permitted_next_intents"],
                "select_and_finalize_permitted": "select_and_finalize" in exhausted_result["permitted_next_intents"],
                "transport": exhausted_transport,
            },
            "cycle_exhaustion": cycle_exhaustion,
        }
        evidence = {
            "schema": EVIDENCE_SCHEMA_V14 if run_exhaustion_probe else EVIDENCE_SCHEMA_V15,
            "identity": identity,
            "scenario": record["scenario"],
            "gate_passed": True,
            "budget_truth": budget_truth,
            "selection": {
                "considered": ["step_zero", "repair_a", "repair_b", "repair_c"],
                "selected": best_label,
                "selected_step": best["decision_facts"]["step_ordinal"],
                "repair_c_is_head": c_ordinal,
                "stop_reason": "no_feasible_strategy",
            },
            "steps": {
                "step_zero": {"ordinal": step_ordinal, "parent": None, "accepted": False},
                "repair_a": {"ordinal": a_ordinal, "parent": step_ordinal, "accepted": False},
                "repair_b": {"ordinal": b_ordinal, "parent": a_ordinal, "accepted": False},
                "repair_c": {"ordinal": c_ordinal, "parent": b_ordinal, "accepted": False},
            },
            "graph": {"source": "step_parentage", "heads": [c_ordinal]},
            "failed_attempts": {
                "intended_step": failed_step,
                "count": len(failed_attempt_results),
                "subtypes": failed_attempt_results,
            },
            "workspace_validation": runner._workspace_status_available(exp_dir),
            "module_paths": {
                "product_root": "skills",
                **{key: published_relative(value) for key, value in provenance.items()},
                "rebuild": published_relative(published_rebuild),
                "geometry": published_relative(published_geometry),
            },
            "final": {
                "manifest": "final/manifest.json",
                "selected_step": final_manifest.get("selected_step"),
                "stop_reason": "no_feasible_strategy",
                "identity_bound": final_manifest.get("selected_step") == best["decision_facts"]["step_ordinal"],
            },
            "client_transport": client_transport,
            "observation_gate": observation_gate,
            "target_section_observation": target_section_observation,
            "draft_evaluation": draft_evaluation,
        }
        _json(evidence_path, evidence)
        _json(
            artifact_manifest_path,
            {
                "schema": "text-to-cad.provider-free-artifact-manifest/14" if run_exhaustion_probe else "text-to-cad.provider-free-artifact-manifest/15",
                "final_status": 0,
                "identity": identity,
                "evidence": {"path": evidence_path.name},
            },
        )
        validate_artifacts(repo_root, record, environ=environ)
        return 0
    finally:
        for resource, action in ((bridge, "stop"), (supervisor, "close"), (candidate_lease, "release"), (sidecar, "stop")):
            if resource is None: continue
            try:
                getattr(resource, action)()
                if action == "close" and not supervisor.cancellation_confirmed:
                    cleanup_errors.append("supervisor cancellation was not confirmed")
            except Exception as exc:
                cleanup_errors.append(f"{action}:{type(exc).__name__}")
        try:
            shutil.rmtree(fixture_root)
        except OSError as exc:
            cleanup_errors.append(f"fixture:{type(exc).__name__}")
        if socket_dir is not None:
            try:
                shutil.rmtree(socket_dir)
            except OSError as exc:
                cleanup_errors.append(f"socket:{type(exc).__name__}")
        if cleanup_errors:
            if evidence_path.is_file():
                try:
                    failed = json.loads(evidence_path.read_text(encoding="utf-8")); failed["gate_passed"] = False; failed["cleanup_errors"] = cleanup_errors; _json(evidence_path, failed)
                except (OSError, json.JSONDecodeError):
                    pass
            raise ProviderFreeError("cleanup failed: " + ",".join(cleanup_errors))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--job", required=True); parser.add_argument("--state-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        record = protocol.load_state(args.state_root, args.job)
        return run_job(record, repo_root=Path(__file__).resolve().parents[2], host_home=Path.home(), environ=os.environ)
    except (ProviderFreeError, OSError, protocol.ProtocolError, runner.PilotError, runner.BrowserRuntimeError, runner.CandidateRuntimeError, SupervisorError) as exc:
        print(f"provider-free-workspace-repair-chain: {exc}", file=os.sys.stderr); return 1


if __name__ == "__main__":
    raise SystemExit(main())
