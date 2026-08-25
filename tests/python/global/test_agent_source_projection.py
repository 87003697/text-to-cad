"""Agent Source Projection materializer/verify tests.

The projection is the Agent-only subset of installed skill source plus the
fixed Agent Surface client. These tests exercise it against physical files
so the fail-closed guarantees hold against the real filesystem, not a
mocked one.
"""

from __future__ import annotations

import hashlib
import json
import os
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

    def test_allowlist_never_names_canonical_trusted_documents(self) -> None:
        """The allowlist must not source from canonical authority-facing files."""

        for source_rel, _ in agent_source_projection.ALLOWED_SOURCES:
            self.assertNotIn(
                source_rel,
                agent_source_projection.FORBIDDEN_ORIGINAL_SOURCES,
                f"canonical trusted document reached the allowlist: {source_rel}",
            )

    def test_forbidden_originals_are_not_projection_sources(self) -> None:
        """None of the enumerated leaking documents appears as a source."""

        source_paths = {source for source, _ in agent_source_projection.ALLOWED_SOURCES}
        for forbidden in agent_source_projection.FORBIDDEN_ORIGINAL_SOURCES:
            self.assertNotIn(forbidden, source_paths)

    def test_compute_expected_entries_refuses_forbidden_source(self) -> None:
        """A future edit that projects a canonical document fails closed."""

        original = agent_source_projection.ALLOWED_SOURCES
        try:
            leak = "skills/mesh-to-cad/SKILL.md"
            agent_source_projection.ALLOWED_SOURCES = original + (
                (leak, "skills/mesh-to-cad/SKILL.md"),
            )
            with tempfile.TemporaryDirectory() as temporary:
                repo_root = Path(temporary) / "repo"
                _stage_source(repo_root)
                (repo_root / leak).parent.mkdir(parents=True, exist_ok=True)
                (repo_root / leak).write_bytes(
                    (REPO_ROOT / leak).read_bytes()
                )
                with self.assertRaises(agent_source_projection.ProjectionError):
                    agent_source_projection.compute_expected_entries(repo_root)
        finally:
            agent_source_projection.ALLOWED_SOURCES = original

    def test_scan_rejects_forbidden_content_tokens(self) -> None:
        """Bytes containing any FORBIDDEN_CONTENT_TOKENS token fail verification."""

        for token in agent_source_projection.FORBIDDEN_CONTENT_TOKENS:
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection._scan_content_for_forbidden(
                    "skills/mesh-to-cad/SKILL.md",
                    ("prefix " + token + " suffix\n").encode("utf-8"),
                )
            # Case-insensitive match.
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection._scan_content_for_forbidden(
                    "skills/mesh-to-cad/SKILL.md",
                    ("prefix " + token.upper() + " suffix\n").encode("utf-8"),
                )

    def test_generated_projection_contains_no_forbidden_content(self) -> None:
        """Recursive byte-level scan over the checked-in projection."""

        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        for parent, _, filenames in os.walk(projection_root, followlinks=False):
            parent_path = Path(parent)
            for name in filenames:
                if name == agent_source_projection.MANIFEST_NAME:
                    continue
                body = (parent_path / name).read_bytes()
                lower = body.decode("latin-1").lower()
                for token in agent_source_projection.FORBIDDEN_CONTENT_TOKENS:
                    self.assertNotIn(
                        token.lower(),
                        lower,
                        f"projected file {parent_path / name} exposes {token!r}",
                    )

    def test_generated_projection_does_not_include_canonical_skills(self) -> None:
        """Recursive check: forbidden originals must not be byte-equal to any file."""

        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        forbidden_digests = {
            hashlib.sha256((REPO_ROOT / forbidden).read_bytes()).hexdigest()
            for forbidden in agent_source_projection.FORBIDDEN_ORIGINAL_SOURCES
            if (REPO_ROOT / forbidden).is_file()
        }
        for parent, _, filenames in os.walk(projection_root, followlinks=False):
            parent_path = Path(parent)
            for name in filenames:
                if name == agent_source_projection.MANIFEST_NAME:
                    continue
                digest = hashlib.sha256(
                    (parent_path / name).read_bytes()
                ).hexdigest()
                self.assertNotIn(
                    digest,
                    forbidden_digests,
                    f"{parent_path / name} is byte-equal to a forbidden original",
                )

    def test_projection_includes_agent_surface_client_at_stable_path(self) -> None:
        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        client = agent_source_projection.projected_agent_surface_client(
            projection_root
        )
        self.assertTrue(client.is_file())
        self.assertFalse(client.is_symlink())
        self.assertEqual(
            client.relative_to(projection_root).as_posix(),
            agent_source_projection.CLIENT_PROJECTED_REL,
        )
        # The projected client bytes are the fixed repo client bytes; this is
        # the single source of truth the runner mounts from.
        repo_bytes = (REPO_ROOT / "scripts/pilot/agent_surface_client.py").read_bytes()
        self.assertEqual(client.read_bytes(), repo_bytes)

    def test_projection_root_only_children_are_skills_and_agent_surface(self) -> None:
        """No other siblings under the projection root."""

        projection_root = REPO_ROOT / agent_source_projection.PROJECTION_ROOT_REL
        children = {entry.name for entry in projection_root.iterdir()}
        self.assertEqual(
            children,
            {
                agent_source_projection.MANIFEST_NAME,
                agent_source_projection.SKILLS_SUBDIR,
                agent_source_projection.CLIENT_SUBDIR,
            },
        )

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
            (target / "skills" / "mesh-to-cad" / "SKILL.md").write_text(
                "tampered\n", encoding="utf-8"
            )
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify(target)

    def test_verify_detects_forbidden_token_in_projected_bytes(self) -> None:
        """Tampering a projected file with a forbidden token fails verify."""

        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            target = repo_root / agent_source_projection.PROJECTION_ROOT_REL
            agent_source_projection.materialize(repo_root, target)
            skill_md = target / "skills" / "mesh-to-cad" / "SKILL.md"
            body = skill_md.read_bytes() + b"\nSee mesh-to-cad-workspace init.\n"
            skill_md.write_bytes(body)
            # Rewrite the manifest so the sha256/size line up with the new
            # content — this proves the content-policy guard is *independent*
            # of the digest guard.
            manifest_path = target / agent_source_projection.MANIFEST_NAME
            parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in parsed["entries"]:
                if entry["path"] == "skills/mesh-to-cad/SKILL.md":
                    entry["sha256"] = hashlib.sha256(body).hexdigest()
                    entry["size"] = len(body)
            manifest_path.write_bytes(
                agent_source_projection.canonical_manifest_bytes(
                    agent_source_projection.PROJECTION_SCHEMA,
                    agent_source_projection.PROJECTION_VERSION,
                    tuple(
                        agent_source_projection.ProjectionEntry(**entry)
                        for entry in parsed["entries"]
                    ),
                )
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
            skill_md = (
                repo_root / "skills/mesh-to-cad/agent-source/SKILL.md"
            )
            skill_md.write_bytes(skill_md.read_bytes() + b"\nagent drift\n")
            with self.assertRaises(agent_source_projection.ProjectionError):
                agent_source_projection.verify_matches_source(repo_root, target)

    def test_materialize_refuses_symlinked_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary) / "repo"
            _stage_source(repo_root)
            skill_md = repo_root / "skills/mesh-to-cad/agent-source/SKILL.md"
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
