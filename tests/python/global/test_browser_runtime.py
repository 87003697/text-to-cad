"""Unit tests for the browser_runtime package.

Covers BrowserRuntimeJob lifecycle, per-job isolation, MCP config rendering,
and defensive error surface. All docker interactions are mocked via a
callable so tests never touch a real Docker daemon.
"""

from __future__ import annotations

import json
import base64
import hashlib
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import browser_runtime.job as job_module

from browser_runtime import (
    BROWSER_RUNTIME_CONTRACT,
    BrowserRuntimeError,
    BrowserRuntimeJob,
    IMAGE_LOCK_PATH,
    SANDBOX_CODEX_CONFIG_NAME,
    SANDBOX_CODEX_CONFIG_PATH,
    SANDBOX_MOUNT_ROOT,
    SANDBOX_RUNTIME_CAPABILITY_NAME,
    SANDBOX_RUNTIME_CAPABILITY_PATH,
    render_mcp_config,
)


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _fake_docker(port_by_container: dict[tuple[str, int], int] | None = None):
    """Return a docker CLI mock that emulates create/inspect/rm outcomes."""

    port_by_container = port_by_container or {}
    call_log: list[list[str]] = []
    containers: set[str] = set()
    networks: set[str] = set()

    def _docker(argv):
        argv = list(argv)
        call_log.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return _completed()
        if argv[:3] == ["docker", "network", "create"]:
            networks.add(argv[-1])
            return _completed(stdout=argv[-1] + "\n")
        if argv[:2] == ["docker", "run"]:
            container = argv[argv.index("--name") + 1]
            containers.add(container)
            for index, item in enumerate(argv):
                if item != "--publish":
                    continue
                container_port = int(argv[index + 1].rsplit(":", 1)[1].removesuffix("/tcp"))
                key = (container, container_port)
                port_by_container.setdefault(key, 32700 + len(port_by_container))
            return _completed(stdout=container + "\n")
        if argv[:3] == ["docker", "exec", "--detach"]:
            return _completed()
        if argv[:2] == ["docker", "inspect"] and "--format" in argv:
            container = argv[-1]
            fmt = argv[argv.index("--format") + 1]
            if "HostPort" in fmt:
                container_port = int(fmt.split('"')[1].removesuffix("/tcp"))
                port = port_by_container.get((container, container_port))
                if port is None:
                    raise subprocess.CalledProcessError(
                        1, argv, stderr="no such container"
                    )
                return _completed(stdout=f"{port}\n")
            if "State.Status" in fmt:
                return _completed(stdout="running\n")
            return _completed(stdout="")
        if argv[:2] == ["docker", "rm"]:
            containers.discard(argv[-1])
            return _completed()
        if argv[:3] == ["docker", "network", "rm"]:
            networks.discard(argv[-1])
            return _completed()
        if argv[:3] == ["docker", "container", "inspect"]:
            if argv[-1] not in containers:
                raise subprocess.CalledProcessError(
                    1, argv, stderr=f"Error: No such object: {argv[-1]}"
                )
            return _completed(stdout="{}\n")
        if argv[:3] == ["docker", "network", "inspect"]:
            if argv[-1] not in networks:
                raise subprocess.CalledProcessError(
                    1,
                    argv,
                    stderr=f"Error response from daemon: network {argv[-1]} not found",
                )
            return _completed(stdout="{}\n")
        raise AssertionError(f"unexpected docker call: {argv}")

    _docker.calls = call_log  # type: ignore[attr-defined]
    _docker.ports = port_by_container  # type: ignore[attr-defined]
    _docker.containers = containers  # type: ignore[attr-defined]
    _docker.networks = networks  # type: ignore[attr-defined]
    return _docker


