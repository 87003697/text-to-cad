from __future__ import annotations

import errno
import hashlib
import os
import stat
import subprocess
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts.pilot.agent_runtime import source_snapshot as source_snapshot_module
from scripts.pilot.agent_runtime.canonical_json import canonical_json_digest
from scripts.pilot.agent_runtime.source_snapshot import (
    SourceSnapshotError,
    SourceSnapshotPublicationError,
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
        self.calls: list[str] = []

    def versioning_status(self, bucket: str) -> str:
        self.calls.append("versioning")
        return self.versioning

    def current_version(self, bucket: str, key: str) -> tuple[str, str] | None:
        self.calls.append("current")
        version = self.current.get((bucket, key))
        return None if version is None else (version, f'"etag-{version}"')

    def put_create_only(self, bucket: str, key: str, payload: bytes) -> tuple[str, str]:
        self.calls.append("put")
        if (bucket, key) in self.current:
            raise SourceSnapshotError("create-only precondition failed")
        self.puts += 1
        version = f"v{self.puts}"
        self.objects[(bucket, key, version)] = payload
        self.current[(bucket, key)] = version
        return version, f'"etag-{version}"'

    def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes:
        self.calls.append("get")
        return self.objects[(bucket, key, version_id)]


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class SourceSnapshotTest(unittest.TestCase):
    def _build(self, root: Path, **kwargs: object):
        expected = kwargs["git_commit"]
        include_paths = tuple(sorted(kwargs["include_paths"]))
        entries = []
        for path in include_paths:
            candidate = root / path
            if candidate.is_file() and not candidate.is_symlink():
                payload = candidate.read_bytes()
                mode = "0755" if candidate.stat().st_mode & stat.S_IXUSR else "0644"
                size = len(payload)
                digest = _sha(payload)
            else:
                mode = "0644"
                size = 0
                digest = _sha(b"")
            entries.append(
                source_snapshot_module._GitEntryProof(
                    path=path,
                    mode=mode,
                    size=size,
                    sha256=digest,
                    storage="git-blob",
                )
            )
        proof = source_snapshot_module._GitSourceProof(
            commit=expected,
            entries=tuple(entries),
        )
        return source_snapshot_module._build_source_snapshot(
            root,
            git_verifier=lambda _root, _paths: proof,
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
                git_verifier=lambda _root, _paths: source_snapshot_module._GitSourceProof(
                    commit="b" * 40,
                    entries=(),
                ),
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

    def test_git_proof_rejects_mutation_between_proof_and_capture(self) -> None:
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

        def mutate_included(_root: Path) -> None:
            (root / "src" / "a.txt").write_bytes(b"changed")

        with self.assertRaisesRegex(SourceSnapshotError, "do not match the Git commit"):
            source_snapshot_module._build_source_snapshot(
                root,
                include_paths=("src/a.txt",),
                git_commit=commit,
                after_git_proof=mutate_included,
            )

    def test_git_proof_rechecks_excluded_and_untracked_drift_after_capture(self) -> None:
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

        def add_untracked(_root: Path) -> None:
            (root / "untracked.txt").write_text("drift", encoding="ascii")

        with self.assertRaisesRegex(SourceSnapshotError, "clean"):
            source_snapshot_module._build_source_snapshot(
                root,
                include_paths=("src/a.txt",),
                git_commit=commit,
                after_git_proof=add_untracked,
            )

    def test_git_lfs_pointer_binds_hydrated_bytes_without_disabling_filters(self) -> None:
        if subprocess.run(
            ("git", "lfs", "version"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode:
            self.skipTest("git-lfs is unavailable")
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        payload = (b"hydrated-lfs-payload\n" * 8)
        (root / ".gitattributes").write_text(
            "*.bin filter=lfs diff=lfs merge=lfs -text\n",
            encoding="ascii",
        )
        (root / "asset.bin").write_bytes(payload)
        commands = (
            ("init", "-q"),
            ("config", "user.email", "snapshot-test@example.invalid"),
            ("config", "user.name", "Snapshot Test"),
            ("add", ".gitattributes", "asset.bin"),
            ("commit", "-qm", "lfs fixture"),
        )
        for command in commands:
            subprocess.run(("git", "-C", os.fspath(root), *command), check=True)
        commit = subprocess.run(
            ("git", "-C", os.fspath(root), "rev-parse", "HEAD"),
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()
        pointer = subprocess.run(
            ("git", "-C", os.fspath(root), "cat-file", "blob", f"{commit}:asset.bin"),
            check=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertIn(b"version https://git-lfs.github.com/spec/v1\n", pointer)
        built = build_source_snapshot(root, include_paths=("asset.bin",), git_commit=commit)
        self.assertEqual(built.manifest["files"][0]["sha256"], _sha(payload))
        self.assertEqual(built.manifest["files"][0]["size"], len(payload))

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

    def test_publish_validates_complete_request_before_zero_adapter_calls(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        for bucket, region, candidate_key in (
            ("Bad_Bucket", "us-west-2", key),
            ("bucket-a", "INVALID", key),
            ("bucket-a", "us-west-2", "source-snapshots/latest.tar"),
        ):
            store = FakeStore()
            with self.assertRaises(SourceSnapshotError):
                publish_source_snapshot(
                    built,
                    store=store,
                    bucket=bucket,
                    region=region,
                    key=candidate_key,
                )
            self.assertEqual(store.calls, [])
        forged = replace(built, payload_bytes=built.payload_bytes + 1)
        store = FakeStore()
        with self.assertRaises(SourceSnapshotError):
            publish_source_snapshot(
                forged,
                store=store,
                bucket="bucket-a",
                region="us-west-2",
                key=key,
            )
        self.assertEqual(store.calls, [])

    def test_publish_normalizes_adapter_failures_and_possible_write_state(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"

        class VersioningFailure(FakeStore):
            def versioning_status(self, bucket: str) -> str:
                self.calls.append("versioning")
                raise RuntimeError("adapter detail")

        with self.assertRaises(SourceSnapshotPublicationError) as versioning_error:
            publish_source_snapshot(
                built,
                store=VersioningFailure(),
                bucket="bucket-a",
                region="us-west-2",
                key=key,
            )
        self.assertFalse(versioning_error.exception.may_have_written)
        self.assertFalse(versioning_error.exception.retry_allowed)

        class MalformedPut(FakeStore):
            def put_create_only(self, bucket: str, key: str, payload: bytes):
                self.calls.append("put")
                return ("invalid version", None)

        with self.assertRaises(SourceSnapshotPublicationError) as put_error:
            publish_source_snapshot(
                built,
                store=MalformedPut(),
                bucket="bucket-a",
                region="us-west-2",
                key=key,
            )
        self.assertTrue(put_error.exception.may_have_written)
        self.assertEqual(put_error.exception.stage, "create-only-put-response")

        class LostReread(FakeStore):
            def get_exact_version(self, bucket: str, key: str, version_id: str) -> bytes:
                self.calls.append("get")
                raise RuntimeError("lost response")

        with self.assertRaises(SourceSnapshotPublicationError) as reread_error:
            publish_source_snapshot(
                built,
                store=LostReread(),
                bucket="bucket-a",
                region="us-west-2",
                key=key,
            )
        self.assertTrue(reread_error.exception.may_have_written)
        self.assertEqual(reread_error.exception.stage, "exact-version-reread")

    def test_publish_fails_closed_on_same_key_substitution_or_lost_bytes(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        key = f"source-snapshots/payloads/sha256/{built.payload_sha256[7:]}.tar"
        store = FakeStore()
        store.objects[("bucket-a", key, "evil")] = b"substitution"
        store.current[("bucket-a", key)] = "evil"
        with self.assertRaisesRegex(SourceSnapshotError, "terminal validation"):
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

    def test_snapshot_build_deep_copies_and_freezes_nested_manifest(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(root, include_paths=("src/a.txt",), git_commit="a" * 40)
        mutable = deepcopy(built.manifest)
        copied = source_snapshot_module.SourceSnapshotBuild(
            manifest=mutable,
            manifest_bytes=bytearray(built.manifest_bytes),
            manifest_digest=built.manifest_digest,
            payload=bytearray(built.payload),
            payload_sha256=built.payload_sha256,
            payload_bytes=built.payload_bytes,
        )
        mutable["files"][0]["size"] = 999
        mutable["includePaths"].append("evil")
        self.assertEqual(copied.manifest["files"][0]["size"], 6)
        self.assertEqual(tuple(copied.manifest["includePaths"]), ("src/a.txt",))
        with self.assertRaises(TypeError):
            copied.manifest["pathCount"] = 9
        with self.assertRaises(TypeError):
            copied.manifest["files"][0]["size"] = 9

    def test_tar_pax_payload_matches_independent_long_path_golden(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        long_path = "a" * 110 + ".txt"
        (root / long_path).write_bytes(b"x")
        first = self._build(root, include_paths=(long_path,), git_commit="a" * 40)
        second = self._build(root, include_paths=(long_path,), git_commit="a" * 40)
        self.assertEqual(first.payload, second.payload)
        self.assertEqual(
            first.payload_sha256,
            "sha256:491b0cd8af1c8dda172d4303a1887a7f22ef638b7bf5a446bf040bd522541cb7",
        )
        self.assertEqual(len(first.payload), 10240)
        pax_header = first.payload[:512]
        self.assertEqual(pax_header[:100].rstrip(b"\0"), b"././@PaxHeader")
        self.assertEqual(pax_header[156:157], b"x")
        checksum = int(pax_header[148:156].rstrip(b"\0 "), 8)
        self.assertEqual(sum(pax_header[:148] + b"        " + pax_header[156:]), checksum)
        pax_size = int(pax_header[124:136].rstrip(b"\0 "), 8)
        pax_record = first.payload[512 : 512 + pax_size]
        self.assertEqual(pax_record, b"124 path=" + long_path.encode("ascii") + b"\n")
        file_header = first.payload[1024:1536]
        self.assertEqual(file_header[156:157], b"0")
        self.assertEqual(file_header[:100], long_path[:100].encode("ascii"))
        self.assertEqual(int(file_header[100:108].rstrip(b"\0 "), 8), 0o644)
        self.assertEqual(int(file_header[108:116].rstrip(b"\0 "), 8), 0)
        self.assertEqual(int(file_header[116:124].rstrip(b"\0 "), 8), 0)
        self.assertEqual(int(file_header[124:136].rstrip(b"\0 "), 8), 1)
        self.assertEqual(int(file_header[136:148].rstrip(b"\0 "), 8), 0)
        self.assertEqual(file_header[265:329], bytes(64))
        self.assertEqual(first.payload[1536:1537], b"x")
        self.assertFalse(any(first.payload[1537:]))

    def test_read_only_mount_requires_exact_bytes_modes_and_denied_write(self) -> None:
        temporary, root = self._tree()
        self.addCleanup(temporary.cleanup)
        built = self._build(
            root,
            include_paths=("src/a.txt", "tool.sh"),
            git_commit="a" * 40,
        )

        def denied(_root_fd: int) -> None:
            raise OSError(errno.EROFS, "read-only filesystem")

        self.assertTrue(
            source_snapshot_module._verify_read_only_mount(
                root,
                built.manifest,
                write_probe=denied,
            )
        )
        with self.assertRaisesRegex(SourceSnapshotError, "accepted a write"):
            source_snapshot_module._verify_read_only_mount(
                root,
                built.manifest,
                write_probe=lambda _root_fd: None,
            )
        extra = root / "extra.txt"
        extra.write_text("extra", encoding="ascii")
        with self.assertRaisesRegex(SourceSnapshotError, "path set"):
            source_snapshot_module._verify_read_only_mount(
                root,
                built.manifest,
                write_probe=denied,
            )
        extra.unlink()
        (root / "src" / "a.txt").write_bytes(b"wrong\n")
        with self.assertRaisesRegex(SourceSnapshotError, "differs"):
            source_snapshot_module._verify_read_only_mount(
                root,
                built.manifest,
                write_probe=denied,
            )

    def test_mount_probe_keeps_verified_root_fd_across_path_replacement(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)
        mount = parent / "mount"
        mount.mkdir()
        (mount / "source.txt").write_bytes(b"source")
        built = self._build(mount, include_paths=("source.txt",), git_commit="a" * 40)
        moved = parent / "moved"
        decoy = parent / "decoy"

        def replace_and_deny(root_fd: int) -> None:
            os.rename(mount, moved)
            decoy.mkdir()
            mount.symlink_to(decoy, target_is_directory=True)
            self.assertEqual(os.fstat(root_fd).st_ino, moved.stat().st_ino)
            raise OSError(errno.EROFS, "read-only filesystem")

        self.assertTrue(
            source_snapshot_module._verify_read_only_mount(
                mount,
                built.manifest,
                write_probe=replace_and_deny,
            )
        )

    def test_mount_probe_cleanup_failure_is_closed_and_normalized(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        (root / "source.txt").write_bytes(b"source")
        built = self._build(root, include_paths=("source.txt",), git_commit="a" * 40)
        with patch(
            "scripts.pilot.agent_runtime.source_snapshot.os.unlink",
            side_effect=OSError(errno.EIO, "cleanup failed"),
        ), self.assertRaisesRegex(SourceSnapshotError, "cleanup failed"):
            verify_read_only_mount(root, built.manifest)

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
