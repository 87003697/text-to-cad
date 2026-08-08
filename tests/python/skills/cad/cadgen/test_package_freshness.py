import json
import tempfile
import unittest
from pathlib import Path

from cadgen._internal import generation
from cadgen._internal.component_package import PACKAGE_KIND
from cadgen._internal.glb_topology import read_step_topology_manifest_from_glb


def _write_package(model_dir: Path, entry_name: str, descriptor: dict) -> Path:
    package_dir = model_dir / "__cadgen__" / "models" / entry_name
    (package_dir / "components").mkdir(parents=True)
    (package_dir / "assembly.json").write_text(json.dumps(descriptor), encoding="utf-8")
    return package_dir


class DirAwareManifestReaderTests(unittest.TestCase):
    def test_package_directory_returns_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            descriptor = {"kind": PACKAGE_KIND, "sourceKind": "python", "schemaVersion": 2}
            package_dir = _write_package(root, "part.step.py", descriptor)
            manifest = read_step_topology_manifest_from_glb(package_dir)
            self.assertIsInstance(manifest, dict)
            self.assertEqual(manifest.get("kind"), PACKAGE_KIND)

    def test_directory_without_descriptor_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertIsNone(read_step_topology_manifest_from_glb(Path(temp)))


class PackageFreshnessGateTests(unittest.TestCase):
    def _generated_spec(self, model_dir: Path) -> generation.EntrySpec:
        script = model_dir / "part.step.py"
        script.write_text("def gen_step():\n    return None\n", encoding="utf-8")
        return generation.EntrySpec(
            source_ref="part.step.py",
            cad_ref="part",
            kind="assembly",
            source_path=script,
            display_name="part",
            source="generated",
            step_path=model_dir / "part.step",
            script_path=script,
        )

    def test_assembly_glb_package_current_keys_by_entry_filename(self) -> None:
        # The package lives at __cadgen__/models/part.step.py (entry filename);
        # keying by the logical step path (part.step) must not be required.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = self._generated_spec(root)
            package_dir = _write_package(
                root,
                "part.step.py",
                {
                    "kind": PACKAGE_KIND,
                    "components": {"abc": {"glb": "components/abc.glb"}},
                },
            )
            (package_dir / "components" / "abc.glb").write_bytes(b"glTF-fake")
            self.assertTrue(generation._assembly_glb_package_current(spec))

    def test_assembly_glb_package_current_false_when_component_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = self._generated_spec(root)
            _write_package(
                root,
                "part.step.py",
                {
                    "kind": PACKAGE_KIND,
                    "components": {"abc": {"glb": "components/abc.glb"}},
                },
            )
            self.assertFalse(generation._assembly_glb_package_current(spec))

    def test_package_descriptor_matches_spec_returns_none_without_package(self) -> None:
        # No package on disk: the gate must fall back to the monolith validator
        # rather than deciding freshness itself.
        with tempfile.TemporaryDirectory() as temp:
            spec = self._generated_spec(Path(temp))
            self.assertIsNone(generation._package_descriptor_matches_spec(spec))


class TopologySidecarGateTests(unittest.TestCase):
    def _match(self, manifest, descriptor):
        from cadgen.step_artifacts import _topology_sidecar_matches_descriptor

        return _topology_sidecar_matches_descriptor(manifest, descriptor)

    def test_matches_on_same_closure_regardless_of_mesh_drift(self) -> None:
        descriptor = {"sourceClosureHash": "abc", "mesh": {"linearDeflection": 1.0}}
        manifest = {"sourceClosureHash": "abc", "mesh": {"linearDeflection": 2.0}}
        self.assertTrue(self._match(manifest, descriptor))

    def test_rejects_closure_mismatch(self) -> None:
        self.assertFalse(
            self._match({"sourceClosureHash": "old"}, {"sourceClosureHash": "new"})
        )

    def test_rejects_when_no_provenance_recorded(self) -> None:
        self.assertFalse(self._match({}, {}))

    def test_imported_entry_matches_on_step_hash(self) -> None:
        self.assertTrue(
            self._match({"stepHash": "s1"}, {"stepHash": "s1"})
        )
        self.assertFalse(
            self._match({"stepHash": "s1"}, {"stepHash": "s2"})
        )

    def test_rejects_non_dict_manifest(self) -> None:
        self.assertFalse(self._match(None, {"sourceClosureHash": "abc"}))


if __name__ == "__main__":
    unittest.main()
