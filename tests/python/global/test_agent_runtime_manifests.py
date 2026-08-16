from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_ROOT = REPO_ROOT / "models" / "agent-runtime" / "cup_cup_033"


def canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode("ascii")


class AgentRuntimeManifestTests(unittest.TestCase):
    def test_checked_in_cup_manifest_is_exactly_reproduced(self) -> None:
        from scripts.pilot.agent_runtime.manifests import (
            build_cup_capability_manifest,
            canonical_manifest_bytes,
            manifest_digest,
            parse_manifest_strict,
        )

        stored_bytes = (FIXTURE_ROOT / "cup-capability-manifest.json").read_bytes()
        expected_bytes = stored_bytes.removesuffix(b"\n")
        expected = parse_manifest_strict("cup-capability", stored_bytes)
        produced = build_cup_capability_manifest(REPO_ROOT)

        self.assertEqual(canonical_manifest_bytes(produced), expected_bytes)
        self.assertEqual(produced, expected)
        self.assertEqual(
            manifest_digest(produced),
            "sha256:" + hashlib.sha256(expected_bytes).hexdigest(),
        )

    def test_manifest_parser_is_closed_canonical_and_integer_only(self) -> None:
        from scripts.pilot.agent_runtime.manifests import (
            ManifestError,
            canonical_manifest_bytes,
            parse_manifest_strict,
        )

        payload = (FIXTURE_ROOT / "cup-capability-manifest.json").read_bytes()
        document = parse_manifest_strict("cup-capability", payload)
        self.assertEqual(canonical_manifest_bytes(document), payload.removesuffix(b"\n"))
        before = canonical_manifest_bytes(document)
        with self.assertRaises(TypeError):
            document.value["route"] = "cad"
        with self.assertRaises(TypeError):
            document.value["fixture"]["inputBytes"] = 0
        self.assertEqual(canonical_manifest_bytes(document), before)

        value = copy.deepcopy(document.value)
        value["secret"] = "must-not-pass"
        with self.assertRaisesRegex(ManifestError, "unexpected keys"):
            parse_manifest_strict("cup-capability", canonical(value))
        with self.assertRaisesRegex(ManifestError, "non-canonical"):
            parse_manifest_strict("cup-capability", b"{ " + payload[1:])
        with self.assertRaisesRegex(ManifestError, "numbers"):
            parse_manifest_strict(
                "cup-capability", payload.replace(b"190047", b"190047.0", 1)
            )
        expected_output = json.loads(
            (FIXTURE_ROOT / "expected-output.json").read_bytes()
        )
        expected_output["providerDispatchCount"] = False
        with self.assertRaisesRegex(ManifestError, "dispatch count"):
            parse_manifest_strict("cup-expected-output", canonical(expected_output))
        route = json.loads((FIXTURE_ROOT / "route.json").read_bytes())
        route["observations"]["degenerateFaces"] = False
        with self.assertRaisesRegex(ManifestError, "must be an integer"):
            parse_manifest_strict("numeric-route", canonical(route))

    def test_verification_plan_binds_exact_closed_inputs_without_self_hash(self) -> None:
        from scripts.pilot.agent_runtime.manifests import (
            VERIFICATION_PLAN_FIELDS,
            VERIFICATION_PLAN_EXTERNAL_FIELDS,
            build_verification_plan,
            canonical_manifest_bytes,
            manifest_digest,
            parse_manifest_strict,
        )

        bindings = {
            field: "sha256:" + f"{index:064x}"
            for index, field in enumerate(VERIFICATION_PLAN_EXTERNAL_FIELDS, start=1)
        }
        plan = build_verification_plan(bindings, REPO_ROOT)
        self.assertEqual(set(plan.value), {"schema", *VERIFICATION_PLAN_FIELDS})
        self.assertNotIn("verificationPlanDigest", plan.value)
        self.assertEqual(
            plan.value["cupFixtureDigest"],
            "sha256:3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67",
        )
        self.assertEqual(
            plan.value["routerManifestDigest"],
            "sha256:5b2164ffab9f806fd42422b1a07418b0bac7fb782c49b8c6c11bc9c9de6b3f45",
        )
        self.assertEqual(
            plan.value["expectedOutputDigest"],
            "sha256:cce4b63c67e1fde467918c7109dd1a8b9c429dc7b31407dd62d7222a6e9a12ac",
        )
        self.assertEqual(
            plan.value["conformanceFixtureDigest"],
            "sha256:bb7cd07a62e11bd8a403a020c94df8e8baf072173e97e1c8657b2442e85fc732",
        )
        self.assertEqual(
            parse_manifest_strict("verification-plan", canonical_manifest_bytes(plan)), plan
        )
        self.assertEqual(
            manifest_digest(plan),
            "sha256:" + hashlib.sha256(canonical_manifest_bytes(plan)).hexdigest(),
        )

        missing = dict(bindings)
        missing.pop("scannerDigest")
        with self.assertRaisesRegex(ValueError, "exactly"):
            build_verification_plan(missing, REPO_ROOT)
        substitution = dict(bindings)
        substitution["routerManifestDigest"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(ValueError, "exactly"):
            build_verification_plan(substitution, REPO_ROOT)

    def test_numeric_inspection_routes_cup_to_implicit_without_browser_data(self) -> None:
        from scripts.pilot.agent_runtime.manifests import (
            ManifestError,
            build_numeric_inspection,
            inspect_numeric_route,
            parse_manifest_strict,
        )

        expected_inspection = parse_manifest_strict(
            "numeric-inspection", (FIXTURE_ROOT / "numeric-inspection.json").read_bytes()
        )
        inspection = build_numeric_inspection(
            "sha256:3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67",
            {
                "ok": True,
                "stats": {"faces": 3764},
                "quality": {
                    "degenerate_faces": 0,
                    "euler_number": 144,
                    "watertight": False,
                },
            },
        )
        self.assertEqual(inspection, expected_inspection)
        route = inspect_numeric_route(inspection)
        expected = parse_manifest_strict(
            "numeric-route", (FIXTURE_ROOT / "route.json").read_bytes()
        )
        self.assertEqual(route, expected)
        self.assertEqual(route.value["route"], "implicit-cad")
        self.assertEqual(route.value["matchedRule"], "topology-not-occ-clean")
        self.assertEqual(
            route.value["observations"],
            {"degenerateFaces": 0, "eulerNumber": 144, "faceCount": 3764, "watertight": False},
        )

        clean_topology = copy.deepcopy(inspection.value)
        clean_topology["eulerNumber"] = 2
        with self.assertRaisesRegex(ManifestError, "does not select"):
            inspect_numeric_route(parse_manifest_strict(
                "numeric-inspection", canonical(clean_topology)
            ))

    def test_durable_fixture_bytes_and_expected_output_are_digest_bound(self) -> None:
        from scripts.pilot.agent_runtime.manifests import (
            manifest_digest,
            parse_manifest_strict,
        )

        input_path = FIXTURE_ROOT / "input" / "cup_cup_033.ply"
        self.assertEqual(input_path.stat().st_size, 190047)
        self.assertEqual(
            hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67",
        )
        expected = parse_manifest_strict(
            "cup-expected-output", (FIXTURE_ROOT / "expected-output.json").read_bytes()
        )
        self.assertEqual(
            expected.value["canonicalSourceDigest"],
            "sha256:" + hashlib.sha256(
                (FIXTURE_ROOT / "source/cup_cup_033.implicit.js").read_bytes()
            ).hexdigest(),
        )
        for field, kind, relative_path in (
            ("numericInspectionDigest", "numeric-inspection", "numeric-inspection.json"),
            ("routeManifestDigest", "numeric-route", "route.json"),
        ):
            observed = manifest_digest(parse_manifest_strict(
                kind, (FIXTURE_ROOT / relative_path).read_bytes()
            ))
            self.assertEqual(expected.value[field], observed)
        self.assertEqual(expected.value["providerDispatchCount"], 0)
        self.assertEqual(tuple(expected.value["deferredStages"]), (
            "native-measurement", "broker-preview", "workspace-finalize-validate"
        ))

    def test_cup_source_build_and_rebuild_match_the_provider_free_golden(self) -> None:
        from scripts.pilot.agent_runtime.manifests import parse_manifest_strict

        expected = parse_manifest_strict(
            "cup-expected-output", (FIXTURE_ROOT / "expected-output.json").read_bytes()
        ).value
        cli = REPO_ROOT / "packages" / "implicitjs" / "scripts" / "canonical-build.mjs"
        with tempfile.TemporaryDirectory(prefix="sealed-cup-golden-") as directory:
            root = Path(directory)
            shutil.copyfile(
                FIXTURE_ROOT / "source" / "cup_cup_033.implicit.js",
                root / "cup_cup_033.implicit.js",
            )
            subprocess.run(
                [
                    "node", str(cli), "--source", "cup_cup_033.implicit.js",
                    "--output-dir", "build", "--json",
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                [
                    "node", str(cli), "--recipe", "rebuild.json",
                    "--output-dir", "rebuilt", "--json",
                ],
                cwd=root / "build",
                check=True,
                capture_output=True,
                text=True,
            )
            for build_root in (root / "build", root / "build" / "rebuilt"):
                self.assertEqual(
                    "sha256:" + hashlib.sha256(
                        (build_root / "artifacts" / "model.glb").read_bytes()
                    ).hexdigest(),
                    expected["canonicalBuild"]["measurementGlbDigest"],
                )
                self.assertEqual(
                    "sha256:" + hashlib.sha256(
                        (build_root / "profile.json").read_bytes()
                    ).hexdigest(),
                    expected["canonicalBuild"]["profileDigest"],
                )
                self.assertEqual(
                    "sha256:" + hashlib.sha256(
                        (build_root / "rebuild.json").read_bytes()
                    ).hexdigest(),
                    expected["canonicalBuild"]["rebuildRecipeDigest"],
                )


if __name__ == "__main__":
    unittest.main()