class ContractShapeTests(unittest.TestCase):

    def test_contract_exposes_sandbox_paths(self):
        self.assertEqual(BROWSER_RUNTIME_CONTRACT["sandbox_mount_root"], SANDBOX_MOUNT_ROOT)
        self.assertEqual(
            BROWSER_RUNTIME_CONTRACT["sandbox_codex_config_name"],
            SANDBOX_CODEX_CONFIG_NAME,
        )
        self.assertEqual(
            BROWSER_RUNTIME_CONTRACT["sandbox_codex_config_path"],
            SANDBOX_CODEX_CONFIG_PATH,
        )
        self.assertTrue(SANDBOX_MOUNT_ROOT.startswith("/"))
        self.assertEqual(
            BROWSER_RUNTIME_CONTRACT["sandbox_runtime_capability_name"],
            SANDBOX_RUNTIME_CAPABILITY_NAME,
        )
        self.assertEqual(
            BROWSER_RUNTIME_CONTRACT["sandbox_runtime_capability_path"],
            SANDBOX_RUNTIME_CAPABILITY_PATH,
        )

    def test_image_lock_is_readable_and_lists_exact_id(self):
        self.assertTrue(IMAGE_LOCK_PATH.is_file())
        value = json.loads(IMAGE_LOCK_PATH.read_text(encoding="utf-8"))
        self.assertRegex(value["image"]["id"], r"^sha256:[0-9a-f]{64}$")


class RenderMcpConfigTests(unittest.TestCase):

    def test_config_contains_url_and_transport(self):
        toml = render_mcp_config("http://127.0.0.1:12345/mcp")
        self.assertIn("[mcp_servers.browser]", toml)
        self.assertIn('url = "http://127.0.0.1:12345/mcp"', toml)
        self.assertIn('transport = "http"', toml)
        self.assertIn("startup_timeout_ms", toml)

    def test_different_urls_produce_different_configs(self):
        a = render_mcp_config("http://127.0.0.1:11111/mcp")
        b = render_mcp_config("http://127.0.0.1:22222/mcp")
        self.assertNotEqual(a, b)


EXACT_IMAGE = "sha256:" + "d" * 64


