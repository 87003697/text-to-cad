from __future__ import annotations

import ast
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import trimesh


REPO_ROOT = Path(__file__).resolve().parents[4]
SURFACE_DIR = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-agent-surface"
sys.path.insert(0, str(SURFACE_DIR))

import cli  # noqa: E402
import mcp  # noqa: E402
from handler import (  # noqa: E402
    AgentSurface,
    AgentSurfaceError,
    INTENTS,
    MAX_RESPONSE_BYTES,
    MAX_REQUEST_BYTES,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    error_document,
    tool_descriptors,
)

W2_SOURCE = REPO_ROOT / "packages/meshscope/src"
sys.path.insert(0, str(W2_SOURCE))
from meshscope import ReferenceCapability as W2ReferenceCapability  # noqa: E402


def _request(intent: str, args: dict) -> dict:
    return {"schema": REQUEST_SCHEMA, "intent": intent, "args": args}


def _decision_facts_fixture(*, step_ordinal: int) -> dict:
    """Closed decision-facts fixture for supervisor stubs in this test suite."""

    if step_ordinal == 0:
        return {
            "schema": "mesh-to-cad.decision-facts/1",
            "step_ordinal": 0,
            "parent_step_ordinal": None,
            "accepted": False,
            "acceptance_state": "unaccepted",
            "residual_summary": {
                "objective_facts": {
                    "global_depth_8_zero": False,
                    "out_of_frame_clear": True,
                    "no_evidence_conflict": True,
                },
                "depth_8_missing_surface_count": 1,
                "depth_8_excess_surface_count": 0,
                "depth_8_surface_error_count": 1,
                "depth_8_surface_error_rate": 0.5,
            },
            "repair_targets": {
                "total": 1,
                "returned": 1,
                "remaining": 0,
                "items": [
                    {
                        "rank": 0,
                        "kind": "interior",
                        "missing_surface_count": 1,
                        "excess_surface_count": 0,
                        "surface_error_count": 1,
                    }
                ],
            },
            "preview": {"identity_sha256": "a" * 64, "render_variant": "step"},
            "change_from_parent": None,
        }
    return {
        "schema": "mesh-to-cad.decision-facts/1",
        "step_ordinal": step_ordinal,
        "parent_step_ordinal": step_ordinal - 1,
        "accepted": True,
        "acceptance_state": "acceptance_satisfied",
        "residual_summary": {
            "objective_facts": {
                "global_depth_8_zero": True,
                "out_of_frame_clear": True,
                "no_evidence_conflict": True,
            },
            "depth_8_missing_surface_count": 0,
            "depth_8_excess_surface_count": 0,
            "depth_8_surface_error_count": 0,
            "depth_8_surface_error_rate": 0.0,
        },
        "repair_targets": None,
        "preview": {"identity_sha256": "b" * 64, "render_variant": "step"},
        "change_from_parent": {
            "no_observable_geometry_change": False,
            "parent_accepted": False,
        },
    }


def _initialize_request(request_id: int = 1) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "1"},
        },
    }


