"""Adversarial tests for Workspace-bound Reference Binding.

These tests prove that the production Reference Capability is bound to
exactly the Workspace Canonical Reference and that no ambient
``AGENT_REFERENCE_PATH`` environment override or foreign identity claim
can influence the bound bytes.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import runner as runner_module
from scripts.pilot.workspace_supervisor import (
    SupervisorError,
    WorkspaceSupervisor,
    _load_workspace_api,
)


_PLY_HEADER = (
    b"ply\n"
    b"format binary_little_endian 1.0\n"
    b"element vertex 0\n"
    b"element face 0\n"
    b"end_header\n"
)


def _synthetic_ply(marker: bytes) -> bytes:
    return _PLY_HEADER + marker


def _tree_digest(root: Path) -> str:
    document = [
        {
            "path": child.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(child.read_bytes()).hexdigest(),
        }
        for child in sorted(root.rglob("*"))
        if child.is_file()
    ]
    return hashlib.sha256(
        json.dumps({"files": document}, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ReferenceBindingFacadeTests(unittest.TestCase):
    """Prove the Workspace facade proves reference identity before returning."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.workspace.mkdir()
        self.setup_dir = self.workspace / "setup"
        self.setup_dir.mkdir()
        self.input_dir = self.workspace / "input"
        self.input_dir.mkdir()

        self.reference_a = _synthetic_ply(b"A" * 32)
        self.reference_b = _synthetic_ply(b"B" * 32)
        self.sha_a = hashlib.sha256(self.reference_a).hexdigest()
        self.sha_b = hashlib.sha256(self.reference_b).hexdigest()
        self.assertNotEqual(self.sha_a, self.sha_b)

        (self.input_dir / "reference.ply").write_bytes(self.reference_a)
        self.canonical_reference_sha256 = "c" * 64
        input_manifest = {
            "schema": "voxblame.canonical-reference/1",
            "coordinate_contract": "trellis2_canonical/1",
            "semantic_units": None,
            "boundary_epsilon": 1e-9,
            "canonical_reference_sha256": self.canonical_reference_sha256,
            "captured_files": [],
            "reference_ply": {
                "path": "reference.ply",
                "sha256": self.sha_a,
                "size_bytes": len(self.reference_a),
                "format": "binary_little_endian",
                "vertex_dtype": "float64",
                "face_index_dtype": "int32",
            },
            "normalization_json": {
                "path": "normalization.json",
                "sha256": "0" * 64,
                "size_bytes": 0,
            },
            "triangle_set_sha256": "1" * 64,
            "input_triangle_count": 0,
            "removed_zero_area_triangle_count": 0,
            "canonical_triangle_count": 0,
        }
        (self.input_dir / "input.json").write_text(
            json.dumps(input_manifest, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        input_digest = _tree_digest(self.input_dir)
        setup_digest = _tree_digest(self.setup_dir)
        workspace_document = {
            "schema": "mesh-to-cad.workspace/1",
            "workspace_id": "reference-binding-test",
            "coordinate_contract": "trellis2_canonical/1",
            "canonical_reference_sha256": self.canonical_reference_sha256,
            "preview_profile": {"name": "test", "sha256": "d" * 64},
            "input_identity_sha256": input_digest,
            "setup_identity_sha256": setup_digest,
            "limits": {
                "repair_cycles": 5,
                "attempts_per_step": 3,
                "tool_failures_per_step": 2,
            },
        }
        (self.workspace / "workspace.json").write_text(
            json.dumps(workspace_document, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )

        self.facade = _load_workspace_api()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _no_host_paths_in_error(self, error: BaseException) -> None:
        rendered = repr(error)
        workspace_absolute = os.fspath(self.workspace)
        reference_absolute = os.fspath(self.input_dir / "reference.ply")
        self.assertNotIn(workspace_absolute, rendered)
        self.assertNotIn(reference_absolute, rendered)

    def test_binding_returns_workspace_canonical_reference(self) -> None:
        binding = self.facade.read_canonical_reference_binding(self.workspace)
        self.assertEqual(
            (self.input_dir / "reference.ply").resolve(),
            Path(binding["path"]).resolve(),
        )
        self.assertEqual(self.sha_a, binding["reference_ply_sha256"])
        self.assertEqual(
            self.canonical_reference_sha256,
            binding["canonical_reference_sha256"],
        )

    def test_byte_injection_at_bound_reference_fails_closed(self) -> None:
        # Swap the trusted Canonical Reference bytes for a different valid
        # PLY.  Any attempt to bind must fail before returning a capability.
        (self.input_dir / "reference.ply").write_bytes(self.reference_b)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.read_canonical_reference_binding(self.workspace)
        self._no_host_paths_in_error(raised.exception)

    def test_ambient_agent_reference_path_env_is_ignored(self) -> None:
        # Setting the historical override in the environment must not change
        # the bound Canonical Reference: the facade derives everything from
        # trusted Workspace state.
        self.assertEqual(
            os.fspath((self.input_dir / "reference.ply").resolve()),
            os.fspath(
                Path(
                    self.facade.read_canonical_reference_binding(self.workspace)["path"]
                ).resolve()
            ),
        )
        os.environ["AGENT_REFERENCE_PATH"] = "/tmp/attacker-controlled.ply"
        try:
            binding = self.facade.read_canonical_reference_binding(self.workspace)
        finally:
            os.environ.pop("AGENT_REFERENCE_PATH", None)
        self.assertEqual(
            (self.input_dir / "reference.ply").resolve(),
            Path(binding["path"]).resolve(),
        )
        self.assertEqual(self.sha_a, binding["reference_ply_sha256"])


class RunnerAndSupervisorSurfaceTests(unittest.TestCase):
    """Prove the production runner and supervisor no longer accept ambient overrides."""

    def test_runner_source_has_no_agent_reference_path_override(self) -> None:
        source = inspect.getsource(runner_module)
        self.assertNotIn("AGENT_REFERENCE_PATH", source)

    def test_supervisor_init_no_longer_accepts_reference_path_kwarg(self) -> None:
        signature = inspect.signature(WorkspaceSupervisor.__init__)
        self.assertNotIn("reference_path", signature.parameters)
        self.assertIn("bind_reference", signature.parameters)

    def test_supervisor_requires_workspace_api_binding_helper(self) -> None:
        class _NoBinding:
            def workspace_status(self, _workspace: Path) -> dict:
                return {}

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            with self.assertRaises(SupervisorError):
                WorkspaceSupervisor(
                    workspace,
                    bind_reference=True,
                    candidate_root=Path(tmp) / "candidate",
                    staging_dir=Path(tmp) / "staging",
                    workspace_api=_NoBinding(),
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
