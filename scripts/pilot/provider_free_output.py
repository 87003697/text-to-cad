from __future__ import annotations

from pathlib import Path, PurePosixPath
import stat


class OutputPathError(ValueError):
    """A provider-free experiment path is not a physical contained directory."""


def _component(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise OutputPathError(f"unsafe provider-free output path {label}: {value!r}")
    pure = PurePosixPath(value)
    if (
        len(pure.parts) != 1
        or value in {"", ".", ".."}
        or pure.as_posix() != value
    ):
        raise OutputPathError(f"unsafe provider-free output path {label}: {value!r}")
    return value


def _directory(parent: Path, name: str, *, create: bool) -> tuple[Path, bool]:
    candidate = parent / name
    try:
        mode = candidate.lstat().st_mode
        existed = True
    except FileNotFoundError:
        existed = False
        if not create:
            return candidate, False
        try:
            candidate.mkdir()
        except FileExistsError:
            existed = True
        except OSError as exc:
            raise OutputPathError(
                f"cannot create provider-free output path: {candidate}"
            ) from exc
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise OutputPathError(
                f"cannot inspect provider-free output path: {candidate}"
            ) from exc
    except OSError as exc:
        raise OutputPathError(
            f"cannot inspect provider-free output path: {candidate}"
        ) from exc
    if stat.S_ISLNK(mode):
        raise OutputPathError(f"provider-free output path contains symlink: {candidate}")
    if not stat.S_ISDIR(mode):
        raise OutputPathError(
            f"provider-free output path is not a directory: {candidate}"
        )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise OutputPathError(
            f"cannot resolve provider-free output path: {candidate}"
        ) from exc
    if resolved.parent != parent:
        raise OutputPathError(f"provider-free output path escapes parent: {candidate}")
    return resolved, existed


def physical_exp_path(
    repo_root: str | Path,
    group: str,
    exp: str,
    *,
    create_exp: bool,
    require_exp: bool = False,
) -> tuple[Path, bool]:
    """Validate/create the exact physical outputs/group/exp directory chain."""

    group = _component(group, "group")
    exp = _component(exp, "experiment")
    try:
        root = Path(repo_root).resolve(strict=True)
    except OSError as exc:
        raise OutputPathError("provider-free repository root is missing") from exc
    if not root.is_dir():
        raise OutputPathError("provider-free repository root is not a directory")
    outputs, _ = _directory(root, "outputs", create=True)
    group_dir, _ = _directory(outputs, group, create=True)
    exp_dir, existed = _directory(group_dir, exp, create=create_exp)
    if require_exp and not existed:
        raise OutputPathError(f"provider-free experiment directory is missing: {exp_dir}")
    if existed:
        try:
            relative = exp_dir.relative_to(outputs)
        except ValueError as exc:
            raise OutputPathError(
                f"provider-free experiment escapes outputs: {exp_dir}"
            ) from exc
        if relative.parts != (group, exp):
            raise OutputPathError(
                f"provider-free experiment identity conflicts: {exp_dir}"
            )
    return exp_dir, existed


def require_empty_exp_path(repo_root: str | Path, exp_dir: str | Path) -> Path:
    """Require a newly created physical experiment to contain no child entry."""

    physical = revalidate_exp_path(repo_root, exp_dir)
    try:
        children = sorted(child.name for child in physical.iterdir())
    except OSError as exc:
        raise OutputPathError(
            f"cannot inspect new provider-free experiment: {physical}"
        ) from exc
    if children:
        raise OutputPathError(
            "new provider-free experiment is not empty: " + ", ".join(children)
        )
    return revalidate_exp_path(repo_root, physical)


def revalidate_exp_path(repo_root: str | Path, exp_dir: str | Path) -> Path:
    """Revalidate one existing lexical outputs/group/exp path without following it."""

    declared_root = Path(repo_root).absolute()
    root = declared_root.resolve(strict=True)
    requested = Path(exp_dir).absolute()
    try:
        relative = requested.relative_to(declared_root / "outputs")
    except ValueError as exc:
        try:
            relative = requested.relative_to(root / "outputs")
        except ValueError:
            raise OutputPathError(
                f"provider-free output path is outside repository outputs: {requested}"
            ) from exc
    if len(relative.parts) != 2:
        raise OutputPathError(
            f"provider-free output path must identify group/experiment: {requested}"
        )
    physical, _ = physical_exp_path(
        root,
        relative.parts[0],
        relative.parts[1],
        create_exp=False,
        require_exp=True,
    )
    try:
        requested_physical = requested.resolve(strict=True)
    except OSError as exc:
        raise OutputPathError(
            f"provider-free output path changed during validation: {requested}"
        ) from exc
    if physical != requested_physical:
        raise OutputPathError(
            f"provider-free output path is not physical: {requested}"
        )
    return physical
