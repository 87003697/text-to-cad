import tempfile
import unittest
from pathlib import Path

from cadgen._internal import source_hash


def _closure(script: Path, base: Path):
    return source_hash.closure_for_files(script, [], base=base)


class SemanticClosureHashTests(unittest.TestCase):
    def _write(self, root: Path, name: str, text: str) -> Path:
        path = root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_comment_and_whitespace_edits_do_not_change_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "gen.py", "def gen_step():\n    return build(radius=5)\n")
            hash_a = _closure(a, root).closure_hash
            # add a comment, blank lines, and reindent-free formatting
            a.write_text(
                "# a new comment\n\n"
                "def gen_step():\n"
                "    return build(radius=5)   # trailing comment\n\n\n",
                encoding="utf-8",
            )
            self.assertEqual(hash_a, _closure(a, root).closure_hash)

    def test_docstring_change_changes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "gen.py", 'def gen_step():\n    "one"\n    return 1\n')
            hash_a = _closure(a, root).closure_hash
            a.write_text('def gen_step():\n    "two"\n    return 1\n', encoding="utf-8")
            self.assertNotEqual(hash_a, _closure(a, root).closure_hash)

    def test_real_code_change_changes_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "gen.py", "R = 5\ndef gen_step():\n    return R\n")
            hash_a = _closure(a, root).closure_hash
            a.write_text("R = 6\ndef gen_step():\n    return R\n", encoding="utf-8")
            self.assertNotEqual(hash_a, _closure(a, root).closure_hash)

    def test_syntax_error_falls_back_to_byte_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "broken.py", "def gen_step(:\n    return 1\n")
            hash_a = _closure(a, root).closure_hash
            # a comment edit on an unparseable file DOES change the (byte) hash
            a.write_text("# c\ndef gen_step(:\n    return 1\n", encoding="utf-8")
            self.assertNotEqual(hash_a, _closure(a, root).closure_hash)

    def test_matches_accepts_legacy_byte_recorded_hash(self) -> None:
        # Migration: a descriptor recorded with the old byte-based digest must
        # still validate as unchanged so no mass rebuild happens on upgrade.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "gen.py", "def gen_step():\n    return 1\n")
            legacy = source_hash._recompute_closure_hash(
                ["gen.py"], base=root, hasher=source_hash._sha256_file
            )
            self.assertTrue(source_hash.closure_hash_matches(legacy, ["gen.py"], base=root))

    def test_matches_accepts_semantic_hash_after_comment_edit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            a = self._write(root, "gen.py", "def gen_step():\n    return 1\n")
            semantic = _closure(a, root).closure_hash
            a.write_text("# comment\ndef gen_step():\n    return 1\n", encoding="utf-8")
            self.assertTrue(source_hash.closure_hash_matches(semantic, ["gen.py"], base=root))

    def test_matches_rejects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertFalse(source_hash.closure_hash_matches("abc", ["gone.py"], base=root))


class RuntimeRootsStdinFootgunTests(unittest.TestCase):
    def test_placeholder_main_file_does_not_mark_cwd_as_runtime(self) -> None:
        # A stdin / `-c` driven build sets __main__.__file__ to '<stdin>', whose
        # resolve().parent is the CWD; that must NOT be treated as a runtime root
        # (which would exclude the model + its sibling helpers from the closure
        # and silently disable staleness detection).
        import sys

        main = sys.modules["__main__"]
        original = getattr(main, "__file__", None)
        source_hash._runtime_roots.cache_clear()
        try:
            main.__file__ = "<stdin>"
            source_hash._runtime_roots.cache_clear()
            roots = source_hash._runtime_roots()
            self.assertNotIn(Path.cwd().resolve(), roots)
        finally:
            if original is None:
                if hasattr(main, "__file__"):
                    del main.__file__
            else:
                main.__file__ = original
            source_hash._runtime_roots.cache_clear()

    def test_real_launcher_file_is_a_runtime_root(self) -> None:
        import sys

        main = sys.modules["__main__"]
        original = getattr(main, "__file__", None)
        with tempfile.TemporaryDirectory() as tmp:
            launcher = Path(tmp) / "__main__.py"
            launcher.write_text("# launcher\n", encoding="utf-8")
            source_hash._runtime_roots.cache_clear()
            try:
                main.__file__ = str(launcher)
                source_hash._runtime_roots.cache_clear()
                roots = source_hash._runtime_roots()
                self.assertIn(launcher.parent.resolve(), roots)
            finally:
                if original is None:
                    if hasattr(main, "__file__"):
                        del main.__file__
                else:
                    main.__file__ = original
                source_hash._runtime_roots.cache_clear()


if __name__ == "__main__":
    unittest.main()
