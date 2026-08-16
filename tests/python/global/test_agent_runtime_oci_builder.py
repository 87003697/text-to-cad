from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
BUILDER = REPO_ROOT / "packages" / "agent_runtime" / "oci_builder.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("agent_runtime_oci_builder", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("builder module is unavailable")
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
            first = builder.build_oci_layout(request_a, Path(oa))
            second = builder.build_oci_layout(request_b, Path(ob))

            self.assertEqual(first, second)
            self.assertEqual(builder.directory_bytes(Path(oa)), builder.directory_bytes(Path(ob)))
            self.assertEqual(builder.audit_oci_layout(Path(oa)), first)
            self.assertEqual(first["layerMediaType"], "application/vnd.oci.image.layer.v1.tar+gzip")
            self.assertNotEqual(first["layerDigest"], first["diffId"])

    def test_auditor_rejects_descriptor_or_uncompressed_diffid_substitution(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root = Path(root_dir)
            output = Path(output_dir)
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

    def test_external_artifacts_bind_final_manifest_without_image_self_reference(self) -> None:
        builder = _load_builder()
        with tempfile.TemporaryDirectory() as root_dir, tempfile.TemporaryDirectory() as output_dir:
            root, output = Path(root_dir), Path(output_dir)
            _write_root(root)
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
            for value in artifacts.values():
                artifact_digest = builder.canonical_json_digest(value).encode("ascii")
                self.assertNotIn(artifact_digest, image_bytes)

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


if __name__ == "__main__":
    unittest.main()
