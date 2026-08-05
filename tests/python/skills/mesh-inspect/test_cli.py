from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_PATH = (
    REPO_ROOT
    / "skills"
    / "mesh-inspect"
    / "scripts"
    / "mesh-inspect"
    / "cli.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("mesh_inspect_cli", CLI_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MeshInspectCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cli = _load_cli()

    def test_output_writes_json_instead_of_stdout(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "nested" / "mesh_stats.json"
            stdout = io.StringIO()
            with mock.patch.object(
                self.cli,
                "inspect",
                return_value={"vertices": 42, "faces": 80},
            ), redirect_stdout(stdout):
                status = self.cli.main(["input.ply", "--output", str(output)])

            self.assertEqual(status, 0)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")),
                {"ok": True, "vertices": 42, "faces": 80},
            )

    def test_output_write_failure_returns_json_error(self):
        stdout = io.StringIO()
        with mock.patch.object(
            self.cli,
            "inspect",
            return_value={"vertices": 42, "faces": 80},
        ), mock.patch.object(
            Path,
            "write_text",
            side_effect=OSError("disk full"),
        ), redirect_stdout(stdout):
            status = self.cli.main(["input.3mf", "--output", "mesh_stats.json"])

        self.assertEqual(status, 2)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "ok": False,
            "errors": ["disk full"],
        })


if __name__ == "__main__":
    unittest.main()
