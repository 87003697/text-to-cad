#!/usr/bin/env python3
"""THROWAWAY executable RED/GREEN evidence harness for SAR-003."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from typing import Mapping


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
PROTOTYPE = REPO / "packages/meshshot/prototypes/agent_runtime_boundary"
if str(PROTOTYPE) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE))
from authority import AuthorityAllocator, FileAuthorityStore  # noqa: E402
import boundary  # noqa: E402
from contract import (  # noqa: E402
    Digest, ExecutionIdentity, ExecutionRequest, broker_mac,
    canonical_tree_digest,
)


RECEIPT_SCHEMA = "meshshot.agent-boundary.prototype-matrix/4"


def record(
    schema: str, identity: ExecutionIdentity, **extra: object,
) -> dict[str, object]:
    return {"schema": schema, **identity.as_json(), **extra}


class ScriptedAdapter:
    """Injectable Docker/attach/cleanup fake for the public lifecycle."""
    def __init__(self, spec: boundary.JobSpec) -> None:
        self.spec = spec
        self.calls: list[str] = []
        self.resource_id = "a" * 64
        self.returned_id = self.resource_id
        self.image: boundary.ImageObservation | None = boundary.ImageObservation(
            frozenset({spec.image.manifest_digest.value}),
            spec.image.config_digest.value,
            spec.image.runtime_manifest_digest.value,
        )
        self.container = boundary.ContainerObservation(
            self.resource_id, spec.image.config_digest.value,
            spec.identity.owner_nonce, spec.identity.job_id, False, True, "none",
            ("ALL",), True, "1000:1000", 256, 4 * 1024**3, 4 * 1024**3,
            2_000_000_000, boundary.AGENT_ENV,
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
        self.provisioned_secret: bytes | None = None
        self.broker_key_override: bytes | None = None
        self.fail_read: str | None = None
        self.terminal_status = 0
        self.process_group_absent = True
        self.descendant_residue = False
        self.interrupted_signal: int | None = None
        self.challenge: str | None = None
        self.container_absent = True
        self.owner_absent = True
        self.broker_absent = True
        self.tree_absent = True
        self.browser_scan_roots: tuple[Path, ...] = ()

    def inspect_image(self, reference: str):
        self.calls.append("inspect-image")
        return self.image

    def provision_broker_secret(
        self, identity: ExecutionIdentity, secret: bytes,
    ) -> None:
        self.calls.append("provision-broker-secret")
        self.provisioned_secret = secret

    def create_inert(self, argv: list[str]) -> str:
        self.calls.append("create-inert")
        return self.returned_id

    def inspect_container(self, candidate_id: str):
        self.calls.append("inspect-container")
        return self.container

    def start(self, owned_id: str) -> None:
        self.calls.append("start-entrypoint")

    def read_record(self, owned_id: str, phase: str) -> Mapping[str, object]:
        self.calls.append("read-" + phase)
        if self.fail_read == phase:
            raise boundary.BoundaryFailure(
                "entrypoint" if phase == "ready" else "terminal-publication",
            )
        if phase == "ready":
            if self.browser_scan_roots and discover_browser_artifacts(
                self.browser_scan_roots,
            ):
                raise boundary.BoundaryFailure("browser-deny")
            return record("meshshot.agent-boundary.ready/1", self.spec.identity)
        if phase == "preflight":
            assert self.challenge is not None
            key = self.broker_key_override or self.provisioned_secret
            assert key is not None
            return record(
                "meshshot.agent-boundary.preflight/2", self.spec.identity,
                challenge=self.challenge,
                brokerMac=broker_mac(key, self.spec.identity, self.challenge),
            )
        if phase == "terminal":
            return record(
                "meshshot.agent-boundary.terminal/4", self.spec.identity,
                workloadStatus=self.terminal_status,
                outputDigest="sha256:" + "f" * 64,
                processGroupAbsent=self.process_group_absent,
                descendantResidue=self.descendant_residue,
                interruptedSignal=self.interrupted_signal,
            )
        raise AssertionError(phase)

    def write_record(
        self, owned_id: str, phase: str, value: Mapping[str, object],
    ) -> None:
        self.calls.append("write-" + phase)
        if phase == "challenge":
            self.challenge = str(value["challenge"])

    def remove_exact(self, owned_id: str) -> None:
        self.calls.append("remove-exact")

    def cleanup_broker_volume(self, volume: str, owner_nonce: str) -> None:
        self.calls.append("cleanup-broker-volume")

    def cleanup_private_tree(self, root: Path, owner_nonce: str) -> None:
        self.calls.append("cleanup-private-tree")

    def prove_container_absence(self, owned_id: str, owner_nonce: str) -> bool:
        self.calls.append("prove-container-absence")
        return self.container_absent

    def prove_owner_label_absence(self, owner_nonce: str) -> bool:
        self.calls.append("prove-owner-label-absence")
        return self.owner_absent

    def prove_broker_volume_absence(
        self, volume: str, owner_nonce: str,
    ) -> bool:
        self.calls.append("prove-broker-volume-absence")
        return self.broker_absent

    def prove_private_tree_absence(self, root: Path, owner_nonce: str) -> bool:
        self.calls.append("prove-private-tree-absence")
        return self.tree_absent


@dataclass(frozen=True)
class Fixture:
    spec: boundary.JobSpec
    store: FileAuthorityStore


class DeterministicTokens:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, size: int) -> str:
        self.calls += 1
        marker = {1: "b", 2: "d", 3: "c"}[self.calls]
        return marker * (size * 2)


def fixture_spec(root: Path) -> Fixture:
    root = root.resolve(strict=True)
    workload = ("/opt/text-to-cad/bin/agent", "--fixed")
    staging_source = root / "staging-source"
    staging_input = root / "staging-input"
    staging_source.mkdir()
    staging_input.mkdir()
    (staging_source / "source.txt").write_text("source", encoding="utf-8")
    (staging_input / "input.bin").write_bytes(b"input")
    request = ExecutionRequest(
        "job-a", Digest("sha256:" + "1" * 64),
        Digest("sha256:" + "2" * 64), Digest("sha256:" + "3" * 64),
        canonical_tree_digest(staging_source), canonical_tree_digest(staging_input),
        Digest("sha256:" + "4" * 64), workload,
    )
    grant = AuthorityAllocator(DeterministicTokens()).allocate(request)
    store_root = root / "authority-store"
    store_root.mkdir(mode=0o700)
    store = FileAuthorityStore(store_root)
    claimed = store.claim(grant)
    identity = grant.identity
    job_root = root / boundary.resource_stem(identity)
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
    assert canonical_tree_digest(paths["source"]) == identity.source_digest
    assert canonical_tree_digest(paths["input"]) == identity.input_digest
    image = boundary.ImageExpectation(
        "example.invalid/agent@" + identity.agent_image_digest.value,
        identity.agent_image_digest, identity.agent_config_digest,
        identity.runtime_manifest_digest,
    )
    return Fixture(boundary.JobSpec(
        claimed, image, boundary.JobPaths(**paths),
        boundary.resource_stem(identity),
        boundary.resource_stem(identity) + "-broker",
        workload,
    ), store)


def reclaim_identity(
    fixture: Fixture, identity: ExecutionIdentity,
) -> Fixture:
    grant = replace(fixture.spec.authority.grant, identity=identity)
    claimed = fixture.store.claim(grant)
    return Fixture(replace(fixture.spec, authority=claimed), fixture.store)


def unsafe_red(adapter: ScriptedAdapter) -> tuple[str, ...]:
    candidate = adapter.create_inert(["unsafe"])
    adapter.start(candidate)
    adapter.write_record(candidate, "release", {"schema": "unsafe"})
    adapter.inspect_image("mutable")
    adapter.inspect_container(candidate)
    return tuple(adapter.calls)


def discover_browser_artifacts(roots: tuple[Path, ...]) -> list[dict[str, str]]:
    scanner_path = REPO / "scripts/pilot/browser_surface.py"
    spec = importlib.util.spec_from_file_location("sar003_browser_surface", scanner_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    mounts = [(root, Path("/agent") / root.name, True) for root in roots]
    return module.discover_browser_roots(mounts, permitted_symlink_roots=roots)


CASES = (
    "success", "wrong_image_digest", "missing_image_digest",
    "wrong_image_config_digest", "returned_id_substitution",
    "lost_create_output", "authority_replay",
    "wrong_source_digest", "missing_source_digest", "invalid_workload",
    "substituted_workload", "writable_root", "writable_source",
    "writable_input", "docker_socket_exposure", "extra_network_route",
    "shared_job_home", "shared_job_socket", "shared_job_output",
    "partial_startup_before_verification", "entrypoint_failure",
    "terminal_publication_failure", "container_residue", "owner_label_residue",
    "broker_volume_residue", "private_tree_residue",
    "cross_job_authority_substitution", "renamed_browser_artifact",
    "descendant_process_residue", "interrupted_workload",
)


def matrix() -> dict[str, object]:
    rows = []
    with tempfile.TemporaryDirectory() as temporary:
        for index, case in enumerate(CASES):
            case_root = Path(temporary) / str(index)
            case_root.mkdir()
            fixture = fixture_spec(case_root)
            spec, store = fixture.spec, fixture.store
            adapter = ScriptedAdapter(spec)
            expected_failure: str | None = None
            expected_release = case in {
                "success", "terminal_publication_failure", "container_residue",
                "owner_label_residue", "broker_volume_residue",
                "private_tree_residue", "descendant_process_residue",
                "interrupted_workload",
            }
            if case == "wrong_image_digest":
                adapter.image = replace(
                    adapter.image,
                    manifest_digests=frozenset({"sha256:" + "9" * 64}),
                )
                expected_failure = "image-identity"
            elif case == "missing_image_digest":
                adapter.image = None
                expected_failure = "image-identity"
            elif case == "wrong_image_config_digest":
                adapter.image = replace(
                    adapter.image, config_digest="sha256:" + "9" * 64,
                )
                expected_failure = "image-identity"
            elif case == "returned_id_substitution":
                adapter.container = replace(adapter.container, resource_id="d" * 64)
                expected_failure = "container-ownership"
            elif case == "lost_create_output":
                adapter.returned_id = "lost"
                adapter.owner_absent = False
                expected_failure = "retained-resource"
            elif case == "authority_replay":
                first = boundary.run_job(spec, ScriptedAdapter(spec), store)
                assert first.status == "succeeded"
                expected_failure = "authority-replay"
            elif case == "wrong_source_digest":
                fixture = reclaim_identity(
                    fixture,
                    replace(
                        spec.identity,
                        source_digest=Digest("sha256:" + "9" * 64),
                    ),
                )
                spec, store = fixture.spec, fixture.store
                adapter = ScriptedAdapter(spec)
                expected_failure = "snapshot-identity"
            elif case == "missing_source_digest":
                spec.paths.source.rename(spec.paths.source.with_name("source-missing"))
                expected_failure = "snapshot-identity"
            elif case == "invalid_workload":
                spec = replace(spec, workload=("relative-command",))
                adapter = ScriptedAdapter(spec)
                expected_failure = "workload-identity"
            elif case == "substituted_workload":
                spec = replace(spec, workload=("/opt/text-to-cad/bin/other",))
                adapter = ScriptedAdapter(spec)
                expected_failure = "workload-identity"
            elif case == "writable_root":
                adapter.container = replace(adapter.container, read_only_root=False)
                expected_failure = "inert-container"
            elif case == "writable_source":
                adapter.container = replace(
                    adapter.container,
                    readonly_mounts=adapter.container.readonly_mounts
                    - {"/run/agent-job/source"},
                )
                expected_failure = "inert-container"
            elif case == "writable_input":
                adapter.container = replace(
                    adapter.container,
                    readonly_mounts=adapter.container.readonly_mounts
                    - {"/run/agent-job/input"},
                )
                expected_failure = "inert-container"
            elif case == "docker_socket_exposure":
                adapter.container = replace(adapter.container, docker_socket_exposed=True)
                expected_failure = "inert-container"
            elif case == "extra_network_route":
                adapter.container = replace(adapter.container, network_mode="bridge")
                expected_failure = "inert-container"
            elif case == "shared_job_home":
                spec = replace(spec, paths=replace(spec.paths, home=spec.paths.cache))
                adapter = ScriptedAdapter(spec)
                expected_failure = "job-private-layout"
            elif case == "shared_job_socket":
                spec = replace(spec, broker_volume="shared-broker")
                adapter = ScriptedAdapter(spec)
                expected_failure = "job-private-layout"
            elif case == "shared_job_output":
                spec = replace(spec, paths=replace(spec.paths, output=spec.paths.work))
                adapter = ScriptedAdapter(spec)
                expected_failure = "job-private-layout"
            elif case == "partial_startup_before_verification":
                adapter.container = replace(adapter.container, running=True)
                expected_failure = "inert-container"
            elif case == "entrypoint_failure":
                adapter.fail_read = "ready"
                expected_failure = "entrypoint"
            elif case == "terminal_publication_failure":
                adapter.fail_read = "terminal"
                expected_failure = "terminal-publication"
            elif case == "container_residue":
                adapter.container_absent = False
                expected_failure = "retained-resource"
            elif case == "owner_label_residue":
                adapter.owner_absent = False
                expected_failure = "retained-resource"
            elif case == "broker_volume_residue":
                adapter.broker_absent = False
                expected_failure = "retained-resource"
            elif case == "private_tree_residue":
                adapter.tree_absent = False
                expected_failure = "retained-resource"
            elif case == "cross_job_authority_substitution":
                adapter.broker_key_override = b"x" * 32
                expected_failure = "broker-proof"
            elif case == "renamed_browser_artifact":
                renamed = spec.paths.source / "vendor-render"
                renamed.write_bytes(b"\x7fELF" + b"HeadlessChrome")
                renamed.chmod(0o755)
                assert discover_browser_artifacts((spec.paths.source,))
                fixture = reclaim_identity(
                    fixture,
                    replace(
                        spec.identity,
                        source_digest=canonical_tree_digest(spec.paths.source),
                    ),
                )
                spec, store = fixture.spec, fixture.store
                adapter = ScriptedAdapter(spec)
                adapter.browser_scan_roots = (spec.paths.source,)
                expected_failure = "browser-deny"
            elif case == "descendant_process_residue":
                adapter.descendant_residue = True
                expected_failure = "workload-process-group"
            elif case == "interrupted_workload":
                adapter.interrupted_signal = 15
                adapter.terminal_status = 143
                expected_failure = "workload-interrupted"
            red_calls = unsafe_red(ScriptedAdapter(spec))
            receipt = boundary.run_job(spec, adapter, store)
            passed = (
                receipt.failure_check == expected_failure
                and receipt.status == ("succeeded" if case == "success" else "failed")
                and receipt.workload_released is expected_release
                and red_calls.index("write-release") < red_calls.index("inspect-image")
            )
            rows.append({
                "case": case, "pass": passed,
                "red": {"calls": red_calls, "releasedBeforeInspect": True},
                "green": {
                    "status": receipt.status, "failureCheck": receipt.failure_check,
                    "workloadReleased": receipt.workload_released,
                    "containerAbsent": receipt.container_absent,
                    "ownerLabelsAbsent": receipt.owner_labels_absent,
                    "brokerVolumeAbsent": receipt.broker_volume_absent,
                    "privateTreeAbsent": receipt.private_tree_absent,
                    "calls": receipt.calls,
                },
            })
    return {
        "schema": RECEIPT_SCHEMA, "caseCount": len(rows),
        "passCount": sum(bool(row["pass"]) for row in rows), "cases": rows,
        "verdict": "ADOPT_WITH_FORMAL_VERIFICATION_GATES"
        if all(row["pass"] for row in rows) else "REJECT",
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
