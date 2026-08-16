"""Provider-free single-job Development supervisor for the fixed Cup route.

This is deliberately not a Formal/Sealed authority.  It consumes the fixed
SAI-005 entrypoint and gives the Development pilot one bounded outer lifecycle
without implementing admission, concurrency, paid dispatch, or verification.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import stat
import subprocess
import threading
import time
from typing import Callable, Mapping, Protocol
from urllib.parse import urlsplit

from .canonical_json import canonical_json_bytes, parse_canonical_json


FIXED_ENTRYPOINT = "/usr/local/libexec/text-to-cad-agent-entrypoint"
FIXTURE_ID = "cup_cup_033"
MAX_TIMEOUT_SECONDS = 45 * 60
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXED_CANDIDATE = {
    "imageManifestDigest": "sha256:a64ae96f4703bb8dfdbce1159106f606f1f00e1bf05991fa4bcabe27a0bfedc2",
    "imageConfigDigest": "sha256:a0c40ba26c78749808621bb838bd8eb3e7a1ba528c38eb8557c9d2ec0493dc60",
    "runtimeManifestDigest": "sha256:62114b5e0f8f0c79ce1f4c02c36bc7a2dacc37e8cc4130d0a2e316b8da04dbcf",
    "entrypointDigest": "sha256:65641cb6c91ee71930a35566a61aaf270ffe2315a13560d12cc4ad12797d6f5e",
}
_FIXED_SOURCE_DIGEST = "sha256:5646c3ccef1311f135da96617d06e4dabda908527449821711923fa0462ed115"
_FIXED_INPUT_DIGEST = "sha256:3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67"
IDENTITY_KEYS = (
    "agentImageManifestDigest",
    "runtimeManifestDigest",
    "executionSourceSnapshotDigest",
    "inputSnapshotDigest",
    "agentConfigDigest",
    "brokerAuthorityDigest",
    "workloadDigest",
    "jobId",
    "ownerNonce",
)
_WRITABLE_TARGETS = (
    "/run/text-to-cad-agent/home",
    "/run/text-to-cad-agent/cache",
    "/run/text-to-cad-agent/tmp",
    "/run/text-to-cad-agent/work",
    "/run/text-to-cad-agent/output",
)
_BROKER_HELPER_SOURCE = b'''#!/usr/bin/python3.12
import hashlib
import hmac
import json
import os
import socket
import sys

def canonical(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")

config_line = sys.stdin.buffer.readline(65537)
if not config_line.endswith(b"\\n") or len(config_line) > 65536:
    raise SystemExit(125)
config = json.loads(config_line)
control = config["control"]
secret = bytes.fromhex(config["secretHex"])
path = "/run/meshshot-browser/browser.sock"
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(path)
os.chmod(path, 0o777)
server.listen(1)
sys.stdout.buffer.write(b"READY\\n")
sys.stdout.buffer.flush()
connection, _ = server.accept()
with connection:
    line = connection.makefile("rb").readline(16385)
    if not line.endswith(b"\\n") or len(line) > 16384:
        raise SystemExit(125)
    challenge = json.loads(line)
    identity_keys = (
        "agentImageManifestDigest", "runtimeManifestDigest",
        "executionSourceSnapshotDigest", "inputSnapshotDigest",
        "agentConfigDigest", "brokerAuthorityDigest", "workloadDigest",
        "jobId", "ownerNonce",
    )
    expected = {
        "schema": "text-to-cad.agent-broker-challenge/1",
        "challenge": control["challenge"],
        **{key: control[key] for key in identity_keys},
    }
    if challenge != expected or canonical(challenge) != line[:-1]:
        raise SystemExit(125)
    proof = {
        "schema": "text-to-cad.agent-broker-proof/1",
        "challenge": challenge["challenge"],
        "brokerMac": hmac.new(secret, canonical(challenge), hashlib.sha256).hexdigest(),
        **{key: challenge[key] for key in identity_keys},
    }
    connection.sendall(canonical(proof) + b"\\n")
server.close()
'''


class SupervisorError(RuntimeError):
    """The Development execution failed closed."""


class AttachError(SupervisorError):
    """A safe attach-stage failure with retained process output."""

    def __init__(self, stage: str, cause: str, status: int, stdout: bytes, stderr: bytes) -> None:
        super().__init__(f"{stage}:{cause}")
        self.stage = stage
        self.status = status
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class Mount:
    source: Path | str
    target: str
    read_only: bool
    kind: str = "bind"


@dataclass(frozen=True)
class ContainerSpec:
    image_id: str
    name: str
    entrypoint: str
    command: tuple[str, ...]
    user: str
    read_only_root: bool
    network_mode: str
    mounts: tuple[Mount, ...]
    labels: Mapping[str, str]


@dataclass(frozen=True)
class ContainerObservation:
    container_id: str
    image_id: str
    entrypoint: str
    command: tuple[str, ...]
    user: str
    read_only_root: bool
    network_mode: str
    mounts: tuple[Mount, ...]
    labels: Mapping[str, str]

    @classmethod
    def from_spec(cls, container_id: str, spec: ContainerSpec) -> "ContainerObservation":
        return cls(
            container_id=container_id,
            image_id=spec.image_id,
            entrypoint=spec.entrypoint,
            command=spec.command,
            user=spec.user,
            read_only_root=spec.read_only_root,
            network_mode=spec.network_mode,
            mounts=spec.mounts,
            labels=dict(spec.labels),
        )


@dataclass(frozen=True)
class AttachedResult:
    status: int
    stdout: bytes
    stderr: bytes


class Engine(Protocol):
    def create(self, spec: ContainerSpec) -> str: ...
    def inspect(self, container_id: str) -> ContainerObservation: ...
    def exchange(
        self,
        container_id: str,
        release_for_preflight: Callable[[bytes], bytes],
        timeout_seconds: int,
    ) -> AttachedResult: ...
    def terminate(self, container_id: str) -> None: ...
    def remove(self, container_id: str) -> None: ...
    def container_absent(self, container_id: str) -> bool: ...
    def owner_absent(self, owner_nonce: str) -> bool: ...


@dataclass(frozen=True)
class DevelopmentRequest:
    image_id: str
    image_manifest_digest: str
    image_config_digest: str
    runtime_manifest_digest: str
    entrypoint_digest: str
    source_dir: Path
    input_dir: Path
    output_dir: Path
    workload: tuple[str, ...]
    internal_network: str | None = None
    proxy_base_url: str | None = None
    proxy_client_token: str | None = None
    broker_parent: Path = Path("/tmp")
    timeout_seconds: int = MAX_TIMEOUT_SECONDS


def fixed_candidate_request(
    *,
    repo_root: Path,
    image_id: str,
    output_dir: Path,
    workload_path: Path,
    source_dir: Path | None = None,
    input_dir: Path | None = None,
    internal_network: str | None = None,
    proxy_base_url: str | None = None,
    proxy_client_token: str | None = None,
    broker_parent: Path = Path("/tmp"),
    timeout_seconds: int = MAX_TIMEOUT_SECONDS,
) -> DevelopmentRequest:
    """Build the one allowed acbafef8 Development request from local evidence."""

    receipt_root = (
        repo_root
        / "models/agent-runtime/cup_cup_033/agent-runtime-development/change-request-1"
    )
    try:
        build = json.loads((receipt_root / "build-receipt.json").read_bytes())
        smoke = json.loads((receipt_root / "colima-smoke-receipt.json").read_bytes())
    except (OSError, ValueError) as error:
        raise SupervisorError("fixed acbafef8 candidate receipts are unavailable") from error
    observed = {
        "imageManifestDigest": build.get("build", {}).get("manifestDigest"),
        "imageConfigDigest": build.get("build", {}).get("configDigest"),
        "runtimeManifestDigest": build.get("build", {}).get("runtimeManifestDigest"),
        "entrypointDigest": smoke.get("entrypointDigest"),
    }
    if observed != FIXED_CANDIDATE:
        raise SupervisorError("fixed acbafef8 candidate identity drifted")
    try:
        workload_bytes = workload_path.read_bytes()
        workload = _plain(parse_canonical_json(workload_bytes))
    except (OSError, AttributeError, ValueError) as error:
        raise SupervisorError("workload is not canonical JSON") from error
    if canonical_json_bytes(workload) != workload_bytes or not isinstance(workload, list):
        raise SupervisorError("workload is not one canonical argv array")
    return DevelopmentRequest(
        image_id=image_id,
        image_manifest_digest=FIXED_CANDIDATE["imageManifestDigest"],
        image_config_digest=FIXED_CANDIDATE["imageConfigDigest"],
        runtime_manifest_digest=FIXED_CANDIDATE["runtimeManifestDigest"],
        entrypoint_digest=FIXED_CANDIDATE["entrypointDigest"],
        source_dir=source_dir or repo_root / "models/agent-runtime/cup_cup_033/source",
        input_dir=input_dir or repo_root / "models/agent-runtime/cup_cup_033/input",
        output_dir=output_dir,
        workload=tuple(workload),
        internal_network=internal_network,
        proxy_base_url=proxy_base_url,
        proxy_client_token=proxy_client_token,
        broker_parent=broker_parent,
        timeout_seconds=timeout_seconds,
    )


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _parse_line(payload: bytes, label: str) -> dict[str, object]:
    if not payload or payload.endswith(b"\n") or len(payload) > 16384:
        raise SupervisorError(f"{label} is not one bounded canonical record")
    try:
        value = _plain(parse_canonical_json(payload))
    except (AttributeError, ValueError) as error:
        raise SupervisorError(f"{label} is not canonical JSON") from error
    if not isinstance(value, dict) or canonical_json_bytes(value) != payload:
        raise SupervisorError(f"{label} is not one canonical object")
    return value


def _full_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _tree_digest(root: Path) -> str:
    if not root.is_dir() or root.is_symlink():
        raise SupervisorError("fixed Cup source/input root is not one directory")
    records: list[dict[str, object]] = []
    for candidate in sorted(root.rglob("*")):
        relative = candidate.relative_to(root).as_posix()
        info = candidate.lstat()
        if stat.S_ISDIR(info.st_mode):
            records.append({"kind": "directory", "mode": info.st_mode & 0o777, "path": relative})
        elif stat.S_ISREG(info.st_mode):
            payload = candidate.read_bytes()
            after = candidate.lstat()
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                raise SupervisorError("fixed Cup source/input changed during digest")
            records.append({
                "bytes": len(payload),
                "digest": _digest(payload),
                "kind": "regular",
                "mode": info.st_mode & 0o777,
                "path": relative,
            })
        else:
            raise SupervisorError("fixed Cup source/input contains a link or special entry")
    return _digest(canonical_json_bytes(records))


def _publish_exclusive(path: Path, value: object | bytes, *, mode: int = 0o400) -> None:
    payload = value if isinstance(value, bytes) else canonical_json_bytes(value)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class _BrokerMock:
    def __init__(self, root: Path, control: Mapping[str, object], secret: bytes) -> None:
        self.root = root
        self.control = control
        self.secret = secret
        self.path = root / "browser.sock"
        self.error: BaseException | None = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="development-broker-mock", daemon=True)

    def __enter__(self) -> "_BrokerMock":
        self.root.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.root.chmod(0o777)
        self._thread.start()
        if not self._ready.wait(2.0):
            raise SupervisorError("Broker mock did not become ready")
        if self.error is not None:
            raise SupervisorError("Broker mock failed before readiness") from self.error
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        self._stop.set()
        self._thread.join(2.0)
        if self._thread.is_alive() and self.error is None:
            self.error = SupervisorError("Broker mock did not terminate")
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def _serve(self) -> None:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(str(self.path))
                self.path.chmod(0o777)
                server.listen(1)
                server.settimeout(0.1)
                self._ready.set()
                while True:
                    try:
                        connection, _ = server.accept()
                        break
                    except TimeoutError:
                        if self._stop.is_set():
                            return
                with connection:
                    connection.settimeout(10.0)
                    line = connection.makefile("rb").readline(16385)
                    if not line.endswith(b"\n") or len(line) > 16384:
                        raise SupervisorError("Broker challenge is unavailable")
                    challenge = _parse_line(line[:-1], "Broker challenge")
                    expected = {
                        "schema": "text-to-cad.agent-broker-challenge/1",
                        "challenge": self.control["challenge"],
                        **{key: self.control[key] for key in IDENTITY_KEYS},
                    }
                    if challenge != expected:
                        raise SupervisorError("Broker challenge identity is wrong")
                    mac = hmac.new(self.secret, canonical_json_bytes(challenge), hashlib.sha256).hexdigest()
                    proof = {
                        "schema": "text-to-cad.agent-broker-proof/1",
                        "challenge": challenge["challenge"],
                        "brokerMac": mac,
                        **{key: challenge[key] for key in IDENTITY_KEYS},
                    }
                    connection.sendall(canonical_json_bytes(proof) + b"\n")
        except BaseException as error:
            self.error = error
            self._ready.set()


def _readline_with_timeout(stream: object, timeout: float) -> bytes:
    holder: dict[str, object] = {}

    def reader() -> None:
        try:
            holder["line"] = stream.readline(16385)
        except BaseException as error:
            holder["error"] = error

    thread = threading.Thread(target=reader, name="development-broker-ready", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("Broker helper readiness timed out")
    if "error" in holder:
        raise SupervisorError("Broker helper readiness read failed") from holder["error"]  # type: ignore[misc]
    return holder.get("line", b"")  # type: ignore[return-value]


class _DockerBrokerMock:
    """Colima-local provider-free Broker sharing one job-private volume."""

    def __init__(
        self,
        engine: "DockerEngine",
        image_id: str,
        helper_path: Path,
        job_id: str,
        owner_nonce: str,
        control: Mapping[str, object],
        secret: bytes,
    ) -> None:
        suffix = job_id.removeprefix("development-")
        self.engine = engine
        self.image_id = image_id
        self.helper_path = helper_path
        self.control = control
        self.secret = secret
        self.volume_name = f"t2c-broker-{suffix}"
        self.container_name = f"t2c-broker-helper-{suffix}"
        self.owner_nonce = owner_nonce
        self.process: subprocess.Popen[bytes] | None = None
        self.error: BaseException | None = None
        self.absent = False

    @property
    def mount(self) -> Mount:
        return Mount(self.volume_name, "/run/meshshot-browser", False, "volume")

    def __enter__(self) -> "_DockerBrokerMock":
        created = self.engine._run(
            "volume", "create",
            "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
            self.volume_name,
        )
        if created.stdout.decode("ascii").strip() != self.volume_name:
            raise SupervisorError("Broker volume creation identity is wrong")
        try:
            return self._start()
        except BaseException:
            self._cleanup()
            raise

    def _start(self) -> "_DockerBrokerMock":
        self.process = subprocess.Popen(
            self.engine.command(
                "run", "--interactive", "--name", self.container_name,
                "--pull", "never", "--read-only", "--user", "0:0",
                "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
                "--mount", f"type=volume,src={self.volume_name},dst=/run/meshshot-browser",
                "--mount", f"type=bind,src={self.helper_path},dst=/broker.py,readonly",
                "--entrypoint", "/usr/bin/python3.12", self.image_id, "/broker.py",
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise SupervisorError("Broker helper pipes are unavailable")
        config = {"control": dict(self.control), "secretHex": self.secret.hex()}
        self.process.stdin.write(canonical_json_bytes(config) + b"\n")
        self.process.stdin.close()
        if _readline_with_timeout(self.process.stdout, 10.0) != b"READY\n":
            raise SupervisorError("Broker helper did not become ready")
        return self

    def __exit__(self, _kind, _value, _traceback) -> None:
        if self.process is not None:
            try:
                self.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                self.engine._run("kill", "--signal", "TERM", self.container_name, check=False)
                try:
                    self.process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.engine._run("kill", "--signal", "KILL", self.container_name, check=False)
                    self.process.wait()
            if self.process.returncode not in (0, None) and _kind is None:
                self.error = SupervisorError("Broker helper exited nonzero")
        self._cleanup()

    def _cleanup(self) -> None:
        self.engine._run("rm", "--force", self.container_name, check=False)
        self.engine._run("volume", "rm", "--force", self.volume_name, check=False)
        container_absent = self.engine._run("inspect", self.container_name, check=False).returncode != 0
        volume_absent = self.engine._run("volume", "inspect", self.volume_name, check=False).returncode != 0
        self.absent = container_absent and volume_absent


class _DockerWritableVolumes:
    """Colima-local writable roots that preserve Agent uid ownership."""

    def __init__(
        self,
        engine: "DockerEngine",
        image_id: str,
        job_id: str,
        owner_nonce: str,
        proxy_config: bytes | None,
    ) -> None:
        suffix = job_id.removeprefix("development-")
        self.engine = engine
        self.image_id = image_id
        self.owner_nonce = owner_nonce
        self.proxy_config = proxy_config
        self.names = {
            target: f"t2c-job-{suffix}-{target.rsplit('/', 1)[-1]}"
            for target in _WRITABLE_TARGETS
        }
        self.absent = False

    @property
    def mounts(self) -> tuple[Mount, ...]:
        return tuple(
            Mount(self.names[target], target, False, "volume")
            for target in _WRITABLE_TARGETS
        )

    def prepare(self) -> None:
        created: list[str] = []
        try:
            for name in self.names.values():
                result = self.engine._run(
                    "volume", "create",
                    "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
                    name,
                )
                if result.stdout.decode("ascii").strip() != name:
                    raise SupervisorError("job-private volume creation identity is wrong")
                created.append(name)
            args = [
                "run", "--rm", "--pull", "never", "--read-only",
                "--user", "0:0", "--network", "none", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges",
                "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
            ]
            roots: list[str] = []
            for index, name in enumerate(self.names.values()):
                target = f"/job-root/{index}"
                args += ["--mount", f"type=volume,src={name},dst={target}"]
                roots.append(target)
            args += ["--entrypoint", "/usr/bin/chmod", self.image_id, "0777", *roots]
            self.engine._run(*args)
            if self.proxy_config is not None:
                home_volume = self.names["/run/text-to-cad-agent/home"]
                self.engine._run(
                    "run", "--rm", "--pull", "never", "--read-only",
                    "--user", "0:0", "--network", "none", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
                    "--mount", f"type=volume,src={home_volume},dst=/home",
                    "--entrypoint", "/usr/bin/mkdir", self.image_id,
                    "--mode=0777", "/home/.codex",
                )
                seed = (
                    "import pathlib, sys; "
                    "p=pathlib.Path('/home/.codex'); "
                    "f=p/'config.toml'; f.write_bytes(sys.stdin.buffer.read()); "
                    "f.chmod(0o600)"
                )
                self.engine._run(
                    "run", "--rm", "--interactive", "--pull", "never", "--read-only",
                    "--user", "65532:65532", "--network", "none", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
                    "--mount", f"type=volume,src={home_volume},dst=/home",
                    "--entrypoint", "/usr/bin/python3.12", self.image_id, "-c", seed,
                    input_bytes=self.proxy_config,
                )
                self.engine._run(
                    "run", "--rm", "--pull", "never", "--read-only",
                    "--user", "0:0", "--network", "none", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges",
                    "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
                    "--mount", f"type=volume,src={home_volume},dst=/home",
                    "--entrypoint", "/usr/bin/chmod", self.image_id,
                    "0700", "/home/.codex",
                )
        except BaseException:
            for name in created:
                self.engine._run("volume", "rm", "--force", name, check=False)
            raise

    def copy_output(self, container_id: str, destination: Path) -> None:
        self.engine._run(
            "cp", f"{container_id}:/run/text-to-cad-agent/output/.", str(destination)
        )

    def restore_root_modes_after_container_create(self) -> None:
        """Undo Docker volume copy-up modes before the Agent is started."""

        args = [
            "run", "--rm", "--pull", "never", "--read-only",
            "--user", "0:0", "--network", "none", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--label", f"org.text-to-cad.owner-nonce={self.owner_nonce}",
        ]
        roots: list[str] = []
        for index, name in enumerate(self.names.values()):
            target = f"/job-root/{index}"
            args += ["--mount", f"type=volume,src={name},dst={target}"]
            roots.append(target)
        args += ["--entrypoint", "/usr/bin/chmod", self.image_id, "0777", *roots]
        self.engine._run(*args)

    def cleanup(self) -> None:
        for name in self.names.values():
            self.engine._run("volume", "rm", "--force", name, check=False)
        self.absent = all(
            self.engine._run("volume", "inspect", name, check=False).returncode != 0
            for name in self.names.values()
        )


def _validate_request(request: DevelopmentRequest) -> None:
    for value in (
        request.image_id,
        request.image_manifest_digest,
        request.image_config_digest,
        request.runtime_manifest_digest,
        request.entrypoint_digest,
    ):
        if not _full_digest(value):
            raise SupervisorError("Development image identity is not a full digest")
    if not 0 < request.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise SupervisorError("timeout must be in (0, 2700]")
    if (
        not request.workload
        or len(request.workload) > 256
        or not request.workload[0].startswith("/")
        or any(
            not isinstance(item, str) or not item or not item.isascii()
            for item in request.workload
        )
    ):
        raise SupervisorError("outer-owned workload argv is invalid")
    if not (request.source_dir / f"{FIXTURE_ID}.implicit.js").is_file():
        raise SupervisorError("fixed cup_cup_033 source is absent")
    if not (request.input_dir / f"{FIXTURE_ID}.ply").is_file():
        raise SupervisorError("fixed cup_cup_033 input is absent")
    if _digest((request.source_dir / f"{FIXTURE_ID}.implicit.js").read_bytes()) != _FIXED_SOURCE_DIGEST:
        raise SupervisorError("fixed cup_cup_033 source identity is wrong")
    if _digest((request.input_dir / f"{FIXTURE_ID}.ply").read_bytes()) != _FIXED_INPUT_DIGEST:
        raise SupervisorError("fixed cup_cup_033 input identity is wrong")
    if request.internal_network is not None and (
        not request.internal_network
        or len(request.internal_network) > 128
        or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in request.internal_network)
    ):
        raise SupervisorError("pre-created internal network name is invalid")
    if (request.proxy_base_url is None) != (request.proxy_client_token is None):
        raise SupervisorError("proxy URL and client capability must be supplied together")
    if request.proxy_base_url is not None:
        if request.internal_network is None:
            raise SupervisorError("proxy capability requires one internal network")
        parsed = urlsplit(request.proxy_base_url)
        if (
            parsed.scheme != "http"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path.rstrip("/") != "/v1"
            or parsed.hostname == "v2.open.venus.oa.com"
        ):
            raise SupervisorError("proxy base URL is not one internal Responses endpoint")
        token = request.proxy_client_token
        if token is None or not 32 <= len(token) <= 4096 or "\n" in token or "\r" in token:
            raise SupervisorError("proxy client capability is invalid")
    broker_parent = request.broker_parent.resolve()
    if not broker_parent.is_dir() or broker_parent.is_symlink():
        raise SupervisorError("Broker parent must be one existing host-visible directory")
    if len(os.fsencode(broker_parent / ("t2c-" + "0" * 24) / "browser.sock")) >= 100:
        raise SupervisorError("Broker socket path is too long for Darwin AF_UNIX")
    if not request.output_dir.is_dir() or request.output_dir.is_symlink():
        raise SupervisorError("Development output root must already exist")
    if any(request.output_dir.iterdir()):
        raise SupervisorError("Development output root must be fresh and empty")


def _same_observation(actual: ContainerObservation, expected: ContainerSpec, container_id: str) -> bool:
    return (
        actual.container_id == container_id
        and actual.image_id == expected.image_id
        and actual.entrypoint == expected.entrypoint
        and actual.command == expected.command
        and actual.user == expected.user
        and actual.read_only_root == expected.read_only_root
        and actual.network_mode == expected.network_mode
        and set(actual.mounts) == set(expected.mounts)
        and all(actual.labels.get(key) == value for key, value in expected.labels.items())
    )


def execute(
    request: DevelopmentRequest,
    *,
    engine: Engine,
    broker_factory: Callable[[Path, Mapping[str, object], bytes], object] = _BrokerMock,
) -> dict[str, object]:
    """Execute exactly one provider-free Development job; never retries it."""

    _validate_request(request)
    source_digest = _tree_digest(request.source_dir)
    input_digest = _tree_digest(request.input_dir)
    job_id = f"development-{secrets.token_hex(12)}"
    owner_nonce = secrets.token_hex(32)
    challenge = secrets.token_hex(32)
    broker_secret = secrets.token_bytes(32)
    broker_authority_digest = _digest(broker_secret)
    workload = list(request.workload)
    proxy_config: bytes | None = None
    proxy_capability = "absent"
    execution_mode = "development-provider-free"
    if request.proxy_base_url is not None and request.proxy_client_token is not None:
        proxy_capability = "job-private-one-shot"
        execution_mode = "development-venus-proxy"
        proxy_config = (
            'model_provider = "venus"\n'
            '[model_providers.venus]\n'
            'name = "Venus GPT-5.6 Sol Development Proxy"\n'
            f"base_url = {json.dumps(request.proxy_base_url)}\n"
            'wire_api = "responses"\n'
            f"experimental_bearer_token = {json.dumps(request.proxy_client_token)}\n"
        ).encode("utf-8")
    identity: dict[str, object] = {
        "agentImageManifestDigest": request.image_manifest_digest,
        "runtimeManifestDigest": request.runtime_manifest_digest,
        "executionSourceSnapshotDigest": source_digest,
        "inputSnapshotDigest": input_digest,
        "agentConfigDigest": _digest(canonical_json_bytes({
            "fixtureId": FIXTURE_ID,
            "mode": execution_mode,
            "proxyCapability": proxy_capability,
            "proxyBaseUrl": request.proxy_base_url,
            "agentNetwork": request.internal_network or "none",
            "timeoutSeconds": request.timeout_seconds,
        })),
        "brokerAuthorityDigest": broker_authority_digest,
        "workloadDigest": _digest(canonical_json_bytes(workload)),
        "jobId": job_id,
        "ownerNonce": owner_nonce,
    }
    control = {
        "schema": "text-to-cad.agent-entrypoint-control/1",
        "challenge": challenge,
        "workload": workload,
        **identity,
    }
    supervisor_root = request.output_dir / "supervisor"
    private_root = supervisor_root / "private"
    control_root = private_root / "control"
    control_root.mkdir(mode=0o700, parents=True)
    _publish_exclusive(control_root / "manifest.json", control, mode=0o444)
    helper_path = control_root / "broker-helper.py"
    _publish_exclusive(helper_path, _BROKER_HELPER_SOURCE, mode=0o444)
    control_root.chmod(0o555)
    writable_sources: dict[str, Path] = {}
    artifact_root = request.output_dir / "artifacts"
    artifact_root.mkdir(mode=0o700)
    docker_writable: _DockerWritableVolumes | None = None
    if isinstance(engine, DockerEngine):
        docker_writable = _DockerWritableVolumes(
            engine, request.image_id, job_id, owner_nonce, proxy_config
        )
        writable_mounts = docker_writable.mounts
    else:
        for target in _WRITABLE_TARGETS[:-1]:
            source = private_root / target.rsplit("/", 1)[-1]
            source.mkdir(mode=0o700)
            source.chmod(0o777)
            writable_sources[target] = source
        if proxy_config is not None:
            codex_home = writable_sources["/run/text-to-cad-agent/home"] / ".codex"
            codex_home.mkdir(mode=0o700)
            _publish_exclusive(codex_home / "config.toml", proxy_config, mode=0o600)
        artifact_root.chmod(0o777)
        writable_sources[_WRITABLE_TARGETS[-1]] = artifact_root
        writable_mounts = tuple(
            Mount(writable_sources[target], target, False)
            for target in _WRITABLE_TARGETS
        )
    # Darwin's AF_UNIX path ceiling is short.  Colima callers must select a
    # short /Users parent shared with its VM; unit callers may use /tmp.
    broker_root = request.broker_parent.resolve() / f"t2c-{job_id.removeprefix('development-')}"
    if broker_factory is _BrokerMock and isinstance(engine, DockerEngine):
        broker_context: object = _DockerBrokerMock(
            engine, request.image_id, helper_path, job_id, owner_nonce,
            control, broker_secret,
        )
        broker_mount = broker_context.mount  # type: ignore[attr-defined]
    else:
        broker_context = broker_factory(broker_root, control, broker_secret)
        broker_mount = Mount(broker_root, "/run/meshshot-browser", False)
    mounts = (
        Mount(control_root, "/run/text-to-cad-agent/control", True),
        Mount(request.source_dir.resolve(), "/run/text-to-cad-agent/source", True),
        Mount(request.input_dir.resolve(), "/run/text-to-cad-agent/input", True),
        *writable_mounts,
        broker_mount,
    )
    labels = {
        "org.text-to-cad.development": "true",
        "org.text-to-cad.fixture": FIXTURE_ID,
        "org.text-to-cad.job-id": job_id,
        "org.text-to-cad.owner-nonce": owner_nonce,
    }
    spec = ContainerSpec(
        image_id=request.image_id,
        name=f"text-to-cad-{job_id}",
        entrypoint=FIXED_ENTRYPOINT,
        command=(),
        user="65532:65532",
        read_only_root=True,
        network_mode=request.internal_network or "none",
        mounts=mounts,
        labels=labels,
    )
    container_id: str | None = None
    result: AttachedResult | None = None
    failure_check: str | None = None
    failure_message: str | None = None
    broker_proof_digest: str | None = None
    container_absent = True
    owner_absent = True
    private_tree_absent = False
    broker_tree_absent = False
    writable_volumes_absent = docker_writable is None
    adapter_stage = "broker-start"
    try:
        if docker_writable is not None:
            adapter_stage = "job-volume-prepare"
            docker_writable.prepare()
        adapter_stage = "broker-start"
        with broker_context as broker:
            adapter_stage = "container-create"
            container_id = engine.create(spec)
            if docker_writable is not None:
                adapter_stage = "job-volume-post-create-modes"
                docker_writable.restore_root_modes_after_container_create()
            adapter_stage = "container-inspect"
            if not container_id or not _same_observation(engine.inspect(container_id), spec, container_id):
                failure_check = "inert-container"
                raise SupervisorError("inert container observation is not exact")

            def release_for_preflight(payload: bytes) -> bytes:
                nonlocal broker_proof_digest
                preflight = _parse_line(payload, "entrypoint preflight")
                if set(preflight) != {"schema", "brokerProof", "brokerProofDigest"} or preflight.get("schema") != "text-to-cad.agent-entrypoint-preflight/1":
                    raise SupervisorError("entrypoint preflight schema is not closed")
                proof = preflight["brokerProof"]
                expected_proof = {
                    "schema": "text-to-cad.agent-broker-proof/1",
                    "challenge": challenge,
                    "brokerMac": hmac.new(
                        broker_secret,
                        canonical_json_bytes({
                            "schema": "text-to-cad.agent-broker-challenge/1",
                            "challenge": challenge,
                            **identity,
                        }),
                        hashlib.sha256,
                    ).hexdigest(),
                    **identity,
                }
                if proof != expected_proof:
                    raise SupervisorError("Broker proof is not identity-bound")
                broker_proof_digest = _digest(canonical_json_bytes(proof))
                if preflight["brokerProofDigest"] != broker_proof_digest:
                    raise SupervisorError("Broker proof digest is wrong")
                return canonical_json_bytes({
                    "schema": "text-to-cad.agent-entrypoint-release/1",
                    "brokerProofDigest": broker_proof_digest,
                    "release": True,
                }) + b"\n"

            adapter_stage = "container-exchange"
            result = engine.exchange(container_id, release_for_preflight, request.timeout_seconds)
            adapter_stage = "broker-terminal"
            if broker.error is not None:
                raise SupervisorError("Broker mock failed") from broker.error
        if broker_context.error is not None:  # type: ignore[attr-defined]
            raise SupervisorError("Broker mock failed") from broker_context.error  # type: ignore[attr-defined]
    except TimeoutError as error:
        failure_check = "timeout"
        failure_message = str(error)
        if container_id is not None:
            try:
                engine.terminate(container_id)
            except (OSError, subprocess.SubprocessError, ValueError):
                failure_check = "cleanup-absence"
                failure_message = "container termination did not complete"
    except SupervisorError as error:
        failure_check = failure_check or "execution"
        failure_message = str(error)
        if isinstance(error, AttachError):
            result = AttachedResult(error.status, error.stdout, error.stderr)
        if container_id is not None:
            try:
                engine.terminate(container_id)
            except (OSError, subprocess.SubprocessError, ValueError):
                failure_check = "cleanup-absence"
                failure_message = "container termination did not complete"
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        failure_check = "execution-adapter"
        failure_message = f"{adapter_stage}:{type(error).__name__}"
        if container_id is not None:
            try:
                engine.terminate(container_id)
            except (OSError, subprocess.SubprocessError, ValueError):
                failure_check = "cleanup-absence"
                failure_message = "container termination did not complete"
    finally:
        if container_id is not None:
            if docker_writable is not None:
                try:
                    docker_writable.copy_output(container_id, artifact_root)
                except (OSError, subprocess.SubprocessError, SupervisorError, ValueError):
                    failure_check = failure_check or "output-copy"
                    failure_message = failure_message or "job output copy did not complete"
            try:
                engine.remove(container_id)
                container_absent = engine.container_absent(container_id)
                owner_absent = engine.owner_absent(owner_nonce)
            except (OSError, subprocess.SubprocessError, SupervisorError, ValueError):
                container_absent = False
                owner_absent = False
        if isinstance(broker_context, _DockerBrokerMock) and not broker_context.absent:
            broker_context._cleanup()
        if docker_writable is not None:
            docker_writable.cleanup()
            writable_volumes_absent = docker_writable.absent
        try:
            control_root.chmod(0o700)
        except FileNotFoundError:
            pass
        try:
            shutil.rmtree(private_root)
        except FileNotFoundError:
            pass
        except OSError:
            pass
        private_tree_absent = not private_root.exists()
        if isinstance(broker_context, _DockerBrokerMock):
            broker_tree_absent = broker_context.absent
        else:
            try:
                shutil.rmtree(broker_root)
            except FileNotFoundError:
                pass
            except OSError:
                pass
            broker_tree_absent = not broker_root.exists()

    _publish_exclusive(
        supervisor_root / "entrypoint.stdout.jsonl",
        result.stdout if result is not None else b"",
    )
    _publish_exclusive(
        supervisor_root / "entrypoint.stderr",
        result.stderr if result is not None else b"",
    )
    if result is not None:
        lines = result.stdout.splitlines()
        if len(lines) != 2:
            failure_check = failure_check or "terminal-publication"
            failure_message = failure_message or "entrypoint stdout is not preflight plus terminal"
        else:
            terminal = _parse_line(lines[1], "entrypoint terminal")
            expected_keys = {
                "schema", "workloadStatus", "outputDigest", "processGroupAbsent",
                "descendantResidue", "interruptedSignal", *IDENTITY_KEYS,
            }
            if (
                set(terminal) != expected_keys
                or terminal.get("schema") != "text-to-cad.agent-entrypoint-terminal/1"
                or any(terminal.get(key) != identity[key] for key in IDENTITY_KEYS)
                or terminal.get("workloadStatus") != result.status
                or terminal.get("processGroupAbsent") is not True
                or terminal.get("descendantResidue") is not False
                or not _full_digest(terminal.get("outputDigest"))
            ):
                failure_check = failure_check or "terminal-publication"
                failure_message = failure_message or "entrypoint terminal record is not exact"
            elif result.status != 0:
                failure_check = failure_check or "workload-terminal"
                failure_message = failure_message or f"workload exited {result.status}"
    if not container_absent or not owner_absent:
        failure_check = "cleanup-absence"
        failure_message = "container or owner-label absence is not proven"
    if not private_tree_absent or not broker_tree_absent:
        failure_check = "cleanup-absence"
        failure_message = "job-private tree or Broker socket tree absence is not proven"
    if not writable_volumes_absent:
        failure_check = "cleanup-absence"
        failure_message = "job-private writable volume absence is not proven"

    receipt: dict[str, object] = {
        "schema": "text-to-cad.agent-runtime-development-supervisor-terminal/1",
        "status": "development-failed" if failure_check else "development-succeeded",
        "fixtureId": FIXTURE_ID,
        "classification": "Development/Not Sealed/Not Formal",
        "attemptCount": 1,
        "executionMode": execution_mode,
        "providerDispatchCount": 0 if proxy_config is None else None,
        "providerAccounting": (
            "supervisor-zero-provider-capability"
            if proxy_config is None
            else "external-job-private-proxy-ledger"
        ),
        "proxyCapability": proxy_capability,
        "timeoutSeconds": request.timeout_seconds,
        "containerId": container_id,
        "containerAbsent": container_absent,
        "ownerLabelsAbsent": owner_absent,
        "jobPrivateTreeAbsent": private_tree_absent,
        "brokerSocketTreeAbsent": broker_tree_absent,
        "brokerVolumeAbsent": broker_tree_absent,
        "writableVolumesAbsent": writable_volumes_absent,
        "brokerProofDigest": broker_proof_digest,
        "imageId": request.image_id,
        "imageConfigDigest": request.image_config_digest,
        "entrypointDigest": request.entrypoint_digest,
        **identity,
        "failureCheck": failure_check,
        "failureReason": failure_message,
    }
    _publish_exclusive(supervisor_root / "terminal.json", receipt)
    if failure_check:
        raise SupervisorError(f"{failure_check}: {failure_message}")
    return receipt


class DockerEngine:
    """Docker-compatible adapter reserved for the Colima Development runner."""

    def __init__(self, executable: str = "docker", *, context: str | None = None) -> None:
        self.executable = executable
        self.context = context

    def command(self, *args: str) -> list[str]:
        prefix = [self.executable]
        if self.context is not None:
            prefix += ["--context", self.context]
        return [*prefix, *args]

    def _run(
        self,
        *args: str,
        check: bool = True,
        input_bytes: bytes | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            self.command(*args), check=check,
            stdin=subprocess.DEVNULL if input_bytes is None else None,
            input=input_bytes,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def create(self, spec: ContainerSpec) -> str:
        args = ["create", "--interactive", "--name", spec.name, "--pull", "never"]
        if spec.read_only_root:
            args.append("--read-only")
        args += ["--user", spec.user, "--network", spec.network_mode, "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]
        for key, value in sorted(spec.labels.items()):
            args += ["--label", f"{key}={value}"]
        for mount in spec.mounts:
            value = f"type={mount.kind},src={mount.source},dst={mount.target}"
            if mount.read_only:
                value += ",readonly"
            args += ["--mount", value]
        args += ["--entrypoint", spec.entrypoint, spec.image_id]
        completed = self._run(*args)
        return completed.stdout.decode("ascii").strip()

    def inspect(self, container_id: str) -> ContainerObservation:
        completed = self._run("inspect", container_id)
        value = json.loads(completed.stdout)[0]
        mounts = tuple(
            Mount(
                item["Name"] if item["Type"] == "volume" else Path(item["Source"]),
                item["Destination"],
                not item["RW"],
                item["Type"],
            )
            for item in value["Mounts"]
        )
        return ContainerObservation(
            container_id=value["Id"], image_id=value["Image"],
            entrypoint=value["Config"]["Entrypoint"][0],
            command=tuple(value["Config"].get("Cmd") or ()), user=value["Config"]["User"],
            read_only_root=value["HostConfig"]["ReadonlyRootfs"],
            network_mode=value["HostConfig"]["NetworkMode"], mounts=mounts,
            labels=value["Config"].get("Labels") or {},
        )

    def exchange(self, container_id, release_for_preflight, timeout_seconds):
        process = subprocess.Popen(
            self.command("start", "--attach", "--interactive", container_id),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            stdout, stderr = _interactive_exchange(process, release_for_preflight, timeout_seconds)
        except BaseException:
            if process.poll() is not None:
                raise
            try:
                os.killpg(process.pid, 15)
                process.wait(timeout=2)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, 9)
                except ProcessLookupError:
                    pass
                process.wait()
            raise
        return AttachedResult(process.returncode, stdout, stderr)

    def terminate(self, container_id: str) -> None:
        self._run("kill", "--signal", "TERM", container_id, check=False)
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            observed = self._run(
                "inspect", "--format", "{{.State.Running}}", container_id,
                check=False,
            )
            if observed.returncode != 0 or observed.stdout.strip() == b"false":
                return
            time.sleep(0.1)
        self._run("kill", "--signal", "KILL", container_id, check=False)

    def remove(self, container_id: str) -> None:
        completed = self._run("rm", container_id, check=False)
        if completed.returncode != 0 and b"No such container" not in completed.stderr:
            raise SupervisorError("exact Development container removal failed")

    def container_absent(self, container_id: str) -> bool:
        return self._run("inspect", container_id, check=False).returncode != 0

    def owner_absent(self, owner_nonce: str) -> bool:
        completed = self._run("ps", "--all", "--quiet", "--filter", f"label=org.text-to-cad.owner-nonce={owner_nonce}")
        return completed.stdout.strip() == b""


def _interactive_exchange(process, release_for_preflight, timeout_seconds: int) -> tuple[bytes, bytes]:
    """Read preflight, release once, then collect terminal under one deadline."""
    if process.stdout is None or process.stdin is None or process.stderr is None:
        raise SupervisorError("Docker attach pipes are unavailable")
    holder: dict[str, object] = {}

    def worker() -> None:
        stage = "attach-read-preflight"
        first = b""
        try:
            first = process.stdout.readline(16385)
            if not first.endswith(b"\n"):
                process.wait()
                holder["stdout"] = first + process.stdout.read()
                holder["stderr"] = process.stderr.read()
                holder["error"] = AttachError(
                    stage,
                    "preflight-unavailable",
                    process.returncode if isinstance(process.returncode, int) else 125,
                    holder["stdout"],  # type: ignore[arg-type]
                    holder["stderr"],  # type: ignore[arg-type]
                )
                return
            stage = "attach-validate-preflight"
            release = release_for_preflight(first[:-1])
            stage = "attach-write-release"
            process.stdin.write(release)
            process.stdin.flush()
            process.stdin.close()
            stage = "attach-read-terminal"
            tail = process.stdout.read()
            holder["stdout"] = first + tail
            stage = "attach-read-stderr"
            holder["stderr"] = process.stderr.read()
            stage = "attach-wait"
            process.wait()
        except BaseException as error:
            if isinstance(error, AttachError):
                holder["error"] = error
            else:
                holder["error"] = AttachError(
                    stage,
                    type(error).__name__,
                    process.returncode if isinstance(process.returncode, int) else 125,
                    holder.get("stdout", first),  # type: ignore[arg-type]
                    holder.get("stderr", b""),  # type: ignore[arg-type]
                )

    thread = threading.Thread(target=worker, name="development-docker-attach", daemon=True)
    thread.start()
    thread.join(timeout_seconds)
    if thread.is_alive():
        raise TimeoutError(f"single Development job exceeded {timeout_seconds}s")
    if "error" in holder:
        raise holder["error"]  # type: ignore[misc]
    return holder.get("stdout", b""), holder.get("stderr", b"")  # type: ignore[return-value]
