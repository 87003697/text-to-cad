#!/usr/bin/env python3
"""THROWAWAY real-filesystem concurrency evidence for SAR-007."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from typing import Mapping


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PROTOTYPE = REPO / "packages/meshshot/prototypes/agent_runtime_boundary"
sys.path.insert(0, str(PROTOTYPE))
sys.path.insert(0, str(HERE))
from authority import AuthorityAllocator, FileAuthorityStore  # noqa: E402
import boundary  # noqa: E402
import concurrency  # noqa: E402
from contract import (  # noqa: E402
    Digest, ExecutionIdentity, ExecutionRequest, broker_mac,
    canonical_tree_digest,
)
from agent_runtime_boundary_matrix import ScriptedAdapter, record  # noqa: E402


SCHEMA = "meshshot.agent-boundary.prototype-concurrency/1"
CUP_MANIFEST_DIGEST = "sha256:" + "5" * 64
VERIFICATION_PLAN_DIGEST = "sha256:" + "6" * 64
SHARED_IDENTITY_KEYS = (
    "agentImageDigest", "agentConfigDigest", "runtimeManifestDigest",
    "sourceDigest", "inputDigest", "brokerAuthorityDigest", "workloadDigest",
    "cupRuntimeCapabilityManifestDigest", "verificationPlanDigest",
)


class Tokens:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.count = 0

    def __call__(self, size: int) -> str:
        self.count += 1
        digit = format((int(self.marker, 16) + self.count) % 16, "x")
        return digit * (size * 2)


@dataclass(frozen=True)
class Fixture:
    spec: boundary.JobSpec
    store: FileAuthorityStore
    volume_root: Path
    receipt_path: Path


def authority_group(root: Path) -> tuple[FileAuthorityStore, Path]:
    store_root = (root / "authority-store").resolve()
    receipt_root = (root / "supervisor-receipts").resolve()
    store_root.mkdir(mode=0o700)
    receipt_root.mkdir(mode=0o700)
    return FileAuthorityStore(store_root), receipt_root


def fixture(
    root: Path, index: int, store: FileAuthorityStore, receipt_root: Path,
) -> Fixture:
    root.mkdir()
    root = root.resolve(strict=True)
    staging_source = root / "staging-source"
    staging_input = root / "staging-input"
    staging_source.mkdir()
    staging_input.mkdir()
    (staging_source / "source.txt").write_text("source", encoding="utf-8")
    (staging_input / "input.bin").write_bytes(b"input")
    workload = ("/opt/text-to-cad/bin/agent", "--fixed")
    request = ExecutionRequest(
        f"job-{index}", Digest("sha256:" + "1" * 64),
        Digest("sha256:" + "2" * 64), Digest("sha256:" + "3" * 64),
        canonical_tree_digest(staging_source), canonical_tree_digest(staging_input),
        Digest("sha256:" + "4" * 64), workload,
    )
    grant = AuthorityAllocator(Tokens(format(index + 1, "x"))).allocate(request)
    claimed = store.claim(grant)
    job_root = root / boundary.resource_stem(grant.identity)
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
    identity = grant.identity
    image = boundary.ImageExpectation(
        "example.invalid/agent@" + identity.agent_image_digest.value,
        identity.agent_image_digest, identity.agent_config_digest,
        identity.runtime_manifest_digest,
    )
    spec = boundary.JobSpec(
        claimed, image, boundary.JobPaths(**paths),
        boundary.resource_stem(identity),
        boundary.resource_stem(identity) + "-broker", workload,
    )
    volume_root = root / (spec.broker_volume + "-volume")
    volume_root.mkdir(mode=0o700)
    receipt_path = receipt_root / f"{identity.job_id}.terminal.json"
    return Fixture(spec, store, volume_root, receipt_path)


class FilesystemAdapter(ScriptedAdapter):
    """Outer validator with real private subprocesses, files, and receipts."""

    def __init__(
        self, fixture_value: Fixture, release: threading.Event | None = None,
        residual_preview: bool = False, retain_tree: bool = False,
    ) -> None:
        super().__init__(fixture_value.spec)
        self.fixture = fixture_value
        self.release = release
        self.residual_preview = residual_preview
        self.retain_tree = retain_tree
        self.broker_started = False
        self.sidecar_started = False
        self.resource_processes: dict[str, subprocess.Popen[bytes]] = {}
        self.resource_markers: dict[str, Path] = {}
        self.resource_identity_verified: dict[str, bool] = {}
        self.broker_process_absent: bool | None = None
        self.sidecar_process_absent: bool | None = None
        self.output_tree_digest: str | None = None
        self.observed_terminal_identity_digest: str | None = None
        self.receipt_bytes: bytes | None = None
        self.at_terminal = threading.Event()
        self.terminal_identity_override: ExecutionIdentity | None = None
        self.challenge_override: str | None = None
        self.output_digest_override: str | None = None
        self.receipt_path_override: Path | None = None

    @staticmethod
    def _mapping_digest(value: Mapping[str, object]) -> str:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    def _start_resource(self, role: str) -> None:
        if role in self.resource_processes:
            raise RuntimeError("resource started twice")
        marker = self.fixture.volume_root / f"{role}.identity.json"
        marker_value = {
            "schema": "meshshot.agent-boundary.synthetic-resource/1",
            "role": role,
            "jobId": self.spec.identity.job_id,
            "ownerNonce": self.spec.identity.owner_nonce,
        }
        marker.write_text(json.dumps(
            marker_value, sort_keys=True, separators=(",", ":"),
        ), encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable, "-c", "import time; time.sleep(60)",
                "meshshot-sar007-resource", role, self.spec.identity.job_id,
                self.spec.identity.owner_nonce,
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if process.poll() is not None:
            raise RuntimeError(f"{role} failed to remain alive")
        self.resource_markers[role] = marker
        self.resource_processes[role] = process
        self.resource_identity_verified[role] = (
            json.loads(marker.read_text(encoding="utf-8")) == marker_value
            and process.args[-4:] == [
                "meshshot-sar007-resource", role,
                self.spec.identity.job_id, self.spec.identity.owner_nonce,
            ]
        )
        if role == "broker":
            self.broker_started = True
        else:
            self.sidecar_started = True

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

    def force_stop_resources(self) -> None:
        for role in ("sidecar", "broker"):
            process = self.resource_processes.get(role)
            if process is not None:
                self._stop_process(process)

    def processes_absent(self) -> bool:
        return all(
            process.poll() is not None
            for process in self.resource_processes.values()
        )

    def _persist_terminal_receipt(
        self, terminal: Mapping[str, object],
    ) -> None:
        observed_identity = ExecutionIdentity.from_mapping(terminal)
        expected_subject_digest = self._mapping_digest(self.spec.identity.as_json())
        self.observed_terminal_identity_digest = self._mapping_digest(
            observed_identity.as_json(),
        )
        requested_path = self.receipt_path_override or self.fixture.receipt_path
        checks = {
            "terminalSubjectExact": observed_identity == self.spec.identity,
            "outputTreeDigestExact": (
                terminal.get("outputDigest") == self.output_tree_digest
            ),
            "receiptPathExact": requested_path == self.fixture.receipt_path,
        }
        receipt = {
            "schema": "meshshot.agent-boundary.synthetic-terminal-receipt/1",
            "jobId": self.spec.identity.job_id,
            "expectedSubjectDigest": expected_subject_digest,
            "observedTerminalIdentityDigest": self.observed_terminal_identity_digest,
            "observedOutputTreeDigest": self.output_tree_digest,
            "terminal": dict(terminal),
            "outerValidation": checks,
            "requestedReceiptPathDigest": "sha256:" + hashlib.sha256(
                requested_path.name.encode("utf-8"),
            ).hexdigest(),
            "status": "accepted" if all(checks.values()) else "rejected",
        }
        encoded = json.dumps(
            receipt, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        descriptor = os.open(
            self.fixture.receipt_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o400,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self.receipt_bytes = encoded
        if not all(checks.values()):
            raise boundary.BoundaryFailure("terminal-publication")

    def read_record(self, owned_id: str, phase: str) -> Mapping[str, object]:
        if phase == "preflight":
            self._start_resource("broker")
            self.calls.append("read-preflight")
            key = self.broker_key_override or self.provisioned_secret
            assert key is not None
            challenge = self.challenge_override or self.challenge
            assert challenge is not None
            return record(
                "meshshot.agent-boundary.preflight/2", self.spec.identity,
                challenge=challenge,
                brokerMac=broker_mac(
                    key, self.spec.identity, challenge,
                ),
            )
        if phase == "terminal":
            self.at_terminal.set()
            if self.release is not None and not self.release.wait(timeout=10):
                raise RuntimeError("test release timed out")
            output = self.spec.paths.output / "subject.json"
            output.write_text(
                json.dumps(self.spec.identity.as_json(), sort_keys=True),
                encoding="utf-8",
            )
            self.output_tree_digest = canonical_tree_digest(
                self.spec.paths.output,
            ).value
            if self.residual_preview:
                self._start_resource("sidecar")
            self.calls.append("read-terminal")
            identity = self.terminal_identity_override or self.spec.identity
            terminal = record(
                "meshshot.agent-boundary.terminal/4", identity,
                workloadStatus=self.terminal_status,
                outputDigest=(
                    self.output_digest_override or self.output_tree_digest
                ),
                processGroupAbsent=self.process_group_absent,
                descendantResidue=self.descendant_residue,
                interruptedSignal=self.interrupted_signal,
            )
            self._persist_terminal_receipt(terminal)
            return terminal
        return super().read_record(owned_id, phase)

    def cleanup_broker_volume(self, volume: str, owner_nonce: str) -> None:
        self.calls.append("cleanup-broker-volume")
        if volume != self.spec.broker_volume or owner_nonce != self.spec.identity.owner_nonce:
            raise RuntimeError("foreign Broker cleanup authority")
        failures = []
        for role in ("sidecar", "broker"):
            process = self.resource_processes.get(role)
            if process is not None:
                try:
                    self._stop_process(process)
                except Exception as exc:  # pragma: no cover - injected boundary
                    failures.append(exc)
        shutil.rmtree(self.fixture.volume_root)
        if failures:
            raise failures[0]

    def cleanup_private_tree(self, root: Path, owner_nonce: str) -> None:
        self.calls.append("cleanup-private-tree")
        if root != self.spec.paths.source.parent or owner_nonce != self.spec.identity.owner_nonce:
            raise RuntimeError("foreign tree cleanup authority")
        if not self.retain_tree:
            shutil.rmtree(root)

    def prove_broker_volume_absence(self, volume: str, owner_nonce: str) -> bool:
        self.calls.append("prove-broker-volume-absence")
        broker = self.resource_processes.get("broker")
        sidecar = self.resource_processes.get("sidecar")
        self.broker_process_absent = broker is None or broker.poll() is not None
        self.sidecar_process_absent = sidecar is None or sidecar.poll() is not None
        return (
            self.broker_process_absent and self.sidecar_process_absent
            and not self.fixture.volume_root.exists()
        )

    def prove_private_tree_absence(self, root: Path, owner_nonce: str) -> bool:
        self.calls.append("prove-private-tree-absence")
        return not root.exists()


def _shared_subject(spec: boundary.JobSpec) -> dict[str, str]:
    value = spec.identity.as_json()
    return {
        key: value[key]
        for key in SHARED_IDENTITY_KEYS
        if key in value
    } | {
        "cupRuntimeCapabilityManifestDigest": CUP_MANIFEST_DIGEST,
        "verificationPlanDigest": VERIFICATION_PLAN_DIGEST,
    }


def _execution_result(
    fixture_value: Fixture, adapter: FilesystemAdapter,
    receipt: boundary.LifecycleReceipt,
) -> dict[str, object]:
    identity = fixture_value.spec.identity
    subject_digest = "sha256:" + hashlib.sha256(json.dumps(
        identity.as_json(), sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    receipt_bytes = fixture_value.receipt_path.read_bytes()
    receipt_document = json.loads(receipt_bytes)
    return {
        "jobId": identity.job_id,
        "syntheticAuthorityPseudonym": "sha256:" + hashlib.sha256(
            identity.owner_nonce.encode("ascii"),
        ).hexdigest(),
        "brokerResourcePseudonym": "sha256:" + hashlib.sha256(
            fixture_value.spec.broker_volume.encode("utf-8"),
        ).hexdigest(),
        "executionSubjectDigest": subject_digest,
        "terminalReceiptSubjectDigest": adapter.observed_terminal_identity_digest,
        "outputTreeDigest": adapter.output_tree_digest,
        "receiptOutputTreeDigest": receipt_document["observedOutputTreeDigest"],
        "receiptDigest": "sha256:" + hashlib.sha256(receipt_bytes).hexdigest(),
        "receiptImmutableMode": oct(stat.S_IMODE(
            fixture_value.receipt_path.stat().st_mode,
        )),
        "receiptSupervisorOwned": (
            fixture_value.receipt_path.stat().st_uid == os.getuid()
            and fixture_value.receipt_path.parent.stat().st_uid == os.getuid()
            and stat.S_IMODE(
                fixture_value.receipt_path.parent.stat().st_mode,
            ) == 0o700
        ),
        "status": receipt.status,
        "failureCheck": receipt.failure_check,
        "workloadReleased": receipt.workload_released,
        "cleanupAbsence": receipt.absence_proved,
        "previewRequested": adapter.residual_preview,
        "brokerStartedAtPreflight": adapter.broker_started,
        "brokerIdentityMarkerExact": adapter.resource_identity_verified.get(
            "broker", False,
        ),
        "brokerProcessAbsent": adapter.broker_process_absent,
        "sidecarStarted": adapter.sidecar_started,
        "sidecarIdentityMarkerExact": adapter.resource_identity_verified.get(
            "sidecar", not adapter.residual_preview,
        ),
        "startedResourcesDistinct": len({
            process.pid for process in adapter.resource_processes.values()
        }) == len(adapter.resource_processes),
        "sidecarProcessAbsent": adapter.sidecar_process_absent,
        "outerTerminalValidation": receipt_document["outerValidation"],
    }


def admission_case(root: Path) -> dict[str, object]:
    store, receipt_root = authority_group(root)
    fixtures = [
        fixture(root / f"execution-{index}", index, store, receipt_root)
        for index in range(5)
    ]
    controller = concurrency.AdmissionController()
    releases = [threading.Event() for _ in fixtures]
    adapters = [
        FilesystemAdapter(item, releases[index], residual_preview=index in (1, 4))
        for index, item in enumerate(fixtures)
    ]
    futures: list[Future[boundary.LifecycleReceipt]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        try:
            for index in range(4):
                futures.append(pool.submit(
                    controller.run, fixtures[index].spec.identity.job_id,
                    lambda index=index: boundary.run_job(
                        fixtures[index].spec, adapters[index], store,
                    ),
                ))
                controller.wait_until(
                    lambda state, index=index: len(state.active) == index + 1,
                )
                if not adapters[index].at_terminal.wait(timeout=10):
                    raise TimeoutError("active execution did not reach terminal gate")
            futures.append(pool.submit(
                controller.run, fixtures[4].spec.identity.job_id,
                lambda: boundary.run_job(
                    fixtures[4].spec, adapters[4], store,
                ),
            ))
            controller.wait_until(lambda state: state.queued == ("job-4",))
            fifth_queued_at_cap = (
                controller.snapshot() == concurrency.AdmissionSnapshot(
                    ("job-0", "job-1", "job-2", "job-3"), ("job-4",), 4,
                )
            )
            queued_fifth_has_no_process = (
                not adapters[4].resource_processes
                and not adapters[4].broker_started
                and not adapters[4].sidecar_started
            )
            releases[0].set()
            controller.wait_until(lambda state: "job-4" in state.active)
            if not adapters[4].at_terminal.wait(timeout=10):
                raise TimeoutError("fifth execution did not reach terminal gate")
            released_slot_after_absence = (
                adapters[0].broker_process_absent is True
                and adapters[0].sidecar_process_absent is True
                and not fixtures[0].volume_root.exists()
            )
            for release in releases[1:]:
                release.set()
            receipts = [future.result(timeout=10) for future in futures]
        finally:
            for release in releases:
                release.set()
            for adapter in adapters:
                adapter.force_stop_resources()
    shared = _shared_subject(fixtures[0].spec)
    shared_only = all(_shared_subject(item.spec) == shared for item in fixtures)
    private_authority = all(len(set(values)) == len(fixtures) for values in (
        [item.spec.identity.owner_nonce for item in fixtures],
        [item.spec.broker_secret for item in fixtures],
        [item.spec.challenge for item in fixtures],
        [str(item.spec.paths.source.parent) for item in fixtures],
        [item.spec.broker_volume for item in fixtures],
    )) and len({
        str(path) for item in fixtures for path in item.spec.paths.all_paths()
    }) == len(fixtures) * 8
    results = sorted(
        (_execution_result(item, adapter, receipt)
         for item, adapter, receipt in zip(fixtures, adapters, receipts)),
        key=lambda value: str(value["jobId"]),
    )
    replay_adapter = FilesystemAdapter(fixtures[0])
    replay_receipt = boundary.run_job(fixtures[0].spec, replay_adapter, store)
    replay_before_admission_or_resource = (
        replay_receipt.failure_check == "authority-replay"
        and replay_adapter.calls == ["prove-owner-label-absence"]
        and not replay_adapter.resource_processes
    )
    return {
        "case": "five-job-admission",
        "activeCap": concurrency.FIRST_RELEASE_ACTIVE_CAP,
        "fifthQueuedAtCap": fifth_queued_at_cap,
        "queuedFifthHasNoProcess": queued_fifth_has_no_process,
        "slotReleasedAfterProcessAbsence": released_slot_after_absence,
        "observedPeak": controller.snapshot().observed_peak,
        "sharedImmutableIdentitiesOnly": shared_only,
        "privateExecutionAuthority": private_authority,
        "allReceiptsExactAndAbsent": all(
            receipt.status == "succeeded" and receipt.absence_proved
            and result["executionSubjectDigest"]
            == result["terminalReceiptSubjectDigest"]
            and result["outputTreeDigest"] == result["receiptOutputTreeDigest"]
            and result["receiptImmutableMode"] == "0o400"
            and result["receiptSupervisorOwned"] is True
            and all(result["outerTerminalValidation"].values())
            for receipt, result in zip(receipts, results)
        ),
        "replayRejectedBeforeAdmissionOrResource": replay_before_admission_or_resource,
        "sharedAuthorityStoreClosed": (
            len({str(item.store.root) for item in fixtures}) == 1
            and len(list(store.root.glob("*.consumed"))) == 5
            and not list(store.root.glob("*.claimed"))
        ),
        "sharedSubject": shared,
        "executions": results,
    }


def failure_isolation_case(root: Path) -> dict[str, object]:
    store, receipt_root = authority_group(root)
    fixtures = [
        fixture(root / f"failure-{index}", index + 6, store, receipt_root)
        for index in range(4)
    ]
    releases = [threading.Event() for _ in fixtures]
    adapters = [
        FilesystemAdapter(item, releases[index], retain_tree=index == 0)
        for index, item in enumerate(fixtures)
    ]
    adapters[0].terminal_status = 9
    controller = concurrency.AdmissionController()
    with ThreadPoolExecutor(max_workers=4) as pool:
        try:
            futures = [pool.submit(
                controller.run, item.spec.identity.job_id,
                lambda index=index: boundary.run_job(
                    fixtures[index].spec, adapters[index], store,
                ),
            ) for index, item in enumerate(fixtures)]
            controller.wait_until(lambda state: len(state.active) == 4)
            for adapter in adapters:
                if not adapter.at_terminal.wait(timeout=10):
                    raise TimeoutError("failure-isolation job missed terminal gate")
            for release in releases:
                release.set()
            receipts = [future.result(timeout=10) for future in futures]
        finally:
            for release in releases:
                release.set()
            for adapter in adapters:
                adapter.force_stop_resources()
    failed_root = fixtures[0].spec.paths.source.parent
    other_roots = [item.spec.paths.source.parent for item in fixtures[1:]]
    receipt_bytes = [item.receipt_path.read_bytes() for item in fixtures]
    receipt_documents = [json.loads(value) for value in receipt_bytes]
    receipt_hashes = [hashlib.sha256(value).hexdigest() for value in receipt_bytes]
    receipt_mappings_exact = all(
        document["observedTerminalIdentityDigest"]
        == FilesystemAdapter._mapping_digest(item.spec.identity.as_json())
        and document["observedOutputTreeDigest"] == adapter.output_tree_digest
        and document["terminal"]["outputDigest"] == adapter.output_tree_digest
        and all(document["outerValidation"].values())
        for document, item, adapter in zip(
            receipt_documents, fixtures, adapters,
        )
    )
    exclusive_rewrite_rejected = True
    for item in fixtures:
        try:
            descriptor = os.open(
                item.receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400,
            )
        except FileExistsError:
            continue
        else:  # pragma: no cover - contract failure
            os.close(descriptor)
            exclusive_rewrite_rejected = False
    return {
        "case": "one-residue-does-not-falsify-peers",
        "failedJobFailureCheck": receipts[0].failure_check,
        "failedJobRetained": failed_root.exists(),
        "peerStatuses": [receipt.status for receipt in receipts[1:]],
        "peerRootsAbsent": all(not path.exists() for path in other_roots),
        "allProcessesAbsent": all(adapter.processes_absent() for adapter in adapters),
        "durableReceiptCount": len(receipt_bytes),
        "durableReceiptsDistinct": len(set(receipt_hashes)) == 4,
        "durableReceiptsImmutable": (
            exclusive_rewrite_rejected and all(
                stat.S_IMODE(item.receipt_path.stat().st_mode) == 0o400
                for item in fixtures
            )
        ),
        "durableReceiptsSupervisorOwned": all(
            item.receipt_path.stat().st_uid == os.getuid()
            and item.receipt_path.parent.stat().st_uid == os.getuid()
            and stat.S_IMODE(item.receipt_path.parent.stat().st_mode) == 0o700
            for item in fixtures
        ),
        "receiptOutputMappingsExact": receipt_mappings_exact,
        "retainedOutputStillMatchesReceipt": (
            canonical_tree_digest(fixtures[0].spec.paths.output).value
            == receipt_documents[0]["observedOutputTreeDigest"]
        ),
        "observedPeak": controller.snapshot().observed_peak,
    }


def substitutions_case(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    store, receipt_root = authority_group(root)
    names = (
        "owner", "secret", "challenge", "broker-volume", "source", "input",
        "output", "terminal-subject", "output-digest", "receipt-path",
    )
    expected = {
        "owner": "container-ownership", "secret": "broker-proof",
        "challenge": "broker-proof", "broker-volume": "inert-container",
        "source": "job-private-layout", "input": "job-private-layout",
        "output": "job-private-layout",
        "terminal-subject": "terminal-publication",
        "output-digest": "terminal-publication",
        "receipt-path": "terminal-publication",
    }
    for index, name in enumerate(names):
        left = fixture(
            root / f"substitution-{index}-left", index + 10,
            store, receipt_root,
        )
        right = fixture(
            root / f"substitution-{index}-right", index + 30,
            store, receipt_root,
        )
        (right.spec.paths.output / "foreign-output.txt").write_text(
            "foreign-output", encoding="utf-8",
        )
        foreign_receipt = b"foreign-supervisor-receipt\n"
        descriptor = os.open(
            right.receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(foreign_receipt)
        foreign_output_before = canonical_tree_digest(
            right.spec.paths.output,
        ).value
        foreign_receipt_before = hashlib.sha256(foreign_receipt).hexdigest()
        spec = left.spec
        adapter = FilesystemAdapter(left)
        if name == "owner":
            adapter.container = replace(
                adapter.container, owner_nonce=right.spec.identity.owner_nonce,
            )
        elif name == "secret":
            adapter.broker_key_override = right.spec.broker_secret
        elif name == "challenge":
            adapter.challenge_override = right.spec.challenge
        elif name == "broker-volume":
            adapter.container = replace(
                adapter.container, broker_volume=right.spec.broker_volume,
            )
        elif name in {"source", "input", "output"}:
            spec = replace(
                spec, paths=replace(
                    spec.paths, **{name: getattr(right.spec.paths, name)},
                ),
            )
            adapter.spec = spec
        elif name == "terminal-subject":
            adapter.terminal_identity_override = right.spec.identity
        elif name == "output-digest":
            adapter.output_digest_override = foreign_output_before
        elif name == "receipt-path":
            adapter.receipt_path_override = right.receipt_path
        try:
            receipt = boundary.run_job(spec, adapter, store)
        finally:
            adapter.force_stop_resources()
        foreign_preserved = (
            right.spec.paths.source.parent.exists() and right.volume_root.exists()
            and canonical_tree_digest(right.spec.paths.output).value
            == foreign_output_before
            and hashlib.sha256(right.receipt_path.read_bytes()).hexdigest()
            == foreign_receipt_before
        )
        rows.append({
            "substitution": name,
            "failureCheck": str(receipt.failure_check),
            "foreignResourcesPreserved": foreign_preserved,
            "rejectedTerminalReceiptPersisted": left.receipt_path.exists(),
            "allStartedProcessesAbsent": adapter.processes_absent(),
            "verdict": (
                "PASS" if receipt.failure_check == expected[name]
                and foreign_preserved else "FAIL"
            ),
        })
    return {"case": "cross-job-substitution", "rows": rows}


def matrix() -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for name in ("admission", "failure", "substitution"):
            (root / name).mkdir()
        admission = admission_case(root / "admission")
        failure = failure_isolation_case(root / "failure")
        substitutions = substitutions_case(root / "substitution")
    checks = {
        "hardCapFour": admission["observedPeak"] == 4,
        "fifthQueuedUntilRelease": admission["fifthQueuedAtCap"] is True,
        "queuedExecutionHasNoLiveResource": admission["queuedFifthHasNoProcess"] is True,
        "slotReleaseFollowsResourceAbsence": (
            admission["slotReleasedAfterProcessAbsence"] is True
        ),
        "authorityReplayRejectedBeforeAdmission": (
            admission["replayRejectedBeforeAdmissionOrResource"] is True
            and admission["sharedAuthorityStoreClosed"] is True
        ),
        "onlyImmutableIdentitiesShared": admission["sharedImmutableIdentitiesOnly"] is True,
        "executionAuthorityPrivate": admission["privateExecutionAuthority"] is True,
        "exactSubjectAndCleanup": admission["allReceiptsExactAndAbsent"] is True,
        "previewLifecycleLazyAndPrivate": [
            (
                row["previewRequested"], row["brokerStartedAtPreflight"],
                row["sidecarStarted"], row["brokerProcessAbsent"],
                row["sidecarProcessAbsent"], row["brokerIdentityMarkerExact"],
                row["sidecarIdentityMarkerExact"], row["startedResourcesDistinct"],
            )
            for row in admission["executions"]
        ] == [
            (False, True, False, True, True, True, True, True),
            (True, True, True, True, True, True, True, True),
            (False, True, False, True, True, True, True, True),
            (False, True, False, True, True, True, True, True),
            (True, True, True, True, True, True, True, True),
        ],
        "oneFailureCannotFalsifyPeers": (
            failure["failedJobFailureCheck"] == "retained-resource"
            and failure["failedJobRetained"] is True
            and failure["peerStatuses"] == ["succeeded"] * 3
            and failure["peerRootsAbsent"] is True
            and failure["allProcessesAbsent"] is True
            and failure["durableReceiptCount"] == 4
            and failure["durableReceiptsDistinct"] is True
            and failure["durableReceiptsImmutable"] is True
            and failure["durableReceiptsSupervisorOwned"] is True
            and failure["receiptOutputMappingsExact"] is True
            and failure["retainedOutputStillMatchesReceipt"] is True
        ),
        "allCrossJobSubstitutionsRejected": all(
            row["verdict"] == "PASS"
            and row["allStartedProcessesAbsent"] is True
            for row in substitutions["rows"]
        ),
    }
    return {
        "schema": SCHEMA,
        "verdict": "ADOPT_FIRST_RELEASE_CAP_FOUR" if all(checks.values()) else "REJECT",
        "checks": checks,
        "admission": admission,
        "failureIsolation": failure,
        "substitutions": substitutions,
        "dockerAuthorityInAgent": False,
        "authorityGeneration": "SYNTHETIC_DETERMINISTIC_TEST_ONLY",
        "cryptographicRandomnessSampled": False,
        "realOciContainers": "NOT_RUN",
        "colimaConformance": "NOT_RUN",
        "cvmConformance": "NOT_RUN",
        "agentRuntimeVerified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("matrix",))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "matrix":
        encoded = json.dumps(
            matrix(), sort_keys=True, separators=(",", ":"),
        ) + "\n"
        if args.output is None:
            print(encoded, end="")
        else:
            args.output.write_text(encoded, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
