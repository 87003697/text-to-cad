"""Semantic image tests through the public meshshot render API."""

from __future__ import annotations

import base64
from contextlib import contextmanager, ExitStack
import errno
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import statistics
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from PIL import Image

from tests.python.support.paths import add_repo_path

add_repo_path("packages/meshshot/src")

from meshshot import MeshGeometry, MeshshotError, render_residual_preview  # noqa: E402


def _geometry(*triangles: tuple[tuple[float, float, float], ...]) -> MeshGeometry:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    for triangle in triangles:
        start = len(vertices)
        vertices.extend([list(vertex) for vertex in triangle])
        faces.append([start, start + 1, start + 2])
    return MeshGeometry(vertices=vertices, faces=faces)


def _runtime_patch(browser: object) -> tuple[mock._patch, mock.MagicMock]:
    runtime = mock.MagicMock()
    runtime.evidence = {
        "schema": "meshshot.prelaunched-cdp-runtime/1",
        "adapter_profile": {
            "name": "playwright-1.60-chromium-1223-loopback-cdp/1",
            "sha256": "16ef68d9ee9700f10c9e92b6ca88c0430dc98c6808145258f9a6125f3acd5c04",
        },
        "browser_identity": {
            "playwright": "1.60.0",
            "browser": "chromium-headless-shell",
            "revision": "1223",
            "version": "Google Chrome for Testing 148.0.7778.96",
            "sha256": "2" * 64,
        },
        "result": "passed",
    }

    @contextmanager
    def opened(_chromium: object):
        yield browser

    runtime.open.side_effect = opened
    runtime_class = mock.MagicMock(return_value=runtime)
    return mock.patch("meshshot.renderer.PrelaunchedCdpRuntime", runtime_class), runtime


def _attested_connected_browser() -> mock.MagicMock:
    browser = mock.MagicMock()
    session = browser.new_browser_cdp_session.return_value
    session.send.return_value = {
        "product": "HeadlessChrome/148.0.7778.96",
    }
    return browser


