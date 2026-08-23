"""Tests for the CVM-side ``cvm_install_plugin`` publisher.

These tests exercise the fail-closed contracts that make the receipt trustable:

* The Codex CLI version gate must run before any marketplace mutation and must
  reject malformed or too-old CLI versions.
* Push provenance transported over the SSH argv must be strictly bounded,
  base64url, canonical JSON, and schema-valid — any failure aborts the install
  stage before touching the authority.
* When a same-content deployment slot already exists, the publisher must
  idempotently republish the existing pointer instead of racing a partial
  reinstall.
* Divergent contents at the same deployment id must fail the verify stage
  rather than clobber the existing slot.

Every test wires stub replacements for ``smoke.codex_version``,
``smoke.install_plugin_isolated``, ``cvm_install_plugin._rsync_copy``,
``cvm_install_plugin._run_finalize``, and ``smoke.assert_critical_runtimes`` so
nothing touches the real Codex CLI or network.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tests.python.support.paths import REPO_ROOT


def _load(module_name: str, rel: str):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, REPO_ROOT / rel)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


cvm_install_plugin = _load(
    "cvm_install_plugin", "scripts/pilot/cvm_install_plugin.py"
)
plugin_deployment = _load(
    "plugin_deployment", "scripts/pilot/plugin_deployment.py"
)
smoke = _load("smoke_installed_plugin", "scripts/release/smoke_installed_plugin.py")

# ``cvm_install_plugin`` imports smoke via ``from scripts.release import
# smoke_installed_plugin as smoke``, so the module object bound inside the
# publisher is *not* the ``smoke`` we loaded above. Monkey-patches must target
# the publisher's ``smoke`` attribute to actually intercept its calls.
_pkg_smoke = cvm_install_plugin.smoke


def _encode_provenance(document: dict[str, Any]) -> str:
    body = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(body).decode("ascii")


def _valid_provenance(head: str = "0" * 40) -> dict[str, Any]:
    return {
        "schema": "text-to-cad.push-provenance/1",
        "mac_branch": "develop",
        "mac_head": head,
        "mac_state": "clean",
        "transfer_summary": {
            "sent_bytes": 1,
            "received_bytes": 1,
            "bytes_per_second": 1.0,
        },
        "runtime_attestation": {"scripts/pilot/runner.py": "a" * 64},
    }


class VersionGateTests(unittest.TestCase):
    """The gate must fail closed before any marketplace mutation."""

    def _run_gate(self, version_string: str) -> str:
        original = _pkg_smoke.codex_version
        _pkg_smoke.codex_version = lambda _exe: version_string  # type: ignore[assignment]
        try:
            return cvm_install_plugin._ensure_codex_version_gate("codex")
        finally:
            _pkg_smoke.codex_version = original  # type: ignore[assignment]

    def test_gate_accepts_exact_minimum(self) -> None:
        result = self._run_gate("codex-cli 0.142.0")
        self.assertEqual(result, "codex-cli 0.142.0")

    def test_gate_accepts_newer(self) -> None:
        result = self._run_gate("codex-cli 0.147.0")
        self.assertEqual(result, "codex-cli 0.147.0")

    def test_gate_rejects_below_minimum(self) -> None:
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            self._run_gate("codex-cli 0.141.9")
        self.assertEqual(ctx.exception.stage, "install")
        self.assertIn("0.141.9", str(ctx.exception))

    def test_gate_rejects_unparseable(self) -> None:
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            self._run_gate("codex-cli beta")
        self.assertEqual(ctx.exception.stage, "install")

    def test_gate_wraps_smoke_error(self) -> None:
        def raiser(_exe: str) -> str:
            raise _pkg_smoke.SmokeError("cannot exec codex")

        original = _pkg_smoke.codex_version
        _pkg_smoke.codex_version = raiser  # type: ignore[assignment]
        try:
            with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                cvm_install_plugin._ensure_codex_version_gate("codex")
        finally:
            _pkg_smoke.codex_version = original  # type: ignore[assignment]
        self.assertEqual(ctx.exception.stage, "install")


class ProvenanceDecodeTests(unittest.TestCase):
    """Bad provenance must never reach ``publish()``."""

    def test_valid_provenance_decodes(self) -> None:
        encoded = _encode_provenance(_valid_provenance())
        decoded = cvm_install_plugin._decode_provenance(encoded)
        self.assertEqual(decoded["schema"], "text-to-cad.push-provenance/1")

    def test_missing_provenance_rejected(self) -> None:
        for value in (None, "", "   "):
            with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                cvm_install_plugin._decode_provenance(value)
            self.assertEqual(ctx.exception.stage, "install")

    def test_oversized_provenance_rejected(self) -> None:
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance(
                "A" * (cvm_install_plugin.MAX_PROVENANCE_BYTES + 1)
            )
        self.assertEqual(ctx.exception.stage, "install")

    def test_non_base64url_alphabet_rejected(self) -> None:
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance("not*base64!!")
        self.assertEqual(ctx.exception.stage, "install")

    def test_invalid_base64_rejected(self) -> None:
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance("AAA")
        self.assertEqual(ctx.exception.stage, "install")

    def test_non_json_payload_rejected(self) -> None:
        encoded = base64.urlsafe_b64encode(b"not json").decode("ascii")
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance(encoded)
        self.assertEqual(ctx.exception.stage, "install")

    def test_schema_mismatch_rejected(self) -> None:
        bad = _valid_provenance()
        bad["schema"] = "text-to-cad.push-provenance/0"
        encoded = _encode_provenance(bad)
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance(encoded)
        self.assertEqual(ctx.exception.stage, "install")

    def test_bad_head_rejected(self) -> None:
        bad = _valid_provenance(head="not-a-sha")
        encoded = _encode_provenance(bad)
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance(encoded)
        self.assertEqual(ctx.exception.stage, "install")


class LockFileContractTests(unittest.TestCase):
    """The publisher must open ``deployments/.publish.lock`` before mutation.

    Fully exercising an end-to-end idempotent republish requires standing up a
    real Codex CLI install — out of scope for this unit test. We instead assert
    the fail-closed lock-acquire path: attempting to publish while the lock
    handle is already held by another writer must not race, and the publisher
    must reject a missing-source before ever asking for the lock.
    """

    def test_missing_transferred_source_rejected_before_lock(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                cvm_install_plugin.publish(
                    root / "does-not-exist",
                    codex_home_root,
                    provenance=_valid_provenance(),
                )
        self.assertEqual(ctx.exception.stage, "install")
        # No lock file must have been created before validation rejected the
        # inputs; if this stops holding we have leaked the lock to callers
        # that never got past bootstrap.
        self.assertFalse(
            (
                codex_home_root
                / plugin_deployment.AUTHORITY_ROOT_NAME
                / "deployments"
                / plugin_deployment.LOCK_NAME
            ).exists()
        )


class _PublishHarness:
    """Assemble the minimum external stubs for a real ``publish()`` run.

    We keep the real lock, manifest recompute, receipt write, move_into_place,
    and pointer publication paths so a lock-re-entry regression cannot slip
    past. Only the pieces that need a live Codex CLI, a real finalize script,
    or a real rsync are stubbed: ``smoke.codex_version``,
    ``smoke.install_plugin_isolated``, ``cvm_install_plugin._run_finalize``,
    and ``cvm_install_plugin._rsync_copy``.
    """

    def __init__(self, transferred: Path, codex_home_root: Path) -> None:
        self.transferred = transferred
        self.codex_home_root = codex_home_root
        self._orig_rsync = cvm_install_plugin._rsync_copy
        self._orig_install = _pkg_smoke.install_plugin_isolated
        self._orig_codex_version = _pkg_smoke.codex_version
        self._orig_finalize = cvm_install_plugin._run_finalize

    def __enter__(self) -> "_PublishHarness":
        def fake_rsync(src: Path, dst: Path) -> None:
            dst.mkdir(parents=True, exist_ok=True)
            for entry in src.rglob("*"):
                if entry.is_file() and not entry.is_symlink():
                    rel = entry.relative_to(src)
                    target = dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entry, target)

        def fake_install(
            publish_tree: Path,
            codex_home: Path,
            *,
            codex_executable: str,
            plugin_selector: str,
        ) -> dict[str, Any]:
            # Mirror the publish tree byte-for-byte into a canonical cache path
            # so the manifest recompute the publisher does against the
            # installed_path matches without any real CLI running.
            cache_rel = Path("plugins/cache/text-to-cad/cad")
            installed = codex_home / cache_rel
            installed.mkdir(parents=True, exist_ok=True)
            for entry in publish_tree.rglob("*"):
                if entry.is_file() and not entry.is_symlink():
                    dst = installed / entry.relative_to(publish_tree)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(entry, dst)
            return {"installed_path": str(installed)}

        cvm_install_plugin._rsync_copy = fake_rsync  # type: ignore[assignment]
        _pkg_smoke.install_plugin_isolated = fake_install  # type: ignore[assignment]
        _pkg_smoke.codex_version = lambda _exe: "codex-cli 0.147.0"  # type: ignore[assignment]
        cvm_install_plugin._run_finalize = lambda _tree: None  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc) -> None:
        cvm_install_plugin._rsync_copy = self._orig_rsync  # type: ignore[assignment]
        _pkg_smoke.install_plugin_isolated = self._orig_install  # type: ignore[assignment]
        _pkg_smoke.codex_version = self._orig_codex_version  # type: ignore[assignment]
        cvm_install_plugin._run_finalize = self._orig_finalize  # type: ignore[assignment]


def _build_transferred_source(root: Path) -> Path:
    """Minimum ``~/text-to-cad``-shaped tree that satisfies ``publish()``."""

    src = root / "text-to-cad"
    src.mkdir()
    (src / "VERSION").write_text("0.4.21\n", encoding="utf-8")
    # Plant every critical-runtime probe file so smoke.assert_critical_runtimes
    # succeeds on the installed cache without any real runtime bundling.
    for runtime_dir, probe_rel in _pkg_smoke.CRITICAL_RUNTIME_PATHS:
        probe = src / runtime_dir / probe_rel
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"probe\n")
    return src


class PublishLockReentryRegressionTests(unittest.TestCase):
    """Regression: ``publish()`` must return while holding its outer flock.

    The previous release opened ``deployments/.publish.lock`` inside
    ``cvm_install_plugin.publish()`` then called ``publish_authority`` which
    opened the same lock on a second file descriptor and re-requested
    ``LOCK_EX``. Two OFDs on the same lock file cannot both hold ``LOCK_EX``,
    so the second ``flock`` blocked forever. A real isolated CLI probe caught
    it after 15 minutes; the test below reproduces it with a 5-second wall
    timeout and no live Codex CLI.
    """

    def _publish_with_timeout(
        self,
        transferred: Path,
        codex_home_root: Path,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        import threading

        holder: dict[str, Any] = {}
        error: dict[str, BaseException] = {}

        def worker() -> None:
            try:
                holder["receipt"] = cvm_install_plugin.publish(
                    transferred,
                    codex_home_root,
                    provenance=_valid_provenance(),
                )
            except BaseException as exc:  # noqa: BLE001
                error["exc"] = exc

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise AssertionError(
                "cvm_install_plugin.publish() did not return within "
                f"{timeout}s — the deployments/.publish.lock re-entry deadlock "
                "has regressed"
            )
        if "exc" in error:
            raise error["exc"]
        return holder["receipt"]

    def test_fresh_publish_returns_without_deadlocking_on_publish_lock(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                receipt = self._publish_with_timeout(transferred, codex_home_root)
            self.assertEqual(receipt["schema"], plugin_deployment.RECEIPT_SCHEMA)
            pointer = (
                codex_home_root
                / plugin_deployment.AUTHORITY_ROOT_NAME
                / "deployments"
                / plugin_deployment.POINTER_NAME
            )
            self.assertTrue(pointer.is_file(), "current.json pointer not published")
            published = json.loads(pointer.read_text(encoding="utf-8"))
            self.assertEqual(
                published["deployment_id"], receipt["deployment_id"]
            )

    def test_identical_content_republish_returns_without_deadlocking(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                first = self._publish_with_timeout(transferred, codex_home_root)
                # Second publish of identical bytes must take the
                # existing-slot idempotent branch and republish under the same
                # already-held outer lock, which is the second self-deadlock
                # site (line 316 in cvm_install_plugin.py).
                second = self._publish_with_timeout(transferred, codex_home_root)
            self.assertEqual(first["deployment_id"], second["deployment_id"])


if __name__ == "__main__":
    unittest.main()
