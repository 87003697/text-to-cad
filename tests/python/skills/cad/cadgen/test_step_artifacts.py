import tempfile
import unittest
from pathlib import Path

from cadgen import step_artifacts
from cadgen._internal import generation
from cadgen.step_targets import ResolvedStepTarget


class StepArtifactsTests(unittest.TestCase):
    def test_existing_step_target_ignores_python_source_for_glb_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            source_path = root / "part.py"
            step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
            source_path.write_text("def gen_step():\n    return None\n", encoding="utf-8")

            target = ResolvedStepTarget(
                cad_path="part",
                kind="part",
                source_path=source_path,
                step_path=step_path,
            )
            self.assertIsNone(step_artifacts._python_source_for_target(target))

    def test_explicit_python_target_wins_over_same_stem_step_export(self) -> None:
        # A `<name>.step.py` generator explicitly targeted by the caller keeps
        # resolving to the generator even when its same-stem exported
        # `<name>.step` exists beside it (entry-keyed coexistence).
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            source_path = root / "part.step.py"
            step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
            source_path.write_text("def gen_step():\n    return None\n", encoding="utf-8")

            target = ResolvedStepTarget(
                cad_path="part",
                kind="part",
                source_path=source_path,
                step_path=step_path,
                explicit_python=True,
            )
            self.assertEqual(step_artifacts._python_source_for_target(target), source_path)

    def test_non_explicit_step_target_with_same_stem_export_stays_imported(self) -> None:
        # Without an explicit .py target, an existing .step file keeps its
        # documented direct-import semantics even when a generator exists.
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            source_path = root / "part.step.py"
            step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
            source_path.write_text("def gen_step():\n    return None\n", encoding="utf-8")

            target = ResolvedStepTarget(
                cad_path="part",
                kind="part",
                source_path=source_path,
                step_path=step_path,
            )
            self.assertIsNone(step_artifacts._python_source_for_target(target))

    def test_missing_logical_step_finds_step_py_convention_generator(self) -> None:
        # The generator for `<name>.step` is `<name>.step.py` under the entry
        # convention; the fallback must find it when no .step file exists.
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            generator_path = root / "part.step.py"
            generator_path.write_text("def gen_step():\n    return None\n", encoding="utf-8")

            target = ResolvedStepTarget(
                cad_path="part",
                kind="part",
                source_path=step_path,
                step_path=step_path,
            )
            self.assertEqual(step_artifacts._python_source_for_target(target), generator_path)

    def test_missing_logical_step_can_still_use_python_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            source_path = root / "part.py"
            source_path.write_text("def gen_step():\n    return None\n", encoding="utf-8")

            target = ResolvedStepTarget(
                cad_path="part",
                kind="part",
                source_path=step_path,
                step_path=step_path,
            )
            self.assertEqual(step_artifacts._python_source_for_target(target), source_path)

    def test_existing_step_spec_can_reuse_python_backed_glb_when_step_hash_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            step_path = root / "part.step"
            step_path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")

            spec = generation.EntrySpec(
                source_ref="part.step",
                cad_ref="part",
                kind="part",
                source_path=step_path,
                display_name="part",
                source="imported",
                step_path=step_path,
            )
            self.assertFalse(generation._artifact_source_kind_matches_spec(spec, {"sourceKind": "python"}))
            self.assertTrue(
                generation._artifact_source_kind_matches_spec(
                    spec,
                    {
                        "sourceKind": "python",
                        "stepHash": generation.step_file_hash(step_path),
                    },
                )
            )


if __name__ == "__main__":
    unittest.main()
