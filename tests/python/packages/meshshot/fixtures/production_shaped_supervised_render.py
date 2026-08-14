"""Controlled Linux proof for the production-shaped supervised render chain."""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import time
import zlib


_BWRAP = "/usr/bin/bwrap"
_FIXTURE = Path(os.environ.get("MESHSHOT_R3_FIXTURE_ROOT", "/fixture"))
_REPO = Path(os.environ.get("MESHSHOT_R3_REPO_ROOT", "/"))
_EXECUTABLE = Path(
    "/tmp/provider-free-playwright/attested/"
    "chrome-headless-shell-linux64/chrome-headless-shell"
)


def _png_data_url(width: int = 504, height: int = 1008) -> str:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)
        )

    rows = b"".join(b"\0" + b"\0\0\0" * width for _ in range(height))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _write_fake_playwright() -> None:
    pil = _FIXTURE / "PIL"
    pil.mkdir(parents=True, exist_ok=True)
    (pil / "__init__.py").write_text("from . import Image\n", encoding="utf-8")
    (pil / "Image.py").write_text(
        '''class _Image:
    size = (504, 1008)
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def load(self): return None
    def convert(self, _mode): return self
    def save(self, stream, **_kwargs): stream.write(b"controlled-png")
def open(_stream): return _Image()
''',
        encoding="utf-8",
    )
    package = _FIXTURE / "playwright"
    (package / "driver/package").mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "driver/package/browsers.json").write_text(
        json.dumps(
            {
                "browsers": [
                    {"name": "chromium-headless-shell", "revision": "1223"}
                ]
            }
        ),
        encoding="utf-8",
    )
    distribution = _FIXTURE / "playwright-1.60.0.dist-info"
    distribution.mkdir(exist_ok=True)
    (distribution / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: playwright\nVersion: 1.60.0\n",
        encoding="utf-8",
    )
    (package / "sync_api.py").write_text(
        '''from __future__ import annotations
import os

class Session:
    def send(self, method):
        assert method == "Browser.getVersion"
        return {"product": "HeadlessChrome/148.0.7778.96"}
    def detach(self):
        return None

class Page:
    def on(self, *_args):
        return None
    def route(self, *_args):
        return None
    def goto(self, *_args, **_kwargs):
        return None
    def wait_for_function(self, *_args, **_kwargs):
        return None
    def evaluate(self, _source):
        from production_shaped_supervised_render import _png_data_url
        return {
            "ok": True,
            "pngDataUrl": _png_data_url(),
            "views": [
                {"name": name}
                for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }

class Context:
    def route(self, *_args):
        return None
    def new_page(self):
        return Page()
    def close(self):
        return None

class Browser:
    def new_browser_cdp_session(self):
        return Session()
    def new_context(self, **_kwargs):
        return Context()
    def close(self):
        return None

class Chromium:
    executable_path = os.environ.get("MESHSHOT_BROWSER_EXECUTABLE", "/missing")
    def connect_over_cdp(self, *_args, **_kwargs):
        return Browser()

class Playwright:
    chromium = Chromium()

class Manager:
    def __enter__(self):
        return Playwright()
    def __exit__(self, *_args):
        return False

def sync_playwright():
    return Manager()
''',
        encoding="utf-8",
    )


