"""Closed manifest producers for the first sealed Agent runtime Cup route."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .canonical_json import (
    CanonicalJSONInput,
    CanonicalJSONValue,
    canonical_json_bytes,
    canonical_json_digest,
    parse_canonical_json,
)


class ManifestError(ValueError):
    """A manifest does not satisfy its selected closed schema."""


@dataclass(frozen=True)
class ManifestDocument:
    """An immutable canonical JSON value tagged with its selected schema."""

    kind: str
    value: Mapping[str, CanonicalJSONValue]

    def __post_init__(self) -> None:
        try:
            frozen = parse_canonical_json(canonical_json_bytes(self.value))
        except ValueError as exc:
            raise ManifestError(str(exc)) from exc
        if not isinstance(frozen, Mapping):
            raise ManifestError("manifest must be a JSON object")
        object.__setattr__(
            self,
            "value",
            cast(Mapping[str, CanonicalJSONValue], frozen),
        )


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

VERIFICATION_PLAN_FIELDS = (
    "scannerDigest",
    "verificationSourceSnapshotDigest",
    "verificationSourceManifestDigest",
    "verificationInputSnapshotDigest",
    "cupFixtureDigest",
    "routerManifestDigest",
    "expectedOutputDigest",
    "conformanceFixtureDigest",
    "lifecycleHarnessDigest",
    "entrypointDigest",
    "lifecycleReceiptSchemaDigest",
    "agentConfigDigest",
    "brokerAuthorityDigest",
    "workloadDigest",
)
_CUP_PLAN_FIELDS = (
    "cupFixtureDigest",
    "routerManifestDigest",
    "expectedOutputDigest",
    "conformanceFixtureDigest",
)
VERIFICATION_PLAN_EXTERNAL_FIELDS = tuple(
    field for field in VERIFICATION_PLAN_FIELDS if field not in _CUP_PLAN_FIELDS
)

_EXECUTABLES = (
    "bash", "cat", "chmod", "codex@0.147.0", "cp", "env", "file", "find",
    "git", "git-lfs", "ls", "mkdir", "mv", "node@24.13.0", "ps",
    "python3@3.12", "rg", "rm", "sed", "sha256sum", "stat",
)
_FORMAL_OPERATIONS = (
    "mesh-inspect.numeric-route",
    "mesh-compare.voxblame-prepare-reference",
    "mesh-to-cad-workspace.init",
    "mesh-to-cad-workspace.begin-attempt",
    "mesh-to-cad-workspace.run",
    "implicit-cad.canonical-build",
    "mesh-compare.voxblame-measure",
    "mesh-compare.voxblame-targets",
    "mesh-compare.voxblame-diff",
    "mesh-compare.voxblame-preview",
    "mesh-compare.voxblame-verify",
    "mesh-to-cad-workspace.publish-step-zero",
    "mesh-to-cad-workspace.publish-cycle",
    "mesh-to-cad-workspace.record-attempt",
    "mesh-to-cad-workspace.finalize",
    "mesh-to-cad-workspace.recover",
    "mesh-to-cad-workspace.validate",
)
_PYTHON_IMPORTS = (
    "PIL", "meshscope", "meshscope.voxblame._native",
    "meshshot.broker_client", "numpy", "trimesh",
)
_NETWORK_CAPABILITIES = (
    "venus-retry-proxy-job-token", "browser-broker-job-private-unix-socket",
)
_RUNTIME_ENVIRONMENT = (
    "CODEX_HOME", "GIT_TERMINAL_PROMPT", "HOME", "LANG", "LC_ALL", "PATH",
    "PYTHONDONTWRITEBYTECODE", "TMPDIR", "TZ", "XDG_CACHE_HOME",
)
_WRITABLE_ROOTS = (
    "job-cache", "job-codex-home", "job-output", "job-tmp", "job-workspace",
)
_EXCLUDED_CAPABILITIES = (
    "apt", "build123d", "cad-snapshot", "cadpy", "cadquery-ocp", "chromium", "cloud-cli",
    "compiler", "curl", "dnf", "docker", "freecad", "host-codex-home",
    "host-git-config", "host-venv", "implicit-snapshot", "matplotlib", "mesh-preview",
    "npm", "pip", "playwright",
    "podman", "ros", "rsync", "scipy", "ssh", "uv", "viewer-server", "wget",
)

_FIXTURE_ROOT = PurePosixPath("models/agent-runtime/cup_cup_033")
_FIXTURE_PATHS = {
    "inputPath": _FIXTURE_ROOT / "input/cup_cup_033.ply",
    "sourcePath": _FIXTURE_ROOT / "source/cup_cup_033.implicit.js",
    "numericInspectionPath": _FIXTURE_ROOT / "numeric-inspection.json",
    "routeManifestPath": _FIXTURE_ROOT / "route.json",
    "expectedOutputPath": _FIXTURE_ROOT / "expected-output.json",
}
_CONFORMANCE_FIXTURE_PATH = _FIXTURE_ROOT / "conformance-fixture.json"
_OBSERVATIONS = {
    "degenerateFaces": 0,
    "eulerNumber": 144,
    "faceCount": 3764,
    "watertight": False,
}
_ROUTE_REJECTION_REASON = (
    "The numeric topology rule matches before machinable-feature or "
    "agent-judgment rules."
)


def _require_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise ManifestError(f"{label} has unexpected keys")
    return value


def _require_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or _DIGEST_RE.fullmatch(value) is None:
        raise ManifestError(f"{label} is not a canonical sha256 digest")


def _require_exact_sequence(value: Any, expected: tuple[str, ...], label: str) -> None:
    if not isinstance(value, Sequence) or isinstance(value, str):
        raise ManifestError(f"{label} must be an array")
    if tuple(value) != expected:
        raise ManifestError(f"{label} does not equal the closed allowlist")


def _require_cup_observations(value: Any, label: str) -> Mapping[str, Any]:
    observations = _require_keys(value, set(_OBSERVATIONS), label)
    for field in ("degenerateFaces", "eulerNumber", "faceCount"):
        if isinstance(observations[field], bool) or not isinstance(
            observations[field], int
        ):
            raise ManifestError(f"{label}.{field} must be an integer")
    if not isinstance(observations["watertight"], bool):
        raise ManifestError(f"{label}.watertight must be Boolean")
    if dict(observations) != _OBSERVATIONS:
        raise ManifestError(f"{label} are not the admitted Cup values")
    return observations


def _validate_verification_plan(value: Mapping[str, Any]) -> None:
    _require_keys(value, {"schema", *VERIFICATION_PLAN_FIELDS}, "verification plan")
    if value["schema"] != "text-to-cad.agent-runtime-verification-plan/1":
        raise ManifestError("verification plan schema is invalid")
    for field in VERIFICATION_PLAN_FIELDS:
        _require_digest(value[field], f"verification plan.{field}")


def _validate_numeric_inspection(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {"schema", "inputDigest", "faceCount", "watertight", "eulerNumber", "degenerateFaces"},
        "numeric inspection",
    )
    if value["schema"] != "text-to-cad.numeric-mesh-inspection/1":
        raise ManifestError("numeric inspection schema is invalid")
    _require_digest(value["inputDigest"], "numeric inspection.inputDigest")
    for field in ("faceCount", "eulerNumber", "degenerateFaces"):
        item = value[field]
        if isinstance(item, bool) or not isinstance(item, int):
            raise ManifestError(f"numeric inspection.{field} must be an integer")
    if value["faceCount"] < 0 or value["degenerateFaces"] < 0:
        raise ManifestError("numeric inspection counts must be nonnegative")
    if not isinstance(value["watertight"], bool):
        raise ManifestError("numeric inspection.watertight must be Boolean")


def _validate_numeric_route(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {"schema", "route", "matchedRule", "observations", "consideredAlternative"},
        "numeric route",
    )
    if value["schema"] != "text-to-cad.numeric-route/1":
        raise ManifestError("numeric route schema is invalid")
    if value["route"] != "implicit-cad" or value["matchedRule"] != "topology-not-occ-clean":
        raise ManifestError("numeric route is outside the Cup contract")
    _require_cup_observations(value["observations"], "numeric route observations")
    alternative = _require_keys(
        value["consideredAlternative"], {"route", "rejectedBecause"},
        "numeric route alternative",
    )
    if (
        alternative["route"] != "cad"
        or alternative["rejectedBecause"] != _ROUTE_REJECTION_REASON
    ):
        raise ManifestError("numeric route alternative is invalid")


def _validate_expected_output(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "schema", "fixtureId", "canonicalSourceDigest", "numericInspectionDigest",
            "routeManifestDigest", "route", "observations", "canonicalBuild",
            "providerDispatchCount", "deferredStages",
        },
        "Cup expected output",
    )
    if (
        value["schema"] != "text-to-cad.cup-expected-output/1"
        or value["fixtureId"] != "cup_cup_033"
    ):
        raise ManifestError("Cup expected output identity is invalid")
    for field in ("canonicalSourceDigest", "numericInspectionDigest", "routeManifestDigest"):
        _require_digest(value[field], f"Cup expected output.{field}")
    if (
        value["route"] != "implicit-cad"
        or isinstance(value["providerDispatchCount"], bool)
        or value["providerDispatchCount"] != 0
    ):
        raise ManifestError("Cup expected output route or dispatch count is invalid")
    _require_cup_observations(value["observations"], "Cup expected observations")
    _require_exact_sequence(
        value["deferredStages"],
        ("native-measurement", "broker-preview", "workspace-finalize-validate"),
        "Cup deferred stages",
    )
    build = _require_keys(
        value["canonicalBuild"],
        {"entrypoint", "measurementGlbDigest", "profileDigest", "profileId", "rebuildRecipeDigest"},
        "Cup canonical build",
    )
    if (
        build["entrypoint"] != "implicit-cad.canonical-build/1"
        or build["profileId"] != "implicit_voxblame_depth8/1"
    ):
        raise ManifestError("Cup canonical build identity is invalid")
    for field in ("measurementGlbDigest", "profileDigest", "rebuildRecipeDigest"):
        _require_digest(build[field], f"Cup canonical build.{field}")


def _validate_conformance_fixture(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "schema", "cupCapabilityManifestDigest", "cupFixtureDigest",
            "canonicalSourceDigest", "numericInspectionDigest",
            "routerManifestDigest", "expectedOutputDigest", "providerDispatchCount",
            "deferredStages",
        },
        "Cup conformance fixture",
    )
    if value["schema"] != "text-to-cad.cup-conformance-fixture/1":
        raise ManifestError("Cup conformance fixture schema is invalid")
    for field in (
        "cupCapabilityManifestDigest", "cupFixtureDigest", "canonicalSourceDigest",
        "numericInspectionDigest", "routerManifestDigest", "expectedOutputDigest",
    ):
        _require_digest(value[field], f"Cup conformance fixture.{field}")
    if (
        isinstance(value["providerDispatchCount"], bool)
        or value["providerDispatchCount"] != 0
    ):
        raise ManifestError("Cup conformance fixture dispatch count is invalid")
    _require_exact_sequence(
        value["deferredStages"],
        ("native-measurement", "broker-preview", "workspace-finalize-validate"),
        "Cup conformance deferred stages",
    )


def _validate_capability(value: Mapping[str, Any]) -> None:
    _require_keys(
        value,
        {
            "schema", "platform", "route", "fixture", "executables", "pythonImports",
            "formalOperations", "browserPrograms", "networkCapabilities",
            "runtimeEnvironment", "writableRoots", "excludedCapabilities",
        },
        "Cup capability manifest",
    )
    if value["schema"] != "text-to-cad.cup-runtime-capability-manifest/1":
        raise ManifestError("Cup capability schema is invalid")
    platform = _require_keys(
        value["platform"], {"architecture", "os"}, "Cup capability platform"
    )
    if (
        dict(platform) != {"architecture": "amd64", "os": "linux"}
        or value["route"] != "implicit-cad"
    ):
        raise ManifestError("Cup capability platform or route is invalid")
    for field, expected in (
        ("executables", _EXECUTABLES),
        ("pythonImports", _PYTHON_IMPORTS),
        ("formalOperations", _FORMAL_OPERATIONS),
        ("networkCapabilities", _NETWORK_CAPABILITIES),
        ("runtimeEnvironment", _RUNTIME_ENVIRONMENT),
        ("writableRoots", _WRITABLE_ROOTS),
        ("excludedCapabilities", _EXCLUDED_CAPABILITIES),
    ):
        _require_exact_sequence(value[field], expected, f"Cup capability.{field}")
    expected_program = {
        "id": "residual",
        "profile": "cadena_residual_eight_view/1",
        "programDigest": "sha256:d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962afae9ae02b0180b",
        "stages": ("step", "final"),
    }
    programs = value["browserPrograms"]
    if not isinstance(programs, Sequence) or isinstance(programs, str) or len(programs) != 1:
        raise ManifestError("Cup Browser program allowlist is invalid")
    program = _require_keys(
        programs[0], {"id", "profile", "programDigest", "stages"},
        "Cup Browser program",
    )
    if dict(program) != expected_program:
        raise ManifestError("Cup Browser program allowlist is invalid")
    fixture = _require_keys(
        value["fixture"],
        {
            "id", "inputPath", "inputDigest", "inputBytes", "sourcePath", "sourceDigest",
            "numericInspectionPath", "numericInspectionDigest", "routeManifestPath",
            "routeManifestDigest", "expectedOutputPath", "expectedOutputDigest",
        },
        "Cup capability fixture",
    )
    if fixture["id"] != "cup_cup_033" or fixture["inputBytes"] != 190047:
        raise ManifestError("Cup fixture identity or size is invalid")
    for path_field, expected_path in _FIXTURE_PATHS.items():
        if fixture[path_field] != expected_path.as_posix():
            raise ManifestError(f"Cup fixture {path_field} is invalid")
    for field in (
        "inputDigest", "sourceDigest", "numericInspectionDigest",
        "routeManifestDigest", "expectedOutputDigest",
    ):
        _require_digest(fixture[field], f"Cup fixture.{field}")


_VALIDATORS = {
    "verification-plan": _validate_verification_plan,
    "cup-capability": _validate_capability,
    "numeric-inspection": _validate_numeric_inspection,
    "numeric-route": _validate_numeric_route,
    "cup-expected-output": _validate_expected_output,
    "cup-conformance-fixture": _validate_conformance_fixture,
}


def parse_manifest_strict(kind: str, payload: bytes) -> ManifestDocument:
    """Select and validate one closed schema after canonical JSON parsing."""

    if kind not in _VALIDATORS:
        raise ManifestError(f"unknown manifest kind: {kind}")
    try:
        value = parse_canonical_json(payload)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if not isinstance(value, Mapping):
        raise ManifestError("manifest must be a JSON object")
    _VALIDATORS[kind](value)
    return ManifestDocument(kind, value)


def canonical_manifest_bytes(document: ManifestDocument) -> bytes:
    if not isinstance(document, ManifestDocument) or document.kind not in _VALIDATORS:
        raise ManifestError("canonical manifest encoder requires a typed manifest")
    _VALIDATORS[document.kind](document.value)
    try:
        return canonical_json_bytes(document.value)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


def manifest_digest(document: ManifestDocument) -> str:
    canonical_manifest_bytes(document)
    try:
        return canonical_json_digest(document.value)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc


def _file_bytes(path: Path, label: str) -> bytes:
    try:
        if path.is_symlink() or not path.is_file():
            raise ManifestError(f"{label} must be a regular file")
        return path.read_bytes()
    except ManifestError:
        raise
    except OSError as exc:
        raise ManifestError(f"cannot read {label}") from exc


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(_file_bytes(path, str(path))).hexdigest()


def _manifest_file_digest(kind: str, path: Path) -> str:
    return manifest_digest(
        parse_manifest_strict(kind, _file_bytes(path, f"{kind} manifest"))
    )


def _typed(kind: str, value: Mapping[str, CanonicalJSONInput]) -> ManifestDocument:
    try:
        payload = canonical_json_bytes(value)
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    return parse_manifest_strict(kind, payload)


def validate_cup_fixture_graph(repo_root: Path) -> dict[str, str]:
    """Validate every durable Cup edge before returning plan-owned bindings."""

    root = Path(repo_root).resolve()
    fixture_root = root / _FIXTURE_ROOT
    capability = parse_manifest_strict(
        "cup-capability",
        _file_bytes(
            fixture_root / "cup-capability-manifest.json",
            "stored Cup capability manifest",
        ),
    )
    produced_capability = build_cup_capability_manifest(root)
    if canonical_manifest_bytes(capability) != canonical_manifest_bytes(
        produced_capability
    ):
        raise ManifestError("stored Cup capability manifest substitution")
    conformance = parse_manifest_strict(
        "cup-conformance-fixture",
        _file_bytes(
            root / _CONFORMANCE_FIXTURE_PATH,
            "stored Cup conformance fixture",
        ),
    )
    numeric = parse_manifest_strict(
        "numeric-inspection",
        _file_bytes(
            root / _FIXTURE_PATHS["numericInspectionPath"],
            "stored Cup numeric inspection",
        ),
    )
    route = parse_manifest_strict(
        "numeric-route",
        _file_bytes(
            root / _FIXTURE_PATHS["routeManifestPath"],
            "stored Cup route manifest",
        ),
    )
    expected_output = parse_manifest_strict(
        "cup-expected-output",
        _file_bytes(
            root / _FIXTURE_PATHS["expectedOutputPath"],
            "stored Cup expected output",
        ),
    )
    expected = capability.value["fixture"]
    numeric_digest = manifest_digest(numeric)
    route_digest = manifest_digest(route)
    expected_output_digest = manifest_digest(expected_output)
    if numeric.value["inputDigest"] != expected["inputDigest"]:
        raise ManifestError("numeric inspection inputDigest substitution")
    numeric_observations = {
        field: numeric.value[field] for field in _OBSERVATIONS
    }
    if dict(route.value["observations"]) != numeric_observations:
        raise ManifestError("route observations do not bind numeric inspection")
    if expected_output.value["canonicalSourceDigest"] != expected["sourceDigest"]:
        raise ManifestError(
            "expected output canonicalSourceDigest substitution"
        )
    if expected_output.value["numericInspectionDigest"] != numeric_digest:
        raise ManifestError(
            "expected output numericInspectionDigest substitution"
        )
    if expected_output.value["routeManifestDigest"] != route_digest:
        raise ManifestError("expected output routeManifestDigest substitution")
    if expected_output.value["route"] != route.value["route"]:
        raise ManifestError("expected output route substitution")
    if dict(expected_output.value["observations"]) != numeric_observations:
        raise ManifestError("expected output observations do not bind numeric inspection")
    for field, observed_digest in (
        ("numericInspectionDigest", numeric_digest),
        ("routeManifestDigest", route_digest),
        ("expectedOutputDigest", expected_output_digest),
    ):
        if expected[field] != observed_digest:
            raise ManifestError(f"Cup capability {field} substitution")
    bindings = {
        "cupFixtureDigest": expected["inputDigest"],
        "routerManifestDigest": expected["routeManifestDigest"],
        "expectedOutputDigest": expected["expectedOutputDigest"],
        "conformanceFixtureDigest": manifest_digest(conformance),
    }
    conformance_expected = {
        "cupCapabilityManifestDigest": manifest_digest(capability),
        "cupFixtureDigest": bindings["cupFixtureDigest"],
        "canonicalSourceDigest": expected["sourceDigest"],
        "numericInspectionDigest": expected["numericInspectionDigest"],
        "routerManifestDigest": bindings["routerManifestDigest"],
        "expectedOutputDigest": bindings["expectedOutputDigest"],
    }
    for field, expected_digest in conformance_expected.items():
        if conformance.value[field] != expected_digest:
            raise ManifestError(f"Cup conformance fixture {field} substitution")
    return bindings


def build_verification_plan(
    bindings: Mapping[str, str], repo_root: Path
) -> ManifestDocument:
    if set(bindings) != set(VERIFICATION_PLAN_EXTERNAL_FIELDS):
        raise ManifestError(
            "verification plan external bindings must contain exactly the closed fields"
        )
    all_bindings = {**bindings, **validate_cup_fixture_graph(repo_root)}
    return _typed("verification-plan", {
        "schema": "text-to-cad.agent-runtime-verification-plan/1",
        **{field: all_bindings[field] for field in VERIFICATION_PLAN_FIELDS},
    })


def build_cup_capability_manifest(repo_root: Path) -> ManifestDocument:
    """Produce the Cup allowlist from durable fixture bytes, never observations."""

    root = Path(repo_root).resolve()
    input_path = root / _FIXTURE_PATHS["inputPath"]
    input_bytes = _file_bytes(input_path, "Cup input fixture")
    fixture = {
        "id": "cup_cup_033",
        "inputPath": _FIXTURE_PATHS["inputPath"].as_posix(),
        "inputDigest": "sha256:" + hashlib.sha256(input_bytes).hexdigest(),
        "inputBytes": len(input_bytes),
        "sourcePath": _FIXTURE_PATHS["sourcePath"].as_posix(),
        "sourceDigest": _file_digest(root / _FIXTURE_PATHS["sourcePath"]),
        "numericInspectionPath": _FIXTURE_PATHS["numericInspectionPath"].as_posix(),
        "numericInspectionDigest": _manifest_file_digest(
            "numeric-inspection", root / _FIXTURE_PATHS["numericInspectionPath"]
        ),
        "routeManifestPath": _FIXTURE_PATHS["routeManifestPath"].as_posix(),
        "routeManifestDigest": _manifest_file_digest(
            "numeric-route", root / _FIXTURE_PATHS["routeManifestPath"]
        ),
        "expectedOutputPath": _FIXTURE_PATHS["expectedOutputPath"].as_posix(),
        "expectedOutputDigest": _manifest_file_digest(
            "cup-expected-output", root / _FIXTURE_PATHS["expectedOutputPath"]
        ),
    }
    return _typed("cup-capability", {
        "browserPrograms": [{
            "id": "residual",
            "profile": "cadena_residual_eight_view/1",
            "programDigest": (
                "sha256:d2138ad7f3b74094862cfa8bd4d3ee0fb59ba8bde89a82962"
                "afae9ae02b0180b"
            ),
            "stages": ["step", "final"],
        }],
        "excludedCapabilities": list(_EXCLUDED_CAPABILITIES),
        "executables": list(_EXECUTABLES),
        "fixture": fixture,
        "formalOperations": list(_FORMAL_OPERATIONS),
        "networkCapabilities": list(_NETWORK_CAPABILITIES),
        "platform": {"architecture": "amd64", "os": "linux"},
        "pythonImports": list(_PYTHON_IMPORTS),
        "route": "implicit-cad",
        "runtimeEnvironment": list(_RUNTIME_ENVIRONMENT),
        "schema": "text-to-cad.cup-runtime-capability-manifest/1",
        "writableRoots": list(_WRITABLE_ROOTS),
    })


def build_numeric_inspection(
    input_digest: str, mesh_inspect_output: Mapping[str, Any]
) -> ManifestDocument:
    """Project browser-free mesh-inspect output onto the closed routing fields."""

    _require_digest(input_digest, "numeric inspection input")
    if not isinstance(mesh_inspect_output, Mapping) or mesh_inspect_output.get("ok") is not True:
        raise ManifestError("mesh-inspect did not produce a successful numeric report")
    stats = mesh_inspect_output.get("stats")
    quality = mesh_inspect_output.get("quality")
    if not isinstance(stats, Mapping) or not isinstance(quality, Mapping):
        raise ManifestError("mesh-inspect numeric report is missing stats or quality")
    return _typed("numeric-inspection", {
        "degenerateFaces": quality.get("degenerate_faces"),
        "eulerNumber": quality.get("euler_number"),
        "faceCount": stats.get("faces"),
        "inputDigest": input_digest,
        "schema": "text-to-cad.numeric-mesh-inspection/1",
        "watertight": quality.get("watertight"),
    })


def inspect_numeric_route(inspection: ManifestDocument) -> ManifestDocument:
    """Apply the closed numeric Cup routing seam without preview/browser data."""

    if not isinstance(inspection, ManifestDocument) or inspection.kind != "numeric-inspection":
        raise ManifestError("numeric route requires a typed numeric inspection")
    value = inspection.value
    if value["inputDigest"] != (
        "sha256:3d4c7ca9118ef8a6d4ae3e7af3117250ca824ad5b8de36dcfa2c66cece47ae67"
    ):
        raise ManifestError("numeric route input is not the admitted Cup fixture")
    observations = {field: value[field] for field in _OBSERVATIONS}
    if not (
        value["faceCount"] > 100000
        or value["eulerNumber"] != 2
        or value["degenerateFaces"] > 0
    ):
        raise ManifestError("numeric Cup topology does not select implicit-cad")
    return _typed("numeric-route", {
        "consideredAlternative": {
            "rejectedBecause": _ROUTE_REJECTION_REASON,
            "route": "cad",
        },
        "matchedRule": "topology-not-occ-clean",
        "observations": observations,
        "route": "implicit-cad",
        "schema": "text-to-cad.numeric-route/1",
    })
