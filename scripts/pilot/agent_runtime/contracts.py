"""Closed schema vocabulary for Agent runtime verification evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .canonical_json import EvidenceError, _freeze


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


ROOT_ROLES = (
    ("agent-lifecycle", "colima"),
    ("agent-lifecycle", "cvm"),
    ("browser-deny", None),
    ("build-input-set", None),
    ("build-provenance", None),
    ("codex-admission", None),
    ("dependency-admission", None),
    ("image-identity", None),
    ("sbom", None),
    ("source-snapshot", "colima"),
    ("source-snapshot", "cvm"),
)

DEPENDENCIES = {
    ("agent-lifecycle", "colima"): (("image-identity", None), ("browser-deny", None), ("source-snapshot", "colima")),
    ("agent-lifecycle", "cvm"): (("image-identity", None), ("browser-deny", None), ("source-snapshot", "cvm")),
    ("browser-deny", None): (("image-identity", None),),
    ("build-input-set", None): (),
    ("build-provenance", None): (("build-input-set", None),),
    ("codex-admission", None): (("dependency-admission", None),),
    ("dependency-admission", None): (("build-input-set", None),),
    ("image-identity", None): (("build-provenance", None), ("sbom", None)),
    ("sbom", None): (("build-provenance", None),),
    ("source-snapshot", "colima"): (),
    ("source-snapshot", "cvm"): (),
}

SUBJECT_FIELDS = {
    "build-input-set": ("buildInputSetDigest", "buildRecipeDigest", "baseImageManifestDigest", "ubuntuSnapshotManifestDigest", "dependencyLockDigest", "projectRuntimeArtifactSetDigest"),
    "build-provenance": ("buildInputSetDigest", "builderImageManifestDigest", "buildRecipeDigest", "baseImageManifestDigest", "outputImageManifestDigest", "outputImageConfigDigest"),
    "dependency-admission": ("buildInputSetDigest", "dependencyLockDigest", "aptClosureManifestDigest", "pythonWheelManifestDigest", "nodeArtifactDigest"),
    "codex-admission": (
        "codexVersion", "platform", "retrievalReceiptDigest", "archiveDigest",
        "executableDigest", "signatureBundleDigest", "signaturePolicyDigest",
        "signatureVerificationReceiptDigest", "elfClosureDigest",
    ),
    "sbom": ("agentImageManifestDigest", "sbomDigest", "format"),
    "image-identity": ("agentImageManifestDigest", "agentImageConfigDigest", "runtimeManifestDigest", "platform"),
    "browser-deny": ("agentImageManifestDigest", "scannerDigest", "inventoryDigest", "browserFindingCount", "chromiumProcessCount"),
    "source-snapshot": ("executionSourceSnapshotDigest", "sourceManifestDigest", "pathCount", "totalBytes"),
    "agent-lifecycle": ("agentImageManifestDigest", "agentImageConfigDigest", "runtimeManifestDigest", "executionSourceSnapshotDigest", "inputSnapshotDigest", "agentConfigDigest", "browserRuntimeCapabilityDigest", "workloadDigest", "lifecycleHarnessDigest", "entrypointDigest", "lifecycleReceiptSchemaDigest", "resourceDisposition", "cleanupDisposition"),
}

PREDICATES = {
    "build-input-set": ("manifestSchemaExact", "recipeBound", "baseManifestBound", "ubuntuSnapshotBound", "dependencyLockBound", "projectRuntimeArtifactsBound", "pathSetClosed", "fileDigestsBound", "immutableObjectVisible"),
    "build-provenance": ("builderIdentityExact", "buildRecipeDigestExact", "baseManifestDigestExact", "platformLinuxAmd64", "buildInputSetBound", "networkDisabled", "pullDisabled", "cleanContextAllowlisted", "outputManifestDigestExact", "outputConfigDigestExact"),
    "dependency-admission": ("ubuntuSnapshotPinned", "ubuntuMetadataAuthenticated", "debClosureComplete", "pythonWheelClosureComplete", "nativeMeshscopeWheelAdmitted", "browserFreeMeshshotWheelAdmitted", "nodeArtifactAdmitted", "runtimeFilesByteLocked", "offlineRebuildSucceeded"),
    "codex-admission": (
        "versionExact", "platformArtifactExact", "retrievalMetadataRecorded",
        "archiveDigestExact", "executableDigestExact", "archiveSingleExecutableExact",
        "signatureBundleDigestExact", "signaturePolicyExact", "signatureVerified",
        "certificateIdentityExact", "certificateIssuerExact", "transparencyLogVerified",
        "elfClosureClosed", "nodeAbsentSmokePassed", "noninteractiveSmokePassed",
        "immutableMirrorVisible",
    ),
    "sbom": ("formatExact", "subjectManifestDigestExact", "allRuntimeFilesCovered", "packageVersionsExact", "nativeLibrariesCovered", "licensesRecorded", "sbomDigestBound"),
    "image-identity": ("immutableReferenceExact", "manifestDigestObserved", "configDigestObserved", "runtimeManifestInsideImageExact", "osLinux", "architectureAmd64", "entrypointExact", "userNonRoot", "noMutableTagAuthority"),
    "browser-deny": ("packageInventoryEmpty", "executableInventoryEmpty", "cacheInventoryEmpty", "elfMarkerInventoryEmpty", "productMarkerInventoryEmpty", "playwrightInventoryEmpty", "chromiumProcessZero", "browserLifecycleAuthorityAbsent"),
    "source-snapshot": ("manifestSchemaExact", "pathSetClosed", "regularFilesOnly", "fileModesBound", "fileSizesBound", "fileDigestsBound", "treeDigestMatchesObservation", "readOnlyMountEligible"),
    "agent-lifecycle": ("adapterOperationsClosed", "authorityFresh", "jobPrivateLayoutExact", "snapshotIdentityExact", "workloadIdentityExact", "imageIdentityOuterAttested", "returnedContainerIdExact", "containerOwnershipExact", "inertContainerConfigExact", "readOnlyRoot", "sourceReadOnly", "inputReadOnly", "writableMountAllowlistExact", "dockerSocketAbsent", "capabilitiesEmpty", "noNewPrivileges", "externalNetworkAbsent", "entrypointPreflightExact", "browserRuntimeCapabilityIdentityBound", "workloadReleasedOnce", "terminalPublicationExact", "workloadProcessGroupAbsent", "descendantResidueFalse", "workloadNotInterrupted", "workloadTerminalZero", "containerCleanupSucceeded", "browserCapabilityCleanupSucceeded", "jobPrivateTreeCleanupSucceeded", "agentContainerAbsent", "ownerLabelsAbsent", "browserCapabilityAbsent", "jobPrivateTreeAbsent"),
}

LIFECYCLE_RESOURCE_FIELDS = ("agentContainer", "ownerLabels", "browserCapability", "jobPrivateTree", "workloadProcessGroup")
LIFECYCLE_CLEANUP_FIELDS = ("agentContainer", "browserCapability", "jobPrivateTree")
