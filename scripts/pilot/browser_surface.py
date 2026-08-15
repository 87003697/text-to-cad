"""Discover browser artifacts across the exact read-only Agent mount surface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Iterable, Mapping


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
_CACHE_MARKERS = _PRODUCT_MARKERS + (
    b'"product":"chromium"',
    b'"product": "chromium"',
)
_ALL_MARKERS = tuple(marker.lower() for marker in _PACKAGE_MARKERS + _CACHE_MARKERS)
_OPEN_BASE = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)


class BrowserSurfaceError(RuntimeError):
    """The complete mounted surface could not be inspected fail-closed."""


class SurfaceFilesystem:
    """Public OS-boundary adapter for deterministic fail-closed inspection tests."""

    def lstat(self, path: os.PathLike[str] | str, *, dir_fd: int | None = None):
        """Return metadata without following the named entry."""

        return os.stat(path, dir_fd=dir_fd, follow_symlinks=False)

    def open(
        self,
        path: os.PathLike[str] | str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        """Open one exact entry relative to its already-open parent."""

        return os.open(path, flags, dir_fd=dir_fd)

    def fstat(self, descriptor: int):
        """Return metadata for an opened descriptor."""

        return os.fstat(descriptor)

    def scandir(self, descriptor: int):
        """Enumerate one already-open directory descriptor."""

        return os.scandir(descriptor)

    def readlink(
        self,
        path: os.PathLike[str] | str,
        *,
        dir_fd: int | None = None,
    ) -> str:
        """Read one link relative to its already-open parent."""

        return os.readlink(path, dir_fd=dir_fd)

    def read(self, descriptor: int, size: int) -> bytes:
        """Read bytes from one verified regular-file descriptor."""

        return os.read(descriptor, size)

    def close(self, descriptor: int) -> None:
        """Close an owned descriptor."""

        os.close(descriptor)


@dataclass
class _Node:
    """One lstat-observed entry below a declared source root."""

    relative: tuple[str, ...]
    metadata: os.stat_result
    link_target: str | None = None
    is_elf: bool = False
    package_marker: bool = False
    product_marker: bool = False
    cache_marker: bool = False


def _closed(exc: OSError) -> BrowserSurfaceError:
    return BrowserSurfaceError("cannot inspect mounted browser surface")


def _same_inode(before: os.stat_result, after: os.stat_result) -> bool:
    """Require the opened object to be the exact lstat-observed object."""

    return (
        before.st_dev == after.st_dev
        and before.st_ino == after.st_ino
        and before.st_mode == after.st_mode
    )


def _read_markers(
    filesystem: SurfaceFilesystem,
    descriptor: int,
    first: bytes,
) -> tuple[bool, bool, bool]:
    """Stream the complete verified file and detect every fixed marker."""

    found: set[bytes] = set()
    longest = max(len(marker) for marker in _ALL_MARKERS)
    tail = b""
    chunk = first
    while chunk:
        block = (tail + chunk).lower()
        found.update(marker for marker in _ALL_MARKERS if marker in block)
        tail = block[-longest:]
        chunk = filesystem.read(descriptor, 1024 * 1024)
    return (
        any(marker.lower() in found for marker in _PACKAGE_MARKERS),
        any(marker.lower() in found for marker in _PRODUCT_MARKERS),
        any(marker.lower() in found for marker in _CACHE_MARKERS),
    )


def _inspect_file(
    filesystem: SurfaceFilesystem,
    parent_descriptor: int | None,
    name: os.PathLike[str] | str,
    node: _Node,
    *,
    force_complete: bool = False,
) -> None:
    """Inspect one regular file through a no-follow descriptor."""

    descriptor = filesystem.open(name, _OPEN_BASE, dir_fd=parent_descriptor)
    try:
        opened = filesystem.fstat(descriptor)
        if not _same_inode(node.metadata, opened) or not stat.S_ISREG(opened.st_mode):
            raise BrowserSurfaceError("mounted browser surface identity changed")
        first = filesystem.read(descriptor, 4096)
        node.is_elf = first.startswith(b"\x7fELF")
        observed_cache = "cache" in {part.casefold() for part in node.relative}
        complete = (
            force_complete
            or bool(node.metadata.st_mode & 0o111)
            or node.is_elf
            or (
                node.relative
                and node.relative[-1].casefold() in {"metadata", "package.json"}
            )
            or observed_cache
        )
        if complete:
            (
                node.package_marker,
                node.product_marker,
                node.cache_marker,
            ) = _read_markers(filesystem, descriptor, first)
    finally:
        filesystem.close(descriptor)


def _target_path(target_root: Path, relative: tuple[str, ...]) -> Path:
    return target_root.joinpath(*relative) if relative else target_root


def _finding(
    findings: list[dict[str, str]],
    *,
    kind: str,
    target: Path,
    directory: bool,
) -> None:
    target_text = target.as_posix()
    findings.append(
        {
            "kind": kind,
            "target": target_text,
            "mask": "tmpfs" if directory else "dev-null",
        }
    )


def _classify(
    findings: list[dict[str, str]],
    node: _Node,
    observed_relative: tuple[str, ...],
    target_root: Path,
) -> None:
    """Classify one exact node, optionally through a resolved symlink alias."""

    name = (
        observed_relative[-1].casefold()
        if observed_relative
        else target_root.name.casefold()
    )
    target = _target_path(target_root, node.relative)
    cache_parts = {part.casefold() for part in observed_relative}
    if stat.S_ISDIR(node.metadata.st_mode) and _BROWSER_NAME.fullmatch(name):
        kind = (
            "cache"
            if "cache" in cache_parts or name == "ms-playwright"
            else "package"
        )
        _finding(findings, kind=kind, target=target, directory=True)
    elif stat.S_ISREG(node.metadata.st_mode) and name in {"metadata", "package.json"}:
        if node.package_marker:
            _finding(findings, kind="package", target=target.parent, directory=True)
    elif stat.S_ISREG(node.metadata.st_mode) and (
        node.metadata.st_mode & 0o111 or node.is_elf
    ):
        if _BROWSER_NAME.fullmatch(name) or node.product_marker:
            _finding(findings, kind="executable", target=target, directory=False)
    elif stat.S_ISREG(node.metadata.st_mode) and "cache" in cache_parts:
        if node.cache_marker:
            _finding(findings, kind="cache", target=target.parent, directory=True)


def _inside_root(root: str, candidate: str) -> bool:
    try:
        return os.path.commonpath((root, candidate)) == root
    except ValueError:
        return False


def _link_destination(
    source_root: str,
    link_relative: tuple[str, ...],
    link_target: str,
    suffix: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Resolve link text lexically while refusing any declared-root escape."""

    candidate = _absolute_link_destination(
        source_root, link_relative, link_target, suffix
    )
    if not _inside_root(source_root, candidate):
        raise BrowserSurfaceError("mounted browser surface symlink escapes root")
    relative = os.path.relpath(candidate, source_root)
    return () if relative == "." else tuple(Path(relative).parts)


