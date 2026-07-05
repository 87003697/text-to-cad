"""Deterministic unit tests for the /__cad/artifact freshness logic.

Builds a synthetic imported-.step component-GLB package (no cadgen/OCP) and
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
    """Create <root>/<step_name> + its __cadgen__/models/<step_name> package."""
    step_path = os.path.join(root, step_name)
    with open(step_path, "wb") as h:
        h.write(b"ISO-10303-21;\nfake step\n")
    with open(step_path, "rb") as h:
        actual_hash = hashlib.sha256(h.read()).hexdigest()
    pkg = os.path.join(root, "__cadgen__", "models", step_name)
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
    (sourceKind=python), keyed by the .step.py name like cadgen writes it."""
    py_path = os.path.join(root, py_name)
    with open(py_path, "w") as h:
        h.write("def gen_step():\n    return None\n")
    for rel in (closure_extra or []):
        with open(os.path.join(root, rel), "w") as h:
            h.write("# closure dep\n")
    if not with_package:
        return py_path, None
    pkg = os.path.join(root, "__cadgen__", "models", py_name)
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
            # No __cadgen__ at all — it must still be listed (built on demand).
            self.assertIn(py, scanner._collect_cad_source_files(root, []))

    def test_unbuilt_dxf_py_is_collected(self):
        with tempfile.TemporaryDirectory() as root:
            py = os.path.join(root, "outline.dxf.py")
            with open(py, "w") as h:
                h.write("def gen_dxf():\n    return None\n")
            self.assertIn(py, scanner._collect_cad_source_files(root, []))


def _write_drawing_package(root, py_name, *, closure_extra=None, with_package=True, kind="drawing-package"):
    """A gen_dxf generator + optionally its drawing package, keyed by the
    .dxf.py name like cadgen writes it."""
    py_path = os.path.join(root, py_name)
    with open(py_path, "w") as h:
        h.write("def gen_dxf():\n    return None\n")
    for rel in (closure_extra or []):
        with open(os.path.join(root, rel), "w") as h:
            h.write("# closure dep\n")
    if not with_package:
        return py_path, None
    pkg = os.path.join(root, "__cadgen__", "models", py_name)
    os.makedirs(pkg, exist_ok=True)
    with open(os.path.join(pkg, "drawing.dxf"), "w") as h:
        h.write("0\nEOF\n")
    descriptor = {
        "kind": kind,
        "sourceKind": "python",
        "sourcePath": py_name,
        "sourceHash": "abc123",
        "dxf": "drawing.dxf",
        "sourceClosureFiles": [py_name] + list(closure_extra or []),
    }
    with open(os.path.join(pkg, "drawing.json"), "w") as h:
        json.dump(descriptor, h)
    return py_path, pkg


class OwnsDxfEntry(unittest.TestCase):
    def test_generated_dxf_py_is_owned_and_raw_dxf_is_not(self):
        self.assertTrue(artifact.owns_entry({"file": "/x/outline.dxf.py"}))
        self.assertTrue(artifact.owns_dxf_entry({"file": "/x/outline.DXF.PY"}))
        # A raw imported .dxf renders directly from disk — never artifact-managed.
        self.assertFalse(artifact.owns_entry({"file": "/x/outline.dxf"}))
        self.assertFalse(artifact.owns_dxf_entry({"file": "/x/a.step.py"}))
        self.assertFalse(artifact.owns_dxf_entry(None))


class GeneratedDxfFreshness(unittest.TestCase):
    def test_built_drawing_is_ready(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py", closure_extra=["lib.py"])
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertTrue(ok, code)

    def test_unbuilt_drawing_is_needs_build(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py", with_package=False)
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "missing_dxf_artifact")
            self.assertIn(code, artifact.BUILDABLE_STEP_ARTIFACT_CODES)

    def test_missing_drawing_dxf_is_buildable(self):
        with tempfile.TemporaryDirectory() as root:
            py, pkg = _write_drawing_package(root, "outline.dxf.py")
            os.remove(os.path.join(pkg, "drawing.dxf"))
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "missing_dxf_artifact")

    def test_unsupported_descriptor(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py", kind="something-else")
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "unsupported_dxf_artifact")

    def test_stale_when_closure_dep_newer(self):
        import time
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py", closure_extra=["lib.py"])
            time.sleep(0.01)
            os.utime(os.path.join(root, "lib.py"), None)  # dep newer than the descriptor
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "stale_dxf_artifact")

    def test_stale_when_source_newer(self):
        import time
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py")
            time.sleep(0.01)
            os.utime(py, None)
            ok, code = artifact.validate_dxf_freshness(root, py)
            self.assertFalse(ok)
            self.assertEqual(code, "stale_dxf_artifact")


class ScannerDxfEntry(unittest.TestCase):
    def test_built_drawing_entry_carries_cached_asset(self):
        with tempfile.TemporaryDirectory() as root:
            py, pkg = _write_drawing_package(root, "outline.dxf.py")
            entry = scanner.create_generated_dxf_entry(root, root, py)
            self.assertEqual(entry["kind"], "dxf")
            self.assertEqual(entry["file"], "outline.dxf.py")
            self.assertEqual(entry["sourceKind"], "python")
            self.assertIn("__cadgen__/models/outline.dxf.py/drawing.dxf", entry["url"])
            self.assertTrue(entry["hash"])
            self.assertEqual(entry["source"]["sourceHash"], "abc123")

    def test_unbuilt_drawing_entry_has_no_asset(self):
        with tempfile.TemporaryDirectory() as root:
            py, _ = _write_drawing_package(root, "outline.dxf.py", with_package=False)
            entry = scanner.create_generated_dxf_entry(root, root, py)
            self.assertEqual(entry["kind"], "dxf")
            self.assertEqual(entry["url"], "")
            self.assertEqual(entry["hash"], "")

    def test_scan_directory_lists_generated_and_raw_dxf(self):
        with tempfile.TemporaryDirectory() as root:
            _write_drawing_package(root, "outline.dxf.py")
            with open(os.path.join(root, "imported.dxf"), "w") as h:
                h.write("0\nEOF\n")
            catalog = scanner.scan_cad_directory(root, "", include_artifact_status=False)
            by_file = {entry["file"]: entry for entry in catalog["entries"]}
            self.assertIn("outline.dxf.py", by_file)
            self.assertIn("imported.dxf", by_file)
            self.assertEqual(by_file["outline.dxf.py"]["kind"], "dxf")
            self.assertEqual(by_file["outline.dxf.py"].get("sourceKind"), "python")
            self.assertEqual(by_file["imported.dxf"]["kind"], "dxf")
            self.assertNotIn("sourceKind", by_file["imported.dxf"])


if __name__ == "__main__":
    unittest.main()
