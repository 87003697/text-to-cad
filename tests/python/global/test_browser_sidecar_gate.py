"""Public behavior tests for the fixed in-bwrap Browser Gate."""

from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")


REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_PATH = REPO_ROOT / "scripts/pilot/browser_sidecar_gate.py"
CONTRACT_PATH = REPO_ROOT / "packages/meshshot/src/meshshot/browser_contract.json"


def load_gate():
    """Load the fixed-path gate using the repository-owned contract fixture."""

    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    spec = importlib.util.spec_from_file_location("browser_sidecar_gate", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    original = Path.read_text

    def fixed_read(path: Path, *args, **kwargs):
        if path == Path("/run/meshshot-gate/meshshot-src/meshshot/browser_contract.json"):
            return contract
        return original(path, *args, **kwargs)

    with mock.patch.object(Path, "read_text", fixed_read):
        spec.loader.exec_module(module)
    return module


class BrowserSidecarGateTests(unittest.TestCase):
    """Exercise the repository-owned gate with no caller-selected render input."""

    def test_fixed_gate_calls_public_residual_and_registered_viewer(self) -> None:
        gate = load_gate()
        public_render = gate.render_residual_preview(
            gate.MeshGeometry(
                vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
                faces=[[0, 1, 2]],
            ),
            gate.MeshGeometry(
                vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
                faces=[[0, 1, 2]],
            ),
            variant="step",
            exterior_directions=[],
        )
        viewer = {
            "title": "CAD Viewer | browser_sidecar_inspection.step",
            "modelKey": "inspection-step",
            "programDigest": gate.CONTRACT["programs"]["viewer"],
            "screenshotDataUrl": "data:image/png;base64,cG5n",
            "screenshotSha256": "0" * 64,
            "screenshotBytes": 3,
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
            "inspection": {
                "control": "toggle-projection",
                "before": "Display and projection: Solid, Orthographic",
                "target": "Perspective",
                "after": "Display and projection: Solid, Perspective",
                "changed": True,
            },
        }
        with (
            mock.patch.object(
                gate,
                "render_residual_preview",
                return_value=public_render,
            ) as public_api,
            mock.patch.object(
                gate.hashlib,
                "sha256",
                return_value=SimpleNamespace(
                    hexdigest=lambda: gate.GATE["publicPngSha256"]
                ),
            ),
            mock.patch.object(gate, "_viewer_request", return_value=viewer) as request,
            mock.patch.object(gate, "_browser_processes", return_value=[]),
            mock.patch.object(gate.shutil, "which", return_value=None),
            mock.patch.object(gate, "urlopen", side_effect=OSError("blocked")),
        ):
            proof = gate.run_gate_checks()

        self.assertEqual(proof["schema"], gate.GATE["schema"])
        self.assertEqual(proof["status"], "succeeded")
        self.assertTrue(all(proof["predicates"].values()))
        self.assertNotIn("sourceHidden", proof["predicates"])
        self.assertNotIn("egressBlocked", proof["predicates"])
        self.assertEqual(
            proof["residual"],
            {
                "pngSha256": "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b",
                "mode": "RGB",
                "size": [504, 1008],
                "profileSha256": "87da3cc3f625cb9c24f51bed41dcdc70402a4d461b2af29eaa19846b1e8f7241",
                "views": ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            },
        )
        self.assertEqual(
            proof["inventory"],
            {
                "browserExecutables": [],
                "browserPackages": [],
                "browserCaches": [],
                "browserProcesses": [],
            },
        )
        request.assert_called_once_with()
        public_api.assert_called_once()
        self.assertEqual(public_api.call_args.kwargs, {
            "variant": "step",
            "exterior_directions": [],
        })

    def test_gate_exec_surface_has_no_render_arguments_or_shell(self) -> None:
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("os.execvpe(workload[0], workload", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("add_argument", source)
        self.assertNotIn("MESHSHOT_BROWSER_AUTHORITY_FILE", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("SOURCE_ALIASES", source)

    def test_gate_artifact_and_proof_are_bound_to_read_only_job_input(self) -> None:
        """The sealed gate validates its bytes, job, nonce, and surface manifest."""

        gate = load_gate()
        expected = {
            "schema": "meshshot.browser-sidecar.nested-gate-input/1",
            "jobId": "formal-job-1",
            "nonce": "a" * 32,
            "artifactSha256": "b" * 64,
            "surfaceManifest": {
                "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
                "scanRoots": ["/usr", "/workspace/repo/.venv"],
                "browserExclusions": [],
            },
        }
        canonical = json.dumps(
            expected["surfaceManifest"], sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        with (
            mock.patch.object(gate, "GATE_INPUT_PATH", Path("/fixed/gate-input.json")),
            mock.patch.object(Path, "read_bytes", return_value=(json.dumps(expected) + "\n").encode()),
            mock.patch.object(gate, "_artifact_sha256", return_value="b" * 64),
        ):
            identity = gate.load_gate_identity()
        self.assertEqual(identity["jobId"], "formal-job-1")
        self.assertEqual(identity["nonce"], "a" * 32)
        self.assertEqual(
            identity["surfaceManifestSha256"], hashlib.sha256(canonical).hexdigest()
        )


if __name__ == "__main__":
    unittest.main()