def _absolute_link_destination(
    source_root: str,
    link_relative: tuple[str, ...],
    link_target: str,
    suffix: tuple[str, ...] = (),
) -> str:
    """Return one normalized absolute destination without following the link."""

    if os.path.isabs(link_target):
        candidate = os.path.normpath(os.path.join(link_target, *suffix))
    else:
        parent = os.path.join(source_root, *link_relative[:-1])
        candidate = os.path.normpath(os.path.join(parent, link_target, *suffix))
    return candidate


def _resolve_permitted_external_alias(
    source_root: str,
    link: _Node,
    permitted_roots: tuple[str, ...],
    filesystem: SurfaceFilesystem,
) -> _Node:
    """Resolve one immutable cross-root alias into the declared root closure."""

    assert link.link_target is not None
    candidate = _absolute_link_destination(
        source_root, link.relative, link.link_target
    )
    try:
        resolved = os.path.realpath(candidate, strict=True)
    except (OSError, RuntimeError) as exc:
        raise BrowserSurfaceError(
            "mounted browser surface external symlink is unresolved"
        ) from exc
    if not any(_inside_root(root, resolved) for root in permitted_roots):
        raise BrowserSurfaceError(
            "mounted browser surface external symlink escapes declared roots"
        )
    try:
        metadata = filesystem.lstat(resolved)
    except OSError as exc:
        raise _closed(exc) from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise BrowserSurfaceError(
            "mounted browser surface external symlink identity changed"
        )
    return _Node(link.relative, metadata)


