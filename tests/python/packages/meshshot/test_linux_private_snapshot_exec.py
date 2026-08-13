"""Linux-only execution proof for the private browser image mount contract."""

from __future__ import annotations

import importlib
import fcntl
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
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
                    REPO_ROOT / "packages/meshshot/src/meshshot/browser_runtime.py",
                    "/browser_runtime.py",
                ),
                (
                    REPO_ROOT / "packages/meshshot/src/meshshot/fd_exec_handoff.py",
                    "/fd_exec_handoff.py",
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
