"""Closed schema vocabulary for Agent runtime verification evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


class EvidenceError(ValueError):
    """The public proof does not satisfy the closed evidence contract."""


class _FrozenMapping(Mapping[str, Any]):
    """Recursively immutable mapping with an explicit mutable-copy escape hatch."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(values)))

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("evidence mappings are immutable")

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[str, Any]:
        import copy

        return {key: copy.deepcopy(value, memo) for key, value in self._values.items()}


class _FrozenSequence(tuple):
    """Immutable JSON array whose explicit deep copy is mutable for fixture editing."""

    def __deepcopy__(self, memo: dict[int, Any]) -> list[Any]:
        import copy

        return [copy.deepcopy(value, memo) for value in self]


@dataclass(frozen=True)
class EvidenceDocument:
    """A strictly parsed document tagged with its selected closed schema."""

    kind: str
    value: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze(self.value))


@dataclass(frozen=True)
class GraphValidation:
    """Successful validation of a complete terminal evidence graph."""

    status: str
    failure_check: str | None
    root_digest: str


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _FrozenMapping({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return _FrozenSequence(_freeze(item) for item in value)
    return value


ROOT_ROLES = (
    ("agent-lifecycle", "colima"),
    ("agent-lifecycle", "cvm"),
    ("browser-deny", None),
    ("build-input-set", None),
    ("build-provenance", None),
    ("capability-conformance", "colima"),
    ("capability-conformance", "cvm"),
    ("codex-admission", None),
    ("cup-golden", None),
    ("dependency-admission", None),
    ("image-identity", None),
    ("sbom", None),
    ("source-snapshot", "colima"),
    ("source-snapshot", "cvm"),
    ("verification-plan", None),
)

DEPENDENCIES = {
    ("agent-lifecycle", "colima"): (("image-identity", None), ("browser-deny", None), ("source-snapshot", "colima"), ("verification-plan", None)),
    ("agent-lifecycle", "cvm"): (("image-identity", None), ("browser-deny", None), ("source-snapshot", "cvm"), ("verification-plan", None)),
    ("browser-deny", None): (("image-identity", None), ("verification-plan", None)),
    ("build-input-set", None): (),
    ("build-provenance", None): (("build-input-set", None),),
    ("capability-conformance", "colima"): (("agent-lifecycle", "colima"), ("cup-golden", None), ("verification-plan", None)),
    ("capability-conformance", "cvm"): (("agent-lifecycle", "cvm"), ("cup-golden", None), ("verification-plan", None)),
    ("codex-admission", None): (("dependency-admission", None),),
    ("cup-golden", None): (("image-identity", None), ("verification-plan", None)),
    ("dependency-admission", None): (("build-input-set", None),),
    ("image-identity", None): (("build-provenance", None), ("sbom", None)),
    ("sbom", None): (("build-provenance", None),),
    ("source-snapshot", "colima"): (("verification-plan", None),),
    ("source-snapshot", "cvm"): (("verification-plan", None),),
    ("verification-plan", None): (),
}

SUBJECT_FIELDS = {
    "build-input-set": ("buildInputSetDigest", "buildRecipeDigest", "baseImageManifestDigest", "ubuntuSnapshotManifestDigest", "dependencyLockDigest", "projectRuntimeArtifactSetDigest"),
    "build-provenance": ("buildInputSetDigest", "builderImageManifestDigest", "buildRecipeDigest", "baseImageManifestDigest", "outputImageManifestDigest", "outputImageConfigDigest"),
    "dependency-admission": ("buildInputSetDigest", "dependencyLockDigest", "aptClosureManifestDigest", "pythonWheelManifestDigest", "nodeArtifactDigest", "implicitBundleDigest"),
    "codex-admission": ("codexVersion", "platform", "retrievalReceiptDigest", "archiveDigest", "executableDigest", "elfClosureDigest"),
    "sbom": ("agentImageManifestDigest", "sbomDigest", "format"),
    "image-identity": ("agentImageManifestDigest", "agentImageConfigDigest", "runtimeManifestDigest", "cupRuntimeCapabilityManifestDigest", "platform"),
    "browser-deny": ("agentImageManifestDigest", "scannerDigest", "inventoryDigest", "browserFindingCount", "chromiumProcessCount"),
    "cup-golden": ("agentImageManifestDigest", "fixtureDigest", "routerManifestDigest", "expectedOutputDigest", "observedOutputDigest", "faceCount", "watertight", "eulerNumber"),
    "source-snapshot": ("executionSourceSnapshotDigest", "sourceManifestDigest", "pathCount", "totalBytes"),
    "agent-lifecycle": ("agentImageManifestDigest", "agentImageConfigDigest", "runtimeManifestDigest", "executionSourceSnapshotDigest", "inputSnapshotDigest", "agentConfigDigest", "brokerAuthorityDigest", "workloadDigest", "lifecycleHarnessDigest", "entrypointDigest", "lifecycleReceiptSchemaDigest", "resourceDisposition", "cleanupDisposition"),
    "capability-conformance": ("agentImageManifestDigest", "runtimeManifestDigest", "cupRuntimeCapabilityManifestDigest", "executionSourceSnapshotDigest", "inputSnapshotDigest", "conformanceFixtureDigest", "expectedOutputDigest", "observedOutputDigest"),
    "verification-plan": ("verificationPlanDigest", "scannerDigest", "verificationSourceSnapshotDigest", "verificationSourceManifestDigest", "verificationInputSnapshotDigest", "cupFixtureDigest", "routerManifestDigest", "expectedOutputDigest", "conformanceFixtureDigest", "lifecycleHarnessDigest", "entrypointDigest", "lifecycleReceiptSchemaDigest", "agentConfigDigest", "brokerAuthorityDigest", "workloadDigest"),
}

PREDICATES = {
    "build-input-set": ("manifestSchemaExact", "recipeBound", "baseManifestBound", "ubuntuSnapshotBound", "dependencyLockBound", "projectRuntimeArtifactsBound", "pathSetClosed", "fileDigestsBound", "immutableObjectVisible"),
    "build-provenance": ("builderIdentityExact", "buildRecipeDigestExact", "baseManifestDigestExact", "platformLinuxAmd64", "buildInputSetBound", "networkDisabled", "pullDisabled", "cleanContextAllowlisted", "outputManifestDigestExact", "outputConfigDigestExact"),
    "dependency-admission": ("ubuntuSnapshotPinned", "ubuntuMetadataAuthenticated", "debClosureComplete", "pythonWheelClosureComplete", "nativeMeshscopeWheelAdmitted", "browserFreeMeshshotWheelAdmitted", "nodeArtifactAdmitted", "canonicalImplicitBundleClosed", "runtimeFilesByteLocked", "offlineRebuildSucceeded"),
    "codex-admission": ("versionExact", "platformArtifactExact", "retrievalMetadataRecorded", "archiveDigestExact", "executableDigestExact", "elfClosureClosed", "nodeAbsentSmokePassed", "noninteractiveSmokePassed", "immutableMirrorVisible", "publisherSignatureClaimAbsent"),
    "sbom": ("formatExact", "subjectManifestDigestExact", "allRuntimeFilesCovered", "packageVersionsExact", "nativeLibrariesCovered", "licensesRecorded", "sbomDigestBound"),
    "image-identity": ("immutableReferenceExact", "manifestDigestObserved", "configDigestObserved", "runtimeManifestInsideImageExact", "cupManifestInsideImageExact", "osLinux", "architectureAmd64", "entrypointExact", "userNonRoot", "noMutableTagAuthority"),
    "browser-deny": ("packageInventoryEmpty", "executableInventoryEmpty", "cacheInventoryEmpty", "elfMarkerInventoryEmpty", "productMarkerInventoryEmpty", "playwrightInventoryEmpty", "chromiumProcessZero", "browserLifecycleAuthorityAbsent"),
    "cup-golden": ("fixtureDigestExact", "formalRouterImplicitOnly", "faceCount3764", "watertightFalse", "eulerNumber144", "nodeImplicitSubsetExact", "meshscopeAccepted", "voxBlameAccepted", "residualBrokerPreviewAccepted", "outputDigestRepeatable"),
    "source-snapshot": ("manifestSchemaExact", "pathSetClosed", "regularFilesOnly", "fileModesBound", "fileSizesBound", "fileDigestsBound", "treeDigestMatchesObservation", "readOnlyMountEligible"),
    "verification-plan": ("planSchemaExact", "planDigestExact", "scannerApproved", "sourceSnapshotApproved", "sourceManifestApproved", "inputSnapshotApproved", "cupFixtureApproved", "routerManifestApproved", "expectedOutputApproved", "conformanceFixtureApproved", "lifecycleHarnessApproved", "entrypointApproved", "receiptSchemaApproved", "agentConfigApproved", "brokerAuthorityApproved", "workloadApproved"),
    "agent-lifecycle": ("adapterOperationsClosed", "authorityFresh", "jobPrivateLayoutExact", "snapshotIdentityExact", "workloadIdentityExact", "imageIdentityOuterAttested", "returnedContainerIdExact", "containerOwnershipExact", "inertContainerConfigExact", "readOnlyRoot", "sourceReadOnly", "inputReadOnly", "writableMountAllowlistExact", "dockerSocketAbsent", "capabilitiesEmpty", "noNewPrivileges", "externalNetworkAbsent", "entrypointPreflightExact", "brokerProofIdentityBound", "workloadReleasedOnce", "terminalPublicationExact", "workloadProcessGroupAbsent", "descendantResidueFalse", "workloadNotInterrupted", "workloadTerminalZero", "containerCleanupSucceeded", "brokerVolumeCleanupSucceeded", "jobPrivateTreeCleanupSucceeded", "agentContainerAbsent", "ownerLabelsAbsent", "brokerVolumeAbsent", "jobPrivateTreeAbsent"),
    "capability-conformance": ("runtimeManifestBound", "cupManifestBound", "sourceSnapshotBound", "inputSnapshotBound", "codexExecutableExact", "python312Exact", "nodeRuntimeExact", "canonicalImplicitSubsetExact", "meshscopeCallable", "voxBlameCallable", "brokerAuthorityJobPrivate", "residualPublicParity", "browserInventoryEmpty", "browserProcessZero", "dockerAuthorityAbsent", "credentialSurfaceEmpty", "providerNetworkDenied", "cupGoldenAccepted", "outputDigestBound", "terminalAndCleanupBound"),
}

LIFECYCLE_RESOURCE_FIELDS = ("agentContainer", "ownerLabels", "brokerVolume", "jobPrivateTree", "workloadProcessGroup")
LIFECYCLE_CLEANUP_FIELDS = ("agentContainer", "brokerVolume", "jobPrivateTree")