class JobFactoryTests(unittest.TestCase):

    def test_create_allocates_capability_under_exp_dir(self):
        with TemporaryDirectory() as tmp:
            exp = Path(tmp)
            job = BrowserRuntimeJob.create(exp, image_ref=EXACT_IMAGE, docker=_fake_docker())
            self.assertTrue(
                job.capability_dir.as_posix().startswith((exp / "run/browser-runtime").as_posix())
            )
            self.assertEqual(job.image_ref, EXACT_IMAGE)

    def test_create_generates_unique_nonces(self):
        with TemporaryDirectory() as tmp:
            docker = _fake_docker()
            a = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=docker)
            b = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=docker)
            self.assertNotEqual(a.owner_nonce, b.owner_nonce)
            self.assertNotEqual(a.container_name, b.container_name)
            self.assertNotEqual(a.network_name, b.network_name)

    def test_create_uses_supplied_owner_nonce(self):
        with TemporaryDirectory() as tmp:
            job = BrowserRuntimeJob.create(
                Path(tmp),
                owner_nonce="abcdef0123456789abcd",
                image_ref=EXACT_IMAGE,
                docker=_fake_docker(),
            )
            self.assertEqual(job.prefix, "ttc-br-abcdef012345")

    def test_short_nonce_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                BrowserRuntimeJob(
                    owner_nonce="short",
                    capability_dir=Path(tmp),
                    image_ref=EXACT_IMAGE,
                )

    def test_viewer_mount_requires_materialized_runtime(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "viewer"
            runtime.mkdir()
            job = BrowserRuntimeJob(
                owner_nonce="abcdef0123456789abcd",
                capability_dir=root / "authority",
                image_ref=EXACT_IMAGE,
                viewer_runtime_dir=runtime,
                docker=_fake_docker(),
            )
            job.capability_dir.mkdir()
            job._stage_viewer_smoke_document()

            with self.assertRaisesRegex(
                BrowserRuntimeError, "Viewer runtime assets"
            ):
                job._build_run_argv()

    def test_viewer_mount_is_read_only_and_starts_inside_runtime(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "viewer"
            (runtime / "backend").mkdir(parents=True)
            (runtime / "dist").mkdir()
            (runtime / "backend/server.mjs").write_text("// server\n")
            (runtime / "dist/index.html").write_text("<!doctype html>\n")
            docker = _fake_docker()
            job = BrowserRuntimeJob(
                owner_nonce="abcdef0123456789abcd",
                capability_dir=root / "authority",
                image_ref=EXACT_IMAGE,
                viewer_runtime_dir=runtime,
                docker=docker,
            )
            job._wait_for_port = lambda: None  # type: ignore[method-assign]

            job.start()

            run_call = next(a for a in docker.calls if a[:2] == ["docker", "run"])
            mounts = [
                run_call[index + 1]
                for index, value in enumerate(run_call)
                if value == "--mount"
            ]
            self.assertEqual(len(mounts), 2)
            self.assertTrue(all(value.endswith(",readonly") for value in mounts))
            smoke_document = (
                job.capability_dir
                / "viewer-smoke-assets"
                / job_module.VIEWER_SMOKE_DOCUMENT
            )
            self.assertTrue(job_module.BrowserRuntimeJob._is_self_consistent_glb(
                smoke_document
            ))
            self.assertEqual(smoke_document.stat().st_mode & 0o777, 0o444)
            self.assertEqual(smoke_document.parent.stat().st_mode & 0o777, 0o555)
            expected_digest = "sha256:" + hashlib.sha256(
                smoke_document.read_bytes()
            ).hexdigest()
            self.assertEqual(job._viewer_document_sha256, expected_digest)
            job._stage_viewer_smoke_document()
            self.assertEqual(job._viewer_document_sha256, expected_digest)
            self.assertIn(
                "source=" + str(smoke_document.parent.resolve()),
                mounts[1],
            )
            exec_call = next(
                a for a in docker.calls if a[:3] == ["docker", "exec", "--detach"]
            )
            self.assertIn("/opt/text-to-cad/viewer/backend/server.mjs", exec_call)

    def test_viewer_mount_rejects_comma_delimited_source_path(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "experiment,invalid"
            runtime = root / "viewer"
            (runtime / "backend").mkdir(parents=True)
            (runtime / "dist").mkdir()
            (runtime / "backend/server.mjs").write_text("// server\n")
            (runtime / "dist/index.html").write_text("<!doctype html>\n")
            job = BrowserRuntimeJob(
                owner_nonce="abcdef0123456789abcd",
                capability_dir=root / "authority",
                image_ref=EXACT_IMAGE,
                viewer_runtime_dir=runtime,
                docker=_fake_docker(),
            )
            job.capability_dir.mkdir(parents=True)
            job._stage_viewer_smoke_document()

            with self.assertRaisesRegex(
                BrowserRuntimeError, "unsupported delimiter"
            ):
                job._build_run_argv()

    def test_viewer_staging_rejects_unsafe_retained_temporary(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "viewer"
            runtime.mkdir()
            job = BrowserRuntimeJob(
                owner_nonce="abcdef0123456789abcd",
                capability_dir=root / "authority",
                image_ref=EXACT_IMAGE,
                viewer_runtime_dir=runtime,
                docker=_fake_docker(),
            )
            model_dir = job.capability_dir / "viewer-smoke-assets"
            model_dir.mkdir(parents=True)
            temporary = model_dir / f".{job_module.VIEWER_SMOKE_DOCUMENT}.tmp"
            temporary.symlink_to(root / "outside")
            model_dir.chmod(0o555)

            with self.assertRaisesRegex(
                BrowserRuntimeError, "cannot stage CAD Viewer smoke document"
            ):
                job._stage_viewer_smoke_document()
            self.assertTrue(temporary.is_symlink())

    def test_missing_locked_id_fails_without_tag_fallback(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "image-lock.json"
            lock.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "image": {
                            "name": "text-to-cad-browser-runtime",
                            "tag": "build",
                            "id": "sha256:mac-only",
                        },
                    }
                ),
                encoding="utf-8",
            )
            calls = []

            def exact_docker(argv):
                argv = list(argv)
                calls.append(argv)
                if argv[:3] == ["docker", "image", "inspect"]:
                    raise subprocess.CalledProcessError(1, argv)
                return _completed()

            with self.assertRaisesRegex(BrowserRuntimeError, "exact image ID"):
                BrowserRuntimeJob.create(
                    root,
                    image_lock_path=lock,
                    docker=exact_docker,
                )
            self.assertEqual(calls, [])

    def test_explicit_tag_is_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(BrowserRuntimeError, "exact image ID"):
                BrowserRuntimeJob.create(
                    Path(tmp),
                    image_ref="text-to-cad-browser-runtime:latest",
                    docker=_fake_docker(),
                )


class LifecycleTests(unittest.TestCase):

    def _make_job(self, tmp: str, **overrides):
        docker = overrides.pop("docker", _fake_docker())
        job = BrowserRuntimeJob.create(
            Path(tmp),
            image_ref=overrides.pop("image_ref", EXACT_IMAGE),
            docker=docker,
        )
        # Skip the real socket poll — port-open check needs a live listener.
        job._wait_for_port = lambda: None  # type: ignore[method-assign]
        job.cleanup_absence_timeout_s = 0.01
        job.cleanup_absence_poll_s = 0.0
        def capture_ledger():
            value = {
                "schema": "text-to-cad.browser-runtime-render-ledger/1",
                "jobId": job.owner_nonce,
                "imageRef": job.image_ref,
                "programs": dict(job_module.CAD_RENDER_PROGRAMS),
                "requests": [],
            }
            return job._publish_json_receipt("render-ledger.json", value), 0
        job._capture_render_ledger = capture_ledger  # type: ignore[method-assign]
        return job, docker

    def test_start_creates_network_then_container(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()
            call_argvs = docker.calls
            create_idx = next(i for i, a in enumerate(call_argvs) if a[:3] == ["docker", "network", "create"])
            run_idx = next(i for i, a in enumerate(call_argvs) if a[:2] == ["docker", "run"])
            self.assertLess(create_idx, run_idx)

    def test_start_publishes_container_port(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()
            run_call = next(a for a in docker.calls if a[:2] == ["docker", "run"])
            self.assertIn("--publish", run_call)
            publish_spec = run_call[run_call.index("--publish") + 1]
            self.assertEqual(publish_spec, "127.0.0.1:0:9223/tcp")

    def test_start_populates_mcp_url(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            self.assertTrue(job.mcp_url.startswith("http://127.0.0.1:"))
            self.assertTrue(job.mcp_url.endswith("/mcp"))
            self.assertGreater(job.published_port, 0)

    def test_start_publishes_closed_runtime_capability(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            capability_path = job.capability_dir / SANDBOX_RUNTIME_CAPABILITY_NAME
            capability = json.loads(capability_path.read_text(encoding="ascii"))
            self.assertEqual(
                set(capability),
                {
                    "schema",
                    "jobId",
                    "imageRef",
                    "mcpUrl",
                    "cadRenderUrl",
                    "cadRenderToken",
                    "programs",
                },
            )
            self.assertEqual(
                capability["schema"],
                "text-to-cad.browser-runtime-capability/1",
            )
            self.assertEqual(capability["jobId"], job.owner_nonce)
            self.assertEqual(capability["imageRef"], job.image_ref)
            self.assertEqual(capability["mcpUrl"], job.mcp_url)
            self.assertEqual(capability["cadRenderUrl"], job.cad_render_url)
            self.assertEqual(set(capability["programs"]), {"residual", "snapshot"})
            self.assertEqual(capability_path.stat().st_mode & 0o777, 0o444)

    def test_start_publishes_image_authority_from_selected_lock(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock_path = root / "image-lock.json"
            lock = {
                "schema_version": 1,
                "image": {
                    "name": "text-to-cad-browser-runtime",
                    "id": EXACT_IMAGE,
                    "base_image": "example.invalid/runtime@sha256:fixed",
                    "base_id": "sha256:" + "b" * 64,
                    "playwright_mcp_version": "0.0.79",
                    "content_size_bytes": 1234,
                    "architecture": "amd64",
                },
                "built_from_ref": "1" * 40,
                "notes": "test lock",
                "host": {
                    "sourceImageId": "sha256:" + "c" * 64,
                    "retentionReference": "text-to-cad-browser-runtime-retained:"
                    + "d" * 64,
                    "archiveSha256": "a" * 64,
                },
            }
            encoded = json.dumps(
                lock, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode("ascii")
            lock_path.write_bytes(encoded)
            job = BrowserRuntimeJob.create(
                root,
                image_lock_path=lock_path,
                docker=_fake_docker(),
            )
            job._wait_for_port = lambda: None  # type: ignore[method-assign]
            job.start()

            authority_path = job.capability_dir / "image-authority.json"
            authority = json.loads(authority_path.read_text(encoding="ascii"))
            self.assertEqual(authority["imageRef"], EXACT_IMAGE)
            self.assertEqual(authority["sourceRevision"], "1" * 40)
            self.assertEqual(
                authority["lockSha256"],
                "sha256:" + hashlib.sha256(encoded).hexdigest(),
            )
            self.assertEqual(authority_path.stat().st_mode & 0o777, 0o444)

    def test_malformed_host_lock_cannot_publish_authority(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = json.loads(IMAGE_LOCK_PATH.read_text(encoding="utf-8"))
            source["image"]["id"] = EXACT_IMAGE
            source["host"] = {}
            lock_path = root / "image-lock.json"
            lock_path.write_text(json.dumps(source), encoding="utf-8")
            job = BrowserRuntimeJob.create(
                root,
                image_lock_path=lock_path,
                docker=_fake_docker(),
            )
            job._wait_for_port = lambda: None  # type: ignore[method-assign]

            with self.assertRaisesRegex(
                BrowserRuntimeError, "image authority is invalid"
            ):
                job.start()

    def test_mcp_preflight_publishes_tool_and_viewer_smoke_receipt(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            job._viewer_document_sha256 = "sha256:" + "a" * 64
            png = b"\x89PNG\r\n\x1a\nviewer-smoke"
            tool_names = [
                "browser_navigate",
                "browser_run_code_unsafe",
                "browser_snapshot",
                "browser_take_screenshot",
            ]
            responses = iter(
                [
                    ({"result": {"protocolVersion": "2025-03-26"}}, "session"),
                    ({}, "session"),
                    (
                        {"result": {"tools": [{"name": name} for name in tool_names]}},
                        "session",
                    ),
                    ({"result": {"content": [{"type": "text", "text": "ready"}]}}, "session"),
                    (
                        {
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": (
                                            "### Result\n"
                                            + json.dumps(
                                                {
                                                    "url": job_module.VIEWER_SMOKE_URL,
                                                    "screenshotEnabled": True,
                                                }
                                            )
                                        ),
                                    }
                                ]
                            }
                        },
                        "session",
                    ),
                    (
                        {
                            "result": {
                                "content": [
                                    {"type": "text", "text": "button Copy screenshot"}
                                ]
                            }
                        },
                        "session",
                    ),
                    (
                        {
                            "result": {
                                "content": [
                                    {
                                        "type": "image",
                                        "mimeType": "image/png",
                                        "data": base64.b64encode(png).decode("ascii"),
                                    }
                                ]
                            }
                        },
                        "session",
                    ),
                ]
            )
            job._mcp_post = mock.Mock(side_effect=lambda *args, **kwargs: next(responses))  # type: ignore[method-assign]
            job.preflight_mcp()

            receipt = json.loads(
                (job.capability_dir / "mcp-smoke.json").read_text(encoding="ascii")
            )
            self.assertTrue(receipt["passed"])
            self.assertEqual(receipt["imageRef"], EXACT_IMAGE)
            self.assertEqual(receipt["viewerUrl"], job_module.VIEWER_SMOKE_URL)
            self.assertIn("browser_navigate", receipt["toolNames"])
            self.assertIn("browser_snapshot", receipt["toolNames"])

    def test_start_publishes_mcp_and_cad_render_ports(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()
            run_call = next(a for a in docker.calls if a[:2] == ["docker", "run"])
            published = [
                run_call[index + 1]
                for index, item in enumerate(run_call)
                if item == "--publish"
            ]
            self.assertEqual(
                published,
                ["127.0.0.1:0:9223/tcp", "127.0.0.1:0:9224/tcp"],
            )
            self.assertTrue(job.cad_render_url.startswith("http://127.0.0.1:"))
            self.assertTrue(job.cad_render_url.endswith("/cad/render/residual"))

    def test_preflight_renders_before_publishing_receipt(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            png = b"\x89PNG\r\n\x1a\nfixed-preflight"
            response_value = {
                "schema": "text-to-cad.cad-render-response/1",
                "jobId": job.owner_nonce,
                "program": "residual",
                "programDigest": job_module.CAD_RENDER_PROGRAMS["residual"],
                "result": {
                    "ok": True,
                    "pngDataUrl": "data:image/png;base64,"
                    + base64.b64encode(png).decode("ascii"),
                    "views": [{"name": str(index)} for index in range(8)],
                },
            }

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(response_value).encode("ascii")

            with mock.patch.object(job_module._LOOPBACK_OPENER, "open", return_value=Response()):
                job.preflight()

            receipt = json.loads(
                (job.capability_dir / "preflight.json").read_text(encoding="ascii")
            )
            self.assertTrue(receipt["passed"])
            self.assertEqual(receipt["program"], "residual")
            self.assertEqual(receipt["programDigest"], job_module.CAD_RENDER_PROGRAMS["residual"])

    def test_preflight_uses_proxy_free_loopback_client(self):
        handlers = job_module._LOOPBACK_OPENER.handlers
        proxy_handlers = [
            handler for handler in handlers
            if isinstance(handler, job_module.urllib_request.ProxyHandler)
        ]
        # Supplying ProxyHandler({}) suppresses urllib's default environment-
        # aware ProxyHandler; build_opener intentionally omits the empty one.
        self.assertEqual(proxy_handlers, [])

    def test_preflight_retries_a_transient_loopback_disconnect(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            png = b"\x89PNG\r\n\x1a\nretry"
            response_value = {
                "schema": "text-to-cad.cad-render-response/1",
                "jobId": job.owner_nonce,
                "program": "residual",
                "programDigest": job_module.CAD_RENDER_PROGRAMS["residual"],
                "result": {
                    "ok": True,
                    "pngDataUrl": "data:image/png;base64,"
                    + base64.b64encode(png).decode("ascii"),
                    "views": [{} for _ in range(8)],
                },
            }

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(response_value).encode("ascii")

            with (
                mock.patch.object(
                    job_module._LOOPBACK_OPENER,
                    "open",
                    side_effect=[ConnectionResetError("warming up"), Response()],
                ) as open_request,
                mock.patch.object(job_module.time, "sleep"),
            ):
                job.preflight()

            self.assertEqual(open_request.call_count, 2)
            self.assertTrue((job.capability_dir / "preflight.json").is_file())

    def test_mcp_url_before_start_raises(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            with self.assertRaises(BrowserRuntimeError):
                _ = job.mcp_url

    def test_stop_removes_container_and_network(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()
            docker.calls.clear()
            job.stop()
            self.assertTrue(
                any(a[:2] == ["docker", "rm"] and a[-1] == job.container_name for a in docker.calls)
            )
            self.assertTrue(
                any(a[:3] == ["docker", "network", "rm"] and a[-1] == job.network_name for a in docker.calls)
            )

    def test_stop_publishes_ledger_bound_cleanup_absence_receipt(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            job.stop()
            ledger_path = job.capability_dir / "render-ledger.json"
            cleanup_path = job.capability_dir / "cleanup.json"
            ledger_bytes = ledger_path.read_bytes()
            cleanup = json.loads(cleanup_path.read_text(encoding="ascii"))
            self.assertEqual(ledger_path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(cleanup_path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(
                cleanup,
                {
                    "schema": "text-to-cad.browser-runtime-cleanup/1",
                    "jobId": job.owner_nonce,
                    "imageRef": EXACT_IMAGE,
                    "containerName": job.container_name,
                    "networkName": job.network_name,
                    "renderLedgerSha256": "sha256:"
                    + job_module.hashlib.sha256(ledger_bytes).hexdigest(),
                    "renderRequestCount": 0,
                    "containerAbsent": True,
                    "networkAbsent": True,
                    "passed": True,
                },
            )

    def test_stop_fails_closed_when_absence_cannot_be_proved(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()

            def retained_container(argv):
                argv = list(argv)
                if argv[:2] == ["docker", "rm"]:
                    return _completed()
                return docker(argv)

            job.docker = retained_container
            with self.assertRaisesRegex(BrowserRuntimeError, "container remains"):
                job.stop()
            cleanup = json.loads(
                (job.capability_dir / "cleanup.json").read_text(encoding="ascii")
            )
            self.assertFalse(cleanup["passed"])
            self.assertFalse(cleanup["containerAbsent"])

    def test_stop_retry_after_receipt_write_failure_preserves_ledger_binding(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            original_publish = job._publish_cleanup_receipt
            attempts = 0

            def fail_once(**kwargs):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise BrowserRuntimeError("injected cleanup receipt failure")
                return original_publish(**kwargs)

            job._publish_cleanup_receipt = fail_once  # type: ignore[method-assign]
            with self.assertRaisesRegex(BrowserRuntimeError, "injected"):
                job.stop()
            self.assertFalse(job._started)
            self.assertIsNotNone(job._captured_ledger_bytes)
            job.stop()
            cleanup = json.loads(
                (job.capability_dir / "cleanup.json").read_text(encoding="ascii")
            )
            self.assertTrue(cleanup["passed"])
            self.assertIsNotNone(cleanup["renderLedgerSha256"])
            self.assertEqual(cleanup["renderRequestCount"], 0)

    def test_stop_retries_transient_network_removal_until_exactly_absent(self):
        with TemporaryDirectory() as tmp:
            job, docker = self._make_job(tmp)
            job.start()
            network_remove_attempts = 0

            def lagged_network_cleanup(argv):
                nonlocal network_remove_attempts
                argv = list(argv)
                if argv[:3] == ["docker", "network", "rm"]:
                    network_remove_attempts += 1
                    if network_remove_attempts == 1:
                        return _completed()
                return docker(argv)

            job.docker = lagged_network_cleanup
            job.stop()
            self.assertGreaterEqual(network_remove_attempts, 2)
            cleanup = json.loads(
                (job.capability_dir / "cleanup.json").read_text(encoding="ascii")
            )
            self.assertTrue(cleanup["networkAbsent"])
            self.assertTrue(cleanup["passed"])

    def test_capture_render_ledger_exact_validates_and_publishes(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            value = {
                "schema": "text-to-cad.cad-render-request-ledger/1",
                "jobId": job.owner_nonce,
                "programs": dict(job_module.CAD_RENDER_PROGRAMS),
                "requests": [{
                    "sequence": 0,
                    "program": "snapshot",
                    "programDigest": job_module.CAD_RENDER_PROGRAMS["snapshot"],
                    "requestBytes": 123,
                    "requestSha256": "sha256:" + "1" * 64,
                    "responseStatus": 200,
                    "responseBytes": 456,
                    "responseSha256": "sha256:" + "2" * 64,
                    "outcome": "succeeded",
                }],
            }

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(value).encode("ascii")

            with mock.patch.object(job_module._LOOPBACK_OPENER, "open", return_value=Response()):
                encoded, count = BrowserRuntimeJob._capture_render_ledger(job)
            self.assertEqual(count, 1)
            self.assertEqual(
                encoded,
                (job.capability_dir / "render-ledger.json").read_bytes(),
            )
            durable = json.loads(encoded)
            self.assertEqual(durable["imageRef"], EXACT_IMAGE)
            self.assertEqual(durable["requests"], value["requests"])

    def test_capture_render_ledger_rejects_impossible_outcome(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            value = {
                "schema": "text-to-cad.cad-render-request-ledger/1",
                "jobId": job.owner_nonce,
                "programs": dict(job_module.CAD_RENDER_PROGRAMS),
                "requests": [{
                    "sequence": 0,
                    "program": "residual",
                    "programDigest": job_module.CAD_RENDER_PROGRAMS["residual"],
                    "requestBytes": 1,
                    "requestSha256": "sha256:" + "1" * 64,
                    "responseStatus": 500,
                    "responseBytes": 1,
                    "responseSha256": "sha256:" + "2" * 64,
                    "outcome": "succeeded",
                }],
            }

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(value).encode("ascii")

            with mock.patch.object(job_module._LOOPBACK_OPENER, "open", return_value=Response()):
                with self.assertRaisesRegex(BrowserRuntimeError, "ledger row"):
                    BrowserRuntimeJob._capture_render_ledger(job)

    def test_capture_render_ledger_rejects_boolean_and_out_of_budget_numbers(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            row = {
                "sequence": 0,
                "program": "residual",
                "programDigest": job_module.CAD_RENDER_PROGRAMS["residual"],
                "requestBytes": 1,
                "requestSha256": "sha256:" + "1" * 64,
                "responseStatus": 200,
                "responseBytes": 1,
                "responseSha256": "sha256:" + "2" * 64,
                "outcome": "succeeded",
            }
            variants = [
                {**row, "sequence": False},
                {**row, "requestBytes": True},
                {
                    **row,
                    "requestBytes": job_module._MAX_RENDER_REQUEST_BYTES + 1,
                },
                {**row, "responseBytes": True},
                {
                    **row,
                    "responseBytes": job_module._MAX_RENDER_RESPONSE_BYTES + 1,
                },
            ]

            class Response:
                def __init__(self, value):
                    self.value = value

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(self.value).encode("ascii")

            for invalid_row in variants:
                value = {
                    "schema": "text-to-cad.cad-render-request-ledger/1",
                    "jobId": job.owner_nonce,
                    "programs": dict(job_module.CAD_RENDER_PROGRAMS),
                    "requests": [invalid_row],
                }
                with (
                    self.subTest(row=invalid_row),
                    mock.patch.object(
                        job_module._LOOPBACK_OPENER,
                        "open",
                        return_value=Response(value),
                    ),
                    self.assertRaisesRegex(BrowserRuntimeError, "ledger row"),
                ):
                    BrowserRuntimeJob._capture_render_ledger(job)

    def test_capture_render_ledger_rejects_more_than_registered_budget(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            value = {
                "schema": "text-to-cad.cad-render-request-ledger/1",
                "jobId": job.owner_nonce,
                "programs": dict(job_module.CAD_RENDER_PROGRAMS),
                "requests": [{}] * (job_module._MAX_RENDER_LEDGER_ENTRIES + 1),
            }

            class Response:
                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

                def read(self, limit):
                    return json.dumps(value).encode("ascii")

            with mock.patch.object(job_module._LOOPBACK_OPENER, "open", return_value=Response()):
                with self.assertRaisesRegex(BrowserRuntimeError, "ledger identity"):
                    BrowserRuntimeJob._capture_render_ledger(job)

    def test_start_rolls_back_network_when_container_run_fails(self):
        with TemporaryDirectory() as tmp:
            def failing_docker(argv):
                argv = list(argv)
                if argv[:2] == ["docker", "run"]:
                    raise subprocess.CalledProcessError(125, argv, stderr="boom")
                if argv[:3] == ["docker", "network", "create"]:
                    return _completed(stdout=argv[-1])
                if argv[:3] == ["docker", "network", "rm"]:
                    failing_docker.rmed = True  # type: ignore[attr-defined]
                    return _completed()
                if argv[:2] == ["docker", "rm"]:
                    return _completed()
                return _completed()
            failing_docker.rmed = False  # type: ignore[attr-defined]
            job = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=failing_docker)
            with self.assertRaises(BrowserRuntimeError):
                job.start()
            self.assertTrue(failing_docker.rmed)  # type: ignore[attr-defined]

    def test_docker_missing_raises_browser_runtime_error(self):
        with TemporaryDirectory() as tmp:
            def missing(argv):
                raise FileNotFoundError("docker not found")
            job = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=missing)
            with self.assertRaises(BrowserRuntimeError):
                job.start()

    def test_poll_failed_before_start_is_false(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            self.assertFalse(job.poll_failed())

    def test_poll_failed_true_when_container_stopped(self):
        with TemporaryDirectory() as tmp:
            docker = _fake_docker()
            job, _ = self._make_job(tmp, docker=docker)
            job.start()
            # Swap the docker mock to report a non-running status.
            def dead_docker(argv):
                argv = list(argv)
                if argv[:2] == ["docker", "inspect"] and "State.Status" in argv[argv.index("--format") + 1]:
                    return _completed(stdout="exited\n")
                return docker(argv)
            job.docker = dead_docker
            self.assertTrue(job.poll_failed())

    def test_capability_dir_created_with_group_readable_mode(self):
        with TemporaryDirectory() as tmp:
            job, _ = self._make_job(tmp)
            job.start()
            mode = job.capability_dir.stat().st_mode & 0o777
            # 0o750 requested; umask can strip group/other bits but not owner.
            self.assertTrue(mode & 0o700 == 0o700)


class ConcurrentIsolationTests(unittest.TestCase):

    def test_two_jobs_use_distinct_networks_and_containers(self):
        with TemporaryDirectory() as tmp:
            docker = _fake_docker()
            a = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=docker)
            b = BrowserRuntimeJob.create(Path(tmp), image_ref=EXACT_IMAGE, docker=docker)
            a._wait_for_port = lambda: None  # type: ignore[method-assign]
            b._wait_for_port = lambda: None  # type: ignore[method-assign]
            a.start()
            b.start()
            self.assertNotEqual(a.network_name, b.network_name)
            self.assertNotEqual(a.container_name, b.container_name)
            self.assertNotEqual(a.published_port, b.published_port)
            self.assertNotEqual(a.capability_dir, b.capability_dir)


if __name__ == "__main__":
    unittest.main()
