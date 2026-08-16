from __future__ import annotations

import hashlib
import errno
import os
import stat
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.pilot.agent_runtime import source_snapshot as source_snapshot_module
from scripts.pilot.agent_runtime.canonical_json import canonical_json_digest
from scripts.pilot.agent_runtime.source_snapshot import (
    SourceSnapshotError,
    build_source_snapshot,
    build_source_snapshot_lock,
    parse_source_snapshot_document,
    publish_source_snapshot,
    verify_read_only_mount,
    verify_source_snapshot_visibility,
)


class FakeStore:
    def __init__(self, *, versioning: str = "Enabled") -> None:
        self.versioning = versioning
        self.objects: dict[tuple[str, str, str], bytes] = {}
        self.current: dict[tuple[str, str], str] = {}
        self.puts = 0

    def versioning_status(self, bucket: str) -> str:
        return self.versioning

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None:
        version = self.current.get((bucket, key))
        return None if version is None else (version, f'"etag-{version}"')

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]:
        if (bucket, key) in self.current:
            raise SourceSnapshotError("create-only precondition failed")
        self.puts += 1
        version = f"v{self.puts}"
        self.objects[(bucket, key, version)] = payload
        self.current[(bucket, key)] = version
        return version, f'"etag-{version}"'

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes:
        return self.objects[(bucket, key, version_id)]


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class SourceSnapshotTest(unittest.TestCase):
    def _build(self, root: Path, **kwargs: object):
        expected = kwargs["git_commit"]
        return source_snapshot_module._build_source_snapshot(
            root,
            git_verifier=lambda _root, _paths: expected,
            **kwargs,
        )

    def _tree(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "src").mkdir()
        (root / "src" / "a.txt").write_bytes(b"alpha\n")
        executable = root / "tool.sh"
        executable.write_bytes(b"#!/bin/sh\n")
        executable.chmod(0o755)
        return temporary, root

    def test_builder_is_closed_deterministic_and_uses_shared_encoder(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        first = self._build(
            root,
            include_paths=("tool.sh", "src/a.txt"),
            git_commit="a" * 40,
        )
        second = self._build(
            root,
            include_paths=("src/a.txt", "tool.sh"),
            git_commit="a" * 40,
        )
        self.assertEqual(first.manifest_bytes, second.manifest_bytes)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(first.manifest_digest, canonical_json_digest(first.manifest))
        self.assertEqual(first.payload_sha256, _sha(first.payload))
        self.assertEqual(first.manifest["pathCount"], 2)
        self.assertEqual(first.manifest["totalBytes"], 16)
        self.assertEqual(
            list(first.manifest["files"]),
            [
                {
                    "mode": "0644",
                    "path": "src/a.txt",
                    "sha256": _sha(b"alpha\n"),
                    "size": 6,
                    "type": "regular",
                },
                {
                    "mode": "0755",
                    "path": "tool.sh",
                    "sha256": _sha(b"#!/bin/sh\n"),
                    "size": 10,
                    "type": "regular",
                },
            ],
        )
        parsed = parse_source_snapshot_document("manifest", first.manifest_bytes)
        self.assertEqual(canonical_json_digest(parsed), first.manifest_digest)

    def test_builder_rejects_noncanonical_paths_symlinks_and_special_files(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        (root / "link").symlink_to("src/a.txt")
        fifo = root / "pipe"
        os.mkfifo(fifo)
        self.addCleanup(lambda: fifo.unlink(missing_ok=True))
        for path in (
            "/etc/passwd",
            "../escape",
            "src/../tool.sh",
            "",
            "src//a.txt",
            "src\\a.txt",
            "src/a.txt\n",
        ):
            with self.subTest(path=path), self.assertRaises(SourceSnapshotError):
                self._build(root, include_paths=(path,), git_commit="a" * 40)
        with self.assertRaisesRegex(SourceSnapshotError, "symlink"):
            self._build(root, include_paths=("link",), git_commit="a" * 40)
        with self.assertRaisesRegex(SourceSnapshotError, "regular"):
            self._build(root, include_paths=("pipe",), git_commit="a" * 40)
        socket_path = root / "socket"
        socket_path.write_bytes(b"")
        original_stat = os.stat
        socket_stat = list(original_stat(socket_path))
        socket_stat[0] = stat.S_IFSOCK | 0o600

        def report_socket(path: object, *args: object, **kwargs: object):
            if path == "socket":
                return os.stat_result(socket_stat)
            return original_stat(path, *args, **kwargs)

        with patch(
            "scripts.pilot.agent_runtime.source_snapshot.os.stat",
            side_effect=report_socket,
        ), self.assertRaisesRegex(SourceSnapshotError, "regular"):
            self._build(root, include_paths=("socket",), git_commit="a" * 40)

    def test_builder_rejects_directory_and_duplicate_or_overlapping_selection(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        for selected in (("src",), ("src/a.txt", "src/a.txt")):
            with self.assertRaises(SourceSnapshotError):
                self._build(root, include_paths=selected, git_commit="a" * 40)

    def test_builder_detects_read_race(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)

        def mutate(path: str, _: int) -> None:
            if path == "src/a.txt":
                (root / path).write_bytes(b"changed")

        with self.assertRaisesRegex(SourceSnapshotError, "changed while reading"):
            self._build(
                root,
                include_paths=("src/a.txt",),
                git_commit="a" * 40,
                after_read=mutate,
            )

    def test_builder_detects_path_replacement_race(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)

        def replace(path: str, _: int) -> None:
            original = root / path
            replacement = root / "replacement"
            replacement.write_bytes(original.read_bytes())
            os.replace(replacement, original)

        with self.assertRaisesRegex(SourceSnapshotError, "changed while reading"):
            self._build(
                root,
                include_paths=("src/a.txt",),
                git_commit="a" * 40,
                after_read=replace,
            )

    def test_git_commit_proof_is_mandatory_and_exact(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        with self.assertRaisesRegex(SourceSnapshotError, "does not equal"):
            source_snapshot_module._build_source_snapshot(
                root,
                include_paths=("src/a.txt",),
                git_commit="a" * 40,
                git_verifier=lambda _root, _paths: "b" * 40,
            )

    def test_default_git_policy_rejects_any_dirty_or_untracked_source(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        commands = (
            ("init", "-q"),
            ("config", "user.email", "snapshot-test@example.invalid"),
            ("config", "user.name", "Snapshot Test"),
            ("add", "src/a.txt", "tool.sh"),
            ("commit", "-qm", "fixture"),
        )
        for command in commands:
            subprocess.run(("git", "-C", os.fspath(root), *command), check=True)
        commit = subprocess.run(
            ("git", "-C", os.fspath(root), "rev-parse", "HEAD"),
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        built = build_source_snapshot(root, include_paths=("src/a.txt",), git_commit=commit)
        self.assertEqual(built.manifest["gitCommit"], commit)
        (root / "untracked.txt").write_text("not admitted", encoding="ascii")
        with self.assertRaisesRegex(SourceSnapshotError, "clean"):
            build_source_snapshot(root, include_paths=("src/a.txt",), git_commit=commit)

    def test_publish_requires_versioning_content_key_and_exact_reread(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(
            root,
            include_paths=("src/a.txt", "tool.sh"),
            git_commit="a" * 40,
        )
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        store = FakeStore()
        receipt = publish_source_snapshot(
            built, store=store, bucket="bucket-a", region="us-west-2", key=key
        )
        self.assertEqual(receipt["versionId"], "v1")
        self.assertEqual(receipt["disposition"], "created")
        self.assertTrue(receipt["exactVersionReread"])
        reused = publish_source_snapshot(
            built, store=store, bucket="bucket-a", region="us-west-2", key=key
        )
        self.assertEqual(reused["disposition"], "exact-reuse")
        self.assertEqual(store.puts, 1)

        with self.assertRaisesRegex(SourceSnapshotError, "versioning"):
            publish_source_snapshot(
                built,
                store=FakeStore(versioning="Suspended"),
                bucket="bucket-a",
                region="us-west-2",
                key=key,
            )
        with self.assertRaisesRegex(SourceSnapshotError, "content-addressed"):
            publish_source_snapshot(
                built, store=FakeStore(), bucket="bucket-a", region="us-west-2", key="latest.tar"
            )

    def test_publish_fails_closed_on_same_key_substitution_or_lost_bytes(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        store = FakeStore()
        store.objects[("bucket-a", key, "evil")] = b"substitution"
        store.current[("bucket-a", key)] = "evil"
        with self.assertRaisesRegex(SourceSnapshotError, "exact reread"):
            publish_source_snapshot(
                built, store=store, bucket="bucket-a", region="us-west-2", key=key
            )
        forged_payload = built.payload[:-1] + b"x"
        forged = replace(
            built,
            payload=forged_payload,
            payload_sha256=_sha(forged_payload),
        )
        forged_key = f"source-snapshots/payloads/sha256/{forged.payload_sha256[7:]}.tar"
        with self.assertRaisesRegex(SourceSnapshotError, "tar-pax-v1"):
            publish_source_snapshot(
                forged,
                store=FakeStore(),
                bucket="bucket-a",
                region="us-west-2",
                key=forged_key,
            )

    def test_visibility_rereads_exact_s3_version_and_mac_payload(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        store = FakeStore()
        publication = publish_source_snapshot(
            built, store=store, bucket="bucket-a", region="us-west-2", key=key
        )
        visibility = verify_source_snapshot_visibility(
            built, publication, store=store, mac_reader=lambda relative_key: built.payload
        )
        self.assertTrue(visibility["s3ExactVersionVisible"])
        self.assertTrue(visibility["macMountVisible"])
        with self.assertRaisesRegex(SourceSnapshotError, "Mac mount"):
            verify_source_snapshot_visibility(
                built, publication, store=store, mac_reader=lambda relative_key: b"wrong"
            )

    def test_lock_closes_chain_without_self_or_reverse_binding(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        store = FakeStore()
        publication = publish_source_snapshot(
            built, store=store, bucket="bucket-a", region="us-west-2", key=key
        )
        visibility = verify_source_snapshot_visibility(
            built, publication, store=store, mac_reader=lambda relative_key: built.payload
        )
        lock = build_source_snapshot_lock(built, publication, visibility)
        lock_digest = canonical_json_digest(lock)
        self.assertEqual(lock["publicationReceiptDigest"], canonical_json_digest(publication))
        self.assertEqual(lock["visibilityReceiptDigest"], canonical_json_digest(visibility))
        self.assertNotIn("sourceSnapshotLockDigest", lock)
        self.assertNotIn("sourceSnapshotLockDigest", publication)
        self.assertNotIn("sourceSnapshotLockDigest", visibility)
        self.assertRegex(lock_digest, r"^sha256:[0-9a-f]{64}$")

    def test_read_only_mount_requires_exact_bytes_modes_and_denied_write(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(
            root,
            include_paths=("src/a.txt", "tool.sh"),
            git_commit="a" * 40,
        )

        def denied() -> None:
            raise OSError(errno.EROFS, "read-only filesystem")

        self.assertTrue(verify_read_only_mount(root, built.manifest, write_probe=denied))
        with self.assertRaisesRegex(SourceSnapshotError, "accepted a write"):
            verify_read_only_mount(root, built.manifest, write_probe=lambda: None)
        extra = root / "extra.txt"
        extra.write_text("extra", encoding="ascii")
        with self.assertRaisesRegex(SourceSnapshotError, "path set"):
            verify_read_only_mount(root, built.manifest, write_probe=denied)
        extra.unlink()
        (root / "src" / "a.txt").write_bytes(b"wrong\n")
        with self.assertRaisesRegex(SourceSnapshotError, "differs"):
            verify_read_only_mount(root, built.manifest, write_probe=denied)

    def test_parsers_reject_unknown_keys_and_cross_document_substitution(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        malformed = dict(built.manifest)
        malformed["success"] = True
        from scripts.pilot.agent_runtime.canonical_json import canonical_json_bytes

        with self.assertRaisesRegex(SourceSnapshotError, "unexpected keys"):
            parse_source_snapshot_document("manifest", canonical_json_bytes(malformed))
        with self.assertRaises(SourceSnapshotError):
            parse_source_snapshot_document("lock", built.manifest_bytes)


if __name__ == "__main__":
    unittest.main()
