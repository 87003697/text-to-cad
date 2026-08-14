"""Linux-only execution proof for the private browser image mount contract."""

from __future__ import annotations

import importlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


try:
    REPO_ROOT = Path(__file__).resolve().parents[4]
except IndexError:
    REPO_ROOT = Path("/")


@unittest.skipUnless(
    sys.platform.startswith("linux")
    and os.environ.get("MESHSHOT_LINUX_EXEC_ROOT_TEST") == "1",
    "requires the controlled Linux noexec-/tmp + exec-root harness",
)
class LinuxPrivateSnapshotExecutionTests(unittest.TestCase):
    def test_proc_relative_resource_browser_reaches_readiness(self) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("controlled Linux image lacks the existing bwrap runtime")
        runtime_source = Path(os.environ["MESHSHOT_BROWSER_RUNTIME_SOURCE"])
        package = sys.modules.get("meshshot")
        if package is None:
            package = types.ModuleType("meshshot")
            package.__path__ = [os.fspath(runtime_source.parent)]
            sys.modules["meshshot"] = package
        runtime = importlib.import_module("meshshot.browser_runtime")
        fixture_root = Path("/fixture/proc-resource-browser")
        source_root = fixture_root / "attested"
        executable_root = source_root / "chrome-headless-shell-linux64"
        executable_root.mkdir(parents=True, exist_ok=True)
        source = executable_root / "chrome-headless-shell"
        c_source = fixture_root / "fixture.c"
        (executable_root / "runtime.dat").write_bytes(b"resource")
        c_source.write_text(
            r'''
#include <arpa/inet.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
int main(int argc, char **argv) {
  if (argc == 2 && strcmp(argv[1], "--version") == 0) {
    puts("Google Chrome for Testing 148.0.7778.96"); return 0;
  }
  char executable[PATH_MAX] = {0};
  if (readlink("/proc/self/exe", executable, sizeof(executable)-1) <= 0) return 31;
  char *name = strrchr(executable, '/'); if (!name) return 32;
  strcpy(name + 1, "runtime.dat"); if (access(executable, R_OK)) return 33;
  const char *profile = NULL;
  for (int i = 1; i < argc; ++i) {
    if (!strncmp(argv[i], "--user-data-dir=", 16)) profile = argv[i] + 16;
  }
  if (!profile) return 34;
  int listener = socket(AF_INET, SOCK_STREAM, 0);
  struct sockaddr_in address = {0}; address.sin_family = AF_INET;
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = 0;
  if (listener < 0 || bind(listener, (void *)&address, sizeof(address)) || listen(listener, 1)) return 35;
  socklen_t size = sizeof(address); if (getsockname(listener, (void *)&address, &size)) return 36;
  char readiness[PATH_MAX]; snprintf(readiness, sizeof(readiness), "%s/DevToolsActivePort", profile);
  FILE *stream = fopen(readiness, "w"); if (!stream) return 37;
  fprintf(stream, "%u\n/devtools/browser/proc-resource\n", ntohs(address.sin_port)); fclose(stream);
  sleep(5); return 0;
}
''',
            encoding="utf-8",
        )
        compiled = subprocess.run(
            ["/usr/bin/gcc", "-O2", os.fspath(c_source), "-o", os.fspath(source)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        self.assertEqual(0, compiled.returncode)
        source.chmod(0o755)
        manifest = {
            "schema": "meshshot.browser-tree-manifest/1",
            "entries": [
                {"path": ".", "kind": "directory", "mode": 0o755},
                {
                    "path": "chrome-headless-shell-linux64",
                    "kind": "directory",
                    "mode": 0o755,
                },
                {
                    "path": (
                        "chrome-headless-shell-linux64/"
                        "chrome-headless-shell"
                    ),
                    "kind": "file",
                    "mode": 0o755,
                    "sha256": __import__("hashlib").sha256(
                        source.read_bytes()
                    ).hexdigest(),
                },
                {
                    "path": "chrome-headless-shell-linux64/runtime.dat",
                    "kind": "file",
                    "mode": 0o644,
                    "sha256": __import__("hashlib").sha256(
                        b"resource"
                    ).hexdigest(),
                },
            ],
        }
        manifest_sha256 = __import__("hashlib").sha256(
            json.dumps(
                manifest, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        previous_root = os.environ.get("MESHSHOT_EXECUTABLE_ROOT")
        previous_manifest = os.environ.get(
            "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
        )
        previous_devnull = os.devnull
        harness_devnull = Path(
            f"/tmp/meshshot-issue64-devnull-{secrets.token_hex(8)}"
        )
        harness_devnull.write_bytes(b"")
        harness_devnull.chmod(0o600)
        os.devnull = os.fspath(harness_devnull)
        os.environ["MESHSHOT_EXECUTABLE_ROOT"] = "/meshshot-exec"
        os.environ["MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"] = manifest_sha256
        Path("/meshshot-supervisor").chmod(0o700)
        pinned = runtime._PinnedExecutable(source)
        browser = object.__new__(runtime.PrelaunchedCdpRuntime)
        browser._executable = source
        browser._profile = {
            "arguments": [], "startup_timeout_ms": 10000,
            "cleanup_term_ms": 1000, "cleanup_kill_ms": 1000,
        }
        browser._pinned_executable = pinned
        browser._profile_dir = None
        browser._profile_identity = None
        browser._profile_cleanup_forbidden = False
        browser._profile_fd = None
        browser._profile_parent_fd = None
        browser._process = None
        browser._process_group = None
        try:
            with mock.patch.object(runtime, "_verify_listener_owner"):
                self.assertRegex(browser._prelaunch(), r"^http://127\.0\.0\.1:[0-9]+$")
        finally:
            if pinned.fd is not None:
                browser._cleanup()
            os.devnull = previous_devnull
            harness_devnull.unlink(missing_ok=True)
            if previous_root is None:
                os.environ.pop("MESHSHOT_EXECUTABLE_ROOT", None)
            else:
                os.environ["MESHSHOT_EXECUTABLE_ROOT"] = previous_root
            if previous_manifest is None:
                os.environ.pop("MESHSHOT_BROWSER_TREE_MANIFEST_SHA256", None)
            else:
                os.environ[
                    "MESHSHOT_BROWSER_TREE_MANIFEST_SHA256"
                ] = previous_manifest

    def test_production_shaped_double_bwrap_public_render_and_cleanup(self) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("controlled Linux image lacks the existing bwrap runtime")
        from production_shaped_supervised_render import run

        result = run()
        self.assertEqual("permission", result["old_nested_owner"])
        self.assertEqual("passed", result["runtime_staging"])
        self.assertEqual("passed", result["supervised_public_render"])
        self.assertEqual("passed", result["real_cdp_identity"])
        self.assertEqual("passed", result["supervisor_mount_namespace"])
        self.assertEqual("passed", result["supervisor_mount_propagation"])
        self.assertEqual("passed", result["shared_supervisor_records"])
        self.assertEqual("passed", result["same_uid_mount_boundaries"])
        self.assertEqual("passed", result["version_live_child_mounts"])
        self.assertEqual(0, result["nested_browser_popen_count"])
        self.assertEqual("passed", result["completion_shutdown"])
        self.assertEqual("passed", result["supervisor_cleanup"])

    def test_supervisor_term_cleans_separate_browser_group_and_private_state(
        self,
    ) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("controlled Linux image lacks the existing bwrap runtime")
        from production_shaped_supervised_render import run_signal_cleanup

        result = run_signal_cleanup()
        self.assertEqual("passed", result["supervisor_reaped"])
        self.assertEqual("passed", result["browser_group_empty"])
        self.assertEqual("passed", result["profile_removed"])
        self.assertEqual("passed", result["private_tree_removed"])
        self.assertEqual("passed", result["socket_removed"])

    def test_fixed_seqpacket_authority_crosses_read_only_nested_bind(self) -> None:
        if not Path("/usr/bin/bwrap").is_file():
            self.skipTest("controlled Linux image lacks the existing bwrap runtime")
        outer = Path("/meshshot-supervisor")
        outer.mkdir(mode=0o700, exist_ok=True)
        endpoint = outer / "authority.sock"
        if os.path.lexists(endpoint):
            self.fail("controlled supervisor socket must start absent")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        child: subprocess.Popen[bytes] | None = None
        try:
            server.bind(os.fspath(endpoint))
            endpoint.chmod(0o600)
            server.listen(1)
            child = subprocess.Popen(
                [
                    "/usr/bin/bwrap",
                    "--die-with-parent",
                    "--new-session",
                    "--cap-drop",
                    "ALL",
                    "--bind",
                    "/",
                    "/",
                    "--ro-bind",
                    "/meshshot-supervisor",
                    "/run/meshshot-supervisor",
                    "--tmpfs",
                    "/meshshot-supervisor",
                    "--",
                    "/usr/bin/python3",
                    "-c",
                    (
                        "import os,socket;"
                        "assert not os.path.exists('/meshshot-supervisor/authority.sock');"
                        "s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET);"
                        "s.connect('/run/meshshot-supervisor/authority.sock');"
                        "s.send(b'hello');"
                        "assert s.recv(16)==b'authority';s.close()"
                    ),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                close_fds=True,
            )
            server.settimeout(5)
            connection, _address = server.accept()
            try:
                peer_pid, peer_uid, _peer_gid = struct.unpack(
                    "3i",
                    connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
                )
                self.assertEqual(child.pid, peer_pid)
                self.assertEqual(os.geteuid(), peer_uid)
                self.assertEqual(b"hello", connection.recv(16))
                connection.send(b"authority")
            finally:
                connection.close()
            stdout, stderr = child.communicate(timeout=5)
            self.assertEqual(
                0,
                child.returncode,
                (stdout + stderr).decode("utf-8", errors="replace"),
            )
            child = None
        finally:
            if child is not None:
                child.kill()
                child.wait(timeout=5)
            server.close()
            if os.path.lexists(endpoint):
                endpoint.unlink()

    def test_private_image_moves_execution_off_noexec_tmp(self) -> None:
        runtime_source = Path(
            os.environ.get(
                "MESHSHOT_BROWSER_RUNTIME_SOURCE",
                REPO_ROOT / "packages/meshshot/src/meshshot/browser_runtime.py",
            )
        )
        package = types.ModuleType("meshshot")
        package.__path__ = [os.fspath(runtime_source.parent)]
        sys.modules.setdefault("meshshot", package)
        runtime = importlib.import_module("meshshot.browser_runtime")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "chrome-headless-shell"
            c_source = root / "version.c"
            c_source.write_text(
                "#include <stdio.h>\n"
                "int main(void) {\n"
                "  puts(\"Google Chrome for Testing 148.0.7778.96\");\n"
                "  return 0;\n"
                "}\n",
                encoding="utf-8",
            )
            compiled = subprocess.run(
                [
                    "/usr/bin/gcc",
                    "-O2",
                    os.fspath(c_source),
                    "-o",
                    os.fspath(source),
                ],
                check=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
            self.assertEqual(
                0,
                compiled.returncode,
                "Linux ELF fixture compile failed",
            )
            with self.assertRaises(PermissionError):
                subprocess.run(
                    [source, "--version"],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

            previous_root = os.environ.get("MESHSHOT_EXECUTABLE_ROOT")
            try:
                os.environ["MESHSHOT_EXECUTABLE_ROOT"] = "/meshshot-exec"
                pinned = runtime._PinnedExecutable(source)
                try:
                    completed = pinned.run_version(timeout=5)
                    self.assertEqual(0, completed.returncode)
                    self.assertEqual(
                        b"Google Chrome for Testing 148.0.7778.96\n",
                        completed.stdout,
                    )
                    self.assertEqual(b"", completed.stderr)
                finally:
                    pinned.close()
            finally:
                if previous_root is None:
                    os.environ.pop("MESHSHOT_EXECUTABLE_ROOT", None)
                else:
                    os.environ["MESHSHOT_EXECUTABLE_ROOT"] = previous_root

    def test_sealed_elf_preserves_private_resources_and_self_reexec(self) -> None:
        runtime_source = Path(os.environ["MESHSHOT_BROWSER_RUNTIME_SOURCE"])
        package = sys.modules.get("meshshot")
        if package is None:
            package = types.ModuleType("meshshot")
            package.__path__ = [os.fspath(runtime_source.parent)]
            sys.modules["meshshot"] = package
        runtime = importlib.import_module("meshshot.browser_runtime")
        source_root = Path("/fixture/source")
        source_root.mkdir(parents=True)
        source = source_root / "chrome-headless-shell"
        c_source = source_root / "fixture.c"
        c_source.write_text(
            """
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>
static int resource(const char *argv0) {
  char path[PATH_MAX]; const char *slash = strrchr(argv0, '/');
  if (!slash) return 10; size_t n = (size_t)(slash - argv0);
  memcpy(path, argv0, n); memcpy(path+n, "/runtime.dat", 13);
  FILE *f = fopen(path, "rb"); if (!f) return 11;
  char value[9] = {0}; size_t count = fread(value, 1, 8, f); fclose(f);
  return count == 8 && memcmp(value, "resource", 8) == 0 ? 0 : 12;
}
int main(int argc, char **argv) {
  int status = resource(argv[0]); if (status) return status;
  if (argc == 2 && strcmp(argv[1], "hold") == 0) { sleep(5); return 0; }
  if (argc == 2 && strcmp(argv[1], "reexec") == 0) {
    puts("sealed-elf-resource-reexec-ok"); return 0;
  }
  char *child[] = {argv[0], "reexec", NULL};
  execv("/proc/self/exe", child); return errno ? errno : 20;
}
""",
            encoding="utf-8",
        )
        (source_root / "runtime.dat").write_bytes(b"resource")
        compiled = subprocess.run(
            [
                "/usr/bin/gcc",
                "-O2",
                "-Wall",
                os.fspath(c_source),
                "-o",
                os.fspath(source),
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        self.assertEqual(0, compiled.returncode, "Linux ELF fixture compile failed")
        previous_root = os.environ.get("MESHSHOT_EXECUTABLE_ROOT")
        try:
            os.environ["MESHSHOT_EXECUTABLE_ROOT"] = "/meshshot-exec"
            pinned = runtime._PinnedExecutable(source)
            try:
                required = (
                    fcntl.F_SEAL_WRITE
                    | fcntl.F_SEAL_GROW
                    | fcntl.F_SEAL_SHRINK
                    | fcntl.F_SEAL_SEAL
                )
                self.assertEqual(
                    required,
                    fcntl.fcntl(pinned.fd, fcntl.F_GET_SEALS) & required,
                )
                with self.assertRaises(PermissionError):
                    os.write(pinned.fd, b"x")
                process = pinned.popen(
                    [os.fspath(source)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(0, process.returncode)
                self.assertEqual(b"sealed-elf-resource-reexec-ok\n", stdout)
                self.assertEqual(b"", stderr)
                held = pinned.popen(
                    [os.fspath(source), "hold"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                try:
                    self.assertIsNone(
                        held.poll(),
                        "successful exec must close the handshake before exit",
                    )
                    inherited = os.stat(f"/proc/{held.pid}/fd/{pinned.fd}")
                    sealed = os.fstat(pinned.fd)
                    self.assertEqual(
                        (sealed.st_dev, sealed.st_ino),
                        (inherited.st_dev, inherited.st_ino),
                    )
                    pinned.verify_running_image(held.pid, timeout=5)
                finally:
                    held.terminate()
                    held.wait(timeout=5)
            finally:
                pinned.close()
        finally:
            if previous_root is None:
                os.environ.pop("MESHSHOT_EXECUTABLE_ROOT", None)
            else:
                os.environ["MESHSHOT_EXECUTABLE_ROOT"] = previous_root


@unittest.skipIf(
    os.environ.get("MESHSHOT_LINUX_EXEC_ROOT_TEST") == "1",
    "the controlled Linux container runs only the inner execution proof",
)
class DockerLinuxPrivateSnapshotExecutionTests(unittest.TestCase):
    """Run the Linux proof when a pre-existing local Docker image is available."""

    _IMAGE = "node:22-bookworm"
    _DOCKER_ROUTING_ENVIRONMENT = frozenset(
        {
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "DOCKER_CONFIG",
            "DOCKER_API_VERSION",
        }
    )

    def _local_docker_socket(self) -> Path:
        candidates = (
            Path("/var/run/docker.sock"),
            Path.home() / ".docker/run/docker.sock",
            Path.home() / ".colima/docker.sock",
            Path.home() / ".colima/default/docker.sock",
        )
        for candidate in candidates:
            try:
                resolved = candidate.resolve(strict=True)
                info = resolved.stat()
            except OSError:
                continue
            if (
                stat.S_ISSOCK(info.st_mode)
                and info.st_uid == os.getuid()
                and stat.S_IMODE(info.st_mode) & 0o077 == 0
            ):
                return resolved
            if (
                resolved == Path("/var/run/docker.sock")
                and stat.S_ISSOCK(info.st_mode)
                and info.st_uid == 0
                and stat.S_IMODE(info.st_mode) & 0o002 == 0
            ):
                return resolved
        raise unittest.SkipTest("owned local Docker Unix socket is unavailable")

    def _docker_environment(self, endpoint: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in self._DOCKER_ROUTING_ENVIRONMENT
        }
        environment["DOCKER_HOST"] = f"unix://{endpoint}"
        return environment

    def test_production_fixture_uses_outer_tmpfs_staging_contract(self) -> None:
        from tests.python.packages.meshshot.fixtures import (
            production_shaped_supervised_render as fixture,
        )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = root / "browser-revision/chrome-headless-shell-linux64"
            revision.mkdir(parents=True)
            executable = revision / "chrome-headless-shell"
            executable.write_bytes(b"fixture browser")
            executable.chmod(0o755)
            with (
                mock.patch.object(fixture, "_FIXTURE", root),
                mock.patch.object(fixture, "_REPO", REPO_ROOT),
            ):
                argv = fixture._outer_argv("run-staged-orchestrate")

        triples = [argv[index : index + 3] for index in range(len(argv) - 2)]
        pairs = [argv[index : index + 2] for index in range(len(argv) - 1)]
        self.assertIn(["--tmpfs", "/tmp/provider-free-playwright"], pairs)
        self.assertIn(
            [
                "--ro-bind",
                os.fspath(root / "browser-revision"),
                "/run/meshshot-browser-source",
            ],
            triples,
        )
        self.assertNotIn(
            [
                "--ro-bind",
                os.fspath(root / "browser-revision"),
                "/tmp/provider-free-playwright/attested",
            ],
            triples,
        )
        self.assertIn("MESHSHOT_BROWSER_TREE_MANIFEST_SHA256", argv)
        self.assertEqual("run-staged-orchestrate", argv[-1])

    def test_harness_scrubs_hostile_docker_routing_environment(self) -> None:
        calls: list[dict[str, object]] = []

        def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
            calls.append({"argv": argv, **kwargs})
            return subprocess.CompletedProcess(argv, 1)

        hostile = {
            "DOCKER_HOST": "tcp://attacker.invalid:2375",
            "DOCKER_CONTEXT": "remote-production",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/private/client-certificates",
        }
        with (
            mock.patch.dict(os.environ, hostile),
            mock.patch.object(shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(subprocess, "run", side_effect=fake_run),
            mock.patch.object(
                self,
                "_local_docker_socket",
                return_value=Path("/private/local/docker.sock"),
                create=True,
            ),
        ):
            with self.assertRaises(unittest.SkipTest):
                self.test_local_linux_noexec_harness()
        self.assertTrue(calls)
        for call in calls:
            environment = call["env"]
            self.assertEqual(
                "unix:///private/local/docker.sock",
                environment["DOCKER_HOST"],
            )
            self.assertNotIn("DOCKER_CONTEXT", environment)
            self.assertNotIn("DOCKER_TLS_VERIFY", environment)
            self.assertNotIn("DOCKER_CERT_PATH", environment)

    def test_create_timeout_still_removes_only_the_randomized_exact_name(self) -> None:
        calls: list[list[str]] = []

        def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess:
            calls.append(argv)
            if "create" in argv:
                raise subprocess.TimeoutExpired(argv, 15)
            if argv[1:3] == ["image", "inspect"]:
                return subprocess.CompletedProcess(argv, 0)
            if argv[1:3] == ["rm", "--force"]:
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 0)

        with (
            mock.patch.object(shutil, "which", return_value="/usr/bin/docker"),
            mock.patch.object(subprocess, "run", side_effect=fake_run),
            mock.patch.object(
                self,
                "_local_docker_socket",
                return_value=Path("/private/local/docker.sock"),
                create=True,
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            self.test_local_linux_noexec_harness()
        create = next(argv for argv in calls if "create" in argv)
        name = create[create.index("--name") + 1]
        self.assertRegex(name, r"^meshshot-linux-exec-[0-9]+-[0-9a-f]{12}$")
        self.assertEqual(
            [["/usr/bin/docker", "rm", "--force", name]],
            [argv for argv in calls if argv[1:3] == ["rm", "--force"]],
        )

    def test_cleanup_accepts_only_exact_absent_container_evidence(self) -> None:
        cases = (
            ("absent", 1, b"[]\n", None),
            ("retained", 0, b'[{"Id":"retained"}]\n', AssertionError),
            ("daemon-error", 1, b"", AssertionError),
        )
        for label, inspect_status, inspect_stdout, expected in cases:
            calls: list[list[str]] = []

            def fake_run(
                argv: list[str], **_kwargs: object
            ) -> subprocess.CompletedProcess:
                calls.append(argv)
                if argv[1:3] == ["image", "inspect"]:
                    return subprocess.CompletedProcess(argv, 0)
                if "create" in argv:
                    raise subprocess.TimeoutExpired(argv, 15)
                if argv[1:3] == ["rm", "--force"]:
                    return subprocess.CompletedProcess(argv, 1)
                if argv[1:3] == ["container", "inspect"]:
                    return subprocess.CompletedProcess(
                        argv,
                        inspect_status,
                        stdout=inspect_stdout,
                        stderr=b"",
                    )
                return subprocess.CompletedProcess(argv, 0)

            context = (
                self.assertRaises(expected)
                if expected is not None
                else self.assertRaises(subprocess.TimeoutExpired)
            )
            with (
                self.subTest(cleanup=label),
                mock.patch.object(shutil, "which", return_value="/usr/bin/docker"),
                mock.patch.object(subprocess, "run", side_effect=fake_run),
                mock.patch.object(
                    self,
                    "_local_docker_socket",
                    return_value=Path("/private/local/docker.sock"),
                    create=True,
                ),
                context,
            ):
                self.test_local_linux_noexec_harness()
            self.assertEqual(
                1,
                sum(argv[1:3] == ["container", "inspect"] for argv in calls),
            )

    def test_local_linux_noexec_harness(self) -> None:
        docker = shutil.which("docker")
        if docker is None:
            self.skipTest("local Docker is unavailable")
        endpoint = self._local_docker_socket()
        docker_environment = self._docker_environment(endpoint)

        def run_docker(
            arguments: list[str], *, timeout: float, quiet: bool = False
        ) -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                [docker, *arguments],
                check=False,
                env=docker_environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if quiet else subprocess.PIPE,
                stderr=subprocess.DEVNULL if quiet else subprocess.PIPE,
                timeout=timeout,
            )

        try:
            daemon = run_docker(["info"], timeout=10, quiet=True)
            image = run_docker(
                ["image", "inspect", self._IMAGE], timeout=10, quiet=True
            )
        except (OSError, subprocess.SubprocessError):
            self.skipTest("local Docker is unavailable")
        if daemon.returncode != 0 or image.returncode != 0:
            self.skipTest("controlled local Docker image is unavailable")

        name = f"meshshot-linux-exec-{os.getpid()}-{secrets.token_hex(6)}"
        try:
            create = run_docker(
                [
                    "create",
                    "--pull=never",
                    "--name",
                    name,
                    "--network",
                    "none",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--user",
                    "65534:65534",
                    "--tmpfs",
                    "/tmp:noexec,mode=1777,uid=65534,gid=65534",
                    "--tmpfs",
                    "/meshshot-exec:exec,mode=0755,uid=65534,gid=65534",
                    "--tmpfs",
                    "/fixture:exec,mode=0755,uid=65534,gid=65534",
                    "--tmpfs",
                    "/meshshot-supervisor:exec,mode=0700,uid=65534,gid=65534",
                    "-e",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "-e",
                    "MESHSHOT_LINUX_EXEC_ROOT_TEST=1",
                    "-e",
                    "MESHSHOT_BROWSER_RUNTIME_SOURCE=/browser_runtime.py",
                    self._IMAGE,
                    "python3",
                    "/test_linux_private_snapshot_exec.py",
                ],
                timeout=15,
            )
            if create.returncode != 0:
                self.skipTest("controlled local Docker container is unavailable")
            for source, target in (
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot",
                    "/meshshot",
                ),
                (REPO_ROOT / "scripts", "/scripts"),
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot/browser_runtime.py",
                    "/browser_runtime.py",
                ),
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot/fd_exec_handoff.py",
                    "/fd_exec_handoff.py",
                ),
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot/browser_supervisor.py",
                    "/browser_supervisor.py",
                ),
                (
                    REPO_ROOT
                    / "tests/python/packages/meshshot/fixtures/"
                    "production_shaped_supervised_render.py",
                    "/production_shaped_supervised_render.py",
                ),
                (Path(__file__), "/test_linux_private_snapshot_exec.py"),
            ):
                copied = run_docker(
                    ["cp", os.fspath(source), f"{name}:{target}"],
                    timeout=15,
                )
                self.assertEqual(0, copied.returncode, "Linux harness copy failed")
            completed = run_docker(
                ["start", "--attach", name],
                timeout=30,
            )
            self.assertEqual(
                0,
                completed.returncode,
                "Linux harness failed closed: "
                + (completed.stdout + completed.stderr).decode(
                    "utf-8", errors="replace"
                ),
            )
        finally:
            try:
                removed = run_docker(
                    ["rm", "--force", name], timeout=15, quiet=True
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise AssertionError("Linux harness cleanup failed") from exc
            if removed.returncode != 0:
                inspected = run_docker(
                    ["container", "inspect", name], timeout=10
                )
                try:
                    inspection = json.loads(inspected.stdout)
                except (TypeError, UnicodeDecodeError, ValueError) as exc:
                    raise AssertionError("Linux harness cleanup failed") from exc
                self.assertTrue(
                    inspected.returncode != 0 and inspection == [],
                    "Linux harness cleanup failed",
                )


if __name__ == "__main__":
    unittest.main()
