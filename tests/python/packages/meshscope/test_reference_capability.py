from __future__ import annotations

import builtins
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import trimesh

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshscope/src")

from meshscope import (  # noqa: E402
    ReferenceCapability,
    ReferenceCapabilityError,
)
from meshscope.voxblame.prepare_reference import prepare_reference  # noqa: E402
from meshscope.reference_capability import (  # noqa: E402
    COMPONENTS_SCHEMA,
    COORDINATE_CONTRACT,
    DEFAULT_COMPONENT_LIMIT,
    MAX_COMPONENT_LIMIT,
    MAX_REFERENCE_BYTES,
    MAX_RESPONSE_BYTES,
    PLY_HEADER_MAX_BYTES,
    PLY_HEADER_MAX_LINE_BYTES,
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    SUMMARY_SCHEMA,
)


def _cube() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(0.8, 0.8, 0.8))


def _disconnected_triangles(count: int) -> trimesh.Trimesh:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for index in range(count):
        start = len(vertices)
        spacing = 0.98 / count
        x = -0.49 + spacing * index
        vertices.extend(
            [
                [x, -0.1, 0.0],
                [x + spacing * 0.25, -0.1, 0.0],
                [x, -0.1 + spacing * 0.25, 0.0],
            ]
        )
        faces.append([start, start + 1, start + 2])
    return trimesh.Trimesh(vertices=vertices, faces=faces, process=False)


class ReferenceCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="meshscope-capability-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.reference = self.root / "reference.ply"
        _cube().export(self.reference)
        self.capability = ReferenceCapability("job-ref-7", self.reference)

    @staticmethod
    def request(method: str, args: dict | None = None, reference_id: str = "job-ref-7") -> dict:
        return {
            "schema": REQUEST_SCHEMA,
            "reference_id": reference_id,
            "method": method,
            "args": {} if args is None else args,
        }

    def assert_error(self, callback, classification: str) -> None:
        with self.assertRaises(ReferenceCapabilityError) as raised:
            callback()
        self.assertEqual(classification, raised.exception.classification)
        self.assertEqual(classification, str(raised.exception))
        self.assertNotIn(str(self.root), str(raised.exception))

    def test_summary_is_closed_bounded_and_path_free(self) -> None:
        result = self.capability.handle(self.request("summary"))

        self.assertEqual(
            {"schema", "reference_id", "method", "observation"},
            set(result),
        )
        self.assertEqual(RESPONSE_SCHEMA, result["schema"])
        self.assertEqual("job-ref-7", result["reference_id"])
        self.assertEqual("summary", result["method"])
        observation = result["observation"]
        self.assertEqual(SUMMARY_SCHEMA, observation["schema"])
        self.assertEqual(COORDINATE_CONTRACT, observation["coordinate_contract"])
        self.assertEqual(
            {"vertices", "faces", "edges", "bounds", "surface_area", "volume"},
            set(observation["stats"]),
        )
        self.assertEqual(
            {"watertight", "volume_valid", "degenerate_faces", "euler_number"},
            set(observation["quality"]),
        )
        self.assertEqual(
            {"center", "status", "pca_axes", "eigenvalues"},
            set(observation["canonical_frame"]),
        )
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        self.assertLessEqual(len(encoded.encode("ascii")), MAX_RESPONSE_BYTES)
        for forbidden in (str(self.reference), "raw_bytes", "export", "to_trimesh"):
            self.assertNotIn(forbidden, encoded)

    def test_components_are_fixed_and_bounded(self) -> None:
        disconnected = self.root / "disconnected.ply"
        _disconnected_triangles(MAX_COMPONENT_LIMIT + 8).export(disconnected)
        capability = ReferenceCapability("job-ref-many", disconnected)

        result = capability.handle(
            self.request("components", {"limit": MAX_COMPONENT_LIMIT}, "job-ref-many")
        )
        observation = result["observation"]
        self.assertEqual(COMPONENTS_SCHEMA, observation["schema"])
        self.assertEqual(MAX_COMPONENT_LIMIT + 8, observation["total"])
        self.assertEqual(MAX_COMPONENT_LIMIT, observation["returned"])
        self.assertEqual(8, observation["omitted"])
        self.assertEqual(MAX_COMPONENT_LIMIT, len(observation["components"]))
        self.assertEqual(
            list(range(1, MAX_COMPONENT_LIMIT + 1)),
            [row["rank"] for row in observation["components"]],
        )
        self.assertTrue(
            all(
                set(row) == {"rank", "vertices", "faces", "bounds", "centroid"}
                for row in observation["components"]
            )
        )

    def test_noncanonical_and_overlarge_materials_fail_before_observation_work(self) -> None:
        outside = _cube().copy()
        outside.apply_translation([0.3, 0.0, 0.0])
        outside_path = self.root / "outside.ply"
        outside.export(outside_path)
        self.assert_error(
            lambda: ReferenceCapability("outside", outside_path),
            "noncanonical_reference",
        )

        too_many = self.root / "too-many-components.ply"
        too_many.write_bytes(
            (
                "ply\n"
                "format ascii 1.0\n"
                "element vertex 3\n"
                "property float x\n"
                "property float y\n"
                "property float z\n"
                f"element face {MAX_REFERENCE_BYTES}\n"
                "property list uchar int vertex_indices\n"
                "end_header\n"
            ).encode("ascii")
        )
        self.assert_error(
            lambda: ReferenceCapability("too-many", too_many),
            "reference_too_complex",
        )

    def test_pca_marks_repeated_eigenvalues_ambiguous_and_reindex_is_stable(self) -> None:
        sphere = trimesh.creation.icosphere(subdivisions=1, radius=0.4)
        sphere_reindexed = trimesh.Trimesh(
            vertices=sphere.vertices[::-1],
            faces=sphere.faces[::-1, ::-1],
            process=False,
        )
        frames = []
        for index, mesh in enumerate((sphere, sphere_reindexed)):
            path = self.root / f"sphere-{index}.ply"
            mesh.export(path)
            frame = self._summary_frame(ReferenceCapability("sphere", path), "sphere")
            frames.append(frame)
        self.assertEqual("ambiguous", frames[0]["status"])
        self.assertIsNone(frames[0]["pca_axes"])
        self.assertEqual(frames[0], frames[1])

        box = trimesh.creation.box(extents=(0.8, 0.7, 0.6))
        box_reindexed = trimesh.Trimesh(
            vertices=box.vertices[::-1],
            faces=len(box.vertices) - 1 - box.faces[::-1, ::-1],
            process=False,
        )
        frames = []
        for index, mesh in enumerate((box, box_reindexed)):
            path = self.root / f"box-{index}.ply"
            mesh.export(path)
            frames.append(self._summary_frame(ReferenceCapability("box", path), "box"))
        self.assertEqual("stable", frames[0]["status"])
        self.assertEqual(frames[0], frames[1])

    @staticmethod
    def _summary_frame(capability: ReferenceCapability, reference_id: str) -> dict:
        return capability.handle(
            {
                "schema": REQUEST_SCHEMA,
                "reference_id": reference_id,
                "method": "summary",
                "args": {},
            }
        )["observation"]["canonical_frame"]

    def test_repeated_calls_and_fresh_capability_are_deterministic(self) -> None:
        request = self.request("summary")
        first = self.capability.handle(request)
        self.assertEqual(first, self.capability.handle(request))
        fresh = ReferenceCapability("job-ref-7", self.reference)
        self.assertEqual(first, fresh.handle(request))

        first["observation"]["stats"]["vertices"] = -1
        self.assertGreater(
            self.capability.handle(request)["observation"]["stats"]["vertices"],
            0,
        )

    def test_request_schema_is_closed_and_ids_are_opaque(self) -> None:
        extra = self.request("summary")
        extra["unexpected"] = True
        self.assert_error(lambda: self.capability.handle(extra), "invalid_request")
        self.assert_error(
            lambda: self.capability.handle({**self.request("summary"), "schema": "other/1"}),
            "invalid_request",
        )
        self.assert_error(
            lambda: self.capability.handle(self.request("summary", reference_id="other")),
            "invalid_reference",
        )
        self.assert_error(
            lambda: self.capability.handle(self.request("summary", {"detail": True})),
            "invalid_request",
        )
        self.assert_error(
            lambda: self.capability.handle(self.request("components", {"limit": True})),
            "invalid_request",
        )
        self.assert_error(
            lambda: self.capability.handle(None),
            "invalid_request",
        )
        wrong_types = self.request("summary")
        wrong_types["method"] = ["summary"]
        self.assert_error(lambda: self.capability.handle(wrong_types), "invalid_request")
        wrong_types = self.request("summary")
        wrong_types["args"] = []
        self.assert_error(lambda: self.capability.handle(wrong_types), "invalid_request")
        self.assert_error(
            lambda: self.capability.handle(
                self.request("components", {"limit": MAX_COMPONENT_LIMIT + 1})
            ),
            "invalid_request",
        )
        self.assertEqual(
            DEFAULT_COMPONENT_LIMIT,
            self.capability.handle(self.request("components"))["observation"]["limit"],
        )

    def test_unknown_and_prohibited_operations_fail_closed(self) -> None:
        self.assert_error(lambda: self.capability.handle(self.request("unknown")), "unknown_method")
        self.assert_error(lambda: self.capability.handle(self.request("slices")), "unknown_method")
        for method in (
            "vertices",
            "faces",
            "raw_bytes",
            "export",
            "raycast",
            "nearest_point",
            "slice_plane",
            "fine_occupancy_query",
            "roi",
        ):
            with self.subTest(method=method):
                self.assert_error(
                    lambda method=method: self.capability.handle(self.request(method)),
                    "unsupported_operation",
                )

    def test_material_errors_are_stable_and_reject_symlink_paths(self) -> None:
        broken = self.root / "broken.ply"
        broken.write_text("not a mesh", encoding="ascii")
        self.assert_error(
            lambda: ReferenceCapability("job-broken", broken),
            "invalid_reference_material",
        )
        link = self.root / "reference-link.ply"
        try:
            link.symlink_to(self.reference)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable")
        self.assert_error(
            lambda: ReferenceCapability("job-link", link),
            "invalid_reference_material",
        )
        self.assert_error(
            lambda: ReferenceCapability("bad/id", self.reference),
            "invalid_reference",
        )
        oversized = self.root / "oversized.ply"
        with oversized.open("wb") as handle:
            handle.truncate(MAX_REFERENCE_BYTES + 1)
        self.assert_error(
            lambda: ReferenceCapability("job-large", oversized),
            "invalid_reference_material",
        )
        self.assert_error(
            lambda: ReferenceCapability("job-escape", self.root / ".." / self.reference.name),
            "invalid_reference_material",
        )

    def test_texture_comments_never_resolve_external_files(self) -> None:
        outside = self.root.parent / "outside.png"
        outside.write_bytes(b"do-not-open")
        source = self.reference.read_bytes()
        marker = b"end_header\n"
        self.assertIn(marker, source)
        textured = self.root / "texture-comment.ply"
        textured.write_bytes(
            source.replace(
                marker,
                b"comment TextureFile ../outside.png\n" + marker,
                1,
            )
        )

        real_open = builtins.open

        def guarded_open(file, *args, **kwargs):
            if str(outside) in str(file):
                raise AssertionError("external texture was opened")
            return real_open(file, *args, **kwargs)

        with (
            mock.patch("builtins.open", side_effect=guarded_open),
            mock.patch(
                "meshscope.reference_capability.trimesh.load",
                wraps=trimesh.load,
            ) as loader,
        ):
            capability = ReferenceCapability("texture", textured)
            result = capability.handle(self.request("summary", reference_id="texture"))
        self.assertEqual(SUMMARY_SCHEMA, result["observation"]["schema"])
        self.assertEqual(b"do-not-open", outside.read_bytes())
        self.assertEqual("ply", loader.call_args.kwargs["file_type"])
        self.assertTrue(loader.call_args.kwargs["skip_materials"])
        self.assertFalse(isinstance(loader.call_args.args[0], (str, Path)))

    def test_header_counts_are_rejected_before_trimesh(self) -> None:
        def header(format_name: str, vertex_count: str, face_count: str) -> bytes:
            return (
                "ply\n"
                f"format {format_name}\n"
                f"element vertex {vertex_count}\n"
                "property float x\n"
                "property float y\n"
                "property float z\n"
                f"element face {face_count}\n"
                "property list uchar int vertex_indices\n"
                "end_header\n"
            ).encode("ascii")

        for format_name in ("ascii 1.0", "binary_little_endian 1.0"):
            for name, data in (
                (
                    f"{format_name.split()[0]}-too-many-vertices",
                    header(format_name, str(MAX_REFERENCE_BYTES), "1"),
                ),
                (
                    f"{format_name.split()[0]}-too-many-faces",
                    header(format_name, "3", str(MAX_REFERENCE_BYTES)),
                ),
            ):
                path = self.root / f"{name}.ply"
                path.write_bytes(data)
                with self.subTest(name=name), mock.patch(
                    "meshscope.reference_capability.trimesh.load",
                    side_effect=AssertionError("PLY parser was reached"),
                ) as loader:
                    self.assert_error(
                        lambda path=path: ReferenceCapability(name, path),
                        "reference_too_complex",
                    )
                    loader.assert_not_called()

    def test_bounded_malformed_headers_fail_before_trimesh(self) -> None:
        valid_prefix = (
            b"ply\nformat ascii 1.0\nelement vertex 3\n"
            b"property float x\nproperty float y\nproperty float z\n"
            b"element face 1\nproperty list uchar int vertex_indices\n"
        )
        cases = {
            "duplicate-element": valid_prefix
            + b"element vertex 3\nend_header\n",
            "unterminated": valid_prefix + b"end_header",
            "count-trick": valid_prefix.replace(b"element vertex 3", b"element vertex +3")
            + b"end_header\n",
            "oversized-line": valid_prefix
            + b"comment "
            + b"x" * PLY_HEADER_MAX_LINE_BYTES
            + b"\nend_header\n",
            "oversized-header": valid_prefix
            + (b"comment " + b"x" * 4000 + b"\n") * 20
            + b"end_header\n",
        }
        for name, data in cases.items():
            path = self.root / f"{name}.ply"
            path.write_bytes(data)
            with self.subTest(name=name), mock.patch(
                "meshscope.reference_capability.trimesh.load",
                side_effect=AssertionError("PLY parser was reached"),
            ) as loader:
                self.assert_error(
                    lambda path=path: ReferenceCapability(name, path),
                    "invalid_reference_material",
                )
                loader.assert_not_called()
        self.assertLessEqual(PLY_HEADER_MAX_BYTES, 64 * 1024)
        self.assertLessEqual(PLY_HEADER_MAX_LINE_BYTES, PLY_HEADER_MAX_BYTES)

    def test_only_self_contained_ply_is_loaded(self) -> None:
        outside = self.root.parent / "outside.bin"
        outside.write_bytes(b"do-not-read")
        tiny_gltf = self.root / "tiny.gltf"
        tiny_gltf.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [{"uri": "../outside.bin", "byteLength": 11}],
                }
            ),
            encoding="ascii",
        )
        with mock.patch(
            "meshscope.reference_capability.trimesh.load",
            side_effect=AssertionError("non-PLY loader path reached"),
        ) as loader:
            self.assert_error(
                lambda: ReferenceCapability("gltf", tiny_gltf),
                "invalid_reference_material",
            )
            loader.assert_not_called()
        self.assertEqual(b"do-not-read", outside.read_bytes())

        for suffix in (".glb", ".obj", ".stl"):
            unsupported = self.root / f"unsupported{suffix}"
            unsupported.write_bytes(b"not an accepted reference")
            with self.subTest(suffix=suffix), mock.patch(
                "meshscope.reference_capability.trimesh.load",
                side_effect=AssertionError("unsupported loader path reached"),
            ) as loader:
                self.assert_error(
                    lambda unsupported=unsupported: ReferenceCapability(
                        "unsupported", unsupported
                    ),
                    "invalid_reference_material",
                )
                loader.assert_not_called()

        oversized_external = self.root / "oversized-external.gltf"
        external = self.root / "external.bin"
        external.write_bytes(b"external-marker")
        oversized_external.write_text(
            json.dumps(
                {
                    "asset": {"version": "2.0"},
                    "buffers": [
                        {
                            "uri": "external.bin",
                            "byteLength": MAX_REFERENCE_BYTES + 1,
                        }
                    ],
                }
            ),
            encoding="ascii",
        )
        with mock.patch(
            "meshscope.reference_capability.trimesh.load",
            side_effect=AssertionError("external-resource loader path reached"),
        ) as loader:
            self.assert_error(
                lambda: ReferenceCapability("external", oversized_external),
                "invalid_reference_material",
            )
            loader.assert_not_called()
        self.assertEqual(b"external-marker", external.read_bytes())

    def test_prepare_reference_thin_nonzero_triangle_is_compatible(self) -> None:
        source = self.root / "thin-source.ply"
        trimesh.Trimesh(
            vertices=[[-0.5, 0.0, 0.0], [0.5, 0.0, 0.0], [-0.5, 1e-12, 0.0]],
            faces=[[0, 1, 2]],
            process=False,
        ).export(source)
        published = self.root / "canonical-reference"
        prepare_reference(source, published)
        capability = ReferenceCapability("thin", published / "reference.ply")
        result = capability.handle(self.request("summary", reference_id="thin"))
        self.assertEqual(1, result["observation"]["stats"]["faces"])


if __name__ == "__main__":
    unittest.main()
