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
    def test_step_and_generated_step_py_are_owned(self):
        self.assertTrue(artifact.owns_entry({"file": "/x/a.step"}))
        self.assertTrue(artifact.owns_entry({"file": "/x/a.STP"}))
        # Generated models are owned too — they get the needs-build/build flow so a
        # not-yet-built .step.py is listed and built on demand.
        self.assertTrue(artifact.owns_entry({"file": "/x/a.step.py"}))
        self.assertTrue(artifact.owns_entry({"file": "/x/a.STP.py"}))
        self.assertFalse(artifact.owns_entry({"file": "/x/a.stl"}))
        self.assertFalse(artifact.owns_entry({"file": "/x/lib.py"}))  # plain .py is not a model
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


def _write_generated_package(root, py_name, *, closure_extra=None, with_package=True):
    """A gen_step generator + optionally its generated component-GLB package
    (sourceKind=python), keyed by the .step.py name like cadpy writes it."""
    py_path = os.path.join(root, py_name)
    with open(py_path, "w") as h:
        h.write("def gen_step():\n    return None\n")
    for rel in (closure_extra or []):
        with open(os.path.join(root, rel), "w") as h:
            h.write("# closure dep\n")
    if not with_package:
        return py_path, None
    pkg = os.path.join(root, "__cadcache__", "models", py_name)
    os.makedirs(os.path.join(pkg, "components"), exist_ok=True)
    with open(os.path.join(pkg, "components", "c0.glb"), "wb") as h:
        h.write(b"glTF\x02\x00\x00\x00")
    descriptor = {
        "kind": "assembly-package",
        "sourceKind": "python",
        "sourcePath": py_name,
        "sourceClosureFiles": [py_name] + list(closure_extra or []),
        "components": {"c0": {"glb": "components/c0.glb"}},
    }
    with open(os.path.join(pkg, "assembly.json"), "w") as h:
        json.dump(descriptor, h)
    return py_path, pkg


class GeneratedStepFreshness(unittest.TestCase):
    def test_built_generated_is_ready(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_generated_package(root, "widget.step.py", closure_extra=["lib.py"])
            ok, code = artifact.validate_step_freshness(root, py)
            self.assertTrue(ok, code)

    def test_unbuilt_generated_is_needs_build(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_generated_package(root, "widget.step.py", with_package=False)
            ok, code = artifact.validate_step_freshness(root, py)
            self.assertFalse(ok)
            self.assertIn(code, artifact.BUILDABLE_STEP_ARTIFACT_CODES)

    def test_stale_when_closure_dep_newer(self):
        import time
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_generated_package(root, "widget.step.py", closure_extra=["lib.py"])
            time.sleep(0.01)
            os.utime(os.path.join(root, "lib.py"), None)  # dep newer than the descriptor
            ok, code = artifact.validate_step_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "stale_step_artifact")


class ScannerListsGenerated(unittest.TestCase):
    def test_unbuilt_step_py_is_collected(self):
        with tempfile.TemporaryDirectory() as root:
            py = os.path.join(root, "widget.step.py")
            with open(py, "w") as h:
                h.write("def gen_step():\n    return None\n")
            # No __cadcache__ at all — it must still be listed (built on demand).
            self.assertIn(py, scanner._collect_cad_source_files(root, []))


if __name__ == "__main__":
    unittest.main()
