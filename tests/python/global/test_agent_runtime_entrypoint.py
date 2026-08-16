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
from unittest import mock


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

    def test_entrypoint_uses_the_shared_canonical_json_seam(self) -> None:
        payload = ENTRYPOINT.read_bytes()
        self.assertNotIn(b"import json", payload)
        self.assertIn(b"agent_runtime_canonical_json", payload)

    def test_signal_is_latched_before_spawn_and_replayed_to_the_process_group(self) -> None:
        entrypoint = _load_entrypoint()

        class Process:
            pid = 4242

        class Adapter:
            def __init__(self):
                self.events = []

            def install_handlers(self, handler):
                self.handler = handler
                self.events.append("handlers")
                handler(entrypoint.signal.SIGTERM, None)
                return {}

            def restore_handlers(self, _token): self.events.append("restore")
            def spawn(self, _argv, _stdout, _stderr): self.events.append("spawn"); return Process()
            def wait(self, _process): self.events.append("wait"); return -entrypoint.signal.SIGTERM
            def group_exists(self, _pgid): return False
            def signal_group(self, pgid, signum): self.events.append(("signal", pgid, signum))
            def monotonic(self): return 0.0
            def sleep(self, _seconds): pass

        adapter = Adapter()
        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory) / name for name in ("home", "cache", "tmp", "work", "output"))
            for path in paths:
                path.mkdir()
            with mock.patch.object(entrypoint, "WRITABLE", paths):
                result = entrypoint._run_workload(["/usr/bin/true"], adapter=adapter)
        self.assertEqual(result.returncode, 143)
        self.assertEqual(result.interrupted_signal, entrypoint.signal.SIGTERM)
        self.assertTrue(result.group_absent)
        self.assertEqual(adapter.events[:4], ["handlers", "spawn", ("signal", 4242, entrypoint.signal.SIGTERM), "wait"])
        self.assertEqual(adapter.events[-1], "restore")

    def test_descendant_residue_is_cleaned_before_failed_terminal_status(self) -> None:
        entrypoint = _load_entrypoint()

        class Process:
            pid = 7171

        class Adapter:
            def __init__(self, persistent=False):
                self.present = True
                self.persistent = persistent
                self.signals = []
                self.clock = 0.0
            def install_handlers(self, _handler): return {}
            def restore_handlers(self, _token): pass
            def spawn(self, _argv, _stdout, _stderr): return Process()
            def wait(self, _process): return 0
            def group_exists(self, _pgid): return self.present
            def signal_group(self, _pgid, signum):
                self.signals.append(signum)
                if not self.persistent and signum == entrypoint.signal.SIGTERM:
                    self.present = False
            def monotonic(self): self.clock += 4.0; return self.clock
            def sleep(self, _seconds): pass

        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory) / name for name in ("home", "cache", "tmp", "work", "output"))
            for path in paths:
                path.mkdir()
            with mock.patch.object(entrypoint, "WRITABLE", paths):
                adapter = Adapter()
                result = entrypoint._run_workload(["/usr/bin/true"], adapter=adapter)
                self.assertEqual(result, entrypoint.GroupResult(125, True, True, None))
                self.assertEqual(adapter.signals, [entrypoint.signal.SIGTERM])
                (paths[-1] / "workload.stdout").unlink()
                (paths[-1] / "workload.stderr").unlink()
                persistent = Adapter(persistent=True)
                failed = entrypoint._run_workload(["/usr/bin/true"], adapter=persistent)
        self.assertEqual(failed.returncode, 125)
        self.assertTrue(failed.descendant_residue)
        self.assertFalse(failed.group_absent)
        self.assertEqual(persistent.signals, [entrypoint.signal.SIGTERM, entrypoint.signal.SIGKILL])

    def test_spawn_and_exclusive_output_errors_are_uniform_status_125(self) -> None:
        entrypoint = _load_entrypoint()

        class Adapter:
            def install_handlers(self, _handler): return {}
            def restore_handlers(self, _token): pass
            def spawn(self, _argv, _stdout, _stderr): raise OSError("spawn denied")
            def group_exists(self, _pgid): return False

        with tempfile.TemporaryDirectory() as directory:
            paths = tuple(Path(directory) / name for name in ("home", "cache", "tmp", "work", "output"))
            for path in paths:
                path.mkdir()
            with mock.patch.object(entrypoint, "WRITABLE", paths):
                result = entrypoint._run_workload(["/missing"], adapter=Adapter())
                self.assertEqual(result.returncode, 125)
                (paths[-1] / "workload.stdout").write_bytes(b"occupied")
                collision = entrypoint._run_workload(["/missing"], adapter=Adapter())
        self.assertEqual(collision.returncode, 125)

    def test_output_digest_binds_paths_modes_and_bytes_and_rejects_symlinks(self) -> None:
        entrypoint = _load_entrypoint()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "artifact.txt"
            first.write_bytes(b"one")
            first.chmod(0o600)
            initial = entrypoint._output_digest(root)
            first.write_bytes(b"two")
            self.assertNotEqual(entrypoint._output_digest(root), initial)
            content_changed = entrypoint._output_digest(root)
            first.chmod(0o400)
            self.assertNotEqual(entrypoint._output_digest(root), content_changed)
            first.rename(root / "renamed.txt")
            self.assertNotEqual(entrypoint._output_digest(root), content_changed)
            (root / "link").symlink_to(root / "renamed.txt")
            with self.assertRaises(entrypoint.GateError):
                entrypoint._output_digest(root)

    def test_terminal_is_published_only_after_group_absence_with_closed_observations(self) -> None:
        entrypoint = _load_entrypoint()
        identity = {
            "agentImageManifestDigest": "sha256:" + "1" * 64,
            "runtimeManifestDigest": "sha256:" + "2" * 64,
            "executionSourceSnapshotDigest": "sha256:" + "3" * 64,
            "inputSnapshotDigest": "sha256:" + "4" * 64,
            "agentConfigDigest": "sha256:" + "5" * 64,
            "brokerAuthorityDigest": "sha256:" + "6" * 64,
            "workloadDigest": "sha256:" + "7" * 64,
            "jobId": "job",
            "ownerNonce": "owner",
        }
        control = {"schema": entrypoint.SCHEMA, "challenge": "challenge", "workload": ["/usr/bin/true"], **identity}
        proof = {"schema": "text-to-cad.agent-broker-proof/1", "challenge": "challenge", "brokerMac": "a" * 64, **identity}
        proof_digest = entrypoint._digest(entrypoint._canonical(proof))
        release = {"schema": "text-to-cad.agent-entrypoint-release/1", "brokerProofDigest": proof_digest, "release": True}
        published = []
        with mock.patch.object(entrypoint.sys, "argv", [str(ENTRYPOINT)]), mock.patch.object(entrypoint, "_load_control", return_value=control), mock.patch.object(entrypoint, "_preflight"), mock.patch.object(entrypoint, "_socket_exchange", return_value=proof), mock.patch.object(entrypoint, "_read_release", return_value=release), mock.patch.object(entrypoint, "_run_workload", return_value=entrypoint.GroupResult(143, False, True, 15)), mock.patch.object(entrypoint, "_output_digest", return_value="sha256:" + "8" * 64), mock.patch.object(entrypoint, "_publish", side_effect=published.append):
            self.assertEqual(entrypoint.main(), 143)
        terminal = published[-1]
        self.assertEqual(terminal["workloadStatus"], 143)
        self.assertEqual(terminal["interruptedSignal"], 15)
        self.assertTrue(terminal["processGroupAbsent"])
        self.assertFalse(terminal["descendantResidue"])
        self.assertEqual(terminal["outputDigest"], "sha256:" + "8" * 64)

        published.clear()
        with mock.patch.object(entrypoint.sys, "argv", [str(ENTRYPOINT)]), mock.patch.object(entrypoint, "_load_control", return_value=control), mock.patch.object(entrypoint, "_preflight"), mock.patch.object(entrypoint, "_socket_exchange", return_value=proof), mock.patch.object(entrypoint, "_read_release", return_value=release), mock.patch.object(entrypoint, "_run_workload", return_value=entrypoint.GroupResult(125, True, False, None)), mock.patch.object(entrypoint, "_publish", side_effect=published.append):
            with self.assertRaises(entrypoint.GateError):
                entrypoint.main()
        self.assertEqual(len(published), 1)


if __name__ == "__main__":
    unittest.main()
