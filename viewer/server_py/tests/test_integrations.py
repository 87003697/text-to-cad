"""Unit tests for the Open-in integrations module (detection env seams, launch
recipes, validation) and the backend STEP materialization cache."""

import os
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from server_py import backend as backend_mod  # noqa: E402
from server_py import integrations  # noqa: E402

_ENV_KEYS = (
    "VIEWER_OPEN_IN_PLATFORM",
    "VIEWER_OPEN_IN_FORCE_INSTALLED",
    "VIEWER_DISABLE_APP_LAUNCH",
)


class _EnvIsolatedTest(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        for k in _ENV_KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class IntegrationTargets(_EnvIsolatedTest):
    def test_platform_filtering_darwin(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        payload = integrations.list_integration_targets()
        ids = [t["id"] for t in payload["targets"]]
        self.assertEqual(payload["platform"], "darwin")
        self.assertIn("freecad", ids)
        self.assertIn("fusion360", ids)
        self.assertNotIn("solidworks", ids)
        self.assertEqual(ids[-1], "system-default")

    def test_platform_filtering_win32(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "win32"
        ids = [t["id"] for t in integrations.list_integration_targets()["targets"]]
        self.assertIn("solidworks", ids)
        self.assertNotIn("cad-assistant", ids)

    def test_system_default_always_installed(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "linux"
        targets = {t["id"]: t for t in integrations.list_integration_targets()["targets"]}
        self.assertTrue(targets["system-default"]["installed"])

    def test_forced_installed(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = "freecad=/Applications/FreeCAD.app, rhino"
        targets = {t["id"]: t for t in integrations.list_integration_targets()["targets"]}
        self.assertTrue(targets["freecad"]["installed"])
        self.assertTrue(targets["rhino"]["installed"])


class LaunchRecipes(_EnvIsolatedTest):
    def setUp(self):
        super().setUp()
        os.environ["VIEWER_DISABLE_APP_LAUNCH"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.step_path = os.path.join(self._tmp.name, "part.step")
        with open(self.step_path, "w", encoding="utf-8") as handle:
            handle.write("ISO-10303-21;\nEND-ISO-10303-21;\n")

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def test_freecad_darwin_execs_bundle_binary(self):
        # NOT `open -a`: FreeCAD's Apple-event handler drops non-.fcstd files.
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = "freecad=/Applications/FreeCAD.app"
        result = integrations.launch_integration("freecad", self.step_path)
        self.assertEqual(result["command"], [
            "/Applications/FreeCAD.app/Contents/MacOS/FreeCAD",
            "--single-instance",
            os.path.realpath(self.step_path),
        ])

    def test_fusion_darwin_fires_url_scheme(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = "fusion360"
        result = integrations.launch_integration("fusion360", self.step_path)
        self.assertEqual(result["command"][0], "open")
        url = result["command"][1]
        self.assertTrue(url.startswith("fusion360://host/?command=open&file="))
        self.assertNotIn(os.path.realpath(self.step_path), url)  # path must be URL-encoded
        self.assertIn("%2Fpart.step", url)

    def test_bundle_apps_open_with_detected_path(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = "rhino=/Applications/Rhino 8.app"
        result = integrations.launch_integration("rhino", self.step_path)
        self.assertEqual(result["command"], [
            "open", "-a", "/Applications/Rhino 8.app", os.path.realpath(self.step_path),
        ])

    def test_system_default_per_platform(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        self.assertEqual(
            integrations.launch_integration("system-default", self.step_path)["command"][0], "open")
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "win32"
        self.assertEqual(
            integrations.launch_integration("system-default", self.step_path)["command"][0], "os.startfile")
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "linux"
        self.assertEqual(
            integrations.launch_integration("system-default", self.step_path)["command"][0], "xdg-open")

    def test_unknown_app_rejected(self):
        with self.assertRaises(ValueError):
            integrations.launch_integration("evil-app", self.step_path)

    def test_not_installed_rejected(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        os.environ["VIEWER_OPEN_IN_FORCE_INSTALLED"] = ""
        # Probe a machine-independent miss: solidworks has no darwin recipe at all.
        with self.assertRaises(ValueError):
            integrations.launch_integration("solidworks", self.step_path)

    def test_relative_path_rejected(self):
        with self.assertRaises(ValueError):
            integrations.launch_integration("system-default", "part.step")

    def test_non_step_extension_rejected(self):
        other = os.path.join(self._tmp.name, "part.stl")
        with open(other, "w", encoding="utf-8") as handle:
            handle.write("solid\n")
        with self.assertRaises(ValueError):
            integrations.launch_integration("system-default", other)

    def test_url_like_path_rejected(self):
        with self.assertRaises(ValueError):
            integrations.launch_integration("system-default", "http://evil.example/part.step")

    def test_reveal_command_per_platform(self):
        self.assertEqual(integrations.reveal_command("/tmp/a.step", "darwin"), ["open", "-R", "/tmp/a.step"])
        self.assertEqual(integrations.reveal_command("C:\\m\\a.step", "win32"), ["explorer.exe", "/select,C:\\m\\a.step"])
        self.assertEqual(integrations.reveal_command("/tmp/a.step", "linux"), ["xdg-open", "/tmp"])

    def test_reveal_disabled_returns_command(self):
        os.environ["VIEWER_OPEN_IN_PLATFORM"] = "darwin"
        result = integrations.reveal_in_file_manager(self.step_path)
        self.assertEqual(result["command"], ["open", "-R", os.path.realpath(self.step_path)])


class StepMaterialization(_EnvIsolatedTest):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._tmp.name)
        self.backend = backend_mod.LocalAssetBackend(directory_root=self.root, root_dir=self.root)
        self.resolved = self.backend.resolve_root(self.root)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def _write(self, name, text):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def test_imported_step_resolves_to_itself(self):
        step = self._write("part.step", "ISO-10303-21;\nEND-ISO-10303-21;\n")
        result = self.backend.materialize_step_output(step, self.resolved)
        self.assertEqual(result, {"path": step, "materialized": False})

    def test_lfs_pointer_rejected(self):
        step = self._write(
            "part.step",
            "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 42\n",
        )
        with self.assertRaisesRegex(ValueError, "git lfs checkout"):
            self.backend.materialize_step_output(step, self.resolved)

    def test_generated_export_caches_by_source_content(self):
        source = self._write("box.step.py", "def gen_step():\n    return None\n")
        calls = []

        def fake_run_cadgen(module, args, repo_root):
            calls.append((module, list(args)))
            out = args[args.index("--out") + 1]
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as handle:
                handle.write("ISO-10303-21;\n")
            return {"ok": True, "path": out}

        original = backend_mod.cadgen_bridge.run_cadgen
        backend_mod.cadgen_bridge.run_cadgen = fake_run_cadgen
        try:
            first = self.backend.materialize_step_output(source, self.resolved)
            self.assertTrue(first["materialized"])
            self.assertTrue(first["path"].endswith("box.step"))
            self.assertIn(os.path.join("__cadgen__", "models"), first["path"])

            second = self.backend.materialize_step_output(source, self.resolved)
            self.assertEqual(second["path"], first["path"])
            self.assertFalse(second["materialized"])
            self.assertEqual(len(calls), 1)

            self._write("box.step.py", "def gen_step():\n    return 'changed'\n")
            third = self.backend.materialize_step_output(source, self.resolved)
            self.assertTrue(third["materialized"])
            self.assertNotEqual(third["path"], first["path"])
            self.assertEqual(len(calls), 2)
            # The stale cache entry is pruned once the new export lands.
            self.assertFalse(os.path.exists(first["path"]))
        finally:
            backend_mod.cadgen_bridge.run_cadgen = original

    def test_outside_root_rejected(self):
        with tempfile.TemporaryDirectory() as other:
            step = os.path.join(os.path.realpath(other), "part.step")
            with open(step, "w", encoding="utf-8") as handle:
                handle.write("ISO-10303-21;\n")
            with self.assertRaises(ValueError):
                self.backend.materialize_step_output(step, self.resolved)


if __name__ == "__main__":
    unittest.main()
