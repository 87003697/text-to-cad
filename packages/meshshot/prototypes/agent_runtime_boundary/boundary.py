#!/usr/bin/env python3
"""THROWAWAY executable outer lifecycle for the SAR-003 seam."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Mapping, Protocol


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from contract import (  # noqa: E402
    ContractError, Digest, ExecutionIdentity, broker_mac,
    canonical_tree_digest, require_exact_record, verify_broker_mac,
)


RECEIPT_SCHEMA = "meshshot.agent-boundary.prototype-matrix/2"
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
        return (self.source, self.input, self.control, self.home, self.cache, self.tmp, self.work, self.output)

    def writable_paths(self) -> tuple[Path, ...]:
        return (self.home, self.cache, self.tmp, self.work, self.output)


@dataclass(frozen=True)
class ImageExpectation:
    reference: str
    manifest_digest: Digest
    config_digest: Digest
    runtime_manifest_digest: Digest

    def __post_init__(self) -> None:
        if not self.reference.endswith("@" + self.manifest_digest.value):
            raise ContractError("image reference must carry the expected manifest digest")


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


@dataclass(frozen=True)
class LifecycleReceipt:
    status: str
    failure_check: str | None
    workload_released: bool
    workload_status: int | None
    absence_proved: bool
    retained_resource: bool
    calls: tuple[str, ...]


class LifecycleAdapter(Protocol):
    calls: list[str]
    def inspect_image(self, reference: str) -> ImageObservation | None: ...
    def provision_broker_secret(self, identity: ExecutionIdentity, secret: bytes) -> None: ...
    def create_inert(self, argv: list[str]) -> str: ...
    def inspect_container(self, resource_id: str) -> ContainerObservation: ...
    def start(self, resource_id: str) -> None: ...
    def read_record(self, resource_id: str, phase: str) -> Mapping[str, object]: ...
    def write_record(self, resource_id: str, phase: str, value: Mapping[str, object]) -> None: ...
    def remove_exact(self, resource_id: str) -> None: ...
    def cleanup_owned(self, owner_nonce: str) -> None: ...
    def prove_absence(self, resource_id: str, owner_nonce: str) -> bool: ...
    def prove_owner_absence(self, owner_nonce: str) -> bool: ...


def _resource_stem(identity: ExecutionIdentity) -> str:
    return f"meshshot-agent-boundary-prototype-{identity.job_id}-{identity.owner_nonce[:12]}"


def _validate_paths(spec: JobSpec) -> None:
    try:
        resolved = [path.resolve(strict=True) for path in spec.paths.all_paths()]
    except OSError as exc:
        raise BoundaryFailure("snapshot-identity") from exc
    root = resolved[0].parent
    if root.name != _resource_stem(spec.identity) or any(path.parent != root for path in resolved):
        raise BoundaryFailure("job-private-layout")
    if root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise BoundaryFailure("job-private-layout")
    if tuple(path.name for path in resolved) != (
        "source", "input", "control", "home", "cache", "tmp", "work", "output",
    ):
        raise BoundaryFailure("job-private-layout")
    writable = [path.resolve() for path in spec.paths.writable_paths()]
    if len(set(writable)) != len(writable) or any(
        left in right.parents or right in left.parents
        for index, left in enumerate(writable) for right in writable[index + 1:]
    ):
        raise BoundaryFailure("job-private-layout")
    if (
        spec.container_name != _resource_stem(spec.identity)
        or spec.broker_volume != _resource_stem(spec.identity) + "-broker"
    ):
        raise BoundaryFailure("job-private-layout")
    try:
        source_digest = canonical_tree_digest(spec.paths.source)
        input_digest = canonical_tree_digest(spec.paths.input)
    except (OSError, ContractError) as exc:
        raise BoundaryFailure("snapshot-identity") from exc
    if source_digest != spec.identity.source_digest or input_digest != spec.identity.input_digest:
        raise BoundaryFailure("snapshot-identity")


def build_create_argv(spec: JobSpec, docker_host: str = "unix:///outer/docker.sock") -> list[str]:
    _validate_paths(spec)
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
        mount = f"type=bind,src={source.resolve()},dst={destination}" + (",readonly" if readonly else "")
        argv += ["--mount", mount]
    argv += ["--mount", f"type=volume,src={spec.broker_volume},dst=/run/meshshot-browser,volume-nocopy,readonly"]
    argv += [spec.image.reference]
    return argv


def _verify_image(expected: ImageExpectation, observed: ImageObservation | None) -> None:
    if observed is None:
        raise BoundaryFailure("image-identity")
    if (
        expected.manifest_digest.value not in observed.manifest_digests
        or observed.config_digest != expected.config_digest.value
        or observed.runtime_manifest_digest != expected.runtime_manifest_digest.value
        or observed.os != "linux" or observed.architecture != "amd64"
    ):
        raise BoundaryFailure("image-identity")


def _verify_container(spec: JobSpec, resource_id: str, observed: ContainerObservation) -> None:
    expected_ro = {"/run/agent-job/source", "/run/agent-job/input", "/run/agent-boundary", "/run/meshshot-browser"}
    expected_rw = {
        "/run/agent-job/home", "/run/agent-job/cache", "/run/agent-job/tmp",
        "/run/agent-job/work", "/run/agent-job/output",
    }
    expected_ro_sources = tuple(sorted((
        ("/run/agent-job/source", str(spec.paths.source.resolve())),
        ("/run/agent-job/input", str(spec.paths.input.resolve())),
        ("/run/agent-boundary", str(spec.paths.control.resolve())),
    )))
    expected_rw_sources = tuple(sorted(zip(
        sorted(expected_rw),
        (str(spec.paths.cache.resolve()), str(spec.paths.home.resolve()),
         str(spec.paths.output.resolve()), str(spec.paths.tmp.resolve()),
         str(spec.paths.work.resolve())),
        strict=True,
    )))
    if (
        observed.resource_id != resource_id
        or observed.image_config_digest != spec.image.config_digest.value
        or observed.owner_nonce != spec.identity.owner_nonce
        or observed.job_id != spec.identity.job_id
        or observed.running or not observed.read_only_root
        or observed.network_mode != "none"
        or observed.cap_drop != ("ALL",) or not observed.no_new_privileges
        or observed.user != "1000:1000" or observed.pids_limit != 256
        or observed.memory_bytes != 4 * 1024**3
        or observed.memory_swap_bytes != 4 * 1024**3
        or observed.nano_cpus != 2_000_000_000
        or observed.environment != AGENT_ENV
        or observed.readonly_mounts != expected_ro
        or observed.writable_mounts != expected_rw
        or observed.readonly_bind_sources != expected_ro_sources
        or observed.writable_bind_sources != expected_rw_sources
        or observed.broker_volume != spec.broker_volume
        or observed.docker_socket_exposed
    ):
        raise BoundaryFailure("inert-container")


def _record(schema: str, identity: ExecutionIdentity, **extra: object) -> dict[str, object]:
    return {"schema": schema, **identity.as_json(), **extra}


def run_job(spec: JobSpec, adapter: LifecycleAdapter) -> LifecycleReceipt:
    owned_id: str | None = None
    failure: str | None = None
    released = False
    workload_status: int | None = None
    cleanup_failed = False
    absence = False
    try:
        argv = build_create_argv(spec)
        _verify_image(spec.image, adapter.inspect_image(spec.image.reference))
        adapter.provision_broker_secret(spec.identity, spec.broker_secret)
        returned_id = adapter.create_inert(argv)
        if not RESOURCE_ID_RE.fullmatch(returned_id):
            raise BoundaryFailure("returned-container-id")
        owned_id = returned_id
        _verify_container(spec, owned_id, adapter.inspect_container(owned_id))
        adapter.start(owned_id)
        ready = adapter.read_record(owned_id, "ready")
        try:
            require_exact_record(ready, "meshshot.agent-boundary.ready/1", spec.identity)
        except ContractError as exc:
            raise BoundaryFailure("entrypoint-preflight") from exc
        adapter.write_record(owned_id, "challenge", _record(
            "meshshot.agent-boundary.challenge/1", spec.identity, challenge=spec.challenge,
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
            spec.broker_secret, spec.identity, spec.challenge, preflight.get("brokerMac"),
        ):
            raise BoundaryFailure("broker-proof")
        adapter.write_record(owned_id, "release", _record(
            "meshshot.agent-boundary.release/1", spec.identity,
        ))
        released = True
        terminal = adapter.read_record(owned_id, "terminal")
        try:
            require_exact_record(
                terminal, "meshshot.agent-boundary.terminal/2", spec.identity,
                ("workloadStatus", "outputDigest"),
            )
        except ContractError as exc:
            raise BoundaryFailure("terminal-publication") from exc
        workload_status = terminal.get("workloadStatus") if isinstance(terminal.get("workloadStatus"), int) else None
        try:
            Digest(str(terminal.get("outputDigest")))
        except ContractError as exc:
            raise BoundaryFailure("terminal-publication") from exc
        adapter.write_record(owned_id, "ack", _record(
            "meshshot.agent-boundary.ack/1", spec.identity,
        ))
        if workload_status != 0:
            raise BoundaryFailure("workload-terminal")
    except BoundaryFailure as exc:
        failure = exc.check
    except Exception:
        failure = "adapter-failure"
    finally:
        try:
            if owned_id is not None:
                adapter.remove_exact(owned_id)
            adapter.cleanup_owned(spec.identity.owner_nonce)
        except Exception:
            cleanup_failed = True
        try:
            absence = (
                (owned_id is None or adapter.prove_absence(owned_id, spec.identity.owner_nonce))
                and adapter.prove_owner_absence(spec.identity.owner_nonce)
            )
        except Exception:
            absence = False
        if not absence:
            failure = "retained-resource"
        elif cleanup_failed:
            failure = "cleanup"
    return LifecycleReceipt(
        status="succeeded" if failure is None else "failed",
        failure_check=failure,
        workload_released=released,
        workload_status=workload_status,
        absence_proved=absence,
        retained_resource=not absence,
        calls=tuple(adapter.calls),
    )


def run_unsafe_baseline(adapter: LifecycleAdapter) -> tuple[str, ...]:
    """Executable RED: releases work before identity/config/proof verification."""
    resource_id = adapter.create_inert(["unsafe"])
    adapter.start(resource_id)
    adapter.write_record(resource_id, "release", {"schema": "unsafe-release"})
    adapter.inspect_image("mutable")
    adapter.inspect_container(resource_id)
    return tuple(adapter.calls)


def discover_browser_artifacts(roots: tuple[Path, ...]) -> list[dict[str, str]]:
    scanner_path = HERE.parents[3] / "scripts/pilot/browser_surface.py"
    spec = importlib.util.spec_from_file_location("sar003_browser_surface", scanner_path)
    if spec is None or spec.loader is None:
        raise BoundaryFailure("browser-deny")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    mounts = [(root, Path("/agent") / root.name, True) for root in roots]
    return module.discover_browser_roots(mounts, permitted_symlink_roots=roots)


class ScriptedAdapter:
    """Injectable OS/Docker/attach fake used by the executable matrix."""
    def __init__(self, spec: JobSpec) -> None:
        self.spec = spec
        self.calls: list[str] = []
        self.resource_id = "a" * 64
        self.returned_id = self.resource_id
        self.image: ImageObservation | None = ImageObservation(
            frozenset({spec.image.manifest_digest.value}),
            spec.image.config_digest.value, spec.image.runtime_manifest_digest.value,
        )
        self.container = ContainerObservation(
            self.resource_id, spec.image.config_digest.value,
            spec.identity.owner_nonce, spec.identity.job_id, False, True, "none",
            ("ALL",), True, "1000:1000", 256, 4 * 1024**3, 4 * 1024**3,
            2_000_000_000, AGENT_ENV,
            frozenset({
                "/run/agent-job/source", "/run/agent-job/input",
                "/run/agent-boundary", "/run/meshshot-browser",
            }),
            frozenset({
                "/run/agent-job/home", "/run/agent-job/cache",
                "/run/agent-job/tmp", "/run/agent-job/work",
                "/run/agent-job/output",
            }),
            tuple(sorted((
                ("/run/agent-job/source", str(spec.paths.source.resolve())),
                ("/run/agent-job/input", str(spec.paths.input.resolve())),
                ("/run/agent-boundary", str(spec.paths.control.resolve())),
            ))),
            tuple(sorted((
                ("/run/agent-job/home", str(spec.paths.home.resolve())),
                ("/run/agent-job/cache", str(spec.paths.cache.resolve())),
                ("/run/agent-job/tmp", str(spec.paths.tmp.resolve())),
                ("/run/agent-job/work", str(spec.paths.work.resolve())),
                ("/run/agent-job/output", str(spec.paths.output.resolve())),
            ))),
            spec.broker_volume,
        )
        self.provisioned_broker_secret: bytes | None = None
        self.broker_key_override: bytes | None = None
        self.fail_read: str | None = None
        self.absent = True
        self.owner_absent = True
        self.terminal_status = 0
        self.challenge: str | None = None

    def inspect_image(self, reference: str) -> ImageObservation | None:
        self.calls.append("inspect-image")
        return self.image

    def create_inert(self, argv: list[str]) -> str:
        self.calls.append("create-inert")
        return self.returned_id

    def provision_broker_secret(self, identity: ExecutionIdentity, secret: bytes) -> None:
        self.calls.append("provision-broker-secret")
        self.provisioned_broker_secret = secret

    def inspect_container(self, resource_id: str) -> ContainerObservation:
        self.calls.append("inspect-container")
        return self.container

    def start(self, resource_id: str) -> None:
        self.calls.append("start-entrypoint")

    def read_record(self, resource_id: str, phase: str) -> Mapping[str, object]:
        self.calls.append("read-" + phase)
        if self.fail_read == phase:
            raise BoundaryFailure("entrypoint" if phase == "ready" else "terminal-publication")
        if phase == "ready":
            return _record("meshshot.agent-boundary.ready/1", self.spec.identity)
        if phase == "preflight":
            assert self.challenge is not None
            key = self.broker_key_override or self.provisioned_broker_secret
            assert key is not None
            return _record(
                "meshshot.agent-boundary.preflight/2", self.spec.identity,
                challenge=self.challenge,
                brokerMac=broker_mac(key, self.spec.identity, self.challenge),
            )
        if phase == "terminal":
            return _record(
                "meshshot.agent-boundary.terminal/2", self.spec.identity,
                workloadStatus=self.terminal_status, outputDigest="sha256:" + "f" * 64,
            )
        raise AssertionError(phase)

    def write_record(self, resource_id: str, phase: str, value: Mapping[str, object]) -> None:
        self.calls.append("write-" + phase)
        if phase == "challenge":
            self.challenge = str(value["challenge"])

    def remove_exact(self, resource_id: str) -> None:
        self.calls.append("remove-exact")

    def cleanup_owned(self, owner_nonce: str) -> None:
        self.calls.append("cleanup-owned")

    def prove_absence(self, resource_id: str, owner_nonce: str) -> bool:
        self.calls.append("prove-id-absence")
        return self.absent

    def prove_owner_absence(self, owner_nonce: str) -> bool:
        self.calls.append("prove-owner-absence")
        return self.owner_absent


def _fixture_spec(root: Path) -> JobSpec:
    identity_seed = ExecutionIdentity(
        "job-a", "b" * 32, Digest("sha256:" + "1" * 64),
        Digest("sha256:" + "2" * 64), Digest("sha256:" + "3" * 64),
        Digest("sha256:" + "0" * 64), Digest("sha256:" + "0" * 64),
        Digest("sha256:" + "4" * 64),
    )
    job_root = root / _resource_stem(identity_seed)
    job_root.mkdir(mode=0o700)
    paths = {
        name: job_root / name
        for name in (
            "source", "input", "control", "home", "cache", "tmp", "work",
            "output",
        )
    }
    for path in paths.values():
        path.mkdir()
    (paths["source"] / "source.txt").write_text("source", encoding="utf-8")
    (paths["input"] / "input.bin").write_bytes(b"input")
    identity = replace(
        identity_seed,
        source_digest=canonical_tree_digest(paths["source"]),
        input_digest=canonical_tree_digest(paths["input"]),
    )
    image = ImageExpectation(
        "example.invalid/agent@" + identity.agent_image_digest.value,
        identity.agent_image_digest, identity.agent_config_digest,
        identity.runtime_manifest_digest,
    )
    return JobSpec(
        identity, image, JobPaths(**paths), _resource_stem(identity),
        _resource_stem(identity) + "-broker", b"k" * 32, "c" * 64,
    )


def matrix() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    cases = (
        "success", "wrong_image_digest", "missing_image_digest", "wrong_image_config_digest",
        "returned_id_substitution", "wrong_source_digest", "missing_source_digest",
        "writable_root", "writable_source", "writable_input", "docker_socket_exposure",
        "extra_network_route", "shared_job_home", "shared_job_socket", "shared_job_output",
        "partial_startup_before_verification", "entrypoint_failure",
        "terminal_publication_failure", "cleanup_residue",
        "cross_job_authority_substitution", "renamed_browser_artifact",
    )
    expected = {
        "success": ("succeeded", None, True),
        "wrong_image_digest": ("failed", "image-identity", False),
        "missing_image_digest": ("failed", "image-identity", False),
        "wrong_image_config_digest": ("failed", "image-identity", False),
        "returned_id_substitution": ("failed", "inert-container", False),
        "wrong_source_digest": ("failed", "snapshot-identity", False),
        "missing_source_digest": ("failed", "snapshot-identity", False),
        "writable_root": ("failed", "inert-container", False),
        "writable_source": ("failed", "inert-container", False),
        "writable_input": ("failed", "inert-container", False),
        "docker_socket_exposure": ("failed", "inert-container", False),
        "extra_network_route": ("failed", "inert-container", False),
        "shared_job_home": ("failed", "job-private-layout", False),
        "shared_job_socket": ("failed", "job-private-layout", False),
        "shared_job_output": ("failed", "job-private-layout", False),
        "partial_startup_before_verification": ("failed", "inert-container", False),
        "entrypoint_failure": ("failed", "entrypoint", False),
        "terminal_publication_failure": ("failed", "terminal-publication", True),
        "cleanup_residue": ("failed", "retained-resource", True),
        "cross_job_authority_substitution": ("failed", "broker-proof", False),
        "renamed_browser_artifact": ("failed", "entrypoint", False),
    }
    with tempfile.TemporaryDirectory() as temporary:
        for index, case in enumerate(cases):
            case_root = Path(temporary) / str(index)
            case_root.mkdir()
            spec = _fixture_spec(case_root)
            adapter = ScriptedAdapter(spec)
            if case == "wrong_image_digest":
                adapter.image = replace(adapter.image, manifest_digests=frozenset({"sha256:" + "9" * 64}))
            elif case == "missing_image_digest":
                adapter.image = None
            elif case == "wrong_image_config_digest":
                adapter.image = replace(adapter.image, config_digest="sha256:" + "9" * 64)
            elif case == "returned_id_substitution":
                adapter.container = replace(adapter.container, resource_id="d" * 64)
            elif case == "wrong_source_digest":
                spec = replace(spec, identity=replace(spec.identity, source_digest=Digest("sha256:" + "9" * 64)))
                adapter = ScriptedAdapter(spec)
            elif case == "missing_source_digest":
                spec.paths.source.rename(spec.paths.source.with_name("source-missing"))
            elif case == "writable_root":
                adapter.container = replace(adapter.container, read_only_root=False)
            elif case == "writable_source":
                adapter.container = replace(
                    adapter.container,
                    readonly_mounts=adapter.container.readonly_mounts
                    - {"/run/agent-job/source"},
                )
            elif case == "writable_input":
                adapter.container = replace(
                    adapter.container,
                    readonly_mounts=adapter.container.readonly_mounts
                    - {"/run/agent-job/input"},
                )
            elif case == "docker_socket_exposure":
                adapter.container = replace(adapter.container, docker_socket_exposed=True)
            elif case == "extra_network_route":
                adapter.container = replace(adapter.container, network_mode="bridge")
            elif case == "shared_job_home":
                spec = replace(spec, paths=replace(spec.paths, home=spec.paths.cache))
                adapter = ScriptedAdapter(spec)
            elif case == "shared_job_socket":
                spec = replace(spec, broker_volume="shared-broker")
                adapter = ScriptedAdapter(spec)
            elif case == "shared_job_output":
                spec = replace(spec, paths=replace(spec.paths, output=spec.paths.work))
                adapter = ScriptedAdapter(spec)
            elif case == "partial_startup_before_verification":
                adapter.container = replace(adapter.container, running=True)
            elif case == "entrypoint_failure":
                adapter.fail_read = "ready"
            elif case == "terminal_publication_failure":
                adapter.fail_read = "terminal"
            elif case == "cleanup_residue":
                adapter.absent = False
            elif case == "cross_job_authority_substitution":
                adapter.broker_key_override = b"x" * 32
            elif case == "renamed_browser_artifact":
                renamed = spec.paths.source / "vendor-render"
                renamed.write_bytes(b"\x7fELF" + b"HeadlessChrome")
                renamed.chmod(0o755)
                findings = discover_browser_artifacts((spec.paths.source,))
                if not findings:
                    raise AssertionError("formal scanner missed renamed browser artifact")
                spec = replace(
                    spec,
                    identity=replace(
                        spec.identity,
                        source_digest=canonical_tree_digest(spec.paths.source),
                    ),
                )
                adapter = ScriptedAdapter(spec)
                adapter.fail_read = "ready"
            red_adapter = ScriptedAdapter(spec)
            red_calls = run_unsafe_baseline(red_adapter)
            receipt = run_job(spec, adapter)
            expected_status, expected_failure, expected_release = expected[case]
            passed = (
                receipt.status == expected_status
                and receipt.failure_check == expected_failure
                and receipt.workload_released is expected_release
                and red_calls.index("write-release") < red_calls.index("inspect-image")
            )
            rows.append({
                "case": case, "pass": passed,
                "red": {"calls": red_calls, "releasedBeforeInspect": True},
                "green": {
                    "status": receipt.status, "failureCheck": receipt.failure_check,
                    "workloadReleased": receipt.workload_released,
                    "absenceProved": receipt.absence_proved, "calls": receipt.calls,
                },
            })
    return {
        "schema": RECEIPT_SCHEMA, "caseCount": len(rows),
        "passCount": sum(bool(row["pass"]) for row in rows), "cases": rows,
        "verdict": "ADOPT_WITH_FORMAL_VERIFICATION_GATES" if all(row["pass"] for row in rows) else "REJECT",
        "realOciRun": "NOT_RUN", "agentRuntimeVerified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("matrix",))
    parser.parse_args()
    print(json.dumps(matrix(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