def _resolve_link(
    source_root: str,
    link: _Node,
    nodes: dict[tuple[str, ...], _Node],
) -> _Node:
    """Resolve every in-root link component with dangling/cycle detection."""

    assert link.link_target is not None
    pending = _link_destination(source_root, link.relative, link.link_target)
    seen = {link.relative}
    while True:
        replaced = False
        for index in range(len(pending)):
            prefix = pending[: index + 1]
            target = nodes.get(prefix)
            if target is None:
                raise BrowserSurfaceError("mounted browser surface symlink is dangling")
            if target.link_target is None:
                continue
            if prefix in seen:
                raise BrowserSurfaceError("mounted browser surface symlink cycle")
            seen.add(prefix)
            pending = _link_destination(
                source_root,
                prefix,
                target.link_target,
                pending[index + 1 :],
            )
            replaced = True
            break
        if replaced:
            continue
        target = nodes.get(pending)
        if target is None:
            raise BrowserSurfaceError("mounted browser surface symlink is dangling")
        if stat.S_ISDIR(target.metadata.st_mode):
            parent = link.relative[:-1]
            if (
                len(target.relative) <= len(parent)
                and parent[: len(target.relative)] == target.relative
            ):
                raise BrowserSurfaceError("mounted browser surface symlink cycle")
        return target


def _reject_graph_cycles(
    nodes: dict[tuple[str, ...], _Node],
    resolved_links: dict[tuple[str, ...], tuple[str, ...]],
) -> None:
    """Reject cycles formed by directory containment plus resolved link edges."""

    children: dict[tuple[str, ...], list[tuple[str, ...]]] = {}
    for relative in nodes:
        if relative:
            children.setdefault(relative[:-1], []).append(relative)

    colors: dict[tuple[str, ...], int] = {}

    def visit(relative: tuple[str, ...]) -> None:
        color = colors.get(relative, 0)
        if color == 1:
            raise BrowserSurfaceError("mounted browser surface symlink cycle")
        if color == 2:
            return
        colors[relative] = 1
        node = nodes[relative]
        edges: list[tuple[str, ...]] = []
        if stat.S_ISDIR(node.metadata.st_mode):
            edges.extend(sorted(children.get(relative, ())))
        if relative in resolved_links:
            edges.append(resolved_links[relative])
        for target in edges:
            visit(target)
        colors[relative] = 2

    visit(())


def _walk_mount(
    source_root: Path,
    target_root: Path,
    required: bool,
    filesystem: SurfaceFilesystem,
    findings: list[dict[str, str]],
    permitted_symlink_roots: tuple[str, ...],
) -> None:
    """Walk one declared root with directory descriptors and no implicit links."""

    source_text = os.path.abspath(os.fspath(source_root))
    nodes: dict[tuple[str, ...], _Node] = {}

    def inspect_resolved_file(node: _Node, *, force_complete: bool) -> None:
        """Reopen an already-enumerated target through verified directory fds."""

        if not node.relative:
            _inspect_file(
                filesystem,
                None,
                source_text,
                node,
                force_complete=force_complete,
            )
            return
        owned: list[int] = []
        try:
            descriptor = filesystem.open(
                source_text, _OPEN_BASE | getattr(os, "O_DIRECTORY", 0)
            )
            owned.append(descriptor)
            root_opened = filesystem.fstat(descriptor)
            if not _same_inode(nodes[()].metadata, root_opened):
                raise BrowserSurfaceError("mounted browser surface identity changed")
            prefix: tuple[str, ...] = ()
            for component in node.relative[:-1]:
                prefix += (component,)
                descriptor = filesystem.open(
                    component,
                    _OPEN_BASE | getattr(os, "O_DIRECTORY", 0),
                    dir_fd=owned[-1],
                )
                owned.append(descriptor)
                opened = filesystem.fstat(descriptor)
                expected = nodes[prefix].metadata
                if not _same_inode(expected, opened) or not stat.S_ISDIR(opened.st_mode):
                    raise BrowserSurfaceError("mounted browser surface identity changed")
            _inspect_file(
                filesystem,
                owned[-1],
                node.relative[-1],
                node,
                force_complete=force_complete,
            )
        finally:
            for descriptor in reversed(owned):
                filesystem.close(descriptor)

    def inspect_entry(
        parent_descriptor: int | None,
        name: os.PathLike[str] | str,
        relative: tuple[str, ...],
        metadata: os.stat_result,
    ) -> _Node:
        node = _Node(relative, metadata)
        nodes[relative] = node
        if stat.S_ISLNK(metadata.st_mode):
            node.link_target = filesystem.readlink(name, dir_fd=parent_descriptor)
            return node
        if stat.S_ISREG(metadata.st_mode):
            return node
        if not stat.S_ISDIR(metadata.st_mode):
            return node
        flags = _OPEN_BASE | getattr(os, "O_DIRECTORY", 0)
        descriptor = filesystem.open(name, flags, dir_fd=parent_descriptor)
        try:
            opened = filesystem.fstat(descriptor)
            if not _same_inode(metadata, opened) or not stat.S_ISDIR(opened.st_mode):
                raise BrowserSurfaceError("mounted browser surface identity changed")
            with filesystem.scandir(descriptor) as entries:
                names = sorted(entry.name for entry in entries)
            for child_name in names:
                child_metadata = filesystem.lstat(child_name, dir_fd=descriptor)
                inspect_entry(
                    descriptor,
                    child_name,
                    relative + (child_name,),
                    child_metadata,
                )
        finally:
            filesystem.close(descriptor)
        return node

    try:
        root_metadata = filesystem.lstat(source_text)
    except FileNotFoundError:
        if required:
            raise
        return
    root = inspect_entry(None, source_text, (), root_metadata)
    if root.link_target is not None:
        raise BrowserSurfaceError("declared browser surface root is a symlink")

    resolved_links: dict[tuple[str, ...], tuple[str, ...]] = {}
    resolved_aliases: list[tuple[tuple[str, ...], _Node]] = []
    force_complete: set[tuple[str, ...]] = set()
    for relative in sorted(
        (path for path, node in nodes.items() if node.link_target is not None)
    ):
        link = nodes[relative]
        candidate = _absolute_link_destination(
            source_text, link.relative, link.link_target or ""
        )
        if not _inside_root(source_text, candidate):
            if not permitted_symlink_roots:
                raise BrowserSurfaceError(
                    "mounted browser surface symlink escapes root"
                )
            alias = _resolve_permitted_external_alias(
                source_text, link, permitted_symlink_roots, filesystem
            )
            _classify(findings, alias, relative, target_root)
            continue
        target = _resolve_link(source_text, link, nodes)
        resolved_links[relative] = target.relative
        resolved_aliases.append((relative, target))
        alias_name = relative[-1].casefold()
        alias_cache = "cache" in {part.casefold() for part in relative}
        if stat.S_ISREG(target.metadata.st_mode) and (
            alias_name in {"metadata", "package.json"}
            or alias_cache
        ):
            force_complete.add(target.relative)
    _reject_graph_cycles(nodes, resolved_links)

    for relative in sorted(nodes):
        node = nodes[relative]
        if stat.S_ISREG(node.metadata.st_mode):
            inspect_resolved_file(
                node,
                force_complete=relative in force_complete,
            )
        if node.link_target is None:
            _classify(findings, node, relative, target_root)
    for relative, target in resolved_aliases:
        _classify(findings, target, relative, target_root)


