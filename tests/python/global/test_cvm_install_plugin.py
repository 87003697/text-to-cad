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
``smoke.install_plugin_isolated``, ``cvm_install_plugin._run_finalize``, and
``smoke.assert_critical_runtimes`` so nothing touches the real Codex CLI or
network. ``cvm_install_plugin._materialize_publish_source`` runs for real
against a planted ``.text-to-cad-stage-manifest.json`` because that is the
whole thing under test — no stub could exercise the manifest-driven
identity contract without reintroducing the exclusion-list bypass the fix
was meant to remove.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import tomllib
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


def _valid_provenance(
    head: str = "0" * 40, stage_manifest_digest: str = "0" * 64
) -> dict[str, Any]:
    return {
        "schema": "text-to-cad.push-provenance/2",
        "mac_branch": "develop",
        "mac_head": head,
        "mac_state": "clean",
        "stage_manifest_digest": stage_manifest_digest,
        "transfer_summary": {
            "sent_bytes": 1,
            "received_bytes": 1,
            "bytes_per_second": 1.0,
        },
        "runtime_attestation": {
            path: "a" * 64
            for path in plugin_deployment.REQUIRED_RUNTIME_ATTESTATION_PATHS
        },
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
        self.assertEqual(decoded["schema"], "text-to-cad.push-provenance/2")

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

    We keep the real lock, real ``_materialize_publish_source`` (so the
    stage-manifest identity contract is actually exercised), manifest
    recompute, receipt write, ``move_into_place``, and pointer publication
    paths so a lock-re-entry regression or a manifest-driven regression
    cannot slip past. Only the pieces that need a live Codex CLI or a real
    finalize script are stubbed: ``smoke.codex_version``,
    ``smoke.install_plugin_isolated``, and
    ``cvm_install_plugin._run_finalize``.
    """

    def __init__(self, transferred: Path, codex_home_root: Path) -> None:
        self.transferred = transferred
        self.codex_home_root = codex_home_root
        self._orig_install = _pkg_smoke.install_plugin_isolated
        self._orig_codex_version = _pkg_smoke.codex_version
        self._orig_finalize = cvm_install_plugin._run_finalize

    def __enter__(self) -> "_PublishHarness":
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
            # The real ``codex plugin marketplace add`` writes config.toml
            # holding the local marketplace registration and the plugin
            # enablement flag. The publisher includes it in the whole-home
            # manifest and parses it for registration semantics, so the harness must emit an
            # equivalent minimal document.
            config_path = codex_home / "config.toml"
            config_path.write_text(
                "[marketplaces.text-to-cad]\n"
                f'source = "{publish_tree}"\n'
                'source_type = "local"\n'
                '[plugins."cad@text-to-cad"]\n'
                "enabled = true\n",
                encoding="utf-8",
            )
            config_path.chmod(0o600)
            return {"installed_path": str(installed)}

        _pkg_smoke.install_plugin_isolated = fake_install  # type: ignore[assignment]
        _pkg_smoke.codex_version = lambda _exe: "codex-cli 0.147.0"  # type: ignore[assignment]
        cvm_install_plugin._run_finalize = lambda _tree: None  # type: ignore[assignment]
        return self

    def __exit__(self, *_exc) -> None:
        _pkg_smoke.install_plugin_isolated = self._orig_install  # type: ignore[assignment]
        _pkg_smoke.codex_version = self._orig_codex_version  # type: ignore[assignment]
        cvm_install_plugin._run_finalize = self._orig_finalize  # type: ignore[assignment]


class ReceiptWriteSafetyTests(unittest.TestCase):
    def test_receipt_temporary_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            receipt = root / plugin_deployment.RECEIPT_FILE
            temporary = receipt.with_name(f".{receipt.name}.tmp")
            victim = root / "victim.txt"
            victim.write_text("victim\n", encoding="utf-8")
            os.symlink(victim, temporary)

            cvm_install_plugin._atomic_receipt(receipt, {"ok": True})
            self.assertEqual(victim.read_text(encoding="utf-8"), "victim\n")
            self.assertTrue(temporary.is_symlink())
            self.assertTrue(receipt.is_file())

    def test_symlinked_transfer_root_is_rejected_before_install(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            actual = root / "actual"
            actual.mkdir()
            redirected = root / "text-to-cad"
            os.symlink(actual, redirected)
            codex_home = root / "home"
            codex_home.mkdir()

            with self.assertRaisesRegex(
                cvm_install_plugin.InstallError, "physical directory"
            ):
                cvm_install_plugin.publish(
                    redirected,
                    codex_home,
                    provenance=_valid_provenance(),
                )


def _build_transferred_source(root: Path) -> tuple[Path, str]:
    """Minimum ``~/text-to-cad``-shaped tree plus its authored stage manifest.

    Returns ``(src, stage_manifest_digest)``; callers thread the digest into
    ``_valid_provenance(..., stage_manifest_digest=digest)`` so the
    publisher's manifest-driven materializer validates end-to-end without
    a stubbed transport.
    """

    src = root / "text-to-cad"
    src.mkdir()
    (src / "VERSION").write_text("0.4.21\n", encoding="utf-8")
    # Plant every critical-runtime probe file so smoke.assert_critical_runtimes
    # succeeds on the installed cache without any real runtime bundling.
    for runtime_dir, probe_rel in _pkg_smoke.CRITICAL_RUNTIME_PATHS:
        probe = src / runtime_dir / probe_rel
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_bytes(b"probe\n")
    digest = plugin_deployment.write_stage_manifest(src)
    return src, digest


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
        stage_manifest_digest: str,
        *,
        head: str = "0" * 40,
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
                    provenance=_valid_provenance(
                        head=head,
                        stage_manifest_digest=stage_manifest_digest,
                    ),
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
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                receipt = self._publish_with_timeout(
                    transferred, codex_home_root, stage_digest
                )
            self.assertEqual(receipt["schema"], plugin_deployment.RECEIPT_SCHEMA)
            config = tomllib.loads(
                (Path(receipt["codex_home"]) / "config.toml").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                config["marketplaces"]["text-to-cad"]["source"],
                receipt["publish_tree"],
            )
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
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                first = self._publish_with_timeout(
                    transferred, codex_home_root, stage_digest
                )
                # Second publish of identical bytes must take the
                # existing-slot idempotent branch and republish under the same
                # already-held outer lock, which is the second self-deadlock
                # site (line 316 in cvm_install_plugin.py).
                second = self._publish_with_timeout(
                    transferred, codex_home_root, stage_digest
                )
            self.assertEqual(first["deployment_id"], second["deployment_id"])

    def test_identical_stage_with_different_git_head_keeps_own_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                first = self._publish_with_timeout(
                    transferred,
                    codex_home_root,
                    stage_digest,
                    head="1" * 40,
                )
                second = self._publish_with_timeout(
                    transferred,
                    codex_home_root,
                    stage_digest,
                    head="2" * 40,
                )

            self.assertNotEqual(first["deployment_id"], second["deployment_id"])
            self.assertEqual(first["source_git_sha"], "1" * 40)
            self.assertEqual(second["source_git_sha"], "2" * 40)

    def test_extra_codex_home_file_fails_before_replacing_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                self._publish_with_timeout(
                    transferred,
                    codex_home_root,
                    stage_digest,
                    head="1" * 40,
                )
                pointer = plugin_deployment.pointer_path(codex_home_root)
                previous_pointer = pointer.read_bytes()
                base_install = _pkg_smoke.install_plugin_isolated

                def install_with_extra_file(*args, **kwargs):
                    result = base_install(*args, **kwargs)
                    codex_home = Path(args[1])
                    (codex_home / "auth.json").write_text(
                        "{}\n", encoding="utf-8"
                    )
                    return result

                _pkg_smoke.install_plugin_isolated = install_with_extra_file  # type: ignore[assignment]
                with self.assertRaisesRegex(
                    cvm_install_plugin.InstallError, "unbound file"
                ):
                    self._publish_with_timeout(
                        transferred,
                        codex_home_root,
                        stage_digest,
                        head="2" * 40,
                    )
            self.assertEqual(pointer.read_bytes(), previous_pointer)

    def test_codex_cli_temporary_directory_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                base_install = _pkg_smoke.install_plugin_isolated

                def install_with_cli_tmp(*args, **kwargs):
                    result = base_install(*args, **kwargs)
                    cli_tmp = Path(args[1]) / "tmp" / "arg0"
                    cli_tmp.mkdir(parents=True, mode=0o700)
                    return result

                _pkg_smoke.install_plugin_isolated = install_with_cli_tmp  # type: ignore[assignment]
                receipt = self._publish_with_timeout(
                    transferred,
                    codex_home_root,
                    stage_digest,
                )

            self.assertFalse((Path(receipt["codex_home"]) / "tmp").exists())

    def test_different_transfers_that_finalize_identically_keep_own_provenance(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            first_src, _ = _build_transferred_source(first_root)
            second_src, _ = _build_transferred_source(second_root)
            (first_src / "requirements-dev.txt").write_text(
                "tooling==1\n", encoding="utf-8"
            )
            (second_src / "requirements-dev.txt").write_text(
                "tooling==2\n", encoding="utf-8"
            )
            first_stage = plugin_deployment.write_stage_manifest(first_src)
            second_stage = plugin_deployment.write_stage_manifest(second_src)
            self.assertNotEqual(first_stage, second_stage)

            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(first_src, codex_home_root):
                # The real finalizer removes development-only inputs. These
                # two transfers therefore converge to identical publish-tree
                # bytes even though their transfer authority is different.
                cvm_install_plugin._run_finalize = lambda tree: (  # type: ignore[assignment]
                    tree / "requirements-dev.txt"
                ).unlink()
                first = self._publish_with_timeout(
                    first_src, codex_home_root, first_stage
                )
                second = self._publish_with_timeout(
                    second_src, codex_home_root, second_stage
                )

            self.assertEqual(
                first["prepared_manifest_digest"],
                second["prepared_manifest_digest"],
            )
            self.assertNotEqual(first["deployment_id"], second["deployment_id"])
            self.assertEqual(
                first["transfer_provenance"]["stage_manifest_digest"],
                first_stage,
            )
            self.assertEqual(
                second["transfer_provenance"]["stage_manifest_digest"],
                second_stage,
            )


class CanonicalExclusionTests(unittest.TestCase):
    """The Mac-side exclusion list is now hygiene-only.

    Regression: the ``STAGE_SOURCE_EXCLUDES`` tuple exposed on
    ``cvm_push`` remains the SAME OBJECT as
    ``plugin_deployment.DEPLOYMENT_EXCLUDE_PATTERNS`` (no local copy that
    could drift). Exact snapshot identity is now enforced by the stage
    manifest, so this tuple is documented as staging hygiene only —
    dropping a pattern here can no longer smuggle CVM-overlay files into
    the published deployment identity, but keeping it out of drift is
    still worth catching before the two lists diverge silently.
    """

    def test_canonical_exclusion_list_is_shared_with_cvm_push(self) -> None:
        # ``cvm_push.STAGE_SOURCE_EXCLUDES`` is now sourced from
        # plugin_deployment.DEPLOYMENT_EXCLUDE_PATTERNS so drift is impossible.
        # We can't assertIs against the test's own plugin_deployment handle
        # (cvm_push imports via ``scripts.pilot.plugin_deployment`` while the
        # test loads a file-path module under the bare name ``plugin_deployment``,
        # so the two module objects — and thus the two tuple literals — are
        # distinct instances). Instead we assert the two contract-relevant
        # invariants directly: cvm_push's exported name is the SAME object as
        # the tuple it imported from plugin_deployment (no local copy), and the
        # tuple contents are byte-for-byte equal to the canonical list.
        cvm_push = _load("cvm_push", "scripts/pilot/cvm_push.py")
        self.assertIs(
            cvm_push.STAGE_SOURCE_EXCLUDES,
            cvm_push._plugin_deployment.DEPLOYMENT_EXCLUDE_PATTERNS,
        )
        self.assertEqual(
            cvm_push.STAGE_SOURCE_EXCLUDES,
            plugin_deployment.DEPLOYMENT_EXCLUDE_PATTERNS,
        )


class ManifestDrivenIdentityTests(unittest.TestCase):
    """Regression: publish-tree-src is bounded by the stage manifest.

    ``cvm-push`` runs a non-deleting rsync into ``~/text-to-cad`` on the
    CVM, so any file a prior push wrote there that later disappeared from
    the Mac stage lingers forever. The previous fix relied on an
    exclusion tuple to keep such files out of publish staging; an
    exclusion list can never guarantee identity against that overlay
    because a stale file that matches no pattern would silently ride
    into deployment identity. The stage manifest — authored on Mac,
    digest-bound into push provenance, validated by the publisher —
    replaces that with an allow-list: only the listed paths get
    materialized, and each is hash-checked on the way in.
    """

    def test_stale_persistent_overlay_file_is_absent_from_publish_staging(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src, stage_digest = _build_transferred_source(root)
            # Simulate the persistent CVM overlay accumulating a stale
            # ordinary file that was NOT listed in the stage manifest the
            # Mac just transferred (e.g. a prior push wrote it, the Mac
            # later removed it, and the CVM's non-deleting rsync kept it).
            stale_dir = src / "scripts" / "leftover"
            stale_dir.mkdir(parents=True, exist_ok=True)
            stale_file = stale_dir / "old.py"
            stale_file.write_text(
                "this file was removed from the Mac side\n", encoding="utf-8"
            )

            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(src, codex_home_root):
                receipt = cvm_install_plugin.publish(
                    src,
                    codex_home_root,
                    provenance=_valid_provenance(
                        stage_manifest_digest=stage_digest
                    ),
                )
            publish_tree = Path(receipt["publish_tree"])
            # Listed keeper file entered the published tree.
            self.assertTrue((publish_tree / "VERSION").is_file())
            for runtime_dir, probe_rel in _pkg_smoke.CRITICAL_RUNTIME_PATHS:
                self.assertTrue(
                    (publish_tree / runtime_dir / probe_rel).is_file(),
                    f"listed critical runtime probe missing: {runtime_dir}/{probe_rel}",
                )
            # Stale unlisted file is absent from publish staging even
            # though it lived under the persistent source root.
            self.assertFalse(
                (publish_tree / "scripts" / "leftover" / "old.py").exists(),
                "stale non-excluded ordinary file leaked into publish staging",
            )
            # And the stage manifest file itself must not become plugin
            # content (would create a recursive self-hashing paradox on
            # future republishes and expose control-plane state as
            # authority state).
            self.assertFalse(
                (publish_tree / plugin_deployment.STAGE_MANIFEST_FILENAME).exists()
            )

    def test_version_is_read_from_materialized_snapshot_not_mutable_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            original_materialize = cvm_install_plugin._materialize_publish_source

            def materialize_then_mutate_overlay(*args, **kwargs):
                original_materialize(*args, **kwargs)
                (src / "VERSION").write_text("9.9.9\n", encoding="utf-8")

            with _PublishHarness(src, codex_home_root):
                cvm_install_plugin._materialize_publish_source = (  # type: ignore[assignment]
                    materialize_then_mutate_overlay
                )
                try:
                    receipt = cvm_install_plugin.publish(
                        src,
                        codex_home_root,
                        provenance=_valid_provenance(
                            stage_manifest_digest=stage_digest
                        ),
                    )
                finally:
                    cvm_install_plugin._materialize_publish_source = (  # type: ignore[assignment]
                        original_materialize
                    )
            self.assertEqual(receipt["version"], "0.4.21")

    def test_provenance_digest_mismatch_fails_install(self) -> None:
        # If the provenance blob claims a digest that does not match the
        # manifest actually on disk in the transferred source, the
        # publisher must fail before writing any authority state.
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src, _ = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(src, codex_home_root):
                with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                    cvm_install_plugin.publish(
                        src,
                        codex_home_root,
                        provenance=_valid_provenance(
                            stage_manifest_digest="f" * 64
                        ),
                    )
            self.assertEqual(ctx.exception.stage, "install")
            self.assertIn("digest", str(ctx.exception).lower())

    def test_content_mismatch_after_manifest_fails_install(self) -> None:
        # If a listed file was tampered with in the persistent overlay
        # after the manifest was authored, the publisher must fail with
        # a content-mismatch error rather than publish the tampered bytes.
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            src, stage_digest = _build_transferred_source(root)
            (src / "VERSION").write_text(
                "tampered-in-flight\n", encoding="utf-8"
            )
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(src, codex_home_root):
                with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                    cvm_install_plugin.publish(
                        src,
                        codex_home_root,
                        provenance=_valid_provenance(
                            stage_manifest_digest=stage_digest
                        ),
                    )
            self.assertEqual(ctx.exception.stage, "install")
            self.assertIn("content mismatch", str(ctx.exception).lower())

    def test_missing_stage_manifest_in_provenance_fails_decode(self) -> None:
        # A well-formed provenance blob with NO ``stage_manifest_digest``
        # must fail closed at ``_decode_provenance`` — that is where the
        # schema check lives (``publish()`` trusts an already-validated
        # dict), and it is the boundary an untrusted SSH argv actually
        # crosses. Rejecting there prevents a legacy provenance shape
        # from ever reaching ``publish()``.
        bad = _valid_provenance()
        del bad["stage_manifest_digest"]
        encoded = _encode_provenance(bad)
        with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
            cvm_install_plugin._decode_provenance(encoded)
        self.assertEqual(ctx.exception.stage, "install")
        self.assertIn("stage_manifest_digest", str(ctx.exception))


class SameSlotRevalidationTests(unittest.TestCase):
    """Regression: same-digest republication must fully re-verify the slot.

    The pre-fix idempotent branch republished ``current.json`` after
    matching only ``prepared_manifest_digest`` and ``version``. Any post-
    publication tampering with the installed cache, config.toml, or
    critical runtime probes was silently reused. We now call
    :func:`plugin_deployment.validate_deployment_slot` before republishing
    and the fail must map to verify-stage (exit 8), never install-stage.
    """

    def test_corrupted_existing_slot_fails_verify_on_republish(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                first = cvm_install_plugin.publish(
                    transferred,
                    codex_home_root,
                    provenance=_valid_provenance(
                        stage_manifest_digest=stage_digest
                    ),
                )
                # Tamper the already-published slot: flip enabled=true to
                # enabled=false in the isolated CODEX_HOME's config.toml.
                # Same input bytes on the second publish → same
                # prepared_manifest_digest+version match → without the
                # new revalidation the tampered slot would silently
                # republish.
                config_path = Path(first["codex_home"]) / "config.toml"
                tampered = config_path.read_text(encoding="utf-8").replace(
                    "enabled = true", "enabled = false"
                )
                config_path.write_text(tampered, encoding="utf-8")

                with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                    cvm_install_plugin.publish(
                        transferred,
                        codex_home_root,
                        provenance=_valid_provenance(
                            stage_manifest_digest=stage_digest
                        ),
                    )
            self.assertEqual(ctx.exception.stage, "verify")
            self.assertIn("revalidation", str(ctx.exception).lower())

    def test_deleted_critical_runtime_fails_verify_on_republish(self) -> None:
        with tempfile.TemporaryDirectory() as root_text:
            root = Path(root_text)
            transferred, stage_digest = _build_transferred_source(root)
            codex_home_root = root / "home"
            codex_home_root.mkdir()
            with _PublishHarness(transferred, codex_home_root):
                first = cvm_install_plugin.publish(
                    transferred,
                    codex_home_root,
                    provenance=_valid_provenance(
                        stage_manifest_digest=stage_digest
                    ),
                )
                # Remove one critical runtime probe from the installed cache.
                installed = Path(first["installed_path"])
                runtime_dir, probe_rel = _pkg_smoke.CRITICAL_RUNTIME_PATHS[0]
                (installed / runtime_dir / probe_rel).unlink()

                with self.assertRaises(cvm_install_plugin.InstallError) as ctx:
                    cvm_install_plugin.publish(
                        transferred,
                        codex_home_root,
                        provenance=_valid_provenance(
                            stage_manifest_digest=stage_digest
                        ),
                    )
            self.assertEqual(ctx.exception.stage, "verify")


if __name__ == "__main__":
    unittest.main()
