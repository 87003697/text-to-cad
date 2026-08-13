#!/usr/bin/env python3
"""Synchronize the one frozen provider-free Python runtime dependency on CVM."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from scripts.pilot import cvm_playwright_runtime as runtime
from scripts.pilot.cvm_push import CommandRunner, REPO_ROOT


RECEIPT_SCHEMA = "cvm-playwright-runtime-sync.receipt/1"
INSTALL_COMMAND = (
    "cd ~/text-to-cad && "
    "./.venv/bin/python -m pip install --disable-pip-version-check "
    "--no-input --no-deps --upgrade --force-reinstall playwright==1.60.0"
)


def _receipt(before: str, after: str, status: int) -> dict[str, object]:
    return {
        "schema": RECEIPT_SCHEMA,
        "requested_identity": runtime.requested_identity(),
        "before": before,
        "after": after,
        "exit_status": status,
    }


def _probe(runner, repo_root: Path):
    result = runner.remote(runtime.probe_command(), cwd=repo_root, check=False)
    if result.returncode != 0:
        raise ValueError("identity probe failed")
    return runtime.parse_identity(result.stdout)


def execute(runner, *, repo_root: Path = REPO_ROOT) -> int:
    """Run the fixed sync and emit exactly one sanitized terminal receipt."""

    before_status = "not_checked"
    after_status = "not_run"
    status = 1
    try:
        before = _probe(runner, repo_root)
        before_status = "matched" if before.matched else "mismatched"
        if before.matched:
            after = _probe(runner, repo_root)
            after_status = "matched" if (
                after.matched
                and after.browser_sha256 == before.browser_sha256
            ) else "mismatched"
            status = 0 if after_status == "matched" else 1
        elif before.browser_sha256 is not None:
            installed = runner.remote(
                INSTALL_COMMAND,
                cwd=repo_root,
                check=False,
            )
            if installed.returncode == 0:
                after = _probe(runner, repo_root)
                after_status = "matched" if (
                    after.matched
                    and after.browser_sha256 == before.browser_sha256
                ) else "mismatched"
                status = 0 if after_status == "matched" else 1
    except Exception:
        status = 1
    print(
        json.dumps(
            _receipt(before_status, after_status, status),
            separators=(",", ":"),
            sort_keys=True,
        )
    )
    return status


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return argparse.ArgumentParser(
        description=(
            "Synchronize the fixed provider-free Playwright Python runtime on CVM."
        )
    ).parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    parse_args(argv)
    return execute(CommandRunner())


if __name__ == "__main__":
    raise SystemExit(main())
