"""Drift guard for the viewer server's deliberate cadgen mirrors.

viewer/server_py must stay importable WITHOUT cadgen/OCP installed, so
scanner.py re-implements the ``__cadgen__`` render-package path helpers and
descriptor constants instead of importing them, and source_hash.py
re-implements the source-closure freshness digest. This test pins the two sides
together: if either renames the directory, the models namespace, a descriptor
filename, changes the package-path shape, or changes how a closure hashes, it
fails here instead of silently splitting the viewer from the build pipeline.

The freshness digest matters most: the viewer and the CLI must agree on whether
a package is stale, or the viewer rebuilds packages the CLI considers current
(or worse, serves ones it considers stale).
"""

from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]

for entry in (str(REPO_ROOT / "viewer"), str(REPO_ROOT / "packages" / "cadgen" / "src")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from cadgen import catalog as cadgen_catalog  # noqa: E402
from server_py import scanner  # noqa: E402


def _module_constants(path: Path, names: set[str]) -> dict[str, object]:
    """Read simple ``NAME = <literal>`` assignments without importing the module
    (component_package/drawing_package pull in OCP/ezdxf at import time)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                found[target.id] = ast.literal_eval(node.value)
    return found


class ViewerCadgenMirrorTest(unittest.TestCase):
    def test_cadgen_dirname_constants_match(self) -> None:
        self.assertEqual(scanner.CADGEN_DIRNAME, cadgen_catalog.CADGEN_DIRNAME)
        self.assertEqual(scanner.CADGEN_MODELS_DIRNAME, cadgen_catalog.CADGEN_MODELS_DIRNAME)

    def test_render_package_dir_shapes_match(self) -> None:
        entry = REPO_ROOT / "models" / "sample" / "part.step"
        expected = cadgen_catalog.render_package_dir(entry)
        actual = Path(scanner.render_package_dir(str(entry))).resolve()
        self.assertEqual(expected, actual)
        # Round trip: the scanner must map the package dir back to the entry file.
        self.assertEqual(
            str(entry),
            scanner.entry_path_for_render_package(scanner.render_package_dir(str(entry))),
        )

    def test_step_descriptor_constants_match(self) -> None:
        constants = _module_constants(
            REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen" / "_internal" / "component_package.py",
            {"DESCRIPTOR_NAME", "PACKAGE_KIND"},
        )
        self.assertEqual(constants.get("DESCRIPTOR_NAME"), "assembly.json")
        # scanner.py reads assembly.json literally in read_step_catalog_metadata /
        # _package_descriptor_stats; pin the emit side to the same name.
        self.assertEqual(constants.get("PACKAGE_KIND"), "assembly-package")

    def test_drawing_descriptor_constants_match(self) -> None:
        constants = _module_constants(
            REPO_ROOT / "packages" / "cadgen" / "src" / "cadgen" / "_internal" / "drawing_package.py",
            {"DRAWING_DESCRIPTOR_NAME", "DRAWING_PACKAGE_KIND"},
        )
        self.assertEqual(constants.get("DRAWING_DESCRIPTOR_NAME"), scanner.DRAWING_DESCRIPTOR_NAME)
        self.assertEqual(constants.get("DRAWING_PACKAGE_KIND"), scanner.DRAWING_PACKAGE_KIND)

    def test_generation_lock_paths_match(self) -> None:
        # cadgen holds the lock (generation_status.generation_lock_path); the viewer
        # probes it (server_py.artifact.generation_lock_path). Same package dir must
        # yield the same sentinel file.
        from cadgen._internal.generation_status import generation_lock_path as write_side
        from server_py.artifact import generation_lock_path as read_side

        package_dir = REPO_ROOT / "models" / "sample" / "__cadgen__" / "models" / "part.step"
        self.assertEqual(str(write_side(package_dir)), read_side(str(package_dir)))

    def test_generation_lock_is_honoured_across_the_mirror(self) -> None:
        """cadgen's holder and the viewer's probe must agree on real lock state."""
        from cadgen._internal.generation_status import generation_lock_path as write_side
        from cadgen._internal.generation_status import track_generation_run
        from server_py.artifact import generation_lock_active

        with tempfile.TemporaryDirectory() as tmp:
            package_dir = Path(tmp) / "__cadgen__" / "models" / "part.step"
            lock = write_side(package_dir)
            self.assertFalse(generation_lock_active(str(lock)))
            with track_generation_run(lock):
                self.assertTrue(
                    generation_lock_active(str(lock)),
                    "the viewer must see a build cadgen is running",
                )
            self.assertFalse(
                generation_lock_active(str(lock)),
                "the viewer must see the build finish",
            )


# The corpus the two hash implementations must agree on. Comment/whitespace/docstring
# cases pin comment-insensitivity; the broken and non-Python cases pin the byte-hash
# fallbacks, which is where a naive mirror diverges.
_HASH_CORPUS = {
    "plain.py": "def gen_step():\n    return 1\n",
    "commented.py": "# leading\ndef gen_step():\n    return 1  # trailing\n",
    "blank_lines.py": "\n\n\ndef gen_step():\n\n    return 1\n",
    "reindented.py": "def gen_step():\n\treturn 1\n",
    "docstring.py": 'def gen_step():\n    """doc"""\n    return 1\n',
    "different.py": "def gen_step():\n    return 2\n",
    "syntax_error.py": "def gen_step(:\n",
    "empty.py": "",
    "child.step": "ISO-10303-21;\nDATA;\nENDSEC;\n",
    "binary.glb": "glTF\x02\x00\x00\x00",
}


class ClosureHashMirrorTest(unittest.TestCase):
    """cadgen decides "is current; skipped recompose"; the viewer decides ready vs
    needs-build. Both must compute the SAME source-closure digest."""

    def setUp(self) -> None:
        from cadgen._internal import source_hash as cadgen_hash
        from server_py import source_hash as viewer_hash

        self.cadgen_hash = cadgen_hash
        self.viewer_hash = viewer_hash
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        for name, body in _HASH_CORPUS.items():
            (self.root / name).write_text(body, encoding="utf-8")

    def _write(self, name: str, body: str) -> None:
        (self.root / name).write_text(body, encoding="utf-8")

    def test_per_file_hashes_match(self) -> None:
        for name in _HASH_CORPUS:
            path = self.root / name
            self.assertEqual(
                self.cadgen_hash._semantic_source_hash(path),
                self.viewer_hash.semantic_source_hash(str(path)),
                f"per-file digest drift on {name}",
            )

    def test_comment_insensitivity_agrees(self) -> None:
        # Same AST, different bytes -> one digest, on both sides.
        digests = set()
        for name in ("plain.py", "commented.py", "blank_lines.py", "reindented.py"):
            path = self.root / name
            digests.add(self.cadgen_hash._semantic_source_hash(path))
            digests.add(self.viewer_hash.semantic_source_hash(str(path)))
        self.assertEqual(1, len(digests), "comment/format-only edits must not change the digest")
        # A real semantic change must break out of that set, on both sides.
        different = self.root / "different.py"
        self.assertNotIn(self.cadgen_hash._semantic_source_hash(different), digests)
        self.assertNotIn(self.viewer_hash.semantic_source_hash(str(different)), digests)

    def test_closure_hash_match_verdicts_agree(self) -> None:
        from cadgen._internal.source_hash import closure_for_files

        names = sorted(_HASH_CORPUS)
        closure = closure_for_files(
            self.root / "plain.py", [self.root / n for n in names], base=self.root
        )
        self.assertEqual(tuple(names), closure.files)

        def verdicts():
            return (
                self.cadgen_hash.closure_hash_matches(
                    closure.closure_hash, closure.files, base=self.root
                ),
                self.viewer_hash.closure_hash_matches(
                    closure.closure_hash, closure.files, str(self.root)
                ),
            )

        self.assertEqual((True, True), verdicts(), "a freshly recorded closure must match")

        self._write("plain.py", "# only a comment changed\ndef gen_step():\n    return 1\n")
        self.assertEqual((True, True), verdicts(), "a comment-only edit must stay current")

        self._write("plain.py", "def gen_step():\n    return 99\n")
        self.assertEqual((False, False), verdicts(), "a semantic edit must go stale")

        self._write("plain.py", _HASH_CORPUS["plain.py"])
        self.assertEqual((True, True), verdicts(), "restoring the source must restore currency")

        (self.root / "child.step").write_text("ISO-10303-21;\nCHANGED;\n", encoding="utf-8")
        self.assertEqual((False, False), verdicts(), "a non-Python closure input must go stale")

    def test_missing_closure_file_verdicts_agree(self) -> None:
        from cadgen._internal.source_hash import closure_for_files

        names = ["plain.py", "child.step"]
        closure = closure_for_files(
            self.root / "plain.py", [self.root / n for n in names], base=self.root
        )
        (self.root / "child.step").unlink()
        self.assertFalse(
            self.cadgen_hash.closure_hash_matches(closure.closure_hash, closure.files, base=self.root)
        )
        self.assertFalse(
            self.viewer_hash.closure_hash_matches(closure.closure_hash, closure.files, str(self.root))
        )

    def test_empty_and_missing_recorded_hash_agree(self) -> None:
        for recorded in ("", None, "   ", "not-a-real-digest"):
            self.assertFalse(
                self.cadgen_hash.closure_hash_matches(recorded, ["plain.py"], base=self.root)
            )
            self.assertFalse(
                self.viewer_hash.closure_hash_matches(recorded, ["plain.py"], str(self.root))
            )

    def test_legacy_byte_recorded_closure_still_validates_on_both_sides(self) -> None:
        """Descriptors written before comment-insensitive hashing recorded a byte
        digest; both sides must keep accepting them until the next real rebuild."""
        names = ["plain.py", "child.step"]
        pairs = [(n, self.cadgen_hash._sha256_file(self.root / n)) for n in names]
        legacy = self.cadgen_hash._closure_hash_for_pairs(pairs)
        self.assertTrue(
            self.cadgen_hash.closure_hash_matches(legacy, names, base=self.root)
        )
        self.assertTrue(
            self.viewer_hash.closure_hash_matches(legacy, names, str(self.root))
        )


if __name__ == "__main__":
    unittest.main()
