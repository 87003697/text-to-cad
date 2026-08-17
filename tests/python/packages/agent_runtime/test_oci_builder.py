from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[4]
BUILDER = REPO_ROOT / "packages" / "agent_runtime" / "oci_builder.py"
BUILD_CLI = REPO_ROOT / "scripts" / "pilot" / "agent-runtime-build.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("agent_runtime_oci_builder", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("builder module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_build_cli():
    spec = importlib.util.spec_from_file_location("agent_runtime_build_cli", BUILD_CLI)
    if spec is None or spec.loader is None:
        raise RuntimeError("build CLI module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_root(root: Path, marker: bytes = b"same\n") -> None:
    entrypoint = root / "usr/local/libexec/text-to-cad-agent-entrypoint"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_bytes(b"#!/bin/sh\nprintf 'sealed-agent-runtime-self-test\\n'\n")
    entrypoint.chmod(0o555)
    payload = root / "opt/text-to-cad/payload.txt"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(marker)
    payload.chmod(0o444)
    cup = root / "usr/share/text-to-cad/cup-capability-manifest.json"
    cup.parent.mkdir(parents=True)
    cup.write_bytes(b'{"schema":"fixture"}')
    cup.chmod(0o444)


class AgentRuntimeOciBuilderTests(unittest.TestCase):
    def test_exact_input_loader_rejects_symlink_and_substitution(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = root / "payload"
            payload.write_bytes(b"approved")
            payload.chmod(0o444)
            digest = "sha256:" + hashlib.sha256(b"approved").hexdigest()
            self.assertEqual(
                builder.read_exact_regular(payload, digest=digest, size=8),
                b"approved",
            )
            link = root / "link"
            link.symlink_to(payload)
            with self.assertRaises(builder.BuildInputError):
                builder.read_exact_regular(link, digest=digest, size=8)
            payload.chmod(0o644)
            payload.write_bytes(b"replaced")
            with self.assertRaises(builder.BuildInputError):
                builder.read_exact_regular(payload, digest=digest, size=8)

    def test_two_absolute_roots_produce_identical_complete_oci_closures(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory(prefix="oci-root-a-") as a, tempfile.TemporaryDirectory(
            prefix="oci-root-b-"
        ) as b, tempfile.TemporaryDirectory(prefix="oci-out-a-") as oa, tempfile.TemporaryDirectory(
            prefix="oci-out-b-"
        ) as ob:
            root_a, root_b = Path(a), Path(b)
            _write_root(root_a)
            _write_root(root_b)
            request_a = builder.synthetic_test_request(root_a)
            request_b = builder.synthetic_test_request(root_b)
            out_a, out_b = Path(oa).resolve() / "layout", Path(ob).resolve() / "layout"
            first = builder.build_oci_layout(request_a, out_a)
            second = builder.build_oci_layout(request_b, out_b)

            self.assertEqual(first, second)
            self.assertEqual(builder.directory_bytes(out_a), builder.directory_bytes(out_b))
            self.assertEqual(builder.audit_oci_layout(out_a), first)
            archive_a = builder.encode_oci_archive(out_a)
            archive_b = builder.encode_oci_archive(out_b)
            self.assertEqual(archive_a, archive_b)
            self.assertEqual(builder.audit_oci_archive(archive_a), first)
            self.assertEqual(first["layerMediaType"], "application/vnd.oci.image.layer.v1.tar+gzip")
            self.assertNotEqual(first["layerDigest"], first["diffId"])
            entrypoint = builder._layout_rootfs_entries(out_a)[
                "usr/local/libexec/text-to-cad-agent-entrypoint"
            ]
            self.assertEqual(entrypoint[:2], ("regular", 0o555))

    def test_builder_rejects_a_symlink_rootfs(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as output:
            actual = Path(directory) / "actual"
            actual.mkdir()
            _write_root(actual)
            link = Path(directory) / "root"
            link.symlink_to(actual, target_is_directory=True)
            request = builder.synthetic_test_request(actual)._replace(rootfs=link)
            with self.assertRaises(builder.BuildInputError):
                builder.build_oci_layout(request, Path(output).resolve() / "layout")

    def test_builder_rejects_symlink_or_existing_output_without_external_write(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as parent_dir, tempfile.TemporaryDirectory() as external_dir:
            root = Path(root_dir)
            _write_root(root)
            request = builder.synthetic_test_request(root)
            output = Path(parent_dir) / "layout"
            output.symlink_to(Path(external_dir), target_is_directory=True)
            with self.assertRaises(builder.BuildInputError):
                builder.build_oci_layout(request, output)
            self.assertEqual(list(Path(external_dir).iterdir()), [])
            output.unlink()
            output.mkdir()
            with self.assertRaises(builder.BuildInputError):
                builder.build_oci_layout(request, output)

    def test_file_publication_is_exclusive_and_rejects_symlink_ancestors_and_terminal(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as parent_dir, tempfile.TemporaryDirectory() as external_dir:
            parent = Path(parent_dir).resolve()
            external = Path(external_dir).resolve()
            destination = parent / "receipt.json"
            builder.publish_exclusive_file(destination, b"first", 0o444)
            self.assertEqual(destination.read_bytes(), b"first")
            with self.assertRaises(builder.BuildInputError):
                builder.publish_exclusive_file(destination, b"replacement", 0o444)
            self.assertEqual(destination.read_bytes(), b"first")

            terminal_link = parent / "linked.json"
            terminal_link.symlink_to(external / "escaped.json")
            with self.assertRaises(builder.BuildInputError):
                builder.publish_exclusive_file(terminal_link, b"escaped", 0o444)
            self.assertFalse((external / "escaped.json").exists())

            ancestor_link = parent / "linked-parent"
            ancestor_link.symlink_to(external, target_is_directory=True)
            with self.assertRaises(builder.BuildInputError):
                builder.publish_exclusive_file(ancestor_link / "escaped.json", b"escaped", 0o444)
            self.assertFalse((external / "escaped.json").exists())
            self.assertEqual(list(parent.glob(".*.stage-*")), [])

    def test_sealed_rootfs_filters_compiler_package_manager_and_ssh_variants(self) -> None:
        builder = _load_builder()
        forbidden = (
            "usr/bin/apt", "usr/bin/apt-get", "usr/bin/ssh", "usr/bin/ssh-agent",
            "usr/bin/gcc", "usr/bin/g++", "usr/bin/make", "usr/bin/gmake",
            "usr/bin/x86_64-linux-gnu-readelf", "usr/local/bin/clang++",
            "usr/local/bin/cmake", "usr/local/include/header.h",
        )
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root = Path(root_dir)
            _write_root(root)
            for relative in forbidden:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"forbidden")
                path.chmod(0o555)
            alias = root / "usr/local/bin/compiler-alias"
            alias.symlink_to("/usr/bin/gcc")
            output = Path(output_dir).resolve() / "layout"
            record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
            entries = builder._layout_rootfs_entries(output)
            for relative in forbidden:
                self.assertNotIn(relative, entries)
            self.assertNotIn("usr/local/bin/compiler-alias", entries)
            self.assertEqual(builder.audit_oci_layout(output), record)

    def test_auditor_rejects_descriptor_or_uncompressed_diffid_substitution(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root = Path(root_dir)
            output = Path(output_dir).resolve() / "layout"
            _write_root(root)
            record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
            manifest_path = output / "blobs/sha256" / record["manifestDigest"].removeprefix("sha256:")
            manifest = json.loads(manifest_path.read_text(encoding="ascii"))
            manifest["layers"][0]["size"] += 1
            manifest_path.chmod(0o644)
            manifest_path.write_bytes(builder.canonical_json_bytes(manifest))
            with self.assertRaises(builder.OciAuditError):
                builder.audit_oci_layout(output)

    def test_runtime_manifest_rejects_self_or_downstream_hashes(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_root(root)
            manifest = builder.synthetic_test_request(root).runtime_manifest
            builder.validate_runtime_manifest(manifest, root)
            for forbidden in ("runtimeManifestDigest", "sbomDigest", "agentImageManifestDigest"):
                changed = copy.deepcopy(manifest)
                changed[forbidden] = "sha256:" + "0" * 64
                with self.assertRaises(builder.RuntimeManifestError):
                    builder.validate_runtime_manifest(changed, root)

    def test_runtime_manifest_rejects_bool_bounds_modes_and_malformed_observations(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_root(root, marker=b"x")
            manifest = builder.synthetic_test_request(root).runtime_manifest
            cases = []
            for field, invalid in (("bytes", True), ("bytes", -1), ("bytes", 2**63), ("mode", True), ("mode", -1), ("mode", 0o1000)):
                changed = copy.deepcopy(manifest)
                changed["runtimeFiles"][0][field] = invalid
                cases.append(changed)
            for invalid in (b"digest", "sha256:ABC", "sha256:" + "0" * 63):
                changed = copy.deepcopy(manifest)
                changed["runtimeFiles"][0]["digest"] = invalid
                cases.append(changed)
            for field, invalid in (("name", True), ("name", ""), ("version", "v\N{SNOWMAN}")):
                changed = copy.deepcopy(manifest)
                changed["programs"][0][field] = invalid
                cases.append(changed)
            changed = copy.deepcopy(manifest)
            changed["nativeLibraries"] = [{"path": manifest["runtimeFiles"][0]["path"], "soname": True, "digest": manifest["runtimeFiles"][0]["digest"]}]
            cases.append(changed)
            for changed in cases:
                with self.subTest(changed=changed):
                    with self.assertRaises(builder.RuntimeManifestError):
                        builder.validate_runtime_manifest(changed, root)

    def test_external_artifacts_bind_final_manifest_without_image_self_reference(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root, output = Path(root_dir), Path(output_dir).resolve() / "layout"
            _write_root(root)
            extra = root / "etc/fixture"
            extra.parent.mkdir(parents=True)
            extra.write_bytes(b"outside-runtime-manifest")
            record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
            image_bytes = b"".join(builder.directory_bytes(output).values())
            artifacts = builder.produce_external_artifacts(
                output,
                agent_manifest_digest=record["manifestDigest"],
                license_catalog={
                    "schema": "text-to-cad.spdx-license-catalog/1",
                    "licenseListVersion": "3.28.0",
                    "licenses": ["MIT"],
                    "exceptions": [],
                },
                development_test_only=True,
            )
            self.assertEqual(
                artifacts["browserInventory"]["agentImageManifestDigest"],
                record["manifestDigest"],
            )
            self.assertEqual(artifacts["browserInventory"]["findings"], [])
            layer_regular = {
                "/" + path: value
                for path, value in builder._layout_rootfs_entries(output).items()
                if value[0] == "regular"
            }
            spdx_paths = {item["fileName"] for item in artifacts["sbom"]["files"]}
            self.assertEqual(spdx_paths, set(layer_regular))
            self.assertIn("/etc/fixture", spdx_paths)
            for value in artifacts.values():
                artifact_digest = builder.canonical_json_digest(value).encode("ascii")
                self.assertNotIn(artifact_digest, image_bytes)

    def test_browser_lifecycle_helper_is_filtered_and_browser_payload_is_rejected(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root, output = Path(root_dir), Path(output_dir).resolve() / "layout"
            _write_root(root)
            helper = root / "usr/lib/python3.12/webbrowser.py"
            helper.parent.mkdir(parents=True)
            helper.write_bytes(b"#!/usr/bin/python3\n# HeadlessChrome Chromium Google Chrome\n")
            helper.chmod(0o755)
            browser = root / "usr/local/bin/chromium"
            browser.parent.mkdir(parents=True, exist_ok=True)
            browser.write_bytes(b"#!/bin/sh\n# HeadlessChrome\n")
            browser.chmod(0o555)
            record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
            artifacts = builder.produce_external_artifacts(
                output,
                agent_manifest_digest=record["manifestDigest"],
                license_catalog={
                    "schema": "text-to-cad.spdx-license-catalog/1",
                    "licenseListVersion": "3.28.0",
                    "licenses": ["MIT"],
                    "exceptions": [],
                },
                development_test_only=True,
            )
            layer_entries = builder._layout_rootfs_entries(output)
            self.assertNotIn("usr/lib/python3.12/webbrowser.py", layer_entries)
            self.assertEqual(artifacts["browserScanReceipt"]["result"], "rejected")
            self.assertGreater(artifacts["browserScanReceipt"]["browserFindingCount"], 0)

    def test_browser_candidate_root_rejects_empty_or_opaque_closure_but_not_adjacent_name(self) -> None:
        builder = _load_builder()
        for candidate in ("chromium", "ms-playwright"):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
                root, output = Path(root_dir), Path(output_dir).resolve() / "layout"
                _write_root(root)
                browser_root = root / candidate
                browser_root.mkdir()
                if candidate == "chromium":
                    (browser_root / "opaque").write_bytes(b"no markers")
                record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
                artifacts = builder.produce_external_artifacts(
                    output,
                    agent_manifest_digest=record["manifestDigest"],
                    license_catalog={"schema": "text-to-cad.spdx-license-catalog/1", "licenseListVersion": "3.28.0", "licenses": ["MIT"], "exceptions": []},
                    development_test_only=True,
                )
                self.assertEqual(artifacts["browserScanReceipt"]["result"], "rejected")
                self.assertTrue(any(item["matchKind"] == "candidate-root" for item in artifacts["browserInventory"]["findings"]))
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root, output = Path(root_dir), Path(output_dir).resolve() / "layout"
            _write_root(root)
            (root / "chromium-old").mkdir()
            record = builder.build_oci_layout(builder.synthetic_test_request(root), output)
            artifacts = builder.produce_external_artifacts(
                output,
                agent_manifest_digest=record["manifestDigest"],
                license_catalog={"schema": "text-to-cad.spdx-license-catalog/1", "licenseListVersion": "3.28.0", "licenses": ["MIT"], "exceptions": []},
                development_test_only=True,
            )
            self.assertEqual(artifacts["browserScanReceipt"]["result"], "accepted")

    def test_fixed_spdx_wheel_derives_the_exact_12540_byte_catalog(self) -> None:
        builder = _load_builder()
        wheel = Path("/private/tmp/sai005-spdx_license_list-3.28.0-py3-none-any.whl")
        if not wheel.exists():
            self.skipTest("admitted local SPDX wheel is not provisioned")
        catalog = builder.derive_spdx_license_catalog(wheel)
        encoded = builder.canonical_json_bytes(catalog)
        self.assertEqual(len(encoded), 12_540)
        self.assertEqual(builder.canonical_json_digest(catalog), builder.SPDX_CATALOG_DIGEST)
        self.assertEqual(len(catalog["licenses"]), 727)
        self.assertEqual(len(catalog["exceptions"]), 84)

    def test_build_receipt_inputs_bind_integrated_sai003_and_sai004_records(self) -> None:
        builder = _load_builder()
        cli = _load_build_cli()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_root(root)
            manifest = builder.synthetic_test_request(root).runtime_manifest
            bindings = cli._exact_build_inputs(manifest)
        self.assertEqual(
            bindings["qualifiedLocalRecordDigest"],
            "sha256:0ac123449e0042cfa0bcc231d27f3c624aa8c092d1351131768755fc3bb2f766",
        )
        self.assertEqual(
            bindings["localCasLocatorManifestDigest"],
            "sha256:9f068c5b6c3d03eae562a5da5a872abd3370ea1fc1cee46b26c330ce49e60e66",
        )
        self.assertEqual(
            bindings["runtimeOsImageId"],
            "sha256:921e5f6de0f7a8b2fdafff4f5f561fc5a797baa0787e89bf3ec7cfe4fe6cf61c",
        )

    def test_spdx_package_inventory_covers_debs_python_projects_node_and_codex(self) -> None:
        builder = _load_builder()
        cli = _load_build_cli()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_root(root)
            manifest = copy.deepcopy(builder.synthetic_test_request(root).runtime_manifest)
        for path in (
            "/usr/local/lib/python3.12/dist-packages/meshshot/__init__.py",
            "/usr/local/lib/text-to-cad/implicitjs/index.js",
        ):
            manifest["runtimeFiles"].append({"path": path, "mode": 0o444, "bytes": 1, "digest": "sha256:" + "1" * 64})
        packages = cli._exact_spdx_packages(manifest)
        names = {item["name"] for item in packages}
        self.assertEqual(len(packages), 55)
        self.assertTrue({"bash", "numpy", "Pillow", "trimesh", "node", "codex", "meshscope", "meshshot", "text-to-cad-implicit-runtime"} <= names)
        versions = {item["name"]: item["version"] for item in packages}
        self.assertEqual(versions["node"], "24.13.0")
        self.assertEqual(versions["codex"], "0.147.0")


if __name__ == "__main__":
    unittest.main()
