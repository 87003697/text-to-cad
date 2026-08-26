from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import agent_source_projection as projection
from tests.python.support.paths import REPO_ROOT


def _stage_source(root: Path) -> None:
    for source, _ in projection.SOURCE_MAPPINGS:
        destination = root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / source).read_bytes())


class AgentSourceProjectionTests(unittest.TestCase):
    def _bundle(self, temporary: str) -> tuple[Path, Path]:
        root = Path(temporary) / "repo"
        _stage_source(root)
        target = root / projection.PROJECTION_ROOT_REL
        projection.bundle(root, target)
        return root, target

    def test_bundle_and_checked_in_projection_are_fresh_and_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, target = self._bundle(temporary)
            inventory = projection.check_bundle(root, target)
            self.assertEqual(projection.PROJECTED_PATHS, tuple(e.path for e in inventory.entries))
        checked_in = REPO_ROOT / projection.PROJECTION_ROOT_REL
        projection.check_bundle(REPO_ROOT, checked_in)
        files: set[str] = set()
        for parent, _, names in os.walk(checked_in):
            for name in names:
                path = Path(parent) / name
                self.assertFalse(path.is_symlink())
                files.add(path.relative_to(checked_in).as_posix())
        self.assertEqual({projection.MANIFEST_NAME, *projection.PROJECTED_PATHS}, files)

    def test_projected_skill_carries_agent_authored_document_contracts(self) -> None:
        skill = (
            REPO_ROOT
            / ".claude/agent-source-projection/skills/mesh-to-cad/SKILL.md"
        ).read_text(encoding="utf-8")
        for required in (
            '"schema": "mesh-to-cad.initial-plan/1"',
            '"summary": "Build the first CAD candidate directly in canonical coordinates."',
            '"schema": "voxblame.repair-batch/1"',
            '"selected_targets"',
            '"planned_edits"',
            "Every selected target must be covered by one or more planned",
            "`/candidate/work/assessment.json` is Repair-only",
            "mesh-to-cad.assessment/1",
            "`/candidate/selection.json` is the bounded semantic claim",
            "exact six-key schema is in the projected",
            "exactly these seven lines, in this order",
            "`## Preserved Structural Features`",
            '"components":',
            '"features":',
            '"relations":',
            "IDs are globally unique",
            "`parent_id`, revisions, digests, history, request",
            "at most 32 regular sidecar files",
            "at most 512 KiB",
            "trusted operations produce measured evidence",
        ):
            with self.subTest(required=required):
                self.assertIn(required, skill)
        request_section = skill.split(
            "### Exact `run_candidate_tool` request", 1
        )[1].split("\n## ", 1)[0]
        shape_match = re.search(
            r"```json\n(?P<body>\{.*?\})\n```",
            request_section,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(shape_match)
        assert shape_match is not None
        request_shape = json.loads(shape_match.group("body"))
        self.assertEqual(
            {
                "workspace_handle",
                "attempt_handle",
                "candidate_handle",
                "operation_handle",
            },
            set(request_shape),
        )
        self.assertEqual(
            "<capability_bundle_handle returned by start_attempt>",
            request_shape["operation_handle"],
        )
        self.assertIn(
            "Replace the `operation_handle` placeholder with the capability bundle handle",
            request_section,
        )
        for forbidden_field in (
            '"tool":',
            '"argv":',
            '"command":',
            '"capability_bundle_handle":',
        ):
            with self.subTest(forbidden_field=forbidden_field):
                self.assertNotIn(forbidden_field, request_section)
        assessment = (
            REPO_ROOT
            / ".claude/agent-source-projection/skills/mesh-to-cad/references"
            / "assessment.md"
        ).read_text(encoding="utf-8")
        self.assertIn("Repair Cycle's Measured Step", assessment)
        self.assertIn("Submit through `submit_repair`", assessment)
        from_step_contract = assessment.split("- `from_step`", 1)[1].split(
            "- `to_step`", 1
        )[0]
        self.assertIn("selected parent's submission", from_step_contract)
        from_step_source = from_step_contract.split("Do not", 1)[0]
        self.assertIn("`step_ordinal`", from_step_source)
        self.assertNotIn("`parent_step_ordinal`", from_step_source)
        self.assertIn("Do not", from_step_contract)
        self.assertIn("`parent_step_ordinal`", from_step_contract)
        to_step_contract = assessment.split("- `to_step`", 1)[1].split(
            "- `preview_observation`", 1
        )[0]
        self.assertIn("intended child step", to_step_contract)
        self.assertNotIn("`step_ordinal` in the decision facts", to_step_contract)
        for forbidden in ("Step 0", "`null`", "submit_step_zero"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, assessment)

    def test_projected_candidate_authoring_carries_location_guidance(self) -> None:
        guidance = (
            REPO_ROOT
            / ".claude/agent-source-projection/skills/mesh-to-cad/references"
            / "candidate-authoring.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(guidance.split())
        for required in (
            "`Location` is a transform value, not a context manager.",
            "Inside a `BuildPart` context",
            "`with Locations((x, y, z)):`",
            "`Location((x, y, z)) * shape`",
            "`shape.moved(Location((x, y, z)))`",
            "`with Location(...):`",
            "do not discard the transformed value",
            "reuse the same active Attempt's opaque operation",
            "never repeat unchanged source",
        ):
            with self.subTest(required=required):
                self.assertIn(required, normalized)
        location_section = normalized.split(
            "`Location` is a transform value, not a context manager.", 1
        )[1].split("## Repair edits", 1)[0]
        self.assertNotIn("BuildSketch", location_section)

    def test_verify_rejects_missing_extra_and_digest_mismatch(self) -> None:
        mutations = (
            lambda target: (target / projection.PROJECTED_PATHS[0]).unlink(),
            lambda target: (target / "skills/mesh-to-cad/extra.md").write_text("extra"),
            lambda target: (target / projection.PROJECTED_PATHS[0]).write_text("tampered"),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temporary:
                _, target = self._bundle(temporary)
                mutate(target)
                with self.assertRaises(projection.ProjectionError):
                    projection.verify(target)

    def test_verify_rejects_symlinks_and_manifest_path_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            path = target / projection.PROJECTED_PATHS[0]
            original = path.with_suffix(".original")
            path.rename(original)
            os.symlink(original.name, path)
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)

    def test_verify_rejects_noninteger_manifest_size(self) -> None:
        for invalid in (3957.0, True):
            with self.subTest(size=invalid), tempfile.TemporaryDirectory() as temporary:
                _, target = self._bundle(temporary)
                manifest_path = target / projection.MANIFEST_NAME
                manifest = json.loads(manifest_path.read_text())
                manifest["entries"][0]["size"] = invalid
                manifest_path.write_text(
                    json.dumps(
                        manifest,
                        indent=2,
                        sort_keys=True,
                        separators=(",", ": "),
                    )
                    + "\n"
                )
                with self.assertRaises(projection.ProjectionError):
                    projection.verify(target)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_verify_rejects_fifo_manifest_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            manifest = target / projection.MANIFEST_NAME
            manifest.unlink()
            os.mkfifo(manifest)
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)
        with tempfile.TemporaryDirectory() as temporary:
            _, target = self._bundle(temporary)
            manifest_path = target / projection.MANIFEST_NAME
            manifest = json.loads(manifest_path.read_text())
            manifest["entries"][0]["path"] = "skills/mesh-to-cad/extra.md"
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaises(projection.ProjectionError):
                projection.verify(target)

    def test_check_detects_source_drift_and_bundle_lints_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, target = self._bundle(temporary)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            source.write_bytes(source.read_bytes() + b"\nordinary drift\n")
            with self.assertRaises(projection.ProjectionError):
                projection.check_bundle(root, target)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            _stage_source(root)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            source.write_bytes(source.read_bytes() + b"\nmesh-to-cad-workspace\n")
            with self.assertRaises(projection.ProjectionError):
                projection.bundle(root, root / projection.PROJECTION_ROOT_REL)

    def test_bundle_refuses_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "repo"
            _stage_source(root)
            source = root / projection.SOURCE_MAPPINGS[0][0]
            original = source.with_suffix(".original")
            source.rename(original)
            os.symlink(original.name, source)
            with self.assertRaises(projection.ProjectionError):
                projection.bundle(root, root / projection.PROJECTION_ROOT_REL)

    def test_runtime_paths_are_fixed(self) -> None:
        root = REPO_ROOT / projection.PROJECTION_ROOT_REL
        self.assertEqual(root / "skills", projection.projected_skills_root(root))
        self.assertEqual(
            root / "agent-surface/client.py",
            projection.projected_agent_surface_client(root),
        )


if __name__ == "__main__":
    unittest.main()
