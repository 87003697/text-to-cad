"""Public behavior tests for the fixed in-bwrap Browser Gate."""

from __future__ import annotations

import importlib.util
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

from scripts.pilot import runner

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")


REPO_ROOT = Path(__file__).resolve().parents[3]
GATE_PATH = REPO_ROOT / "scripts/pilot/browser_sidecar_gate.py"


def load_gate():
    """Load the fixed-path gate using the repository-owned contract fixture."""

    spec = importlib.util.spec_from_file_location("browser_sidecar_gate", GATE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BrowserSidecarGateTests(unittest.TestCase):
    """Exercise the repository-owned gate with no caller-selected render input."""

    def test_discovery_permits_only_the_fixed_runtime_shm_alias(self) -> None:
        """Ubuntu's /run/shm alias may resolve only to Docker's fixed shm mount."""

        gate = load_gate()
        with mock.patch.object(
            gate, "discover_browser_roots", return_value=[]
        ) as discover:
            gate.discover_conformance_surface()

        permitted = discover.call_args.kwargs["permitted_symlink_roots"]
        self.assertIn(Path("/dev/shm"), permitted)
        self.assertTrue(
            {Path("/bin"), Path("/lib"), Path("/lib64"), Path("/sbin")}.issubset(
                permitted
            )
        )
        self.assertNotIn(Path("/dev"), permitted)
        self.assertNotIn(Path("/"), permitted)

    def test_fixed_gate_calls_public_residual_and_registered_viewer(self) -> None:
        gate = load_gate()
        surface = {
            "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
            "scanRoots": ["/usr"],
            "browserExclusions": [
                {
                    "kind": "system",
                    "target": "/usr/share/polkit-1/rules.d",
                    "mask": "tmpfs",
                }
            ],
        }
        identity = {
            "schema": "meshshot.browser-sidecar.nested-gate-input/1",
            "jobId": "formal-job-1",
            "nonce": "a" * 32,
            "artifactSha256": "b" * 64,
            "surfaceManifest": surface,
            "surfaceManifestSha256": hashlib.sha256(
                json.dumps(surface, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        public_render = gate.render_residual_preview(
            gate.MeshGeometry(
                vertices=[[-0.46, -0.2, 0.0], [-0.2, -0.2, 0.0], [-0.33, 0.2, 0.0]],
                faces=[[0, 1, 2]],
            ),
            gate.MeshGeometry(
                vertices=[[0.2, -0.2, 0.0], [0.46, -0.2, 0.0], [0.33, 0.2, 0.0]],
                faces=[[0, 1, 2]],
            ),
            variant="step",
            exterior_directions=[],
        )
        viewer = {
            "title": "CAD Viewer | browser_sidecar_inspection.step",
            "modelKey": "inspection-step",
            "programDigest": gate.CONTRACT["programs"]["viewer"],
            "screenshotDataUrl": "data:image/png;base64,cG5n",
            "screenshotSha256": "0" * 64,
            "screenshotBytes": 3,
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
            "inspection": {
                "control": "toggle-projection",
                "before": "Display and projection: Solid, Orthographic",
                "target": "Perspective",
                "after": "Display and projection: Solid, Perspective",
                "changed": True,
            },
        }
        with (
            mock.patch.object(
                gate,
                "render_residual_preview",
                return_value=public_render,
            ) as public_api,
            mock.patch.object(
                gate.hashlib,
                "sha256",
                return_value=SimpleNamespace(
                    hexdigest=lambda: gate.GATE["publicPngSha256"]
                ),
            ),
            mock.patch.object(gate, "_viewer_request", return_value=viewer) as request,
            mock.patch.object(gate, "load_gate_identity", return_value=identity),
            mock.patch.object(gate, "_authority", return_value={"jobId": "formal-job-1"}),
            mock.patch.object(gate, "_exclusions_closed", return_value=True),
            mock.patch.object(
                gate, "discover_browser_roots", return_value=[]
            ) as discover,
            mock.patch.object(gate, "_browser_processes", return_value=[]),
        ):
            proof = gate.run_gate_checks()

        self.assertEqual(proof["schema"], gate.GATE["schema"])
        self.assertEqual(proof["status"], "succeeded")
        self.assertTrue(all(proof["predicates"].values()))
        self.assertNotIn("sourceHidden", proof["predicates"])
        self.assertNotIn("egressBlocked", proof["predicates"])
        self.assertEqual(
            proof["residual"],
            {
                "pngSha256": "b498c55c68662989a3a95c4925432d61f979183d30c2cdf593e154c7b0ca9d5b",
                "mode": "RGB",
                "size": [504, 1008],
                "profileSha256": "87da3cc3f625cb9c24f51bed41dcdc70402a4d461b2af29eaa19846b1e8f7241",
                "views": ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            },
        )
        self.assertEqual(
            proof["inventory"],
            {
                "browserExecutables": [],
                "browserPackages": [],
                "browserCaches": [],
                "browserProcesses": [],
            },
        )
        request.assert_called_once_with(identity)
        public_api.assert_called_once()
        self.assertEqual(
            discover.call_args.kwargs["masked_source_roots"],
            [Path("/usr/share/polkit-1/rules.d")],
        )
        self.assertEqual(public_api.call_args.kwargs, {
            "variant": "step",
            "exterior_directions": [],
        })

    def test_gate_failure_reports_only_a_closed_stage(self) -> None:
        """A failed gate exposes one fixed stage without exception details."""

        gate = load_gate()
        identity = {
            "jobId": "formal-job-1",
            "nonce": "a" * 32,
            "artifactSha256": "b" * 64,
            "surfaceManifestSha256": "c" * 64,
        }
        with (
            mock.patch.object(gate, "_authority", return_value={}),
            mock.patch.object(
                gate,
                "render_residual_preview",
                side_effect=RuntimeError("secret detail must not escape"),
            ),
        ):
            with self.assertRaises(gate.GateCheckError) as caught:
                gate.run_gate_checks(identity)

        self.assertEqual(caught.exception.stage, "residual-render")
        proof = gate._failed_proof(identity, caught.exception.stage)
        self.assertEqual(proof["status"], "failed:residual-render")
        self.assertNotIn("secret", json.dumps(proof, sort_keys=True))

    def test_surface_failure_reports_only_a_fixed_scanner_reason(self) -> None:
        """Scanner paths stay private while its fixed reason remains actionable."""

        gate = load_gate()
        surface = {
            "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
            "scanRoots": ["/usr"],
            "browserExclusions": [],
        }
        identity = {
            "jobId": "formal-job-1",
            "nonce": "a" * 32,
            "artifactSha256": "b" * 64,
            "surfaceManifest": surface,
            "surfaceManifestSha256": "c" * 64,
        }
        rendered = SimpleNamespace(
            png_bytes=b"png",
            profile_sha256=gate.GATE["profileSha256"],
            views=({"name": name} for name in gate.GATE["views"]),
        )
        viewer = {
            "inspection": {
                "before": "Display and projection: Solid, Orthographic",
                "after": "Display and projection: Solid, Perspective",
            },
            "bodyMentionsFixture": True,
            "bodyHasArtifactError": False,
        }
        with (
            mock.patch.object(gate, "_authority", return_value={}),
            mock.patch.object(gate, "render_residual_preview", return_value=rendered),
            mock.patch.object(gate.Image, "open") as opened,
            mock.patch.object(gate, "_viewer_request", return_value=viewer),
            mock.patch.object(gate, "_exclusions_closed", return_value=True),
            mock.patch.object(
                gate,
                "discover_browser_roots",
                side_effect=gate.BrowserSurfaceRootError(
                    "/private/secret/root",
                    "mounted browser surface symlink is dangling",
                ),
            ),
        ):
            image = opened.return_value.__enter__.return_value
            image.mode = "RGB"
            image.size = (504, 1008)
            with mock.patch.object(
                gate.hashlib,
                "sha256",
                return_value=SimpleNamespace(
                    hexdigest=lambda: gate.GATE["publicPngSha256"]
                ),
            ):
                with self.assertRaises(gate.GateCheckError) as caught:
                    gate.run_gate_checks(identity)

        self.assertEqual(caught.exception.stage, "surface-symlink-dangling")
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(
            gate.SURFACE_FAILURE_STAGES[
                "mounted browser surface symlink is unresolved"
            ],
            "surface-symlink-unresolved",
        )

    def test_surface_os_failure_reports_errno_class_without_path(self) -> None:
        """An OS failure exposes only its fixed errno class."""

        gate = load_gate()
        failure = gate.BrowserSurfaceRootError(
            "/private/secret/root", "cannot inspect mounted browser surface"
        )
        wrapper = OSError("closed wrapper")
        wrapper.__cause__ = PermissionError(
            gate.errno.EACCES, "private detail", "/private/secret/file"
        )
        failure.__cause__ = wrapper

        with self.assertRaises(gate.GateCheckError) as caught:
            gate._checked(
                "surface-discovery",
                mock.Mock(side_effect=failure),
            )

        self.assertEqual(caught.exception.stage, "surface-os-permission-other")
        self.assertNotIn("private", str(caught.exception))

        categories = {
            "/usr": "system-usr",
            "/etc/ssl": "system-config",
            "/workspace/repo/.venv": "venv",
            "/workspace/repo/gateway/codex-tap-gpt56": "gateway",
            "/workspace/repo/skills/cad": "skill",
            "/home/pilot/.codex/skills/cad": "skill",
            "/workspace/repo/outputs/group/exp": "experiment",
            "/home/pilot/.codex": "codex-home",
            "/sys": "empty-system",
            "/workspace/repo/models/input.ply": "input",
        }
        for target, expected in categories.items():
            with self.subTest(target=target):
                self.assertEqual(gate._surface_root_category(target), expected)

    def test_gate_exec_surface_has_no_render_arguments_or_shell(self) -> None:
        source = GATE_PATH.read_text(encoding="utf-8")
        self.assertIn("os.execvpe(workload[0], workload", source)
        self.assertNotIn("shell=True", source)
        self.assertNotIn("add_argument", source)
        self.assertNotIn("MESHSHOT_BROWSER_AUTHORITY_FILE", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("SOURCE_ALIASES", source)

    def test_browser_process_inventory_detects_headless_shell_names(self) -> None:
        """Common Chromium product names cannot evade the zero-process proof."""

        gate = load_gate()
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            process = proc / "101"
            process.mkdir()
            (process / "comm").write_text("chrome-headless-shell\n", encoding="utf-8")
            (process / "cmdline").write_bytes(
                b"/opt/chrome-headless-shell\0--headless\0"
            )
            with mock.patch.object(
                gate,
                "Path",
                new=lambda value: proc if value == "/proc" else Path(value),
            ):
                self.assertEqual(gate._browser_processes(), ["chrome-headless-shell"])

    def test_browser_process_inventory_fails_closed_when_proc_is_unreadable(self) -> None:
        """An inspectable live PID with unreadable metadata cannot prove zero."""

        gate = load_gate()
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)
            process = proc / "202"
            process.mkdir()
            (process / "comm").write_text("python\n", encoding="utf-8")
            (process / "cmdline").mkdir()
            with mock.patch.object(
                gate,
                "Path",
                new=lambda value: proc if value == "/proc" else Path(value),
            ):
                with self.assertRaisesRegex(ValueError, "process inventory"):
                    gate._browser_processes()

    def test_gate_artifact_and_proof_are_bound_to_read_only_job_input(self) -> None:
        """The sealed gate validates its bytes, job, nonce, and surface manifest."""

        gate = load_gate()
        expected = {
            "schema": "meshshot.browser-sidecar.nested-gate-input/1",
            "jobId": "formal-job-1",
            "nonce": "a" * 32,
            "artifactSha256": "b" * 64,
            "surfaceManifest": {
                "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
                "scanRoots": ["/usr", "/workspace/repo/.venv"],
                "browserExclusions": [],
            },
        }
        canonical = json.dumps(
            expected["surfaceManifest"], sort_keys=True, separators=(",", ":")
        ).encode("ascii")
        with (
            mock.patch.object(gate, "GATE_INPUT_PATH", Path("/fixed/gate-input.json")),
            mock.patch.object(
                gate,
                "_fixed_bytes",
                return_value=(json.dumps(expected) + "\n").encode(),
            ),
            mock.patch.object(gate, "_artifact_sha256", return_value="b" * 64),
        ):
            identity = gate.load_gate_identity()
        self.assertEqual(identity["jobId"], "formal-job-1")
        self.assertEqual(identity["nonce"], "a" * 32)
        self.assertEqual(
            identity["surfaceManifestSha256"], hashlib.sha256(canonical).hexdigest()
        )

    def test_outer_builds_one_deterministic_source_free_gate_zipapp(self) -> None:
        """The fixed mount is one immutable artifact, not a live source alias."""

        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.pyz"
            second = Path(temp) / "second.pyz"
            first_digest = runner._build_gate_artifact(REPO_ROOT, first)
            second_digest = runner._build_gate_artifact(REPO_ROOT, second)
            first_bytes = first.read_bytes()
            second_bytes = second.read_bytes()
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            executed = subprocess.run(
                [sys.executable, first],
                check=False,
                capture_output=True,
                text=True,
            )
        self.assertEqual(first_digest, second_digest)
        self.assertEqual(first_bytes, second_bytes)
        self.assertIn("__main__.py", names)
        self.assertIn("browser_surface.py", names)
        self.assertIn("meshshot/browser_contract.json", names)
        self.assertFalse(
            any(name.startswith("/") or ".." in Path(name).parts for name in names)
        )
        self.assertEqual(executed.returncode, 2, executed.stderr)

    def test_canonical_parent_mask_survives_bwrap_and_real_gate_closure(self) -> None:
        """One empty parent mask replaces every contradictory child predicate."""

        gate = load_gate()
        with tempfile.TemporaryDirectory(dir="/tmp") as temp:
            root = Path(temp)
            repo_root = root / "repo"
            exp_dir = repo_root / "outputs/group/exp"
            source = root / "source"
            mounted = root / "mounted"
            capability = root / "capability"
            upper = exp_dir / "run/.codex-upper"
            browser_dir = source / "ms-playwright"
            executable = browser_dir / "chromium"
            browser_dir.mkdir(parents=True)
            (browser_dir / "playwright").mkdir()
            executable.write_bytes(b"\x7fELF" + b"\0" * 32)
            executable.chmod(0o755)
            exclusions = runner.discover_browser_roots(
                [(source, mounted, True)]
            )

            exp_dir.mkdir(parents=True)
            upper.mkdir(parents=True)
            (repo_root / ".venv").mkdir()
            gateway = repo_root / "gateway/codex-tap-gpt56"
            gateway.parent.mkdir(parents=True)
            gateway.write_text("#!/bin/sh\n", encoding="utf-8")
            capability.mkdir()
            gate_artifact = capability / "browser-gate.pyz"
            gate_artifact.write_bytes(b"sealed")
            gate_artifact.chmod(0o444)
            sandbox_exp = runner.SANDBOX_REPO_ROOT / exp_dir.relative_to(repo_root)
            manifest = {
                "schema": "meshshot.browser-sidecar.agent-browser-surface/1",
                "scanRoots": sorted(
                    [
                        mounted.as_posix(),
                        sandbox_exp.as_posix(),
                        runner.SANDBOX_CODEX_HOME.as_posix(),
                    ]
                ),
                "browserExclusions": exclusions,
            }
            (capability / "gate-input.json").write_text(
                json.dumps({"surfaceManifest": manifest}) + "\n",
                encoding="ascii",
            )
            environ = {
                "HOME": str(root / "home"),
                "PATH": "/fake/bin",
                "VENUS_TOKEN": "test-token",
            }
            with (
                mock.patch.object(
                    runner.shutil,
                    "which",
                    side_effect=lambda command, **_: {
                        "bwrap": "/fake/bwrap",
                        "codex": "/usr/bin/codex",
                    }[command],
                ),
                mock.patch.object(runner, "resolve_sandbox_codex"),
                mock.patch.object(runner, "validate_input_paths", return_value=[]),
                mock.patch.object(
                    runner, "resolve_installed_skill_dirs", return_value=[]
                ),
                mock.patch.object(runner, "prepare_sandbox", return_value=upper),
                mock.patch.object(runner, "existing_system_paths", return_value=[]),
                mock.patch.object(
                    runner,
                    "_readonly_surface_mounts",
                    return_value=[(source, mounted, True)],
                ),
            ):
                argv = runner.build_bwrap_argv(
                    repo_root,
                    exp_dir,
                    [],
                    ["/fixed/agent"],
                    environ,
                    browser_capability_dir=capability,
                )

            masked_parent = mounted / "ms-playwright"
            masked_parent.mkdir(parents=True)
            closed = gate._exclusions_closed(exclusions)

        self.assertEqual(
            exclusions,
            [
                {
                    "kind": "cache",
                    "target": masked_parent.as_posix(),
                    "mask": "tmpfs",
                }
            ],
        )
        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        self.assertIn(["--tmpfs", masked_parent.as_posix()], [
            argv[index : index + 2] for index in range(len(argv) - 1)
        ])
        self.assertNotIn(
            ["--ro-bind", "/dev/null", (masked_parent / "chromium").as_posix()],
            triples,
        )
        self.assertTrue(closed)


if __name__ == "__main__":
    unittest.main()
