#!/usr/bin/env python3
"""Create and audit portable canonical Workspace Git authority packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Sequence


SCHEMA = "mesh-to-cad.workspace-authority/1"
WORKSPACE_SCHEMA = "mesh-to-cad.workspace/1"
PUBLICATION_REF = "refs/workspace-authority/portable-v1"
BUNDLE_NAME = "workspace-authority.bundle"
RECEIPT_NAME = "workspace-authority.json"
CREATOR_NAME = "text-to-cad.workspace-authority"
CREATOR_VERSION = 1
DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_BYTES = 5 * 1024 * 1024 * 1024
RECEIPT_KEYS = {
    "schema",
    "bundle",
    "created_by",
    "protocol_commits",
    "required_commits",
    "validation",
    "workspace",
}
OID = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AuthorityError(RuntimeError):
    """A stable fail-closed portable-authority classification."""

    def __init__(self, classification: str, detail: str) -> None:
        super().__init__(detail)
        self.classification = classification
        self.detail = detail


def _canonical_json(value: object) -> bytes:
    """Return the contract's UTF-8 canonical JSON encoding."""

    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode(
        "utf-8"
    )


def _sha256_bytes(value: bytes) -> str:
    """Return the lowercase SHA-256 digest of bytes."""

    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path, *, deadline: float | None = None) -> str:
    """Hash one regular file while respecting the audit deadline."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                if deadline is not None and time.monotonic() > deadline:
                    raise AuthorityError("authority_timeout", "authority audit timed out")
                digest.update(chunk)
    except OSError as exc:
        raise AuthorityError("authority_partial", f"cannot read {path.name}: {exc}") from exc
    return digest.hexdigest()


def _command(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run one local argv command without a shell."""

    try:
        return subprocess.run(
            list(argv),
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuthorityError("authority_timeout", f"command timed out: {argv[0]}") from exc
    except OSError as exc:
        raise AuthorityError("authority_tool_failure", f"cannot run {argv[0]}: {exc}") from exc


def _git(
    workspace: Path,
    *argv: str,
    timeout: float | None = None,
    classification: str = "authority_invalid_bundle",
) -> str:
    """Run Git in a declared repository and return stripped stdout."""

    completed = _command(["git", *argv], cwd=workspace, timeout=timeout)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "Git command failed"
        raise AuthorityError(classification, f"git {' '.join(argv)}: {detail}")
    return completed.stdout.strip()


def _helper_command(helper: str | Path) -> list[str]:
    """Resolve the existing Workspace public process interface."""

    helper_text = str(helper)
    helper_path = Path(helper_text).expanduser()
    if helper_path.exists() and (helper_path.is_dir() or helper_path.suffix == ".py"):
        return [sys.executable, str(helper_path.resolve())]
    return [helper_text]


def _validate_workspace(
    workspace: Path,
    helper: str | Path,
    *,
    timeout: float,
) -> dict[str, Any]:
    """Invoke the existing Workspace validator unchanged and return its payload."""

    completed = _command(
        [
            *_helper_command(helper),
            "validate",
            "--workspace",
            str(workspace),
        ],
        timeout=timeout,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuthorityError(
            "authority_validator_failure", "Workspace validator returned invalid JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise AuthorityError(
            "authority_validator_failure", "Workspace validator returned a non-object"
        )
    if completed.returncode != 0 or payload.get("ok") is not True:
        error = payload.get("error")
        classification = (
            str(error.get("classification"))
            if isinstance(error, dict) and error.get("classification")
            else "invalid_workspace"
        )
        detail = (
            str(error.get("detail"))
            if isinstance(error, dict) and error.get("detail")
            else "Workspace validation failed"
        )
        raise AuthorityError(classification, detail)
    graph = payload.get("graph")
    if not isinstance(graph, dict):
        raise AuthorityError("authority_validator_failure", "validator omitted graph")
    if not isinstance(graph.get("final_delivery"), dict):
        raise AuthorityError("authority_incomplete_workspace", "Workspace has no Final Delivery")
    return payload


def _read_workspace_document(workspace: Path) -> dict[str, Any]:
    """Read the canonical Workspace identity used by the routing receipt."""

    path = workspace / "workspace.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityError("authority_partial", f"cannot read workspace.json: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema") != WORKSPACE_SCHEMA:
        raise AuthorityError("authority_workspace_mismatch", "workspace.json schema is invalid")
    workspace_id = value.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise AuthorityError("authority_workspace_mismatch", "workspace_id is missing")
    return value


def _commit_records(workspace: Path, head: str) -> list[dict[str, Any]]:
    """Describe the exact linear-or-branching commit graph reachable from HEAD."""

    commits = _git(
        workspace,
        "rev-list",
        "--reverse",
        "--topo-order",
        head,
        classification="authority_git_workspace",
    ).splitlines()
    records: list[dict[str, Any]] = []
    for commit in commits:
        raw = _git(
            workspace,
            "show",
            "-s",
            "--format=%T%n%P",
            commit,
            classification="authority_git_workspace",
        ).splitlines()
        if not raw:
            raise AuthorityError("authority_git_workspace", f"cannot inspect commit {commit}")
        records.append(
            {
                "commit": commit,
                "parents": raw[1].split() if len(raw) > 1 and raw[1] else [],
                "tree": raw[0],
            }
        )
    if not records or records[-1]["commit"] != head:
        raise AuthorityError("authority_git_workspace", "HEAD history is incomplete")
    return records


def _protocol_commits(workspace: Path) -> list[str]:
    """Return publishing commits for tracked Workspace authority paths."""

    paths = _git(
        workspace,
        "ls-files",
        "-z",
        classification="authority_git_workspace",
    ).split("\0")
    commits: set[str] = set()
    for relative in paths:
        if not relative or relative == ".gitignore":
            continue
        commit = _git(
            workspace,
            "log",
            "-1",
            "--format=%H",
            "--",
            relative,
            classification="authority_git_workspace",
        )
        if commit:
            commits.add(commit)
    order = _git(
        workspace,
        "rev-list",
        "--reverse",
        "--topo-order",
        "HEAD",
        classification="authority_git_workspace",
    ).splitlines()
    return [commit for commit in order if commit in commits]


def create_authority(workspace: Path, helper: str | Path, *, timeout: float) -> dict[str, Any]:
    """Atomically publish one minimal-ref bundle and canonical routing receipt."""

    workspace = workspace.resolve()
    if not (workspace / ".git").exists():
        raise AuthorityError("authority_git_workspace", "Workspace is not a live Git repository")
    validation = _validate_workspace(workspace, helper, timeout=timeout)
    document = _read_workspace_document(workspace)
    head = _git(workspace, "rev-parse", "HEAD", classification="authority_git_workspace")
    tree = _git(
        workspace,
        "rev-parse",
        f"{head}^{{tree}}",
        classification="authority_git_workspace",
    )
    records = _commit_records(workspace, head)
    protocol_commits = _protocol_commits(workspace)
    graph_digest = _sha256_bytes(_canonical_json(validation["graph"]))
    creator_digest = _sha256_file(Path(__file__).resolve())
    bundle_tmp = workspace / f".{BUNDLE_NAME}.tmp-{os.getpid()}"
    receipt_tmp = workspace / f".{RECEIPT_NAME}.tmp-{os.getpid()}"
    ref_created = False
    try:
        ref_check = _command(
            ["git", "show-ref", "--verify", "--quiet", PUBLICATION_REF],
            cwd=workspace,
        )
        if ref_check.returncode == 0:
            raise AuthorityError("authority_ref_conflict", f"ref already exists: {PUBLICATION_REF}")
        if ref_check.returncode != 1:
            raise AuthorityError(
                "authority_git_workspace",
                f"cannot inspect publication ref: {ref_check.stderr.strip()}",
            )
        _git(
            workspace,
            "update-ref",
            PUBLICATION_REF,
            head,
            classification="authority_git_workspace",
        )
        ref_created = True
        _git(
            workspace,
            "bundle",
            "create",
            str(bundle_tmp),
            PUBLICATION_REF,
            classification="authority_bundle_creation",
        )
        bundle_bytes = bundle_tmp.read_bytes()
        receipt = {
            "schema": SCHEMA,
            "bundle": {
                "path": BUNDLE_NAME,
                "sha256": _sha256_bytes(bundle_bytes),
                "size_bytes": len(bundle_bytes),
            },
            "created_by": {
                "name": CREATOR_NAME,
                "sha256": creator_digest,
                "version": CREATOR_VERSION,
            },
            "protocol_commits": protocol_commits,
            "required_commits": records,
            "validation": {
                "classification": "valid",
                "graph_sha256": graph_digest,
            },
            "workspace": {
                "head": head,
                "id": document["workspace_id"],
                "publication_ref": PUBLICATION_REF,
                "schema": WORKSPACE_SCHEMA,
                "tree": tree,
            },
        }
        receipt_tmp.write_bytes(_canonical_json(receipt))
        bundle_tmp.replace(workspace / BUNDLE_NAME)
        receipt_tmp.replace(workspace / RECEIPT_NAME)
    except OSError as exc:
        raise AuthorityError("authority_publication_failure", str(exc)) from exc
    finally:
        ref_cleanup = None
        if ref_created:
            ref_cleanup = _command(
                ["git", "update-ref", "-d", PUBLICATION_REF],
                cwd=workspace,
            )
        for path in (bundle_tmp, receipt_tmp):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if ref_cleanup is not None and ref_cleanup.returncode != 0:
            raise AuthorityError(
                "authority_publication_failure",
                f"cannot remove temporary publication ref: {ref_cleanup.stderr.strip()}",
            )
    return {
        "mode": "live",
        "evidence": [RECEIPT_NAME, BUNDLE_NAME],
        "receipt": receipt,
    }


def _read_receipt(root: Path) -> dict[str, Any]:
    """Read and strictly validate canonical receipt syntax and top-level shape."""

    path = root / RECEIPT_NAME
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except FileNotFoundError as exc:
        raise AuthorityError("authority_missing", f"missing {RECEIPT_NAME}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityError("authority_corrupt_receipt", f"cannot read receipt: {exc}") from exc
    if not isinstance(receipt, dict) or set(receipt) != RECEIPT_KEYS:
        raise AuthorityError("authority_corrupt_receipt", "receipt object is not closed")
    if raw != _canonical_json(receipt):
        raise AuthorityError("authority_corrupt_receipt", "receipt is not canonical JSON")
    if receipt.get("schema") != SCHEMA:
        raise AuthorityError("authority_corrupt_receipt", "unsupported receipt schema")
    for key in ("bundle", "created_by", "validation", "workspace"):
        if not isinstance(receipt.get(key), dict):
            raise AuthorityError("authority_corrupt_receipt", f"receipt.{key} is invalid")
    if not isinstance(receipt.get("required_commits"), list) or not isinstance(
        receipt.get("protocol_commits"), list
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt commit sets are invalid")
    bundle = receipt["bundle"]
    if (
        set(bundle) != {"path", "sha256", "size_bytes"}
        or bundle.get("path") != BUNDLE_NAME
        or not isinstance(bundle.get("sha256"), str)
        or SHA256.fullmatch(bundle["sha256"]) is None
        or type(bundle.get("size_bytes")) is not int
        or bundle["size_bytes"] < 1
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt.bundle is invalid")
    creator = receipt["created_by"]
    if (
        set(creator) != {"name", "sha256", "version"}
        or creator.get("name") != CREATOR_NAME
        or creator.get("version") != CREATOR_VERSION
        or not isinstance(creator.get("sha256"), str)
        or SHA256.fullmatch(creator["sha256"]) is None
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt.created_by is invalid")
    validation = receipt["validation"]
    if (
        set(validation) != {"classification", "graph_sha256"}
        or validation.get("classification") != "valid"
        or not isinstance(validation.get("graph_sha256"), str)
        or SHA256.fullmatch(validation["graph_sha256"]) is None
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt.validation is invalid")
    workspace = receipt["workspace"]
    if (
        set(workspace) != {"head", "id", "publication_ref", "schema", "tree"}
        or not isinstance(workspace.get("head"), str)
        or OID.fullmatch(workspace["head"]) is None
        or not isinstance(workspace.get("tree"), str)
        or OID.fullmatch(workspace["tree"]) is None
        or not isinstance(workspace.get("id"), str)
        or not workspace["id"]
        or not isinstance(workspace.get("publication_ref"), str)
        or not workspace["publication_ref"]
        or not isinstance(workspace.get("schema"), str)
        or not workspace["schema"]
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt.workspace is invalid")
    required = receipt["required_commits"]
    if not required:
        raise AuthorityError("authority_corrupt_receipt", "required_commits is empty")
    for record in required:
        if (
            not isinstance(record, dict)
            or set(record) != {"commit", "parents", "tree"}
            or not isinstance(record.get("commit"), str)
            or OID.fullmatch(record["commit"]) is None
            or not isinstance(record.get("tree"), str)
            or OID.fullmatch(record["tree"]) is None
            or not isinstance(record.get("parents"), list)
            or any(
                not isinstance(parent, str) or OID.fullmatch(parent) is None
                for parent in record["parents"]
            )
        ):
            raise AuthorityError("authority_corrupt_receipt", "required commit record is invalid")
    protocol = receipt["protocol_commits"]
    if not protocol or any(
        not isinstance(commit, str) or OID.fullmatch(commit) is None
        for commit in protocol
    ):
        raise AuthorityError("authority_corrupt_receipt", "protocol_commits is invalid")
    return receipt


def _copy_tree_bounded(
    source: Path,
    target: Path,
    *,
    deadline: float,
    max_files: int,
    max_bytes: int,
) -> None:
    """Stage one retained experiment locally with explicit time and volume bounds."""

    files = 0
    total = 0
    target.mkdir(parents=True)
    for path in source.rglob("*"):
        if time.monotonic() > deadline:
            raise AuthorityError("authority_timeout", "authority staging timed out")
        relative = path.relative_to(source)
        if not relative.parts or relative.parts[0] == ".git":
            continue
        destination = target / relative
        if path.is_symlink():
            files += 1
            link = os.readlink(path)
            total += len(link.encode())
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(link)
        elif path.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        elif path.is_file():
            files += 1
            size = path.stat().st_size
            total += size
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as reader, destination.open("wb") as writer:
                while chunk := reader.read(1024 * 1024):
                    if time.monotonic() > deadline:
                        raise AuthorityError("authority_timeout", "authority staging timed out")
                    writer.write(chunk)
        else:
            raise AuthorityError("authority_unsafe_path", f"unsupported source entry: {relative}")
        if files > max_files or total > max_bytes:
            raise AuthorityError("authority_stage_bounds", "authority staging bounds exceeded")


def _remaining(deadline: float) -> float:
    """Return a positive remaining timeout or fail with the stable class."""

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AuthorityError("authority_timeout", "authority audit timed out")
    return remaining


def _verify_bundle(
    staged: Path,
    materialized: Path,
    receipt: dict[str, Any],
    *,
    deadline: float,
) -> None:
    """Verify bundle identity, its sole ref, commit graph, and transferred files."""

    bundle = staged / BUNDLE_NAME
    if not bundle.is_file():
        raise AuthorityError("authority_partial", f"missing {BUNDLE_NAME}")
    bundle_record = receipt["bundle"]
    if (
        set(bundle_record) != {"path", "sha256", "size_bytes"}
        or bundle_record.get("path") != BUNDLE_NAME
    ):
        raise AuthorityError("authority_corrupt_receipt", "receipt.bundle is invalid")
    if bundle.stat().st_size != bundle_record.get("size_bytes"):
        raise AuthorityError("authority_digest_mismatch", "bundle size does not match receipt")
    if _sha256_file(bundle, deadline=deadline) != bundle_record.get("sha256"):
        raise AuthorityError("authority_digest_mismatch", "bundle digest does not match receipt")

    workspace_record = receipt["workspace"]
    if set(workspace_record) != {"head", "id", "publication_ref", "schema", "tree"}:
        raise AuthorityError("authority_corrupt_receipt", "receipt.workspace is invalid")
    if workspace_record.get("publication_ref") != PUBLICATION_REF:
        raise AuthorityError("authority_wrong_ref", "receipt publication ref is not canonical")
    if workspace_record.get("schema") != WORKSPACE_SCHEMA:
        raise AuthorityError("authority_workspace_mismatch", "receipt Workspace schema is invalid")

    materialized.mkdir()
    _git(materialized, "init", "--quiet", classification="authority_invalid_bundle")
    _git(
        materialized,
        "bundle",
        "verify",
        str(bundle),
        timeout=_remaining(deadline),
        classification="authority_invalid_bundle",
    )
    heads = _git(
        materialized,
        "bundle",
        "list-heads",
        str(bundle),
        timeout=_remaining(deadline),
        classification="authority_invalid_bundle",
    ).splitlines()
    expected_head = workspace_record.get("head")
    if heads != [f"{expected_head} {PUBLICATION_REF}"]:
        well_formed_ref = (
            len(heads) == 1
            and re.fullmatch(r"[0-9a-f]{40} refs/[A-Za-z0-9._/-]+", heads[0])
            is not None
        )
        raise AuthorityError(
            "authority_wrong_ref" if well_formed_ref else "authority_invalid_bundle",
            "bundle does not contain exactly the receipt ref",
        )
    _git(
        materialized,
        "fetch",
        "--quiet",
        str(bundle),
        f"{PUBLICATION_REF}:{PUBLICATION_REF}",
        timeout=_remaining(deadline),
        classification="authority_invalid_bundle",
    )
    _git(
        materialized,
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.required=false",
        "checkout",
        "--quiet",
        "--detach",
        str(expected_head),
        timeout=_remaining(deadline),
        classification="authority_invalid_bundle",
    )
    actual_records = _commit_records(materialized, str(expected_head))
    if actual_records != receipt["required_commits"]:
        raise AuthorityError(
            "authority_parent_mismatch",
            "required commit parent/tree records disagree",
        )
    if actual_records[-1]["tree"] != workspace_record.get("tree"):
        raise AuthorityError("authority_tree_mismatch", "HEAD tree disagrees with receipt")
    actual_commits = {item["commit"] for item in actual_records}
    protocol = receipt["protocol_commits"]
    if not protocol or any(
        not isinstance(item, str) or item not in actual_commits
        for item in protocol
    ):
        raise AuthorityError("authority_commit_mismatch", "protocol commit set is invalid")

    tracked = _git(
        materialized,
        "ls-files",
        "-z",
        classification="authority_invalid_bundle",
    ).split("\0")
    for relative in tracked:
        if not relative:
            continue
        expected = materialized / relative
        transferred = staged / relative
        if not transferred.exists() and not transferred.is_symlink():
            raise AuthorityError("authority_partial", f"missing tracked artifact: {relative}")
        attribute = _git(
            materialized,
            "check-attr",
            "filter",
            "--",
            relative,
            classification="authority_invalid_bundle",
        )
        is_lfs = attribute.endswith(": lfs")
        if is_lfs:
            if not transferred.is_file() or transferred.is_symlink():
                raise AuthorityError(
                    "authority_dirty_artifact", f"LFS artifact type mismatch: {relative}"
                )
            mode = expected.stat().st_mode
            shutil.copyfile(transferred, expected)
            expected.chmod(mode)
            index_oid = _git(
                materialized,
                "rev-parse",
                f":{relative}",
                classification="authority_invalid_bundle",
            )
            worktree_oid = _git(
                materialized,
                "hash-object",
                f"--path={relative}",
                relative,
                classification="authority_invalid_bundle",
            )
            if worktree_oid != index_oid:
                raise AuthorityError(
                    "authority_dirty_artifact", f"dirty artifact: {relative}"
                )
        elif expected.is_symlink() or transferred.is_symlink():
            if not (expected.is_symlink() and transferred.is_symlink()) or os.readlink(
                expected
            ) != os.readlink(transferred):
                raise AuthorityError("authority_dirty_artifact", f"dirty artifact: {relative}")
        elif expected.is_file() and transferred.is_file():
            if _sha256_file(expected, deadline=deadline) != _sha256_file(
                transferred, deadline=deadline
            ):
                raise AuthorityError("authority_dirty_artifact", f"dirty artifact: {relative}")
        else:
            raise AuthorityError("authority_dirty_artifact", f"artifact type mismatch: {relative}")


def audit_authority(
    source: Path,
    helper: str | Path,
    *,
    timeout: float,
    max_files: int,
    max_bytes: int,
) -> dict[str, Any]:
    """Validate live authority or materialize a portable retained experiment."""

    source = source.resolve()
    if (source / ".git").exists():
        validation = _validate_workspace(source, helper, timeout=timeout)
        return {
            "authority": {"mode": "live", "evidence": [".git", "workspace.json"]},
            "workspace_validation": validation,
        }
    if not (source / RECEIPT_NAME).is_file():
        raise AuthorityError("authority_missing", f"missing {RECEIPT_NAME}")
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="workspace-authority-audit-") as temp:
        root = Path(temp)
        staged = root / "staged"
        materialized = root / "materialized"
        _copy_tree_bounded(
            source,
            staged,
            deadline=deadline,
            max_files=max_files,
            max_bytes=max_bytes,
        )
        receipt = _read_receipt(staged)
        _verify_bundle(staged, materialized, receipt, deadline=deadline)
        document = _read_workspace_document(materialized)
        if document["workspace_id"] != receipt["workspace"]["id"]:
            raise AuthorityError(
                "authority_workspace_mismatch", "workspace_id disagrees with receipt"
            )
        validation = _validate_workspace(
            materialized,
            helper,
            timeout=_remaining(deadline),
        )
        if _sha256_bytes(_canonical_json(validation["graph"])) != receipt["validation"].get(
            "graph_sha256"
        ):
            raise AuthorityError(
                "authority_validation_mismatch",
                "validation graph disagrees with receipt",
            )
        return {
            "authority": {
                "mode": "materialized",
                "evidence": [RECEIPT_NAME, BUNDLE_NAME],
                "head": receipt["workspace"]["head"],
                "publication_ref": PUBLICATION_REF,
                "receipt_sha256": _sha256_file(staged / RECEIPT_NAME, deadline=deadline),
            },
            "workspace_validation": validation,
        }


def _parser() -> argparse.ArgumentParser:
    """Build the small public create/audit process interface."""

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--workspace", type=Path, required=True)
    create.add_argument("--workspace-helper", required=True)
    create.add_argument("--timeout-seconds", type=float, default=60.0)
    audit = commands.add_parser("audit")
    audit.add_argument("--source", type=Path, required=True)
    audit.add_argument("--workspace-helper", required=True)
    audit.add_argument("--timeout-seconds", type=float, default=60.0)
    audit.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    audit.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Execute the authority interface and emit exactly one JSON object."""

    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            authority = create_authority(
                args.workspace,
                args.workspace_helper,
                timeout=args.timeout_seconds,
            )
            payload = {"ok": True, "authority": authority}
        else:
            payload = {
                "ok": True,
                **audit_authority(
                    args.source,
                    args.workspace_helper,
                    timeout=args.timeout_seconds,
                    max_files=args.max_files,
                    max_bytes=args.max_bytes,
                ),
            }
    except AuthorityError as exc:
        payload = {
            "ok": False,
            "classification": "not_auditable",
            "authority": {
                "classification": exc.classification,
                "detail": exc.detail,
                "evidence": [RECEIPT_NAME, BUNDLE_NAME],
            },
        }
        print(_canonical_json(payload).decode("utf-8"), end="")
        print(f"{exc.classification}: {exc.detail}", file=sys.stderr)
        return 2
    print(_canonical_json(payload).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
