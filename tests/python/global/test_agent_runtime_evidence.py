from __future__ import annotations

import copy
import json
import unittest


D = "sha256:" + "1" * 64
SUBJECT = (
    b'{"agentImageConfigDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111",'
    b'"agentImageManifestDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111",'
    b'"buildInputSetDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111",'
    b'"platform":{"architecture":"amd64","os":"linux"},'
    b'"runtimeManifestDigest":"sha256:1111111111111111111111111111111111111111111111111111111111111111"}'
)


def typed(kind, value):
    from scripts.pilot.agent_runtime import parse_strict

    return parse_strict(
        kind,
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii"),
    )


def valid_graph(*, failed_role=None, failed_predicate=None, root_failure=None, subject_changes=None):
    from scripts.pilot.agent_runtime import digest
    from scripts.pilot.agent_runtime.contracts import DEPENDENCIES, PREDICATES, ROOT_ROLES, SUBJECT_FIELDS

    root_subject = json.loads(SUBJECT)
    subject_digest = digest(typed("subject", root_subject))
    cache = {}

    def make_subject(kind):
        value = {}
        for field in SUBJECT_FIELDS[kind]:
            if field == "codexVersion":
                value[field] = "0.147.0"
            elif field == "platform":
                value[field] = {"architecture": "amd64", "os": "linux"} if kind == "image-identity" else "x86_64-unknown-linux-musl"
            elif field == "format":
                value[field] = "spdx-json-2.3"
            elif field == "faceCount":
                value[field] = 3764
            elif field == "eulerNumber":
                value[field] = 144
            elif field in {"pathCount", "totalBytes", "browserFindingCount", "chromiumProcessCount"}:
                value[field] = 0
            elif field == "watertight":
                value[field] = False
            elif field == "resourceDisposition":
                value[field] = {"agentContainer": "absent", "brokerVolume": "absent", "jobPrivateTree": "absent", "ownerLabels": "absent", "workloadProcessGroup": "absent"}
            elif field == "cleanupDisposition":
                value[field] = {"agentContainer": "succeeded", "brokerVolume": "succeeded", "jobPrivateTree": "succeeded"}
            else:
                value[field] = D
        return value

    def build(role):
        if role in cache:
            return cache[role]
        dependencies = [build(dep) for dep in DEPENDENCIES[role]]
        status = "succeeded"
        blocked_by = None
        failure_check = None
        predicate_values = {key: True for key in PREDICATES[role[0]]}
        failed_dependencies = [
            (dependency_role, dependency)
            for dependency_role, dependency in zip(DEPENDENCIES[role], dependencies)
            if dependency.value["status"] != "succeeded"
        ]
        if failed_dependencies:
            status = "not-run"
            failure_check = "dependency-failed"
            blocked_role, blocked_document = failed_dependencies[0]
            blocked_by = {
                "digest": digest(blocked_document),
                "environment": blocked_role[1],
                "kind": blocked_role[0],
            }
            predicate_values = {key: None for key in predicate_values}
        elif role == failed_role:
            status = "failed"
            failure_check = failed_predicate
            index = PREDICATES[role[0]].index(failed_predicate)
            predicate_values = {
                key: True if offset < index else False if offset == index else None
                for offset, key in enumerate(PREDICATES[role[0]])
            }
        subject = make_subject(role[0])
        if status == "not-run":
            for field in {
                "browser-deny": ("inventoryDigest", "browserFindingCount", "chromiumProcessCount"),
                "source-snapshot": ("sourceManifestDigest", "pathCount", "totalBytes"),
                "agent-lifecycle": ("resourceDisposition", "cleanupDisposition"),
            }.get(role[0], ()):
                subject[field] = None
        if role == failed_role and subject_changes:
            subject.update(subject_changes)
        if status == "failed":
            observation_bindings = {
                "browser-deny": {
                    "inventoryDigest": "packageInventoryEmpty",
                    "browserFindingCount": "packageInventoryEmpty",
                    "chromiumProcessCount": "chromiumProcessZero",
                },
                "source-snapshot": {
                    "sourceManifestDigest": "treeDigestMatchesObservation",
                    "pathCount": "pathSetClosed",
                    "totalBytes": "fileSizesBound",
                },
            }.get(role[0], {})
            for field, predicate in observation_bindings.items():
                if predicate_values[predicate] is None:
                    subject[field] = None
        value = {
            "blockedBy": blocked_by,
            "dependsOn": [digest(item) for item in dependencies],
            "environment": role[1],
            "failureCheck": failure_check,
            "kind": role[0],
            "predicates": predicate_values,
            "retryAllowed": False,
            "schema": "text-to-cad.agent-runtime-evidence/1",
            "status": status,
            "subject": subject,
            "subjectDigest": subject_digest,
        }
        cache[role] = typed("evidence", value)
        return cache[role]

    children = [build(role) for role in ROOT_ROLES]
    root = typed("verification", {
        "failureCheck": root_failure,
        "graph": {
            "algorithm": "sha256-canonical-json-v1",
            "children": [
                {"digest": digest(child), "environment": role[1], "kind": role[0]}
                for role, child in zip(ROOT_ROLES, children)
            ],
            "subjectDigest": subject_digest,
        },
        "retryAllowed": False,
        "schema": "text-to-cad.agent-runtime-verification/1",
        "status": "failed" if failed_role else "verified",
        "subject": root_subject,
    })
    return root, children


