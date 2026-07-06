import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import build123d

from cadgen._internal import component_package as cp


class ComponentWorkerDeterminismTests(unittest.TestCase):
    @staticmethod
    def _glb_json(path: Path) -> dict:
        import struct

        data = path.read_bytes()
        length = struct.unpack_from("<I", data, 12)[0]
        return json.loads(data[20 : 20 + length])

    def test_component_structure_is_class_independent(self) -> None:
        # Both build paths normalize through the BREP payload, so a parametric
        # Box and a plain Solid wrapping the same geometry emit structurally
        # identical components: XCAF auto-labels (e.g. "=>[0:1:1:2]") must not
        # leak into node/mesh names. Byte equality is NOT asserted: the
        # mesh/extract stage is not byte-reproducible run-to-run (pre-existing;
        # cids hash geometry, not bytes, so correctness is unaffected).
        box = build123d.Box(20.0, 12.0, 6.0).moved(build123d.Location((100, 50, 25)))
        solid = build123d.Solid(box.wrapped)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            gltfs = []
            for name, shape in (("box.glb", box), ("solid.glb", solid)):
                out = root / name
                cid, error = cp._build_component_glb_worker(
                    (cp._shape_brep_bytes(shape), "cid-test", str(out), 0.5, 0.3)
                )
                self.assertIsNone(error)
                self.assertEqual("cid-test", cid)
                gltfs.append(self._glb_json(out))
            names_a = [node.get("name") for node in gltfs[0].get("nodes", [])]
            names_b = [node.get("name") for node in gltfs[1].get("nodes", [])]
            self.assertEqual(names_a, names_b)
            extras_a = [node.get("extras") for node in gltfs[0].get("nodes", [])]
            extras_b = [node.get("extras") for node in gltfs[1].get("nodes", [])]
            self.assertEqual(extras_a, extras_b)
            self.assertEqual(
                [m.get("name") for m in gltfs[0].get("meshes", [])],
                [m.get("name") for m in gltfs[1].get("meshes", [])],
            )
            for gltf in gltfs:
                self.assertEqual(1, len(gltf.get("meshes", [])))
                self.assertNotIn("=>", json.dumps(gltf.get("nodes", [])))

    def test_worker_reports_error_instead_of_raising(self) -> None:
        cid, error = cp._build_component_glb_worker(
            (b"not a brep", "cid-bad", "/nonexistent/out.glb", 0.5, 0.3)
        )
        self.assertEqual("cid-bad", cid)
        self.assertIsNotNone(error)


class ComponentWorkerCountTests(unittest.TestCase):
    def test_small_builds_stay_serial(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertEqual(1, cp._component_build_worker_count(1))
            self.assertEqual(1, cp._component_build_worker_count(5))

    def test_large_builds_parallelize(self) -> None:
        with mock.patch.dict("os.environ", {"CADGEN_COMPONENT_WORKERS": ""}):
            self.assertGreater(cp._component_build_worker_count(24), 1)

    def test_env_override_disables(self) -> None:
        with mock.patch.dict("os.environ", {"CADGEN_COMPONENT_WORKERS": "1"}):
            self.assertEqual(1, cp._component_build_worker_count(50))
        with mock.patch.dict("os.environ", {"CADGEN_COMPONENT_WORKERS": "0"}):
            self.assertEqual(1, cp._component_build_worker_count(50))

    def test_env_override_sets_count(self) -> None:
        with mock.patch.dict("os.environ", {"CADGEN_COMPONENT_WORKERS": "3"}):
            self.assertEqual(3, cp._component_build_worker_count(50))


class OrphanComponentPruningTests(unittest.TestCase):
    def test_unreferenced_component_glbs_are_pruned(self) -> None:
        part = build123d.Box(10.0, 8.0, 4.0)
        part.label = "box"
        compound = build123d.Compound(obj=[part], children=[part], label="asm")
        with tempfile.TemporaryDirectory() as temp:
            package_dir = Path(temp) / "pkg"
            comp_dir = package_dir / cp.COMPONENT_DIRNAME
            comp_dir.mkdir(parents=True)
            orphan = comp_dir / "deadbeefdeadbeef.glb"
            orphan.write_bytes(b"stale bytes from an earlier geometry revision")

            stats = cp.build_package_from_compound(
                compound,
                package_dir=package_dir,
                root_name="asm",
                linear_deflection=0.5,
                angular_deflection=0.3,
            )

            self.assertFalse(orphan.exists())
            self.assertEqual(1, stats["components_pruned"])
            descriptor = json.loads((package_dir / "assembly.json").read_text())
            for entry in descriptor["components"].values():
                self.assertTrue((package_dir / entry["glb"]).is_file())


if __name__ == "__main__":
    unittest.main()
