from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from scripts.pilot import cvm_sidecar_probe
from tests.python.support.paths import REPO_ROOT


WRAPPER = REPO_ROOT / "scripts" / "pilot" / "cvm-sidecar-probe.sh"
MODULE = REPO_ROOT / "scripts" / "pilot" / "cvm_sidecar_probe.py"
SOURCE_REVISION = "a" * 40
SIDECAR_ID = f"sha256:{'1' * 64}"
CLIENT_ID = f"sha256:{'2' * 64}"


def copy_cli(repo: Path) -> Path:
    pilot = repo / "scripts" / "pilot"
    pilot.mkdir(parents=True)
    shutil.copy2(WRAPPER, pilot / WRAPPER.name)
    shutil.copy2(MODULE, pilot / MODULE.name)
    (repo / "AGENTS.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "--", "AGENTS.md", "scripts"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=CVM Sidecar Test",
            "-c",
            "user.email=cvm-sidecar-test@example.invalid",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        cwd=repo,
        check=True,
    )
    return pilot / WRAPPER.name


def internal_remote_cli(repo: Path) -> Path:
    return repo / "scripts" / "pilot" / MODULE.name


def write_image_docker(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json
            import pathlib
            import sys

            sidecar = {SIDECAR_ID!r}
            client = {CLIENT_ID!r}
            if sys.argv[1:3] == ["image", "inspect"]:
                image = sys.argv[3]
                entrypoint = ["node", "/opt/browser-sidecar/prototype/server.mjs"] if image == sidecar else ["node", "/opt/browser-sidecar/prototype/client.mjs"]
                print(json.dumps([{{
                    "Id": image,
                    "Architecture": "amd64",
                    "Os": "linux",
                    "Config": {{"Entrypoint": entrypoint, "Cmd": None, "Env": ["NODE_ENV=production"], "Labels": {{"org.opencontainers.image.revision": {SOURCE_REVISION!r}}}}},
                }}]))
            elif sys.argv[1:3] == ["image", "save"]:
                output = pathlib.Path(sys.argv[4])
                assert sys.argv[5:] == [sidecar, client]
                output.write_bytes(b"exact fake docker archive\\n")
            elif sys.argv[1:3] == ["image", "load"]:
                assert pathlib.Path(sys.argv[4]).read_bytes() == b"exact fake docker archive\\n"
                print("Loaded exact images")
            else:
                raise SystemExit(f"unexpected docker argv: {{sys.argv[1:]}}")
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def cli_env(fake_bin: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    return env


class CvmSidecarProbeCliTests(unittest.TestCase):
    def test_public_wrapper_exposes_only_prepare_provision_and_probe(self) -> None:
        result = subprocess.run(
            [WRAPPER, "remote-probe", "cvmsp-" + "0" * 24],
            cwd=REPO_ROOT,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("prepare|provision|probe", result.stderr)

        ignores = (REPO_ROOT / ".cvmignore").read_text(encoding="utf-8")
        gitignores = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/.cvm-sidecar-probes/", ignores)
        self.assertIn("/.cvm-sidecar-probes/", gitignores)

    def test_prepare_builds_one_attested_archive_from_exact_image_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-prepare-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)

            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    f"""\
                    #!/usr/bin/env python3
                    import json
                    import pathlib
                    import sys

                    sidecar = {SIDECAR_ID!r}
                    client = {CLIENT_ID!r}
                    if sys.argv[1:3] == ["image", "inspect"]:
                        image = sys.argv[3]
                        entrypoint = ["node", "/opt/browser-sidecar/prototype/server.mjs"] if image == sidecar else ["node", "/opt/browser-sidecar/prototype/client.mjs"]
                        print(json.dumps([{{
                            "Id": image,
                            "Architecture": "amd64",
                            "Os": "linux",
                            "Config": {{"Entrypoint": entrypoint, "Cmd": None, "Env": ["NODE_ENV=production"], "Labels": {{"org.opencontainers.image.revision": {SOURCE_REVISION!r}}}}},
                        }}]))
                    elif sys.argv[1:3] == ["image", "save"]:
                        output = pathlib.Path(sys.argv[4])
                        assert sys.argv[5:] == [sidecar, client]
                        output.write_bytes(b"exact fake docker archive\\n")
                    else:
                        raise SystemExit(f"unexpected docker argv: {{sys.argv[1:]}}")
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

            result = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["schema"], "cvm-sidecar.prepare-receipt/1")
            self.assertEqual(receipt["status"], "prepared")
            self.assertEqual(receipt["sourceRevision"], SOURCE_REVISION)
            self.assertEqual(
                [(image["role"], image["id"], image["platform"]) for image in receipt["images"]],
                [
                    ("sidecar", SIDECAR_ID, "linux/amd64"),
                    ("client", CLIENT_ID, "linux/amd64"),
                ],
            )
            archive = repo / receipt["archive"]["relativePath"]
            self.assertEqual(archive.read_bytes(), b"exact fake docker archive\n")
            self.assertEqual(
                receipt["archive"]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(receipt["archive"]["bytes"], archive.stat().st_size)
            self.assertTrue(all(image["configSha256"] for image in receipt["images"]))

    def test_prepare_rejects_revision_not_bound_by_both_image_configs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-source-bind-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            result = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    "b" * 40,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=cli_env(fake_bin),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("does not match", result.stderr)
            self.assertFalse((repo / ".cvm-sidecar-probes").exists())

    def test_external_command_timeout_is_closed_and_classified(self) -> None:
        with mock.patch.object(
            cvm_sidecar_probe.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["docker", "image"], 1),
        ):
            with self.assertRaises(cvm_sidecar_probe.ProbeError) as error:
                cvm_sidecar_probe._run(
                    ["docker", "image"], cwd=REPO_ROOT, timeout=1
                )
        self.assertEqual(error.exception.check, "docker-timeout")

    def test_provision_uses_one_fixed_no_delete_transport_and_cannot_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-provision-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            transport_log = root / "transport.jsonl"
            ssh = fake_bin / "ssh"
            ssh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps({"tool": "ssh", "argv": sys.argv[1:]}) + "\\n")
                    command = sys.argv[-1]
                    handle = command.split()[-1]
                    if " remote-begin " in command:
                        print(json.dumps({"schema": "cvm-sidecar.remote-begin-receipt/1", "status": "ready-for-fixed-archive", "handle": handle}))
                    elif " remote-provision " in command:
                        print(json.dumps({"schema": "cvm-sidecar.provision-receipt/1", "status": "provisioned", "handle": handle, "sourceRevision": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "archive": {"remoteVerified": True}, "images": [], "retainedImageIds": []}))
                    else:
                        raise SystemExit("unexpected fixed ssh command")
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            rsync = fake_bin / "rsync"
            rsync.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps({"tool": "rsync", "argv": sys.argv[1:]}) + "\\n")
                    """
                ),
                encoding="utf-8",
            )
            rsync.chmod(0o755)
            env = cli_env(fake_bin)
            env["FAKE_TRANSPORT_LOG"] = os.fspath(transport_log)
            prepared = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            handle = json.loads(prepared.stdout)["handle"]

            first = subprocess.run(
                [wrapper, "provision", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            second = subprocess.run(
                [wrapper, "provision", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already claimed", second.stderr)
            calls = [json.loads(line) for line in transport_log.read_text().splitlines()]
            self.assertEqual([call["tool"] for call in calls], ["ssh", "rsync", "ssh"])
            rsync_argv = calls[1]["argv"]
            self.assertEqual(rsync_argv[:2], ["-a", "--"])
            self.assertNotIn("--delete", rsync_argv)
            self.assertTrue(rsync_argv[-1].endswith(f"/{handle}/incoming/"))
            self.assertTrue(all(call["argv"][:2] == ["-n", "cvm"] for call in (calls[0], calls[2])))

    def test_remote_provision_verifies_loaded_identity_and_removes_transfer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-remote-provision-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            env = cli_env(fake_bin)
            prepared = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            handle = json.loads(prepared.stdout)["handle"]
            state = repo / ".cvm-sidecar-probes" / handle
            incoming = state / "incoming"
            incoming.mkdir()
            shutil.move(state / "images.tar", incoming / "images.tar")
            shutil.move(state / "prepare.json", incoming / "prepare.json")

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-provision", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], "provisioned")
            self.assertEqual(receipt["retainedImageIds"], [SIDECAR_ID, CLIENT_ID])
            self.assertEqual(
                receipt["transferCleanup"],
                {
                    "archiveAbsent": True,
                    "prepareReceiptAbsent": True,
                    "incomingDirectoryAbsent": True,
                    "errors": [],
                },
            )
            self.assertFalse(incoming.exists())

    def test_remote_provision_cleanup_failure_dominates_hash_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-cleanup-dominates-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            env = cli_env(fake_bin)
            prepared = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            handle = json.loads(prepared.stdout)["handle"]
            state = repo / ".cvm-sidecar-probes" / handle
            incoming = state / "incoming"
            incoming.mkdir()
            shutil.move(state / "images.tar", incoming / "images.tar")
            shutil.move(state / "prepare.json", incoming / "prepare.json")
            (incoming / "unexpected").write_text("retain\n", encoding="utf-8")
            (incoming / "images.tar").write_bytes(b"corrupt")

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-provision", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("transfer cleanup failed", result.stderr)
            self.assertFalse((state / "provision.json").exists())
            self.assertTrue((incoming / "unexpected").is_file())

    def test_transfer_failure_invokes_one_fixed_abort_and_records_absence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-abort-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            transport_log = root / "transport.jsonl"
            ssh = fake_bin / "ssh"
            ssh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    command = sys.argv[-1]
                    handle = command.split()[-1]
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps({"tool": "ssh", "command": command}) + "\\n")
                    if " remote-begin " in command:
                        print(json.dumps({"schema": "cvm-sidecar.remote-begin-receipt/1", "status": "ready-for-fixed-archive", "handle": handle}))
                    elif " remote-abort " in command:
                        print(json.dumps({"schema": "cvm-sidecar.abort-receipt/1", "status": "aborted", "handle": handle, "transferAbsenceProved": True, "errors": [], "retryAllowed": False}))
                    else:
                        raise SystemExit("unexpected ssh operation")
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            rsync = fake_bin / "rsync"
            rsync.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps({"tool": "rsync", "argv": sys.argv[1:]}) + "\\n")
                    raise SystemExit(23)
                    """
                ),
                encoding="utf-8",
            )
            rsync.chmod(0o755)
            env = cli_env(fake_bin)
            env["FAKE_TRANSPORT_LOG"] = os.fspath(transport_log)
            prepared = subprocess.run(
                [
                    wrapper,
                    "prepare",
                    "--source-revision",
                    SOURCE_REVISION,
                    "--sidecar-image",
                    SIDECAR_ID,
                    "--client-image",
                    CLIENT_ID,
                ],
                cwd=repo,
                env=env,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )
            handle = json.loads(prepared.stdout)["handle"]

            result = subprocess.run(
                [wrapper, "provision", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            receipt = json.loads(
                (repo / ".cvm-sidecar-probes" / handle / "provision.json").read_text()
            )
            self.assertEqual(receipt["status"], "failed")
            self.assertFalse(receipt["retryAllowed"])
            self.assertTrue(receipt["abort"]["transferAbsenceProved"])
            calls = [json.loads(line) for line in transport_log.read_text().splitlines()]
            self.assertEqual([call["tool"] for call in calls], ["ssh", "rsync", "ssh"])
            self.assertIn(" remote-abort ", calls[-1]["command"])
            self.assertNotIn("--delete", calls[1]["argv"])

    def test_remote_begin_refuses_to_adopt_a_predictable_existing_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-no-adopt-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            handle = "cvmsp-" + "3" * 24
            (repo / ".cvm-sidecar-probes" / handle).mkdir(parents=True)

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-begin", handle],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("adoption is forbidden", result.stderr)

    def test_receipt_parser_rejects_duplicate_json_keys(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-duplicate-json-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            handle = "cvmsp-" + "4" * 24
            state = repo / ".cvm-sidecar-probes" / handle
            state.mkdir(parents=True)
            (state / "prepare.json").write_text(
                '{"schema":"cvm-sidecar.prepare-receipt/1",'
                f'"handle":"{handle}","handle":"{handle}"}}\n',
                encoding="utf-8",
            )

            result = subprocess.run(
                [wrapper, "provision", handle],
                cwd=repo,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((state / "provision-attempt.json").exists())

    def test_remote_probe_runs_sealed_request_with_bounds_and_proves_absence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-remote-probe-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.jsonl"
            docker_state = root / "docker-state.json"
            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    log = pathlib.Path(os.environ["FAKE_DOCKER_LOG"])
                    state_path = pathlib.Path(os.environ["FAKE_DOCKER_STATE"])
                    state = json.loads(state_path.read_text()) if state_path.exists() else {"containers": {}, "networks": {}}
                    stdin = sys.stdin.read()
                    with log.open("a") as stream:
                        stream.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin}) + "\\n")
                    argv = sys.argv[1:]
                    if argv[:2] == ["network", "create"]:
                        name = argv[-1]; state["networks"][name] = "net-id"; print("net-id")
                    elif argv[:2] == ["run", "-d"]:
                        name = argv[argv.index("--name") + 1]
                        job = [item.split("=", 1)[1] for item in argv if item.startswith("BROWSER_SIDECAR_JOB_ID=")][0]
                        state["containers"][name] = {"id": "sidecar-id", "job": job, "running": True, "exit": None}
                        print("sidecar-id")
                    elif argv[:3] == ["container", "create", "--name"]:
                        name = argv[3]
                        job = [item.split("=", 1)[1] for item in argv if item.startswith("BROWSER_SIDECAR_JOB_ID=")][0]
                        state["containers"][name] = {"id": "client-id", "job": job, "running": False, "exit": None}
                        print("client-id")
                    elif argv[:3] == ["container", "start", "--attach"]:
                        name = argv[-1]; job = state["containers"][name]["job"]
                        request = json.loads(stdin)
                        assert request == {"schema": "meshshot.browser-sidecar.render-request/2", "program": "probe", "payload": {}}
                        state["containers"][name]["exit"] = 0
                        print(json.dumps({"ok": True, "schema": request["schema"], "requestSha256": "fixed", "program": "probe", "jobId": job, "result": {"connected": True, "browserExecutablesVisible": [], "contextCount": 1, "pageCount": 1, "sourceAliasesVisible": [], "externalEgressBlocked": True}}))
                    elif argv[0] == "logs":
                        name = argv[-1]; item = state["containers"][name]; job = item["job"]
                        print(json.dumps({"event": "ready", "jobId": job, "endpointPath": "/fixed", "programs": {"viewer": "v", "residual": "r"}}))
                        if not item["running"]:
                            print(json.dumps({"event": "closing", "jobId": job, "reason": "SIGTERM"}))
                    elif argv[:2] == ["container", "inspect"] and "--format" in argv:
                        item = state["containers"][argv[2]]
                        print(json.dumps({"Running": item["running"], "ExitCode": item["exit"]}))
                    elif argv[:2] == ["container", "inspect"]:
                        print(json.dumps([{"HostConfig": {"ReadonlyRootfs": True, "Memory": 1610612736, "MemorySwap": 1610612736, "NanoCpus": 1500000000, "PidsLimit": 256, "ShmSize": 268435456}, "Mounts": []}]))
                    elif argv[0] == "stop":
                        item = state["containers"][argv[-1]]; item["running"] = False; item["exit"] = 0; print(argv[-1])
                    elif argv[0] == "rm":
                        state["containers"].pop(argv[-1], None); print(argv[-1])
                    elif argv[:2] == ["network", "rm"]:
                        state["networks"].pop(argv[-1], None); print(argv[-1])
                    elif argv[:3] == ["container", "ls", "-a"]:
                        print("\\n".join(item["id"] for item in state["containers"].values()))
                    elif argv[:2] == ["network", "ls"]:
                        print("\\n".join(state["networks"].values()))
                    else:
                        raise SystemExit(f"unexpected docker argv: {argv}")
                    state_path.write_text(json.dumps(state))
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = cli_env(fake_bin)
            env["FAKE_DOCKER_LOG"] = os.fspath(docker_log)
            env["FAKE_DOCKER_STATE"] = os.fspath(docker_state)
            identity = {
                "sourceRevision": SOURCE_REVISION,
                "images": [SIDECAR_ID, CLIENT_ID],
            }
            handle = "cvmsp-" + hashlib.sha256(
                json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()[:24]
            state = repo / ".cvm-sidecar-probes" / handle
            state.mkdir(parents=True)
            (state / "provision.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.provision-receipt/1",
                        "status": "provisioned",
                        "handle": handle,
                        "sourceRevision": SOURCE_REVISION,
                        "images": [
                            {"role": "sidecar", "id": SIDECAR_ID, "platform": "linux/amd64", "configSha256": "s"},
                            {"role": "client", "id": CLIENT_ID, "platform": "linux/amd64", "configSha256": "c"},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-probe", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], "succeeded")
            self.assertTrue(receipt["absenceProof"]["proved"])
            self.assertEqual(receipt["absenceProof"]["containers"], [])
            self.assertEqual(receipt["absenceProof"]["networks"], [])
            self.assertTrue(receipt["terminal"]["closingObserved"])
            self.assertEqual(receipt["terminal"]["state"]["ExitCode"], 0)
            self.assertEqual(
                [entry["state"] for entry in receipt["resourceLedger"]],
                ["removed", "removed", "removed"],
            )
            call_count = len(docker_log.read_text().splitlines())
            retry = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-probe", handle],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertNotEqual(retry.returncode, 0)
            self.assertEqual(len(docker_log.read_text().splitlines()), call_count)
            calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
            create_client = next(call for call in calls if call["argv"][:2] == ["container", "create"])
            self.assertEqual(create_client["argv"][-1], CLIENT_ID)
            self.assertIn("--pull=never", create_client["argv"])
            start_client = next(call for call in calls if call["argv"][:2] == ["container", "start"])
            self.assertEqual(
                json.loads(start_client["stdin"]),
                {"schema": "meshshot.browser-sidecar.render-request/2", "program": "probe", "payload": {}},
            )
            self.assertFalse(any("--delete" in call["argv"] for call in calls))


if __name__ == "__main__":
    unittest.main()
