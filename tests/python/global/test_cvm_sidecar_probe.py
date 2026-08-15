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
SIDECAR_CONFIG_BLOB = b'{"kind":"sidecar","schema":1}'
CLIENT_CONFIG_BLOB = b'{"kind":"client","schema":1}'
SIDECAR_ID = "sha256:" + hashlib.sha256(SIDECAR_CONFIG_BLOB).hexdigest()
CLIENT_ID = "sha256:" + hashlib.sha256(CLIENT_CONFIG_BLOB).hexdigest()
PORTABLE_SIDECAR_CONFIG = SIDECAR_CONFIG_BLOB
PORTABLE_CLIENT_CONFIG = CLIENT_CONFIG_BLOB
PORTABLE_SIDECAR_ID = SIDECAR_ID
PORTABLE_CLIENT_ID = CLIENT_ID


def remote_provision_images() -> list[dict[str, object]]:
    return [
        {
            "role": "sidecar",
            "id": SIDECAR_ID,
            "platform": "linux/amd64",
            "configSha256": SIDECAR_ID.removeprefix("sha256:"),
            "sourceRevision": SOURCE_REVISION,
        },
        {
            "role": "client",
            "id": CLIENT_ID,
            "platform": "linux/amd64",
            "configSha256": CLIENT_ID.removeprefix("sha256:"),
            "sourceRevision": SOURCE_REVISION,
        },
    ]


def write_remote_provision_attempt_fixture(
    root: Path,
    handle: str,
    owner: str,
    workflow: dict[str, str],
    *,
    images: list[dict[str, object]] | None = None,
) -> tuple[Path, Path, Path]:
    state_root = root / "state"
    state = state_root / handle
    incoming = state / "incoming"
    incoming.mkdir(parents=True)
    archive_bytes = b"x"
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    image_receipts = images if images is not None else remote_provision_images()
    (state / "provision-attempt.json").write_text(
        json.dumps(
            {
                "schema": "cvm-sidecar.remote-provision-attempt/1",
                "handle": handle,
                "ownerNonce": owner,
                "workflowFilesVerified": workflow,
                "freeBytes": 4 * 1024 * 1024 * 1024,
                "archive": {"bytes": 1, "sha256": archive_sha},
            }
        ),
        encoding="utf-8",
    )
    (incoming / "prepare.json").write_text(
        json.dumps(
            {
                "schema": "cvm-sidecar.prepare-receipt/1",
                "handle": handle,
                "sourceRevision": SOURCE_REVISION,
                "imageSourceRevision": SOURCE_REVISION,
                "workflowSourceRevision": "b" * 40,
                "workflowFiles": workflow,
                "archive": {"bytes": 1, "sha256": archive_sha},
                "images": image_receipts,
            }
        ),
        encoding="utf-8",
    )
    (incoming / "images.tar").write_bytes(archive_bytes)
    return state_root, state, incoming


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
    write_portable_archive_docker(path)


def write_portable_archive_docker(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import io
            import json
            import os
            import pathlib
            import sys
            import tarfile

            sidecar = {PORTABLE_SIDECAR_ID!r}
            client = {PORTABLE_CLIENT_ID!r}
            blobs = {{
                sidecar.removeprefix("sha256:") + ".json": {PORTABLE_SIDECAR_CONFIG!r},
                client.removeprefix("sha256:") + ".json": {PORTABLE_CLIENT_CONFIG!r},
            }}

            def add_bytes(archive, name, payload):
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(payload))

            if sys.argv[1:4] == ["image", "inspect", "--format"]:
                projection = sys.argv[4]
                image = sys.argv[5]
                canonical_image = (
                    image if image.startswith("sha256:") else "sha256:" + image
                )
                values = {{
                    "{{{{.Id}}}}": canonical_image,
                    "{{{{.Os}}}}": "linux",
                    "{{{{.Architecture}}}}": "amd64",
                    '{{{{index .Config.Labels "org.opencontainers.image.revision"}}}}': {SOURCE_REVISION!r},
                }}
                if projection not in values:
                    raise SystemExit(f"unexpected inspect projection: {{projection}}")
                print(values[projection])
            elif sys.argv[1:3] == ["image", "save"]:
                output = pathlib.Path(sys.argv[4])
                assert sys.argv[5:] == [sidecar, client]
                if os.environ.get("FAKE_MANIFEST_VARIANT") == "opaque":
                    output.write_bytes(b"opaque fixed docker-save output" + bytes([10]))
                    raise SystemExit(0)
                sidecar_path, client_path = list(blobs)
                variant = os.environ.get("FAKE_MANIFEST_VARIANT", "valid")
                configs = [sidecar_path, client_path]
                if variant == "mismatch":
                    configs[0] = "f" * 64 + ".json"
                elif variant == "duplicate":
                    configs[1] = sidecar_path
                elif variant == "missing":
                    configs = configs[:1]
                elif variant == "traversal":
                    configs[0] = "../" + sidecar_path
                manifest = [
                    {{"Config": config, "RepoTags": None, "Layers": []}}
                    for config in configs
                ]
                with tarfile.open(output, "w") as archive:
                    add_bytes(archive, "manifest.json", json.dumps(manifest).encode())
                    for name, payload in blobs.items():
                        add_bytes(archive, name, payload)
            elif sys.argv[1:3] == ["image", "load"]:
                with tarfile.open(pathlib.Path(sys.argv[4]), "r") as archive:
                    assert archive.getmember("manifest.json").isfile()
                print("Loaded exact images")
            elif sys.argv[1:3] == ["version", "--format"]:
                print(json.dumps({{"Os": "linux", "Arch": "amd64"}}))
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


def write_verified_local_provision(repo: Path, handle: str) -> dict[str, object]:
    workflow_files = {
        "module": hashlib.sha256(internal_remote_cli(repo).read_bytes()).hexdigest(),
        "wrapper": hashlib.sha256(
            (repo / "scripts" / "pilot" / WRAPPER.name).read_bytes()
        ).hexdigest(),
    }
    images = [
        {
            "role": "sidecar",
            "id": SIDECAR_ID,
            "platform": "linux/amd64",
            "configSha256": SIDECAR_ID.removeprefix("sha256:"),
            "sourceRevision": SOURCE_REVISION,
        },
        {
            "role": "client",
            "id": CLIENT_ID,
            "platform": "linux/amd64",
            "configSha256": CLIENT_ID.removeprefix("sha256:"),
            "sourceRevision": SOURCE_REVISION,
        },
    ]
    prepare = {
        "schema": "cvm-sidecar.prepare-receipt/1",
        "status": "prepared",
        "handle": handle,
        "sourceRevision": SOURCE_REVISION,
        "imageSourceRevision": SOURCE_REVISION,
        "workflowSourceRevision": "b" * 40,
        "workflowFiles": workflow_files,
        "images": images,
        "archive": {
            "relativePath": f".cvm-sidecar-probes/{handle}/images.tar",
            "sha256": "5" * 64,
            "bytes": 123,
        },
    }
    owner = "a" * 32
    provision = {
        "schema": "cvm-sidecar.provision-receipt/1",
        "status": "provisioned",
        "handle": handle,
        "ownerNonce": owner,
        "sourceRevision": SOURCE_REVISION,
        "imageSourceRevision": SOURCE_REVISION,
        "workflowSourceRevision": "b" * 40,
        "workflowFilesVerified": workflow_files,
        "freeBytesAtLoad": 4 * 1024 * 1024 * 1024,
        "archive": {"sha256": "5" * 64, "bytes": 123, "remoteVerified": True},
        "images": images,
        "retainedImageIds": [SIDECAR_ID, CLIENT_ID],
        "transferCleanup": {
            "archiveAbsent": True,
            "prepareReceiptAbsent": True,
            "incomingDirectoryAbsent": True,
            "errors": [],
        },
        "retryAllowed": False,
        "terminalOperation": {
            "operation": "provision",
            "handle": handle,
            "retryAllowed": False,
        },
    }
    state = repo / ".cvm-sidecar-probes" / handle
    state.mkdir(parents=True)
    (state / "prepare.json").write_text(json.dumps(prepare), encoding="utf-8")
    (state / "provision.json").write_text(json.dumps(provision), encoding="utf-8")
    return provision


