#!/usr/bin/env python3
"""PROTOTYPE ONLY: compare Playwright launch with Python prelaunch + CDP."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator
from unittest import mock

from PIL import Image


PROTOTYPE_SCHEMA = "meshshot.prelaunched-cdp-prototype/1"
ADAPTER_PROFILE = "playwright-1.60-defaults-loopback-cdp/1"
ORIGIN = "http://meshshot.local"
STARTUP_DEADLINE_SECONDS = 15.0
RENDER_DEADLINE_SECONDS = 120.0
CLEANUP_TERM_SECONDS = 5.0
CLEANUP_KILL_SECONDS = 2.0
EXPECTED_VIEWS = ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"]

# Exact Playwright v1.60.0 chromiumSwitches.ts feature order. This prototype's
# digest uses placeholders for volatile CDP/profile values and never publishes
# the raw argv.
DISABLED_FEATURES = [
    "AvoidUnnecessaryBeforeUnloadCheckSync",
    "BoundaryEventDispatchTracksNodeRemoval",
    "DestroyProfileOnBrowserClose",
    "DialMediaRouteProvider",
    "GlobalMediaControls",
    "HttpsUpgrades",
    "LensOverlay",
    "MediaRouter",
    "PaintHolding",
    "ThirdPartyStoragePartitioning",
    "Translate",
    "AutoDeElevate",
    "RenderDocument",
    "OptimizationHints",
    "msForceBrowserSignIn",
    "msEdgeUpdateLaunchServicesPreferredVersion",
]
CHROMIUM_SWITCHES = [
    "--disable-field-trial-config",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-back-forward-cache",
    "--disable-breakpad",
    "--disable-client-side-phishing-detection",
    "--disable-component-extensions-with-background-pages",
    "--disable-component-update",
    "--no-default-browser-check",
    "--disable-default-apps",
    "--disable-dev-shm-usage",
    "--disable-edgeupdater",
    "--disable-extensions",
    "--disable-features=" + ",".join(DISABLED_FEATURES),
    "--enable-features=CDPScreenshotNewSurface",
    "--allow-pre-commit-input",
    "--disable-hang-monitor",
    "--disable-ipc-flooding-protection",
    "--disable-popup-blocking",
    "--disable-prompt-on-repost",
    "--disable-renderer-backgrounding",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--no-first-run",
    "--password-store=basic",
    "--use-mock-keychain",
    "--no-service-autorun",
    "--export-tagged-pdf",
    "--disable-search-engine-choice-screen",
    "--unsafely-disable-devtools-self-xss-warnings",
    "--edge-skip-compat-layer-relaunch",
    "--disable-infobars",
    "--disable-search-engine-choice-screen",
    "--disable-sync",
]
HEADLESS_SWITCHES = [
    "--enable-unsafe-swiftshader",
    "--headless",
    "--hide-scrollbars",
    "--mute-audio",
    (
        "--blink-settings=primaryHoverType=2,availableHoverTypes=2,"
        "primaryPointerType=4,availablePointerTypes=4"
    ),
    "--no-sandbox",
    # Existing meshshot also supplies this as a launch arg. Preserve adapter
    # A's exact effective profile, including the harmless duplicate.
    "--no-sandbox",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _install_repo_imports() -> None:
    meshshot_src = _repo_root() / "packages/meshshot/src"
    sys.path.insert(0, os.fspath(meshshot_src))


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_digest(value: Any) -> str:
    return _sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@contextmanager
def _wall_deadline(seconds: float) -> Iterator[None]:
    """Bound the whole synchronous render, including page.evaluate()."""

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def expired(_signum: int, _frame: Any) -> None:
        raise TimeoutError("prototype render deadline exceeded")

    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _browser_identity() -> tuple[Path, dict[str, str]]:
    import playwright

    package = Path(playwright.__file__).resolve().parent
    browsers_json = package / "driver/package/browsers.json"
    manifest = json.loads(browsers_json.read_text(encoding="utf-8"))
    entries = [
        item
        for item in manifest["browsers"]
        if item.get("name") == "chromium-headless-shell"
    ]
    if len(entries) != 1:
        raise RuntimeError("expected one Playwright headless-shell manifest entry")
    entry = entries[0]
    revision = str(entry["revision"])
    cache_root = Path(
        os.environ.get(
            "PLAYWRIGHT_BROWSERS_PATH",
            Path.home() / "Library/Caches/ms-playwright",
        )
    )
    candidates = list(
        (cache_root / f"chromium_headless_shell-{revision}").glob(
            "chrome-headless-shell-*/chrome-headless-shell"
        )
    )
    if len(candidates) != 1:
        raise RuntimeError("exact Playwright headless shell is not uniquely installed")
    executable = candidates[0].resolve(strict=True)
    mode = executable.lstat().st_mode
    if not stat.S_ISREG(mode) or not os.access(executable, os.X_OK):
        raise RuntimeError("exact Playwright headless shell is not executable")
    version = subprocess.run(
        [os.fspath(executable), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=STARTUP_DEADLINE_SECONDS,
    ).stdout.strip()
    return executable, {
        "playwright": "1.60.0",
        "browser": "chromium-headless-shell",
        "revision": revision,
        "version": version,
        "sha256": _sha256(executable.read_bytes()),
    }


@dataclass
class Observations:
    adapter: str
    launch_started: bool = False
    startup_seconds: float | None = None
    readiness: bool = False
    route_registered: bool = False
    fulfilled: list[dict[str, Any]] = field(default_factory=list)
    console_events: list[str] = field(default_factory=list)
    page_evaluate: bool = False
    outside_request_rejected: bool = False
    process_reaped: bool | None = None
    process_group_empty: bool | None = None
    profile_removed: bool | None = None
    cleanup_seconds: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "adapter": self.adapter,
            "launch_started": self.launch_started,
            "startup_seconds": self.startup_seconds,
            "readiness": self.readiness,
            "route_registered": self.route_registered,
            "fulfilled": self.fulfilled,
            "console_events": self.console_events,
            "page_evaluate": self.page_evaluate,
            "outside_request_rejected": self.outside_request_rejected,
            "release": {
                "process_reaped": self.process_reaped,
                "process_group_empty": self.process_group_empty,
                "profile_removed": self.profile_removed,
                "cleanup_seconds": self.cleanup_seconds,
            },
        }


class RouteProbe:
    def __init__(self, route: Any, observations: Observations) -> None:
        self._route = route
        self._observations = observations

    @property
    def request(self) -> Any:
        return self._route.request

    def fulfill(self, **kwargs: Any) -> Any:
        url = str(self._route.request.url)
        path = url.removeprefix(ORIGIN) if url.startswith(ORIGIN) else "outside"
        self._observations.fulfilled.append(
            {"path": path, "status": int(kwargs.get("status", 200))}
        )
        return self._route.fulfill(**kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._route, name)


class PageProbe:
    def __init__(self, page: Any, observations: Observations) -> None:
        self._page = page
        self._observations = observations

    def on(self, event: str, callback: Any) -> Any:
        if event != "console":
            return self._page.on(event, callback)

        def wrapped(message: Any) -> None:
            text = str(message.text)
            if text.startswith("meshshot-stage:"):
                self._observations.console_events.append(text)
            callback(message)

        return self._page.on(event, wrapped)

    def route(self, pattern: str, handler: Any) -> Any:
        self._observations.route_registered = True

        def wrapped(route: Any) -> None:
            handler(RouteProbe(route, self._observations))

        return self._page.route(pattern, wrapped)

    def evaluate(self, expression: str, *args: Any) -> Any:
        self._observations.page_evaluate = True
        result = self._page.evaluate(expression, *args)
        rejected = self._page.evaluate(
            """async () => {
              try {
                await fetch('https://meshshot.invalid/prototype-network-probe');
                return false;
              } catch (_) {
                return true;
              }
            }"""
        )
        self._observations.outside_request_rejected = rejected is True
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._page, name)


class ContextProbe:
    def __init__(self, context: Any, observations: Observations) -> None:
        self._context = context
        self._observations = observations

        def reject_outside(route: Any) -> None:
            if str(route.request.url).startswith(ORIGIN):
                route.continue_()
            else:
                self._observations.outside_request_rejected = True
                route.abort("blockedbyclient")

        context.route("**/*", reject_outside)

    def new_page(self) -> PageProbe:
        return PageProbe(self._context.new_page(), self._observations)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._context, name)


class BrowserProbe:
    def __init__(self, browser: Any, observations: Observations) -> None:
        self._browser = browser
        self._observations = observations

    def new_context(self, **kwargs: Any) -> ContextProbe:
        return ContextProbe(self._browser.new_context(**kwargs), self._observations)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)


class PythonPrelauncher:
    def __init__(self, executable: Path, observations: Observations) -> None:
        self.executable = executable
        self.observations = observations
        self.profile: Path | None = None
        self.process: subprocess.Popen[bytes] | None = None
        self.process_group: int | None = None

    def launch(self) -> str:
        started = time.monotonic()
        self.observations.launch_started = True
        self.profile = Path(tempfile.mkdtemp(prefix="meshshot-cdp-prototype-"))
        argv = [
            os.fspath(self.executable),
            *CHROMIUM_SWITCHES,
            *HEADLESS_SWITCHES,
            f"--user-data-dir={self.profile}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=0",
            "about:blank",
        ]
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.process_group = os.getpgid(self.process.pid)
        active_port = self.profile / "DevToolsActivePort"
        deadline = started + STARTUP_DEADLINE_SECONDS
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                detail = self.process.stderr.read(2048) if self.process.stderr else b""
                raise RuntimeError(
                    "prelaunched browser exited before readiness: "
                    + detail.decode("utf-8", "replace")
                )
            try:
                lines = active_port.read_text(encoding="utf-8").splitlines()
            except (FileNotFoundError, PermissionError):
                time.sleep(0.02)
                continue
            if len(lines) >= 2 and lines[0].isdigit():
                port = int(lines[0])
                if 0 < port < 65536 and lines[1].startswith("/devtools/browser/"):
                    self.observations.readiness = True
                    self.observations.startup_seconds = round(
                        time.monotonic() - started, 6
                    )
                    return f"http://127.0.0.1:{port}"
            time.sleep(0.02)
        raise TimeoutError("prelaunched browser readiness deadline exceeded")

    def cleanup(self) -> None:
        started = time.monotonic()
        process = self.process
        group = self.process_group
        try:
            if group is not None:
                try:
                    os.killpg(group, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            # Reap the group leader promptly. A terminated-but-unreaped leader
            # still makes killpg(group, 0) look live on macOS.
            if process is not None:
                try:
                    process.wait(timeout=CLEANUP_TERM_SECONDS)
                except subprocess.TimeoutExpired:
                    if group is not None:
                        try:
                            os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=CLEANUP_KILL_SECONDS)
                self.observations.process_reaped = process.poll() is not None
            if group is not None:
                deadline = time.monotonic() + CLEANUP_KILL_SECONDS
                while time.monotonic() < deadline and not _group_is_empty(group):
                    time.sleep(0.02)
            self.observations.process_group_empty = (
                True if group is None else _group_is_empty(group)
            )
        finally:
            if self.profile is not None:
                shutil.rmtree(self.profile, ignore_errors=False)
                self.observations.profile_removed = not self.profile.exists()
            self.observations.cleanup_seconds = round(time.monotonic() - started, 6)


def _group_is_empty(group: int) -> bool:
    try:
        os.killpg(group, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


class ChromiumAdapter:
    def __init__(
        self,
        chromium: Any,
        executable: Path,
        observations: Observations,
        mode: str,
        fail_after_readiness: bool,
    ) -> None:
        self._chromium = chromium
        self._executable = executable
        self._observations = observations
        self._mode = mode
        self._fail_after_readiness = fail_after_readiness
        self._prelauncher: PythonPrelauncher | None = None

    @property
    def executable_path(self) -> str:
        return self._chromium.executable_path

    def launch(self, **options: Any) -> BrowserProbe:
        self._observations.launch_started = True
        started = time.monotonic()
        if self._mode == "playwright-launch":
            browser = self._chromium.launch(**options)
            self._observations.readiness = True
            self._observations.startup_seconds = round(time.monotonic() - started, 6)
            return BrowserProbe(browser, self._observations)
        if Path(options["executable_path"]).resolve() != self._executable:
            raise RuntimeError("adapter B did not receive the attested executable")
        self._prelauncher = PythonPrelauncher(self._executable, self._observations)
        endpoint = self._prelauncher.launch()
        if self._fail_after_readiness:
            raise RuntimeError("intentional prototype failure after CDP readiness")
        remaining_ms = max(
            1,
            int(
                (STARTUP_DEADLINE_SECONDS - (time.monotonic() - started))
                * 1000
            ),
        )
        browser = self._chromium.connect_over_cdp(
            endpoint,
            timeout=remaining_ms,
            is_local=True,
        )
        return BrowserProbe(browser, self._observations)

    def cleanup(self) -> None:
        if self._prelauncher is not None:
            self._prelauncher.cleanup()


class PlaywrightAdapter:
    def __init__(
        self,
        real_factory: Any,
        executable: Path,
        observations: Observations,
        mode: str,
        fail_after_readiness: bool = False,
    ) -> None:
        self._real_factory = real_factory
        self._executable = executable
        self._observations = observations
        self._mode = mode
        self._fail_after_readiness = fail_after_readiness
        self._context_manager: Any = None
        self._chromium_adapter: ChromiumAdapter | None = None

    def __enter__(self) -> Any:
        self._context_manager = self._real_factory()
        playwright = self._context_manager.__enter__()
        self._chromium_adapter = ChromiumAdapter(
            playwright.chromium,
            self._executable,
            self._observations,
            self._mode,
            self._fail_after_readiness,
        )
        return mock.Mock(wraps=playwright, chromium=self._chromium_adapter)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Any:
        cleanup_error: BaseException | None = None
        try:
            if self._chromium_adapter is not None:
                self._chromium_adapter.cleanup()
        except BaseException as error:
            cleanup_error = error
        handled = self._context_manager.__exit__(exc_type, exc, traceback)
        if cleanup_error is not None:
            raise cleanup_error
        return handled


def _geometry() -> Any:
    from meshshot import MeshGeometry

    shared = ((-0.12, -0.22, 0.0), (0.12, -0.22, 0.0), (0.0, 0.18, 0.0))
    left = ((-0.46, -0.2, 0.0), (-0.2, -0.2, 0.0), (-0.33, 0.2, 0.0))
    vertices = [list(point) for triangle in (shared, left) for point in triangle]
    return MeshGeometry(vertices=vertices, faces=[[0, 1, 2], [3, 4, 5]])


@contextmanager
def _adapter_patch(
    executable: Path,
    observations: Observations,
    mode: str,
    fail_after_readiness: bool = False,
) -> Iterator[None]:
    import playwright.sync_api

    real_factory = playwright.sync_api.sync_playwright

    def factory() -> PlaywrightAdapter:
        return PlaywrightAdapter(
            real_factory,
            executable,
            observations,
            mode,
            fail_after_readiness,
        )

    with mock.patch.object(playwright.sync_api, "sync_playwright", factory):
        yield


def _run_render(executable: Path, mode: str) -> tuple[dict[str, Any], bytes]:
    from meshshot import render_residual_preview

    observations = Observations(adapter=mode)
    started = time.monotonic()
    with (
        mock.patch.dict(
            os.environ,
            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
        ),
        _adapter_patch(executable, observations, mode),
        _wall_deadline(RENDER_DEADLINE_SECONDS),
    ):
        rendered = render_residual_preview(_geometry(), _geometry(), variant="step")
    elapsed = time.monotonic() - started
    with Image.open(BytesIO(rendered.png_bytes)) as image:
        image.load()
        image_fact = {
            "mode": image.mode,
            "dimensions": list(image.size),
            "sha256": _sha256(rendered.png_bytes),
        }
    view_names = [str(view.get("name")) for view in rendered.views]
    public = observations.public()
    public.update(
        {
            "status": "PASS",
            "render_seconds": round(elapsed, 6),
            "image": image_fact,
            "ordered_views": view_names,
            "profile_sha256": rendered.profile_sha256,
        }
    )
    return public, rendered.png_bytes


def _run_forced_failure(executable: Path) -> dict[str, Any]:
    from meshshot import MeshshotError, render_residual_preview

    observations = Observations(adapter="prelaunched-cdp-forced-failure")
    caught = False
    with (
        mock.patch.dict(
            os.environ,
            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
        ),
        _adapter_patch(
            executable,
            observations,
            "prelaunched-cdp",
            fail_after_readiness=True,
        ),
        _wall_deadline(RENDER_DEADLINE_SECONDS),
    ):
        try:
            render_residual_preview(_geometry(), _geometry(), variant="step")
        except MeshshotError as exc:
            caught = exc.phase == "browser_launch"
    public = observations.public()
    public.update(
        {
            "status": "PASS" if caught else "FAIL",
            "expected_closed_failure_observed": caught,
        }
    )
    return public


def _validate_case(case: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if case["status"] != "PASS":
        failures.append(f"{case['adapter']}: status")
    if not case["readiness"]:
        failures.append(f"{case['adapter']}: readiness")
    if not case["route_registered"]:
        failures.append(f"{case['adapter']}: route")
    fulfilled = {(item["path"], item["status"]) for item in case["fulfilled"]}
    for expected in {
        ("/render.html", 200),
        ("/residual-render.js", 200),
        ("/payload.json", 200),
    }:
        if expected not in fulfilled:
            failures.append(f"{case['adapter']}: fulfill {expected[0]}")
    if not case["console_events"] or "meshshot-stage:png-encode:done" not in case[
        "console_events"
    ]:
        failures.append(f"{case['adapter']}: console stages")
    if not case["page_evaluate"]:
        failures.append(f"{case['adapter']}: evaluate")
    if not case["outside_request_rejected"]:
        failures.append(f"{case['adapter']}: outside network")
    if case["ordered_views"] != EXPECTED_VIEWS:
        failures.append(f"{case['adapter']}: ordered views")
    if case["image"]["mode"] != "RGB" or case["image"]["dimensions"] != [504, 1008]:
        failures.append(f"{case['adapter']}: PNG semantics")
    return failures


def run() -> dict[str, Any]:
    _install_repo_imports()
    executable, identity = _browser_identity()
    baseline, baseline_png = _run_render(executable, "playwright-launch")
    candidate, candidate_png = _run_render(executable, "prelaunched-cdp")
    forced = _run_forced_failure(executable)
    failures = _validate_case(baseline) + _validate_case(candidate)
    pixel_equal = baseline_png == candidate_png
    if not pixel_equal:
        failures.append("A/B PNG bytes differ")
    for label, release in (
        ("B normal", candidate["release"]),
        ("B forced", forced["release"]),
    ):
        if not all(
            release[key] is True
            for key in ("process_reaped", "process_group_empty", "profile_removed")
        ):
            failures.append(f"{label}: resource release")
    if forced["status"] != "PASS":
        failures.append("B forced: closed failure")
    profile_digest = _canonical_digest(
        [
            *CHROMIUM_SWITCHES,
            *HEADLESS_SWITCHES,
            "--user-data-dir=$ISOLATED_PROFILE",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=$EPHEMERAL",
            "about:blank",
        ]
    )
    return {
        "schema": PROTOTYPE_SCHEMA,
        "throwaway": True,
        "question": (
            "Can Python prelaunch the exact Playwright 1.60 headless shell on "
            "loopback and preserve meshshot semantics through connect_over_cdp()?"
        ),
        "verdict": "PASS" if not failures else "FAIL",
        "browser_identity": identity,
        "adapter_profile": {"name": ADAPTER_PROFILE, "sha256": profile_digest},
        "deadlines_seconds": {
            "startup_and_readiness": STARTUP_DEADLINE_SECONDS,
            "render": RENDER_DEADLINE_SECONDS,
            "cleanup_term": CLEANUP_TERM_SECONDS,
            "cleanup_kill": CLEANUP_KILL_SECONDS,
        },
        "baseline": baseline,
        "prelaunched_cdp": candidate,
        "forced_failure": forced,
        "comparison": {
            "same_executable_identity": True,
            "png_bytes_equal": pixel_equal,
            "png_sha256": baseline["image"]["sha256"] if pixel_equal else None,
            "profile_sha256_equal": (
                baseline["profile_sha256"] == candidate["profile_sha256"]
            ),
            "ordered_views_equal": (
                baseline["ordered_views"] == candidate["ordered_views"]
            ),
        },
        "failures": failures,
        "not_run": [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = run()
    except Exception as exc:
        result = {
            "schema": PROTOTYPE_SCHEMA,
            "throwaway": True,
            "verdict": "FAIL",
            # Never project browser stderr, argv, endpoints, PIDs, or paths.
            "failures": [f"prototype_exception:{type(exc).__name__}"],
            "not_run": ["remaining A/B observations after prototype failure"],
        }
    encoded = json.dumps(result, indent=2, sort_keys=True) + "\n"
    result_path = Path(__file__).resolve().with_name("recorded-result.json")
    result_path.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
