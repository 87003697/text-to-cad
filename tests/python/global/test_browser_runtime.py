"""Unit tests for the browser_runtime package.

Covers BrowserRuntimeJob lifecycle, per-job isolation, MCP config rendering,
and defensive error surface. All docker interactions are mocked via a
callable so tests never touch a real Docker daemon.
"""

from __future__ import annotations

import json
import base64
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

    def _docker(argv):
        argv = list(argv)
        call_log.append(argv)
        if argv[:3] == ["docker", "network", "create"]:
            return _completed(stdout=argv[-1] + "\n")
        if argv[:2] == ["docker", "run"]:
            container = argv[argv.index("--name") + 1]
            for index, item in enumerate(argv):
                if item != "--publish":
                    continue
                container_port = int(argv[index + 1].rsplit(":", 1)[1].removesuffix("/tcp"))
                key = (container, container_port)
                port_by_container.setdefault(key, 32700 + len(port_by_container))
            return _completed(stdout=container + "\n")
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
        if argv[:2] == ["docker", "rm"] or argv[:3] == ["docker", "network", "rm"]:
            return _completed()
        raise AssertionError(f"unexpected docker call: {argv}")

    _docker.calls = call_log  # type: ignore[attr-defined]
    _docker.ports = port_by_container  # type: ignore[attr-defined]
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

    def test_image_lock_is_readable_and_lists_id_or_tag(self):
        self.assertTrue(IMAGE_LOCK_PATH.is_file())
        text = IMAGE_LOCK_PATH.read_text(encoding="utf-8")
        self.assertIn("text-to-cad-browser-runtime", text)


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


class JobFactoryTests(unittest.TestCase):

    def test_create_allocates_capability_under_exp_dir(self):
        with TemporaryDirectory() as tmp:
            exp = Path(tmp)
            job = BrowserRuntimeJob.create(exp, image_ref="sha256:deadbeef", docker=_fake_docker())
            self.assertTrue(
                job.capability_dir.as_posix().startswith((exp / "run/browser-runtime").as_posix())
            )
            self.assertEqual(job.image_ref, "sha256:deadbeef")

    def test_create_generates_unique_nonces(self):
        with TemporaryDirectory() as tmp:
            docker = _fake_docker()
            a = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=docker)
            b = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=docker)
            self.assertNotEqual(a.owner_nonce, b.owner_nonce)
            self.assertNotEqual(a.container_name, b.container_name)
            self.assertNotEqual(a.network_name, b.network_name)

    def test_create_uses_supplied_owner_nonce(self):
        with TemporaryDirectory() as tmp:
            job = BrowserRuntimeJob.create(
                Path(tmp),
                owner_nonce="abcdef0123456789abcd",
                image_ref="sha:x",
                docker=_fake_docker(),
            )
            self.assertEqual(job.prefix, "ttc-br-abcdef012345")

    def test_short_nonce_rejected(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                BrowserRuntimeJob(
                    owner_nonce="short",
                    capability_dir=Path(tmp),
                    image_ref="sha:x",
                )

    def test_locked_id_falls_back_to_locked_tag_after_transport(self):
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
            base = _fake_docker()

            def transported_docker(argv):
                argv = list(argv)
                if argv[:3] == ["docker", "image", "inspect"]:
                    if argv[-1] == "sha256:mac-only":
                        raise subprocess.CalledProcessError(1, argv)
                    self.assertEqual(
                        argv[-1], "text-to-cad-browser-runtime:build"
                    )
                    return _completed()
                return base(argv)

            job = BrowserRuntimeJob.create(
                root,
                image_lock_path=lock,
                docker=transported_docker,
            )
            job._wait_for_port = lambda: None  # type: ignore[method-assign]
            job.start()
            self.assertEqual(
                job.image_ref, "text-to-cad-browser-runtime:build"
            )
            run_call = next(
                call for call in base.calls if call[:2] == ["docker", "run"]
            )
            self.assertEqual(run_call[-1], "text-to-cad-browser-runtime:build")


class LifecycleTests(unittest.TestCase):

    def _make_job(self, tmp: str, **overrides):
        docker = overrides.pop("docker", _fake_docker())
        job = BrowserRuntimeJob.create(
            Path(tmp),
            image_ref=overrides.pop("image_ref", "sha256:deadbeef"),
            docker=docker,
        )
        # Skip the real socket poll — port-open check needs a live listener.
        job._wait_for_port = lambda: None  # type: ignore[method-assign]
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
            self.assertEqual(set(capability["programs"]), {"residual"})
            self.assertEqual(capability_path.stat().st_mode & 0o777, 0o444)

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
            job = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=failing_docker)
            with self.assertRaises(BrowserRuntimeError):
                job.start()
            self.assertTrue(failing_docker.rmed)  # type: ignore[attr-defined]

    def test_docker_missing_raises_browser_runtime_error(self):
        with TemporaryDirectory() as tmp:
            def missing(argv):
                raise FileNotFoundError("docker not found")
            job = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=missing)
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
            a = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=docker)
            b = BrowserRuntimeJob.create(Path(tmp), image_ref="sha:x", docker=docker)
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