class FakePorts:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.raise_error = False
        self.port_exception: Exception | None = None
        self.result: dict | None = None

    @staticmethod
    def _next() -> list[str]:
        return ["workspace_status"]

    def _default_result(self, name: str, *args) -> dict:
        if name == "workspace_status":
            return {
                "state": "ready",
                "workspace_identity": "workspace-identity",
                "budgets": {
                    "remaining_cycles": 4,
                    "remaining_attempts": 3,
                    "remaining_tool_failures": 2,
                },
                "permitted_next_intents": self._next(),
            }
        if name == "start_attempt":
            return {"state": "started", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1", "capability_bundle_handle": "capability:1", "permitted_next_intents": self._next()}
        if name == "run_candidate_tool":
            return {"state": "completed", "candidate_handle": "candidate:1", "result_handle": "result:1", "permitted_next_intents": self._next()}
        if name == "submit_step_zero":
            return {
                "state": "published",
                "step_handle": "step:0",
                "decision_facts": _decision_facts_fixture(step_ordinal=0),
                "permitted_next_intents": self._next(),
            }
        if name == "submit_repair":
            return {
                "state": "published",
                "step_handle": "step:1",
                "cycle_handle": "cycle:1",
                "decision_facts": _decision_facts_fixture(step_ordinal=1),
                "permitted_next_intents": self._next(),
            }
        if name == "select_and_finalize":
            return {"state": "finalized", "final_delivery_handle": "final:1", "permitted_next_intents": self._next()}
        observation = args[1]
        if observation["method"] == "components":
            value = {
                "schema": "meshscope.reference-components/1",
                "limit": observation["args"].get("limit", 32),
                "total": 1,
                "returned": 1,
                "omitted": 0,
                "components": [{
                    "rank": 1,
                    "vertices": 8,
                    "faces": 12,
                    "bounds": {"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5], "size": [1.0, 1.0, 1.0]},
                    "centroid": [0.0, 0.0, 0.0],
                }],
            }
        else:
            value = {
                "schema": "meshscope.reference-summary/1",
                "coordinate_contract": "trellis2_canonical/1",
                "stats": {
                    "vertices": 8,
                    "faces": 12,
                    "edges": 18,
                    "bounds": {"min": [-0.5, -0.5, -0.5], "max": [0.5, 0.5, 0.5], "size": [1.0, 1.0, 1.0]},
                    "surface_area": 6.0,
                    "volume": 1.0,
                },
                "quality": {"watertight": True, "volume_valid": True, "degenerate_faces": 0, "euler_number": 2},
                "canonical_frame": {"center": [0.0, 0.0, 0.0], "status": "ambiguous", "pca_axes": None, "eigenvalues": [0.25, 0.25, 0.25]},
            }
        return {
            "schema": "meshscope.reference-response/1",
            "reference_id": "reference:1",
            "method": observation["method"],
            "observation": value,
        }

    def _call(self, name: str, *args, **kwargs) -> dict:
        self.calls.append((name, args, kwargs))
        if self.port_exception is not None:
            raise self.port_exception
        if self.raise_error:
            raise RuntimeError("host path /private/secret and token=secret")
        return json.loads(json.dumps(self.result if self.result is not None else self._default_result(name, *args)))

    def workspace_status(self, workspace_handle):
        return self._call("workspace_status", workspace_handle)

    def start_attempt(self, workspace_handle, plan_handle, from_step):
        return self._call(
            "start_attempt", workspace_handle, plan_handle, from_step
        )

    def run_candidate_tool(
        self, workspace_handle, attempt_handle, candidate_handle, operation_handle
    ):
        return self._call(
            "run_candidate_tool",
            workspace_handle,
            attempt_handle,
            candidate_handle,
            operation_handle,
        )

    def submit_step_zero(
        self,
        workspace_handle,
        attempt_handle,
        candidate_handle,
    ):
        return self._call(
            "submit_step_zero",
            workspace_handle,
            attempt_handle,
            candidate_handle,
        )

    def submit_repair(
        self,
        workspace_handle,
        attempt_handle,
        candidate_handle,
    ):
        return self._call(
            "submit_repair",
            workspace_handle,
            attempt_handle,
            candidate_handle,
        )

    def select_and_finalize(
        self, workspace_handle, step_handle, selection_handle, notes_handle
    ):
        return self._call(
            "select_and_finalize",
            workspace_handle,
            step_handle,
            selection_handle,
            notes_handle,
        )

    def observe_reference(self, reference_handle, observation):
        return self._call("observe_reference", reference_handle, observation)


class AgentSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ports = FakePorts()
        self.surface = AgentSurface(self.ports)

    def assert_error(self, callback, classification: str) -> AgentSurfaceError:
        with self.assertRaises(AgentSurfaceError) as raised:
            callback()
        self.assertEqual(classification, raised.exception.classification)
        self.assertNotIn("/private", raised.exception.detail)
        return raised.exception

    def test_all_intents_delegate_through_opaque_ports(self) -> None:
        handles = {
            "workspace_handle": "ws:1",
            "plan_handle": "plan:1",
            "attempt_handle": "attempt:1",
            "candidate_handle": "candidate:1",
            "operation_handle": "operation:build",
            "step_handle": "step:0",
            "selection_handle": "selection:1",
            "notes_handle": "notes:1",
            "reference_handle": "reference:1",
        }
        requests = [
            _request("workspace_status", {"workspace_handle": handles["workspace_handle"]}),
            _request(
                "start_attempt",
                {
                    "workspace_handle": handles["workspace_handle"],
                    "plan_handle": handles["plan_handle"],
                },
            ),
            _request(
                "run_candidate_tool",
                {
                    key: handles[key]
                    for key in (
                        "workspace_handle",
                        "attempt_handle",
                        "candidate_handle",
                        "operation_handle",
                    )
                },
            ),
            _request(
                "submit_step_zero",
                {key: handles[key] for key in (
                    "workspace_handle", "attempt_handle", "candidate_handle",
                )},
            ),
            _request(
                "submit_repair",
                {key: handles[key] for key in (
                    "workspace_handle", "attempt_handle", "candidate_handle",
                )},
            ),
            _request(
                "select_and_finalize",
                {key: handles[key] for key in (
                    "workspace_handle", "step_handle", "selection_handle", "notes_handle",
                )},
            ),
            _request(
                "observe_reference",
                {
                    "reference_handle": handles["reference_handle"],
                    "observation": {"method": "summary", "args": {}},
                },
            ),
        ]
        for request in requests:
            response = self.surface.handle(request)
            self.assertEqual(RESPONSE_SCHEMA, response["schema"])
            self.assertEqual(request["intent"], response["intent"])
            if request["intent"] == "observe_reference":
                self.assertEqual("summary", response["result"]["observation"]["method"])
            else:
                self.assertIn("state", response["result"])
        self.assertEqual(list(INTENTS), [call[0] for call in self.ports.calls])

    def test_request_and_intent_schemas_are_closed(self) -> None:
        extra = _request("workspace_status", {"workspace_handle": "ws:1"})
        extra["unexpected"] = True
        self.assert_error(lambda: self.surface.handle(extra), "invalid_request")
        self.assert_error(
            lambda: self.surface.handle(_request("unknown", {})), "unknown_intent"
        )
        self.assert_error(
            lambda: self.surface.handle(
                {"schema": REQUEST_SCHEMA, "intent": "workspace_status", "args": []}
            ),
            "invalid_request",
        )
        bad = _request("workspace_status", {"workspace_handle": "ws:1", "path": "/tmp"})
        self.assert_error(lambda: self.surface.handle(bad), "invalid_request")

    def test_handles_state_and_observation_injections_fail_closed(self) -> None:
        self.assert_error(
            lambda: self.surface.handle(
                _request("workspace_status", {"workspace_handle": "/tmp/authority"})
            ),
            "invalid_handle",
        )
        self.assert_error(
            lambda: self.surface.handle(
                _request(
                    "start_attempt",
                    {
                        "workspace_handle": "ws:1",
                        "plan_handle": "../plan",
                    },
                )
            ),
            "invalid_handle",
        )
        args = {
            "workspace_handle": "ws:1",
            "attempt_handle": "attempt:1",
            "candidate_handle": "candidate:1",
            "operation_handle": "operation:1",
            "argv": ["sh", "-c", "cat /secret"],
        }
        self.assert_error(lambda: self.surface.handle(_request("run_candidate_tool", args)), "invalid_request")
        for method in ("slices", "vertices", "faces", "raw_bytes", "export", "raycast"):
            with self.subTest(method=method):
                self.assert_error(
                    lambda method=method: self.surface.handle(
                        _request(
                            "observe_reference",
                            {
                                "reference_handle": "reference:1",
                                "observation": {"method": method, "args": {}},
                            },
                        )
                    ),
                    "unsupported_operation" if method != "slices" else "unknown_method",
                )

    def test_illegal_state_and_reference_arguments_are_rejected(self) -> None:
        initial = {"workspace_handle": "ws:1", "plan_handle": "plan:1"}
        self.assertEqual("started", self.surface.handle(_request("start_attempt", initial))["result"]["state"])
        self.assert_error(
            lambda: self.surface.handle(
                _request("start_attempt", {**initial, "intended_step": 0})
            ),
            "invalid_request",
        )
        self.assertEqual(
            "started",
            self.surface.handle(_request("start_attempt", {**initial, "from_step": 0}))[
                "result"
            ]["state"],
        )
        self.assert_error(
            lambda: self.surface.handle(_request("start_attempt", {**initial, "from_step": 5})),
            "budget_violation",
        )
        self.assert_error(
            lambda: self.surface.handle(
                _request(
                    "observe_reference",
                    {
                        "reference_handle": "reference:1",
                        "observation": {"method": "components", "args": {"limit": 33}},
                    },
                )
            ),
            "invalid_request",
        )

    def test_port_failures_and_malicious_port_results_are_safe(self) -> None:
        self.ports.raise_error = True
        error = self.assert_error(
            lambda: self.surface.handle(_request("workspace_status", {"workspace_handle": "ws:1"})),
            "supervisor_failure",
        )
        self.assertNotIn("secret", error.detail)
        self.ports.port_exception = AgentSurfaceError(
            "port_internal_detail", "$.port", "token=/private/secret"
        )
        error = self.assert_error(
            lambda: self.surface.handle(_request("workspace_status", {"workspace_handle": "ws:1"})),
            "supervisor_failure",
        )
        self.assertNotIn("port_internal_detail", error.detail)
        self.assertNotIn("secret", error.detail)
        self.ports.port_exception = None
        self.ports.raise_error = False
        self.ports.result = {"path": "/private/secret"}
        self.assert_error(
            lambda: self.surface.handle(_request("workspace_status", {"workspace_handle": "ws:1"})),
            "supervisor_contract_violation",
        )
        self.ports.result = {"state": "ready", "vertices": [[0, 0, 0]]}
        self.assert_error(
            lambda: self.surface.handle(_request("workspace_status", {"workspace_handle": "ws:1"})),
            "supervisor_contract_violation",
        )

        self.ports.result = {"state": "ready", "payload": "x" * (MAX_RESPONSE_BYTES + 1)}
        self.assert_error(
            lambda: self.surface.handle(_request("workspace_status", {"workspace_handle": "ws:1"})),
            "supervisor_contract_violation",
        )

    def test_responses_are_deterministic_and_tool_descriptors_are_closed(self) -> None:
        request = _request("workspace_status", {"workspace_handle": "ws:1"})
        self.assertEqual(self.surface.handle(request), self.surface.handle(request))
        descriptors = tool_descriptors()
        self.assertEqual(list(INTENTS), [item["name"] for item in descriptors])
        for descriptor in descriptors:
            schemas = descriptor["inputSchema"].get("oneOf", [descriptor["inputSchema"]])
            for schema in schemas:
                self.assertFalse(schema.get("additionalProperties"))
                self.assertNotIn("argv", schema["properties"])
                self.assertNotIn("path", schema["properties"])
        start_schema = next(item for item in descriptors if item["name"] == "start_attempt")["inputSchema"]
        self.assertEqual("object", start_schema["type"])
        self.assertEqual(2, len(start_schema["oneOf"]))
        self.assertNotIn("from_step", start_schema["oneOf"][0]["properties"])
        self.assertEqual(4, start_schema["oneOf"][1]["properties"]["from_step"]["maximum"])
        observe_schema = next(item for item in descriptors if item["name"] == "observe_reference")["inputSchema"]
        self.assertEqual(
            ["summary", "components"],
            [variant["properties"]["method"]["const"] for variant in observe_schema["properties"]["observation"]["oneOf"]],
        )

    def test_each_intent_has_an_intent_specific_closed_result(self) -> None:
        requests = [
            _request("workspace_status", {"workspace_handle": "ws:1"}),
            _request("start_attempt", {"workspace_handle": "ws:1", "plan_handle": "plan:1"}),
            _request("run_candidate_tool", {"workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1", "operation_handle": "operation:1"}),
            _request("submit_step_zero", {
                "workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1",
            }),
            _request("submit_repair", {
                "workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1",
            }),
            _request("select_and_finalize", {"workspace_handle": "ws:1", "step_handle": "step:0", "selection_handle": "selection:1", "notes_handle": "notes:1"}),
            _request("observe_reference", {"reference_handle": "reference:1", "observation": {"method": "summary", "args": {}}}),
        ]
        for request in requests:
            with self.subTest(intent=request["intent"]):
                self.ports.result = {"state": "ready"}
                self.assert_error(
                    lambda request=request: self.surface.handle(request),
                    "supervisor_contract_violation",
                )
        self.ports.result = None

    def test_port_scalar_fields_are_closed_not_descriptive_strings(self) -> None:
        cases = [
            ("workspace_status", {"workspace_handle": "ws:1"}, ("workspace_status", "ws:1"), "state"),
            ("workspace_status", {"workspace_handle": "ws:1"}, ("workspace_status", "ws:1"), "workspace_identity"),
            ("workspace_status", {"workspace_handle": "ws:1"}, ("workspace_status", "ws:1"), "permitted_next_intents"),
            ("start_attempt", {"workspace_handle": "ws:1", "plan_handle": "plan:1"}, ("start_attempt", "ws:1", "plan:1", None), "state"),
            ("run_candidate_tool", {"workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1", "operation_handle": "operation:1"}, ("run_candidate_tool", "ws:1", "attempt:1", "candidate:1", "operation:1"), "candidate_handle"),
            ("submit_step_zero", {"workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1"}, ("submit_step_zero", "ws:1", "attempt:1", "candidate:1"), "state"),
            ("submit_repair", {"workspace_handle": "ws:1", "attempt_handle": "attempt:1", "candidate_handle": "candidate:1"}, ("submit_repair", "ws:1", "attempt:1", "candidate:1"), "cycle_handle"),
            ("select_and_finalize", {"workspace_handle": "ws:1", "step_handle": "step:0", "selection_handle": "selection:1", "notes_handle": "notes:1"}, ("select_and_finalize", "ws:1", "step:0", "selection:1", "notes:1"), "final_delivery_handle"),
            ("observe_reference", {"reference_handle": "reference:1", "observation": {"method": "summary", "args": {}}}, ("observe_reference", "reference:1", {"method": "summary", "args": {}}), "reference_id"),
        ]
        for intent, args, port_args, field in cases:
            with self.subTest(intent=intent):
                result = self.ports._default_result(*port_args)
                target = result if intent == "observe_reference" else result
                if field == "reference_id":
                    target[field] = "/private/secret"
                elif field == "state":
                    target[field] = "token=/private/secret"
                elif field == "workspace_identity":
                    target[field] = "/private/secret"
                elif field == "permitted_next_intents":
                    target[field] = ["token=/private/secret"]
                elif field in {"candidate_handle", "cycle_handle", "final_delivery_handle"}:
                    target[field] = "/private/secret"
                self.ports.result = result
                self.assert_error(
                    lambda intent=intent, args=args: self.surface.handle(_request(intent, args)),
                    "supervisor_contract_violation",
                )
        self.ports.result = None

    def test_submit_intents_reject_agent_selected_evidence_handles(self) -> None:
        """The Agent cannot name evidence handles or paths on the submit seam."""

        submit_step_zero_narrow = {
            "workspace_handle": "ws:1",
            "attempt_handle": "attempt:1",
            "candidate_handle": "candidate:1",
        }
        submit_repair_narrow = dict(submit_step_zero_narrow)
        forbidden_extras = (
            "candidate_mesh_handle",
            "measurement_handle",
            "preview_handle",
            "region_diff_handle",
            "assessment_handle",
            "source_changes_handle",
            "path",
            "candidate_mesh",
        )
        for extra in forbidden_extras:
            with self.subTest(extra=extra):
                for intent, narrow in (
                    ("submit_step_zero", submit_step_zero_narrow),
                    ("submit_repair", submit_repair_narrow),
                ):
                    wide = dict(narrow)
                    wide[extra] = "handle:1"
                    self.assert_error(
                        lambda intent=intent, wide=wide: self.surface.handle(
                            _request(intent, wide)
                        ),
                        "invalid_request",
                    )
        self.assertEqual([], self.ports.calls)

    def test_submit_intent_schemas_expose_only_three_handles(self) -> None:
        """Tool descriptors publish the narrow (workspace, attempt, candidate) shape."""

        descriptors = {item["name"]: item for item in tool_descriptors()}
        for intent in ("submit_step_zero", "submit_repair"):
            with self.subTest(intent=intent):
                schema = descriptors[intent]["inputSchema"]
                self.assertFalse(schema.get("additionalProperties"))
                self.assertEqual(
                    {"workspace_handle", "attempt_handle", "candidate_handle"},
                    set(schema["properties"]),
                )

    def test_w2_response_is_bound_to_reference_method_and_component_limit(self) -> None:
        request = _request(
            "observe_reference",
            {
                "reference_handle": "reference:1",
                "observation": {"method": "components", "args": {"limit": 8}},
            },
        )
        original = self.ports._default_result(
            "observe_reference",
            "reference:1",
            request["args"]["observation"],
        )
        self.ports.result = original
        self.assertEqual(
            "components",
            self.surface.handle(request)["result"]["observation"]["method"],
        )
        for mutation in ("reference", "method", "limit"):
            replay = json.loads(json.dumps(original))
            if mutation == "reference":
                replay["reference_id"] = "reference:other"
            elif mutation == "method":
                replay["method"] = "summary"
            else:
                replay["observation"]["limit"] = 32
            self.ports.result = replay
            self.assert_error(
                lambda: self.surface.handle(request),
                "supervisor_contract_violation",
            )
        self.ports.result = None


class AdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ports = FakePorts()

    def test_cli_emits_one_object_and_uses_handler(self) -> None:
        request = _request("workspace_status", {"workspace_handle": "ws:1"})
        stdin = io.StringIO(json.dumps(request))
        stdout = io.StringIO()
        status = cli.main(self.ports, stdin=stdin, stdout=stdout)
        self.assertEqual(0, status)
        payload = json.loads(stdout.getvalue())
        self.assertEqual({"ok", "response"}, set(payload))
        self.assertEqual(RESPONSE_SCHEMA, payload["response"]["schema"])
        self.assertEqual(1, len(stdout.getvalue().splitlines()))

    def test_cli_invalid_input_and_unwired_entrypoint_are_safe(self) -> None:
        stdout = io.StringIO()
        self.assertEqual(2, cli.main(self.ports, stdin=io.StringIO("not json"), stdout=stdout))
        payload = json.loads(stdout.getvalue())
        self.assertEqual("invalid_request", payload["error"]["classification"])
        stdout = io.StringIO()
        self.assertEqual(
            2,
            cli.main(
                None,
                stdin=io.StringIO(json.dumps(_request("workspace_status", {"workspace_handle": "ws:1"}))),
                stdout=stdout,
            ),
        )
        self.assertEqual("supervisor_unavailable", json.loads(stdout.getvalue())["error"]["classification"])

    def test_mcp_initialize_list_and_call_share_handler(self) -> None:
        request = _request("workspace_status", {"workspace_handle": "ws:1"})
        lines = "\n".join(
            [
                json.dumps(_initialize_request()),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {
                            "name": request["intent"],
                            "arguments": request["args"],
                        },
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/cancelled"}),
                json.dumps({"jsonrpc": "2.0", "method": "client/progress"}),
                json.dumps({"jsonrpc": "1.0", "method": "unknown-notification"}),
            ]
        ) + "\n"
        stdout = io.StringIO()
        self.assertEqual(0, mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout))
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([1, 2, 3], [frame["id"] for frame in frames])
        self.assertEqual(list(INTENTS), [item["name"] for item in frames[1]["result"]["tools"]])
        self.assertEqual(
            AgentSurface(self.ports).handle(request),
            frames[2]["result"]["structuredContent"],
        )
        self.assertFalse(frames[2]["result"]["isError"])

    def test_mcp_protocol_and_tool_errors_are_closed(self) -> None:
        lines = "\n".join(
            [
                "{not json",
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "method": "unknown-notification"}),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {
                            "name": "workspace_status",
                            "arguments": {"workspace_handle": "/tmp/secret"},
                        },
                    }
                ),
            ]
        ) + "\n"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(-32700, frames[0]["error"]["code"])
        self.assertEqual(-32002, frames[1]["error"]["code"])
        self.assertEqual(-32002, frames[2]["error"]["code"])

    def test_mcp_lifecycle_and_params_are_strict(self) -> None:
        lines = "\n".join(
            [
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {}}),
                json.dumps(_initialize_request(3)),
                json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/list"}),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(_initialize_request(5)),
                json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {"cursor": "opaque-cursor"}}),
                json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "workspace_status"}}),
                json.dumps({"jsonrpc": "2.0", "id": 8, "method": "unknown"}),
            ]
        ) + "\n"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([1, 2, 3, 4, 5, 6, 7, 8], [frame["id"] for frame in frames])
        self.assertEqual(-32002, frames[0]["error"]["code"])
        self.assertEqual(-32602, frames[1]["error"]["code"])
        self.assertEqual("2025-06-18", frames[2]["result"]["protocolVersion"])
        self.assertEqual(-32002, frames[3]["error"]["code"])
        self.assertEqual(-32600, frames[4]["error"]["code"])
        self.assertEqual(-32602, frames[5]["error"]["code"])
        self.assertEqual(-32602, frames[6]["error"]["code"])
        self.assertEqual(-32601, frames[7]["error"]["code"])

    def test_mcp_negotiates_newer_version_and_accepts_standard_extensions(self) -> None:
        request = _initialize_request(1)
        request["params"]["protocolVersion"] = "2099-01-01"
        request["params"]["capabilities"] = {"experimental": {"example": {"enabled": True}}}
        request["params"]["clientInfo"]["title"] = "Test Client"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(json.dumps(request) + "\n"), stdout=stdout)
        frame = json.loads(stdout.getvalue())
        self.assertEqual("2025-06-18", frame["result"]["protocolVersion"])
        self.assertEqual("mesh-to-cad-agent-surface", frame["result"]["serverInfo"]["name"])

        invalid = _initialize_request(2)
        invalid["params"]["capabilities"] = {"experimental": []}
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(json.dumps(invalid) + "\n"), stdout=stdout)
        self.assertEqual(-32602, json.loads(stdout.getvalue())["error"]["code"])

    def test_mcp_invalid_request_ids_fail_without_state_transition(self) -> None:
        invalid_object = _initialize_request()
        invalid_object["id"] = {"bad": True}
        invalid_bool = _initialize_request()
        invalid_bool["id"] = True
        invalid_after_init = {"jsonrpc": "2.0", "id": [1], "method": "tools/list"}
        lines = "\n".join(
            [
                json.dumps(invalid_object),
                json.dumps(invalid_bool),
                json.dumps(_initialize_request()),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                json.dumps(invalid_after_init),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        ) + "\n"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([None, None, 1, None, 2], [frame["id"] for frame in frames])
        self.assertEqual(-32600, frames[0]["error"]["code"])
        self.assertEqual(-32600, frames[1]["error"]["code"])
        self.assertEqual(-32600, frames[3]["error"]["code"])
        self.assertIn("tools", frames[4]["result"])

    def test_initialized_notification_accepts_meta_and_activates_session(self) -> None:
        lines = "\n".join(
            [
                json.dumps(_initialize_request()),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {"_meta": {"com.example/extension": {"enabled": True}}}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        ) + "\n"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([1, 2], [frame["id"] for frame in frames])
        self.assertIn("tools", frames[1]["result"])

    def test_non_object_initialized_params_do_not_activate_session(self) -> None:
        lines = "\n".join(
            [
                json.dumps(_initialize_request()),
                json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": []}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            ]
        ) + "\n"
        stdout = io.StringIO()
        mcp.serve(self.ports, stdin=io.StringIO(lines), stdout=stdout)
        frames = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual([1, 2], [frame["id"] for frame in frames])
        self.assertEqual(-32002, frames[1]["error"]["code"])

    def test_mcp_bounded_readline_rejects_unterminated_oversized_frame(self) -> None:
        class GuardedStream:
            def __init__(self) -> None:
                self.sizes: list[int] = []
                self.done = False

            def readline(self, size: int = -1) -> str:
                self.sizes.append(size)
                if self.done:
                    return ""
                self.done = True
                return "x" * (MAX_REQUEST_BYTES + 1)

        stream = GuardedStream()
        stdout = io.StringIO()
        self.assertEqual(2, mcp.serve(self.ports, stdin=stream, stdout=stdout))
        self.assertEqual([MAX_REQUEST_BYTES + 1], stream.sizes)
        self.assertEqual(-32600, json.loads(stdout.getvalue())["error"]["code"])

    def test_mcp_subprocess_protocol_fixture(self) -> None:
        script = f"""
import sys
sys.path.insert(0, {str(SURFACE_DIR)!r})
import mcp

class Ports:
    def workspace_status(self, workspace_handle):
        return {{
            "state": "ready",
            "workspace_identity": "workspace-identity",
            "budgets": {{"remaining_cycles": 4, "remaining_attempts": 3, "remaining_tool_failures": 2}},
            "permitted_next_intents": ["workspace_status"],
        }}

raise SystemExit(mcp.serve(Ports()))
"""
        lines = [
            json.dumps(_initialize_request()),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "workspace_status",
                        "arguments": {"workspace_handle": "ws:1"},
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "unknown-notification"}),
            json.dumps({"jsonrpc": "2.0", "id": 4, "method": "unknown"}),
            "{bad-json",
            "x" * (MAX_REQUEST_BYTES + 100),
        ]
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = process.communicate("\n".join(lines) + "\n", timeout=10)
        self.assertEqual(2, process.returncode, stderr)
        frames = [json.loads(line) for line in stdout.splitlines()]
        self.assertEqual([1, 2, 3, 4, None, None], [frame["id"] for frame in frames])
        tools = frames[1]["result"]["tools"]
        status_tool = next(item for item in tools if item["name"] == "workspace_status")
        schema = status_tool["inputSchema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual("string", schema["properties"]["workspace_handle"]["type"])
        self.assertEqual(RESPONSE_SCHEMA, frames[2]["result"]["structuredContent"]["schema"])
        self.assertFalse(frames[2]["result"]["isError"])
        self.assertEqual(-32601, frames[3]["error"]["code"])
        self.assertEqual(-32700, frames[4]["error"]["code"])
        self.assertEqual(-32600, frames[5]["error"]["code"])


class RealW2ObservationContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-surface-w2-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _surface_for(self, mesh: trimesh.Trimesh, reference_id: str) -> AgentSurface:
        path = self.root / f"{reference_id.replace(':', '-')}.ply"
        mesh.export(path)
        capability = W2ReferenceCapability(reference_id, path)

        class Ports(FakePorts):
            def observe_reference(inner_self, reference_handle, observation):
                return capability.handle(
                    {
                        "schema": "meshscope.reference-request/1",
                        "reference_id": reference_handle,
                        "method": observation["method"],
                        "args": observation["args"],
                    }
                )

        return AgentSurface(Ports())

    def test_real_w2_summary_stable_pca_is_projected(self) -> None:
        surface = self._surface_for(
            trimesh.creation.box(extents=(0.8, 0.7, 0.6)), "reference:stable"
        )
        response = surface.handle(
            _request(
                "observe_reference",
                {
                    "reference_handle": "reference:stable",
                    "observation": {"method": "summary", "args": {}},
                },
            )
        )
        projection = response["result"]["observation"]["value"]
        self.assertEqual("meshscope.reference-summary/1", projection["schema"])
        self.assertEqual("stable", projection["canonical_frame"]["status"])
        self.assertEqual([0.0, 0.0, 0.0], projection["canonical_frame"]["center"])
        self.assertNotIn("pca_axes", projection["canonical_frame"])

    def test_real_w2_ambiguous_pca_and_components_are_closed(self) -> None:
        surface = self._surface_for(
            trimesh.creation.icosphere(subdivisions=1, radius=0.4), "reference:ambiguous"
        )
        summary = surface.handle(
            _request(
                "observe_reference",
                {
                    "reference_handle": "reference:ambiguous",
                    "observation": {"method": "summary", "args": {}},
                },
            )
        )["result"]["observation"]["value"]
        self.assertEqual("ambiguous", summary["canonical_frame"]["status"])
        components = surface.handle(
            _request(
                "observe_reference",
                {
                    "reference_handle": "reference:ambiguous",
                    "observation": {"method": "components", "args": {"limit": 32}},
                },
            )
        )["result"]["observation"]["value"]
        self.assertEqual("meshscope.reference-components/1", components["schema"])
        self.assertLessEqual(len(components["components"]), 32)
        self.assertNotIn("reference_id", components)


class IsolationTests(unittest.TestCase):
    def test_agent_surface_runtime_has_no_concrete_repository_imports(self) -> None:
        for path in SURFACE_DIR.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                else:
                    continue
                for module in modules:
                    self.assertFalse(module == "workspace_core" or module.startswith("meshscope"))
                    self.assertFalse(module == "skills" or module.startswith("skills."))


if __name__ == "__main__":
    unittest.main()