def _compile_browser() -> None:
    source = _FIXTURE / "fake-browser.c"
    revision = _FIXTURE / "browser-revision/chrome-headless-shell-linux64"
    revision.mkdir(parents=True, exist_ok=True)
    executable = revision / "chrome-headless-shell"
    source.write_text(
        r'''#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }
int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "--version") == 0) {
    puts("Google Chrome for Testing 148.0.7778.96");
    return 0;
  }
  const char *profile = NULL;
  for (int i = 1; i < argc; ++i) {
    if (strncmp(argv[i], "--user-data-dir=", 16) == 0) profile = argv[i] + 16;
  }
  if (!profile) return 20;
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  struct sockaddr_in address = {0};
  address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  address.sin_port = 0;
  if (fd < 0 || bind(fd, (struct sockaddr *)&address, sizeof(address)) || listen(fd, 4)) return 21;
  socklen_t size = sizeof(address);
  if (getsockname(fd, (struct sockaddr *)&address, &size)) return 22;
  char path[4096];
  snprintf(path, sizeof(path), "%s/DevToolsActivePort", profile);
  FILE *stream = fopen(path, "w");
  if (!stream) return 23;
  fprintf(stream, "%u\n/devtools/browser/fixture\n", ntohs(address.sin_port));
  fclose(stream);
  signal(SIGTERM, stop); signal(SIGINT, stop); signal(SIGALRM, stop); alarm(5);
  while (running) pause();
  close(fd);
  return 0;
}
''',
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["/usr/bin/gcc", "-O2", os.fspath(source), "-o", os.fspath(executable)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    if completed.returncode != 0:
        raise AssertionError("controlled browser compilation failed")
    executable.chmod(0o755)


def _wrapper() -> Path:
    wrapper = _FIXTURE / "python-wrapper-r3"
    if wrapper.is_file():
        return wrapper
    wrapper.write_text(
        "#!/bin/sh\n"
        f"PYTHONPATH={_FIXTURE}:{_REPO / 'packages/meshshot/src'}:{_REPO}:/ "
        "exec /usr/bin/python3 \"$@\"\n",
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    return wrapper


def _outer_argv(mode: str) -> list[str]:
    capabilities = (
        "CAP_SYS_ADMIN",
        "CAP_SYS_CHROOT",
        "CAP_NET_ADMIN",
        "CAP_SETUID",
        "CAP_SETGID",
        "CAP_SYS_PTRACE",
        "CAP_SETFCAP",
    )
    argv = [
        *(["/usr/bin/sudo"] if os.environ.get("MESHSHOT_R3_BWRAP_SUDO") == "1" else []),
        _BWRAP,
        "--unshare-user",
        "--unshare-net",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--cap-drop",
        "ALL",
    ]
    for capability in capabilities:
        argv.extend(("--cap-add", capability))
    argv.extend(
        (
            "--die-with-parent",
            "--new-session",
            "--bind",
            "/",
            "/",
            # Colima's nested device cgroup rejects the synthetic --dev nodes;
            # bind the same fixed host device tree for this local-only proof.
            "--dev-bind",
            "/dev",
            "/dev",
            "--proc",
            "/proc",
            "--tmpfs",
            "/tmp",
            "--tmpfs",
            "/meshshot-exec",
            "--tmpfs",
            "/meshshot-supervisor",
            "--dir",
            "/tmp/provider-free-playwright",
            "--ro-bind",
            os.fspath(_FIXTURE / "browser-revision"),
            "/tmp/provider-free-playwright/attested",
            "--setenv",
            "MESHSHOT_R3_FIXTURE_ROOT",
            os.fspath(_FIXTURE),
            "--setenv",
            "MESHSHOT_R3_REPO_ROOT",
            os.fspath(_REPO),
            "--chdir",
            os.fspath(_FIXTURE),
            "--",
            os.fspath(_wrapper()),
            os.fspath(Path(__file__)),
            mode,
        )
    )
    return argv


def _configure_scenario() -> object:
    from scripts.pilot import provider_free_scenarios as scenarios

    scenarios.REPO_ROOT = _REPO
    scenarios.MESHSHOT_BROWSER_SUPERVISOR = (
        _REPO / "packages/meshshot/src/meshshot/browser_supervisor.py"
    )
    scenarios.TRUSTED_BWRAP_PATH = Path(_BWRAP)
    scenarios._BROWSER_SUPERVISOR_TIMEOUT_SECONDS = 10.0
    scenarios.sys.executable = os.fspath(_wrapper())
    scenarios._browser_supervisor_group_empty = lambda _group: True
    return scenarios


def _nested_render() -> int:
    Path("/tmp/nested-client-pid").write_text(str(os.getpid()), encoding="ascii")
    from meshshot import browser_runtime
    from meshshot import MeshGeometry, render_residual_preview

    old_result = "unexpected"
    old_runtime = None
    try:
        old_runtime = browser_runtime.PrelaunchedCdpRuntime(_EXECUTABLE)
    except browser_runtime.BrowserRuntimeError as exc:
        if exc.browser_identity_check == "private_version_helper_spawn_permission":
            old_result = "permission"
        else:
            raise
    finally:
        if old_runtime is not None:
            old_runtime._pinned_executable.close()
    browser_runtime.subprocess.Popen = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("nested browser owner Popen forbidden")
    )
    triangle = MeshGeometry(
        vertices=[[-0.2, -0.2, 0.0], [0.2, -0.2, 0.0], [0.0, 0.2, 0.0]],
        faces=[[0, 1, 2]],
    )
    rendered = render_residual_preview(triangle, triangle, variant="step")
    print(
        json.dumps(
            {
                "ok": True,
                "old_nested_owner": old_result,
                "supervised_public_render": "passed" if rendered.png_bytes else "failed",
                "nested_browser_popen_count": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _runtime_only() -> int:
    from meshshot.browser_runtime import PrelaunchedCdpRuntime
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        runtime = PrelaunchedCdpRuntime(_EXECUTABLE)
        with runtime.open(playwright.chromium):
            authority = runtime.supervisor_authority()
            assert authority["type"] == "authority"
    print(json.dumps({"runtime_only": "passed"}, sort_keys=True))
    return 0


def _orchestrate() -> int:
    scenarios = _configure_scenario()
    command_log = Path("/tmp/public-command.jsonl")
    nested = scenarios._preview_sandbox_argv(
        ["/usr/bin/python3", os.fspath(Path(__file__)), "nested-render"],
        cwd=_FIXTURE,
    )
    try:
        with scenarios._browser_supervisor() as supervisor:
            try:
                result = scenarios._run_public(
                    nested,
                    cwd=_FIXTURE,
                    command_log=command_log,
                    process_started=lambda process: supervisor.register_client(
                        process,
                        expected_executable=Path("/usr/bin/python3"),
                    ),
                )
            except scenarios.ScenarioError as nested_exc:
                print(
                    json.dumps(
                        {
                            "nested_public": nested_exc.classification
                            or nested_exc.operation,
                            "nested_detail": str(nested_exc),
                        },
                        sort_keys=True,
                    )
                )
                raise
    except scenarios.ScenarioError as exc:
        nested_payload = None
        try:
            nested_payload = json.loads(
                command_log.read_text(encoding="utf-8").splitlines()[-1]
            )
        except (OSError, IndexError, json.JSONDecodeError):
            pass
        binding = "unavailable"
        private_result = None
        try:
            expected = json.loads(
                Path("/meshshot-supervisor/expected-client.json").read_text(
                    encoding="utf-8"
                )
            )["client_pid"]
            actual = int(
                Path("/tmp/nested-client-pid").read_text(encoding="ascii")
            )
            binding = "exact" if expected == actual else "mismatch"
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass
        try:
            private_result = json.loads(
                Path("/meshshot-supervisor/result.json").read_text(
                    encoding="utf-8"
                )
            )
        except (OSError, json.JSONDecodeError):
            pass
        print(
            json.dumps(
                {
                    "closed_failure": exc.classification or exc.operation,
                    "substage": exc.browser_identity_substage,
                    "phase": exc.browser_identity_phase,
                    "check": exc.browser_identity_check,
                    "client_binding": binding,
                    "nested_exit": (
                        nested_payload.get("exit_code")
                        if isinstance(nested_payload, dict)
                        else None
                    ),
                    "private_result": private_result,
                },
                sort_keys=True,
            )
        )
        return 3
    result.update(
        {
            "completion_shutdown": "passed",
            "supervisor_cleanup": (
                "passed"
                if not os.path.lexists("/meshshot-supervisor/authority.sock")
                else "failed"
            ),
        }
    )
    print(json.dumps(result, sort_keys=True))
    return 0


def _signal_orchestrate() -> int:
    scenarios = _configure_scenario()
    environment = {
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "PLAYWRIGHT_BROWSERS_PATH": "/tmp/provider-free-playwright",
        "MESHSHOT_BROWSER_EXECUTABLE": os.fspath(_EXECUTABLE),
        "MESHSHOT_EXECUTABLE_ROOT": "/meshshot-exec",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": (
            f"{_FIXTURE}:{_REPO / 'packages/meshshot/src'}:{_REPO}:/"
        ),
    }
    process = subprocess.Popen(
        [os.fspath(_wrapper()), os.fspath(scenarios.MESHSHOT_BROWSER_SUPERVISOR)],
        cwd=_REPO,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if Path("/meshshot-supervisor/authority.sock").is_socket():
            break
        if process.poll() is not None:
            raise AssertionError("supervisor exited before signal readiness")
        time.sleep(0.02)
    else:
        raise AssertionError("supervisor signal readiness timed out")
    descendants: list[int] = []
    pending = [process.pid]
    while pending:
        pid = pending.pop()
        try:
            children = (
                Path("/proc") / str(pid) / "task" / str(pid) / "children"
            ).read_text(encoding="ascii").split()
        except OSError:
            continue
        values = [int(value) for value in children]
        descendants.extend(values)
        pending.extend(values)
    browser_groups = {
        os.getpgid(pid)
        for pid in descendants
        if pid > 1 and os.getpgid(pid) != process.pid
    }
    os.kill(process.pid, signal.SIGTERM)
    process.wait(timeout=15)
    groups_empty = True
    for group in browser_groups:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            continue
        groups_empty = False
    private_entries = list(Path("/meshshot-exec").iterdir())
    state_entries = list(Path("/meshshot-supervisor").iterdir())
    print(
        json.dumps(
            {
                "supervisor_reaped": "passed" if process.returncode is not None else "failed",
                "browser_group_empty": "passed" if groups_empty else "failed",
                "profile_removed": "passed" if not private_entries else "failed",
                "private_tree_removed": "passed" if not private_entries else "failed",
                "socket_removed": (
                    "passed"
                    if not any(path.name == "authority.sock" for path in state_entries)
                    else "failed"
                ),
            },
            sort_keys=True,
        )
    )
    return 0


def run() -> dict[str, object]:
    _write_fake_playwright()
    _compile_browser()
    completed = subprocess.run(
        _outer_argv("orchestrate"),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=40,
    )
    if completed.returncode != 0:
        raise AssertionError("production-shaped double-bwrap render failed")
    return json.loads(completed.stdout.splitlines()[-1])


def run_signal_cleanup() -> dict[str, str]:
    _write_fake_playwright()
    _compile_browser()
    completed = subprocess.run(
        _outer_argv("signal-orchestrate"),
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=40,
    )
    if completed.returncode != 0:
        raise AssertionError("production-shaped signal cleanup failed")
    return json.loads(completed.stdout.splitlines()[-1])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    if sys.argv[1] == "orchestrate":
        raise SystemExit(_orchestrate())
    if sys.argv[1] == "nested-render":
        raise SystemExit(_nested_render())
    if sys.argv[1] == "runtime-only":
        raise SystemExit(_runtime_only())
    if sys.argv[1] == "signal-orchestrate":
        raise SystemExit(_signal_orchestrate())
    raise SystemExit(2)
