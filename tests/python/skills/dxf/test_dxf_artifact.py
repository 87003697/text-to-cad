"""Tests for the viewer-facing cadgen.dxf_artifact build/export command."""

import json
import textwrap
import unittest
from pathlib import Path

from cadgen import dxf_artifact
from tests.python.support.tmp_root import temporary_directory

DXF_GENERATOR_SOURCE = textwrap.dedent(
    '''
    """Standalone DXF drawing generator."""

    import ezdxf


    def gen_dxf():
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True)
        return doc
    '''
).strip()


class DxfArtifactTests(unittest.TestCase):
    def _write_generator(self, root: Path) -> Path:
        script_path = root / "outline.dxf.py"
        script_path.write_text(DXF_GENERATOR_SOURCE + "\n", encoding="utf-8")
        return script_path

    def test_builds_drawing_package_and_skips_when_current(self) -> None:
        with temporary_directory(prefix="dxf-artifact") as root:
            script_path = self._write_generator(Path(root))

            payload = dxf_artifact.build_dxf_artifact(repo_root=Path(root), source_path=script_path)
            self.assertTrue(payload["ok"])
            self.assertNotIn("skipped", payload)
            package_dir = Path(root) / "__cadcache__" / "models" / "outline.dxf.py"
            self.assertTrue((package_dir / "drawing.dxf").is_file())

            payload = dxf_artifact.build_dxf_artifact(repo_root=Path(root), source_path=script_path)
            self.assertTrue(payload.get("skipped"))

            payload = dxf_artifact.build_dxf_artifact(
                repo_root=Path(root), source_path=script_path, force=True
            )
            self.assertNotIn("skipped", payload)

    def test_rejects_plain_py_generator(self) -> None:
        with temporary_directory(prefix="dxf-artifact") as root:
            script_path = Path(root) / "outline.py"
            script_path.write_text(DXF_GENERATOR_SOURCE + "\n", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "dedicated <name>.dxf.py generator"):
                dxf_artifact.build_dxf_artifact(repo_root=Path(root), source_path=script_path)

    def test_export_writes_fresh_dxf_with_relocated_identity(self) -> None:
        with temporary_directory(prefix="dxf-artifact") as root:
            script_path = self._write_generator(Path(root))
            export_path = Path(root) / "exports" / "outline.dxf"

            payload = dxf_artifact.build_dxf_artifact(
                repo_root=Path(root), source_path=script_path, export=export_path
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(str(export_path), payload["path"])
            self.assertEqual("outline.dxf", payload["filename"])
            self.assertTrue(export_path.is_file())
            text = export_path.read_text(encoding="utf-8")
            # The identity comment points at the generator relative to the export.
            self.assertIn("cadgen:sourcePath=../outline.dxf.py", text)
            descriptor = json.loads(
                (Path(root) / "__cadcache__" / "models" / "outline.dxf.py" / "drawing.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn(f"cadgen:sourceHash={descriptor['sourceHash']}", text)


if __name__ == "__main__":
    unittest.main()