def canonicalize_browser_masks(
    findings: Iterable[Mapping[str, str]],
) -> list[dict[str, str]]:
    """Return one deterministic shortest-directory antichain of exact masks."""

    unique: dict[str, dict[str, str]] = {}
    for finding in findings:
        candidate = {
            "kind": finding["kind"],
            "target": finding["target"],
            "mask": finding["mask"],
        }
        current = unique.get(candidate["target"])
        rank = (candidate["mask"] != "tmpfs", candidate["kind"])
        current_rank = (
            (current["mask"] != "tmpfs", current["kind"])
            if current is not None
            else None
        )
        if current_rank is None or rank < current_rank:
            unique[candidate["target"]] = candidate

    ordered = sorted(
        unique.values(),
        key=lambda item: (
            len(PurePosixPath(item["target"]).parts),
            item["target"],
            item["mask"] != "tmpfs",
            item["kind"],
        ),
    )
    selected: list[dict[str, str]] = []
    for candidate in ordered:
        target = PurePosixPath(candidate["target"])
        if any(
            selected_item["mask"] == "tmpfs"
            and (
                PurePosixPath(selected_item["target"]) == target
                or PurePosixPath(selected_item["target"]) in target.parents
            )
            for selected_item in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda item: item["target"])


def discover_browser_roots(
    mounts: Iterable[tuple[Path, Path, bool]],
    *,
    filesystem: SurfaceFilesystem | None = None,
    permitted_symlink_roots: Iterable[Path] = (),
) -> list[dict[str, str]]:
    """Return exact masks, optionally closing immutable cross-root aliases."""

    adapter = filesystem or SurfaceFilesystem()
    permitted = tuple(
        sorted(
            {
                os.path.realpath(os.path.abspath(os.fspath(root)), strict=True)
                for root in permitted_symlink_roots
            }
        )
    )
    findings: list[dict[str, str]] = []
    for source_root, target_root, required in mounts:
        if not isinstance(required, bool):
            raise BrowserSurfaceError("mounted browser surface requiredness is invalid")
        try:
            _walk_mount(
                Path(source_root),
                Path(target_root),
                required,
                adapter,
                findings,
                permitted,
            )
        except BrowserSurfaceError:
            raise
        except OSError as exc:
            raise _closed(exc) from exc
    return canonicalize_browser_masks(findings)