def valid_probe_success(
    handle: str, provision: dict[str, object]
) -> dict[str, object]:
    suffix = handle.removeprefix("cvmsp-")
    job = f"cvm-probe-{suffix[:12]}"
    prefix = f"ttc-cvmsp-{suffix[:16]}"
    owner = "d" * 32
    request_sha = "b155c2ac8a5396971825cd09626f75510d2669fbcdd669f9e1cfe9ce41fdf3a6"
    result = {
        "connected": True,
        "browserExecutablesVisible": [],
        "contextCount": 1,
        "pageCount": 1,
        "sourceAliasesVisible": [],
        "externalEgressBlocked": True,
    }
    return {
        "schema": "cvm-sidecar.probe-receipt/1",
        "status": "succeeded",
        "handle": handle,
        "sourceRevision": provision["sourceRevision"],
        "imageSourceRevision": provision["imageSourceRevision"],
        "workflowSourceRevision": provision["workflowSourceRevision"],
        "workflowFilesVerified": provision["workflowFilesVerified"],
        "freeBytesAtProbe": 4 * 1024 * 1024 * 1024,
        "images": provision["images"],
        "requestSha256": request_sha,
        "readiness": {
            "event": "ready",
            "jobId": job,
            "endpointPath": "/fixed",
            "programs": {"viewer": "viewer", "residual": "residual"},
        },
        "result": {
            "ok": True,
            "schema": "meshshot.browser-sidecar.render-request/2",
            "requestSha256": request_sha,
            "program": "probe",
            "jobId": job,
            "result": result,
        },
        "outerConfig": {
            "readonlyRootfs": True,
            "mounts": [],
            "memory": 1610612736,
            "memorySwap": 1610612736,
            "nanoCpus": 1500000000,
            "pidsLimit": 256,
            "shmSize": 268435456,
        },
        "resourceLedger": [
            {"kind": "network", "name": f"{prefix}-net", "state": "removed", "ownerNonce": owner, "id": "a" * 64},
            {"kind": "container", "name": f"{prefix}-sidecar", "state": "removed", "ownerNonce": owner, "id": "b" * 64},
            {"kind": "container", "name": f"{prefix}-client", "state": "removed", "ownerNonce": owner, "id": "c" * 64},
        ],
        "ownerNonce": owner,
        "terminal": {
            "state": {"Running": False, "ExitCode": 0},
            "closingObserved": True,
        },
        "absenceProof": {
            "label": f"io.text-to-cad.cvm-sidecar-handle={handle}",
            "ownerNonce": owner,
            "containers": [],
            "networks": [],
            "errors": [],
            "proved": True,
        },
        "retainedImageIds": [SIDECAR_ID, CLIENT_ID],
        "terminalOperation": {
            "operation": "probe",
            "handle": handle,
            "retryAllowed": False,
        },
        "errorOperation": None,
        "errorCheck": None,
        "cleanupErrors": [],
        "retryAllowed": False,
    }


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
            write_image_docker(docker)
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
            self.assertTrue(archive.is_file())
            self.assertEqual(
                receipt["archive"]["sha256"],
                hashlib.sha256(archive.read_bytes()).hexdigest(),
            )
            self.assertEqual(receipt["archive"]["bytes"], archive.stat().st_size)
            self.assertEqual(
                [image["configSha256"] for image in receipt["images"]],
                [
                    SIDECAR_ID.removeprefix("sha256:"),
                    CLIENT_ID.removeprefix("sha256:"),
                ],
            )

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

    def test_prepare_config_digest_is_portable_across_inspect_display(self) -> None:
        receipts = []
        for variant in ("null", "empty-extra"):
            with tempfile.TemporaryDirectory(
                prefix=f"cvm-sidecar-config-{variant}-"
            ) as root_text:
                root = Path(root_text)
                repo = root / "repo"
                wrapper = copy_cli(repo)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                write_portable_archive_docker(fake_bin / "docker")
                env = cli_env(fake_bin)
                env["FAKE_INSPECT_VARIANT"] = variant
                result = subprocess.run(
                    [
                        wrapper,
                        "prepare",
                        "--source-revision",
                        SOURCE_REVISION,
                        "--sidecar-image",
                        PORTABLE_SIDECAR_ID,
                        "--client-image",
                        PORTABLE_CLIENT_ID,
                    ],
                    cwd=repo,
                    env=env,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                receipts.append(json.loads(result.stdout))

        expected = [
            PORTABLE_SIDECAR_ID.removeprefix("sha256:"),
            PORTABLE_CLIENT_ID.removeprefix("sha256:"),
        ]
        self.assertEqual(
            [image["configSha256"] for image in receipts[0]["images"]], expected
        )
        self.assertEqual(receipts[0]["images"], receipts[1]["images"])

    def test_prepare_bounds_unexpected_role_inspect_boundary_failures(self) -> None:
        for role, image_id in (("sidecar", SIDECAR_ID), ("client", CLIENT_ID)):
            for phase, suffix, message in (
                (
                    "run",
                    "inspect-id-access",
                    f"{role} fixed image id inspection is inaccessible",
                ),
                (
                    "parse",
                    "inspect-id-format",
                    f"{role} fixed image id inspection format is invalid",
                ),
            ):
                with self.subTest(role=role, phase=phase), tempfile.TemporaryDirectory(
                    prefix=f"cvm-sidecar-{role}-{phase}-"
                ) as root_text:
                    root = Path(root_text)
                    repo = root / "repo"
                    wrapper = copy_cli(repo)
                    fake_bin = root / "bin"
                    fake_bin.mkdir()
                    write_image_docker(fake_bin / "docker")
                    (root / "sitecustomize.py").write_text(
                        textwrap.dedent(
                            """\
                            import os
                            import subprocess

                            target = os.environ["FAKE_INSPECT_EXCEPTION_ID"]
                            phase = os.environ["FAKE_INSPECT_EXCEPTION_PHASE"]
                            if phase == "run":
                                original_run = subprocess.run

                                def bounded_run(argv, *args, **kwargs):
                                    if list(argv)[:3] == ["docker", "image", "inspect"] and list(argv)[-1] == target:
                                        raise OSError(13, "forbidden raw inspect launch detail", "/private/inspect/socket")
                                    return original_run(argv, *args, **kwargs)

                                subprocess.run = bounded_run
                            elif phase == "parse":
                                original_run = subprocess.run

                                class BrokenCompleted:
                                    returncode = 0

                                    @property
                                    def stdout(self):
                                        raise RuntimeError("forbidden raw inspect parse detail")

                                def bounded_run(argv, *args, **kwargs):
                                    if list(argv)[:3] == ["docker", "image", "inspect"] and list(argv)[-1] == target:
                                        return BrokenCompleted()
                                    return original_run(argv, *args, **kwargs)

                                subprocess.run = bounded_run
                            """
                        ),
                        encoding="utf-8",
                    )
                    env = cli_env(fake_bin)
                    env["PYTHONPATH"] = os.fspath(root)
                    env["FAKE_INSPECT_EXCEPTION_ID"] = image_id.removeprefix(
                        "sha256:"
                    )
                    env["FAKE_INSPECT_EXCEPTION_PHASE"] = phase

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

                    self.assertEqual(result.returncode, 2, suffix)
                    self.assertEqual(
                        result.stderr,
                        f"cvm-sidecar-probe: {message}\n",
                    )
                    self.assertEqual(result.stdout, "")
                    self.assertNotIn("forbidden raw", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertNotIn(root_text, result.stderr)
                    self.assertFalse((repo / ".cvm-sidecar-probes").exists())

    def test_prepare_attests_fixed_docker_save_as_opaque_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-opaque-save-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_portable_archive_docker(fake_bin / "docker")
            env = cli_env(fake_bin)
            env["FAKE_MANIFEST_VARIANT"] = "opaque"

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
            archive = repo / receipt["archive"]["relativePath"]
            self.assertEqual(archive.read_bytes(), b"opaque fixed docker-save output\n")
            self.assertEqual(
                receipt["archive"]["sha256"],
                "05099491da3e4de94093bab6672a0cdeabbb2744089dba3e6a2b6201cb5447ff",
            )

    def test_prepare_archive_contract_exposes_no_parser_surface(self) -> None:
        self.assertNotIn("tarfile", vars(cvm_sidecar_probe))
        self.assertNotIn("_verify_saved_archive_configs", vars(cvm_sidecar_probe))

    def test_prepare_cleanup_failure_is_bounded_and_dominates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-prepare-cleanup-") as root_text:
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
                    import pathlib, sys
                    if sys.argv[1:4] == ["image", "inspect", "--format"]:
                        projection = sys.argv[4]
                        image = sys.argv[5]
                        canonical_image = "sha256:" + image
                        values = {{
                            "{{{{.Id}}}}": canonical_image,
                            "{{{{.Os}}}}": "linux",
                            "{{{{.Architecture}}}}": "amd64",
                            '{{{{index .Config.Labels "org.opencontainers.image.revision"}}}}': {SOURCE_REVISION!r},
                        }}
                        print(values[projection])
                    elif sys.argv[1:3] == ["image", "save"]:
                        pathlib.Path(sys.argv[4]).mkdir()
                        raise SystemExit(7)
                    else:
                        raise SystemExit("unexpected fixed docker operation")
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)

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
                env=cli_env(fake_bin),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "cvm-sidecar-probe: prepare cleanup could not prove absence\n",
            )
            self.assertNotIn(root_text, result.stderr)

    def test_prepare_operation_failure_after_cleanup_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-prepare-operation-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            (root / "sitecustomize.py").write_text(
                textwrap.dedent(
                    """\
                    import os

                    original_replace = os.replace
                    failed = False

                    def bounded_replace(source, destination):
                        global failed
                        if not failed and os.fspath(destination).endswith("/images.tar"):
                            failed = True
                            raise OSError(5, "forbidden raw replace detail", destination)
                        return original_replace(source, destination)

                    os.replace = bounded_replace
                    """
                ),
                encoding="utf-8",
            )
            env = cli_env(fake_bin)
            env["PYTHONPATH"] = os.fspath(root)

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

            self.assertEqual(result.returncode, 2)
            self.assertEqual(
                result.stderr,
                "cvm-sidecar-probe: prepare operation failed\n",
            )
            self.assertEqual(result.stdout, "")
            self.assertNotIn("forbidden raw replace detail", result.stderr)
            self.assertNotIn(root_text, result.stderr)
            self.assertFalse((repo / ".cvm-sidecar-probes").exists())

    def test_provision_rejects_mutated_local_archive_before_transfer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-local-tamper-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            marker = root / "transport-called"
            for tool in ("ssh", "rsync"):
                boundary = fake_bin / tool
                boundary.write_text(
                    '#!/bin/sh\n: > "$FAKE_TRANSPORT_MARKER"\nexit 99\n',
                    encoding="utf-8",
                )
                boundary.chmod(0o755)
            env = cli_env(fake_bin)
            env["FAKE_TRANSPORT_MARKER"] = os.fspath(marker)
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
            receipt = json.loads(prepared.stdout)
            archive = repo / receipt["archive"]["relativePath"]
            archive.write_bytes(archive.read_bytes() + b"tampered")

            result = subprocess.run(
                [wrapper, "provision", receipt["handle"]],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("archive does not match", result.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(
                (
                    repo
                    / ".cvm-sidecar-probes"
                    / receipt["handle"]
                    / "provision-attempt.json"
                ).exists()
            )

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

    def test_remote_begin_hash_and_disk_gates_precede_state_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-begin-gates-") as root_text:
            state_root = Path(root_text) / "state"
            expected = {"module": "1" * 64, "wrapper": "2" * 64}
            handle = "cvmsp-" + "7" * 24
            enough = type("Usage", (), {"free": 4 * 1024 * 1024 * 1024})()
            low = type("Usage", (), {"free": 2 * 1024 * 1024 * 1024})()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=expected
                ),
                mock.patch.object(cvm_sidecar_probe.shutil, "disk_usage", return_value=enough),
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as hash_error:
                    cvm_sidecar_probe.remote_begin(
                        handle,
                        "a" * 32,
                        "3" * 64,
                        expected["wrapper"],
                        17,
                        "4" * 64,
                    )
            self.assertEqual(hash_error.exception.check, "deployed-workflow-hash")
            self.assertFalse(state_root.exists())

            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=expected
                ),
                mock.patch.object(cvm_sidecar_probe.shutil, "disk_usage", return_value=low),
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as disk_error:
                    cvm_sidecar_probe.remote_begin(
                        handle,
                        "a" * 32,
                        expected["module"],
                        expected["wrapper"],
                        17,
                        "4" * 64,
                    )
            self.assertEqual(disk_error.exception.check, "remote-capacity-gate")
            self.assertFalse(state_root.exists())

            owner = "e" * 32
            provision_handle = "cvmsp-" + "9" * 24
            provision_state = state_root / provision_handle
            provision_state.mkdir(parents=True)
            (provision_state / "incoming").mkdir()
            (provision_state / "provision-attempt.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.remote-provision-attempt/1",
                        "handle": provision_handle,
                        "ownerNonce": owner,
                        "workflowFilesVerified": expected,
                        "freeBytes": 4 * 1024 * 1024 * 1024,
                        "archive": {"bytes": 17, "sha256": "4" * 64},
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=expected
                ),
                mock.patch.object(cvm_sidecar_probe.shutil, "disk_usage", return_value=low),
                mock.patch.object(cvm_sidecar_probe, "_run") as run,
            ):
                load_gate = cvm_sidecar_probe.remote_provision(provision_handle, owner)
            self.assertEqual(load_gate["errorCheck"], "remote-disk-gate")
            run.assert_not_called()

    def test_remote_begin_requires_archive_capacity_before_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-capacity-gate-") as root_text:
            state_root = Path(root_text) / "state"
            handle = "cvmsp-" + "e" * 24
            owner = "a" * 32
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            archive_bytes = 700 * 1024 * 1024
            archive_sha256 = "3" * 64
            required = 3 * 1024 * 1024 * 1024 + archive_bytes
            low = type("Usage", (), {"free": required - 1})()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=workflow
                ),
                mock.patch.object(cvm_sidecar_probe.shutil, "disk_usage", return_value=low),
                mock.patch.object(cvm_sidecar_probe, "_run") as run,
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as error:
                    cvm_sidecar_probe.remote_begin(
                        handle,
                        owner,
                        workflow["module"],
                        workflow["wrapper"],
                        archive_bytes,
                        archive_sha256,
                    )

            self.assertEqual(error.exception.check, "remote-capacity-gate")
            self.assertFalse(state_root.exists())
            run.assert_not_called()

    def test_remote_begin_requires_linux_amd64_docker_server_before_state(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-docker-gate-") as root_text:
            state_root = Path(root_text) / "state"
            handle = "cvmsp-" + "f" * 24
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            archive_bytes = 17
            enough = type(
                "Usage",
                (),
                {"free": 3 * 1024 * 1024 * 1024 + archive_bytes},
            )()
            docker = subprocess.CompletedProcess(
                ["docker", "version"],
                0,
                '{"Os":"darwin","Arch":"arm64"}\n',
                "",
            )
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=workflow
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(cvm_sidecar_probe, "_run", return_value=docker),
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as error:
                    cvm_sidecar_probe.remote_begin(
                        handle,
                        "a" * 32,
                        workflow["module"],
                        workflow["wrapper"],
                        archive_bytes,
                        "3" * 64,
                    )

            self.assertEqual(error.exception.check, "docker-server-platform")
            self.assertFalse(state_root.exists())

    def test_remote_begin_receipt_binds_archive_capacity_and_docker_server(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-begin-binding-") as root_text:
            state_root = Path(root_text) / "state"
            handle = "cvmsp-" + "6" * 24
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            archive_bytes = 23
            required = 3 * 1024 * 1024 * 1024 + archive_bytes
            enough = type("Usage", (), {"free": required + 4096})()
            docker = subprocess.CompletedProcess(
                ["docker", "version"],
                0,
                '{"Os":"linux","Arch":"amd64"}\n',
                "",
            )
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe, "_workflow_file_hashes", return_value=workflow
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(cvm_sidecar_probe, "_run", return_value=docker),
            ):
                receipt = cvm_sidecar_probe.remote_begin(
                    handle,
                    "a" * 32,
                    workflow["module"],
                    workflow["wrapper"],
                    archive_bytes,
                    "3" * 64,
                )

            self.assertEqual(receipt["freeBytes"], required + 4096)
            self.assertEqual(receipt["requiredFreeBytes"], required)
            self.assertEqual(
                receipt["archive"], {"bytes": archive_bytes, "sha256": "3" * 64}
            )
            self.assertEqual(
                receipt["dockerServer"],
                {"os": "linux", "architecture": "amd64"},
            )
            attempt = json.loads(
                (state_root / handle / "provision-attempt.json").read_text()
            )
            self.assertEqual(attempt["archive"], receipt["archive"])
            self.assertEqual(attempt["requiredFreeBytes"], required)

    def test_remote_probe_rechecks_workflow_and_disk_before_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-probe-gates-") as root_text:
            repo = Path(root_text) / "repo"
            wrapper = copy_cli(repo)
            handle = "cvmsp-" + "c" * 24
            provision = write_verified_local_provision(repo, handle)
            enough = type("Usage", (), {"free": 4 * 1024 * 1024 * 1024})()
            low = type("Usage", (), {"free": 2 * 1024 * 1024 * 1024})()
            state_root = repo / ".cvm-sidecar-probes"
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_workflow_file_hashes",
                    return_value={"module": "e" * 64, "wrapper": "f" * 64},
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(cvm_sidecar_probe, "_docker") as docker,
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as hash_error:
                    cvm_sidecar_probe.remote_probe(handle)

            self.assertEqual(hash_error.exception.check, "deployed-workflow-hash")
            self.assertFalse((state_root / handle / "probe-attempt.json").exists())
            docker.assert_not_called()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_workflow_file_hashes",
                    return_value=provision["workflowFilesVerified"],
                ),
                mock.patch.object(cvm_sidecar_probe.shutil, "disk_usage", return_value=low),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_docker",
                    side_effect=AssertionError("Docker must remain untouched"),
                ) as docker,
            ):
                with self.assertRaises(cvm_sidecar_probe.ProbeError) as error:
                    cvm_sidecar_probe.remote_probe(handle)

            self.assertEqual(error.exception.check, "remote-disk-gate")
            self.assertFalse((state_root / handle / "probe-attempt.json").exists())
            docker.assert_not_called()

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
                    parts = command.split()
                    if "remote-begin" in parts:
                        index = parts.index("remote-begin")
                        handle, owner, module_hash, wrapper_hash, archive_bytes, archive_sha = parts[index + 1:index + 7]
                        archive_bytes = int(archive_bytes)
                        print(json.dumps({
                            "schema": "cvm-sidecar.remote-begin-receipt/1",
                            "status": "ready-for-fixed-archive",
                            "handle": handle,
                            "ownerNonce": owner,
                            "workflowFilesVerified": {"module": module_hash, "wrapper": wrapper_hash},
                            "freeBytes": 4 * 1024 * 1024 * 1024,
                            "requiredFreeBytes": 3 * 1024 * 1024 * 1024 + archive_bytes,
                            "archive": {"bytes": archive_bytes, "sha256": archive_sha},
                            "dockerServer": {"os": "linux", "architecture": "amd64"},
                        }))
                    elif "remote-provision" in parts:
                        index = parts.index("remote-provision")
                        handle, owner = parts[index + 1:index + 3]
                        prepared = json.loads(pathlib.Path(os.environ["FAKE_PREPARE"]).read_text())
                        images = prepared["images"]
                        print(json.dumps({
                            "schema": "cvm-sidecar.provision-receipt/1",
                            "status": "provisioned",
                            "handle": handle,
                            "ownerNonce": owner,
                            "sourceRevision": prepared["sourceRevision"],
                            "imageSourceRevision": prepared["imageSourceRevision"],
                            "workflowSourceRevision": prepared["workflowSourceRevision"],
                            "workflowFilesVerified": prepared["workflowFiles"],
                            "freeBytesAtLoad": 4 * 1024 * 1024 * 1024,
                            "archive": {"sha256": prepared["archive"]["sha256"], "bytes": prepared["archive"]["bytes"], "remoteVerified": True},
                            "images": images,
                            "retainedImageIds": [item["id"] for item in images],
                            "transferCleanup": {"archiveAbsent": True, "prepareReceiptAbsent": True, "incomingDirectoryAbsent": True, "errors": []},
                            "retryAllowed": False,
                            "terminalOperation": {"operation": "provision", "handle": handle, "retryAllowed": False},
                        }))
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
            prepare_receipt = json.loads(prepared.stdout)
            handle = prepare_receipt["handle"]
            env["FAKE_PREPARE"] = os.fspath(
                repo / ".cvm-sidecar-probes" / handle / "prepare.json"
            )

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

    def test_public_provision_rejects_remote_receipt_that_changes_image(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-receipt-bind-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            ssh = fake_bin / "ssh"
            ssh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    command = sys.argv[-1]
                    parts = command.split()
                    if "remote-begin" in parts:
                        index = parts.index("remote-begin")
                        handle, owner, module_hash, wrapper_hash, archive_bytes, archive_sha = parts[index + 1:index + 7]
                        archive_bytes = int(archive_bytes)
                        print(json.dumps({
                            "schema": "cvm-sidecar.remote-begin-receipt/1",
                            "status": "ready-for-fixed-archive",
                            "handle": handle,
                            "ownerNonce": owner,
                            "workflowFilesVerified": {"module": module_hash, "wrapper": wrapper_hash},
                            "freeBytes": 4 * 1024 * 1024 * 1024,
                            "requiredFreeBytes": 3 * 1024 * 1024 * 1024 + archive_bytes,
                            "archive": {"bytes": archive_bytes, "sha256": archive_sha},
                            "dockerServer": {"os": "linux", "architecture": "amd64"},
                        }))
                    elif "remote-provision" in parts:
                        index = parts.index("remote-provision")
                        handle, owner = parts[index + 1:index + 3]
                        prepared = json.loads(pathlib.Path(os.environ["FAKE_PREPARE"]).read_text())
                        images = [dict(item) for item in prepared["images"]]
                        images[1]["id"] = "sha256:" + "9" * 64
                        print(json.dumps({
                            "schema": "cvm-sidecar.provision-receipt/1",
                            "status": "provisioned",
                            "handle": handle,
                            "ownerNonce": owner,
                            "sourceRevision": prepared["sourceRevision"],
                            "imageSourceRevision": prepared["imageSourceRevision"],
                            "workflowSourceRevision": prepared["workflowSourceRevision"],
                            "workflowFilesVerified": prepared["workflowFiles"],
                            "freeBytesAtLoad": 4 * 1024 * 1024 * 1024,
                            "archive": {"sha256": prepared["archive"]["sha256"], "bytes": prepared["archive"]["bytes"], "remoteVerified": True},
                            "images": images,
                            "retainedImageIds": [item["id"] for item in images],
                            "transferCleanup": {"archiveAbsent": True, "prepareReceiptAbsent": True, "incomingDirectoryAbsent": True, "errors": []},
                            "retryAllowed": False,
                            "terminalOperation": {"operation": "provision", "handle": handle, "retryAllowed": False},
                        }))
                    elif "remote-abort" in parts:
                        index = parts.index("remote-abort")
                        handle, owner = parts[index + 1:index + 3]
                        print(json.dumps({"schema": "cvm-sidecar.abort-receipt/1", "status": "aborted", "handle": handle, "ownerNonce": owner, "transferAbsenceProved": True, "errors": [], "retryAllowed": False}))
                    else:
                        raise SystemExit("unexpected fixed ssh command")
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            rsync = fake_bin / "rsync"
            rsync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rsync.chmod(0o755)
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
            prepare_receipt = json.loads(prepared.stdout)
            handle = prepare_receipt["handle"]
            env["FAKE_PREPARE"] = os.fspath(
                repo / ".cvm-sidecar-probes" / handle / "prepare.json"
            )

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
            terminal = json.loads(
                (repo / ".cvm-sidecar-probes" / handle / "provision.json").read_text()
            )
            self.assertEqual(terminal["status"], "failed")
            self.assertFalse(terminal["retryAllowed"])

    def test_public_provision_preserves_bounded_remote_failure_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-public-failure-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            ssh = fake_bin / "ssh"
            ssh.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, sys
                    command = sys.argv[-1]
                    parts = command.split()
                    if "remote-begin" in parts:
                        index = parts.index("remote-begin")
                        handle, owner, module_hash, wrapper_hash, archive_bytes, archive_sha = parts[index + 1:index + 7]
                        archive_bytes = int(archive_bytes)
                        print(json.dumps({
                            "schema": "cvm-sidecar.remote-begin-receipt/1",
                            "status": "ready-for-fixed-archive",
                            "handle": handle,
                            "ownerNonce": owner,
                            "workflowFilesVerified": {"module": module_hash, "wrapper": wrapper_hash},
                            "freeBytes": 4 * 1024 * 1024 * 1024,
                            "requiredFreeBytes": 3 * 1024 * 1024 * 1024 + archive_bytes,
                            "archive": {"bytes": archive_bytes, "sha256": archive_sha},
                            "dockerServer": {"os": "linux", "architecture": "amd64"},
                        }))
                        raise SystemExit(0)
                    if "remote-provision" in parts:
                        index = parts.index("remote-provision")
                        handle, owner = parts[index + 1:index + 3]
                        print(json.dumps({
                            "schema": "cvm-sidecar.provision-receipt/1",
                            "status": "failed",
                            "handle": handle,
                            "ownerNonce": owner,
                            "errorOperation": "remote-provision",
                            "errorCheck": "client-revision",
                            "transferCleanup": {"archiveAbsent": True, "prepareReceiptAbsent": True, "incomingDirectoryAbsent": True, "errors": []},
                            "retryAllowed": False,
                            "terminalOperation": {"operation": "provision", "handle": handle, "retryAllowed": False},
                        }))
                        raise SystemExit(1)
                    if "remote-abort" in parts:
                        index = parts.index("remote-abort")
                        handle, owner = parts[index + 1:index + 3]
                        print(json.dumps({"schema": "cvm-sidecar.abort-receipt/1", "status": "aborted", "handle": handle, "ownerNonce": owner, "transferAbsenceProved": True, "errors": [], "retryAllowed": False}))
                        raise SystemExit(0)
                    raise SystemExit("unexpected operation")
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
            rsync = fake_bin / "rsync"
            rsync.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            rsync.chmod(0o755)
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
            self.assertEqual(receipt["errorCheck"], "client-revision")
            self.assertEqual(receipt["abort"]["status"], "aborted")
            self.assertFalse(receipt["retryAllowed"])

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
            prepare_receipt = json.loads(prepared.stdout)
            handle = prepare_receipt["handle"]
            state = repo / ".cvm-sidecar-probes" / handle
            incoming = state / "incoming"
            incoming.mkdir()
            shutil.move(state / "images.tar", incoming / "images.tar")
            shutil.move(state / "prepare.json", incoming / "prepare.json")
            owner = "a" * 32
            (state / "provision-attempt.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.remote-provision-attempt/1",
                        "handle": handle,
                        "ownerNonce": owner,
                        "workflowFilesVerified": prepare_receipt["workflowFiles"],
                        "freeBytes": 4 * 1024 * 1024 * 1024,
                        "archive": {
                            "bytes": prepare_receipt["archive"]["bytes"],
                            "sha256": prepare_receipt["archive"]["sha256"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-provision", handle, owner],
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

    def test_remote_provision_persists_bounded_archive_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-remote-failure-") as root_text:
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
            prepare_receipt = json.loads(prepared.stdout)
            handle = prepare_receipt["handle"]
            state = repo / ".cvm-sidecar-probes" / handle
            incoming = state / "incoming"
            incoming.mkdir()
            shutil.move(state / "images.tar", incoming / "images.tar")
            shutil.move(state / "prepare.json", incoming / "prepare.json")
            (incoming / "images.tar").write_bytes(b"corrupt")
            owner = "a" * 32
            (state / "provision-attempt.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.remote-provision-attempt/1",
                        "handle": handle,
                        "ownerNonce": owner,
                        "workflowFilesVerified": prepare_receipt["workflowFiles"],
                        "freeBytes": 4 * 1024 * 1024 * 1024,
                        "archive": {
                            "bytes": prepare_receipt["archive"]["bytes"],
                            "sha256": prepare_receipt["archive"]["sha256"],
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    internal_remote_cli(repo),
                    "remote-provision",
                    handle,
                    owner,
                ],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], "failed")
            self.assertEqual(receipt["errorCheck"], "archive-hash-size")
            self.assertEqual(receipt["errorOperation"], "remote-provision")
            self.assertFalse(receipt["retryAllowed"])
            self.assertEqual(receipt["transferCleanup"]["errors"], [])
            self.assertEqual(
                json.loads((state / "provision.json").read_text()), receipt
            )
            self.assertEqual(result.stderr, "")

    def test_remote_provision_classifies_image_load_and_inspect_format(self) -> None:
        for index, expected_check in enumerate(
            ("image-load", "sidecar-inspect-id-format"), start=1
        ):
            with self.subTest(expected_check=expected_check), tempfile.TemporaryDirectory(
                prefix=f"cvm-sidecar-{expected_check}-"
            ) as root_text:
                state_root = Path(root_text) / "state"
                handle = "cvmsp-" + str(index) * 24
                owner = "a" * 32
                workflow = {"module": "1" * 64, "wrapper": "2" * 64}
                archive_bytes = b"x"
                archive_sha = hashlib.sha256(archive_bytes).hexdigest()
                state = state_root / handle
                incoming = state / "incoming"
                incoming.mkdir(parents=True)
                (state / "provision-attempt.json").write_text(
                    json.dumps(
                        {
                            "schema": "cvm-sidecar.remote-provision-attempt/1",
                            "handle": handle,
                            "ownerNonce": owner,
                            "workflowFilesVerified": workflow,
                            "freeBytes": 4 * 1024 * 1024 * 1024,
                            "archive": {"bytes": 1, "sha256": archive_sha},
                        }
                    ),
                    encoding="utf-8",
                )
                (incoming / "prepare.json").write_text(
                    json.dumps(
                        {
                            "schema": "cvm-sidecar.prepare-receipt/1",
                            "handle": handle,
                            "archive": {"bytes": 1, "sha256": archive_sha},
                            "images": [
                                {
                                    "role": "sidecar",
                                    "id": SIDECAR_ID,
                                    "platform": "linux/amd64",
                                    "configSha256": SIDECAR_ID.removeprefix("sha256:"),
                                    "sourceRevision": SOURCE_REVISION,
                                },
                                {
                                    "role": "client",
                                    "id": CLIENT_ID,
                                    "platform": "linux/amd64",
                                    "configSha256": CLIENT_ID.removeprefix("sha256:"),
                                    "sourceRevision": SOURCE_REVISION,
                                },
                            ],
                        }
                    ),
                    encoding="utf-8",
                )
                (incoming / "images.tar").write_bytes(archive_bytes)

                def fixed_run(
                    argv: object, **kwargs: object
                ) -> subprocess.CompletedProcess[str]:
                    del kwargs
                    arguments = list(argv)
                    if arguments[1:3] == ["image", "load"]:
                        if expected_check == "image-load":
                            raise cvm_sidecar_probe.ProbeError("load failed")
                        return subprocess.CompletedProcess(arguments, 0, "loaded\n", "")
                    if arguments[1:3] == ["image", "inspect"]:
                        return subprocess.CompletedProcess(
                            arguments, 0, "invalid\tfield\n", ""
                        )
                    raise AssertionError(f"unexpected command: {arguments}")

                enough = type(
                    "Usage", (), {"free": 4 * 1024 * 1024 * 1024}
                )()
                with (
                    mock.patch.object(
                        cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root
                    ),
                    mock.patch.object(
                        cvm_sidecar_probe,
                        "_workflow_file_hashes",
                        return_value=workflow,
                    ),
                    mock.patch.object(
                        cvm_sidecar_probe.shutil,
                        "disk_usage",
                        return_value=enough,
                    ),
                    mock.patch.object(
                        cvm_sidecar_probe, "_run", side_effect=fixed_run
                    ),
                ):
                    receipt = cvm_sidecar_probe.remote_provision(handle, owner)

                self.assertEqual(receipt["status"], "failed")
                self.assertEqual(receipt["errorCheck"], expected_check)
                self.assertEqual(receipt["transferCleanup"]["errors"], [])
                self.assertEqual(
                    json.loads((state / "provision.json").read_text()), receipt
                )

    def test_remote_provision_splits_portable_image_attestation_checks(self) -> None:
        inspect_formats = (
            ("id", "{{.Id}}"),
            ("os", "{{.Os}}"),
            ("architecture", "{{.Architecture}}"),
            (
                "revision",
                '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            ),
        )
        cases = (
            ("id", "access", "inspect-id-access"),
            ("id", "timeout", "inspect-id-timeout"),
            ("id", "format", "inspect-id-format"),
            ("id", "identity", "id"),
            ("os", "access", "inspect-os-access"),
            ("os", "format", "inspect-os-format"),
            ("os", "identity", "os"),
            ("architecture", "access", "inspect-architecture-access"),
            ("architecture", "format", "inspect-architecture-format"),
            ("architecture", "identity", "architecture"),
            ("revision", "access", "inspect-revision-access"),
            ("revision", "format", "inspect-revision-format"),
            ("revision", "identity", "revision"),
            ("receipt", "identity", "receipt"),
        )
        for role, image_id in (("sidecar", SIDECAR_ID), ("client", CLIENT_ID)):
            for field, variant, check_suffix in cases:
                with self.subTest(
                    role=role, field=field, variant=variant
                ), tempfile.TemporaryDirectory(
                    prefix=f"cvm-sidecar-{role}-{field}-{variant}-"
                ) as root_text:
                    handle = "cvmsp-" + "6" * 24
                    owner = "a" * 32
                    workflow = {"module": "1" * 64, "wrapper": "2" * 64}
                    images = remote_provision_images()
                    if field == "receipt":
                        target = 0 if role == "sidecar" else 1
                        images[target]["configSha256"] = "f" * 64
                    state_root, state, incoming = (
                        write_remote_provision_attempt_fixture(
                            Path(root_text),
                            handle,
                            owner,
                            workflow,
                            images=images,
                        )
                    )
                    inspect_calls: list[list[str]] = []

                    def fixed_run(
                        argv: object, **kwargs: object
                    ) -> subprocess.CompletedProcess[str]:
                        del kwargs
                        arguments = list(argv)
                        if arguments[1:3] == ["image", "load"]:
                            return subprocess.CompletedProcess(arguments, 0, "loaded\n", "")
                        if arguments[1:3] != ["image", "inspect"]:
                            raise AssertionError(f"unexpected command: {arguments}")
                        inspect_calls.append(arguments)
                        inspected_address = arguments[-1]
                        inspected_id = "sha256:" + inspected_address
                        inspected_role = (
                            "sidecar" if inspected_id == SIDECAR_ID else "client"
                        )
                        inspected_field = {
                            projection: name for name, projection in inspect_formats
                        }.get(arguments[4])
                        if inspected_field is None:
                            raise AssertionError(
                                f"unexpected inspect format: {arguments[4]}"
                            )
                        if (
                            inspected_role == role
                            and inspected_field == field
                            and variant == "access"
                        ):
                            raise cvm_sidecar_probe.ProbeError("opaque inspect failure")
                        if (
                            inspected_role == role
                            and inspected_field == field
                            and variant == "timeout"
                        ):
                            raise cvm_sidecar_probe.ProbeError(
                                "opaque inspect timeout", check="docker-timeout"
                            )
                        if (
                            inspected_role == role
                            and inspected_field == field
                            and variant == "format"
                        ):
                            output = "invalid\tfield"
                        else:
                            good_values = {
                                "id": inspected_id,
                                "os": "linux",
                                "architecture": "amd64",
                                "revision": SOURCE_REVISION,
                            }
                            identity_failures = {
                                "id": "sha256:" + "9" * 64,
                                "os": "windows",
                                "architecture": "arm64",
                                "revision": "b" * 40,
                            }
                            output = (
                                identity_failures[inspected_field]
                                if inspected_role == role
                                and inspected_field == field
                                and variant == "identity"
                                else good_values[inspected_field]
                            )
                        return subprocess.CompletedProcess(
                            arguments, 0, output + "\n", ""
                        )

                    enough = type(
                        "Usage", (), {"free": 4 * 1024 * 1024 * 1024}
                    )()
                    with (
                        mock.patch.object(
                            cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe,
                            "_workflow_file_hashes",
                            return_value=workflow,
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe.shutil,
                            "disk_usage",
                            return_value=enough,
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe, "_run", side_effect=fixed_run
                        ),
                    ):
                        receipt = cvm_sidecar_probe.remote_provision(handle, owner)

                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(
                        receipt["errorCheck"], f"{role}-{check_suffix}"
                    )
                    field_formats = [value for _, value in inspect_formats]
                    if field == "receipt":
                        target_formats = []
                    else:
                        target_formats = field_formats[
                            : [name for name, _ in inspect_formats].index(field) + 1
                        ]
                    expected_formats = (
                        target_formats
                        if role == "sidecar"
                        else field_formats + target_formats
                    )
                    self.assertEqual(
                        [arguments[4] for arguments in inspect_calls],
                        expected_formats,
                    )
                    for arguments in inspect_calls:
                        self.assertEqual(arguments[1:4], ["image", "inspect", "--format"])
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
                    self.assertEqual(
                        json.loads((state / "provision.json").read_text()), receipt
                    )

    def test_remote_provision_uses_older_docker_compatible_projection(self) -> None:
        portable_formats = (
            "{{.Id}}",
            "{{.Os}}",
            "{{.Architecture}}",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        )
        with tempfile.TemporaryDirectory(
            prefix="cvm-sidecar-older-docker-inspect-"
        ) as root_text:
            handle = "cvmsp-" + "8" * 24
            owner = "a" * 32
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            state_root, _, _ = write_remote_provision_attempt_fixture(
                Path(root_text),
                handle,
                owner,
                workflow,
            )
            inspect_formats: list[str] = []

            def older_docker_run(
                argv: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                del kwargs
                arguments = list(argv)
                if arguments[1:3] == ["image", "load"]:
                    return subprocess.CompletedProcess(arguments, 0, "loaded\n", "")
                if arguments[1:3] != ["image", "inspect"]:
                    raise AssertionError(f"unexpected command: {arguments}")
                inspect_format = arguments[4]
                inspect_formats.append(inspect_format)
                if "json" in inspect_format:
                    raise cvm_sidecar_probe.ProbeError(
                        "older Docker rejected composite inspect template"
                    )
                if inspect_format not in portable_formats:
                    raise AssertionError(f"unexpected inspect format: {inspect_format}")
                inspected_address = arguments[-1]
                inspected_id = "sha256:" + inspected_address
                values = {
                    "{{.Id}}": inspected_id,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                }
                return subprocess.CompletedProcess(
                    arguments,
                    0,
                    values[inspect_format] + "\n",
                    "",
                )

            enough = type("Usage", (), {"free": 4 * 1024 * 1024 * 1024})()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_workflow_file_hashes",
                    return_value=workflow,
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(
                    cvm_sidecar_probe, "_run", side_effect=older_docker_run
                ),
            ):
                receipt = cvm_sidecar_probe.remote_provision(handle, owner)

            self.assertEqual(
                receipt["status"], "provisioned", receipt.get("errorCheck")
            )
            self.assertEqual(inspect_formats, list(portable_formats) * 2)

    def test_remote_provision_distinguishes_id_only_inspect_access(self) -> None:
        projections = (
            "{{.Id}}",
            "{{.Os}}",
            "{{.Architecture}}",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
        )
        for role, image_id in (("sidecar", SIDECAR_ID), ("client", CLIENT_ID)):
            for reason, returncode in (("not-addressable", 1), ("command-nonzero", 64)):
                with self.subTest(
                    role=role, reason=reason
                ), tempfile.TemporaryDirectory(
                    prefix=f"cvm-sidecar-{role}-id-{reason}-"
                ) as root_text:
                    handle = "cvmsp-" + "9" * 24
                    owner = "a" * 32
                    workflow = {"module": "1" * 64, "wrapper": "2" * 64}
                    state_root, state, incoming = (
                        write_remote_provision_attempt_fixture(
                            Path(root_text), handle, owner, workflow
                        )
                    )
                    inspect_formats: list[str] = []

                    def fixed_subprocess_run(
                        argv: object, **kwargs: object
                    ) -> subprocess.CompletedProcess[str]:
                        del kwargs
                        arguments = list(argv)
                        if arguments[1:3] == ["image", "load"]:
                            return subprocess.CompletedProcess(
                                arguments, 0, "loaded\n", ""
                            )
                        if arguments[1:3] != ["image", "inspect"]:
                            raise AssertionError(f"unexpected command: {arguments}")
                        projection = arguments[4]
                        inspected_address = arguments[-1]
                        inspected_id = "sha256:" + inspected_address
                        inspect_formats.append(projection)
                        if inspected_id == image_id and projection == "{{.Id}}":
                            return subprocess.CompletedProcess(
                                arguments,
                                returncode,
                                "",
                                "fixed inaccessible image" if reason == "not-addressable" else "fixed command failure",
                            )
                        values = {
                            "{{.Id}}": inspected_id,
                            "{{.Os}}": "linux",
                            "{{.Architecture}}": "amd64",
                            '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                        }
                        return subprocess.CompletedProcess(
                            arguments, 0, values[projection] + "\n", ""
                        )

                    enough = type(
                        "Usage", (), {"free": 4 * 1024 * 1024 * 1024}
                    )()
                    with (
                        mock.patch.object(
                            cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe,
                            "_workflow_file_hashes",
                            return_value=workflow,
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe.shutil,
                            "disk_usage",
                            return_value=enough,
                        ),
                        mock.patch.object(
                            cvm_sidecar_probe.subprocess,
                            "run",
                            side_effect=fixed_subprocess_run,
                        ),
                    ):
                        receipt = cvm_sidecar_probe.remote_provision(handle, owner)

                    expected_formats = (
                        ["{{.Id}}"]
                        if role == "sidecar"
                        else list(projections) + ["{{.Id}}"]
                    )
                    self.assertEqual(receipt["status"], "failed")
                    self.assertEqual(
                        receipt["errorCheck"], f"{role}-inspect-id-access"
                    )
                    self.assertEqual(inspect_formats, expected_formats)
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
                    self.assertEqual(
                        json.loads((state / "provision.json").read_text()), receipt
                    )

    def test_remote_provision_uses_bare_config_digest_as_portable_image_address(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="cvm-sidecar-bare-image-address-"
        ) as root_text:
            handle = "cvmsp-" + "a" * 24
            owner = "b" * 32
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            state_root, _, _ = write_remote_provision_attempt_fixture(
                Path(root_text), handle, owner, workflow
            )
            inspected_addresses: list[str] = []

            def older_docker_run(
                argv: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                del kwargs
                arguments = list(argv)
                if arguments[1:3] == ["image", "load"]:
                    return subprocess.CompletedProcess(arguments, 0, "loaded\n", "")
                if arguments[1:3] != ["image", "inspect"]:
                    raise AssertionError(f"unexpected command: {arguments}")
                inspected_address = arguments[-1]
                inspected_addresses.append(inspected_address)
                if inspected_address.startswith("sha256:"):
                    return subprocess.CompletedProcess(
                        arguments, 1, "", "unsupported canonical image address"
                    )
                canonical_id = "sha256:" + inspected_address
                values = {
                    "{{.Id}}": canonical_id,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                }
                return subprocess.CompletedProcess(
                    arguments, 0, values[arguments[4]] + "\n", ""
                )

            enough = type("Usage", (), {"free": 4 * 1024 * 1024 * 1024})()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_workflow_file_hashes",
                    return_value=workflow,
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(
                    cvm_sidecar_probe.subprocess,
                    "run",
                    side_effect=older_docker_run,
                ),
            ):
                receipt = cvm_sidecar_probe.remote_provision(handle, owner)

            self.assertEqual(
                receipt["status"], "provisioned", receipt.get("errorCheck")
            )
            self.assertEqual(
                inspected_addresses,
                [SIDECAR_ID.removeprefix("sha256:")] * 4
                + [CLIENT_ID.removeprefix("sha256:")] * 4,
            )

    def test_remote_provision_uses_root_image_inspect_for_legacy_docker(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="cvm-sidecar-root-image-inspect-"
        ) as root_text:
            handle = "cvmsp-" + "b" * 24
            owner = "c" * 32
            workflow = {"module": "1" * 64, "wrapper": "2" * 64}
            state_root, _, _ = write_remote_provision_attempt_fixture(
                Path(root_text), handle, owner, workflow
            )
            inspect_commands: list[list[str]] = []

            def legacy_docker_run(
                argv: object, **kwargs: object
            ) -> subprocess.CompletedProcess[str]:
                del kwargs
                arguments = list(argv)
                if arguments[1:3] == ["image", "load"]:
                    return subprocess.CompletedProcess(arguments, 0, "loaded\n", "")
                inspect_commands.append(arguments)
                if arguments[1:3] == ["image", "inspect"]:
                    return subprocess.CompletedProcess(
                        arguments, 1, "", "image subcommand is unavailable"
                    )
                if arguments[1:4] != ["inspect", "--type=image", "--format"]:
                    raise AssertionError(f"unexpected command: {arguments}")
                canonical_id = "sha256:" + arguments[-1]
                values = {
                    "{{.Id}}": canonical_id,
                    "{{.Os}}": "linux",
                    "{{.Architecture}}": "amd64",
                    '{{index .Config.Labels "org.opencontainers.image.revision"}}': SOURCE_REVISION,
                }
                return subprocess.CompletedProcess(
                    arguments, 0, values[arguments[4]] + "\n", ""
                )

            enough = type("Usage", (), {"free": 4 * 1024 * 1024 * 1024})()
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(
                    cvm_sidecar_probe,
                    "_workflow_file_hashes",
                    return_value=workflow,
                ),
                mock.patch.object(
                    cvm_sidecar_probe.shutil, "disk_usage", return_value=enough
                ),
                mock.patch.object(
                    cvm_sidecar_probe.subprocess,
                    "run",
                    side_effect=legacy_docker_run,
                ),
            ):
                receipt = cvm_sidecar_probe.remote_provision(handle, owner)

            self.assertEqual(
                receipt["status"], "provisioned", receipt.get("errorCheck")
            )
            self.assertEqual(len(inspect_commands), 8)
            self.assertTrue(
                all(
                    command[1:4] == ["inspect", "--type=image", "--format"]
                    for command in inspect_commands
                )
            )

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
            prepare_receipt = json.loads(prepared.stdout)
            handle = prepare_receipt["handle"]
            state = repo / ".cvm-sidecar-probes" / handle
            incoming = state / "incoming"
            incoming.mkdir()
            shutil.move(state / "images.tar", incoming / "images.tar")
            shutil.move(state / "prepare.json", incoming / "prepare.json")
            owner = "a" * 32
            (state / "provision-attempt.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.remote-provision-attempt/1",
                        "handle": handle,
                        "ownerNonce": owner,
                        "workflowFilesVerified": prepare_receipt["workflowFiles"],
                        "freeBytes": 4 * 1024 * 1024 * 1024,
                        "archive": {
                            "bytes": prepare_receipt["archive"]["bytes"],
                            "sha256": prepare_receipt["archive"]["sha256"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (incoming / "unexpected").write_text("retain\n", encoding="utf-8")
            (incoming / "images.tar").write_bytes(b"corrupt")

            result = subprocess.run(
                [sys.executable, internal_remote_cli(repo), "remote-provision", handle, owner],
                cwd=repo,
                env=env,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 1)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["errorCheck"], "transfer-cleanup")
            self.assertIn("incoming-remove", receipt["transferCleanup"]["errors"])
            self.assertEqual(
                json.loads((state / "provision.json").read_text()), receipt
            )
            self.assertEqual(result.stderr, "")
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
                    parts = command.split()
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps({"tool": "ssh", "command": command}) + "\\n")
                    if "remote-begin" in parts:
                        index = parts.index("remote-begin")
                        handle, owner, module_hash, wrapper_hash, archive_bytes, archive_sha = parts[index + 1:index + 7]
                        archive_bytes = int(archive_bytes)
                        print(json.dumps({"schema": "cvm-sidecar.remote-begin-receipt/1", "status": "ready-for-fixed-archive", "handle": handle, "ownerNonce": owner, "workflowFilesVerified": {"module": module_hash, "wrapper": wrapper_hash}, "freeBytes": 4 * 1024 * 1024 * 1024, "requiredFreeBytes": 3 * 1024 * 1024 * 1024 + archive_bytes, "archive": {"bytes": archive_bytes, "sha256": archive_sha}, "dockerServer": {"os": "linux", "architecture": "amd64"}}))
                    elif "remote-abort" in parts:
                        index = parts.index("remote-abort")
                        handle, owner = parts[index + 1:index + 3]
                        print(json.dumps({"schema": "cvm-sidecar.abort-receipt/1", "status": "aborted", "handle": handle, "ownerNonce": owner, "transferAbsenceProved": True, "errors": [], "retryAllowed": False}))
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

    def test_remote_begin_failure_only_attempts_nonce_scoped_abort(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-begin-failure-") as root_text:
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
                        stream.write(json.dumps(sys.argv[-1]) + "\\n")
                    raise SystemExit(17)
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
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
            commands = [json.loads(line) for line in transport_log.read_text().splitlines()]
            self.assertEqual(len(commands), 2)
            self.assertIn(" remote-begin ", commands[0])
            begin = commands[0].split()
            abort = commands[1].split()
            begin_index = begin.index("remote-begin")
            abort_index = abort.index("remote-abort")
            self.assertEqual(
                begin[begin_index + 1:begin_index + 3],
                abort[abort_index + 1:abort_index + 3],
            )

    def test_lost_begin_receipt_uses_local_nonce_for_exact_abort(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-begin-lost-") as root_text:
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
                    parts = command.split()
                    log = pathlib.Path(os.environ["FAKE_TRANSPORT_LOG"])
                    with log.open("a") as stream:
                        stream.write(json.dumps(command) + "\\n")
                    if "remote-begin" in parts:
                        raise SystemExit(0)  # committed begin receipt was lost
                    if "remote-abort" in parts:
                        index = parts.index("remote-abort")
                        handle, owner = parts[index + 1:index + 3]
                        print(json.dumps({
                            "schema": "cvm-sidecar.abort-receipt/1",
                            "status": "aborted",
                            "handle": handle,
                            "ownerNonce": owner,
                            "transferAbsenceProved": True,
                            "errors": [],
                            "retryAllowed": False,
                        }))
                        raise SystemExit(0)
                    raise SystemExit("unexpected ssh operation")
                    """
                ),
                encoding="utf-8",
            )
            ssh.chmod(0o755)
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
            commands = [json.loads(line) for line in transport_log.read_text().splitlines()]
            self.assertEqual(len(commands), 2)
            begin_parts = commands[0].split()
            begin_index = begin_parts.index("remote-begin")
            begin_handle, begin_owner = begin_parts[begin_index + 1:begin_index + 3]
            abort_parts = commands[1].split()
            abort_index = abort_parts.index("remote-abort")
            abort_handle, abort_owner = abort_parts[abort_index + 1:abort_index + 3]
            self.assertEqual(begin_handle, handle)
            self.assertRegex(begin_owner, r"[0-9a-f]{32}")
            self.assertEqual((abort_handle, abort_owner), (handle, begin_owner))
            terminal = json.loads(
                (repo / ".cvm-sidecar-probes" / handle / "provision.json").read_text()
            )
            self.assertEqual(terminal["abort"]["ownerNonce"], begin_owner)
            attempt = json.loads(
                (
                    repo
                    / ".cvm-sidecar-probes"
                    / handle
                    / "provision-attempt.json"
                ).read_text()
            )
            self.assertEqual(attempt["ownerNonce"], begin_owner)

    def test_remote_begin_refuses_to_adopt_a_predictable_existing_handle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-no-adopt-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            write_image_docker(fake_bin / "docker")
            handle = "cvmsp-" + "3" * 24
            (repo / ".cvm-sidecar-probes" / handle).mkdir(parents=True)
            module_hash = hashlib.sha256(internal_remote_cli(repo).read_bytes()).hexdigest()
            wrapper_hash = hashlib.sha256(wrapper.read_bytes()).hexdigest()

            result = subprocess.run(
                [
                    sys.executable,
                    internal_remote_cli(repo),
                    "remote-begin",
                    handle,
                    "a" * 32,
                    module_hash,
                    wrapper_hash,
                    "17",
                    "4" * 64,
                ],
                cwd=repo,
                env=cli_env(fake_bin),
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
                    def labels_from(arguments):
                        return {
                            arguments[index + 1].split("=", 1)[0]: arguments[index + 1].split("=", 1)[1]
                            for index, value in enumerate(arguments)
                            if value == "--label"
                        }
                    def container(target):
                        return next(item for name, item in state["containers"].items() if target in (name, item["id"]))
                    def network(target):
                        return next(item for name, item in state["networks"].items() if target in (name, item["id"]))
                    if argv[:2] == ["network", "create"]:
                        name = argv[-1]; item_id = "a" * 64
                        state["networks"][name] = {"id": item_id, "labels": labels_from(argv)}; print(item_id)
                    elif argv[:2] == ["run", "-d"]:
                        name = argv[argv.index("--name") + 1]
                        job = [item.split("=", 1)[1] for item in argv if item.startswith("BROWSER_SIDECAR_JOB_ID=")][0]
                        item_id = "b" * 64
                        state["containers"][name] = {"id": item_id, "job": job, "labels": labels_from(argv), "running": True, "exit": None}
                        print(item_id)
                    elif argv[:3] == ["container", "create", "--name"]:
                        name = argv[3]
                        job = [item.split("=", 1)[1] for item in argv if item.startswith("BROWSER_SIDECAR_JOB_ID=")][0]
                        item_id = "c" * 64
                        state["containers"][name] = {"id": item_id, "job": job, "labels": labels_from(argv), "running": False, "exit": None}
                        print(item_id)
                    elif argv[:3] == ["container", "start", "--attach"]:
                        item = container(argv[-1]); job = item["job"]
                        request = json.loads(stdin)
                        assert request == {"schema": "meshshot.browser-sidecar.render-request/2", "program": "probe", "payload": {}}
                        item["exit"] = 0
                        print(json.dumps({"ok": True, "schema": request["schema"], "requestSha256": "b155c2ac8a5396971825cd09626f75510d2669fbcdd669f9e1cfe9ce41fdf3a6", "program": "probe", "jobId": job, "result": {"connected": True, "browserExecutablesVisible": [], "contextCount": 1, "pageCount": 1, "sourceAliasesVisible": [], "externalEgressBlocked": True}}))
                    elif argv[0] == "logs":
                        item = container(argv[-1]); job = item["job"]
                        print(json.dumps({"event": "ready", "jobId": job, "endpointPath": "/fixed", "programs": {"viewer": "v", "residual": "r"}}))
                        if not item["running"]:
                            print(json.dumps({"event": "closing", "jobId": job, "reason": "SIGTERM"}))
                    elif argv[:2] == ["container", "inspect"] and "--format" in argv:
                        item = container(argv[2])
                        print(json.dumps({"Running": item["running"], "ExitCode": item["exit"]}))
                    elif argv[:2] == ["container", "inspect"]:
                        item = container(argv[2])
                        print(json.dumps([{"Id": item["id"], "Config": {"Labels": item["labels"]}, "HostConfig": {"ReadonlyRootfs": True, "Memory": 1610612736, "MemorySwap": 1610612736, "NanoCpus": 1500000000, "PidsLimit": 256, "ShmSize": 268435456}, "Mounts": []}]))
                    elif argv[:2] == ["network", "inspect"]:
                        item = network(argv[2]); print(json.dumps([{"Id": item["id"], "Labels": item["labels"]}]))
                    elif argv[0] == "stop":
                        item = container(argv[-1]); item["running"] = False; item["exit"] = 0; print(argv[-1])
                    elif argv[0] == "rm":
                        target = argv[-1]
                        name = next(name for name, item in state["containers"].items() if target in (name, item["id"]))
                        state["containers"].pop(name); print(target)
                    elif argv[:2] == ["network", "rm"]:
                        target = argv[-1]
                        name = next(name for name, item in state["networks"].items() if target in (name, item["id"]))
                        state["networks"].pop(name); print(target)
                    elif argv[:3] == ["container", "ls", "-a"]:
                        print("\\n".join(item["id"] for item in state["containers"].values()))
                    elif argv[:2] == ["network", "ls"]:
                        print("\\n".join(item["id"] for item in state["networks"].values()))
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
            workflow_files = {
                "module": hashlib.sha256(internal_remote_cli(repo).read_bytes()).hexdigest(),
                "wrapper": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }
            (state / "provision.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.provision-receipt/1",
                        "status": "provisioned",
                        "handle": handle,
                        "sourceRevision": SOURCE_REVISION,
                        "workflowFilesVerified": workflow_files,
                        "images": [
                            {"role": "sidecar", "id": SIDECAR_ID, "platform": "linux/amd64", "configSha256": SIDECAR_ID.removeprefix("sha256:")},
                            {"role": "client", "id": CLIENT_ID, "platform": "linux/amd64", "configSha256": CLIENT_ID.removeprefix("sha256:")},
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

    def test_cleanup_timeout_continues_and_writes_terminal_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-cleanup-timeout-") as root_text:
            state_root = Path(root_text) / "state"
            handle = "cvmsp-" + "8" * 24
            (state_root / handle).mkdir(parents=True)
            owner = "d" * 32
            labels = {
                "io.text-to-cad.cvm-sidecar-handle": handle,
                "io.text-to-cad.cvm-sidecar-owner": owner,
            }
            network_id, sidecar_id, client_id = "a" * 64, "b" * 64, "c" * 64
            resources = {
                network_id: {"kind": "network", "labels": labels},
                sidecar_id: {"kind": "container", "labels": labels, "running": True},
                client_id: {"kind": "container", "labels": labels, "running": False},
            }
            calls: list[tuple[str, ...]] = []

            def completed(args: tuple[str, ...], stdout: str = "") -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(args, 0, stdout, "")

            def fake_docker(
                *args: str,
                input_text: str | None = None,
                check: bool = True,
                timeout: int = 300,
            ) -> subprocess.CompletedProcess[str]:
                del check, timeout
                calls.append(args)
                if args[:2] == ("network", "create"):
                    return completed(args, network_id + "\n")
                if args[:2] == ("run", "-d"):
                    return completed(args, sidecar_id + "\n")
                if args[:2] == ("container", "create"):
                    return completed(args, client_id + "\n")
                if args[:2] == ("container", "start"):
                    request = json.loads(input_text or "")
                    payload = {
                        "ok": True,
                        "schema": request["schema"],
                        "requestSha256": "b155c2ac8a5396971825cd09626f75510d2669fbcdd669f9e1cfe9ce41fdf3a6",
                        "program": "probe",
                        "jobId": "cvm-probe-" + handle.removeprefix("cvmsp-")[:12],
                        "result": {
                            "connected": True,
                            "browserExecutablesVisible": [],
                            "contextCount": 1,
                            "pageCount": 1,
                            "sourceAliasesVisible": [],
                            "externalEgressBlocked": True,
                        },
                    }
                    return completed(args, json.dumps(payload) + "\n")
                if args[0] == "logs":
                    job = "cvm-probe-" + handle.removeprefix("cvmsp-")[:12]
                    records = [
                        {"event": "ready", "jobId": job, "endpointPath": "/fixed", "programs": {"viewer": "v", "residual": "r"}}
                    ]
                    if not resources[sidecar_id]["running"]:
                        records.append({"event": "closing", "jobId": job, "reason": "SIGTERM"})
                    return completed(args, "\n".join(json.dumps(item) for item in records) + "\n")
                if args[:2] == ("container", "inspect") and "--format" in args:
                    target = args[2]
                    return completed(
                        args,
                        json.dumps(
                            {
                                "Running": resources[target]["running"],
                                "ExitCode": 0 if not resources[target]["running"] else None,
                            }
                        )
                        + "\n",
                    )
                if args[:2] == ("container", "inspect"):
                    target = args[2]
                    payload = {
                        "Id": target,
                        "Config": {"Labels": labels},
                        "HostConfig": {
                            "ReadonlyRootfs": True,
                            "Memory": 1610612736,
                            "MemorySwap": 1610612736,
                            "NanoCpus": 1500000000,
                            "PidsLimit": 256,
                            "ShmSize": 268435456,
                        },
                        "Mounts": [],
                    }
                    return completed(args, json.dumps([payload]) + "\n")
                if args[:2] == ("network", "inspect"):
                    return completed(
                        args,
                        json.dumps([{"Id": args[2], "Labels": labels}]) + "\n",
                    )
                if args[0] == "stop":
                    resources[args[-1]]["running"] = False
                    raise cvm_sidecar_probe.ProbeError(
                        "fixed command timed out: docker", check="docker-timeout"
                    )
                if args[0] == "rm":
                    resources.pop(args[-1])
                    return completed(args, args[-1] + "\n")
                if args[:2] == ("network", "rm"):
                    resources.pop(args[-1])
                    return completed(args, args[-1] + "\n")
                if args[:3] == ("container", "ls", "-a"):
                    ids = [key for key, value in resources.items() if value["kind"] == "container"]
                    return completed(args, "\n".join(ids))
                if args[:2] == ("network", "ls"):
                    ids = [key for key, value in resources.items() if value["kind"] == "network"]
                    return completed(args, "\n".join(ids))
                raise AssertionError(f"unexpected docker call: {args}")

            provision = {
                "schema": "cvm-sidecar.provision-receipt/1",
                "status": "provisioned",
                "handle": handle,
                "sourceRevision": SOURCE_REVISION,
                "images": [
                    {"role": "sidecar", "id": SIDECAR_ID},
                    {"role": "client", "id": CLIENT_ID},
                ],
            }
            with (
                mock.patch.object(cvm_sidecar_probe, "LOCAL_STATE_ROOT", state_root),
                mock.patch.object(cvm_sidecar_probe, "_docker", side_effect=fake_docker),
            ):
                receipt = cvm_sidecar_probe._run_remote_probe(
                    handle,
                    provision,
                    owner,
                    {"module": "1" * 64, "wrapper": "2" * 64},
                    4 * 1024 * 1024 * 1024,
                )

            self.assertEqual(receipt["status"], "failed")
            self.assertIn("sidecar stop failed", receipt["cleanupErrors"])
            self.assertTrue(receipt["absenceProof"]["proved"])
            self.assertEqual(resources, {})
            self.assertTrue(any(call[0] == "rm" and call[-1] == client_id for call in calls))
            self.assertTrue(any(call[0] == "rm" and call[-1] == sidecar_id for call in calls))
            self.assertTrue(any(call[:2] == ("network", "rm") for call in calls))
            durable = json.loads((state_root / handle / "probe.json").read_text())
            self.assertEqual(durable["status"], "failed")
            self.assertFalse(durable["terminalOperation"]["retryAllowed"])

    def test_public_probe_lost_remote_output_writes_terminal_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-lost-probe-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            ssh = fake_bin / "ssh"
            ssh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            ssh.chmod(0o755)
            handle = "cvmsp-" + "6" * 24
            state = repo / ".cvm-sidecar-probes" / handle
            write_verified_local_provision(repo, handle)

            result = subprocess.run(
                [wrapper, "probe", handle],
                cwd=repo,
                env=cli_env(fake_bin),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            terminal = json.loads((state / "probe.json").read_text())
            self.assertEqual(terminal["schema"], "cvm-sidecar.probe-receipt/1")
            self.assertEqual(terminal["status"], "failed")
            self.assertFalse(terminal["terminalOperation"]["retryAllowed"])
            self.assertEqual(terminal["errorCheck"], "remote-receipt-missing")

    def test_public_probe_rejects_cross_handle_spoofed_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-probe-binding-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            handle = "cvmsp-" + "a" * 24
            write_verified_local_provision(repo, handle)
            spoofed = {
                "schema": "cvm-sidecar.probe-receipt/1",
                "status": "succeeded",
                "handle": "cvmsp-" + "b" * 24,
            }
            ssh = fake_bin / "ssh"
            ssh.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                f"print(json.dumps({spoofed!r}))\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)

            result = subprocess.run(
                [wrapper, "probe", handle],
                cwd=repo,
                env=cli_env(fake_bin),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertNotEqual(result.returncode, 0)
            terminal = json.loads(
                (repo / ".cvm-sidecar-probes" / handle / "probe.json").read_text()
            )
            self.assertEqual(terminal["status"], "failed")
            self.assertEqual(terminal["errorCheck"], "remote-probe-receipt-binding")
            self.assertFalse(terminal["terminalOperation"]["retryAllowed"])

    def test_public_probe_accepts_only_fully_bound_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-probe-success-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            handle = "cvmsp-" + "d" * 24
            provision = write_verified_local_provision(repo, handle)
            receipt = valid_probe_success(handle, provision)
            ssh = fake_bin / "ssh"
            ssh.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                f"print(json.dumps({receipt!r}))\n",
                encoding="utf-8",
            )
            ssh.chmod(0o755)

            result = subprocess.run(
                [wrapper, "probe", handle],
                cwd=repo,
                env=cli_env(fake_bin),
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), receipt)

    def test_public_probe_rejects_image_mismatch_and_ssh_rc_inconsistency(self) -> None:
        for index, failure in enumerate(("image", "ssh"), start=1):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(
                prefix=f"cvm-sidecar-probe-{failure}-"
            ) as root_text:
                root = Path(root_text)
                repo = root / "repo"
                wrapper = copy_cli(repo)
                fake_bin = root / "bin"
                fake_bin.mkdir()
                handle = "cvmsp-" + str(index) * 24
                provision = write_verified_local_provision(repo, handle)
                receipt = valid_probe_success(handle, provision)
                exit_code = 0
                if failure == "image":
                    receipt["images"] = [dict(item) for item in receipt["images"]]
                    receipt["images"][1]["id"] = "sha256:" + "9" * 64
                else:
                    exit_code = 7
                ssh = fake_bin / "ssh"
                ssh.write_text(
                    "#!/usr/bin/env python3\n"
                    "import json\n"
                    f"print(json.dumps({receipt!r}))\n"
                    f"raise SystemExit({exit_code})\n",
                    encoding="utf-8",
                )
                ssh.chmod(0o755)

                result = subprocess.run(
                    [wrapper, "probe", handle],
                    cwd=repo,
                    env=cli_env(fake_bin),
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )

                self.assertNotEqual(result.returncode, 0)
                terminal = json.loads(
                    (
                        repo
                        / ".cvm-sidecar-probes"
                        / handle
                        / "probe.json"
                    ).read_text()
                )
                self.assertEqual(
                    terminal["errorCheck"], "remote-probe-receipt-binding"
                )

    def test_name_collision_never_removes_unowned_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cvm-sidecar-foreign-collision-") as root_text:
            root = Path(root_text)
            repo = root / "repo"
            wrapper = copy_cli(repo)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            docker_log = root / "docker.jsonl"
            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env python3
                    import json, os, pathlib, sys
                    log = pathlib.Path(os.environ["FAKE_DOCKER_LOG"])
                    argv = sys.argv[1:]
                    with log.open("a") as stream:
                        stream.write(json.dumps(argv) + "\\n")
                    if argv[:2] == ["network", "create"]:
                        raise SystemExit(1)  # deterministic foreign-name collision
                    if argv[:3] == ["container", "ls", "-a"] or argv[:2] == ["network", "ls"]:
                        raise SystemExit(0)
                    if argv[0] == "rm" or argv[:2] == ["network", "rm"]:
                        raise SystemExit(0)
                    raise SystemExit(f"unexpected docker argv: {argv}")
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)
            env = cli_env(fake_bin)
            env["FAKE_DOCKER_LOG"] = os.fspath(docker_log)
            handle = "cvmsp-" + "5" * 24
            state = repo / ".cvm-sidecar-probes" / handle
            state.mkdir(parents=True)
            workflow_files = {
                "module": hashlib.sha256(internal_remote_cli(repo).read_bytes()).hexdigest(),
                "wrapper": hashlib.sha256(wrapper.read_bytes()).hexdigest(),
            }
            (state / "provision.json").write_text(
                json.dumps(
                    {
                        "schema": "cvm-sidecar.provision-receipt/1",
                        "status": "provisioned",
                        "handle": handle,
                        "sourceRevision": SOURCE_REVISION,
                        "workflowFilesVerified": workflow_files,
                        "images": [
                            {"role": "sidecar", "id": SIDECAR_ID, "platform": "linux/amd64", "configSha256": SIDECAR_ID.removeprefix("sha256:")},
                            {"role": "client", "id": CLIENT_ID, "platform": "linux/amd64", "configSha256": CLIENT_ID.removeprefix("sha256:")},
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

            self.assertEqual(result.returncode, 1, result.stderr)
            calls = [json.loads(line) for line in docker_log.read_text().splitlines()]
            self.assertFalse(
                any(
                    argv[0] == "rm" or argv[:2] == ["network", "rm"]
                    for argv in calls
                ),
                "a failed create must not delete predictable foreign names",
            )


if __name__ == "__main__":
    unittest.main()
