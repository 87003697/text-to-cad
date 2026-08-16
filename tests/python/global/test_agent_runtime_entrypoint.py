from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
ENTRYPOINT = REPO_ROOT / "packages/agent_runtime/text-to-cad-agent-entrypoint"


def _load_entrypoint():
    loader = importlib.machinery.SourceFileLoader("production_agent_entrypoint", str(ENTRYPOINT))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("entrypoint spec unavailable")
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ProductionAgentEntrypointTests(unittest.TestCase):
    def test_fixed_entrypoint_bytes_are_executable_and_self_test_without_ambient_authority(self) -> None:
        self.assertEqual(ENTRYPOINT.stat().st_mode & 0o777, 0o555)
        completed = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "--contract-self-test"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            json.loads(completed.stdout),
            {
                "entrypoint": "/usr/local/libexec/text-to-cad-agent-entrypoint",
                "schema": "text-to-cad.agent-entrypoint-control/1",
                "status": "self-test-passed",
            },
        )

    def test_entrypoint_rejects_direct_workload_selection(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ENTRYPOINT), "/bin/true"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 125)
        self.assertIn("outer-owned", completed.stderr)

    def test_control_manifest_binds_workload_and_runtime_bytes(self) -> None:
        entrypoint = _load_entrypoint()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime-manifest.json"
            runtime.write_bytes(b'{"schema":"fixture"}')
            entrypoint.RUNTIME_MANIFEST = runtime
            workload = ["/usr/bin/true"]
            control = {
                "schema": entrypoint.SCHEMA,
                "challenge": "challenge-1",
                "workload": workload,
                "agentImageManifestDigest": "sha256:" + "1" * 64,
                "runtimeManifestDigest": entrypoint._digest(runtime.read_bytes()),
                "executionSourceSnapshotDigest": "sha256:" + "2" * 64,
                "inputSnapshotDigest": "sha256:" + "3" * 64,
                "agentConfigDigest": "sha256:" + "4" * 64,
                "brokerAuthorityDigest": "sha256:" + "5" * 64,
                "workloadDigest": entrypoint._workload_digest(workload),
                "jobId": "job-1",
                "ownerNonce": "owner-1",
            }
            path = root / "control.json"
            path.write_bytes(entrypoint._canonical(control))
            self.assertEqual(entrypoint._load_control(path), control)
            changed = dict(control)
            changed["workload"] = ["/usr/bin/false"]
            path.write_bytes(entrypoint._canonical(changed))
            with self.assertRaises(entrypoint.GateError):
                entrypoint._load_control(path)

    def test_production_entrypoint_is_not_the_throwaway_prototype(self) -> None:
        payload = ENTRYPOINT.read_bytes()
        self.assertNotIn(b"prototypes.agent_runtime_boundary", payload)
        self.assertNotIn(b"from contract import", payload)
        self.assertNotIn(b"import browser_surface", payload)
        prototype = REPO_ROOT / "packages/meshshot/prototypes/agent_runtime_boundary/entrypoint.py"
        self.assertNotEqual(hashlib.sha256(payload).digest(), hashlib.sha256(prototype.read_bytes()).digest())


if __name__ == "__main__":
    unittest.main()