class ResidualRendererTests(unittest.TestCase):
    def test_attachment_requires_exact_supervisor_peer_and_fresh_packet_nonce(
        self,
    ) -> None:
        from meshshot import browser_runtime

        attachment = object.__new__(
            browser_runtime.SupervisedCdpAttachmentRuntime
        )
        connection = mock.MagicMock()
        connection.getsockopt.return_value = struct.pack("3i", 4242, os.geteuid(), os.getegid())
        attachment._validate_supervisor_peer(connection, expected_pid=4242)
        with self.assertRaises(browser_runtime.BrowserRuntimeError):
            attachment._validate_supervisor_peer(connection, expected_pid=4343)

        nonce = "a" * 64
        attachment._profile = {"startup_timeout_ms": 100}
        attachment.evidence = {"schema": browser_runtime.RUNTIME_SCHEMA}
        authority = {
            "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
            "type": "authority",
            "nonce": nonce,
            "endpoint": "http://127.0.0.1:9222",
            "process_group": 42,
            "listener_reproof": "passed",
            "browser_runtime": attachment.evidence,
        }
        with mock.patch.object(browser_runtime, "_verify_listener_owner"):
            attachment._validate_authority(authority, expected_nonce=nonce)
            with self.assertRaises(browser_runtime.BrowserRuntimeError):
                attachment._validate_authority(
                    authority,
                    expected_nonce="b" * 64,
                )

    def test_supervisor_rejects_same_uid_foreign_first_client_and_replay(self) -> None:
        from meshshot import browser_supervisor

        server = mock.MagicMock()
        foreign = mock.MagicMock()
        expected = mock.MagicMock()
        server.accept.side_effect = [(foreign, None), (expected, None)]
        credentials = {
            id(foreign): (1111, os.geteuid(), os.getegid()),
            id(expected): (2222, os.geteuid(), os.getegid()),
        }
        nonce = "a" * 64
        with (
            mock.patch.object(
                browser_supervisor,
                "_peer_credentials",
                side_effect=lambda connection: credentials[id(connection)],
                create=True,
            ),
            mock.patch.object(
                browser_supervisor,
                "_receive_supervisor_packet",
                side_effect=[
                    {"schema": "meshshot.browser-supervisor/1", "type": "hello", "nonce": nonce},
                    {"schema": "meshshot.browser-supervisor/1", "type": "hello", "nonce": nonce},
                ],
            ),
        ):
            accepted = browser_supervisor._accept_authenticated_client(
                server,
                expected_pid=2222,
                nonce=nonce,
                deadline=time.monotonic() + 1,
            )
        self.assertIs(expected, accepted)
        foreign.close.assert_called_once()

        with self.assertRaises(Exception):
            browser_supervisor._validate_message(
                {"schema": "meshshot.browser-supervisor/1", "type": "completion", "nonce": nonce, "result": "passed"},
                expected_type="completion",
                nonce="b" * 64,
            )

    def test_supervisor_entry_unblocks_inherited_runtime_signals_before_run(
        self,
    ) -> None:
        from meshshot import browser_supervisor

        calls: list[object] = []
        with (
            mock.patch.object(
                browser_supervisor.signal,
                "pthread_sigmask",
                side_effect=lambda how, mask: calls.append((how, set(mask))),
                create=True,
            ),
            mock.patch.object(
                browser_supervisor,
                "run",
                side_effect=lambda: calls.append("run"),
            ),
        ):
            self.assertEqual(0, browser_supervisor.main())

        self.assertEqual(
            [
                (
                    signal.SIG_UNBLOCK,
                    {signal.SIGINT, signal.SIGTERM},
                ),
                "run",
            ],
            calls,
        )

    def test_owned_socket_cleanup_rejects_replacement_without_deleting_it(self) -> None:
        from meshshot import browser_supervisor

        unlink_owned_socket = browser_supervisor._unlink_owned_socket
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            endpoint = root / "authority.sock"
            endpoint.touch()
            original = endpoint.lstat()
            endpoint.unlink()
            endpoint.write_text("replacement", encoding="utf-8")
            descriptor = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaises(Exception):
                    unlink_owned_socket(
                        descriptor,
                        (original.st_dev, original.st_ino),
                    )
            finally:
                os.close(descriptor)
            self.assertEqual("replacement", endpoint.read_text(encoding="utf-8"))

    def test_supervisor_protocol_rejects_duplicate_unknown_and_oversize_packets(
        self,
    ) -> None:
        from meshshot import browser_runtime

        for packet in (
            b'{"schema":"meshshot.browser-supervisor/1","type":"hello","type":"authority"}',
            b'{"schema":"meshshot.browser-supervisor/1","type":"hello","unknown":true}',
            b"x" * (browser_runtime._SUPERVISOR_PACKET_LIMIT + 1),
        ):
            with self.subTest(size=len(packet)):
                connection = mock.MagicMock()
                connection.recv.return_value = packet
                with self.assertRaises(browser_runtime.BrowserRuntimeError):
                    value = browser_runtime._receive_supervisor_packet(connection)
                    if value != {
                        "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
                        "type": "hello",
                    }:
                        raise browser_runtime.BrowserRuntimeError("browser_connect")

    def test_attachment_rejects_foreign_endpoint_and_unbound_runtime_evidence(
        self,
    ) -> None:
        from meshshot import browser_runtime

        attachment = object.__new__(
            browser_runtime.SupervisedCdpAttachmentRuntime
        )
        attachment._profile = {
            "startup_timeout_ms": 100,
            "browser_version": "148.0.7778.96",
        }
        attachment.evidence = {"schema": browser_runtime.RUNTIME_SCHEMA}
        base = {
            "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
            "type": "authority",
            "nonce": "a" * 64,
            "endpoint": "http://127.0.0.1:9222",
            "process_group": 42,
            "listener_reproof": "passed",
            "browser_runtime": attachment.evidence,
        }
        for mutation, substage in (
            ({**base, "endpoint": "http://0.0.0.0:9222"}, "loopback_listener_address_ownership"),
            ({**base, "endpoint": "http://127.0.0.1.evil:9222"}, "loopback_listener_address_ownership"),
            ({**base, "browser_runtime": {"schema": browser_runtime.RUNTIME_SCHEMA, "extra": True}}, "runtime_evidence_cross_binding"),
            ({**base, "process_group": True}, "runtime_evidence_cross_binding"),
        ):
            with self.subTest(mutation=mutation), self.assertRaises(
                browser_runtime.BrowserRuntimeError
            ) as raised:
                attachment._validate_authority(
                    mutation,
                    expected_nonce="a" * 64,
                )
            self.assertEqual(substage, raised.exception.browser_identity_substage)

    def test_attachment_requires_authenticated_outer_listener_reproof(self) -> None:
        from meshshot import browser_runtime

        attachment = object.__new__(
            browser_runtime.SupervisedCdpAttachmentRuntime
        )
        attachment._profile = {
            "startup_timeout_ms": 100,
            "browser_version": "148.0.7778.96",
        }
        attachment.evidence = {"schema": browser_runtime.RUNTIME_SCHEMA}
        authority = {
            "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
            "type": "authority",
            "nonce": "a" * 64,
            "endpoint": "http://127.0.0.1:9222",
            "process_group": 42,
            "listener_reproof": "passed",
            "browser_runtime": attachment.evidence,
        }
        with mock.patch.object(
            browser_runtime,
            "_verify_listener_owner",
        ) as verify:
            self.assertEqual(
                ("http://127.0.0.1:9222", 42),
                attachment._validate_authority(
                    authority,
                    expected_nonce="a" * 64,
                ),
            )
        verify.assert_not_called()

        for label, mutation, substage in (
            ("missing", {key: value for key, value in authority.items() if key != "listener_reproof"}, "runtime_evidence_cross_binding"),
            ("tampered", {**authority, "listener_reproof": "failed"}, "runtime_evidence_cross_binding"),
            ("replay", {**authority, "nonce": "b" * 64}, "runtime_evidence_cross_binding"),
        ):
            with (
                self.subTest(label=label),
                self.assertRaises(browser_runtime.BrowserRuntimeError) as raised,
            ):
                attachment._validate_authority(
                    mutation,
                    expected_nonce="a" * 64,
                )
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                substage,
                raised.exception.browser_identity_substage,
            )

    def test_supervisor_authority_freshly_reproves_listener_before_publication(
        self,
    ) -> None:
        from meshshot import browser_runtime

        runtime = object.__new__(browser_runtime.PrelaunchedCdpRuntime)
        runtime._endpoint = "http://127.0.0.1:9222"
        runtime._process_group = 42
        runtime._profile = {"startup_timeout_ms": 100}
        runtime.evidence = {"schema": browser_runtime.RUNTIME_SCHEMA}
        with mock.patch.object(browser_runtime, "_verify_listener_owner") as verify:
            authority = runtime.supervisor_authority()
        verify.assert_called_once_with(42, 9222, 0.1)
        self.assertEqual("passed", authority["listener_reproof"])

        for label in ("browser-death", "foreign-listener", "port-replacement"):
            with (
                self.subTest(label=label),
                mock.patch.object(
                    browser_runtime,
                    "_verify_listener_owner",
                    side_effect=browser_runtime.BrowserRuntimeError("browser_identity"),
                ),
                self.assertRaises(browser_runtime.BrowserRuntimeError) as raised,
            ):
                runtime.supervisor_authority()
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "loopback_listener_address_ownership",
                raised.exception.browser_identity_substage,
            )

    def test_attachment_reports_failed_completion_before_closed_connect_error(
        self,
    ) -> None:
        from meshshot import browser_runtime

        attachment = object.__new__(
            browser_runtime.SupervisedCdpAttachmentRuntime
        )
        attachment._profile = {
            "startup_timeout_ms": 100,
            "browser_version": "148.0.7778.96",
        }
        attachment.evidence = {"schema": browser_runtime.RUNTIME_SCHEMA}
        authority = {
            "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
            "type": "authority",
            "nonce": "a" * 64,
            "endpoint": "http://127.0.0.1:9222",
            "process_group": 42,
            "listener_reproof": "passed",
            "browser_runtime": attachment.evidence,
        }
        connection = mock.MagicMock()
        chromium = mock.MagicMock()
        chromium.connect_over_cdp.side_effect = OSError("private raw failure")
        with (
            mock.patch.object(attachment, "_validate_socket_path"),
            mock.patch.object(
                attachment,
                "_client_authority",
                return_value=(4242, "a" * 64),
            ),
            mock.patch.object(attachment, "_validate_supervisor_peer"),
            mock.patch.object(
                browser_runtime.socket,
                "socket",
                return_value=connection,
            ),
            mock.patch.object(
                browser_runtime,
                "_receive_supervisor_packet",
                side_effect=[
                    authority,
                    {
                        "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
                        "type": "shutdown",
                        "nonce": "a" * 64,
                    },
                ],
            ),
            mock.patch.object(browser_runtime, "_verify_listener_owner"),
            mock.patch.object(
                browser_runtime,
                "_send_supervisor_packet",
            ) as send,
            self.assertRaises(browser_runtime.BrowserRuntimeError) as raised,
        ):
            with attachment.open(chromium):
                self.fail("failed connection must never yield")

        self.assertEqual("browser_connect", raised.exception.operation)
        self.assertEqual(
            {
                "schema": browser_runtime.SUPERVISOR_PROTOCOL_SCHEMA,
                "type": "completion",
                "nonce": "a" * 64,
                "result": "failed",
            },
            send.call_args_list[1].args[1],
        )

    def test_provider_profile_attaches_without_nested_browser_owner_spawn(
        self,
    ) -> None:
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": "data:image/png;base64,"
            + base64.b64encode(png.getvalue()).decode("ascii"),
            "views": [
                {"name": name}
                for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        attachment = mock.MagicMock()
        attachment.evidence = {
            "schema": "meshshot.prelaunched-cdp-runtime/1",
            "adapter_profile": {"name": "profile", "sha256": "a" * 64},
            "browser_identity": {
                "playwright": "1.60.0",
                "browser": "chromium-headless-shell",
                "revision": "1223",
                "version": "Google Chrome for Testing 148.0.7778.96",
                "sha256": "b" * 64,
            },
            "result": "passed",
        }
        attachment.open.return_value.__enter__.return_value = browser
        with (
            mock.patch.dict(
                os.environ,
                {
                    "MESHSHOT_BROWSER_RUNTIME_MODE": (
                        "provider-free-supervised-cdp/1"
                    ),
                    "MESHSHOT_BROWSER_EXECUTABLE": "/fixed/browser",
                },
                clear=False,
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            mock.patch(
                "meshshot.renderer.SupervisedCdpAttachmentRuntime",
                return_value=attachment,
                create=True,
            ) as supervised,
            mock.patch("meshshot.renderer.PrelaunchedCdpRuntime") as owning,
        ):
            result = render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("step", result.variant)
        supervised.assert_called_once_with(Path("/fixed/browser"))
        owning.assert_not_called()

    def test_linux_private_snapshot_executes_from_fixed_sandbox_root(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable_root = root / "meshshot-exec"
            executable_root.mkdir(mode=0o755)
            source_root = root / "browser-source"
            source_root.mkdir()
            executable = source_root / "chrome-headless-shell"
            executable.write_text(
                "#!/bin/sh\nprintf 'Google Chrome for Testing 148.0.7778.96\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)

            with (
                mock.patch.object(
                    browser_runtime,
                    "MESHSHOT_EXECUTABLE_ROOT",
                    executable_root,
                    create=True,
                ),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(
                    _PinnedExecutable,
                    "_sealed_snapshot_fd",
                    side_effect=lambda launch, _source_info: os.open(
                        launch, os.O_RDONLY
                    ),
                ),
                mock.patch.dict(
                    os.environ,
                    {
                        "MESHSHOT_EXECUTABLE_ROOT": os.fspath(
                            executable_root
                        )
                    },
                ),
            ):
                pinned = _PinnedExecutable(executable)
            try:
                assert pinned.launch_root is not None
                self.assertEqual(executable_root, pinned.launch_root.parent)
                completed = pinned.run_version(timeout=5)
                self.assertEqual(0, completed.returncode)
                self.assertEqual(
                    b"Google Chrome for Testing 148.0.7778.96\n",
                    completed.stdout,
                )
                self.assertEqual(b"", completed.stderr)
            finally:
                pinned.close()

    def test_public_render_closes_version_exec_permission_and_timeout(self) -> None:
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            for failure, check in (
                (
                    PermissionError(13, "private path and argv must stay closed"),
                    "private_version_probe_completion",
                ),
                (
                    subprocess.TimeoutExpired(
                        ["/private/raw-browser", "--version"],
                        15,
                        stderr=b"private stderr",
                    ),
                    "private_version_probe_timeout",
                ),
            ):
                with (
                    self.subTest(failure=type(failure).__name__),
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                    ),
                    mock.patch(
                        "playwright.sync_api.sync_playwright", sync_playwright
                    ),
                    mock.patch(
                        "meshshot.browser_runtime.metadata.version",
                        return_value="1.60.0",
                    ),
                    mock.patch(
                        "meshshot.browser_runtime._playwright_revision",
                        return_value="1223",
                    ),
                    mock.patch(
                        "meshshot.browser_runtime._PinnedExecutable.run_version",
                        side_effect=failure,
                    ),
                    self.assertRaises(MeshshotError) as raised,
                ):
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )
                self.assertEqual("browser_identity", raised.exception.phase)
                self.assertEqual(
                    "private_snapshot_launch_image_identity",
                    raised.exception.browser_identity_substage,
                )
                self.assertEqual(
                    "private_launch_version_execution",
                    raised.exception.browser_identity_phase,
                )
                self.assertEqual(check, raised.exception.browser_identity_check)
                self.assertNotIn("private", str(raised.exception))
                self.assertNotIn("stderr", str(raised.exception))

    def test_linux_private_snapshot_rejects_unowned_or_arbitrary_roots(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            expected_root = root / "meshshot-exec"
            expected_root.mkdir(mode=0o755)
            cases = (
                (root / "arbitrary", expected_root),
                (expected_root, root / "missing"),
            )
            for configured, actual in cases:
                with (
                    self.subTest(configured=configured, actual=actual),
                    mock.patch.object(
                        browser_runtime,
                        "MESHSHOT_EXECUTABLE_ROOT",
                        actual,
                    ),
                    mock.patch.object(browser_runtime.sys, "platform", "linux"),
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_EXECUTABLE_ROOT": os.fspath(configured)},
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    _PinnedExecutable(executable)
                self.assertEqual("browser_identity", raised.exception.operation)
                self.assertEqual(
                    "private_tree_materialization",
                    raised.exception.browser_identity_phase,
                )

            os.chmod(expected_root, 0o777)
            with (
                mock.patch.object(
                    browser_runtime,
                    "MESHSHOT_EXECUTABLE_ROOT",
                    expected_root,
                ),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_EXECUTABLE_ROOT": os.fspath(expected_root)},
                ),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable(executable)
            self.assertEqual(
                "private_tree_materialization",
                raised.exception.browser_identity_phase,
            )

    def test_linux_private_snapshot_rejects_root_owned_by_another_uid(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            executable_root = root / "meshshot-exec"
            executable_root.mkdir(mode=0o755)
            foreign_uid = executable_root.stat().st_uid + 1
            with (
                mock.patch.object(
                    browser_runtime,
                    "MESHSHOT_EXECUTABLE_ROOT",
                    executable_root,
                ),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(
                    browser_runtime.os,
                    "geteuid",
                    return_value=foreign_uid,
                ),
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_EXECUTABLE_ROOT": os.fspath(executable_root)},
                ),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable(executable)
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "private_tree_materialization",
                raised.exception.browser_identity_phase,
            )

    def test_default_executable_closes_final_candidate_disappearance_and_swap(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, default_executable

        for mutation in ("disappear", "symlink_swap"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                full_browser = (
                    root
                    / "chromium-1223"
                    / "chrome-mac"
                    / "Google Chrome for Testing"
                )
                candidate = (
                    root
                    / "chromium_headless_shell-1223"
                    / "chrome-headless-shell-mac-arm64"
                    / "chrome-headless-shell"
                )
                replacement = root / "outside-browser"
                for executable in (full_browser, candidate, replacement):
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    executable.chmod(0o755)

                original_resolve = Path.resolve
                mutated = False

                def mutate_on_final_resolve(
                    path: Path,
                    strict: bool = False,
                ) -> Path:
                    nonlocal mutated
                    if (
                        path.name == "chrome-headless-shell"
                        and path.parent.name == "chrome-headless-shell-mac-arm64"
                        and not mutated
                    ):
                        mutated = True
                        candidate.unlink()
                        if mutation == "symlink_swap":
                            candidate.symlink_to(replacement)
                    return original_resolve(path, strict=strict)

                with mock.patch.object(Path, "resolve", mutate_on_final_resolve), self.assertRaises(
                    BrowserRuntimeError
                ) as raised:
                    default_executable(os.fspath(full_browser))
                self.assertTrue(mutated)
                self.assertEqual("browser_identity", raised.exception.operation)
                self.assertEqual(
                    "private_snapshot_launch_image_identity",
                    raised.exception.browser_identity_substage,
                )
                self.assertEqual(
                    "source_executable_identity",
                    raised.exception.browser_identity_phase,
                )

    def test_public_render_closes_default_executable_race_before_pinning(self) -> None:
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        for mutation in ("disappear", "symlink_swap"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                full_browser = (
                    root
                    / "chromium-1223"
                    / "chrome-mac"
                    / "Google Chrome for Testing"
                )
                candidate = (
                    root
                    / "chromium_headless_shell-1223"
                    / "chrome-headless-shell-mac-arm64"
                    / "chrome-headless-shell"
                )
                replacement = root / "outside-browser"
                for executable in (full_browser, candidate, replacement):
                    executable.parent.mkdir(parents=True, exist_ok=True)
                    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                    executable.chmod(0o755)

                original_resolve = Path.resolve
                mutated = False

                def mutate_on_final_resolve(
                    path: Path,
                    strict: bool = False,
                ) -> Path:
                    nonlocal mutated
                    if (
                        path.name == "chrome-headless-shell"
                        and path.parent.name == "chrome-headless-shell-mac-arm64"
                        and not mutated
                    ):
                        mutated = True
                        candidate.unlink()
                        if mutation == "symlink_swap":
                            candidate.symlink_to(replacement)
                    return original_resolve(path, strict=strict)

                sync_playwright = mock.MagicMock()
                sync_playwright.return_value.__enter__.return_value.chromium.executable_path = (
                    os.fspath(full_browser)
                )
                runtime = mock.patch("meshshot.renderer.PrelaunchedCdpRuntime")
                with (
                    mock.patch.dict(os.environ, {}, clear=False),
                    mock.patch.object(Path, "resolve", mutate_on_final_resolve),
                    mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
                    runtime as runtime_class,
                ):
                    os.environ.pop("MESHSHOT_BROWSER_EXECUTABLE", None)
                    with self.assertRaises(MeshshotError) as raised:
                        render_residual_preview(
                            _geometry(triangle),
                            _geometry(triangle),
                            variant="step",
                        )
                self.assertTrue(mutated)
                self.assertEqual("browser_identity", raised.exception.phase)
                self.assertEqual(
                    "private_snapshot_launch_image_identity",
                    raised.exception.browser_identity_substage,
                )
                self.assertEqual(
                    "source_executable_identity",
                    raised.exception.browser_identity_phase,
                )
                runtime_class.assert_not_called()

    def test_public_render_rejects_regular_replacement_before_authoritative_open(
        self,
    ) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            full_browser = (
                root
                / "chromium-1223"
                / "chrome-mac"
                / "Google Chrome for Testing"
            )
            candidate = (
                root
                / "chromium_headless_shell-1223"
                / "chrome-headless-shell-mac-arm64"
                / "chrome-headless-shell"
            )
            replacement = root / "replacement-browser"
            full_browser.parent.mkdir(parents=True, exist_ok=True)
            full_browser.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            full_browser.chmod(0o755)
            candidate.parent.mkdir(parents=True, exist_ok=True)
            original_bytes = b"#!/bin/sh\nprintf original\n"
            replacement_bytes = b"#!/bin/sh\nprintf replaced\n"
            self.assertEqual(len(original_bytes), len(replacement_bytes))
            candidate.write_bytes(original_bytes)
            candidate.chmod(0o755)
            replacement.write_bytes(replacement_bytes)
            replacement.chmod(0o755)

            real_open = os.open
            swapped = False

            def swap_before_open(path: object, flags: int, *args: object) -> int:
                nonlocal swapped
                if Path(path) == candidate and not swapped:
                    swapped = True
                    os.replace(replacement, candidate)
                return real_open(path, flags, *args)

            observed: list[bytes] = []

            def observe_source(
                _pinned: _PinnedExecutable,
                fd: int,
                _source_info: os.stat_result,
            ) -> None:
                observed.append(os.pread(fd, len(replacement_bytes), 0))
                raise BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="private_tree_materialization",
                )

            sync_playwright = mock.MagicMock()
            sync_playwright.return_value.__enter__.return_value.chromium.executable_path = (
                os.fspath(full_browser)
            )
            with (
                mock.patch.dict(os.environ, {}, clear=False),
                mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
                mock.patch("meshshot.browser_runtime.os.open", swap_before_open),
                mock.patch.object(
                    _PinnedExecutable,
                    "_materialize_private_image",
                    autospec=True,
                    side_effect=observe_source,
                ) as materialize,
            ):
                os.environ.pop("MESHSHOT_BROWSER_EXECUTABLE", None)
                with self.assertRaises(MeshshotError) as raised:
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )
            self.assertTrue(swapped)
            self.assertEqual([], observed)
            materialize.assert_not_called()
            self.assertEqual("browser_identity", raised.exception.phase)
            self.assertEqual(
                "private_snapshot_launch_image_identity",
                raised.exception.browser_identity_substage,
            )
            self.assertEqual(
                "source_executable_identity",
                raised.exception.browser_identity_phase,
            )

    def test_public_render_rejects_evil_prefix_as_outside_origin(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        runtime_patch, _runtime = _runtime_patch(browser)
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        routed: list[str] = []
        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")

        def expose_sealed_route(_script: str) -> object:
            sealed_route = context.route.call_args.args[1]
            cases = (
                ("http://meshshot.local/payload.json", "continued"),
                ("http://meshshot.local:80/payload.json", "continued"),
                ("http://meshshot.local.evil/payload.json", "aborted"),
                ("http://user@meshshot.local/payload.json", "aborted"),
                ("http://meshshot.local:81/payload.json", "aborted"),
                ("https://meshshot.local/payload.json", "aborted"),
            )
            for url, _expected in cases:
                route = mock.MagicMock()
                route.request.url = url
                sealed_route(route)
                if route.abort.call_args == mock.call("blockedbyclient"):
                    routed.append("aborted")
                elif route.continue_.called:
                    routed.append("continued")
            return {
                "ok": True,
                "pngDataUrl": "data:image/png;base64,"
                + base64.b64encode(png.getvalue()).decode("ascii"),
                "views": [
                    {"name": name}
                    for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
                ],
            }

        page.evaluate.side_effect = expose_sealed_route
        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            runtime_patch,
        ):
            render_residual_preview(_geometry(triangle), _geometry(triangle), variant="step")
        self.assertEqual(
            ["continued", "continued", "aborted", "aborted", "aborted", "aborted"],
            routed,
        )

    def test_public_render_can_run_from_non_main_thread_without_signal_mutation(self) -> None:
        from meshshot.browser_runtime import _runtime_signal_cleanup

        observed: list[str] = []

        def worker() -> None:
            try:
                with mock.patch("signal.signal") as install:
                    with _runtime_signal_cleanup():
                        observed.append("entered")
                    install.assert_not_called()
            except BaseException as exc:  # pragma: no cover - assertion reports value
                observed.append(type(exc).__name__)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(["entered"], observed)

    def test_signal_cleanup_restores_and_dispatches_custom_handlers_after_cleanup(self) -> None:
        import signal
        from meshshot.browser_runtime import _RuntimeSignal, _runtime_signal_cleanup

        calls: list[tuple[str, int]] = []

        def previous(signum: int, _frame: object) -> None:
            calls.append(("previous", signum))

        for caught in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(caught=caught):
                calls.clear()
                handlers = {signal.SIGINT: previous, signal.SIGTERM: previous}
                installed: dict[int, object] = {}

                def getsignal(signum: int) -> object:
                    return handlers[signum]

                def set_signal(signum: int, handler: object) -> object:
                    installed[signum] = handler
                    handlers[signum] = handler
                    return handler

                with (
                    mock.patch("signal.getsignal", side_effect=getsignal),
                    mock.patch("signal.signal", side_effect=set_signal),
                    self.assertRaises(_RuntimeSignal) as raised,
                ):
                    with _runtime_signal_cleanup():
                        installed[caught](caught, None)

                self.assertEqual(caught, raised.exception.signum)
                self.assertIs(previous, handlers[signal.SIGINT])
                self.assertIs(previous, handlers[signal.SIGTERM])
                self.assertEqual([("previous", caught)], calls)

    def test_signal_cleanup_preserves_ignored_handler_without_interception(self) -> None:
        import signal
        from meshshot.browser_runtime import _runtime_signal_cleanup

        handlers = {signal.SIGINT: signal.SIG_IGN, signal.SIGTERM: signal.SIG_IGN}
        with (
            mock.patch("signal.getsignal", side_effect=lambda signum: handlers[signum]),
            mock.patch("signal.signal") as install,
        ):
            with _runtime_signal_cleanup():
                pass
        install.assert_not_called()

    def test_signal_cleanup_redispatches_default_handler_after_restoration(self) -> None:
        import signal
        from meshshot.browser_runtime import _RuntimeSignal, _runtime_signal_cleanup

        handlers = {signal.SIGINT: signal.SIG_DFL, signal.SIGTERM: signal.SIG_DFL}
        installed: dict[int, object] = {}

        def set_signal(signum: int, handler: object) -> object:
            installed[signum] = handler
            handlers[signum] = handler
            return handler

        with (
            mock.patch("signal.getsignal", side_effect=lambda signum: handlers[signum]),
            mock.patch("signal.signal", side_effect=set_signal),
            mock.patch("os.kill") as redispatch,
            self.assertRaises(_RuntimeSignal),
        ):
            with _runtime_signal_cleanup():
                installed[signal.SIGTERM](signal.SIGTERM, None)
        self.assertIs(signal.SIG_DFL, handlers[signal.SIGTERM])
        redispatch.assert_called_once_with(os.getpid(), signal.SIGTERM)

    def test_prelaunched_runtime_cleans_before_sigint_and_sigterm_custom_dispatch(self) -> None:
        import signal
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        for caught in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(caught=caught), tempfile.TemporaryDirectory() as directory:
                events: list[str] = []
                process = mock.MagicMock(spec=subprocess.Popen)
                process.pid = 43210
                process.wait.side_effect = lambda **_kwargs: events.append("cleanup") or 0
                runtime = object.__new__(PrelaunchedCdpRuntime)
                runtime._profile = {
                    "browser_version": "148.0.7778.96",
                    "startup_timeout_ms": 1,
                    "cleanup_term_ms": 1,
                    "cleanup_kill_ms": 1,
                }
                runtime._profile_dir = Path(directory) / "profile"
                runtime._profile_dir.mkdir()
                runtime._profile_parent_fd = os.open(directory, os.O_RDONLY)
                runtime._profile_fd = os.open(runtime._profile_dir, os.O_RDONLY)
                profile_info = os.fstat(runtime._profile_fd)
                runtime._profile_identity = (
                    profile_info.st_dev,
                    profile_info.st_ino,
                )
                runtime._profile_cleanup_forbidden = False
                runtime._process = process
                runtime._process_group = 43210
                chromium = mock.MagicMock()
                chromium.connect_over_cdp.return_value = _attested_connected_browser()
                installed: dict[int, object] = {}

                def previous(_signum: int, _frame: object) -> None:
                    events.append("previous")

                handlers = {signal.SIGINT: previous, signal.SIGTERM: previous}

                def set_signal(signum: int, handler: object) -> object:
                    installed[signum] = handler
                    handlers[signum] = handler
                    return handler

                with (
                    mock.patch.object(runtime, "_prelaunch", return_value="http://127.0.0.1:49152"),
                    mock.patch("signal.getsignal", side_effect=lambda signum: handlers[signum]),
                    mock.patch("signal.signal", side_effect=set_signal),
                    mock.patch(
                        "os.killpg",
                        side_effect=lambda _pgid, signum: (
                            (_ for _ in ()).throw(ProcessLookupError())
                            if signum == 0
                            else None
                        ),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    with runtime.open(chromium):
                        installed[caught](caught, None)
                self.assertEqual("browser_signal", raised.exception.operation)
                self.assertEqual(["cleanup", "previous"], events)
                self.assertFalse(runtime._profile_dir.exists())

    def test_signal_dispatch_survives_terminal_cleanup_failure(self) -> None:
        import signal
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        events: list[str] = []
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {
            "browser_version": "148.0.7778.96",
            "startup_timeout_ms": 1,
        }
        chromium = mock.MagicMock()
        chromium.connect_over_cdp.return_value = _attested_connected_browser()
        installed: dict[int, object] = {}

        def previous(_signum: int, _frame: object) -> None:
            events.append("previous")

        handlers = {signal.SIGINT: previous, signal.SIGTERM: previous}

        def set_signal(signum: int, handler: object) -> object:
            installed[signum] = handler
            handlers[signum] = handler
            return handler

        def fail_cleanup() -> None:
            events.append("cleanup")
            raise BrowserRuntimeError("browser_cleanup")

        with (
            mock.patch.object(
                runtime, "_prelaunch", return_value="http://127.0.0.1:49152"
            ),
            mock.patch.object(runtime, "_cleanup", side_effect=fail_cleanup),
            mock.patch("signal.getsignal", side_effect=lambda signum: handlers[signum]),
            mock.patch("signal.signal", side_effect=set_signal),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            with runtime.open(chromium):
                installed[signal.SIGTERM](signal.SIGTERM, None)
        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertEqual(["cleanup", "previous"], events)

    def test_prelaunched_runtime_readiness_timeout_reaps_and_removes_profile(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            runtime = object.__new__(PrelaunchedCdpRuntime)
            runtime._profile = {
                "arguments": [], "startup_timeout_ms": 0,
                "cleanup_term_ms": 1, "cleanup_kill_ms": 1,
            }
            runtime._executable = Path(os.__file__)
            runtime._pinned_executable = mock.MagicMock()
            runtime._pinned_executable.popen.return_value = process
            runtime._profile_dir = None
            runtime._process = None
            runtime._process_group = None
            with (
                mock.patch(
                    "tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch("os.getpgid", return_value=43210),
                mock.patch(
                    "os.killpg",
                    side_effect=lambda _pgid, signum: (
                        (_ for _ in ()).throw(ProcessLookupError())
                        if signum == 0
                        else None
                    ),
                ),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                runtime._prelaunch()
            self.assertEqual("browser_readiness_timeout", raised.exception.operation)
            self.assertFalse(profile.exists())

    def test_profile_creation_failure_closes_private_browser_snapshot(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            with mock.patch(
                "meshshot.browser_runtime._attest",
                return_value={
                    "playwright": "1.60.0",
                    "browser": "chromium-headless-shell",
                    "revision": "1223",
                    "version": "Google Chrome for Testing 148.0.7778.96",
                    "sha256": "2" * 64,
                },
            ):
                runtime = PrelaunchedCdpRuntime(executable)
            launch_root = runtime._pinned_executable.launch_root
            assert launch_root is not None
            try:
                with (
                    mock.patch(
                        "tempfile.mkdtemp",
                        side_effect=OSError("sensitive profile setup"),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    runtime._prelaunch()
                self.assertEqual("browser_profile", raised.exception.operation)
                self.assertFalse(launch_root.exists())
            finally:
                runtime._pinned_executable.close()

    def test_spawned_session_uses_pid_as_group_before_readiness_failure(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 54321
        process.poll.return_value = None
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            runtime = object.__new__(PrelaunchedCdpRuntime)
            runtime._profile = {
                "arguments": [], "startup_timeout_ms": 0,
                "cleanup_term_ms": 0, "cleanup_kill_ms": 0,
            }
            runtime._executable = Path(os.__file__)
            runtime._pinned_executable = mock.MagicMock()
            runtime._pinned_executable.popen.return_value = process
            runtime._profile_dir = None
            runtime._process = None
            runtime._process_group = None
            with (
                mock.patch(
                    "tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch("os.getpgid", side_effect=OSError("lost pgid")) as getpgid,
                mock.patch(
                    "os.killpg",
                    side_effect=lambda _pgid, signum: (
                        (_ for _ in ()).throw(ProcessLookupError())
                        if signum == 0
                        else None
                    ),
                ) as killpg,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                runtime._prelaunch()
            self.assertEqual("browser_readiness_timeout", raised.exception.operation)
            getpgid.assert_not_called()
            self.assertIn(
                mock.call(54321, __import__("signal").SIGTERM),
                killpg.mock_calls,
            )
            self.assertFalse(profile.exists())

    def test_signal_delivery_after_popen_observes_owned_process_group(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 54321
        process.poll.return_value = None
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            runtime = object.__new__(PrelaunchedCdpRuntime)
            runtime._profile = {
                "arguments": [], "startup_timeout_ms": 0,
                "cleanup_term_ms": 0, "cleanup_kill_ms": 0,
            }
            runtime._executable = Path(os.__file__)
            runtime._pinned_executable = mock.MagicMock()
            runtime._pinned_executable.popen.return_value = process
            runtime._profile_dir = None
            runtime._process = None
            runtime._process_group = None
            chromium = mock.MagicMock()
            installed: dict[int, object] = {}
            previous_events: list[str] = []

            def previous(_signum: int, _frame: object) -> None:
                previous_events.append("previous")

            handlers = {signal.SIGINT: previous, signal.SIGTERM: previous}

            def set_signal(signum: int, handler: object) -> object:
                installed[signum] = handler
                handlers[signum] = handler
                return handler

            mask_calls = 0

            def pthread_sigmask(how: int, mask: object) -> set[int]:
                nonlocal mask_calls
                mask_calls += 1
                if how == signal.SIG_SETMASK:
                    self.assertEqual(54321, runtime._process_group)
                    installed[signal.SIGTERM](signal.SIGTERM, None)
                return set()

            with (
                mock.patch(
                    "tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch("signal.getsignal", side_effect=lambda signum: handlers[signum]),
                mock.patch("signal.signal", side_effect=set_signal),
                mock.patch("signal.pthread_sigmask", side_effect=pthread_sigmask),
                mock.patch(
                    "os.killpg",
                    side_effect=lambda _pgid, signum: (
                        (_ for _ in ()).throw(ProcessLookupError())
                        if signum == 0
                        else None
                    ),
                ) as killpg,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                with runtime.open(chromium):
                    self.fail("signal should interrupt before browser attach")
            self.assertEqual("browser_signal", raised.exception.operation)
            self.assertGreaterEqual(mask_calls, 2)
            self.assertIn(
                mock.call(54321, __import__("signal").SIGTERM),
                killpg.mock_calls,
            )
            self.assertEqual(["previous"], previous_events)

    def test_browser_crash_is_projected_as_closed_public_render_failure(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = _attested_connected_browser()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        page.evaluate.side_effect = RuntimeError(
            "sensitive crash pid=43210 endpoint=http://127.0.0.1:49152"
        )
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        runtime_patch, _runtime = _runtime_patch(browser)
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            runtime_patch,
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(_geometry(triangle), _geometry(triangle), variant="step")
        self.assertEqual("browser_render", raised.exception.phase)
        self.assertNotIn("43210", str(raised.exception))
        self.assertNotIn("127.0.0.1", str(raised.exception))

    def test_prelaunched_runtime_rejects_nonempty_colliding_profile_before_spawn(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "collision"
            profile.mkdir()
            (profile / "stale").write_text("owned elsewhere", encoding="utf-8")
            runtime = object.__new__(PrelaunchedCdpRuntime)
            runtime._profile = {
                "arguments": [],
                "startup_timeout_ms": 1,
                "cleanup_term_ms": 1,
                "cleanup_kill_ms": 1,
            }
            runtime._executable = Path(os.__file__)
            runtime._profile_dir = None
            runtime._process = None
            runtime._process_group = None
            with (
                mock.patch("tempfile.mkdtemp", return_value=os.fspath(profile)),
                mock.patch("subprocess.Popen") as popen,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                runtime._prelaunch()
            self.assertEqual("browser_cleanup", raised.exception.operation)
            popen.assert_not_called()
            self.assertTrue(profile.is_dir())
            self.assertEqual("owned elsewhere", (profile / "stale").read_text(encoding="utf-8"))

    def test_prelaunched_runtime_rejects_wrong_browser_identity_before_spawn(self) -> None:
        playwright = mock.MagicMock()
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        wrong_version = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"Google Chrome for Testing 148.0.7778.95\n",
            stderr=b"",
        )
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_bytes(b"wrong-browser")
            executable.chmod(0o755)
            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
                mock.patch("subprocess.run", return_value=wrong_version),
                mock.patch("subprocess.Popen") as popen,
                self.assertRaises(MeshshotError) as raised,
            ):
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

        self.assertEqual("browser_identity", raised.exception.phase)
        self.assertEqual(
            "private_snapshot_launch_image_identity",
            raised.exception.browser_identity_substage,
        )
        self.assertEqual(
            "private_launch_version_output_identity",
            raised.exception.browser_identity_phase,
        )
        popen.assert_not_called()

    def test_public_render_preserves_each_private_snapshot_phase(self) -> None:
        from meshshot.browser_runtime import (
            BrowserRuntimeError,
            PRIVATE_SNAPSHOT_IDENTITY_PHASES,
        )

        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        for phase in sorted(PRIVATE_SNAPSHOT_IDENTITY_PHASES):
            with self.subTest(phase=phase), mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": "/attested/chrome-headless-shell"},
            ), mock.patch(
                "playwright.sync_api.sync_playwright",
                sync_playwright,
            ), mock.patch(
                "meshshot.renderer.PrelaunchedCdpRuntime",
                side_effect=BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase=phase,
                ),
            ), self.assertRaises(MeshshotError) as raised:
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )
            self.assertEqual("browser_identity", raised.exception.phase)
            self.assertEqual(
                "private_snapshot_launch_image_identity",
                raised.exception.browser_identity_substage,
            )
            self.assertEqual(
                phase,
                raised.exception.browser_identity_phase,
            )

    def test_public_render_preserves_each_playwright_package_revision_check(
        self,
    ) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError

        checks = (
            "python_distribution_metadata",
            "playwright_package_manifest",
            "browser_manifest_entry",
            "frozen_playwright_version_match",
            "frozen_browser_revision_match",
        )
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        for check in checks:
            with self.subTest(check=check), mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": "/attested/chrome-headless-shell"},
            ), mock.patch(
                "playwright.sync_api.sync_playwright",
                sync_playwright,
            ), mock.patch(
                "meshshot.renderer.PrelaunchedCdpRuntime",
                side_effect=BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="playwright_package_revision_identity",
                    browser_identity_check=check,
                ),
            ), self.assertRaises(MeshshotError) as raised:
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )
            self.assertEqual("browser_identity", raised.exception.phase)
            self.assertEqual(
                "private_snapshot_launch_image_identity",
                raised.exception.browser_identity_substage,
            )
            self.assertEqual(
                "playwright_package_revision_identity",
                raised.exception.browser_identity_phase,
            )
            self.assertEqual(check, raised.exception.browser_identity_check)

    def test_public_render_preserves_each_private_version_execution_check(
        self,
    ) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError

        checks = (
            "sealed_memfd_creation_policy",
            "private_version_helper_spawn_executable_missing",
            "private_version_helper_spawn_permission",
            "private_version_helper_spawn_process_limit",
            "private_version_helper_spawn_file_limit",
            "private_version_helper_spawn_address_space",
            "private_version_helper_spawn_other",
            "private_version_handoff_setup",
            "private_version_handoff_timeout",
            "private_version_helper_exec",
            "private_version_exec_replacement",
            "private_version_probe_completion",
            "private_version_probe_timeout",
        )
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        for check in checks:
            with self.subTest(check=check), mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": "/attested/chrome-headless-shell"},
            ), mock.patch(
                "playwright.sync_api.sync_playwright",
                sync_playwright,
            ), mock.patch(
                "meshshot.renderer.PrelaunchedCdpRuntime",
                side_effect=BrowserRuntimeError(
                    "browser_identity",
                    browser_identity_phase="private_launch_version_execution",
                    browser_identity_check=check,
                ),
            ), self.assertRaises(MeshshotError) as raised:
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )
            self.assertEqual(
                "private_launch_version_execution",
                raised.exception.browser_identity_phase,
            )
            self.assertEqual(check, raised.exception.browser_identity_check)

    def test_public_render_selects_exact_playwright_package_revision_check(
        self,
    ) -> None:
        from importlib import metadata

        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            original_read_text = Path.read_text

            def manifest_text(value: str):
                def read_text(path: Path, *args: object, **kwargs: object) -> str:
                    if path.name == "browsers.json":
                        return value
                    return original_read_text(path, *args, **kwargs)

                return read_text

            cases = (
                (
                    "python_distribution_metadata",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            side_effect=metadata.PackageNotFoundError("playwright"),
                        ),
                    ),
                ),
                (
                    "playwright_package_manifest",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch.object(Path, "read_text", manifest_text("{")),
                    ),
                ),
                (
                    "browser_manifest_entry",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch.object(
                            Path,
                            "read_text",
                            manifest_text('{"browsers":[]}'),
                        ),
                    ),
                ),
                (
                    "frozen_playwright_version_match",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="0.0.0",
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._playwright_revision",
                            return_value="1223",
                        ),
                    ),
                ),
                (
                    "frozen_browser_revision_match",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._playwright_revision",
                            return_value="9999",
                        ),
                    ),
                ),
            )
            for check, patchers in cases:
                with self.subTest(check=check), ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.dict(
                            os.environ,
                            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                        )
                    )
                    stack.enter_context(
                        mock.patch(
                            "playwright.sync_api.sync_playwright",
                            sync_playwright,
                        )
                    )
                    for patcher in patchers:
                        stack.enter_context(patcher)
                    with self.assertRaises(MeshshotError) as raised:
                        render_residual_preview(
                            _geometry(triangle),
                            _geometry(triangle),
                            variant="step",
                        )
                self.assertEqual("browser_identity", raised.exception.phase)
                self.assertEqual(
                    "playwright_package_revision_identity",
                    raised.exception.browser_identity_phase,
                )
                self.assertEqual(check, raised.exception.browser_identity_check)

    def test_public_render_closes_package_revision_parse_boundaries(self) -> None:
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            original_read_text = Path.read_text

            def manifest_text(value: str):
                def read_text(path: Path, *args: object, **kwargs: object) -> str:
                    if path.name == "browsers.json":
                        return value
                    return original_read_text(path, *args, **kwargs)

                return read_text

            def manifest_decode_error(
                path: Path,
                *args: object,
                **kwargs: object,
            ) -> str:
                if path.name == "browsers.json":
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")
                return original_read_text(path, *args, **kwargs)

            cases = (
                (
                    "browser_manifest_entry",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch.object(
                            Path,
                            "read_text",
                            manifest_text('{"browsers":[42]}'),
                        ),
                    ),
                ),
                (
                    "playwright_package_manifest",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch.object(Path, "read_text", manifest_decode_error),
                    ),
                ),
                (
                    "python_distribution_metadata",
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            side_effect=UnicodeDecodeError(
                                "utf-8", b"\xff", 0, 1, "invalid"
                            ),
                        ),
                    ),
                ),
            )
            observed: list[tuple[str | None, str | None]] = []
            for check, patchers in cases:
                with self.subTest(check=check), ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.dict(
                            os.environ,
                            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                        )
                    )
                    stack.enter_context(
                        mock.patch(
                            "playwright.sync_api.sync_playwright",
                            sync_playwright,
                        )
                    )
                    for patcher in patchers:
                        stack.enter_context(patcher)
                    with self.assertRaises(MeshshotError) as raised:
                        render_residual_preview(
                            _geometry(triangle),
                            _geometry(triangle),
                            variant="step",
                        )
                    observed.append(
                        (
                            raised.exception.phase,
                            raised.exception.browser_identity_check,
                        )
                    )
            self.assertEqual(
                [
                    ("browser_identity", "browser_manifest_entry"),
                    ("browser_identity", "playwright_package_manifest"),
                    ("browser_identity", "python_distribution_metadata"),
                ],
                observed,
            )

    def test_package_revision_identity_does_not_swallow_control_flow(self) -> None:
        from meshshot.browser_runtime import _playwright_revision

        for exception in (KeyboardInterrupt(), SystemExit(23)):
            with self.subTest(exception=type(exception).__name__), mock.patch.object(
                Path,
                "read_text",
                side_effect=exception,
            ), self.assertRaises(type(exception)):
                _playwright_revision("chromium-headless-shell")

    def test_version_execution_identity_does_not_swallow_control_flow(self) -> None:
        from meshshot.browser_runtime import _attest

        executable = mock.Mock()
        executable.sha256.return_value = "a" * 64
        profile = {
            "playwright": "1.60.0",
            "browser": "chromium-headless-shell",
            "revision": "1223",
            "startup_timeout_ms": 15000,
            "browser_version": "148.0.7778.96",
        }
        for exception in (KeyboardInterrupt(), SystemExit(23)):
            executable.run_version.side_effect = exception
            with (
                self.subTest(exception=type(exception).__name__),
                mock.patch(
                    "meshshot.browser_runtime.metadata.version",
                    return_value="1.60.0",
                ),
                mock.patch(
                    "meshshot.browser_runtime._playwright_revision",
                    return_value="1223",
                ),
                self.assertRaises(type(exception)),
            ):
                _attest(executable, profile)

    def test_public_render_selects_exact_private_snapshot_failure_phase(self) -> None:
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = mock.MagicMock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            (root / "resource.pak").write_bytes(b"resource")
            cases = (
                (
                    "source_executable_identity",
                    root / "missing-browser",
                    (),
                ),
                (
                    "private_tree_materialization",
                    executable,
                    (
                        mock.patch(
                            "meshshot.browser_runtime._PinnedExecutable._snapshot_resource",
                            side_effect=OSError("closed materialization failure"),
                        ),
                    ),
                ),
                (
                    "private_launch_image_identity",
                    executable,
                    (
                        mock.patch(
                            "meshshot.browser_runtime._PinnedExecutable._sha256_fd",
                            return_value="0" * 64,
                        ),
                    ),
                ),
                (
                    "playwright_package_revision_identity",
                    executable,
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="0.0.0",
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._playwright_revision",
                            return_value="1223",
                        ),
                    ),
                ),
                (
                    "private_launch_version_execution",
                    executable,
                    (
                        mock.patch(
                            "meshshot.browser_runtime.metadata.version",
                            return_value="1.60.0",
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._playwright_revision",
                            return_value="1223",
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._PinnedExecutable.run_version",
                            side_effect=OSError("closed version execution failure"),
                        ),
                    ),
                ),
            )
            for phase, configured_executable, patchers in cases:
                with self.subTest(phase=phase), ExitStack() as stack:
                    stack.enter_context(
                        mock.patch.dict(
                            os.environ,
                            {
                                "MESHSHOT_BROWSER_EXECUTABLE": os.fspath(
                                    configured_executable
                                )
                            },
                        )
                    )
                    stack.enter_context(
                        mock.patch(
                            "playwright.sync_api.sync_playwright",
                            sync_playwright,
                        )
                    )
                    for patcher in patchers:
                        stack.enter_context(patcher)
                    with self.assertRaises(MeshshotError) as raised:
                        render_residual_preview(
                            _geometry(triangle),
                            _geometry(triangle),
                            variant="step",
                        )
                self.assertEqual("browser_identity", raised.exception.phase)
                self.assertEqual(
                    "private_snapshot_launch_image_identity",
                    raised.exception.browser_identity_substage,
                )
                self.assertEqual(
                    phase,
                    raised.exception.browser_identity_phase,
                )

    def test_prelaunched_runtime_rejects_malformed_readiness_and_removes_profile(
        self,
    ) -> None:
        cases = (
            "49152\n/devtools/browser/good\nextra\n",
            "/devtools/browser/reordered\n49152\n",
            "0\n/devtools/browser/zero\n",
            "49152\n/devtools/page/not-a-browser\n",
        )
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        for index, readiness in enumerate(cases):
            with self.subTest(readiness=readiness):
                playwright = mock.MagicMock()
                sync_playwright = mock.MagicMock()
                sync_playwright.return_value.__enter__.return_value = playwright
                process = mock.MagicMock(spec=subprocess.Popen)
                process.pid = 43210 + index
                process.poll.return_value = None
                process.wait.return_value = 0
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    executable = root / "chrome-headless-shell"
                    executable.write_bytes(b"attested-browser")
                    executable.chmod(0o755)
                    profile = root / "profile"

                    def prelaunch(*_args: object, **_kwargs: object) -> object:
                        (profile / "DevToolsActivePort").write_text(
                            readiness,
                            encoding="utf-8",
                        )
                        return process

                    with (
                        mock.patch.dict(
                            os.environ,
                            {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                        ),
                        mock.patch(
                            "playwright.sync_api.sync_playwright", sync_playwright
                        ),
                        mock.patch(
                            "meshshot.browser_runtime._attest",
                            return_value={
                                "playwright": "1.60.0",
                                "browser": "chromium-headless-shell",
                                "revision": "1223",
                                "version": "Google Chrome for Testing 148.0.7778.96",
                                "sha256": "2" * 64,
                            },
                        ),
                        mock.patch("subprocess.Popen", side_effect=prelaunch),
                        mock.patch(
                            "meshshot.browser_runtime._PinnedExecutable.verify_running_image"
                        ),
                        mock.patch(
                            "tempfile.mkdtemp",
                            side_effect=lambda **_kwargs: (
                                profile.mkdir() or os.fspath(profile)
                            ),
                        ),
                        mock.patch("os.getpgid", return_value=process.pid),
                        mock.patch(
                            "os.killpg",
                            side_effect=lambda _pgid, signum: (
                                (_ for _ in ()).throw(ProcessLookupError())
                                if signum == 0
                                else None
                            ),
                        ),
                        self.assertRaises(MeshshotError) as raised,
                    ):
                        render_residual_preview(
                            _geometry(triangle),
                            _geometry(triangle),
                            variant="step",
                        )

                    self.assertEqual("browser_readiness", raised.exception.phase)
                    self.assertFalse(profile.exists())
                    playwright.chromium.connect_over_cdp.assert_not_called()

    def test_prelaunched_runtime_connect_failure_reaps_group_and_removes_profile(
        self,
    ) -> None:
        playwright = mock.MagicMock()
        playwright.chromium.connect_over_cdp.side_effect = RuntimeError(
            "sensitive endpoint failure"
        )
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_bytes(b"attested-browser")
            executable.chmod(0o755)
            profile = root / "profile"

            def prelaunch(*_args: object, **_kwargs: object) -> object:
                (profile / "DevToolsActivePort").write_text(
                    "49152\n/devtools/browser/good\n",
                    encoding="utf-8",
                )
                return process

            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
                mock.patch(
                    "meshshot.browser_runtime._attest",
                    return_value={
                        "playwright": "1.60.0",
                        "browser": "chromium-headless-shell",
                        "revision": "1223",
                        "version": "Google Chrome for Testing 148.0.7778.96",
                        "sha256": "2" * 64,
                    },
                ),
                mock.patch("subprocess.Popen", side_effect=prelaunch),
                mock.patch.object(
                    __import__("meshshot.browser_runtime", fromlist=["_PinnedExecutable"])
                    ._PinnedExecutable,
                    "verify_running_image",
                ),
                mock.patch(
                    "meshshot.browser_runtime._verify_listener_owner"
                ),
                mock.patch(
                    "tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch("os.getpgid", return_value=43210),
                mock.patch(
                    "os.killpg",
                    side_effect=lambda _pgid, signum: (
                        (_ for _ in ()).throw(ProcessLookupError())
                        if signum == 0
                        else None
                    ),
                ) as killpg,
                self.assertRaises(MeshshotError) as raised,
            ):
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

            self.assertEqual("browser_connect", raised.exception.phase)
            self.assertFalse(profile.exists())
            self.assertIn(
                mock.call(43210, __import__("signal").SIGTERM),
                killpg.mock_calls,
            )

    def test_prelaunched_runtime_escalates_term_to_kill(self) -> None:
        from meshshot.browser_runtime import PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired([], 5), 0]
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {"cleanup_term_ms": 0, "cleanup_kill_ms": 0}
        runtime._profile_dir = None
        runtime._process = process
        runtime._process_group = 43210

        with (
            mock.patch(
                "os.killpg",
                side_effect=lambda _pgid, signum: (
                    (_ for _ in ()).throw(ProcessLookupError())
                    if signum == 0
                    else None
                ),
            ) as killpg,
        ):
            runtime._cleanup()

        self.assertEqual(
            [
                mock.call(43210, __import__("signal").SIGTERM),
                mock.call(43210, __import__("signal").SIGKILL),
                mock.call(43210, 0),
            ],
            killpg.mock_calls,
        )

    def test_prelaunched_runtime_kills_descendant_after_leader_exits_on_term(self) -> None:
        from meshshot.browser_runtime import PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.wait.return_value = 0
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {"cleanup_term_ms": 0, "cleanup_kill_ms": 0}
        runtime._profile_dir = None
        runtime._process = process
        runtime._process_group = 43210
        group_checks = iter((False, True))

        with (
            mock.patch(
                "meshshot.browser_runtime._group_empty",
                side_effect=lambda _pgid: next(group_checks),
            ),
            mock.patch("os.killpg") as killpg,
        ):
            runtime._cleanup()

        self.assertEqual(
            [
                mock.call(43210, __import__("signal").SIGTERM),
                mock.call(43210, __import__("signal").SIGKILL),
            ],
            killpg.mock_calls,
        )

    def test_prelaunched_runtime_reports_cleanup_when_descendant_survives_kill(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.wait.return_value = 0
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {"cleanup_term_ms": 0, "cleanup_kill_ms": 0}
        runtime._profile_dir = None
        runtime._process = process
        runtime._process_group = 43210

        with (
            mock.patch("meshshot.browser_runtime._group_empty", return_value=False),
            mock.patch("os.killpg") as killpg,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            runtime._cleanup()

        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertIn(
            mock.call(43210, __import__("signal").SIGKILL),
            killpg.mock_calls,
        )

    def test_runtime_executes_pinned_image_during_swap_then_restore(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text(
                "#!/bin/sh\nprintf 'attested\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            pinned = _PinnedExecutable(executable)
            expected_sha256 = __import__("hashlib").sha256(
                executable.read_bytes()
            ).hexdigest()
            saved = root / "saved-browser"
            replacement = root / "replacement-browser"
            replacement.write_text(
                "#!/bin/sh\nprintf 'replacement\\n'\n",
                encoding="utf-8",
            )
            replacement.chmod(0o755)
            try:
                os.replace(executable, saved)
                os.replace(replacement, executable)
                process = pinned.popen(
                    [os.fspath(executable), "attested"],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
                os.replace(executable, replacement)
                os.replace(saved, executable)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(b"attested\n", stdout)
                self.assertEqual(b"", stderr)
                self.assertEqual(0, process.returncode)
                self.assertEqual(expected_sha256, pinned.sha256())
            finally:
                pinned.close()

    def test_runtime_snapshot_resists_in_place_source_overwrite(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_text(
                "#!/bin/sh\nprintf 'attested\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            expected_sha256 = __import__("hashlib").sha256(
                executable.read_bytes()
            ).hexdigest()
            pinned = _PinnedExecutable(executable)
            try:
                with executable.open("wb") as stream:
                    stream.write(b"#!/bin/sh\nprintf 'substituted\\n'\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                process = pinned.popen(
                    [os.fspath(executable)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(b"attested\n", stdout)
                self.assertEqual(b"", stderr)
                self.assertEqual(0, process.returncode)
                self.assertEqual(expected_sha256, pinned.sha256())
            finally:
                pinned.close()

    def test_running_process_image_rejects_swap_exec_restore(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            pinned = _PinnedExecutable(executable)
            replacement = root / "replacement-browser"
            marker = root / "replacement-executed"
            replacement.write_text(
                f"#!/bin/sh\nprintf 'substituted\\n' > {marker!s}\n"
                "while :; do sleep 1; done\n",
                encoding="utf-8",
            )
            replacement.chmod(0o755)
            assert pinned.launch_path is not None
            launch_root = pinned.launch_path.parent
            saved = root / "saved-snapshot"
            process: subprocess.Popen[bytes] | None = None
            try:
                pinned._thaw_directories(launch_root)
                os.replace(pinned.launch_path, saved)
                os.replace(replacement, pinned.launch_path)
                process = pinned.popen(
                    [os.fspath(executable)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    close_fds=True,
                )
                deadline = time.monotonic() + 5
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual("substituted\n", marker.read_text(encoding="utf-8"))
                os.replace(pinned.launch_path, replacement)
                os.replace(saved, pinned.launch_path)
                pinned._freeze_directories(launch_root)
                with self.assertRaises(BrowserRuntimeError) as raised:
                    pinned.verify_running_image(process.pid, timeout=5)
                self.assertEqual("browser_identity", raised.exception.operation)
            finally:
                if process is not None and process.poll() is None:
                    try:
                        os.killpg(process.pid, __import__("signal").SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.kill()
                    process.wait(timeout=5)
                pinned.close()

    def test_foreign_loopback_listener_is_not_browser_group_owned(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with self.assertRaises(BrowserRuntimeError) as raised:
                _verify_listener_owner(os.getpid() + 100000, port, timeout=5)
            self.assertEqual("browser_identity", raised.exception.operation)
        finally:
            listener.close()

    def test_macos_listener_rejects_foreign_colistener_and_wildcard(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        cases = (
            (
                b"p101\ng43210\nf3\nn127.0.0.1:49152\nTST=LISTEN\n"
                b"p202\ng99999\nf4\nn127.0.0.1:49152\nTST=LISTEN\n"
            ),
            b"p101\ng43210\nf3\nn*:49152\nTST=LISTEN\n",
        )
        for output in cases:
            with self.subTest(output=output):
                with (
                    mock.patch("meshshot.browser_runtime.sys.platform", "darwin"),
                    mock.patch(
                        "meshshot.browser_runtime.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=output, stderr=b""
                        ),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    _verify_listener_owner(43210, 49152, timeout=5)
                self.assertEqual("browser_identity", raised.exception.operation)

    def test_macos_listener_rejects_second_socket_record_under_same_process(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        output = (
            b"p101\ng43210\nf3\nn*:49152\nTST=LISTEN\n"
            b"f4\nn127.0.0.1:49152\nTST=LISTEN\n"
        )
        with (
            mock.patch("meshshot.browser_runtime.sys.platform", "darwin"),
            mock.patch(
                "meshshot.browser_runtime.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=output, stderr=b""
                ),
            ),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            _verify_listener_owner(43210, 49152, timeout=5)
        self.assertEqual("browser_identity", raised.exception.operation)

    def test_macos_listener_rejects_ambiguous_record_grammar(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        cases = (
            (
                "missing-file-field",
                b"p101\ng43210\nn127.0.0.1:49152\nTST=LISTEN\n",
            ),
            (
                "duplicate-name",
                b"p101\ng43210\nf3\nn*:49152\nn127.0.0.1:49152\nTST=LISTEN\n",
            ),
            (
                "duplicate-group",
                b"p101\ng99999\ng43210\nf3\nn127.0.0.1:49152\nTST=LISTEN\n",
            ),
            (
                "name-before-file",
                b"p101\ng43210\nn127.0.0.1:49152\nf3\nTST=LISTEN\n",
            ),
            (
                "file-before-group",
                b"p101\nf3\ng43210\nn127.0.0.1:49152\nTST=LISTEN\n",
            ),
            (
                "state-before-name",
                b"p101\ng43210\nf3\nTST=LISTEN\nn127.0.0.1:49152\n",
            ),
        )
        for label, output in cases:
            with self.subTest(label=label):
                with (
                    mock.patch("meshshot.browser_runtime.sys.platform", "darwin"),
                    mock.patch(
                        "meshshot.browser_runtime.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=output, stderr=b""
                        ),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    _verify_listener_owner(43210, 49152, timeout=5)
                self.assertEqual("browser_identity", raised.exception.operation)

    def test_macos_listener_query_exposes_wildcard_colistener(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        exact = (
            b"p101\ng43210\nf3\nPTCP\nn127.0.0.1:49152\n"
            b"TST=LISTEN\nTQR=0\nTQS=0\n"
        )
        mixed = exact + (
            b"p202\ng99999\nf4\nPTCP\nn*:49152\n"
            b"TST=LISTEN\nTQR=0\nTQS=0\n"
        )

        def lsof(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
            output = exact if "-iTCP@127.0.0.1:49152" in argv else mixed
            return subprocess.CompletedProcess(
                args=argv,
                returncode=0,
                stdout=output,
                stderr=b"",
            )

        with (
            mock.patch("meshshot.browser_runtime.sys.platform", "darwin"),
            mock.patch(
                "meshshot.browser_runtime.subprocess.run",
                side_effect=lsof,
            ),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            _verify_listener_owner(43210, 49152, timeout=5)
        self.assertEqual("browser_identity", raised.exception.operation)

    def test_macos_listener_rejects_noncanonical_auxiliary_grammar(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        prefix = b"p101\ng43210\nf3\n"
        name = b"n127.0.0.1:49152\n"
        cases = (
            ("invalid-fd", b"p101\ng43210\nfabc\nPTCP\n" + name + b"TST=LISTEN\nTQR=0\nTQS=0\n"),
            ("missing-protocol", prefix + name + b"TST=LISTEN\nTQR=0\nTQS=0\n"),
            ("duplicate-protocol", prefix + b"PTCP\nPTCP\n" + name + b"TST=LISTEN\nTQR=0\nTQS=0\n"),
            ("wrong-protocol", prefix + b"PUDP\n" + name + b"TST=LISTEN\nTQR=0\nTQS=0\n"),
            ("protocol-after-name", prefix + name + b"PTCP\nTST=LISTEN\nTQR=0\nTQS=0\n"),
            ("missing-queue", prefix + b"PTCP\n" + name + b"TST=LISTEN\n"),
            ("duplicate-receive-queue", prefix + b"PTCP\n" + name + b"TST=LISTEN\nTQR=0\nTQR=0\nTQS=0\n"),
            ("queue-order", prefix + b"PTCP\n" + name + b"TST=LISTEN\nTQS=0\nTQR=0\n"),
            ("empty-queue", prefix + b"PTCP\n" + name + b"TST=LISTEN\nTQR=\nTQS=0\n"),
            ("nonnumeric-queue", prefix + b"PTCP\n" + name + b"TST=LISTEN\nTQR=zero\nTQS=0\n"),
        )
        for label, output in cases:
            with self.subTest(label=label):
                with (
                    mock.patch("meshshot.browser_runtime.sys.platform", "darwin"),
                    mock.patch(
                        "meshshot.browser_runtime.subprocess.run",
                        return_value=subprocess.CompletedProcess(
                            args=[], returncode=0, stdout=output, stderr=b""
                        ),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    _verify_listener_owner(43210, 49152, timeout=5)
                self.assertEqual("browser_identity", raised.exception.operation)

    def test_linux_listener_rejects_non_loopback_addresses(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _verify_listener_owner

        header = "sl local_address rem_address st tx rx tr tm retr uid timeout inode\n"
        cases = (
            ("00000000:C000", "wildcard-ipv4"),
            ("0200007F:C000", "wrong-ipv4"),
            ("00000000000000000000000000000000:C000", "wildcard-ipv6"),
            ("00000000000000000000000001000000:C000", "loopback-ipv6"),
        )

        class ProcPath:
            name = "101"

            def __truediv__(self, value: str) -> object:
                if value == "stat":
                    return mock.Mock(read_text=lambda **_kwargs: "101 (browser) S 1 43210 1")
                if value == "fd":
                    return mock.Mock(iterdir=lambda: ["fd3"])
                raise AssertionError(value)

        for address, label in cases:
            tcp = header + f"0: {address} 00000000:0000 0A 0 0 0 0 0 12345\n"
            with self.subTest(label=label):
                with (
                    mock.patch("meshshot.browser_runtime.sys.platform", "linux"),
                    mock.patch(
                        "meshshot.browser_runtime.Path.read_text",
                        side_effect=[tcp, header],
                    ),
                    mock.patch(
                        "meshshot.browser_runtime.Path.iterdir",
                        return_value=[ProcPath()],
                    ),
                    mock.patch("os.readlink", return_value="socket:[12345]"),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    _verify_listener_owner(43210, 49152, timeout=5)
                self.assertEqual("browser_identity", raised.exception.operation)

    def test_cleanup_quarantine_failure_still_closes_all_authorities(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {"cleanup_term_ms": 0, "cleanup_kill_ms": 0}
        runtime._process = None
        runtime._process_group = None
        runtime._profile_dir = Path("/private/tmp/meshshot-profile-owned")
        runtime._profile_identity = (1, 2)
        runtime._profile_cleanup_forbidden = False
        runtime._profile_fd = 101
        runtime._profile_parent_fd = 102
        runtime._pinned_executable = mock.MagicMock()
        with (
            mock.patch("os.fstat", return_value=mock.Mock(st_dev=1, st_ino=2)),
            mock.patch(
                "meshshot.browser_runtime._private_child_directory",
                side_effect=BrowserRuntimeError("browser_identity"),
            ),
            mock.patch("os.close") as close,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            runtime._cleanup()
        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertEqual([mock.call(101), mock.call(102)], close.mock_calls)
        runtime._pinned_executable.close.assert_called_once_with()

    def test_private_snapshot_owns_one_root_and_collision_exhaustion_is_closed(
        self,
    ) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            executable = source / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            launches = root / "launches"
            launches.mkdir()
            with (
                mock.patch.object(browser_runtime.tempfile, "gettempdir", return_value=launches),
                mock.patch.object(browser_runtime.sys, "platform", "darwin"),
            ):
                pinned = _PinnedExecutable(executable)
                self.assertEqual([pinned.launch_root], list(launches.iterdir()))
                pinned.close()
            self.assertEqual([], list(launches.iterdir()))

            with (
                mock.patch.object(browser_runtime.tempfile, "gettempdir", return_value=launches),
                mock.patch.object(Path, "mkdir", side_effect=FileExistsError),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                browser_runtime._private_directory("meshshot-image-")
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "private_tree_materialization",
                raised.exception.browser_identity_phase,
            )
            self.assertEqual([], list(launches.iterdir()))

    def test_connected_browser_identity_failure_preserves_identity_classification(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {
            "startup_timeout_ms": 1,
            "browser_version": "148.0.7778.96",
        }
        chromium = mock.MagicMock()
        browser = _attested_connected_browser()
        browser.new_browser_cdp_session.return_value.send.return_value = {
            "product": "HeadlessChrome/148.0.7778.95"
        }
        chromium.connect_over_cdp.return_value = browser
        with (
            mock.patch.object(runtime, "_prelaunch", return_value="http://127.0.0.1:49152"),
            mock.patch.object(runtime, "_cleanup") as cleanup,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            with runtime.open(chromium):
                self.fail("identity failure must precede render")
        self.assertEqual("browser_identity", raised.exception.operation)
        cleanup.assert_called_once_with()

    def test_private_launch_tree_rejects_atomic_executable_replacement(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        if os.geteuid() == 0:
            self.skipTest("root bypasses directory write permission checks")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text(
                "#!/bin/sh\nprintf 'attested\\n'\n",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            pinned = _PinnedExecutable(executable)
            expected_sha256 = pinned.sha256()
            replacement = root / "replacement-browser"
            replacement.write_text(
                "#!/bin/sh\nprintf 'substituted\\n'\n",
                encoding="utf-8",
            )
            replacement.chmod(0o755)
            try:
                assert pinned.launch_path is not None
                replacement_denied = False
                try:
                    os.replace(replacement, pinned.launch_path)
                except PermissionError:
                    replacement_denied = True
                process = pinned.popen(
                    [os.fspath(executable)],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    close_fds=True,
                )
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(b"attested\n", stdout)
                self.assertEqual(b"", stderr)
                self.assertEqual(0, process.returncode)
                self.assertEqual(expected_sha256, pinned.sha256())
                self.assertTrue(replacement_denied)
            finally:
                pinned.close()

    def test_private_launch_tree_cleanup_failure_is_terminal(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            pinned = _PinnedExecutable(executable)
            launch_root = pinned.launch_root
            assert launch_root is not None
            try:
                with (
                    mock.patch(
                        "meshshot.browser_runtime.shutil.rmtree",
                        side_effect=OSError("sensitive cleanup"),
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    pinned.close()
                self.assertEqual("browser_cleanup", raised.exception.operation)
                self.assertNotIn("sensitive", str(raised.exception))
            finally:
                if launch_root.exists():
                    _PinnedExecutable._thaw_directories(launch_root)
                    __import__("shutil").rmtree(launch_root)

    def test_runtime_executes_private_read_only_snapshot_path(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            resources = Path(directory) / "resources"
            resources.mkdir()
            (resources / "runtime.dat").write_bytes(b"resource")
            pinned = _PinnedExecutable(executable)
            try:
                assert pinned.launch_path is not None
                with mock.patch(
                    "meshshot.browser_runtime.subprocess.Popen"
                ) as popen:
                    pinned.popen([os.fspath(executable)], close_fds=True)
                self.assertEqual(
                    os.fspath(pinned.launch_path),
                    popen.call_args.args[0][0],
                )
                self.assertEqual(
                    os.fspath(pinned.launch_path),
                    popen.call_args.kwargs["executable"],
                )
                self.assertNotIn("pass_fds", popen.call_args.kwargs)
                mode = pinned.launch_path.stat().st_mode
                self.assertEqual(0, mode & 0o222)
                assert pinned.launch_root is not None
                for path in pinned.launch_root.rglob("*"):
                    self.assertEqual(0, path.stat().st_mode & 0o222)
                self.assertEqual(
                    0,
                    pinned.launch_root.stat().st_mode & 0o222,
                )
                if os.geteuid() != 0:
                    with self.assertRaises(PermissionError):
                        pinned.launch_path.open("wb")
            finally:
                pinned.close()

    def test_linux_runtime_executes_sealed_fd_with_private_resource_argv0(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.subprocess.Popen") as popen,
            mock.patch(
                "meshshot.browser_runtime._wait_group_empty",
                return_value=True,
            ),
            mock.patch.object(
                pinned,
                "_wait_for_exec_replacement",
            ) as wait_for_exec,
        ):
            pinned.popen(
                ["source-browser", "--headless"],
                close_fds=True,
            )
        wait_for_exec.assert_called_once()
        self.assertEqual(
            os.fspath(Path(browser_runtime.__file__).with_name("fd_exec_handoff.py")),
            popen.call_args.args[0][2],
        )
        self.assertEqual(
            browser_runtime.sys.executable,
            popen.call_args.kwargs["executable"],
        )
        self.assertNotIn(
            "/proc/self/fd",
            " ".join(str(value) for value in popen.call_args.args[0]),
        )
        self.assertIn(73, popen.call_args.kwargs["pass_fds"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])

    def test_linux_version_executes_the_same_sealed_fd(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 91
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.subprocess.Popen") as popen,
            mock.patch(
                "meshshot.browser_runtime._wait_group_empty",
                return_value=True,
            ),
            mock.patch.object(
                pinned,
                "_wait_for_exec_replacement",
            ) as wait_for_exec,
        ):
            popen.return_value.communicate.return_value = (
                b"Google Chrome for Testing 148.0.7778.96\n",
                b"",
            )
            popen.return_value.returncode = 0
            pinned.run_version(timeout=5)
        wait_for_exec.assert_not_called()
        self.assertEqual(browser_runtime.sys.executable, popen.call_args.kwargs["executable"])
        self.assertIn(91, popen.call_args.kwargs["pass_fds"])
        self.assertTrue(popen.call_args.kwargs["close_fds"])
        self.assertNotIn(
            "/proc/self/fd",
            " ".join(str(value) for value in popen.call_args.args[0]),
        )

    def test_linux_version_timeout_reaps_or_fails_cleanup_closed(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 91
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        for cleanup_failed in (False, True):
            process = mock.MagicMock(spec=subprocess.Popen)
            process.communicate.side_effect = subprocess.TimeoutExpired(
                ["closed-version-probe"], 5
            )
            with (
                self.subTest(cleanup_failed=cleanup_failed),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(pinned, "popen", return_value=process),
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=cleanup_failed,
                ) as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.run_version(timeout=5)
            reap.assert_called_once_with(
                process,
                process_group=True,
                cleanup_term_timeout=browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS,
                cleanup_kill_timeout=browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS,
            )
            if cleanup_failed:
                self.assertEqual("browser_cleanup", raised.exception.operation)
            else:
                self.assertEqual(
                    "private_version_probe_timeout",
                    raised.exception.browser_identity_check,
                )

    def test_linux_handoff_passes_only_closed_browser_environment(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.dict(
                os.environ,
                {
                    "HOME": "/closed-home",
                    "PATH": "/usr/bin:/bin",
                    "OPENAI_API_KEY": "must-not-cross-browser-exec",
                },
                clear=True,
            ),
            mock.patch("meshshot.browser_runtime.subprocess.Popen") as popen,
            mock.patch.object(
                pinned,
                "_wait_for_exec_replacement",
            ) as wait_for_exec,
        ):
            pinned.popen(["ignored", "--headless"], close_fds=True)
        wait_for_exec.assert_called_once()
        self.assertEqual(
            {"HOME": "/closed-home", "PATH": "/usr/bin:/bin"},
            popen.call_args.kwargs["env"],
        )

    def test_linux_handoff_failure_matrix_reaps_every_started_helper(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        cases = {
            "helper-spawn": "private_version_helper_spawn_other",
            "select": "private_version_handoff_setup",
            "read": "private_version_handoff_setup",
            "fd-exec": "private_version_helper_exec",
            "timeout": "private_version_handoff_timeout",
        }
        for boundary, expected_check in cases.items():
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            with (
                self.subTest(boundary=boundary),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch(
                    "meshshot.browser_runtime.subprocess.Popen",
                    side_effect=(
                        OSError("closed helper spawn")
                        if boundary == "helper-spawn"
                        else None
                    ),
                    return_value=process,
                ) as popen,
                mock.patch(
                    "meshshot.browser_runtime.select.select",
                    side_effect=(
                        OSError("closed select")
                        if boundary == "select"
                        else None
                    ),
                    return_value=(
                        ([], [], [])
                        if boundary == "timeout"
                        else ([101], [], [])
                    ),
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.pipe",
                    return_value=(101, 102),
                ),
                mock.patch("meshshot.browser_runtime.os.set_inheritable"),
                mock.patch("meshshot.browser_runtime.os.close"),
                mock.patch(
                    "meshshot.browser_runtime.os.read",
                    side_effect=(
                        OSError("closed read") if boundary == "read" else None
                    ),
                    return_value=b"F" if boundary == "fd-exec" else b"",
                ),
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=False,
                ) as reap,
                self.assertRaises(browser_runtime.BrowserRuntimeError) as raised,
            ):
                pinned.popen(
                    ["ignored", "--headless"],
                    start_new_session=True,
                    close_fds=True,
                    _handoff_deadline=time.monotonic() + 1,
                    _handoff_completion="version",
                )
            self.assertEqual(expected_check, raised.exception.browser_identity_check)
            if boundary == "helper-spawn":
                reap.assert_not_called()
            else:
                reap.assert_called_once_with(
                    process,
                    process_group=True,
                    cleanup_term_timeout=browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS,
                    cleanup_kill_timeout=browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS,
                )
            if boundary == "helper-spawn":
                self.assertEqual(1, popen.call_count)

    def test_linux_helper_spawn_errno_selects_one_closed_cause(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        cases = (
            (errno.ENOENT, "private_version_helper_spawn_executable_missing"),
            (errno.ENOTDIR, "private_version_helper_spawn_executable_missing"),
            (errno.EACCES, "private_version_helper_spawn_permission"),
            (errno.EPERM, "private_version_helper_spawn_permission"),
            (errno.ETXTBSY, "private_version_helper_spawn_permission"),
            (errno.EAGAIN, "private_version_helper_spawn_process_limit"),
            (errno.EMFILE, "private_version_helper_spawn_file_limit"),
            (errno.ENFILE, "private_version_helper_spawn_file_limit"),
            (errno.ENOMEM, "private_version_helper_spawn_address_space"),
            (errno.EIO, "private_version_helper_spawn_other"),
        )
        for error_number, expected_check in cases:
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            with (
                self.subTest(error_number=error_number),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch(
                    "meshshot.browser_runtime.subprocess.Popen",
                    side_effect=OSError(error_number, "private detail"),
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.pipe",
                    return_value=(101, 102),
                ),
                mock.patch("meshshot.browser_runtime.os.set_inheritable"),
                mock.patch("meshshot.browser_runtime.os.close"),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.popen(
                    ["ignored", "--version"],
                    start_new_session=True,
                    close_fds=True,
                    _handoff_deadline=time.monotonic() + 1,
                    _handoff_completion="version",
                )
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                expected_check,
                raised.exception.browser_identity_check,
            )
            self.assertNotIn("private detail", str(raised.exception))

    def test_linux_helper_spawn_descriptor_cleanup_failure_dominates(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")

        def close(descriptor: int) -> None:
            if descriptor == 101:
                raise OSError("private descriptor cleanup detail")

        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch(
                "meshshot.browser_runtime.subprocess.Popen",
                side_effect=OSError(errno.EACCES, "private spawn detail"),
            ),
            mock.patch(
                "meshshot.browser_runtime.os.pipe",
                return_value=(101, 102),
            ),
            mock.patch("meshshot.browser_runtime.os.set_inheritable"),
            mock.patch("meshshot.browser_runtime.os.close", side_effect=close),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.popen(
                ["ignored", "--version"],
                start_new_session=True,
                close_fds=True,
                _handoff_deadline=time.monotonic() + 1,
                _handoff_completion="version",
            )
        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertIsNone(raised.exception.browser_identity_check)
        self.assertNotIn("private", str(raised.exception))

    def test_linux_parent_write_close_failure_preserves_setup_classification(
        self,
    ) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for remaining_close_failed in (False, True):
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            closed: list[int] = []

            def close(descriptor: int) -> None:
                closed.append(descriptor)
                if descriptor == 102 or (
                    descriptor == 101 and remaining_close_failed
                ):
                    raise OSError("closed handoff descriptor transition")

            with (
                self.subTest(remaining_close_failed=remaining_close_failed),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch(
                    "meshshot.browser_runtime.subprocess.Popen",
                    return_value=process,
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.pipe",
                    return_value=(101, 102),
                ),
                mock.patch("meshshot.browser_runtime.os.set_inheritable"),
                mock.patch("meshshot.browser_runtime.os.close", side_effect=close),
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=False,
                ) as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.popen(
                    ["ignored", "--version"],
                    start_new_session=True,
                    close_fds=True,
                    _handoff_deadline=time.monotonic() + 1,
                    _handoff_completion="version",
                )
            self.assertEqual([102, 101], closed)
            reap.assert_called_once_with(
                process,
                process_group=True,
                cleanup_term_timeout=(
                    browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS
                ),
                cleanup_kill_timeout=(
                    browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS
                ),
            )
            if remaining_close_failed:
                self.assertEqual("browser_cleanup", raised.exception.operation)
                self.assertIsNone(raised.exception.browser_identity_check)
            else:
                self.assertEqual("browser_identity", raised.exception.operation)
                self.assertEqual(
                    "private_version_handoff_setup",
                    raised.exception.browser_identity_check,
                )

    def test_linux_failed_group_handoff_gets_bounded_kill_and_reap(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = -9
        with (
            mock.patch("meshshot.browser_runtime.os.killpg") as killpg,
            mock.patch(
                "meshshot.browser_runtime._wait_group_empty",
                side_effect=(False, True),
            ),
        ):
            failed = _PinnedExecutable._reap_failed_handoff(
                process,
                process_group=True,
                cleanup_term_timeout=1.0,
                cleanup_kill_timeout=1.0,
            )
        self.assertFalse(failed)
        self.assertEqual(
            [
                mock.call(43210, signal.SIGTERM),
                mock.call(43210, signal.SIGKILL),
            ],
            killpg.mock_calls,
        )
        self.assertEqual(2, process.wait.call_count)

    def test_failed_handoff_kills_group_after_leader_already_exited(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = 127
        process.wait.return_value = 127
        with (
            mock.patch("meshshot.browser_runtime.os.killpg") as killpg,
            mock.patch(
                "meshshot.browser_runtime._wait_group_empty",
                side_effect=(False, True),
            ) as wait_group,
        ):
            failed = _PinnedExecutable._reap_failed_handoff(
                process,
                process_group=True,
                cleanup_term_timeout=1.0,
                cleanup_kill_timeout=1.0,
            )
        self.assertFalse(failed)
        self.assertEqual(
            [
                mock.call(43210, signal.SIGTERM),
                mock.call(43210, signal.SIGKILL),
            ],
            killpg.mock_calls,
        )
        self.assertEqual(2, wait_group.call_count)

    def test_failed_handoff_reports_cleanup_when_group_survives_kill(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = 127
        process.wait.return_value = 127
        with (
            mock.patch("meshshot.browser_runtime.os.killpg"),
            mock.patch(
                "meshshot.browser_runtime._wait_group_empty",
                return_value=False,
            ),
        ):
            failed = _PinnedExecutable._reap_failed_handoff(
                process,
                process_group=True,
                cleanup_term_timeout=1.0,
                cleanup_kill_timeout=1.0,
            )
        self.assertTrue(failed)

    def test_failed_handoff_group_proof_errors_still_kill_and_fail_cleanup(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        for boundary in ("term-proof", "kill-proof"):
            failed = False
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            process.wait.return_value = 127
            proof = (
                (OSError("closed TERM group proof"), True)
                if boundary == "term-proof"
                else (False, OSError("closed KILL group proof"))
            )
            with (
                self.subTest(boundary=boundary),
                mock.patch("meshshot.browser_runtime.os.killpg") as killpg,
                mock.patch(
                    "meshshot.browser_runtime._wait_group_empty",
                    side_effect=proof,
                ),
            ):
                failed = _PinnedExecutable._reap_failed_handoff(
                    process,
                    process_group=True,
                    cleanup_term_timeout=1.0,
                    cleanup_kill_timeout=1.0,
                )
            self.assertTrue(failed)
            self.assertIn(mock.call(43210, signal.SIGKILL), killpg.mock_calls)

    def test_linux_version_probe_owns_process_group_for_descendant_cleanup(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 91
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.communicate.side_effect = subprocess.TimeoutExpired(
            ["closed-version-probe"], 5
        )
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.object(pinned, "popen", return_value=process) as popen,
            mock.patch.object(
                pinned,
                "_reap_failed_handoff",
                return_value=False,
            ) as reap,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.run_version(timeout=5)
        self.assertEqual(
            "private_version_probe_timeout",
            raised.exception.browser_identity_check,
        )
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        reap.assert_called_once_with(
            process,
            process_group=True,
            cleanup_term_timeout=browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS,
            cleanup_kill_timeout=browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS,
        )

    def test_linux_handoff_descriptor_close_failure_preserves_boundary(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for failed_fd in (101, 102):
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            closed: set[int] = set()

            def close(descriptor: int) -> None:
                closed.add(descriptor)
                if descriptor == failed_fd:
                    raise OSError("closed descriptor cleanup")

            with (
                self.subTest(failed_fd=failed_fd),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch(
                    "meshshot.browser_runtime.subprocess.Popen",
                    return_value=process,
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.pipe",
                    return_value=(101, 102),
                ),
                mock.patch("meshshot.browser_runtime.os.set_inheritable"),
                mock.patch("meshshot.browser_runtime.os.close", side_effect=close),
                mock.patch(
                    "meshshot.browser_runtime.select.select",
                    return_value=([101], [], []),
                ),
                mock.patch("meshshot.browser_runtime.os.read", return_value=b""),
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=False,
                ) as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.popen(
                    ["ignored", "--headless"],
                    start_new_session=True,
                    close_fds=True,
                    _handoff_completion="version",
                )
            if failed_fd == 101:
                self.assertEqual("browser_cleanup", raised.exception.operation)
            else:
                self.assertEqual("browser_identity", raised.exception.operation)
                self.assertEqual(
                    "private_version_handoff_setup",
                    raised.exception.browser_identity_check,
                )
            self.assertEqual({101, 102}, closed)
            reap.assert_called_once()

    def test_fd_exec_helper_has_one_fixed_schema_and_failure_token(self) -> None:
        from meshshot import fd_exec_handoff

        cases = (
            ("unsupported", False, OSError("must not execute")),
            ("exec-rejected", True, OSError("closed exec")),
        )
        for label, supported, exec_failure in cases:
            supports_fd = mock.MagicMock()
            supports_fd.__contains__.return_value = supported
            with (
                self.subTest(label=label),
                mock.patch.object(
                    fd_exec_handoff.sys,
                    "argv",
                    [
                        "fd_exec_handoff.py",
                        "meshshot.fd-exec-handoff/1",
                        "73",
                        "74",
                        "/private/image/chrome-headless-shell",
                        "--version",
                    ],
                ),
                mock.patch.object(fd_exec_handoff.os, "supports_fd", supports_fd),
                mock.patch.object(
                    fd_exec_handoff.os,
                    "execve",
                    side_effect=exec_failure,
                ) as execve,
                mock.patch.object(fd_exec_handoff.os, "set_inheritable") as cloexec,
                mock.patch.object(fd_exec_handoff.os, "write") as write,
            ):
                self.assertEqual(127, fd_exec_handoff.main())
            write.assert_called_once_with(74, b"F")
            if label == "unsupported":
                execve.assert_not_called()
                cloexec.assert_not_called()
            else:
                cloexec.assert_called_once_with(74, False)
                execve.assert_called_once_with(
                    73,
                    ["/private/image/chrome-headless-shell", "--version"],
                    dict(os.environ),
                )

    def test_fd_exec_helper_rejects_schema_drift_without_exec(self) -> None:
        from meshshot import fd_exec_handoff

        with (
            mock.patch.object(
                fd_exec_handoff.sys,
                "argv",
                [
                    "fd_exec_handoff.py",
                    "meshshot.fd-exec-handoff/raw",
                    "73",
                    "74",
                    "/private/image/chrome-headless-shell",
                ],
            ),
            mock.patch.object(fd_exec_handoff.os, "execve") as execve,
            mock.patch.object(fd_exec_handoff.os, "write") as write,
        ):
            self.assertEqual(127, fd_exec_handoff.main())
        execve.assert_not_called()
        write.assert_called_once_with(74, b"F")

    def test_failed_helper_token_write_cannot_publish_version_success(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _attest

        pinned = mock.Mock()
        pinned.sha256.return_value = "a" * 64
        pinned.run_version.return_value = subprocess.CompletedProcess(
            args=["closed-helper"],
            returncode=127,
            stdout=b"",
            stderr=b"",
        )
        profile = {
            "playwright": "1.60.0",
            "browser": "chromium-headless-shell",
            "revision": "1223",
            "startup_timeout_ms": 15000,
            "browser_version": "148.0.7778.96",
        }
        with (
            mock.patch.object(
                browser_runtime.metadata, "version", return_value="1.60.0"
            ),
            mock.patch.object(
                browser_runtime,
                "_playwright_revision",
                return_value="1223",
            ),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            _attest(pinned, profile)
        self.assertEqual("browser_identity", raised.exception.operation)
        self.assertEqual(
            "private_launch_version_output_identity",
            raised.exception.browser_identity_phase,
        )

    def test_helper_death_eof_cannot_publish_production_success(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = 127
        process.wait.return_value = 127
        pinned = mock.MagicMock()
        pinned.popen.return_value = process
        pinned.verify_running_image.side_effect = BrowserRuntimeError(
            "browser_identity"
        )
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._executable = Path("/private/image/chrome-headless-shell")
        runtime._profile = {
            "arguments": [],
            "startup_timeout_ms": 1000,
            "cleanup_term_ms": 100,
            "cleanup_kill_ms": 100,
        }
        runtime._pinned_executable = pinned
        runtime._profile_dir = None
        runtime._profile_identity = None
        runtime._profile_cleanup_forbidden = False
        runtime._profile_fd = None
        runtime._profile_parent_fd = None
        runtime._process = None
        runtime._process_group = None
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            with (
                mock.patch(
                    "meshshot.browser_runtime.tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.killpg",
                    side_effect=ProcessLookupError,
                ) as killpg,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                runtime._prelaunch()
        self.assertEqual("browser_identity", raised.exception.operation)
        self.assertEqual(
            "live_running_image_identity",
            raised.exception.browser_identity_substage,
        )
        self.assertIn(mock.call(43210, signal.SIGTERM), killpg.mock_calls)
        pinned.close.assert_called_once_with()

    def test_linux_authenticated_handoff_survives_later_proc_denial(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            profile = root / "profile"
            pinned = _PinnedExecutable(executable)
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            process.poll.return_value = None
            process.wait.return_value = 0
            runtime = object.__new__(PrelaunchedCdpRuntime)
            runtime._executable = executable
            runtime._profile = {
                "arguments": [],
                "startup_timeout_ms": 1000,
                "cleanup_term_ms": 100,
                "cleanup_kill_ms": 100,
            }
            runtime._pinned_executable = pinned
            runtime._profile_dir = None
            runtime._profile_identity = None
            runtime._profile_cleanup_forbidden = False
            runtime._profile_fd = None
            runtime._profile_parent_fd = None
            runtime._process = None
            runtime._process_group = None

            def authenticated_launch(*_args: object, **_kwargs: object) -> object:
                (profile / "DevToolsActivePort").write_text(
                    "49152\n/devtools/browser/verified\n",
                    encoding="utf-8",
                )
                return process

            with (
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(pinned, "_linux_popen", side_effect=authenticated_launch),
                mock.patch.object(
                    pinned,
                    "verify_running_image",
                    side_effect=BrowserRuntimeError("browser_identity"),
                ) as redundant_proof,
                mock.patch(
                    "meshshot.browser_runtime.tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch(
                    "meshshot.browser_runtime._verify_listener_owner",
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.killpg",
                    side_effect=ProcessLookupError,
                ),
            ):
                endpoint = runtime._prelaunch()
                self.assertEqual("http://127.0.0.1:49152", endpoint)
                redundant_proof.assert_not_called()
                runtime._cleanup()
            self.assertFalse(profile.exists())

    def test_linux_handoff_rejects_actual_helper_death_eof_before_return(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nsleep 5\n", encoding="utf-8")
            executable.chmod(0o755)
            dead_helper = root / "dead-helper.py"
            dead_helper.write_text(
                "import os\nos._exit(127)\n",
                encoding="utf-8",
            )
            pinned = _PinnedExecutable(executable)
            started: list[subprocess.Popen[bytes]] = []
            real_popen = subprocess.Popen

            def record_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
                process = real_popen(*args, **kwargs)
                started.append(process)
                return process

            try:
                with (
                    mock.patch.object(browser_runtime.sys, "platform", "linux"),
                    mock.patch.object(
                        browser_runtime,
                        "_FD_EXEC_HANDOFF",
                        dead_helper,
                    ),
                    mock.patch(
                        "meshshot.browser_runtime.subprocess.Popen",
                        side_effect=record_popen,
                    ),
                    self.assertRaises(BrowserRuntimeError) as raised,
                ):
                    pinned.popen(
                        [os.fspath(executable), "--headless"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                        _handoff_deadline=time.monotonic() + 2,
                    )
                self.assertIn(
                    raised.exception.operation,
                    {"browser_identity", "browser_cleanup"},
                )
            finally:
                for process in started:
                    if process.poll() is None:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    process.wait(timeout=5)
                pinned.close()

    def test_linux_handoff_rejects_exact_proof_after_absolute_deadline(self) -> None:
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        with (
            mock.patch(
                "meshshot.browser_runtime.time.monotonic",
                return_value=10.0,
            ),
            mock.patch.object(pinned, "verify_running_image") as verify,
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            pinned._wait_for_exec_replacement(process, 10.0)
        verify.assert_not_called()

    def test_linux_running_image_digest_obeys_one_absolute_deadline(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        identity = mock.Mock(st_dev=1, st_ino=2)
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.os.fstat", return_value=identity),
            mock.patch("meshshot.browser_runtime.os.readlink", return_value="sealed"),
            mock.patch("meshshot.browser_runtime.os.open", return_value=74),
            mock.patch("meshshot.browser_runtime.os.dup", side_effect=(75, 76)),
            mock.patch("meshshot.browser_runtime.os.lseek"),
            mock.patch(
                "meshshot.browser_runtime.os.read",
                side_effect=(b"first chunk", b"", b"first chunk", b""),
            ),
            mock.patch("meshshot.browser_runtime.os.close"),
            mock.patch(
                "meshshot.browser_runtime.time.monotonic",
                side_effect=(1.0, 1.0, 1.0, 1.0, 1.0, 2.0),
            ),
            self.assertRaises(subprocess.TimeoutExpired),
        ):
            pinned._verify_running_image_until(43210, 2.0)

    def test_linux_fast_exact_version_exit_is_authenticated(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = 0
        process.returncode = 0
        process.communicate.return_value = (
            b"Google Chrome for Testing 148.0.7778.96\n",
            b"",
        )
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.subprocess.Popen", return_value=process),
            mock.patch("meshshot.browser_runtime.select.select", return_value=([101], [], [])),
            mock.patch("meshshot.browser_runtime.os.pipe", return_value=(101, 102)),
            mock.patch("meshshot.browser_runtime.os.set_inheritable"),
            mock.patch("meshshot.browser_runtime.os.close"),
            mock.patch("meshshot.browser_runtime.os.read", return_value=b""),
            mock.patch("meshshot.browser_runtime._wait_group_empty", return_value=True),
        ):
            completed = pinned.run_version(timeout=5)
        self.assertEqual(0, completed.returncode)

    def test_linux_fast_helper_death_cannot_authenticate_version(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = 127
        process.returncode = 127
        process.communicate.return_value = (b"", b"")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.subprocess.Popen", return_value=process),
            mock.patch("meshshot.browser_runtime.select.select", return_value=([101], [], [])),
            mock.patch("meshshot.browser_runtime.os.pipe", return_value=(101, 102)),
            mock.patch("meshshot.browser_runtime.os.set_inheritable"),
            mock.patch("meshshot.browser_runtime.os.close"),
            mock.patch("meshshot.browser_runtime.os.read", return_value=b""),
            mock.patch("meshshot.browser_runtime._wait_group_empty", return_value=True),
            mock.patch.object(pinned, "_reap_failed_handoff", return_value=False) as reap,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.run_version(timeout=5)
        self.assertEqual("browser_identity", raised.exception.operation)
        self.assertEqual(
            "private_version_exec_replacement",
            raised.exception.browser_identity_check,
        )
        reap.assert_not_called()

    def test_linux_version_completion_deadline_and_group_proof_fail_closed(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for boundary in ("expired", "group-proof"):
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            process.returncode = 0
            process.communicate.return_value = (
                b"Google Chrome for Testing 148.0.7778.96\n",
                b"",
            )
            monotonic = (1.0, 1.0, 6.0) if boundary == "expired" else 1.0
            with (
                self.subTest(boundary=boundary),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(pinned, "popen", return_value=process),
                mock.patch(
                    "meshshot.browser_runtime.time.monotonic",
                    side_effect=monotonic if isinstance(monotonic, tuple) else None,
                    return_value=monotonic if isinstance(monotonic, float) else None,
                ),
                mock.patch(
                    "meshshot.browser_runtime._wait_group_empty",
                    side_effect=(
                        OSError("closed group proof")
                        if boundary == "group-proof"
                        else None
                    ),
                ),
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=False,
                ) as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.run_version(timeout=5)
            reap.assert_called_once()
            if boundary == "group-proof":
                self.assertEqual(
                    "private_version_probe_completion",
                    raised.exception.browser_identity_check,
                )
            else:
                self.assertEqual(
                    "private_version_probe_timeout",
                    raised.exception.browser_identity_check,
                )

    def test_linux_version_communicate_failure_always_reaps_owned_group(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for failure in (
            OSError("closed communicate"),
            KeyboardInterrupt(),
            SystemExit(7),
        ):
            for cleanup_failed in (False, True):
                pinned = object.__new__(_PinnedExecutable)
                pinned.fd = 73
                pinned.launch_path = Path("/private/image/chrome-headless-shell")
                process = mock.MagicMock(spec=subprocess.Popen)
                process.communicate.side_effect = failure
                with (
                    self.subTest(
                        failure=type(failure).__name__,
                        cleanup_failed=cleanup_failed,
                    ),
                    mock.patch.object(browser_runtime.sys, "platform", "linux"),
                    mock.patch.object(pinned, "popen", return_value=process),
                    mock.patch.object(
                        pinned,
                        "_reap_failed_handoff",
                        return_value=cleanup_failed,
                    ) as reap,
                    self.assertRaises(
                        BrowserRuntimeError
                        if cleanup_failed or isinstance(failure, OSError)
                        else type(failure)
                    ) as raised,
                ):
                    pinned.run_version(timeout=5)
                reap.assert_called_once_with(
                    process,
                    process_group=True,
                    cleanup_term_timeout=(
                        browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS
                    ),
                    cleanup_kill_timeout=(
                        browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS
                    ),
                )
                if cleanup_failed:
                    self.assertEqual("browser_cleanup", raised.exception.operation)
                elif isinstance(failure, OSError):
                    self.assertEqual(
                        "private_version_probe_completion",
                        raised.exception.browser_identity_check,
                    )
                elif isinstance(failure, SystemExit):
                    self.assertEqual(7, raised.exception.code)

    def test_linux_version_post_spawn_control_flow_always_reaps_owned_group(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for boundary in ("validation", "group-proof"):
            for cleanup_failed in (False, True):
                pinned = object.__new__(_PinnedExecutable)
                pinned.fd = 73
                pinned.launch_path = Path("/private/image/chrome-headless-shell")
                process = mock.MagicMock(spec=subprocess.Popen)
                process.pid = 43210
                process.returncode = 0
                process.communicate.return_value = (
                    b"Google Chrome for Testing 148.0.7778.96\n",
                    b"",
                )

                def validation(_value: object) -> object:
                    raise KeyboardInterrupt()

                with ExitStack() as stack:
                    stack.enter_context(
                        self.subTest(
                            boundary=boundary,
                            cleanup_failed=cleanup_failed,
                        )
                    )
                    stack.enter_context(
                        mock.patch.object(browser_runtime.sys, "platform", "linux")
                    )
                    stack.enter_context(
                        mock.patch.object(pinned, "popen", return_value=process)
                    )
                    if boundary == "validation":
                        stack.enter_context(
                            mock.patch.object(
                                browser_runtime,
                                "_VERSION_OUTPUT",
                                mock.Mock(fullmatch=validation),
                            )
                        )
                        stack.enter_context(
                            mock.patch(
                                "meshshot.browser_runtime._wait_group_empty",
                                return_value=True,
                            )
                        )
                    else:
                        stack.enter_context(
                            mock.patch(
                                "meshshot.browser_runtime._wait_group_empty",
                                side_effect=SystemExit(9),
                            )
                        )
                    reap = stack.enter_context(
                        mock.patch.object(
                            pinned,
                            "_reap_failed_handoff",
                            return_value=cleanup_failed,
                        )
                    )
                    raised = stack.enter_context(
                        self.assertRaises(
                            BrowserRuntimeError
                            if cleanup_failed
                            else (
                                KeyboardInterrupt
                                if boundary == "validation"
                                else SystemExit
                            )
                        )
                    )
                    pinned.run_version(timeout=5)
                reap.assert_called_once()
                if cleanup_failed:
                    self.assertEqual("browser_cleanup", raised.exception.operation)
                elif boundary == "group-proof":
                    self.assertEqual(9, raised.exception.code)

    def test_linux_version_invalid_utf8_preserves_output_identity_phase(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.returncode = 0
        process.communicate.return_value = (b"\xff", b"")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.object(pinned, "popen", return_value=process),
            mock.patch("meshshot.browser_runtime._wait_group_empty", return_value=True),
            mock.patch.object(pinned, "_reap_failed_handoff", return_value=False),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.run_version(timeout=5)
        self.assertEqual("browser_identity", raised.exception.operation)
        self.assertEqual(
            "private_launch_version_output_identity",
            raised.exception.browser_identity_phase,
        )

    def test_linux_version_blocks_signals_until_spawn_reference_is_owned(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        blocked = False

        @contextmanager
        def interrupted_after_assignment():
            nonlocal blocked
            blocked = True
            try:
                yield
            finally:
                blocked = False
                raise KeyboardInterrupt()

        def spawn(*_args: object, **_kwargs: object) -> subprocess.Popen[bytes]:
            self.assertTrue(blocked)
            return process

        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.object(
                browser_runtime,
                "_blocked_runtime_signals",
                interrupted_after_assignment,
            ),
            mock.patch.object(pinned, "popen", side_effect=spawn),
            mock.patch.object(pinned, "_reap_failed_handoff", return_value=False) as reap,
            self.assertRaises(KeyboardInterrupt),
        ):
            pinned.run_version(timeout=5)
        reap.assert_called_once_with(
            process,
            process_group=True,
            cleanup_term_timeout=browser_runtime._FD_EXEC_CLEANUP_TERM_SECONDS,
            cleanup_kill_timeout=browser_runtime._FD_EXEC_CLEANUP_KILL_SECONDS,
        )

    def test_linux_version_does_not_reap_after_positive_empty_group_proof(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.returncode = 0
        process.communicate.return_value = (b"invalid output\n", b"")
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.object(pinned, "popen", return_value=process),
            mock.patch("meshshot.browser_runtime._wait_group_empty", return_value=True),
            mock.patch.object(pinned, "_reap_failed_handoff") as reap,
            mock.patch("meshshot.browser_runtime.os.killpg") as killpg,
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.run_version(timeout=5)
        self.assertEqual(
            "private_launch_version_output_identity",
            raised.exception.browser_identity_phase,
        )
        reap.assert_not_called()
        killpg.assert_not_called()

    def test_linux_authenticated_version_group_failure_is_completion(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for proof in (False, OSError("closed group proof")):
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            process.returncode = 0
            process.communicate.return_value = (
                b"Google Chrome for Testing 148.0.7778.96\n",
                b"",
            )
            proof_patch = (
                mock.patch(
                    "meshshot.browser_runtime._wait_group_empty",
                    side_effect=proof,
                )
                if isinstance(proof, BaseException)
                else mock.patch(
                    "meshshot.browser_runtime._wait_group_empty",
                    return_value=proof,
                )
            )
            with (
                self.subTest(proof=type(proof).__name__),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(pinned, "popen", return_value=process),
                proof_patch,
                mock.patch.object(
                    pinned,
                    "_reap_failed_handoff",
                    return_value=False,
                ) as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.run_version(timeout=5)
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "private_version_probe_completion",
                raised.exception.browser_identity_check,
            )
            reap.assert_called_once()

    def test_linux_exact_version_output_authenticates_completion_anomalies(
        self,
    ) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        for returncode, stderr in ((9, b""), (0, b"closed stderr")):
            pinned = object.__new__(_PinnedExecutable)
            pinned.fd = 73
            pinned.launch_path = Path("/private/image/chrome-headless-shell")
            process = mock.MagicMock(spec=subprocess.Popen)
            process.pid = 43210
            process.returncode = returncode
            process.communicate.return_value = (
                b"Google Chrome for Testing 148.0.7778.96\n",
                stderr,
            )
            setattr(process, "_meshshot_version_handoff_eof", True)
            with (
                self.subTest(returncode=returncode, stderr=stderr),
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(pinned, "popen", return_value=process),
                mock.patch(
                    "meshshot.browser_runtime._wait_group_empty",
                    return_value=True,
                ),
                mock.patch.object(pinned, "_reap_failed_handoff") as reap,
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                pinned.run_version(timeout=5)
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "private_version_probe_completion",
                raised.exception.browser_identity_check,
            )
            reap.assert_not_called()

    def test_linux_authenticated_version_cleanup_failure_dominates_completion(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        pinned.launch_path = Path("/private/image/chrome-headless-shell")
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.returncode = 0
        process.communicate.return_value = (
            b"Google Chrome for Testing 148.0.7778.96\n",
            b"",
        )
        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch.object(pinned, "popen", return_value=process),
            mock.patch("meshshot.browser_runtime._wait_group_empty", return_value=False),
            mock.patch.object(pinned, "_reap_failed_handoff", return_value=True),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.run_version(timeout=5)
        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertIsNone(raised.exception.browser_identity_check)

    def test_linux_running_image_descriptor_close_failure_is_cleanup(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        pinned = object.__new__(_PinnedExecutable)
        pinned.fd = 73
        identity = mock.Mock(st_dev=1, st_ino=2)
        closes: list[int] = []

        def close(descriptor: int) -> None:
            closes.append(descriptor)
            if descriptor == 74:
                raise OSError("closed descriptor cleanup")

        with (
            mock.patch.object(browser_runtime.sys, "platform", "linux"),
            mock.patch("meshshot.browser_runtime.os.fstat", return_value=identity),
            mock.patch("meshshot.browser_runtime.os.readlink", return_value="sealed"),
            mock.patch("meshshot.browser_runtime.os.open", return_value=74),
            mock.patch.object(
                _PinnedExecutable,
                "_sha256_fd_until",
                return_value="a" * 64,
            ),
            mock.patch("meshshot.browser_runtime.os.close", side_effect=close),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            pinned.verify_running_image(43210, timeout=5)
        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertEqual([74], closes)

    def test_macos_readiness_deadline_starts_after_live_image_verification(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        pinned = mock.MagicMock()
        pinned.popen.return_value = process
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._executable = Path("/private/image/chrome-headless-shell")
        runtime._profile = {
            "arguments": [],
            "startup_timeout_ms": 1000,
            "cleanup_term_ms": 100,
            "cleanup_kill_ms": 100,
        }
        runtime._pinned_executable = pinned
        runtime._profile_dir = None
        runtime._profile_identity = None
        runtime._profile_cleanup_forbidden = False
        runtime._profile_fd = None
        runtime._profile_parent_fd = None
        runtime._process = None
        runtime._process_group = None
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "profile"
            times = iter((100.0, 101.0, 102.0))
            with (
                mock.patch.object(browser_runtime.sys, "platform", "darwin"),
                mock.patch(
                    "meshshot.browser_runtime.tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch(
                    "meshshot.browser_runtime.time.monotonic",
                    side_effect=lambda: next(times),
                ),
                mock.patch(
                    "meshshot.browser_runtime.os.killpg",
                    side_effect=ProcessLookupError,
                ),
                self.assertRaises(BrowserRuntimeError),
            ):
                runtime._prelaunch()
        self.assertNotIn("_handoff_deadline", pinned.popen.call_args.kwargs)

    def test_linux_memfd_creation_and_sealing_fail_closed_and_cleanup(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_resources = root / "source-resources"
            source_resources.mkdir()
            executable = source_resources / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            launch_parent = root / "launches"
            launch_parent.mkdir()
            for boundary in ("unavailable", "create", "write", "fchmod", "seal"):
                fake_memfd_path = root / f"{boundary}.memfd"
                fake_memfd_path.write_bytes(b"")
                fake_memfd: int | None = None
                patches: list[mock._patch] = []
                if boundary == "unavailable":
                    patches.append(
                        mock.patch.object(
                            browser_runtime.os,
                            "memfd_create",
                            None,
                            create=True,
                        )
                    )
                elif boundary == "create":
                    patches.append(
                        mock.patch.object(
                            browser_runtime.os,
                            "memfd_create",
                            side_effect=OSError("closed create"),
                            create=True,
                        )
                    )
                else:
                    fake_memfd = os.open(fake_memfd_path, os.O_RDWR)
                    patches.append(
                        mock.patch.object(
                            browser_runtime.os,
                            "memfd_create",
                            return_value=fake_memfd,
                            create=True,
                        )
                    )
                    if boundary == "write":
                        real_write = os.write
                        patches.append(
                            mock.patch.object(
                                browser_runtime.os,
                                "write",
                                side_effect=lambda fd, data: (
                                    (_ for _ in ()).throw(OSError("closed write"))
                                    if fd == fake_memfd
                                    else real_write(fd, data)
                                ),
                            )
                        )
                    elif boundary == "fchmod":
                        real_fchmod = os.fchmod
                        patches.append(
                            mock.patch.object(
                                browser_runtime.os,
                                "fchmod",
                                side_effect=lambda fd, mode: (
                                    (_ for _ in ()).throw(OSError("closed fchmod"))
                                    if fd == fake_memfd
                                    else real_fchmod(fd, mode)
                                ),
                            )
                        )
                    else:
                        patches.append(
                            mock.patch(
                                "meshshot.browser_runtime.fcntl.fcntl",
                                side_effect=OSError("closed seal"),
                            )
                        )
                with (
                    self.subTest(boundary=boundary),
                    mock.patch.object(browser_runtime.sys, "platform", "linux"),
                    mock.patch(
                        "meshshot.browser_runtime._private_directory",
                        side_effect=lambda _prefix: Path(
                            tempfile.mkdtemp(dir=launch_parent)
                        ),
                    ),
                    ExitStack() as stack,
                ):
                    for patch in patches:
                        stack.enter_context(patch)
                    with self.assertRaises(BrowserRuntimeError) as raised:
                        _PinnedExecutable(executable)
                self.assertEqual("browser_identity", raised.exception.operation)
                self.assertEqual(
                    "private_launch_version_execution",
                    raised.exception.browser_identity_phase,
                )
                self.assertEqual(
                    "sealed_memfd_creation_policy",
                    raised.exception.browser_identity_check,
                )
                self.assertEqual([], list(launch_parent.iterdir()))
                if fake_memfd is not None:
                    with self.assertRaises(OSError):
                        os.fstat(fake_memfd)

    def test_linux_memfd_requests_explicit_executable_policy_without_retry(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            executable = source / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            launches = root / "launches"
            launches.mkdir()
            kernel = mock.Mock(side_effect=OSError("executable memfd rejected"))
            with (
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch.object(
                    browser_runtime.os,
                    "memfd_create",
                    kernel,
                    create=True,
                ),
                mock.patch(
                    "meshshot.browser_runtime._private_directory",
                    side_effect=lambda _prefix: Path(
                        tempfile.mkdtemp(dir=launches)
                    ),
                ),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable(executable)
            kernel.assert_called_once_with("meshshot-browser", 0x0013)
            self.assertEqual("browser_identity", raised.exception.operation)
            self.assertEqual(
                "private_launch_version_execution",
                raised.exception.browser_identity_phase,
            )
            self.assertEqual(
                "sealed_memfd_creation_policy",
                raised.exception.browser_identity_check,
            )
            self.assertEqual([], list(launches.iterdir()))

    def test_linux_sealed_snapshot_fd_source_close_failure_is_cleanup(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            launch = Path(directory) / "snapshot"
            launch.write_bytes(b"sealed executable")
            launch.chmod(0o555)
            source_fd = os.open(launch, os.O_RDONLY)
            descriptor_fd = os.open(Path(directory) / "memfd", os.O_RDWR | os.O_CREAT)
            real_close = os.close
            close_calls: list[int] = []

            def close_source_fails(fd: int) -> None:
                close_calls.append(fd)
                real_close(fd)
                if fd == source_fd:
                    raise OSError("closed source cleanup")

            with (
                mock.patch.object(browser_runtime.os, "memfd_create", return_value=descriptor_fd, create=True),
                mock.patch.object(browser_runtime.os, "open", return_value=source_fd),
                mock.patch.object(browser_runtime.os, "close", close_source_fails),
                mock.patch("meshshot.browser_runtime.fcntl.fcntl", return_value=0xF),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable._sealed_snapshot_fd(launch, launch.stat())
            self.assertEqual("browser_cleanup", raised.exception.operation)
            self.assertEqual([source_fd, descriptor_fd], close_calls)
            for fd in (source_fd, descriptor_fd):
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_linux_sealed_snapshot_fd_failure_closes_all_descriptors(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            launch = Path(directory) / "snapshot"
            launch.write_bytes(b"sealed executable")
            launch.chmod(0o555)
            source_fd = os.open(launch, os.O_RDONLY)
            descriptor_fd = os.open(Path(directory) / "memfd", os.O_RDWR | os.O_CREAT)
            real_close = os.close
            close_calls: list[int] = []

            def descriptor_close_fails(fd: int) -> None:
                close_calls.append(fd)
                real_close(fd)
                if fd == descriptor_fd:
                    raise OSError("closed descriptor cleanup")

            with (
                mock.patch.object(browser_runtime.os, "memfd_create", return_value=descriptor_fd, create=True),
                mock.patch.object(browser_runtime.os, "open", return_value=source_fd),
                mock.patch.object(browser_runtime.os, "close", descriptor_close_fails),
                mock.patch("meshshot.browser_runtime.fcntl.fcntl", side_effect=OSError("construction failed")),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable._sealed_snapshot_fd(launch, launch.stat())
            self.assertEqual("browser_cleanup", raised.exception.operation)
            self.assertEqual([source_fd, descriptor_fd], close_calls)
            for fd in (source_fd, descriptor_fd):
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_post_seal_identity_and_close_failure_still_cleans_private_tree(self) -> None:
        from meshshot import browser_runtime
        from meshshot.browser_runtime import BrowserRuntimeError, _PinnedExecutable

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            executable = source / "chrome-headless-shell"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            launch_parent = root / "launches"
            launch_parent.mkdir()
            snapshot = root / "snapshot-fd"
            snapshot.write_bytes(executable.read_bytes())
            snapshot.chmod(0o555)
            snapshot_fd = os.open(snapshot, os.O_RDONLY)
            real_close = os.close
            close_calls: list[int] = []
            snapshot_close_failed = False
            thaw_calls: list[Path] = []
            real_thaw = _PinnedExecutable._thaw_directories

            def close_snapshot_fails(fd: int) -> None:
                nonlocal snapshot_close_failed
                close_calls.append(fd)
                real_close(fd)
                if fd == snapshot_fd and not snapshot_close_failed:
                    snapshot_close_failed = True
                    raise OSError("closed snapshot cleanup")

            def record_thaw(_pinned: _PinnedExecutable, path: Path) -> None:
                thaw_calls.append(path)
                real_thaw(path)

            with (
                mock.patch.object(browser_runtime.sys, "platform", "linux"),
                mock.patch(
                    "meshshot.browser_runtime._private_directory",
                    side_effect=lambda _prefix: Path(
                        tempfile.mkdtemp(dir=launch_parent)
                    ),
                ),
                mock.patch.object(
                    _PinnedExecutable,
                    "_sealed_snapshot_fd",
                    return_value=snapshot_fd,
                ),
                mock.patch.object(
                    _PinnedExecutable,
                    "_sha256_fd",
                    return_value="0" * 64,
                ),
                mock.patch.object(browser_runtime.os, "close", close_snapshot_fails),
                mock.patch.object(_PinnedExecutable, "_thaw_directories", record_thaw),
                self.assertRaises(BrowserRuntimeError) as raised,
            ):
                _PinnedExecutable(executable)
            self.assertEqual("browser_cleanup", raised.exception.operation)
            self.assertTrue(snapshot_close_failed)
            self.assertEqual(1, len(thaw_calls))
            self.assertEqual([], list(launch_parent.iterdir()))
            with self.assertRaises(OSError):
                os.fstat(snapshot_fd)

    def test_prelaunched_runtime_cleanup_failure_is_terminal_and_closed(self) -> None:
        from meshshot.browser_runtime import BrowserRuntimeError, PrelaunchedCdpRuntime

        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        runtime = object.__new__(PrelaunchedCdpRuntime)
        runtime._profile = {"cleanup_term_ms": 1, "cleanup_kill_ms": 1}
        runtime._profile_dir = Path("/private/tmp/meshshot-test-owned-profile")
        runtime._process = process
        runtime._process_group = 43210

        with (
            mock.patch(
                "os.killpg",
                side_effect=lambda _pgid, signum: (
                    (_ for _ in ()).throw(ProcessLookupError())
                    if signum == 0
                    else None
                ),
            ),
            mock.patch("shutil.rmtree", side_effect=OSError("sensitive cleanup")),
            mock.patch("os.path.lexists", return_value=True),
            self.assertRaises(BrowserRuntimeError) as raised,
        ):
            runtime._cleanup()

        self.assertEqual("browser_cleanup", raised.exception.operation)
        self.assertNotIn("sensitive", str(raised.exception))

    def test_public_render_prelaunches_attested_browser_and_attaches_over_loopback_cdp(
        self,
    ) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = _attested_connected_browser()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        playwright.chromium.connect_over_cdp.return_value = browser
        playwright.chromium.launch.side_effect = AssertionError(
            "Playwright must not own the production browser process"
        )

        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": f"data:image/png;base64,{encoded}",
            "views": [
                {"name": name}
                for name in ("+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        process = mock.MagicMock(spec=subprocess.Popen)
        process.pid = 43210
        process.poll.return_value = None
        process.wait.return_value = 0
        version = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=b"Google Chrome for Testing 148.0.7778.96\n",
            stderr=b"",
        )
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "chrome-headless-shell"
            executable.write_bytes(b"attested-browser")
            executable.chmod(0o755)
            profile = root / "runtime-profile"

            def prelaunch(*_args: object, **_kwargs: object) -> object:
                (profile / "DevToolsActivePort").write_text(
                    "49152\n/devtools/browser/01234567-89ab-cdef-0123-456789abcdef\n",
                    encoding="utf-8",
                )
                return process

            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch(
                    "playwright.sync_api.sync_playwright", sync_playwright
                ),
                mock.patch("subprocess.Popen", side_effect=prelaunch),
                mock.patch(
                    "meshshot.browser_runtime._PinnedExecutable.verify_running_image"
                ),
                mock.patch("meshshot.browser_runtime._verify_listener_owner"),
                mock.patch(
                    "subprocess.run",
                    return_value=version,
                ),
                mock.patch(
                    "tempfile.mkdtemp",
                    side_effect=lambda **_kwargs: (
                        profile.mkdir() or os.fspath(profile)
                    ),
                ),
                mock.patch("os.getpgid", return_value=43210),
                mock.patch(
                    "os.killpg",
                    side_effect=lambda _pgid, signum: (
                        (_ for _ in ()).throw(ProcessLookupError())
                        if signum == 0
                        else None
                    ),
                ) as killpg,
            ):
                rendered = render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

        playwright.chromium.launch.assert_not_called()
        playwright.chromium.connect_over_cdp.assert_called_once_with(
            "http://127.0.0.1:49152",
            timeout=mock.ANY,
            is_local=True,
        )
        self.assertIsNotNone(rendered.browser_runtime)
        assert rendered.browser_runtime is not None
        self.assertEqual(
            "meshshot.prelaunched-cdp-runtime/1",
            rendered.browser_runtime["schema"],
        )
        self.assertEqual("passed", rendered.browser_runtime["result"])
        self.assertEqual(
            {"name", "sha256"},
            set(rendered.browser_runtime["adapter_profile"]),
        )
        self.assertEqual(
            {"playwright", "browser", "revision", "version", "sha256"},
            set(rendered.browser_runtime["browser_identity"]),
        )
        self.assertFalse(profile.exists())
        self.assertIn(mock.call(43210, __import__("signal").SIGTERM), killpg.mock_calls)

    def test_real_prelaunched_cdp_runtime_renders_with_exact_headless_shell(self) -> None:
        """Exercise the production prelaunch and CDP attach seam."""

        triangle = (
            (-0.2, -0.2, 0.0),
            (0.2, -0.2, 0.0),
            (0.0, 0.2, 0.0),
        )
        geometry = _geometry(triangle)

        rendered = render_residual_preview(
            geometry,
            geometry,
            variant="step",
        )

        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)
        self.assertIsNotNone(rendered.browser_runtime)
        assert rendered.browser_runtime is not None
        self.assertEqual("passed", rendered.browser_runtime["result"])

    def test_explicit_attested_browser_executable_bypasses_registry_selection(
        self,
    ) -> None:
        playwright = mock.MagicMock()
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "chrome-headless-shell"
            executable.write_bytes(b"browser")
            executable.chmod(0o755)
            with (
                mock.patch.dict(
                    os.environ,
                    {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(executable)},
                ),
                mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
                mock.patch(
                    "meshshot.browser_runtime._attest",
                    return_value={
                        "playwright": "1.60.0",
                        "browser": "chromium-headless-shell",
                        "revision": "1223",
                        "version": "Google Chrome for Testing 148.0.7778.96",
                        "sha256": "2" * 64,
                    },
                ),
                mock.patch("subprocess.Popen", side_effect=OSError("stop")) as popen,
                mock.patch(
                    "meshshot.renderer.default_executable",
                    side_effect=AssertionError("registry selection is forbidden"),
                ),
                self.assertRaises(MeshshotError),
            ):
                render_residual_preview(
                    _geometry(triangle),
                    _geometry(triangle),
                    variant="step",
                )

            launched = Path(popen.call_args.args[0][0])
            self.assertEqual(executable.name, launched.name)
            self.assertNotEqual(executable, launched)
            self.assertTrue(launched.parent.name.startswith("meshshot-image-"))
            self.assertFalse(launched.parent.exists())
            playwright.chromium.launch.assert_not_called()

    def test_explicit_browser_executable_rejects_unsafe_file_types(self) -> None:
        playwright = mock.MagicMock()
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            regular = root / "regular"
            regular.write_bytes(b"browser")
            regular.chmod(0o644)
            symlink = root / "symlink"
            symlink.symlink_to(regular)
            cases = (
                "relative-browser",
                os.fspath(root / "missing"),
                os.fspath(root),
                os.fspath(regular),
                os.fspath(symlink),
            )

            for value in cases:
                with (
                    self.subTest(value=value),
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_BROWSER_EXECUTABLE": value},
                    ),
                    mock.patch(
                        "playwright.sync_api.sync_playwright", sync_playwright
                    ),
                    self.assertRaises(MeshshotError) as raised,
                ):
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )

                self.assertEqual(
                    "browser_identity", raised.exception.phase
                )

        playwright.chromium.connect_over_cdp.assert_not_called()

    def test_browser_launch_failure_has_resource_specific_closed_phase(self) -> None:
        cases = {
            "pthread_create: Resource temporarily unavailable": (
                "browser_launch_process_limit"
            ),
            "Error: spawn /usr/bin/chromium EAGAIN": (
                "browser_launch_process_limit"
            ),
            "Too many open files": "browser_launch_file_limit",
            "Error: spawn /usr/bin/chromium ENFILE": "browser_launch_file_limit",
            "Cannot allocate memory": "browser_launch_address_space",
            "Error: spawn /usr/bin/chromium ENOMEM": (
                "browser_launch_address_space"
            ),
            "Creating shared memory in /dev/shm failed": (
                "browser_launch_shared_memory"
            ),
            "error while loading shared libraries": (
                "browser_launch_executable_dependency"
            ),
            "Error: spawn /missing/chromium ENOENT": (
                "browser_launch_executable_missing"
            ),
            "Error: spawn /denied/chromium EACCES": (
                "browser_launch_executable_spawn_permission"
            ),
            (
                "Error: spawn /denied/chromium --no-sandbox "
                "--user-data-dir=/tmp/pw EACCES"
            ): "browser_launch_executable_spawn_permission",
            "zygote sandbox initialization: Permission denied": (
                "browser_launch_sandbox_permission"
            ),
            "cannot create user data directory: Permission denied": (
                "browser_launch_filesystem_permission"
            ),
            "Failed to create user-data-dir: EROFS": (
                "browser_launch_filesystem_permission"
            ),
            "Profile directory is on a read-only file system": (
                "browser_launch_filesystem_permission"
            ),
            "browser startup: Permission denied": (
                "browser_launch_executable_permission"
            ),
            "posix_spawn: No such file or directory": (
                "browser_launch_executable_missing"
            ),
        }
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        for detail, expected in cases.items():
            with self.subTest(detail=detail):
                playwright = mock.MagicMock()
                sync_playwright = mock.MagicMock()
                sync_playwright.return_value.__enter__.return_value = playwright
                pinned = mock.MagicMock()
                pinned.popen.side_effect = OSError(detail)

                with (
                    mock.patch.dict(
                        os.environ,
                        {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
                    ),
                    mock.patch(
                        "playwright.sync_api.sync_playwright", sync_playwright
                    ),
                    mock.patch(
                        "meshshot.browser_runtime._attest",
                        return_value={
                            "playwright": "1.60.0",
                            "browser": "chromium-headless-shell",
                            "revision": "1223",
                            "version": "Google Chrome for Testing 148.0.7778.96",
                            "sha256": "2" * 64,
                        },
                    ),
                    mock.patch(
                        "meshshot.browser_runtime._PinnedExecutable",
                        return_value=pinned,
                    ),
                    self.assertRaises(MeshshotError) as raised,
                ):
                    render_residual_preview(
                        _geometry(triangle),
                        _geometry(triangle),
                        variant="step",
                    )

                self.assertEqual(expected, raised.exception.phase)

    def test_browser_launch_failure_has_closed_phase(self) -> None:
        playwright = mock.MagicMock()
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        pinned = mock.MagicMock()
        pinned.popen.side_effect = OSError("sensitive launch detail")
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            mock.patch(
                "meshshot.browser_runtime._attest",
                return_value={
                    "playwright": "1.60.0",
                    "browser": "chromium-headless-shell",
                    "revision": "1223",
                    "version": "Google Chrome for Testing 148.0.7778.96",
                    "sha256": "2" * 64,
                },
            ),
            mock.patch(
                "meshshot.browser_runtime._PinnedExecutable",
                return_value=pinned,
            ),
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("browser_launch", raised.exception.phase)

    def test_invalid_browser_image_size_has_browser_result_phase(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context

        png = BytesIO()
        Image.new("RGB", (1, 1), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": f"data:image/png;base64,{encoded}",
            "views": [{} for _ in range(8)],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        runtime_patch, _runtime = _runtime_patch(browser)
        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            runtime_patch,
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("browser_result", raised.exception.phase)

    def test_reordered_browser_view_evidence_has_browser_result_phase(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context
        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        page.evaluate.return_value = {
            "ok": True,
            "pngDataUrl": f"data:image/png;base64,{encoded}",
            "views": [
                {"name": name}
                for name in ("-Z", "+Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso")
            ],
        }
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))
        runtime_patch, _runtime = _runtime_patch(browser)

        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            runtime_patch,
            self.assertRaises(MeshshotError) as raised,
        ):
            render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual("browser_result", raised.exception.phase)

    def test_geometry_payload_crosses_same_origin_route_not_evaluate_arguments(self) -> None:
        page = mock.MagicMock()
        context = mock.MagicMock()
        browser = mock.MagicMock()
        playwright = mock.MagicMock()
        context.new_page.return_value = page
        browser.new_context.return_value = context

        png = BytesIO()
        Image.new("RGB", (504, 1008), (0, 0, 0)).save(png, format="PNG")
        encoded = base64.b64encode(png.getvalue()).decode("ascii")
        routed_payload: dict[str, object] = {}

        def evaluate_without_payload(script: str, *args: object) -> dict[str, object]:
            self.assertIn('fetch("/payload.json"', script)
            self.assertEqual((), args)
            route_handler = page.route.call_args.args[1]
            route = mock.MagicMock()
            route.request.method = "GET"
            route.request.url = "http://meshshot.local/payload.json"
            route_handler(route)
            fulfilled = route.fulfill.call_args.kwargs
            self.assertEqual("application/json", fulfilled["content_type"])
            routed_payload.update(json.loads(fulfilled["body"]))
            return {
                "ok": True,
                "pngDataUrl": f"data:image/png;base64,{encoded}",
                "views": [
                    {"name": name}
                    for name in (
                        "+Z",
                        "-Z",
                        "+Y",
                        "-Y",
                        "+X",
                        "-X",
                        "Iso",
                        "-Iso",
                    )
                ],
            }

        page.evaluate.side_effect = evaluate_without_payload
        sync_playwright = mock.MagicMock()
        sync_playwright.return_value.__enter__.return_value = playwright
        triangle = ((-0.2, -0.2, 0.0), (0.2, -0.2, 0.0), (0.0, 0.2, 0.0))

        runtime_patch, _runtime = _runtime_patch(browser)
        with (
            mock.patch.dict(
                os.environ,
                {"MESHSHOT_BROWSER_EXECUTABLE": os.fspath(Path(os.__file__))},
            ),
            mock.patch("playwright.sync_api.sync_playwright", sync_playwright),
            runtime_patch,
        ):
            rendered = render_residual_preview(
                _geometry(triangle),
                _geometry(triangle),
                variant="step",
            )

        self.assertEqual((504, 1008), Image.open(BytesIO(rendered.png_bytes)).size)
        self.assertEqual(
            [[-0.2, -0.2, 0.0], [0.2, -0.2, 0.0], [0.0, 0.2, 0.0]],
            routed_payload["reference"]["vertices"],
        )

    def test_step_render_exposes_eight_view_residual_channels_in_fixed_layout(self) -> None:
        shared = ((-0.12, -0.22, 0.0), (0.12, -0.22, 0.0), (0.0, 0.18, 0.0))
        reference_only = ((-0.46, -0.2, 0.0), (-0.2, -0.2, 0.0), (-0.33, 0.2, 0.0))
        candidate_only = ((0.2, -0.2, 0.0), (0.46, -0.2, 0.0), (0.33, 0.2, 0.0))

        rendered = render_residual_preview(
            _geometry(shared, reference_only),
            _geometry(shared, candidate_only),
            variant="step",
        )

        image = Image.open(BytesIO(rendered.png_bytes))
        self.assertEqual("RGB", image.mode)
        self.assertEqual((504, 1008), image.size)
        self.assertEqual(
            ["+Z", "-Z", "+Y", "-Y", "+X", "-X", "Iso", "-Iso"],
            [view["name"] for view in rendered.views],
        )
        first_tile = image.crop((0, 0, 252, 252))
        colors = first_tile.getcolors(maxcolors=252 * 252)
        self.assertIsNotNone(colors)
        present = {color for count, color in colors if count >= 8}
        self.assertTrue(any(red > 160 and green < 32 and blue < 32 for red, green, blue in present))
        self.assertTrue(any(green > 160 and red < 32 and blue < 32 for red, green, blue in present))
        self.assertTrue(any(red > 160 and green > 160 and blue < 32 for red, green, blue in present))

    def test_axial_depth_and_negative_flip_share_canonical_screen_framing(self) -> None:
        positive_z_left = (
            (-0.43, -0.18, 0.3),
            (-0.17, -0.18, 0.3),
            (-0.3, 0.18, 0.3),
        )
        negative_z_right = (
            (0.17, -0.18, -0.3),
            (0.43, -0.18, -0.3),
            (0.3, 0.18, -0.3),
        )
        geometry = _geometry(positive_z_left, negative_z_right)

        rendered = render_residual_preview(geometry, geometry, variant="step")
        image = Image.open(BytesIO(rendered.png_bytes))
        positive = image.crop((0, 0, 252, 252))
        negative = image.crop((252, 0, 504, 252))

        def brightness(tile: Image.Image, box: tuple[int, int, int, int]) -> float:
            cropped = tile.crop(box)
            values = [
                (red + green) / 2
                for y in range(cropped.height)
                for x in range(cropped.width)
                for red, green, blue in [cropped.getpixel((x, y))]
                if red > 32 and green > 32 and blue < 16
            ]
            self.assertGreater(len(values), 40)
            return statistics.mean(values)

        left = (36, 82, 102, 176)
        right = (150, 82, 216, 176)
        self.assertGreater(brightness(positive, left), brightness(positive, right) + 50)
        self.assertGreater(brightness(negative, right), brightness(negative, left) + 50)
        self.assertEqual(
            ["orthographic"] * 6 + ["perspective"] * 2,
            [view["framing"]["projection"] for view in rendered.views],
        )

    def test_same_environment_render_is_repeatable_and_final_size_is_frozen(self) -> None:
        triangle = ((-0.35, -0.3, 0.0), (0.35, -0.3, 0.0), (0.0, 0.35, 0.0))
        geometry = _geometry(triangle)

        first = render_residual_preview(geometry, geometry, variant="step")
        second = render_residual_preview(geometry, geometry, variant="step")
        final = render_residual_preview(geometry, geometry, variant="final")

        self.assertEqual(first.png_bytes, second.png_bytes)
        with Image.open(BytesIO(final.png_bytes)) as image:
            self.assertEqual("RGB", image.mode)
            self.assertEqual((1008, 2016), image.size)

    def test_all_negative_axial_flips_have_frozen_pixel_orientation(self) -> None:
        cases = (
            # view row, non-edge-on triangle, expected positive/negative side
            (
                0,
                ((0.22, -0.08, 0.0), (0.38, -0.08, 0.0), (0.30, 0.10, 0.0)),
                ("right", "right"),
            ),
            (
                1,
                ((0.22, 0.0, -0.08), (0.38, 0.0, -0.08), (0.30, 0.0, 0.10)),
                ("right", "left"),
            ),
            (
                2,
                ((0.0, -0.08, -0.38), (0.0, -0.08, -0.22), (0.0, 0.10, -0.30)),
                ("right", "right"),
            ),
        )

        for row, triangle, expected_sides in cases:
            with self.subTest(row=row):
                geometry = _geometry(triangle)
                rendered = render_residual_preview(geometry, geometry, variant="step")
                image = Image.open(BytesIO(rendered.png_bytes))
                for column, expected_side in enumerate(expected_sides):
                    tile = image.crop(
                        (column * 252, row * 252, (column + 1) * 252, (row + 1) * 252)
                    )
                    xs = [
                        pixel_x
                        for pixel_y in range(45, 207)
                        for pixel_x in range(30, 222)
                        for red, green, blue in [tile.getpixel((pixel_x, pixel_y))]
                        if red > 64 and green > 64 and blue < 16
                    ]
                    self.assertGreater(len(xs), 80)
                    mean_x = statistics.mean(xs)
                    if expected_side == "right":
                        self.assertGreater(mean_x, 145)
                    else:
                        self.assertLess(mean_x, 107)

    def test_candidate_never_autofits_or_changes_reference_owned_framing(self) -> None:
        reference = _geometry(
            ((-0.40, -0.18, 0.0), (-0.16, -0.18, 0.0), (-0.28, 0.20, 0.0))
        )
        candidate_in_frame = _geometry(
            ((0.16, -0.18, 0.0), (0.40, -0.18, 0.0), (0.28, 0.20, 0.0))
        )
        candidate_outside = _geometry(
            ((3.0, -0.18, 0.0), (3.24, -0.18, 0.0), (3.12, 0.20, 0.0))
        )

        inside = Image.open(
            BytesIO(
                render_residual_preview(
                    reference, candidate_in_frame, variant="step"
                ).png_bytes
            )
        )
        outside = Image.open(
            BytesIO(
                render_residual_preview(reference, candidate_outside, variant="step").png_bytes
            )
        )

        self.assertEqual(inside.getchannel("G").tobytes(), outside.getchannel("G").tobytes())
        inside_tile = inside.crop((25, 40, 227, 220))
        outside_tile = outside.crop((25, 40, 227, 220))
        inside_red = sum(
            1
            for pixel_y in range(inside_tile.height)
            for pixel_x in range(inside_tile.width)
            for red, green, blue in [inside_tile.getpixel((pixel_x, pixel_y))]
            if red > 64 and green < 32 and blue < 16
        )
        outside_red = sum(
            1
            for pixel_y in range(outside_tile.height)
            for pixel_x in range(outside_tile.width)
            for red, green, blue in [outside_tile.getpixel((pixel_x, pixel_y))]
            if red > 64 and green < 32 and blue < 16
        )
        self.assertGreater(inside_red, outside_red + 200)


if __name__ == "__main__":
    unittest.main()
