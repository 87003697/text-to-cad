"""Closed identity contract for the CVM provider-free Playwright runtime."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Mapping


PLAYWRIGHT_DISTRIBUTION = "playwright"
PLAYWRIGHT_VERSION = "1.60.0"
HEADLESS_SHELL_NAME = "chromium-headless-shell"
HEADLESS_SHELL_REVISION = "1223"
IDENTITY_SCHEMA = "cvm.playwright-runtime-identity/1"
REMOTE_ROOT = "~/text-to-cad"


@dataclass(frozen=True)
class PlaywrightRuntimeIdentity:
    """Sanitized result of one remote identity probe."""

    matched: bool
    browser_sha256: str | None


def _probe_source() -> str:
    # This probe is embedded because cvm-push must diagnose an old remote
    # checkout before transferring the source that contains this module.
    return "\n".join(
        (
            "import hashlib, importlib.metadata as metadata, json, os, stat",
            "from pathlib import Path",
            f"schema = {IDENTITY_SCHEMA!r}",
            "browser_digest = None",
            "try:",
            "    browser = (Path.home() / '.cache/ms-playwright/'"
            f" / {('chromium_headless_shell-' + HEADLESS_SHELL_REVISION)!r}"
            " / 'chrome-headless-shell-linux64/chrome-headless-shell')",
            "    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)",
            "    descriptor = os.open(browser, flags)",
            "    try:",
            "        info = os.fstat(descriptor)",
            "        if not stat.S_ISREG(info.st_mode) or not (info.st_mode & 0o111):",
            "            raise ValueError('browser identity')",
            "        digest = hashlib.sha256()",
            "        while True:",
            "            chunk = os.read(descriptor, 1024 * 1024)",
            "            if not chunk:",
            "                break",
            "            digest.update(chunk)",
            "        browser_digest = digest.hexdigest()",
            "    finally:",
            "        os.close(descriptor)",
            "except Exception:",
            "    browser_digest = None",
            "package_matches = False",
            "try:",
            f"    version = metadata.version({PLAYWRIGHT_DISTRIBUTION!r})",
            (
                "    distribution = metadata.distribution("
                f"{PLAYWRIGHT_DISTRIBUTION!r})"
            ),
            (
                "    manifest = distribution.locate_file("
                "'playwright/driver/package/browsers.json')"
            ),
            "    raw = Path(manifest).read_bytes()",
            "    if len(raw) > 1024 * 1024:",
            "        raise ValueError('manifest size')",
            "    def strict_object(pairs):",
            "        value = {}",
            "        for key, item in pairs:",
            "            if key in value:",
            "                raise ValueError('duplicate JSON key')",
            "            value[key] = item",
            "        return value",
            (
                "    payload = json.loads(raw.decode('utf-8'), "
                "object_pairs_hook=strict_object)"
            ),
            (
                "    browsers = payload.get('browsers') "
                "if isinstance(payload, dict) else None"
            ),
            "    if not isinstance(browsers, list):",
            "        raise ValueError('browser manifest')",
            (
                "    entries = [entry for entry in browsers "
                "if isinstance(entry, dict) and entry.get('name') == "
                f"{HEADLESS_SHELL_NAME!r}]"
            ),
            (
                f"    package_matches = version == {PLAYWRIGHT_VERSION!r} "
                "and len(entries) == 1 and entries[0].get('revision') == "
                f"{HEADLESS_SHELL_REVISION!r}"
            ),
            "except Exception:",
            "    package_matches = False",
            (
                "print(json.dumps({'schema': schema, 'matched': package_matches "
                "and browser_digest is not None, 'browser_sha256': "
                "browser_digest}, separators=(',', ':'), sort_keys=True))"
            ),
        )
    )


def probe_command() -> str:
    """Return the fixed, input-free remote identity probe command."""

    return (
        f"cd {REMOTE_ROOT} && "
        f"./.venv/bin/python -I -c {shlex.quote(_probe_source())}"
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def parse_identity(raw: str) -> PlaywrightRuntimeIdentity:
    """Parse only the closed identity vocabulary; reject ambiguous evidence."""

    try:
        payload = json.loads(raw, object_pairs_hook=_strict_object)
    except (json.JSONDecodeError, UnicodeError, ValueError) as exc:
        raise ValueError("malformed Playwright runtime identity") from exc
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema",
        "matched",
        "browser_sha256",
    }:
        raise ValueError("malformed Playwright runtime identity")
    matched = payload["matched"]
    digest = payload["browser_sha256"]
    if payload["schema"] != IDENTITY_SCHEMA or not isinstance(matched, bool):
        raise ValueError("malformed Playwright runtime identity")
    if digest is not None:
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("malformed Playwright runtime identity")
        try:
            int(digest, 16)
        except ValueError as exc:
            raise ValueError("malformed Playwright runtime identity") from exc
    if matched and digest is None:
        raise ValueError("malformed Playwright runtime identity")
    return PlaywrightRuntimeIdentity(matched=matched, browser_sha256=digest)


def requested_identity() -> dict[str, str]:
    """Return the sole public requested identity for the sync receipt."""

    return {
        "distribution": PLAYWRIGHT_DISTRIBUTION,
        "version": PLAYWRIGHT_VERSION,
        "browser": HEADLESS_SHELL_NAME,
        "revision": HEADLESS_SHELL_REVISION,
    }
