"""Discover browser artifacts across the exact read-only Agent mount surface."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Iterable


_BROWSER_NAME = re.compile(
    r"^(?:ms-playwright|playwright(?:-core)?|chromium(?:-browser)?(?:[-_.].*)?|"
    r"google-chrome(?:[-_.].*)?|chrome-headless-shell(?:[-_.].*)?)$",
    re.IGNORECASE,
)
_PACKAGE_MARKERS = (
    b"name: playwright\n",
    b"name: playwright-core\n",
    b'"name":"playwright"',
    b'"name": "playwright"',
    b'"name":"playwright-core"',
    b'"name": "playwright-core"',
)
_PRODUCT_MARKERS = (
    b"HeadlessChrome",
    b"Chromium ",
    b"Google Chrome",
    b"chrome_crashpad_handler",
)


class BrowserSurfaceError(RuntimeError):
    """The complete mounted surface could not be inspected fail-closed."""


def _contains_marker(path: Path, markers: tuple[bytes, ...]) -> bool:
    """Scan the complete regular file without following a replacement link."""

    try:
        with path.open("rb") as stream:
            tail = b""
            longest = max(len(marker) for marker in markers)
            while chunk := stream.read(1024 * 1024):
                block = tail + chunk
                if any(marker.lower() in block.lower() for marker in markers):
                    return True
                tail = block[-longest:]
    except OSError as exc:
        raise BrowserSurfaceError("cannot inspect mounted browser surface") from exc
    return False


def _is_elf(path: Path) -> bool:
    """Read only the fixed ELF magic without loading a large executable."""

    try:
        with path.open("rb") as stream:
            return stream.read(4) == b"\x7fELF"
    except OSError as exc:
        raise BrowserSurfaceError("cannot inspect mounted browser surface") from exc


def discover_browser_roots(
    mounts: Iterable[tuple[Path, Path]],
) -> list[dict[str, str]]:
    """Return exact maskable browser roots for source-to-sandbox mounts."""

    findings: dict[str, dict[str, str]] = {}
    for source_root, target_root in mounts:
        source_root = source_root.resolve()
        if not source_root.exists():
            continue
        candidates = [source_root]
        if source_root.is_dir():
            candidates.extend(
                path
                for path in source_root.rglob("*")
                if not path.is_symlink()
            )
        for path in candidates:
            try:
                relative = path.relative_to(source_root)
                target = target_root / relative
                metadata = path.stat(follow_symlinks=False)
            except (OSError, ValueError) as exc:
                raise BrowserSurfaceError(
                    "cannot inspect mounted browser surface"
                ) from exc
            name = path.name.casefold()
            kind: str | None = None
            root = path
            if path.is_dir() and _BROWSER_NAME.fullmatch(name):
                kind = "cache" if "cache" in {part.casefold() for part in path.parts} or name == "ms-playwright" else "package"
            elif path.is_file() and path.name in {"METADATA", "package.json"}:
                if _contains_marker(path, _PACKAGE_MARKERS):
                    kind = "package"
                    root = path.parent
                    target = target.parent
            elif path.is_file() and (
                metadata.st_mode & 0o111 or _is_elf(path)
            ):
                if _BROWSER_NAME.fullmatch(name) or _contains_marker(path, _PRODUCT_MARKERS):
                    kind = "executable"
            elif path.is_file() and "cache" in {part.casefold() for part in path.parts}:
                if _contains_marker(path, _PRODUCT_MARKERS + (b'"product":"chromium"', b'"product": "chromium"')):
                    kind = "cache"
                    root = path.parent
                    target = target.parent
            if kind is None:
                continue
            target_text = target.as_posix()
            findings[target_text] = {
                "kind": kind,
                "target": target_text,
                "mask": "tmpfs" if root.is_dir() else "dev-null",
            }
    return [findings[target] for target in sorted(findings)]
