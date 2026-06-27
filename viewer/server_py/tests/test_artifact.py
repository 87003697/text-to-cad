"""Deterministic unit tests for the /__cad/artifact freshness logic.

Builds a synthetic imported-.step component-GLB package (no cadpy/OCP) and
checks the state machine: ready / stale_step_artifact / missing_glb /
unsupported, the owns_entry gate, and the generation-lock reader.
"""

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import artifact, scanner  # noqa: E402


def _write_package(root, step_name, *, source_kind="step", step_hash=None, components=None):
    """Create <root>/<step_name> + its __cadcache__/models/<step_name> package."""
    step_path = os.path.join(root, step_name)
    with open(step_path, "wb") as h:
        h.write(b"ISO-10303-21;\nfake step\n")
    with open(step_path, "rb") as h:
        actual_hash = hashlib.sha256(h.read()).hexdigest()
    pkg = os.path.join(root, "__cadcache__", "models", step_name)
    comp_dir = os.path.join(pkg, "components")
    os.makedirs(comp_dir, exist_ok=True)
    comps = {}
    for cid in (components if components is not None else ["c0"]):
        rel = f"components/{cid}.glb"
        with open(os.path.join(pkg, rel), "wb") as h:
            h.write(b"glTF\x02\x00\x00\x00")
        comps[cid] = {"glb": rel}
    descriptor = {
        "kind": "assembly-package",
        "sourceKind": source_kind,
        "stepHash": step_hash if step_hash is not None else actual_hash,
        "components": comps,
    }
    with open(os.path.join(pkg, "assembly.json"), "w") as h:
        json.dump(descriptor, h)
    return step_path, pkg


class OwnsEntry(unittest.TestCase):
    def test_only_imported_step_is_owned(self):
        self.assertTrue(artifact.owns_entry({"file": "/x/a.step"}))
        self.assertTrue(artifact.owns_entry({"file": "/x/a.STP"}))
        self.assertFalse(artifact.owns_entry({"file": "/x/a.step.py"}))  # generated -> direct
        self.assertFalse(artifact.owns_entry({"file": "/x/a.stl"}))
        self.assertFalse(artifact.owns_entry(None))


class ImportedStepFreshness(unittest.TestCase):
    def test_fresh_package_is_ready(self):
        with tempfile.TemporaryDirectory() as d:
            step, _ = _write_package(d, "imp.step")
            self.assertEqual(artifact.validate_step_freshness(d, step), (True, None))

    def test_stale_step_hash(self):
        with tempfile.TemporaryDirectory() as d:
            step, _ = _write_package(d, "imp.step", step_hash="deadbeef")
            self.assertEqual(artifact.validate_step_freshness(d, step), (False, "stale_step_artifact"))

    def test_missing_component_glb(self):
        with tempfile.TemporaryDirectory() as d:
            step, pkg = _write_package(d, "imp.step")
            os.remove(os.path.join(pkg, "components", "c0.glb"))
            self.assertEqual(artifact.validate_step_freshness(d, step), (False, "missing_glb"))

    def test_unsupported_descriptor(self):
        with tempfile.TemporaryDirectory() as d:
            step, pkg = _write_package(d, "imp.step")
            with open(os.path.join(pkg, "assembly.json"), "w") as h:
                json.dump({"kind": "something-else"}, h)
            self.assertEqual(artifact.validate_step_freshness(d, step), (False, "unsupported_step_topology"))

    def test_missing_package_is_buildable(self):
        with tempfile.TemporaryDirectory() as d:
            step = os.path.join(d, "imp.step")
            open(step, "wb").close()
            self.assertEqual(artifact.validate_step_freshness(d, step), (False, "missing_glb"))


class GenerationLock(unittest.TestCase):
    def _write_lock(self, path, pid, status="running", updated_at="2999-01-01T00:00:00Z"):
        with open(path, "w") as h:
            json.dump({"status": status, "pid": pid, "updatedAt": updated_at}, h)

    def test_live_recent_lock_active(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "x.lock.json")
            self._write_lock(lp, os.getpid())
            self.assertTrue(artifact.generation_lock_active(lp))

    def test_dead_pid_inactive(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "x.lock.json")
            self._write_lock(lp, 999999)  # almost certainly dead
            self.assertFalse(artifact.generation_lock_active(lp))

    def test_stale_heartbeat_inactive(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "x.lock.json")
            self._write_lock(lp, os.getpid(), updated_at="2000-01-01T00:00:00Z")
            self.assertFalse(artifact.generation_lock_active(lp))

    def test_not_running_inactive(self):
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "x.lock.json")
            self._write_lock(lp, os.getpid(), status="done")
            self.assertFalse(artifact.generation_lock_active(lp))


if __name__ == "__main__":
    unittest.main()
