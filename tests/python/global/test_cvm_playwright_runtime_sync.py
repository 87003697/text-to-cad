from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT


MODULE_PATH = REPO_ROOT / "scripts/pilot/cvm_playwright_runtime_sync.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "cvm_playwright_runtime_sync", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.commands: list[str] = []

    def remote(self, command: str, *, cwd: Path, check: bool = True):
        self.commands.append(command)
        status, stdout = self.responses.pop(0)
        return mock.Mock(returncode=status, stdout=stdout, stderr="private")


def identity(*, matched: bool, digest: str | None = "a" * 64) -> str:
    return json.dumps(
        {
            "schema": "cvm.playwright-runtime-identity/1",
            "matched": matched,
            "browser_sha256": digest,
        }
    )


class PlaywrightRuntimeSyncTests(unittest.TestCase):
    def test_wrapper_is_noninteractive_and_rejects_all_inputs(self) -> None:
        wrapper = REPO_ROOT / "scripts/pilot/cvm-sync-playwright-runtime.sh"
        result = subprocess.run(
            [wrapper, "playwright", "1.59.0"],
            cwd=Path("/tmp"),
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("1.59.0", result.stdout)

    def test_mismatch_runs_only_fixed_dependency_sync_and_rechecks(self) -> None:
        module = load_module()
        runner = FakeRunner(
            [(0, identity(matched=False)), (0, ""), (0, identity(matched=True))]
        )

        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = module.execute(runner, repo_root=REPO_ROOT)
        receipt = json.loads(stdout.getvalue())

        self.assertEqual(status, 0)
        self.assertEqual(
            set(receipt),
            {"schema", "requested_identity", "before", "after", "exit_status"},
        )
        self.assertEqual(receipt["schema"], "cvm-playwright-runtime-sync.receipt/1")
        self.assertEqual(
            receipt["requested_identity"],
            {
                "distribution": "playwright",
                "version": "1.60.0",
                "browser": "chromium-headless-shell",
                "revision": "1223",
            },
        )
        self.assertEqual(receipt["before"], "mismatched")
        self.assertEqual(receipt["after"], "matched")
        self.assertEqual(receipt["exit_status"], 0)
        self.assertEqual(len(runner.commands), 3)
        install = runner.commands[1]
        self.assertIn("./.venv/bin/python -m pip install", install)
        self.assertIn("--no-input", install)
        self.assertIn("--no-deps", install)
        self.assertIn("playwright==1.60.0", install)
        self.assertNotIn("playwright install", install)
        self.assertNotIn("outputs", install)
        self.assertNotIn("rm ", install)

    def test_matching_identity_is_idempotent_and_skips_install(self) -> None:
        module = load_module()
        runner = FakeRunner([(0, identity(matched=True))])
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status = module.execute(runner, repo_root=REPO_ROOT)
        receipt = json.loads(stdout.getvalue())

        self.assertEqual(status, 0)
        self.assertEqual(receipt["before"], "matched")
        self.assertEqual(receipt["after"], "matched")
        self.assertEqual(len(runner.commands), 1)

    def test_failure_matrix_is_closed_and_never_emits_remote_output(self) -> None:
        cases = (
            ([(0, "not-json")], "not_checked", "not_run", 1),
            ([(0, identity(matched=False, digest=None))], "mismatched", "not_run", 1),
            ([(0, identity(matched=False)), (19, "raw pip secret")], "mismatched", "not_run", 1),
            ([(0, identity(matched=False)), (0, ""), (0, identity(matched=False))], "mismatched", "mismatched", 1),
            ([(0, identity(matched=False)), (0, ""), (0, identity(matched=True, digest="b" * 64))], "mismatched", "mismatched", 1),
        )
        module = load_module()
        for responses, before, after, expected_status in cases:
            with self.subTest(responses=responses):
                runner = FakeRunner(responses)
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = module.execute(runner, repo_root=REPO_ROOT)
                receipt = json.loads(stdout.getvalue())

                self.assertEqual(status, expected_status)
                self.assertEqual(receipt["before"], before)
                self.assertEqual(receipt["after"], after)
                self.assertEqual(receipt["exit_status"], expected_status)
                self.assertNotIn("raw", stdout.getvalue())
                self.assertNotIn("stderr", stdout.getvalue())
                if before == "not_checked" or responses[0][1].endswith("null}"):
                    self.assertEqual(len(runner.commands), 1)

    def test_duplicate_or_unknown_identity_fields_fail_before_install(self) -> None:
        module = load_module()
        receipts = (
            '{"schema":"cvm.playwright-runtime-identity/1",'
            '"matched":false,"matched":true,"browser_sha256":"'
            + "a" * 64
            + '"}',
            json.dumps(
                {
                    "schema": "cvm.playwright-runtime-identity/1",
                    "matched": False,
                    "browser_sha256": "a" * 64,
                    "path": "/private",
                }
            ),
        )
        for raw in receipts:
            with self.subTest(raw=raw):
                runner = FakeRunner([(0, raw)])
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    status = module.execute(runner, repo_root=REPO_ROOT)
                self.assertEqual(status, 1)
                self.assertEqual(len(runner.commands), 1)


if __name__ == "__main__":
    unittest.main()
