"""Filesystem repository for immutable VoxBlame sessions and candidate steps."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
import uuid

from meshscope.voxblame.codec import read_surface_tree, write_surface_tree
from meshscope.voxblame.errors import OctreeError, SurfaceTreeError
from meshscope.voxblame.reporting import tree_metadata
from meshscope.voxblame.tree import SurfaceTree


SESSION_SCHEMA = "voxblame.session/2"


class VoxBlameStore:
    """Own validated reads and atomic publication below one state directory."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    @property
    def session_path(self) -> Path:
        return self.root / "session.json"

    @property
    def reference_path(self) -> Path:
        return self.root / "reference.vbsvo"

    def step_path(self, step: int) -> Path:
        return self.root / "steps" / f"{step:06d}"

    def load_session(self) -> dict[str, Any]:
        return read_json(self.session_path)

    def initialize(
        self,
        session: dict[str, Any],
        reference_tree: SurfaceTree,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        gitignore = self.root / ".gitignore"
        if (
            gitignore.exists()
            and gitignore.read_text(encoding="utf-8") != ".tmp-*\n"
        ):
            raise OctreeError("voxblame .gitignore must contain only .tmp-*")
        if not gitignore.exists():
            gitignore.write_text(".tmp-*\n", encoding="utf-8")
        token = uuid.uuid4().hex
        temp_tree = self.root / f".tmp-reference-{token}.vbsvo"
        temp_session = self.root / f".tmp-session-{token}.json"
        write_surface_tree(reference_tree, temp_tree)
        write_json(temp_session, session)
        self.load_tree(temp_tree, int(session["max_depth"]))
        if self.reference_path.exists() or self.session_path.exists():
            raise OctreeError("session appeared concurrently during initialization")
        os.replace(temp_tree, self.reference_path)
        os.replace(temp_session, self.session_path)

    def load_reference_tree(self, max_depth: int) -> SurfaceTree:
        return self.load_tree(self.reference_path, max_depth)

    def load_candidate_tree(self, step: int, max_depth: int) -> SurfaceTree:
        path = self.step_path(step)
        if not path.is_dir():
            raise OctreeError(f"compare_to step {step} is not published")
        return self.load_tree(path / "candidate.vbsvo", max_depth)

    def load_tree(self, path: Path, max_depth: int) -> SurfaceTree:
        try:
            tree = read_surface_tree(path)
        except SurfaceTreeError as exc:
            raise OctreeError(str(exc)) from exc
        if tree.max_depth != max_depth:
            raise OctreeError("surface-tree max_depth does not match the session")
        return tree

    def publish_step(
        self,
        step: int,
        candidate_tree: SurfaceTree,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        target = self.step_path(step)
        candidate_digest = candidate_tree.logical_sha256
        if target.exists():
            existing_tree = self.load_tree(
                target / "candidate.vbsvo", candidate_tree.max_depth
            )
            existing_report = read_json(target / "report.json")
            if (
                existing_tree.logical_sha256 != candidate_digest
                or existing_report != report
            ):
                raise OctreeError(
                    f"step {step} is modified or already exists "
                    "with a different candidate"
                )
            return existing_report

        stage = self.root / f".tmp-{step:06d}-{uuid.uuid4().hex}"
        stage.mkdir(parents=False, exist_ok=False)
        try:
            write_surface_tree(candidate_tree, stage / "candidate.vbsvo")
            reloaded = self.load_tree(
                stage / "candidate.vbsvo", candidate_tree.max_depth
            )
            if reloaded.logical_sha256 != candidate_digest:
                raise OctreeError("staged candidate snapshot digest mismatch")
            write_json(stage / "report.json", report)
            staged_report = read_json(stage / "report.json")
            if (
                staged_report.get("candidate", {}).get("logical_sha256")
                != candidate_digest
            ):
                raise OctreeError("staged report digest mismatch")
            target.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(target)
        except Exception:
            # Crash evidence remains under the ignored .tmp-* name by design.
            raise
        return report


def validate_session_metadata(
    *,
    session: dict[str, Any],
    frame_json: dict[str, Any],
    max_depth: int,
    reference_source_digest: str,
    reference_tree: SurfaceTree,
) -> None:
    if session.get("schema") != SESSION_SCHEMA:
        raise OctreeError("unsupported or invalid VoxBlame session schema")
    if session.get("max_depth") != max_depth:
        raise OctreeError("max_depth does not match the existing session")
    if session.get("frame") != frame_json:
        raise OctreeError(
            "reference frame metadata does not match the existing session"
        )
    reference = session.get("reference", {})
    if reference.get("source_sha256") != reference_source_digest:
        raise OctreeError("reference mesh does not match the existing session")
    expected = {
        "source_sha256": reference_source_digest,
        **tree_metadata(reference_tree),
    }
    if reference != expected:
        raise OctreeError("reference snapshot digest does not match the session")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OctreeError(f"failed to read JSON state: {path}") from exc
    if not isinstance(value, dict):
        raise OctreeError(f"JSON state must be an object: {path}")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    try:
        path.write_text(
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                separators=(",", ": "),
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise OctreeError(f"failed to write JSON state: {path}") from exc
