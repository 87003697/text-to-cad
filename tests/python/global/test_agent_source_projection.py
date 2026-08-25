"""Agent Source Projection materializer/verify tests.

The projection is the Agent-only subset of installed skill source. These
tests exercise it against physical files so the fail-closed guarantees hold
against the real filesystem, not a mocked one.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from scripts.pilot import agent_source_projection
from tests.python.support.paths import REPO_ROOT


def _stage_source(repo_root: Path) -> None:
    """Copy the canonical projection sources from the real repo into ``repo_root``."""

    for source_rel, _ in agent_source_projection.ALLOWED_SOURCES:
        destination = repo_root / source_rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO_ROOT / source_rel).read_bytes())


class AgentSourceProjectionTests(unittest.TestCase):
    def test_materialize_produces_only_the_allowlist_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL

            inventory = agent_source_projection.materialize(repo_root, target)

            physical: set[str] = set()
            symlink_seen = False
            for parent, dirnames, filenames in os.walk(target, followlinks=False):
                parent_path = Path(parent)
                for name in dirnames + filenames:
                    if (parent_path / name).is_symlink():
                        symlink_seen = True
                for name in filenames:
                    physical.add((parent_path / name).relative_to(target).as_posix())
            self.assertFalse(symlink_seen, "projection must not contain symlinks")

            expected = {agent_source_projection.MANIFEST_NAME}
            expected.update(
                projected for _, projected in agent_source_projection.ALLOWED_SOURCES
            )
            self.assertEqual(expected, physical)

            self.assertEqual(inventory.schema, agent_source_projection.PROJECTION_SCHEMA)
            self.assertEqual(
                inventory.version, agent_source_projection.PROJECTION_VERSION
            )

            manifest = json.loads(
                (target / agent_source_projection.MANIFEST_NAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest["schema"], agent_source_projection.PROJECTION_SCHEMA)
            for entry in manifest["entries"]:
                body = (target / entry["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(body).hexdigest(), entry["sha256"])
                self.assertEqual(len(body), entry["size"])

    def test_projection_never_contains_forbidden_supervisor_source(self) -> None:
        """Guardrail: no projected path names Workspace, review, or runner code."""

        forbidden_fragments = (
            "workspace.py",
            "workspace_core.py",
            "workspace_supervisor.py",
            "runner.py",
            "mesh-to-cad-workspace",
            "mesh-to-cad-review",
            "mesh-to-cad-agent-surface",
            "reference.vbsvo",
            ".git",
            ".env",
            "credentials",
        )
        for _, projected_rel in agent_source_projection.ALLOWED_SOURCES:
            for fragment in forbidden_fragments:
                self.assertNotIn(
                    fragment,
                    projected_rel,
                    f"forbidden fragment in projection allowlist: {projected_rel}",
                )
            # No raw PLY or canonical reference bytes may sneak in.
            self.assertFalse(projected_rel.endswith(".ply"))
            self.assertFalse(projected_rel.endswith(".vbsvo"))
            # No Python or shell scripts in the projection — SKILL.md + refs only.
            self.assertNotIn("/scripts/", "/" + projected_rel + "/")
            self.assertFalse(projected_rel.endswith(".py"))
            self.assertFalse(projected_rel.endswith(".sh"))

    def test_verify_detects_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            (target / "skills" / "mesh-to-cad" / "SKILL.md").unlink()
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_detects_extra_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            (target / "skills" / "mesh-to-cad" / "extra.md").write_text(
                "surprise\n", encoding="utf-8"
            )
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_detects_digest_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            # Overwrite one projected file body without updating its manifest
            # entry; verify must reject the drift.
            (target / "skills" / "mesh-to-cad" / "SKILL.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_rejects_symlink_in_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            skill_md = target / "skills" / "mesh-to-cad" / "SKILL.md"
            replacement = skill_md.with_suffix(".md.link")
            skill_md.rename(replacement)
            os.symlink(replacement, skill_md)
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_rejects_schema_or_version_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            manifest_path = target / agent_source_projection.MANIFEST_NAME
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            parsed["schema"] = "text-to-cad.agent-source-projection/999"
            manifest_path.write_text(
                json.dumps(parsed, indent=2, sort_keys=True, separators=(",", ": "))
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_matches_source_detects_stale_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            # Mutate the *source* after materialization; the projection is now
            # stale and the runner's live-source check must fail closed.
            skill_md = repo_root / "skills" / "mesh-to-cad" / "SKILL.md"
            skill_md.write_bytes(skill_md.read_bytes() + b"\ndrift\n")
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify_matches_source(repo_root, target)

    def test_materialize_refuses_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            skill_md = repo_root / "skills" / "mesh-to-cad" / "SKILL.md"
            replacement = skill_md.with_suffix(".md.orig")
            skill_md.rename(replacement)
            os.symlink(replacement, skill_md)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.materialize(repo_root, target)

    def test_repo_checked_in_projection_verifies(self) -> None:
        """The projection that ships in the repo tree must always verify."""

        agent_source_projection.verify_matches_source(
            REPO_ROOT, REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        )

    def test_repo_checked_in_projection_contains_no_symlinks(self) -> None:
        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        for parent, dirnames, filenames in os.walk(projection_root, followlinks=False):
            parent_path = Path(parent)
            for name in dirnames + filenames:
                self.assertFalse(
                    (parent_path / name).is_symlink(),
                    f"symlink found in projection: {parent_path / name}",
                )

    def test_repo_projection_root_and_skills_subdir_exist(self) -> None:
        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        skills_root = agent_source_projection.projected_skills_root(projection_root)
        self.assertTrue(projection_root.is_dir())
        self.assertTrue(skills_root.is_dir())
        # No stray absolute host path leaks: relative-path check.
        self.assertFalse(projection_root.is_symlink())
        self.assertFalse(skills_root.is_symlink())

    def test_projection_manifest_bytes_are_canonical(self) -> None:
        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        manifest_body = (
            projection_root / agent_source_projection.MANIFEST_NAME
        ).read_bytes()
        parsed = json.loads(manifest_body)
        entries = tuple(
            agent_source_projection.ProjectionEntry(**entry)
            for entry in parsed["entries"]
        )
        canonical = agent_source_projection.canonical_manifest_bytes(
            agent_source_projection.PROJECTION_SCHEMA,
            agent_source_projection.PROJECTION_VERSION,
            entries,
        )
        self.assertEqual(manifest_body, canonical)


if __name__ == "__main__":
    unittest.main()
