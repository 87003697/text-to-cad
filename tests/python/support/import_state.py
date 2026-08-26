"""Import-state isolation helpers for repository-owned Python tests."""

from __future__ import annotations

from contextlib import contextmanager
import sys
from typing import Iterator


@contextmanager
def isolated_package_import(package_name: str) -> Iterator[None]:
    """Load one package from the path selected by its importer.

    Test suites may import an editable package before exercising a physically
    materialized shipped runtime. Clear the requested package family for the
    scenario, then restore the complete module mapping and import path so the
    isolation cannot leak across test modules.
    """
    prefix = f"{package_name}."
    saved_modules = sys.modules.copy()
    saved_path = sys.path[:]
    try:
        for name in tuple(sys.modules):
            if name == package_name or name.startswith(prefix):
                sys.modules.pop(name, None)
        yield
    finally:
        sys.modules.clear()
        sys.modules.update(saved_modules)
        sys.path[:] = saved_path
