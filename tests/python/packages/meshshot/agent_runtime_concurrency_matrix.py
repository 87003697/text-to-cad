#!/usr/bin/env python3
"""THROWAWAY real-filesystem concurrency evidence for SAR-007."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import shutil
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


def fixture(root: Path, index: int) -> Fixture:
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
    store_root = root / "authority-store"
    store_root.mkdir(mode=0o700)
    store = FileAuthorityStore(store_root)
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
    return Fixture(spec, store, volume_root)


class FilesystemAdapter(ScriptedAdapter):
    """Injected lifecycle adapter with real private files and bounded gates."""

    def __init__(
        self, fixture_value: Fixture, release: threading.Event | None = None,
        residual_preview: bool = False, retain_tree: bool = False,
    ) -> None:
        super().__init__(fixture_value.spec)
        self.fixture = fixture_value
        self.release = release
        self.residual_preview = residual_preview
        self.retain_tree = retain_tree
        self.preview_broker_started = False
        self.sidecar_started = False
        self.preview_owner: str | None = None
        self.preview_secret_digest: str | None = None
        self.output_subject_digest: str | None = None
        self.terminal_identity_override: ExecutionIdentity | None = None
        self.challenge_override: str | None = None

    def read_record(self, owned_id: str, phase: str) -> Mapping[str, object]:
        if phase == "preflight" and self.challenge_override is not None:
            self.calls.append("read-preflight")
            key = self.broker_key_override or self.provisioned_secret
            assert key is not None
            return record(
                "meshshot.agent-boundary.preflight/2", self.spec.identity,
                challenge=self.challenge_override,
                brokerMac=broker_mac(
                    key, self.spec.identity, self.challenge_override,
                ),
            )
        if phase == "terminal":
            if self.release is not None and not self.release.wait(timeout=10):
                raise RuntimeError("test release timed out")
            output = self.spec.paths.output / "subject.json"
            output.write_text(
                json.dumps(self.spec.identity.as_json(), sort_keys=True),
                encoding="utf-8",
            )
            self.output_subject_digest = "sha256:" + hashlib.sha256(
                output.read_bytes(),
            ).hexdigest()
            if self.residual_preview:
                self.preview_broker_started = True
                self.sidecar_started = True
                self.preview_owner = self.spec.identity.owner_nonce
                assert self.provisioned_secret is not None
                self.preview_secret_digest = hashlib.sha256(
                    self.provisioned_secret,
                ).hexdigest()
                (self.fixture.volume_root / "preview-owner").write_text(
                    self.preview_owner, encoding="utf-8",
                )
            self.calls.append("read-terminal")
            identity = self.terminal_identity_override or self.spec.identity
            return record(
                "meshshot.agent-boundary.terminal/4", identity,
                workloadStatus=self.terminal_status,
                outputDigest=self.output_subject_digest,
                processGroupAbsent=self.process_group_absent,
                descendantResidue=self.descendant_residue,
                interruptedSignal=self.interrupted_signal,
            )
        return super().read_record(owned_id, phase)

    def cleanup_broker_volume(self, volume: str, owner_nonce: str) -> None:
        self.calls.append("cleanup-broker-volume")
        if volume != self.spec.broker_volume or owner_nonce != self.spec.identity.owner_nonce:
            raise RuntimeError("foreign Broker cleanup authority")
        shutil.rmtree(self.fixture.volume_root)

    def cleanup_private_tree(self, root: Path, owner_nonce: str) -> None:
        self.calls.append("cleanup-private-tree")
        if root != self.spec.paths.source.parent or owner_nonce != self.spec.identity.owner_nonce:
            raise RuntimeError("foreign tree cleanup authority")
        if not self.retain_tree:
            shutil.rmtree(root)

    def prove_broker_volume_absence(self, volume: str, owner_nonce: str) -> bool:
        self.calls.append("prove-broker-volume-absence")
        return not self.fixture.volume_root.exists()

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
    return {
        "jobId": identity.job_id,
        "ownerNonce": identity.owner_nonce,
        "brokerVolume": fixture_value.spec.broker_volume,
        "executionSubjectDigest": subject_digest,
        "terminalReceiptSubjectDigest": subject_digest,
        "outputSubjectDigest": adapter.output_subject_digest,
        "status": receipt.status,
        "failureCheck": receipt.failure_check,
        "workloadReleased": receipt.workload_released,
        "cleanupAbsence": receipt.absence_proved,
        "previewRequested": adapter.residual_preview,
        "previewBrokerStarted": adapter.preview_broker_started,
        "sidecarStarted": adapter.sidecar_started,
        "sidecarOwnerNonce": adapter.preview_owner,
        "previewAuthorityDigest": adapter.preview_secret_digest,
    }


def admission_case(root: Path) -> dict[str, object]:
    fixtures = [fixture(root / f"execution-{index}", index) for index in range(5)]
    controller = concurrency.AdmissionController()
    releases = [threading.Event() for _ in fixtures]
    adapters = [
        FilesystemAdapter(item, releases[index], residual_preview=index in (1, 4))
        for index, item in enumerate(fixtures)
    ]
    futures: list[Future[boundary.LifecycleReceipt]] = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        for index in range(4):
            futures.append(pool.submit(
                controller.run, fixtures[index].spec.identity.job_id,
                lambda index=index: boundary.run_job(
                    fixtures[index].spec, adapters[index], fixtures[index].store,
                ),
            ))
            controller.wait_until(lambda state, index=index: len(state.active) == index + 1)
        futures.append(pool.submit(
            controller.run, fixtures[4].spec.identity.job_id,
            lambda: boundary.run_job(
                fixtures[4].spec, adapters[4], fixtures[4].store,
            ),
        ))
        controller.wait_until(lambda state: state.queued == ("job-4",))
        fifth_queued_at_cap = controller.snapshot() == concurrency.AdmissionSnapshot(
            ("job-0", "job-1", "job-2", "job-3"), ("job-4",), 4,
        )
        releases[0].set()
        controller.wait_until(lambda state: "job-4" in state.active)
        for release in releases[1:]:
            release.set()
        receipts = [future.result(timeout=10) for future in futures]
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
    return {
        "case": "five-job-admission",
        "activeCap": concurrency.FIRST_RELEASE_ACTIVE_CAP,
        "fifthQueuedAtCap": fifth_queued_at_cap,
        "observedPeak": controller.snapshot().observed_peak,
        "sharedImmutableIdentitiesOnly": shared_only,
        "privateExecutionAuthority": private_authority,
        "allReceiptsExactAndAbsent": all(
            receipt.status == "succeeded" and receipt.absence_proved
            and result["executionSubjectDigest"]
            == result["terminalReceiptSubjectDigest"]
            and result["outputSubjectDigest"] is not None
            for receipt, result in zip(receipts, results)
        ),
        "sharedSubject": shared,
        "executions": results,
    }


def failure_isolation_case(root: Path) -> dict[str, object]:
    fixtures = [fixture(root / f"failure-{index}", index + 6) for index in range(4)]
    releases = [threading.Event() for _ in fixtures]
    adapters = [
        FilesystemAdapter(item, releases[index], retain_tree=index == 0)
        for index, item in enumerate(fixtures)
    ]
    adapters[0].terminal_status = 9
    controller = concurrency.AdmissionController()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(
            controller.run, item.spec.identity.job_id,
            lambda index=index: boundary.run_job(
                fixtures[index].spec, adapters[index], fixtures[index].store,
            ),
        ) for index, item in enumerate(fixtures)]
        controller.wait_until(lambda state: len(state.active) == 4)
        for release in releases:
            release.set()
        receipts = [future.result(timeout=10) for future in futures]
    failed_root = fixtures[0].spec.paths.source.parent
    other_roots = [item.spec.paths.source.parent for item in fixtures[1:]]
    return {
        "case": "one-residue-does-not-falsify-peers",
        "failedJobFailureCheck": receipts[0].failure_check,
        "failedJobRetained": failed_root.exists(),
        "peerStatuses": [receipt.status for receipt in receipts[1:]],
        "peerRootsAbsent": all(not path.exists() for path in other_roots),
        "observedPeak": controller.snapshot().observed_peak,
    }


def substitutions_case(root: Path) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    names = (
        "owner", "secret", "challenge", "broker-volume", "source", "input",
        "output", "receipt",
    )
    expected = {
        "owner": "container-ownership", "secret": "broker-proof",
        "challenge": "broker-proof", "broker-volume": "inert-container",
        "source": "job-private-layout", "input": "job-private-layout",
        "output": "job-private-layout", "receipt": "terminal-publication",
    }
    for index, name in enumerate(names):
        left = fixture(root / f"substitution-{index}-left", index + 10)
        right = fixture(root / f"substitution-{index}-right", index + 20)
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
        elif name == "receipt":
            adapter.terminal_identity_override = right.spec.identity
        receipt = boundary.run_job(spec, adapter, left.store)
        foreign_preserved = (
            right.spec.paths.source.parent.exists() and right.volume_root.exists()
        )
        rows.append({
            "substitution": name,
            "failureCheck": str(receipt.failure_check),
            "foreignResourcesPreserved": foreign_preserved,
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
        "onlyImmutableIdentitiesShared": admission["sharedImmutableIdentitiesOnly"] is True,
        "executionAuthorityPrivate": admission["privateExecutionAuthority"] is True,
        "exactSubjectAndCleanup": admission["allReceiptsExactAndAbsent"] is True,
        "previewLifecycleLazyAndPrivate": [
            (row["previewRequested"], row["previewBrokerStarted"], row["sidecarStarted"])
            for row in admission["executions"]
        ] == [
            (False, False, False), (True, True, True), (False, False, False),
            (False, False, False), (True, True, True),
        ],
        "oneFailureCannotFalsifyPeers": (
            failure["failedJobFailureCheck"] == "retained-resource"
            and failure["failedJobRetained"] is True
            and failure["peerStatuses"] == ["succeeded"] * 3
            and failure["peerRootsAbsent"] is True
        ),
        "allCrossJobSubstitutionsRejected": all(
            row["verdict"] == "PASS" for row in substitutions["rows"]
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
