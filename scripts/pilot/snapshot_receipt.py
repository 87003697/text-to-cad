#!/usr/bin/env python3
"""Create and verify a complete digest receipt for one shipped snapshot tree."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


RECEIPT_NAME = "snapshot-receipt.json"
RECEIPT_SCHEMA = "pilot.shipped-tree-receipt/1"


class SnapshotReceiptError(RuntimeError):
    """The shipped tree cannot be represented or does not match its receipt."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _source_head(root: Path) -> str:
    try:
        value = (root / "HEAD.sha").read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SnapshotReceiptError("snapshot is missing HEAD.sha") from exc
    if len(value) not in {40, 64}:
        raise SnapshotReceiptError("snapshot HEAD.sha is not a Git object identity")
    try:
        int(value, 16)
    except ValueError as exc:
        raise SnapshotReceiptError("snapshot HEAD.sha is not hexadecimal") from exc
    return value.lower()


def build_receipt(root: str | Path) -> dict[str, Any]:
    """Digest every regular shipped file except the receipt itself."""

    snapshot_root = Path(root).resolve()
    if not snapshot_root.is_dir():
        raise SnapshotReceiptError(f"snapshot root is not a directory: {snapshot_root}")
    files: list[dict[str, Any]] = []
    for path in sorted(snapshot_root.rglob("*")):
        relative = path.relative_to(snapshot_root).as_posix()
        if relative == RECEIPT_NAME:
            continue
        if path.is_symlink():
            raise SnapshotReceiptError(
                f"snapshot shipped tree contains an unresolved symlink: {relative}"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise SnapshotReceiptError(
                f"snapshot shipped tree contains an unsupported path: {relative}"
            )
        data = path.read_bytes()
        files.append(
            {
                "path": relative,
                "size_bytes": len(data),
                "sha256": _sha256(data),
            }
        )
    identity_bytes = json.dumps(
        files,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "schema": RECEIPT_SCHEMA,
        "source_head": _source_head(snapshot_root),
        "file_count": len(files),
        "total_bytes": sum(item["size_bytes"] for item in files),
        "tree_sha256": _sha256(identity_bytes),
        "files": files,
    }


def write_receipt(root: str | Path) -> dict[str, Any]:
    snapshot_root = Path(root).resolve()
    receipt = build_receipt(snapshot_root)
    output = snapshot_root / RECEIPT_NAME
    temporary = snapshot_root / f".{RECEIPT_NAME}.tmp"
    temporary.write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    return receipt


def verify_receipt(root: str | Path) -> dict[str, Any]:
    snapshot_root = Path(root).resolve()
    try:
        receipt = json.loads(
            (snapshot_root / RECEIPT_NAME).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotReceiptError("snapshot receipt is missing or invalid") from exc
    expected = build_receipt(snapshot_root)
    if receipt != expected:
        raise SnapshotReceiptError("shipped tree does not match snapshot receipt")
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("write", "check"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    try:
        receipt = (
            write_receipt(args.root)
            if args.command == "write"
            else verify_receipt(args.root)
        )
    except SnapshotReceiptError as exc:
        parser.exit(1, f"snapshot receipt failed: {exc}\n")
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
