#!/usr/bin/env python3
"""THROWAWAY production-shaped outer lifecycle contract for SAR-003."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Protocol


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from contract import (  # noqa: E402
    ContractError, Digest, ExecutionIdentity, canonical_tree_digest,
    require_exact_record, verify_broker_mac, workload_digest,
)


RESOURCE_ID_RE = re.compile(r"^[0-9a-f]{64}$")
OWNER_LABEL = "io.text-to-cad.agent-boundary-owner"
JOB_LABEL = "io.text-to-cad.agent-boundary-job"
AGENT_ENV = (
    "HOME=/run/agent-job/home", "CODEX_HOME=/run/agent-job/home/.codex",
    "XDG_CACHE_HOME=/run/agent-job/cache", "TMPDIR=/run/agent-job/tmp",
    "LANG=C.UTF-8", "LC_ALL=C.UTF-8", "TZ=UTC",
    "GIT_TERMINAL_PROMPT=0", "PYTHONDONTWRITEBYTECODE=1",
)


class BoundaryFailure(RuntimeError):
    def __init__(self, check: str) -> None:
        super().__init__(check)
        self.check = check


@dataclass(frozen=True)
class JobPaths:
    source: Path
    input: Path
    control: Path
    home: Path
    cache: Path
    tmp: Path
    work: Path
    output: Path

    def all_paths(self) -> tuple[Path, ...]:
        return (
            self.source, self.input, self.control, self.home, self.cache,
            self.tmp, self.work, self.output,
        )


@dataclass(frozen=True)
class ImageExpectation:
    reference: str
    manifest_digest: Digest
    config_digest: Digest
    runtime_manifest_digest: Digest

    def __post_init__(self) -> None:
        if not self.reference.endswith("@" + self.manifest_digest.value):
            raise ContractError("image reference must carry its manifest digest")


@dataclass(frozen=True)
class ImageObservation:
    manifest_digests: frozenset[str]
    config_digest: str
    runtime_manifest_digest: str
    os: str = "linux"
    architecture: str = "amd64"


@dataclass(frozen=True)
class ContainerObservation:
    resource_id: str
    image_config_digest: str
    owner_nonce: str
    job_id: str
    running: bool
    read_only_root: bool
    network_mode: str
    cap_drop: tuple[str, ...]
    no_new_privileges: bool
    user: str
    pids_limit: int
    memory_bytes: int
    memory_swap_bytes: int
    nano_cpus: int
    environment: tuple[str, ...]
    readonly_mounts: frozenset[str]
    writable_mounts: frozenset[str]
    readonly_bind_sources: tuple[tuple[str, str], ...]
    writable_bind_sources: tuple[tuple[str, str], ...]
    broker_volume: str
    docker_socket_exposed: bool = False


@dataclass(frozen=True)
class JobSpec:
    identity: ExecutionIdentity
    image: ImageExpectation
    paths: JobPaths
    container_name: str
    broker_volume: str
    broker_secret: bytes
    challenge: str
    workload: tuple[str, ...]


@dataclass(frozen=True)
class LifecycleReceipt:
    status: str
    failure_check: str | None
    workload_released: bool
    workload_status: int | None
    container_absent: bool
    owner_labels_absent: bool
    broker_volume_absent: bool
    private_tree_absent: bool
    retained_resource: bool
    calls: tuple[str, ...]

    @property
    def absence_proved(self) -> bool:
        return (
            self.container_absent and self.owner_labels_absent
            and self.broker_volume_absent and self.private_tree_absent
        )


class LifecycleAdapter(Protocol):
    calls: list[str]
    def inspect_image(self, reference: str) -> ImageObservation | None: ...
    def provision_broker_secret(self, identity: ExecutionIdentity, secret: bytes) -> None: ...
    def create_inert(self, argv: list[str]) -> str: ...
    def inspect_container(self, candidate_id: str) -> ContainerObservation: ...
    def start(self, owned_id: str) -> None: ...
    def read_record(self, owned_id: str, phase: str) -> Mapping[str, object]: ...
    def write_record(self, owned_id: str, phase: str, value: Mapping[str, object]) -> None: ...
    def remove_exact(self, owned_id: str) -> None: ...
    def cleanup_owner_labeled(self, owner_nonce: str) -> None: ...
    def cleanup_broker_volume(self, volume: str, owner_nonce: str) -> None: ...
    def cleanup_private_tree(self, root: Path, owner_nonce: str) -> None: ...
    def prove_container_absence(self, owned_id: str, owner_nonce: str) -> bool: ...
    def prove_owner_label_absence(self, owner_nonce: str) -> bool: ...
    def prove_broker_volume_absence(self, volume: str, owner_nonce: str) -> bool: ...
    def prove_private_tree_absence(self, root: Path, owner_nonce: str) -> bool: ...


def resource_stem(identity: ExecutionIdentity) -> str:
    return f"meshshot-agent-boundary-prototype-{identity.job_id}-{identity.owner_nonce[:12]}"


def admit_job_root(spec: JobSpec) -> Path:
    root = spec.paths.source.parent.absolute()
    try:
        metadata = root.lstat()
        resolved_root = root.resolve(strict=True)
    except OSError as exc:
        raise BoundaryFailure("job-private-layout") from exc
    if (
        resolved_root != root or not stat.S_ISDIR(metadata.st_mode)
        or root.name != resource_stem(spec.identity)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise BoundaryFailure("job-private-layout")
    expected = tuple(root / name for name in (
        "source", "input", "control", "home", "cache", "tmp", "work", "output",
    ))
    if spec.paths.all_paths() != expected:
        raise BoundaryFailure("job-private-layout")
    if (
        spec.container_name != resource_stem(spec.identity)
        or spec.broker_volume != resource_stem(spec.identity) + "-broker"
    ):
        raise BoundaryFailure("job-private-layout")
    return root


def validate_job_spec(spec: JobSpec) -> Path:
    root = admit_job_root(spec)
    for index, path in enumerate(spec.paths.all_paths()):
        try:
            admitted = (
                stat.S_ISDIR(path.lstat().st_mode)
                and path.resolve(strict=True) == path
            )
        except OSError as exc:
            check = "snapshot-identity" if index < 2 else "job-private-layout"
            raise BoundaryFailure(check) from exc
        if not admitted:
            raise BoundaryFailure("job-private-layout")
    try:
        source_digest = canonical_tree_digest(spec.paths.source)
        input_digest = canonical_tree_digest(spec.paths.input)
    except (OSError, ContractError) as exc:
        raise BoundaryFailure("snapshot-identity") from exc
    if (
        source_digest != spec.identity.source_digest
        or input_digest != spec.identity.input_digest
    ):
        raise BoundaryFailure("snapshot-identity")
    try:
        observed_workload_digest = workload_digest(spec.workload)
    except ContractError as exc:
        raise BoundaryFailure("workload-identity") from exc
    if observed_workload_digest != spec.identity.workload_digest:
        raise BoundaryFailure("workload-identity")
    return root


def build_create_argv(spec: JobSpec, docker_host: str = "unix:///outer/docker.sock") -> list[str]:
    validate_job_spec(spec)
    argv = [
        "docker", "--host", docker_host, "create", "--name", spec.container_name,
        "--label", f"{OWNER_LABEL}={spec.identity.owner_nonce}",
        "--label", f"{JOB_LABEL}={spec.identity.job_id}",
        "--read-only", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "1000:1000",
        "--pids-limit", "256", "--memory", "4g", "--memory-swap", "4g",
        "--cpus", "2",
    ]
    for value in AGENT_ENV:
        argv += ["--env", value]
    for source, destination, readonly in (
        (spec.paths.source, "/run/agent-job/source", True),
        (spec.paths.input, "/run/agent-job/input", True),
        (spec.paths.control, "/run/agent-boundary", True),
        (spec.paths.home, "/run/agent-job/home", False),
        (spec.paths.cache, "/run/agent-job/cache", False),
        (spec.paths.tmp, "/run/agent-job/tmp", False),
        (spec.paths.work, "/run/agent-job/work", False),
        (spec.paths.output, "/run/agent-job/output", False),
    ):
        mount = f"type=bind,src={source.resolve()},dst={destination}"
        argv += ["--mount", mount + (",readonly" if readonly else "")]
    argv += [
        "--mount",
        f"type=volume,src={spec.broker_volume},dst=/run/meshshot-browser,volume-nocopy,readonly",
        spec.image.reference,
    ]
    return argv


def verify_image(expected: ImageExpectation, observed: ImageObservation | None) -> None:
    if observed is None or (
        expected.manifest_digest.value not in observed.manifest_digests
        or observed.config_digest != expected.config_digest.value
        or observed.runtime_manifest_digest != expected.runtime_manifest_digest.value
        or observed.os != "linux" or observed.architecture != "amd64"
    ):
        raise BoundaryFailure("image-identity")


def verify_candidate_ownership(
    spec: JobSpec, candidate_id: str, observed: ContainerObservation,
) -> str:
    """Return delete authority only after exact ID and owner labels agree."""
    if (
        not RESOURCE_ID_RE.fullmatch(candidate_id)
        or observed.resource_id != candidate_id
        or observed.owner_nonce != spec.identity.owner_nonce
        or observed.job_id != spec.identity.job_id
    ):
        raise BoundaryFailure("container-ownership")
    return candidate_id


def verify_inert_config(spec: JobSpec, observed: ContainerObservation) -> None:
    expected_ro = {
        "/run/agent-job/source", "/run/agent-job/input",
        "/run/agent-boundary", "/run/meshshot-browser",
    }
    expected_rw = {
        "/run/agent-job/home", "/run/agent-job/cache", "/run/agent-job/tmp",
        "/run/agent-job/work", "/run/agent-job/output",
    }
    expected_ro_sources = tuple(sorted((
        ("/run/agent-job/source", str(spec.paths.source.resolve())),
        ("/run/agent-job/input", str(spec.paths.input.resolve())),
        ("/run/agent-boundary", str(spec.paths.control.resolve())),
    )))
    expected_rw_sources = tuple(sorted((
        ("/run/agent-job/home", str(spec.paths.home.resolve())),
        ("/run/agent-job/cache", str(spec.paths.cache.resolve())),
        ("/run/agent-job/tmp", str(spec.paths.tmp.resolve())),
        ("/run/agent-job/work", str(spec.paths.work.resolve())),
        ("/run/agent-job/output", str(spec.paths.output.resolve())),
    )))
    if (
        observed.image_config_digest != spec.image.config_digest.value
        or observed.running or not observed.read_only_root
        or observed.network_mode != "none" or observed.cap_drop != ("ALL",)
        or not observed.no_new_privileges or observed.user != "1000:1000"
        or observed.pids_limit != 256 or observed.memory_bytes != 4 * 1024**3
        or observed.memory_swap_bytes != 4 * 1024**3
        or observed.nano_cpus != 2_000_000_000 or observed.environment != AGENT_ENV
        or observed.readonly_mounts != expected_ro
        or observed.writable_mounts != expected_rw
        or observed.readonly_bind_sources != expected_ro_sources
        or observed.writable_bind_sources != expected_rw_sources
        or observed.broker_volume != spec.broker_volume
        or observed.docker_socket_exposed
    ):
        raise BoundaryFailure("inert-container")


def record(schema: str, identity: ExecutionIdentity, **extra: object) -> dict[str, object]:
    return {"schema": schema, **identity.as_json(), **extra}


def run_job(spec: JobSpec, adapter: LifecycleAdapter) -> LifecycleReceipt:
    owned_id: str | None = None
    job_root: Path | None = None
    broker_owned = False
    failure: str | None = None
    released = False
    workload_status: int | None = None
    cleanup_failed = False
    container_absent = owner_absent = broker_absent = tree_absent = False
    try:
        job_root = admit_job_root(spec)
        validate_job_spec(spec)
        argv = build_create_argv(spec)
        verify_image(spec.image, adapter.inspect_image(spec.image.reference))
        broker_owned = True
        adapter.provision_broker_secret(spec.identity, spec.broker_secret)
        candidate_id = adapter.create_inert(argv)
        if not RESOURCE_ID_RE.fullmatch(candidate_id):
            raise BoundaryFailure("returned-container-id")
        observed = adapter.inspect_container(candidate_id)
        owned_id = verify_candidate_ownership(spec, candidate_id, observed)
        verify_inert_config(spec, observed)
        adapter.start(owned_id)
        ready = adapter.read_record(owned_id, "ready")
        try:
            require_exact_record(ready, "meshshot.agent-boundary.ready/1", spec.identity)
        except ContractError as exc:
            raise BoundaryFailure("entrypoint-preflight") from exc
        adapter.write_record(owned_id, "challenge", record(
            "meshshot.agent-boundary.challenge/1", spec.identity,
            challenge=spec.challenge,
        ))
        preflight = adapter.read_record(owned_id, "preflight")
        try:
            require_exact_record(
                preflight, "meshshot.agent-boundary.preflight/2", spec.identity,
                ("challenge", "brokerMac"),
            )
        except ContractError as exc:
            raise BoundaryFailure("entrypoint-preflight") from exc
        if preflight.get("challenge") != spec.challenge or not verify_broker_mac(
            spec.broker_secret, spec.identity, spec.challenge,
            preflight.get("brokerMac"),
        ):
            raise BoundaryFailure("broker-proof")
        adapter.write_record(
            owned_id, "release",
            record("meshshot.agent-boundary.release/1", spec.identity),
        )
        released = True
        terminal = adapter.read_record(owned_id, "terminal")
        try:
            require_exact_record(
                terminal, "meshshot.agent-boundary.terminal/3", spec.identity,
                ("workloadStatus", "outputDigest", "processGroupAbsent", "descendantResidue"),
            )
            Digest(str(terminal.get("outputDigest")))
        except ContractError as exc:
            raise BoundaryFailure("terminal-publication") from exc
        value = terminal.get("workloadStatus")
        workload_status = value if isinstance(value, int) and not isinstance(value, bool) else None
        if terminal.get("processGroupAbsent") is not True:
            raise BoundaryFailure("workload-process-group")
        if terminal.get("descendantResidue") is not False:
            raise BoundaryFailure("workload-process-group")
        adapter.write_record(
            owned_id, "ack", record("meshshot.agent-boundary.ack/1", spec.identity),
        )
        if workload_status != 0:
            raise BoundaryFailure("workload-terminal")
    except BoundaryFailure as exc:
        failure = exc.check
    except Exception:
        failure = "adapter-failure"
    finally:
        if owned_id is not None:
            try:
                adapter.remove_exact(owned_id)
            except Exception:
                cleanup_failed = True
        try:
            adapter.cleanup_owner_labeled(spec.identity.owner_nonce)
        except Exception:
            cleanup_failed = True
        if broker_owned:
            try:
                adapter.cleanup_broker_volume(
                    spec.broker_volume, spec.identity.owner_nonce,
                )
            except Exception:
                cleanup_failed = True
        if job_root is not None:
            try:
                adapter.cleanup_private_tree(job_root, spec.identity.owner_nonce)
            except Exception:
                cleanup_failed = True
        if owned_id is None:
            container_absent = True
        else:
            try:
                container_absent = adapter.prove_container_absence(
                    owned_id, spec.identity.owner_nonce,
                )
            except Exception:
                container_absent = False
        try:
            owner_absent = adapter.prove_owner_label_absence(
                spec.identity.owner_nonce,
            )
        except Exception:
            owner_absent = False
        if not broker_owned:
            broker_absent = True
        else:
            try:
                broker_absent = adapter.prove_broker_volume_absence(
                    spec.broker_volume, spec.identity.owner_nonce,
                )
            except Exception:
                broker_absent = False
        if job_root is None:
            tree_absent = True
        else:
            try:
                tree_absent = adapter.prove_private_tree_absence(
                    job_root, spec.identity.owner_nonce,
                )
            except Exception:
                tree_absent = False
        retained = not (
            container_absent and owner_absent and broker_absent and tree_absent
        )
        if retained:
            failure = "retained-resource"
        elif cleanup_failed:
            failure = "cleanup"
    return LifecycleReceipt(
        status="succeeded" if failure is None else "failed",
        failure_check=failure,
        workload_released=released,
        workload_status=workload_status,
        container_absent=container_absent,
        owner_labels_absent=owner_absent,
        broker_volume_absent=broker_absent,
        private_tree_absent=tree_absent,
        retained_resource=not (
            container_absent and owner_absent and broker_absent and tree_absent
        ),
        calls=tuple(adapter.calls),
    )