def replace_document(document, **changes):
    value = copy.deepcopy(document.value)
    value.update(changes)
    return typed(document.kind, value)


def failed_lifecycle_graph(
    false_predicates, selected, root_failure, *, retained=None, unproved=None
):
    from scripts.pilot.agent_runtime import digest
    from scripts.pilot.agent_runtime.contracts import PREDICATES

    root, children = valid_graph()
    lifecycle_value = copy.deepcopy(children[0].value)
    lifecycle_value["status"] = "failed"
    lifecycle_value["failureCheck"] = selected
    for predicate in false_predicates:
        lifecycle_value["predicates"][predicate] = False
    primary_order = list(PREDICATES["agent-lifecycle"][:20])
    primary_false = [primary_order.index(predicate) for predicate in false_predicates if predicate in primary_order]
    if primary_false:
        for predicate in primary_order[min(primary_false) + 1 :]:
            lifecycle_value["predicates"][predicate] = None
    if retained:
        lifecycle_value["subject"]["resourceDisposition"][retained] = "retained"
    if unproved:
        lifecycle_value["subject"]["resourceDisposition"][unproved] = "unproved"
    cleanup_resource = {
        "containerCleanupSucceeded": "agentContainer",
        "brokerVolumeCleanupSucceeded": "brokerVolume",
        "jobPrivateTreeCleanupSucceeded": "jobPrivateTree",
    }
    if selected in cleanup_resource:
        lifecycle_value["subject"]["cleanupDisposition"][cleanup_resource[selected]] = "failed"
    lifecycle = typed("evidence", lifecycle_value)
    children[0] = lifecycle

    root_value = copy.deepcopy(root.value)
    root_value["status"] = "failed"
    root_value["failureCheck"] = root_failure
    root_value["graph"]["children"][0]["digest"] = digest(lifecycle)
    return typed("verification", root_value), children


def dual_lifecycle_failure_graph(colima_check, cvm_check, root_failure):
    from scripts.pilot.agent_runtime import digest

    root, children = valid_graph()
    cleanup_resource = {
        "containerCleanupSucceeded": "agentContainer",
        "brokerVolumeCleanupSucceeded": "brokerVolume",
        "jobPrivateTreeCleanupSucceeded": "jobPrivateTree",
    }
    for lifecycle_index, selected in (
        (0, colima_check),
        (1, cvm_check),
    ):
        value = copy.deepcopy(children[lifecycle_index].value)
        value["status"] = "failed"
        value["failureCheck"] = selected
        value["predicates"][selected] = False
        if selected in cleanup_resource:
            value["subject"]["cleanupDisposition"][cleanup_resource[selected]] = "failed"
        failed = typed("evidence", value)
        children[lifecycle_index] = failed

    root_value = copy.deepcopy(root.value)
    root_value["status"] = "failed"
    root_value["failureCheck"] = root_failure
    for index in (0, 1):
        root_value["graph"]["children"][index]["digest"] = digest(children[index])
    return typed("verification", root_value), children


