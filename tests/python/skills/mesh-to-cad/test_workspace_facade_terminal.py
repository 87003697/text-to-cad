"""Focused compile/verify contract tests for the Workspace facade."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
WORKSPACE_ROOT = REPO_ROOT / "skills/mesh-to-cad/scripts/mesh-to-cad-workspace"
WORKSPACE_PATH = WORKSPACE_ROOT / "workspace.py"
CLI_PATH = WORKSPACE_ROOT / "cli.py"


def _load_facade():
    import sys

    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    spec = importlib.util.spec_from_file_location("mesh_to_cad_workspace_facade", WORKSPACE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace facade")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_cli():
    import sys

    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    spec = importlib.util.spec_from_file_location("mesh_to_cad_workspace_facade_cli", CLI_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Workspace CLI")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(schema: str, value: dict) -> str:
    body = (
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    ).encode("utf-8")
    return _sha(schema.encode("utf-8") + b"\0" + body)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, separators=(",", ": ")) + "\n",
        encoding="utf-8",
    )


class WorkspaceFacadeTerminalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "workspace"
        self.facade = _load_facade()
        self.prepared = self._prepared()
        self._initialize_workspace(self.workspace)

    def _initialize_workspace(self, workspace: Path) -> None:
        workspace.mkdir()
        for args in (
            ("git", "init", "-b", "develop"),
            ("git", "config", "user.name", "Workspace Facade Test"),
            ("git", "config", "user.email", "workspace-facade@example.invalid"),
        ):
            import subprocess

            subprocess.run(args, cwd=workspace, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.facade.initialize_workspace(workspace, self.prepared)

    def _prepared(self) -> Path:
        prepared = self.root / "prepared"
        reference = b"ply\nsynthetic canonical reference\n"
        (prepared / "input").mkdir(parents=True)
        (prepared / "input/reference.ply").write_bytes(reference)
        _write_json(
            prepared / "input/input.json",
            {
                "schema": "voxblame.canonical-reference/1",
                "canonical_reference_sha256": "1" * 64,
                "reference_ply": {"path": "input/reference.ply", "sha256": _sha(reference)},
            },
        )
        (prepared / "setup").mkdir()
        _write_json(
            prepared / "experiment.json",
            {
                "schema": "mesh-to-cad.experiment/1",
                "workspace_id": "facade-terminal-workspace",
                "coordinate_contract": "trellis2_canonical/1",
                "canonical_reference_sha256": "1" * 64,
                "preview_profile": {"name": "cadena_residual_eight_view/1", "sha256": "2" * 64},
            },
        )
        return prepared

    def _terminal_graph(self, workspace: Path | None = None) -> dict:
        target = workspace or self.workspace
        index_path = target / "step_index.json"
        graph = json.loads(index_path.read_text(encoding="utf-8"))
        graph["final_delivery"] = {
            "selected_step": 0,
            "accepted": False,
            "stop_reason": "cycle_limit",
            "identity_sha256": "a" * 64,
            "manifest": "final/manifest.json",
        }
        _write_json(index_path, graph)
        return graph

    def _compile_synthetic(self, workspace: Path | None = None) -> dict:
        target = workspace or self.workspace
        graph = self._terminal_graph(target)
        validation = self.facade.ValidationResult(graph=graph, recovery=[])
        with mock.patch.object(self.facade, "validate_workspace", return_value=validation):
            return self.facade.compile_terminal_validation(target)

    def _inventory(self, workspace: Path) -> list[dict]:
        files: list[Path] = []

        def walk(path: Path) -> None:
            if path.is_symlink():
                raise AssertionError(f"unexpected test symlink: {path}")
            if path.is_file():
                files.append(path)
                return
            for child in sorted(path.iterdir(), key=lambda item: item.name):
                walk(child)

        for child in sorted(workspace.iterdir(), key=lambda item: item.name):
            if child.name in {".git", "run", "work"}:
                continue
            walk(child)
        return [
            {
                "path": path.relative_to(workspace).as_posix(),
                "sha256": _sha(path.read_bytes()),
                "size_bytes": path.stat().st_size,
            }
            for path in files
        ]

    def _recompute_bundle_after_authority_edit(self, bundle: dict) -> dict:
        workspace_document = json.loads((self.workspace / "workspace.json").read_text(encoding="utf-8"))
        manifest = {
            "schema": self.facade.CONTENT_MANIFEST_SCHEMA,
            "workspace_id": workspace_document["workspace_id"],
            "workspace_identity_sha256": _identity("mesh-to-cad.workspace/1", workspace_document),
            "files": self._inventory(self.workspace),
        }
        manifest["identity_sha256"] = _identity(self.facade.CONTENT_MANIFEST_SCHEMA, manifest)
        result = dict(bundle["result"])
        result["workspace_id"] = manifest["workspace_id"]
        result["workspace_identity_sha256"] = manifest["workspace_identity_sha256"]
        result["content_manifest_sha256"] = manifest["identity_sha256"]
        result_without_identity = dict(result)
        result_without_identity.pop("identity_sha256")
        result["identity_sha256"] = _identity(self.facade.TERMINAL_VALIDATION_SCHEMA, result_without_identity)
        rewritten = {"schema": self.facade.TERMINAL_BUNDLE_SCHEMA, "result": result, "manifest": manifest}
        return rewritten

    def test_compile_is_closed_deterministic_and_calls_validator_once(self) -> None:
        graph = self._terminal_graph()
        validation = self.facade.ValidationResult(graph=graph, recovery=[])
        with mock.patch.object(self.facade, "validate_workspace", return_value=validation) as validate:
            publication = self.facade.compile_terminal_validation(self.workspace)
        validate.assert_called_once_with(self.workspace.resolve())
        bundle = publication["bundle"]
        self.assertEqual(
            {"schema", "result", "manifest"},
            set(bundle),
        )
        self.assertEqual(self.facade.TERMINAL_BUNDLE_SCHEMA, bundle["schema"])
        self.assertEqual(
            {"schema", "workspace_id", "workspace_identity_sha256", "validator_version", "graph", "review_graph", "recovery", "review_facts", "evaluation_facts", "content_manifest_sha256", "identity_sha256"},
            set(bundle["result"]),
        )
        self.assertEqual(
            bundle["result"],
            self.facade.verify_terminal_validation(
                self.workspace, bundle, publication["terminal_identity_sha256"]
            ),
        )
        self.assertFalse((self.workspace / "terminal_validation").exists())

    def test_compile_identity_is_deterministic_for_same_content(self) -> None:
        first = self._compile_synthetic()
        twin = self.root / "twin"
        self._initialize_workspace(twin)
        second = self._compile_synthetic(twin)
        self.assertEqual(first["bundle"], second["bundle"])
        self.assertEqual(first["terminal_identity_sha256"], second["terminal_identity_sha256"])

    def test_preterminal_workspace_is_rejected(self) -> None:
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.compile_terminal_validation(self.workspace)
        self.assertEqual("terminal_state_required", raised.exception.classification)

    def test_authority_mutation_during_compile_fails(self) -> None:
        graph = self._terminal_graph()
        validation = self.facade.ValidationResult(graph=graph, recovery=[])
        experiment = self.workspace / "experiment.json"

        def validate_then_mutate(_workspace: Path):
            value = json.loads(experiment.read_text(encoding="utf-8"))
            value["workspace_id"] = "changed-during-compile"
            _write_json(experiment, value)
            return validation

        with mock.patch.object(self.facade, "validate_workspace", side_effect=validate_then_mutate):
            with self.assertRaises(self.facade.WorkspaceError) as raised:
                self.facade.compile_terminal_validation(self.workspace)
        self.assertEqual("workspace_changed_during_validation", raised.exception.classification)

    def test_unreadable_authority_directory_is_safe_workspace_error_and_cli_json(self) -> None:
        target = self.workspace / "input"
        original_iterdir = Path.iterdir

        def unreadable(path: Path):
            if path.resolve() == target.resolve():
                raise PermissionError("unreadable test directory")
            return original_iterdir(path)

        with mock.patch.object(Path, "iterdir", autospec=True, side_effect=unreadable):
            with self.assertRaises(self.facade.WorkspaceError) as raised:
                self.facade.compile_terminal_validation(self.workspace)
        self.assertEqual("corrupt_workspace", raised.exception.classification)
        self.assertEqual("$.input", raised.exception.path)
        self.assertNotIn(str(self.workspace), raised.exception.detail)

        cli = _load_cli()
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(Path, "iterdir", autospec=True, side_effect=unreadable):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cli.main(["terminal-validate", "--workspace", str(self.workspace)])
        self.assertEqual(2, status)
        payload = json.loads(stdout.getvalue())
        self.assertEqual({"ok", "error"}, set(payload))
        self.assertEqual("corrupt_workspace", payload["error"]["classification"])
        self.assertNotIn(str(self.workspace), payload["error"]["detail"])

    def test_disappearing_authority_file_stat_race_is_safe_workspace_error(self) -> None:
        target = (self.workspace / "input/input.json").resolve()
        original_stat = Path.stat

        def disappearing_stat(path: Path, *args, **kwargs):
            if path == target:
                raise FileNotFoundError("disappeared test file")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(Path, "stat", autospec=True, side_effect=disappearing_stat):
            with self.assertRaises(self.facade.WorkspaceError) as raised:
                self.facade.compile_terminal_validation(self.workspace)
        self.assertEqual("corrupt_workspace", raised.exception.classification)
        self.assertEqual("$.input/input.json", raised.exception.path)
        self.assertNotIn(str(self.workspace), raised.exception.detail)

    def test_missing_and_wrong_expected_identity_fail(self) -> None:
        publication = self._compile_synthetic()
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, publication["bundle"])
        self.assertEqual("terminal_identity_required", raised.exception.classification)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, publication["bundle"], "0" * 64)
        self.assertEqual("terminal_identity_mismatch", raised.exception.classification)

    def test_recomputed_result_and_evaluation_tamper_fail_external_identity(self) -> None:
        publication = self._compile_synthetic()
        bundle = json.loads(json.dumps(publication["bundle"]))
        bundle["result"]["graph"]["budget"]["remaining_cycles"] = 999
        bundle["result"]["review_facts"]["budget"]["remaining_cycles"] = 999
        result_without_identity = dict(bundle["result"])
        result_without_identity.pop("identity_sha256")
        bundle["result"]["identity_sha256"] = _identity(self.facade.TERMINAL_VALIDATION_SCHEMA, result_without_identity)
        bundle["manifest"]["identity_sha256"] = bundle["manifest"]["identity_sha256"]
        rewritten_identity = _identity(self.facade.TERMINAL_IDENTITY_SCHEMA, bundle)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, bundle, publication["terminal_identity_sha256"])
        self.assertEqual("terminal_identity_mismatch", raised.exception.classification)
        self.assertNotEqual(rewritten_identity, publication["terminal_identity_sha256"])

    def test_raw_identity_precedes_malformed_nested_graph_and_nested_errors_are_closed(self) -> None:
        publication = self._compile_synthetic()
        malformed = json.loads(json.dumps(publication["bundle"]))
        del malformed["result"]["graph"]["steps"]
        malicious_identity = _identity(self.facade.TERMINAL_IDENTITY_SCHEMA, malformed)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(
                self.workspace, malformed, publication["terminal_identity_sha256"]
            )
        self.assertEqual("terminal_identity_mismatch", raised.exception.classification)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, malformed, malicious_identity)
        self.assertEqual("invalid_contract", raised.exception.classification)

        malformed["result"]["graph"] = {"schema": self.facade._core.INDEX_SCHEMA, "steps": "wrong"}
        malicious_identity = _identity(self.facade.TERMINAL_IDENTITY_SCHEMA, malformed)
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, malformed, malicious_identity)
        self.assertEqual("invalid_contract", raised.exception.classification)

        cli = _load_cli()
        bundle_path = self.root / "malformed-bundle.json"
        _write_json(bundle_path, malformed)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main([
                "terminal-result",
                "--workspace",
                str(self.workspace),
                "--bundle",
                str(bundle_path),
                "--expected-terminal-identity",
                malicious_identity,
            ])
        self.assertEqual(2, status)
        self.assertEqual("invalid_contract", json.loads(stdout.getvalue())["error"]["classification"])

    def test_recomputed_authority_manifest_and_result_fail_external_identity(self) -> None:
        publication = self._compile_synthetic()
        workspace_path = self.workspace / "workspace.json"
        workspace_document = json.loads(workspace_path.read_text(encoding="utf-8"))
        workspace_document["workspace_id"] = "authority-tampered"
        _write_json(workspace_path, workspace_document)
        experiment_path = self.workspace / "experiment.json"
        experiment = json.loads(experiment_path.read_text(encoding="utf-8"))
        experiment["workspace_id"] = "authority-tampered"
        _write_json(experiment_path, experiment)
        rewritten = self._recompute_bundle_after_authority_edit(publication["bundle"])
        with self.assertRaises(self.facade.WorkspaceError) as raised:
            self.facade.verify_terminal_validation(self.workspace, rewritten, publication["terminal_identity_sha256"])
        self.assertEqual("terminal_identity_mismatch", raised.exception.classification)

    def test_extra_missing_and_symlink_authority_content_fail(self) -> None:
        publication = self._compile_synthetic()
        extra = self.workspace / "extra-authority.txt"
        extra.write_text("extra\n", encoding="utf-8")
        with self.assertRaises(self.facade.WorkspaceError):
            self.facade.verify_terminal_validation(self.workspace, publication["bundle"], publication["terminal_identity_sha256"])
        extra.unlink()
        reference = self.workspace / "input/reference.ply"
        saved = reference.read_bytes()
        reference.unlink()
        with self.assertRaises(self.facade.WorkspaceError):
            self.facade.verify_terminal_validation(self.workspace, publication["bundle"], publication["terminal_identity_sha256"])
        reference.write_bytes(saved)
        link = self.workspace / "input/escape"
        link.symlink_to(self.root / "outside.txt")
        with self.assertRaises(self.facade.WorkspaceError):
            self.facade.verify_terminal_validation(self.workspace, publication["bundle"], publication["terminal_identity_sha256"])

    def test_copied_workspace_without_git_verifies(self) -> None:
        publication = self._compile_synthetic()
        copied = self.root / "copied"
        shutil.copytree(self.workspace, copied, ignore=shutil.ignore_patterns(".git", "run", "work"))
        self.assertFalse((copied / ".git").exists())
        self.assertEqual(
            publication["bundle"]["result"],
            self.facade.verify_terminal_validation(copied, publication["bundle"], publication["terminal_identity_sha256"]),
        )

    def test_cli_emits_one_object_and_verifies_bundle_path(self) -> None:
        cli = _load_cli()
        graph = self._terminal_graph()
        validation = self.facade.ValidationResult(graph=graph, recovery=[])
        with mock.patch.object(self.facade, "validate_workspace", return_value=validation):
            publication = self.facade.compile_terminal_validation(self.workspace)
        bundle_path = self.root / "terminal-bundle.json"
        _write_json(bundle_path, publication["bundle"])
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "compile_terminal_validation", return_value=publication):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                status = cli.main(["terminal-validate", "--workspace", str(self.workspace)])
        self.assertEqual(0, status, stderr.getvalue())
        compiled_payload = json.loads(stdout.getvalue())
        self.assertEqual({"ok", "bundle", "terminal_identity_sha256"}, set(compiled_payload))
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main([
                "terminal-result",
                "--workspace",
                str(self.workspace),
                "--bundle",
                str(bundle_path),
                "--expected-terminal-identity",
                publication["terminal_identity_sha256"],
            ])
        self.assertEqual(0, status, stderr.getvalue())
        verified_payload = json.loads(stdout.getvalue())
        self.assertEqual({"ok", "terminal_validation"}, set(verified_payload))
        self.assertEqual(publication["bundle"]["result"], verified_payload["terminal_validation"])
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["terminal-result", "--workspace", str(self.workspace), "--bundle", str(bundle_path)])
        self.assertEqual(2, status)
        self.assertEqual("invalid_arguments", json.loads(stdout.getvalue())["error"]["classification"])

    def test_existing_cli_function_aliases_remain_core_functions(self) -> None:
        cli = _load_cli()
        self.assertIs(cli.validate_workspace, self.facade.validate_workspace)
        self.assertIs(cli.begin_attempt, self.facade.begin_attempt)

    def test_real_final_delivery_compiles_and_verifies(self) -> None:
        try:
            import OCP  # noqa: F401
        except ImportError:
            self.skipTest("OCP is required for the real CAD Final Delivery fixture")
        fixture_spec = importlib.util.spec_from_file_location(
            "workspace_cli_fixture", REPO_ROOT / "tests/python/skills/mesh-to-cad/test_workspace_cli.py"
        )
        if fixture_spec is None or fixture_spec.loader is None:
            self.fail("cannot load Workspace CLI fixture")
        fixture = importlib.util.module_from_spec(fixture_spec)
        fixture_spec.loader.exec_module(fixture)
        case = fixture.WorkspaceCliTests("test_finalize_rebuilds_verifies_and_atomically_publishes_accepted_cad")
        case.setUp()
        try:
            prepared, candidate = case.canonical_cad_flow()
            case.execute_final_case(prepared=prepared, candidate=candidate, candidate_mesh_relative="built/measurement.glb", accepted=True)
            publication = self.facade.compile_terminal_validation(case.workspace)
            self.assertTrue(publication["bundle"]["result"]["evaluation_facts"]["final_delivery_present"])
            self.assertEqual(
                publication["bundle"]["result"],
                self.facade.verify_terminal_validation(case.workspace, publication["bundle"], publication["terminal_identity_sha256"]),
            )
        finally:
            case.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
