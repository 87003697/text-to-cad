"""Strict canonical evidence kernel for the sealed Agent runtime."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .canonical_json import (
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)
from .contracts import (
    DEPENDENCIES,
    LIFECYCLE_CLEANUP_FIELDS,
    LIFECYCLE_RESOURCE_FIELDS,
    PREDICATES,
    ROOT_ROLES,
    SUBJECT_FIELDS,
    EvidenceDocument,
    EvidenceError,
    GraphValidation,
)


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SUBJECT_KEYS = {
    "agentImageManifestDigest", "agentImageConfigDigest", "platform",
    "runtimeManifestDigest", "buildInputSetDigest",
}


def _require_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise EvidenceError(f"{label} has unexpected keys")


def _require_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise EvidenceError(f"{label} is not a canonical sha256 digest")


def _validate_subject(value: Mapping[str, Any]) -> None:
    _require_keys(value, _SUBJECT_KEYS, "subject")
    for key in _SUBJECT_KEYS - {"platform"}:
        _require_digest(value[key], f"subject.{key}")
    if value["platform"] != {"architecture": "amd64", "os": "linux"}:
        raise EvidenceError("subject.platform is not linux/amd64")


def _require_ascii(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.isascii():
        raise EvidenceError(f"{label} must be ASCII")


def _validate_reference(value: Any, label: str) -> tuple[str, str | None, str]:
    if not isinstance(value, Mapping):
        raise EvidenceError(f"{label} must be an object")
    _require_keys(value, {"kind", "environment", "digest"}, label)
    role = (value["kind"], value["environment"])
    if role not in ROOT_ROLES:
        raise EvidenceError(f"{label} has unknown role")
    _require_digest(value["digest"], f"{label}.digest")
    return value["kind"], value["environment"], value["digest"]


def _validate_child_subject(kind: str, subject: Any, status: str) -> None:
    if not isinstance(subject, Mapping):
        raise EvidenceError("evidence subject must be an object")
    _require_keys(subject, set(SUBJECT_FIELDS[kind]), f"{kind} subject")
    nullable = {
        "inventoryDigest", "browserFindingCount", "chromiumProcessCount",
        "sourceManifestDigest", "pathCount", "totalBytes",
        "resourceDisposition", "cleanupDisposition",
    }
    for key, item in subject.items():
        if item is None and key in nullable and status != "succeeded":
            continue
        if key.endswith("Digest"):
            _require_digest(item, f"{kind}.subject.{key}")
        elif key in {"pathCount", "totalBytes", "browserFindingCount", "chromiumProcessCount", "faceCount", "eulerNumber"}:
            if isinstance(item, bool) or not isinstance(item, int) or not -(2**63) <= item < 2**63:
                raise EvidenceError(f"{kind}.subject.{key} must be a signed 64-bit integer")
            if key != "eulerNumber" and item < 0:
                raise EvidenceError(f"{kind}.subject.{key} must be nonnegative")
        elif key == "platform":
            expected = {"architecture": "amd64", "os": "linux"} if kind == "image-identity" else "x86_64-unknown-linux-musl"
            if item != expected:
                raise EvidenceError(f"{kind}.subject.platform is invalid")
        elif key == "codexVersion" and item != "0.147.0":
            raise EvidenceError("codex version is not closed")
        elif key == "format" and item != "spdx-json-2.3":
            raise EvidenceError("SBOM format is not closed")
        elif key == "resourceDisposition":
            if not isinstance(item, Mapping):
                raise EvidenceError("resourceDisposition must be an object")
            _require_keys(item, set(LIFECYCLE_RESOURCE_FIELDS), "resourceDisposition")
            if any(v not in {"absent", "retained", "unproved"} for v in item.values()):
                raise EvidenceError("resourceDisposition value is invalid")
        elif key == "cleanupDisposition":
            if not isinstance(item, Mapping):
                raise EvidenceError("cleanupDisposition must be an object")
            _require_keys(item, set(LIFECYCLE_CLEANUP_FIELDS), "cleanupDisposition")
            if any(v not in {"succeeded", "failed", "not-required"} for v in item.values()):
                raise EvidenceError("cleanupDisposition value is invalid")
        else:
            _require_ascii(item, f"{kind}.subject.{key}")


def _select_lifecycle_failure(value: Mapping[str, Any]) -> str:
    predicates = value["predicates"]
    dispositions = value["subject"]["resourceDisposition"]
    resource_checks = (
        ("workloadProcessGroupAbsent", "workloadProcessGroup"),
        ("agentContainerAbsent", "agentContainer"),
        ("ownerLabelsAbsent", "ownerLabels"),
        ("brokerVolumeAbsent", "brokerVolume"),
        ("jobPrivateTreeAbsent", "jobPrivateTree"),
    )
    for disposition in ("retained", "unproved"):
        for predicate, resource in resource_checks:
            if predicates[predicate] is False and dispositions[resource] == disposition:
                return predicate
    if predicates["terminalPublicationExact"] is False:
        return "terminalPublicationExact"
    for predicate in ("containerCleanupSucceeded", "brokerVolumeCleanupSucceeded", "jobPrivateTreeCleanupSucceeded"):
        if predicates[predicate] is False:
            return predicate
    if predicates["workloadNotInterrupted"] is False:
        return "workloadNotInterrupted"
    for predicate in PREDICATES["agent-lifecycle"]:
        if predicates[predicate] is False:
            return predicate
    raise EvidenceError("failed lifecycle has no false predicate")


def _validate_evidence(value: Mapping[str, Any]) -> None:
    _require_keys(value, {"blockedBy", "dependsOn", "environment", "failureCheck", "kind", "predicates", "retryAllowed", "schema", "status", "subject", "subjectDigest"}, "evidence")
    if value["schema"] != "text-to-cad.agent-runtime-evidence/1" or value["retryAllowed"] is not False:
        raise EvidenceError("evidence terminal envelope is invalid")
    role = (value["kind"], value["environment"])
    if role not in ROOT_ROLES:
        raise EvidenceError("evidence role is invalid")
    status = value["status"]
    if status not in {"succeeded", "failed", "not-run"}:
        raise EvidenceError("evidence status is invalid")
    _require_digest(value["subjectDigest"], "evidence.subjectDigest")
    if not isinstance(value["dependsOn"], Sequence) or isinstance(value["dependsOn"], str):
        raise EvidenceError("dependsOn must be an array")
    for item in value["dependsOn"]:
        _require_digest(item, "dependsOn item")
    if not isinstance(value["predicates"], Mapping):
        raise EvidenceError("predicates must be an object")
    _require_keys(value["predicates"], set(PREDICATES[value["kind"]]), "predicates")
    predicates = [value["predicates"][key] for key in PREDICATES[value["kind"]]]
    if any(item is not True and item is not False and item is not None for item in predicates):
        raise EvidenceError("predicates must contain only Boolean or null")
    _validate_child_subject(value["kind"], value["subject"], status)
    _validate_observations(value)
    if value["kind"] == "agent-lifecycle" and status != "not-run":
        _validate_lifecycle_dispositions(value)
    if status == "succeeded":
        if value["blockedBy"] is not None or value["failureCheck"] is not None or any(item is not True for item in predicates):
            raise EvidenceError("succeeded evidence is not fully true")
    elif status == "not-run":
        if value["failureCheck"] != "dependency-failed" or any(item is not None for item in predicates):
            raise EvidenceError("not-run evidence grammar is invalid")
        _validate_reference(value["blockedBy"], "blockedBy")
        observations = {
            "browser-deny": ("inventoryDigest", "browserFindingCount", "chromiumProcessCount"),
            "source-snapshot": ("sourceManifestDigest", "pathCount", "totalBytes"),
            "agent-lifecycle": ("resourceDisposition", "cleanupDisposition"),
        }.get(value["kind"], ())
        if any(value["subject"][key] is not None for key in observations):
            raise EvidenceError("not-run observation fields must be null")
    else:
        if value["blockedBy"] is not None or value["failureCheck"] not in PREDICATES[value["kind"]]:
            raise EvidenceError("failed evidence grammar is invalid")
        if value["kind"] == "agent-lifecycle":
            primary = predicates[:20]
            if False in primary:
                first_false = primary.index(False)
                if primary[:first_false] != [True] * first_false or any(
                    item is not None for item in primary[first_false + 1 :]
                ):
                    raise EvidenceError("lifecycle primary phase does not stop at first failure")
            elif any(item is not True for item in primary):
                raise EvidenceError("lifecycle primary phase must be complete before suffix failure")
            if value["failureCheck"] != _select_lifecycle_failure(value):
                raise EvidenceError("lifecycle failureCheck is not dominant")
        else:
            index = PREDICATES[value["kind"]].index(value["failureCheck"])
            if predicates[:index] != [True] * index or predicates[index] is not False or any(item is not None for item in predicates[index + 1:]):
                raise EvidenceError("failed evidence does not stop at first failure")


def _validate_root(value: Mapping[str, Any]) -> None:
    _require_keys(value, {"schema", "status", "subject", "graph", "failureCheck", "retryAllowed"}, "verification root")
    if value["schema"] != "text-to-cad.agent-runtime-verification/1" or value["retryAllowed"] is not False:
        raise EvidenceError("verification terminal envelope is invalid")
    if value["status"] not in {"verified", "failed"}:
        raise EvidenceError("verification status is invalid")
    _validate_subject(value["subject"])
    graph = value["graph"]
    if not isinstance(graph, Mapping):
        raise EvidenceError("graph must be an object")
    _require_keys(graph, {"algorithm", "children", "subjectDigest"}, "graph")
    if graph["algorithm"] != "sha256-canonical-json-v1" or not isinstance(graph["children"], Sequence) or isinstance(graph["children"], str):
        raise EvidenceError("graph header is invalid")
    _require_digest(graph["subjectDigest"], "graph.subjectDigest")
    for index, reference in enumerate(graph["children"]):
        _validate_reference(reference, f"graph.children[{index}]")
    if value["status"] == "verified" and value["failureCheck"] is not None:
        raise EvidenceError("verified root failureCheck must be null")
    if value["status"] == "failed" and value["failureCheck"] not in _ROOT_FAILURE_CHECKS:
        raise EvidenceError("failed root needs a closed failureCheck")


def _validate_tombstone_shape(value: Mapping[str, Any]) -> None:
    _require_keys(value, {"attemptAuthorityDigest", "failureCheck", "lastDurableStage", "retentionRequired", "retryAllowed", "schema", "status", "subjectDigest"}, "verification tombstone")
    if value["schema"] != "text-to-cad.agent-runtime-verification-attempt/1" or value["status"] != "publication-failed" or value["retentionRequired"] is not True or value["retryAllowed"] is not False:
        raise EvidenceError("verification tombstone terminal envelope is invalid")
    _require_digest(value["attemptAuthorityDigest"], "attemptAuthorityDigest")
    _require_digest(value["subjectDigest"], "subjectDigest")
    allowed = {
        "graph-publication": {"none", "children-partial"},
        "root-publication": {"children-complete"},
        "visibility-verification": {"root-written"},
    }
    if value["failureCheck"] not in allowed or value["lastDurableStage"] not in allowed[value["failureCheck"]]:
        raise EvidenceError("tombstone failure/stage pairing is invalid")


def _validate_lifecycle_dispositions(value: Mapping[str, Any]) -> None:
    subject = value["subject"]
    predicates = value["predicates"]
    resources = subject["resourceDisposition"]
    cleanup = subject["cleanupDisposition"]
    if resources is None or cleanup is None:
        raise EvidenceError("succeeded or failed lifecycle needs concrete dispositions")
    resource_predicates = {
        "agentContainer": "agentContainerAbsent",
        "ownerLabels": "ownerLabelsAbsent",
        "brokerVolume": "brokerVolumeAbsent",
        "jobPrivateTree": "jobPrivateTreeAbsent",
        "workloadProcessGroup": "workloadProcessGroupAbsent",
    }
    for resource, predicate in resource_predicates.items():
        expected = resources[resource] == "absent"
        if predicates[predicate] is not expected:
            raise EvidenceError("lifecycle resource disposition contradicts predicate")
    cleanup_predicates = {
        "agentContainer": "containerCleanupSucceeded",
        "brokerVolume": "brokerVolumeCleanupSucceeded",
        "jobPrivateTree": "jobPrivateTreeCleanupSucceeded",
    }
    for resource, predicate in cleanup_predicates.items():
        expected = cleanup[resource] in {"succeeded", "not-required"}
        if predicates[predicate] is not expected:
            raise EvidenceError("lifecycle cleanup disposition contradicts predicate")


def _observation_state(
    value: Mapping[str, Any], field: str, predicate: str, label: str
) -> tuple[Any, bool | None]:
    observation = value["subject"][field]
    established = value["predicates"][predicate]
    if (observation is None) != (established is None):
        raise EvidenceError(f"{label} null state contradicts establishing predicate")
    return observation, established


def _validate_observations(value: Mapping[str, Any]) -> None:
    kind = value["kind"]
    subject = value["subject"]
    predicates = value["predicates"]
    if kind == "browser-deny":
        inventory, inventory_state = _observation_state(
            value, "inventoryDigest", "packageInventoryEmpty", "browser observation"
        )
        finding_count, _ = _observation_state(
            value, "browserFindingCount", "packageInventoryEmpty", "browser observation"
        )
        process_count, process_state = _observation_state(
            value, "chromiumProcessCount", "chromiumProcessZero", "browser observation"
        )
        inventory_predicates = (
            "packageInventoryEmpty", "executableInventoryEmpty", "cacheInventoryEmpty",
            "elfMarkerInventoryEmpty", "productMarkerInventoryEmpty",
            "playwrightInventoryEmpty",
        )
        if inventory_state is not None:
            _require_digest(inventory, "browser-deny.subject.inventoryDigest")
            any_false = any(predicates[key] is False for key in inventory_predicates)
            all_true = all(predicates[key] is True for key in inventory_predicates)
            if (any_false and finding_count < 1) or (all_true and finding_count != 0):
                raise EvidenceError("browser observation count contradicts inventory predicates")
        if process_state is True and process_count != 0:
            raise EvidenceError("browser observation process count contradicts predicate")
        if process_state is False and process_count < 1:
            raise EvidenceError("browser observation process count contradicts predicate")
    elif kind == "source-snapshot":
        for field, predicate in (
            ("sourceManifestDigest", "treeDigestMatchesObservation"),
            ("pathCount", "pathSetClosed"),
            ("totalBytes", "fileSizesBound"),
        ):
            _observation_state(value, field, predicate, "Source Snapshot observation")


def _validate_selected(kind: str, value: dict[str, Any]) -> None:
    if kind == "subject":
        _validate_subject(value)
        return
    if kind == "verification":
        _validate_root(value)
    elif kind == "evidence":
        _validate_evidence(value)
    elif kind == "verification-attempt":
        _validate_tombstone_shape(value)
    else:
        raise EvidenceError(f"unknown document kind: {kind}")


def parse_strict(kind: str, payload: bytes) -> EvidenceDocument:
    """Parse canonical JSON and validate it under one selected evidence schema."""

    value = parse_canonical_json(payload)
    if not isinstance(value, Mapping):
        raise EvidenceError("document must be a JSON object")
    _validate_selected(kind, value)
    return EvidenceDocument(kind=kind, value=value)


def canonical_bytes(document: EvidenceDocument) -> bytes:
    if not isinstance(document, EvidenceDocument):
        raise EvidenceError("canonical encoder accepts only typed documents")
    _validate_selected(document.kind, document.value)
    return canonical_json_bytes(document.value)


def digest(document: EvidenceDocument) -> str:
    if not isinstance(document, EvidenceDocument):
        raise EvidenceError("digest accepts only typed documents")
    _validate_selected(document.kind, document.value)
    return canonical_json_digest(document.value)


def validate_graph(
    root: EvidenceDocument, children: list[EvidenceDocument]
) -> GraphValidation:
    if not isinstance(root, EvidenceDocument) or root.kind != "verification":
        raise EvidenceError("root must be a typed verification document")
    canonical_bytes(root)
    if not isinstance(children, list) or any(
        not isinstance(child, EvidenceDocument) or child.kind != "evidence"
        for child in children
    ):
        raise EvidenceError("children must be typed evidence documents")
    for child in children:
        canonical_bytes(child)

    root_value = root.value
    subject_document = EvidenceDocument("subject", root_value["subject"])
    subject_digest = digest(subject_document)
    if root_value["graph"]["subjectDigest"] != subject_digest:
        raise EvidenceError("root subject digest substitution")

    references = root_value["graph"]["children"]
    reference_roles = [(item["kind"], item["environment"]) for item in references]
    if tuple(reference_roles) != ROOT_ROLES:
        raise EvidenceError("root children are missing, duplicated, additional, or out of order")
    reference_digests = [item["digest"] for item in references]
    if len(set(reference_digests)) != len(reference_digests):
        raise EvidenceError("root child digest is duplicated")
    if len(children) != len(ROOT_ROLES):
        raise EvidenceError("graph does not contain the exact required children")

    child_by_role: dict[tuple[str, str | None], dict[str, Any]] = {}
    for child in children:
        value = child.value
        role = (value["kind"], value["environment"])
        if role in child_by_role:
            raise EvidenceError("duplicate child document")
        child_by_role[role] = value
        if value["subjectDigest"] != subject_digest:
            raise EvidenceError("child subject graft")
    if set(child_by_role) != set(ROOT_ROLES):
        raise EvidenceError("child role set is not closed")

    declared_by_role = {
        (reference["kind"], reference["environment"]): reference["digest"]
        for reference in references
    }
    declared_children = {
        declared_by_role[role]: child_by_role[role] for role in ROOT_ROLES
    }
    for value in child_by_role.values():
        for dependency_digest in value["dependsOn"]:
            if dependency_digest not in declared_children:
                raise EvidenceError("dependency resolves outside the graph")
    _reject_cycles(declared_children)
    for role, value in child_by_role.items():
        if digest(EvidenceDocument("evidence", value)) != declared_by_role[role]:
            raise EvidenceError("child digest substitution or unreferenced child")

    for role, value in child_by_role.items():
        expected = [digest_for_role(child_by_role, dependency) for dependency in DEPENDENCIES[role]]
        if tuple(value["dependsOn"]) != tuple(expected):
            raise EvidenceError("dependency list or dependency order is invalid")
        failed_dependencies = [
            dependency for dependency in DEPENDENCIES[role]
            if child_by_role[dependency]["status"] != "succeeded"
        ]
        if failed_dependencies:
            if value["status"] != "not-run":
                raise EvidenceError("node executed through a failed dependency")
            blocked_role = failed_dependencies[0]
            blocked = value["blockedBy"]
            if (
                (blocked["kind"], blocked["environment"]) != blocked_role
                or blocked["digest"] != digest_for_role(child_by_role, blocked_role)
            ):
                raise EvidenceError("blockedBy is not the first failed dependency")
        elif value["status"] == "not-run":
            raise EvidenceError("ready node cannot be not-run")

    _validate_cross_bindings(root_value["subject"], child_by_role)
    statuses = [child_by_role[role]["status"] for role in ROOT_ROLES]
    if root_value["status"] == "verified":
        if any(status != "succeeded" for status in statuses):
            raise EvidenceError("verified root contains failed or not-run evidence")
    else:
        if "failed" not in statuses:
            raise EvidenceError("failed root has no failed child")
        expected_failure = _root_failure(child_by_role)
        if root_value["failureCheck"] != expected_failure:
            raise EvidenceError("root failureCheck does not have dominant precedence")
    return GraphValidation(root_value["status"], root_value["failureCheck"], digest(root))


def validate_tombstone(
    tombstone: EvidenceDocument, *, subject_digest: str, attempt_authority_digest: str
) -> None:
    if not isinstance(tombstone, EvidenceDocument) or tombstone.kind != "verification-attempt":
        raise EvidenceError("tombstone must be a typed verification-attempt document")
    canonical_bytes(tombstone)
    _require_digest(subject_digest, "expected subjectDigest")
    _require_digest(attempt_authority_digest, "expected attemptAuthorityDigest")
    if tombstone.value["subjectDigest"] != subject_digest:
        raise EvidenceError("tombstone subject binding is invalid")
    if tombstone.value["attemptAuthorityDigest"] != attempt_authority_digest:
        raise EvidenceError("tombstone attempt authority binding is invalid")
    if digest(tombstone) in {subject_digest, attempt_authority_digest}:
        raise EvidenceError("tombstone cannot reference its own digest")


def digest_for_role(
    children: dict[tuple[str, str | None], dict[str, Any]],
    role: tuple[str, str | None],
) -> str:
    return digest(EvidenceDocument("evidence", children[role]))


def _reject_cycles(children: dict[str, dict[str, Any]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_digest: str) -> None:
        if node_digest in visiting:
            raise EvidenceError("evidence dependency graph contains a cycle")
        if node_digest in visited:
            return
        visiting.add(node_digest)
        for dependency in children[node_digest]["dependsOn"]:
            visit(dependency)
        visiting.remove(node_digest)
        visited.add(node_digest)

    for node_digest in children:
        visit(node_digest)


def _equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise EvidenceError(f"cross-document binding mismatch: {label}")


def _validate_cross_bindings(
    root_subject: dict[str, Any],
    children: dict[tuple[str, str | None], dict[str, Any]],
) -> None:
    def subject(kind: str, environment: str | None = None) -> dict[str, Any]:
        return children[(kind, environment)]["subject"]

    root_fields = (
        "agentImageManifestDigest", "agentImageConfigDigest", "runtimeManifestDigest",
        "buildInputSetDigest",
    )
    for role in ROOT_ROLES:
        child_subject = children[role]["subject"]
        for field in root_fields:
            if field in child_subject:
                _equal(child_subject[field], root_subject[field], f"{role}.{field}")
    _equal(subject("image-identity")["platform"], root_subject["platform"], "image platform")
    source_nodes = [children[("source-snapshot", env)] for env in ("colima", "cvm")]
    if all(node["status"] == "succeeded" for node in source_nodes):
        _equal(source_nodes[0]["subject"], source_nodes[1]["subject"], "cross-host source snapshot")
    build_inputs = subject("build-input-set")
    provenance = subject("build-provenance")
    admission = subject("dependency-admission")
    image = subject("image-identity")
    _equal(provenance["buildInputSetDigest"], build_inputs["buildInputSetDigest"], "provenance build input")
    _equal(provenance["buildRecipeDigest"], build_inputs["buildRecipeDigest"], "provenance recipe")
    _equal(provenance["baseImageManifestDigest"], build_inputs["baseImageManifestDigest"], "provenance base image")
    _equal(admission["buildInputSetDigest"], build_inputs["buildInputSetDigest"], "admission build input")
    _equal(admission["dependencyLockDigest"], build_inputs["dependencyLockDigest"], "admission dependency lock")
    _equal(image["agentImageManifestDigest"], provenance["outputImageManifestDigest"], "image output manifest")
    _equal(image["agentImageConfigDigest"], provenance["outputImageConfigDigest"], "image output config")
    _equal(subject("sbom")["agentImageManifestDigest"], image["agentImageManifestDigest"], "SBOM image manifest")


_LIFECYCLE_ALIAS = {
    "adapterOperationsClosed": "adapter-failure", "authorityFresh": "authority-replay",
    "jobPrivateLayoutExact": "job-private-layout", "snapshotIdentityExact": "snapshot-identity",
    "workloadIdentityExact": "workload-identity", "imageIdentityOuterAttested": "image-identity",
    "returnedContainerIdExact": "returned-container-id", "containerOwnershipExact": "container-ownership",
    "inertContainerConfigExact": "inert-container", "readOnlyRoot": "inert-container",
    "sourceReadOnly": "inert-container", "inputReadOnly": "inert-container",
    "writableMountAllowlistExact": "inert-container", "dockerSocketAbsent": "inert-container",
    "capabilitiesEmpty": "inert-container", "noNewPrivileges": "inert-container",
    "externalNetworkAbsent": "inert-container", "entrypointPreflightExact": "entrypoint-preflight",
    "brokerProofIdentityBound": "broker-proof", "workloadReleasedOnce": "workload-release",
    "terminalPublicationExact": "terminal-publication",
    "descendantResidueFalse": "workload-process-group",
    "workloadNotInterrupted": "workload-interrupted", "workloadTerminalZero": "workload-terminal",
    "containerCleanupSucceeded": "cleanup-container", "brokerVolumeCleanupSucceeded": "cleanup-broker-volume",
    "jobPrivateTreeCleanupSucceeded": "cleanup-private-tree",
}

_ROOT_FAILURE_CHECKS = {
    f"{kind}{f':{environment}' if environment else ''}.{predicate}"
    for kind, environment in ROOT_ROLES
    if kind != "agent-lifecycle"
    for predicate in PREDICATES[kind]
} | {
    f"agent-lifecycle:{environment}.{alias}"
    for environment in ("colima", "cvm")
    for alias in set(_LIFECYCLE_ALIAS.values()) | {"retained-resource", "absence-proof"}
}


def _lifecycle_alias(value: dict[str, Any]) -> str:
    selected = value["failureCheck"]
    dispositions = value["subject"]["resourceDisposition"]
    resources = {
        "workloadProcessGroupAbsent": "workloadProcessGroup", "agentContainerAbsent": "agentContainer",
        "ownerLabelsAbsent": "ownerLabels", "brokerVolumeAbsent": "brokerVolume",
        "jobPrivateTreeAbsent": "jobPrivateTree",
    }
    if selected in resources:
        disposition = dispositions[resources[selected]]
        if disposition == "retained":
            return "retained-resource"
        if disposition == "unproved":
            return "absence-proof"
        raise EvidenceError("resource failure has no exact disposition alias")
    try:
        return _LIFECYCLE_ALIAS[selected]
    except KeyError as exc:
        raise EvidenceError("lifecycle failure has no exact alias") from exc


def _root_failure(children: dict[tuple[str, str | None], dict[str, Any]]) -> str:
    failed = [(index, role, children[role]) for index, role in enumerate(ROOT_ROLES) if children[role]["status"] == "failed"]
    def priority(item: tuple[int, tuple[str, str | None], dict[str, Any]]) -> tuple[int, int]:
        index, role, value = item
        if role[0] == "agent-lifecycle":
            alias = _lifecycle_alias(value)
            ranks = {
                "retained-resource": 0,
                "absence-proof": 1,
                "terminal-publication": 2,
                "cleanup-container": 3,
                "cleanup-broker-volume": 4,
                "cleanup-private-tree": 5,
                "workload-interrupted": 6,
            }
            return ranks.get(alias, 7), index
        return 7, index
    _, role, value = min(failed, key=priority)
    if role[0] == "agent-lifecycle":
        return f"agent-lifecycle:{role[1]}.{_lifecycle_alias(value)}"
    qualifier = role[0] + (f":{role[1]}" if role[1] else "")
    return f"{qualifier}.{value['failureCheck']}"