class AgentRuntimeEvidenceTests(unittest.TestCase):
    def test_schema_neutral_canonical_json_seam_is_public_and_immutable(self) -> None:
        from scripts.pilot.agent_runtime.canonical_json import (
            canonical_json_bytes,
            canonical_json_digest,
            parse_canonical_json,
        )

        payload = b'{"a":[1,true,null,"x"],"z":{}}'
        value = parse_canonical_json(payload + b"\n")
        self.assertEqual(canonical_json_bytes(value), payload)
        self.assertEqual(
            canonical_json_digest(value),
            "sha256:3d78d041b34c0bba6cd9983614d7a15920dfe7025e285dd02b420c905cfbb130",
        )
        with self.assertRaises(TypeError):
            value["a"][0] = 2

    def test_schema_neutral_canonical_json_grammar_is_closed(self) -> None:
        from scripts.pilot.agent_runtime import (
            EvidenceError,
            canonical_json_bytes,
            parse_canonical_json,
        )

        self.assertEqual(parse_canonical_json(b"null"), None)
        self.assertEqual(canonical_json_bytes({"z": 0, "a": 1}), b'{"a":1,"z":0}')
        self.assertEqual(canonical_json_bytes('\x00"\n\\'), b'"\\u0000\\"\\n\\\\"')
        self.assertEqual(canonical_json_bytes({"\n": '"\\'}), b'{"\\n":"\\"\\\\"}')
        self.assertEqual(canonical_json_bytes(-(2**63)), b"-9223372036854775808")
        self.assertEqual(canonical_json_bytes(2**63 - 1), b"9223372036854775807")
        exact_limit = b'"' + b"x" * (1024 * 1024 - 2) + b'"'
        self.assertEqual(len(canonical_json_bytes(parse_canonical_json(exact_limit))), 1024 * 1024)
        depth_64 = b"[" * 64 + b"0" + b"]" * 64
        self.assertEqual(canonical_json_bytes(parse_canonical_json(depth_64)), depth_64)

        malformed = {
            "duplicate": b'{"a":1,"a":2}',
            "whitespace": b'{"a": 1}',
            "key-order": b'{"z":0,"a":1}',
            "bom": b"\xef\xbb\xbfnull",
            "two-newlines": b"null\n\n",
            "trailing-value": b"null true",
            "float": b"1.0",
            "constant": b"NaN",
            "integer-overflow": b"9223372036854775808",
            "non-ascii-string": '"é"'.encode(),
            "escaped-non-ascii-string": b'"\\u00e9"',
            "non-ascii-key": '{"é":0}'.encode(),
            "byte-limit": b'"' + b"x" * (1024 * 1024 - 1) + b'"',
            "depth-limit": b"[" * 65 + b"0" + b"]" * 65,
        }
        for label, payload in malformed.items():
            with self.subTest(label=label), self.assertRaises(EvidenceError):
                parse_canonical_json(payload)

        for value in (1.5, 2**63, {"é": 0}, {"a": "é"}, {"a": object()}):
            with self.subTest(value=repr(value)), self.assertRaises(EvidenceError):
                canonical_json_bytes(value)
        with self.assertRaisesRegex(EvidenceError, "byte limit"):
            canonical_json_bytes("x" * (1024 * 1024 - 1))

    def test_typed_wrappers_delegate_without_accepting_raw_json(self) -> None:
        from scripts.pilot.agent_runtime import (
            EvidenceError,
            canonical_bytes,
            canonical_json_bytes,
            canonical_json_digest,
            digest,
            parse_canonical_json,
            parse_strict,
            validate_graph,
        )

        raw = parse_canonical_json(SUBJECT)
        document = parse_strict("subject", SUBJECT)
        self.assertEqual(canonical_bytes(document), canonical_json_bytes(raw))
        self.assertEqual(digest(document), canonical_json_digest(raw))
        for operation in (
            lambda: canonical_bytes(raw),
            lambda: digest(raw),
            lambda: validate_graph(raw, []),
            lambda: canonical_json_bytes(document),
            lambda: canonical_json_digest(SUBJECT),
            lambda: canonical_json_digest(document),
        ):
            with self.subTest(operation=operation), self.assertRaises(EvidenceError):
                operation()
        with self.assertRaisesRegex(EvidenceError, "unknown document kind"):
            parse_strict("supply-manifest", SUBJECT)

    def test_canonical_encoder_bounds_snapshot_before_materializing(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, canonical_json_bytes

        huge_string = "x" * 100_000_000
        with self.assertRaisesRegex(EvidenceError, "byte limit"):
            canonical_json_bytes(huge_string)
        with self.assertRaisesRegex(EvidenceError, "byte limit"):
            canonical_json_bytes({"a": huge_string, "b": object()})

        exact_null_array = [None] * 209_715
        self.assertEqual(len(canonical_json_bytes(exact_null_array)), 1024 * 1024)
        exact_null_array.extend((None, object()))
        with self.assertRaisesRegex(EvidenceError, "byte limit"):
            canonical_json_bytes(exact_null_array)

    def test_canonical_encoder_rejects_undeclared_containers_and_normalizes_errors(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, canonical_json_bytes

        class ChangesAfterFirstRead(dict):
            def items(self):
                raise KeyError("must not leak")

        class BrokenList(list):
            def __iter__(self):
                raise RuntimeError("mutated during iteration")

        self.assertEqual(canonical_json_bytes([{"b": 2, "a": 1}]), b'[{"a":1,"b":2}]')
        for value in (ChangesAfterFirstRead(), BrokenList(), range(3), {"a": range(1)}):
            with self.subTest(value=type(value).__name__), self.assertRaises(EvidenceError):
                canonical_json_bytes(value)

    def test_typed_document_is_deeply_immutable_and_digest_stable(self) -> None:
        from scripts.pilot.agent_runtime import canonical_bytes, digest, parse_strict

        document = parse_strict("subject", SUBJECT)
        before = digest(document)
        before_bytes = canonical_bytes(document)
        with self.assertRaises(TypeError):
            document.value["platform"]["os"] = "other"
        self.assertEqual(digest(document), before)
        self.assertEqual(canonical_bytes(document), before_bytes)

        _, children = valid_graph()
        evidence = children[0]
        before = digest(evidence)
        with self.assertRaises(TypeError):
            evidence.value["dependsOn"][0] = D
        with self.assertRaises(TypeError):
            evidence.value._values["status"] = "failed"
        with self.assertRaises(TypeError):
            evidence.value._values = {}
        self.assertEqual(digest(evidence), before)

    def test_parser_bounds_bytes_depth_and_normalizes_recursion(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, parse_strict

        with self.assertRaisesRegex(EvidenceError, "byte limit"):
            parse_strict("subject", b" " * (1024 * 1024 + 1))
        with self.assertRaisesRegex(EvidenceError, "nesting depth"):
            parse_strict("subject", b"[" * 65 + b"0" + b"]" * 65)
        with self.assertRaises(EvidenceError):
            parse_strict("subject", b"[" * 2000 + b"0" + b"]" * 2000)

    def test_strict_parser_rejects_duplicate_json_keys(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, parse_strict

        with self.assertRaisesRegex(EvidenceError, "duplicate JSON key"):
            parse_strict("subject", b'{"platform":{},"platform":{}}')

    def test_schema_rejects_unknown_and_missing_keys(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, parse_strict

        unknown = json.loads(SUBJECT)
        unknown["secret"] = "raw"
        with self.assertRaisesRegex(EvidenceError, "unexpected keys"):
            parse_strict(
                "subject",
                json.dumps(unknown, sort_keys=True, separators=(",", ":")).encode("ascii"),
            )
        with self.assertRaisesRegex(EvidenceError, "unexpected keys"):
            parse_strict(
                "subject",
                SUBJECT.replace(b',"runtimeManifestDigest":"sha256:' + b"1" * 64 + b'"', b""),
            )

    def test_noncanonical_input_is_rejected_and_output_has_one_encoding(self) -> None:
        from scripts.pilot.agent_runtime import (
            EvidenceError,
            canonical_bytes,
            digest,
            parse_strict,
        )

        document = parse_strict("subject", SUBJECT + b"\n")
        self.assertEqual(canonical_bytes(document), SUBJECT)
        self.assertEqual(
            digest(document),
            "sha256:eb95e20bb046d802d186e6d3db35e7ea6ccc6ff2631297aeda61a8bd7350ca5b",
        )
        with self.assertRaisesRegex(EvidenceError, "non-canonical"):
            parse_strict("subject", b"{" + SUBJECT[1:].replace(b'\":\"', b'\": \"', 1))
        for malformed in (b"\xef\xbb\xbf" + SUBJECT, SUBJECT + b"\n\n", b"\xff"):
            with self.subTest(malformed=malformed[:4]), self.assertRaises(EvidenceError):
                parse_strict("subject", malformed)

    def test_json_numbers_are_integer_only_and_not_boolean_substitutes(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError

        _, children = valid_graph()
        browser = copy.deepcopy(children[2].value)
        browser["subject"]["browserFindingCount"] = 1.5
        with self.assertRaisesRegex(EvidenceError, "numbers"):
            typed("evidence", browser)
        browser["subject"]["browserFindingCount"] = True
        with self.assertRaisesRegex(EvidenceError, "signed 64-bit integer"):
            typed("evidence", browser)
        browser["subject"]["browserFindingCount"] = 2**63
        with self.assertRaisesRegex(EvidenceError, "signed 64-bit"):
            typed("evidence", browser)
        browser = copy.deepcopy(children[2].value)
        browser["predicates"]["packageInventoryEmpty"] = 1
        with self.assertRaisesRegex(EvidenceError, "Boolean or null"):
            typed("evidence", browser)

    def test_verified_graph_closes_exact_runtime_nodes(self) -> None:
        from scripts.pilot.agent_runtime import validate_graph

        result = validate_graph(*valid_graph())
        self.assertEqual(result.status, "verified")
        self.assertIsNone(result.failure_check)

    def test_digest_substitution_and_cross_subject_graft_are_rejected(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, validate_graph

        root, children = valid_graph()
        bad_root = copy.deepcopy(root.value)
        bad_root["graph"]["children"][5]["digest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(EvidenceError, "digest substitution"):
            validate_graph(typed("verification", bad_root), children)

        graft = copy.deepcopy(children[0].value)
        graft["subjectDigest"] = "sha256:" + "e" * 64
        grafted = typed("evidence", graft)
        graft_root = copy.deepcopy(root.value)
        from scripts.pilot.agent_runtime import digest
        graft_root["graph"]["children"][0]["digest"] = digest(grafted)
        with self.assertRaisesRegex(EvidenceError, "subject graft"):
            validate_graph(typed("verification", graft_root), [grafted, *children[1:]])

        subject_graft = copy.deepcopy(root.value)
        subject_graft["graph"]["subjectDigest"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(EvidenceError, "root subject digest substitution"):
            validate_graph(typed("verification", subject_graft), children)

    def test_missing_and_duplicate_children_are_rejected(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, validate_graph

        root, children = valid_graph()
        with self.assertRaisesRegex(EvidenceError, "exact required"):
            validate_graph(root, children[:-1])
        with self.assertRaisesRegex(EvidenceError, "duplicate child"):
            validate_graph(root, [children[0], *children[1:-1], children[0]])

    def test_dependency_cycle_and_order_are_rejected(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, digest, validate_graph

        root, children = valid_graph()
        cycled_value = copy.deepcopy(children[3].value)
        cycled_value["dependsOn"] = [digest(children[4])]
        cycled = typed("evidence", cycled_value)
        changed = [cycled if index == 3 else child for index, child in enumerate(children)]
        with self.assertRaisesRegex(EvidenceError, "cycle"):
            validate_graph(root, changed)

        ordered_value = copy.deepcopy(children[0].value)
        ordered_value["dependsOn"] = list(reversed(ordered_value["dependsOn"]))
        ordered = typed("evidence", ordered_value)
        changed = children[:]
        changed[0] = ordered
        order_root = copy.deepcopy(root.value)
        order_root["graph"]["children"][0]["digest"] = digest(ordered)
        with self.assertRaisesRegex(EvidenceError, "dependency list or dependency order"):
            validate_graph(typed("verification", order_root), changed)

    def test_failed_and_not_run_grammar_is_closed(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError

        _, children = valid_graph()
        failed = copy.deepcopy(children[3].value)
        failed["status"] = "failed"
        failed["failureCheck"] = "recipeBound"
        failed["predicates"]["recipeBound"] = False
        failed["predicates"]["baseManifestBound"] = None
        with self.assertRaisesRegex(EvidenceError, "stop at first failure"):
            typed("evidence", failed)

        not_run = copy.deepcopy(children[3].value)
        not_run["status"] = "not-run"
        not_run["failureCheck"] = "dependency-failed"
        not_run["predicates"] = {key: None for key in not_run["predicates"]}
        not_run["blockedBy"] = {"digest": D, "environment": None, "kind": "dependency-admission"}
        with self.assertRaisesRegex(EvidenceError, "outside the graph"):
            from scripts.pilot.agent_runtime import validate_graph
            root, all_children = valid_graph()
            altered = typed("evidence", not_run)
            all_children[3] = altered
            root_value = copy.deepcopy(root.value)
            from scripts.pilot.agent_runtime import digest
            root_value["graph"]["children"][3]["digest"] = digest(altered)
            validate_graph(typed("verification", root_value), all_children)

    def test_failed_source_manifest_observation_is_status_aware(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, validate_graph

        mismatch = "sha256:" + "c" * 64
        root, children = valid_graph(
            failed_role=("source-snapshot", "colima"),
            failed_predicate="treeDigestMatchesObservation",
            root_failure="source-snapshot:colima.treeDigestMatchesObservation",
            subject_changes={"sourceManifestDigest": mismatch},
        )
        self.assertEqual(validate_graph(root, children).status, "failed")

        root, children = valid_graph(
            failed_role=("source-snapshot", "colima"),
            failed_predicate="readOnlyMountEligible",
            root_failure="source-snapshot:colima.readOnlyMountEligible",
        )
        self.assertEqual(validate_graph(root, children).status, "failed")

        root, children = valid_graph(
            failed_role=("source-snapshot", "colima"),
            failed_predicate="fileDigestsBound",
            root_failure="source-snapshot:colima.fileDigestsBound",
        )
        self.assertEqual(validate_graph(root, children).status, "failed")

    def test_verified_root_rejects_false_null_failed_and_not_run(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, validate_graph

        root, children = valid_graph(
            failed_role=("build-input-set", None),
            failed_predicate="recipeBound",
            root_failure="build-input-set.recipeBound",
        )
        root_value = copy.deepcopy(root.value)
        root_value["status"] = "verified"
        root_value["failureCheck"] = None
        with self.assertRaisesRegex(EvidenceError, "verified root contains"):
            validate_graph(typed("verification", root_value), children)

    def test_lifecycle_multiple_false_uses_dominant_retained_resource(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, validate_graph

        _, children = valid_graph()
        lifecycle = copy.deepcopy(children[0].value)
        lifecycle["status"] = "failed"
        lifecycle["predicates"]["entrypointPreflightExact"] = False
        lifecycle["predicates"]["brokerProofIdentityBound"] = None
        lifecycle["predicates"]["workloadReleasedOnce"] = None
        lifecycle["predicates"]["agentContainerAbsent"] = False
        lifecycle["subject"]["resourceDisposition"]["agentContainer"] = "retained"
        lifecycle["failureCheck"] = "entrypointPreflightExact"
        with self.assertRaisesRegex(EvidenceError, "not dominant"):
            typed("evidence", lifecycle)
        root, graph = failed_lifecycle_graph(
            ["entrypointPreflightExact", "agentContainerAbsent"],
            "agentContainerAbsent",
            "agent-lifecycle:colima.retained-resource",
            retained="agentContainer",
        )
        self.assertEqual(validate_graph(root, graph).failure_check, "agent-lifecycle:colima.retained-resource")
        root, graph = failed_lifecycle_graph(
            ["agentContainerAbsent"],
            "agentContainerAbsent",
            "agent-lifecycle:colima.absence-proof",
            unproved="agentContainer",
        )
        self.assertEqual(
            validate_graph(root, graph).failure_check,
            "agent-lifecycle:colima.absence-proof",
        )

    def test_lifecycle_primary_and_disposition_states_are_closed(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError

        _, children = valid_graph()
        suffix_failure = copy.deepcopy(children[0].value)
        suffix_failure["status"] = "failed"
        suffix_failure["failureCheck"] = "descendantResidueFalse"
        suffix_failure["predicates"]["authorityFresh"] = None
        suffix_failure["predicates"]["descendantResidueFalse"] = False
        with self.assertRaisesRegex(EvidenceError, "primary phase"):
            typed("evidence", suffix_failure)

        missing_dispositions = copy.deepcopy(children[0].value)
        missing_dispositions["status"] = "failed"
        missing_dispositions["failureCheck"] = "containerCleanupSucceeded"
        missing_dispositions["predicates"]["containerCleanupSucceeded"] = False
        missing_dispositions["subject"]["resourceDisposition"] = None
        missing_dispositions["subject"]["cleanupDisposition"] = None
        with self.assertRaisesRegex(EvidenceError, "disposition"):
            typed("evidence", missing_dispositions)

        cleanup_mismatch = copy.deepcopy(children[0].value)
        cleanup_mismatch["status"] = "failed"
        cleanup_mismatch["failureCheck"] = "containerCleanupSucceeded"
        cleanup_mismatch["predicates"]["containerCleanupSucceeded"] = False
        with self.assertRaisesRegex(EvidenceError, "cleanup disposition"):
            typed("evidence", cleanup_mismatch)

        absence_mismatch = copy.deepcopy(children[0].value)
        absence_mismatch["status"] = "failed"
        absence_mismatch["failureCheck"] = "agentContainerAbsent"
        absence_mismatch["predicates"]["agentContainerAbsent"] = False
        with self.assertRaisesRegex(EvidenceError, "resource disposition"):
            typed("evidence", absence_mismatch)

    def test_observation_bindings_reject_null_and_scalar_substitution(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError
        from scripts.pilot.agent_runtime.contracts import PREDICATES

        _, children = valid_graph()
        browser = copy.deepcopy(children[2].value)
        browser["subject"]["browserFindingCount"] = 1
        with self.assertRaisesRegex(EvidenceError, "browser observation"):
            typed("evidence", browser)

        browser = copy.deepcopy(children[2].value)
        browser["status"] = "failed"
        browser["failureCheck"] = "packageInventoryEmpty"
        browser["predicates"] = {
            key: False if key == "packageInventoryEmpty" else None
            for key in browser["predicates"]
        }
        browser["subject"]["browserFindingCount"] = 0
        browser["subject"]["chromiumProcessCount"] = None
        with self.assertRaisesRegex(EvidenceError, "browser observation"):
            typed("evidence", browser)
        browser["subject"]["browserFindingCount"] = 1
        self.assertEqual(typed("evidence", browser).value["status"], "failed")

    def test_source_count_observations_follow_establishing_predicates(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError

        _, children = valid_graph()
        source = copy.deepcopy(children[9].value)
        source["status"] = "failed"
        source["failureCheck"] = "manifestSchemaExact"
        source["predicates"] = {
            key: False if key == "manifestSchemaExact" else None
            for key in source["predicates"]
        }
        source["subject"]["sourceManifestDigest"] = None
        source["subject"]["pathCount"] = 0
        source["subject"]["totalBytes"] = None
        with self.assertRaisesRegex(EvidenceError, "Source Snapshot observation"):
            typed("evidence", source)

        source = copy.deepcopy(children[9].value)
        source["status"] = "failed"
        source["failureCheck"] = "pathSetClosed"
        source["predicates"] = {
            key: True if key == "manifestSchemaExact" else False if key == "pathSetClosed" else None
            for key in source["predicates"]
        }
        source["subject"]["sourceManifestDigest"] = None
        source["subject"]["pathCount"] = 17
        source["subject"]["totalBytes"] = None
        self.assertEqual(typed("evidence", source).value["status"], "failed")

    def test_lifecycle_alias_table_is_closed_and_exact(self) -> None:
        from scripts.pilot.agent_runtime import validate_graph
        from scripts.pilot.agent_runtime.contracts import PREDICATES
        from scripts.pilot.agent_runtime.evidence import _LIFECYCLE_ALIAS

        cases = {
            "adapterOperationsClosed": "adapter-failure",
            "authorityFresh": "authority-replay",
            "jobPrivateLayoutExact": "job-private-layout",
            "snapshotIdentityExact": "snapshot-identity",
            "workloadIdentityExact": "workload-identity",
            "imageIdentityOuterAttested": "image-identity",
            "returnedContainerIdExact": "returned-container-id",
            "containerOwnershipExact": "container-ownership",
            "inertContainerConfigExact": "inert-container",
            "readOnlyRoot": "inert-container",
            "sourceReadOnly": "inert-container",
            "inputReadOnly": "inert-container",
            "writableMountAllowlistExact": "inert-container",
            "dockerSocketAbsent": "inert-container",
            "capabilitiesEmpty": "inert-container",
            "noNewPrivileges": "inert-container",
            "externalNetworkAbsent": "inert-container",
            "entrypointPreflightExact": "entrypoint-preflight",
            "brokerProofIdentityBound": "broker-proof",
            "workloadReleasedOnce": "workload-release",
            "terminalPublicationExact": "terminal-publication",
            "descendantResidueFalse": "workload-process-group",
            "workloadNotInterrupted": "workload-interrupted",
            "workloadTerminalZero": "workload-terminal",
            "containerCleanupSucceeded": "cleanup-container",
            "brokerVolumeCleanupSucceeded": "cleanup-broker-volume",
            "jobPrivateTreeCleanupSucceeded": "cleanup-private-tree",
        }
        resource_predicates = {
            "workloadProcessGroupAbsent", "agentContainerAbsent", "ownerLabelsAbsent",
            "brokerVolumeAbsent", "jobPrivateTreeAbsent",
        }
        self.assertEqual(
            set(_LIFECYCLE_ALIAS),
            set(PREDICATES["agent-lifecycle"]) - resource_predicates,
        )
        for predicate, alias in cases.items():
            with self.subTest(predicate=predicate):
                expected = f"agent-lifecycle:colima.{alias}"
                root, graph = failed_lifecycle_graph([predicate], predicate, expected)
                self.assertEqual(validate_graph(root, graph).failure_check, expected)

    def test_root_dominance_orders_cleanup_kind_before_child_order(self) -> None:
        from scripts.pilot.agent_runtime import validate_graph

        expected = "agent-lifecycle:cvm.cleanup-container"
        root, graph = dual_lifecycle_failure_graph(
            "jobPrivateTreeCleanupSucceeded",
            "containerCleanupSucceeded",
            expected,
        )
        self.assertEqual(validate_graph(root, graph).failure_check, expected)

    def test_tombstone_is_non_self_referential_and_exactly_bound(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError, digest, validate_tombstone

        value = {
            "attemptAuthorityDigest": "sha256:" + "a" * 64,
            "failureCheck": "root-publication",
            "lastDurableStage": "children-complete",
            "retentionRequired": True,
            "retryAllowed": False,
            "schema": "text-to-cad.agent-runtime-verification-attempt/1",
            "status": "publication-failed",
            "subjectDigest": "sha256:" + "b" * 64,
        }
        tombstone = typed("verification-attempt", value)
        validate_tombstone(tombstone, subject_digest=value["subjectDigest"], attempt_authority_digest=value["attemptAuthorityDigest"])
        with self.assertRaisesRegex(EvidenceError, "subject binding"):
            validate_tombstone(tombstone, subject_digest=D, attempt_authority_digest=value["attemptAuthorityDigest"])
        self_ref = copy.deepcopy(value)
        self_ref["tombstoneDigest"] = digest(tombstone)
        with self.assertRaisesRegex(EvidenceError, "unexpected keys"):
            typed("verification-attempt", self_ref)
        wrong_stage = copy.deepcopy(value)
        wrong_stage["lastDurableStage"] = "root-written"
        with self.assertRaisesRegex(EvidenceError, "failure/stage pairing"):
            typed("verification-attempt", wrong_stage)

    def test_proof_only_documents_reject_secret_and_raw_dynamic_error(self) -> None:
        from scripts.pilot.agent_runtime import EvidenceError

        _, children = valid_graph()
        for key in ("secret", "rawError"):
            value = copy.deepcopy(children[3].value)
            value[key] = "do-not-publish"
            with self.subTest(key=key), self.assertRaisesRegex(EvidenceError, "unexpected keys"):
                typed("evidence", value)
        root, _ = valid_graph()
        root_value = copy.deepcopy(root.value)
        root_value["status"] = "failed"
        root_value["failureCheck"] = "raw docker timeout"
        with self.assertRaisesRegex(EvidenceError, "closed failureCheck"):
            typed("verification", root_value)


if __name__ == "__main__":
    unittest.main()
