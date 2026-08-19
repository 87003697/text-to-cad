# Provider-Free Browser Runtime Deep-Module Design

Status: Superseded before implementation

Date: 2026-08-14

Decision authority:
[ADR 0004](../adr/0004-own-provider-free-browser-lifecycle-by-authority.md).
Current Development implementation:
[`packages/browser_runtime`](../../packages/browser_runtime/README.md).

This document records the fallback deep-module design considered before the
outer-owned OCI Browser Sidecar decision in ADR 0004. It is not the current
implementation plan. Retain it as design evidence for why the legacy
`_PinnedExecutable` runtime should not be reorganized before the Sidecar
prototype decides which code is deleted.

## Goal

Preserve the existing residual-preview Interface while concentrating browser
image authority, Source-Hidden execution, process ownership, CDP control,
cleanup arbitration, and evidence construction behind one private execution
Seam. The design must reduce synchronization work without weakening any
Issue #62–#67 invariant.

## Design It Twice result

Three alternatives were considered:

1. A two-entry fixed-topology Module maximized Interface Depth, but risked
   replacing `_PinnedExecutable` with another oversized authority object.
2. A sealed job plus opaque capabilities maximized Locality, but some proposed
   seams represented hypothetical future Adapters.
3. A caller-first single authority kept the common path trivial, but placed too
   many independent ownership rules in one implementation object.

The selected design is a hybrid: keep the caller-first external Interface,
adopt the sealed one-shot executor as the only private execution Seam, and use
small internal authority-owner Modules without exposing their lifecycle.

## External Interface

The existing Interface remains the only caller surface:

```python
def render_residual_preview(
    reference: MeshGeometry,
    candidate: MeshGeometry,
    *,
    variant: Literal["step", "final"] = "step",
    exterior_directions: Sequence[OutsideDirection] = (),
) -> RenderedPreview: ...
```

Callers do not learn browser mode, executable, source path, endpoint, argv,
environment, namespace, timeout, cleanup policy, or Adapter choice. A call
still means one fixed eight-view render and returns only after terminal cleanup.

## Private execution Seam

```python
class _BrowserExecutor(Protocol):
    def execute(
        self,
        job: _SealedResidualBrowserJob,
    ) -> _BrowserExecution: ...
```

`_SealedResidualBrowserJob` can only be created by the preview renderer. It
contains canonical payload bytes, fixed assets and digests, the fixed render
entrypoint, expected view/profile identity, and frozen budgets. It cannot
express an arbitrary URL, script, endpoint, browser path, or workload.

`_BrowserExecution` contains renderer value, bounded renderer events, and
closed runtime evidence. It never exposes a browser, page, connection, process,
descriptor, path, endpoint, or cleanup handle.

This Seam is real because it has two production Adapters:

- `_DarwinLocalBrowserExecutor` preserves the current Darwin behavior.
- `_LinuxSupervisedBrowserExecutor` owns Source-Hidden execution, the fixed
  supervisor topology, loopback CDP, and terminal process cleanup.

There is no Adapter registry or environment-selected implementation. A closed
composition function selects one of the two fixed Adapters from the validated
execution role and platform.

## Linux topology

The source-owning materializer and the renderer must not share a renderer-
visible process root:

```text
source-owning staging namespace
  ├─ read-only deployment source
  ├─ copy, manifest validation, freeze
  └─ launch sealed render namespace
       ├─ private PID namespace and private /proc
       ├─ read-only Browser Execution Tree only
       ├─ browser supervisor
       ├─ nested renderer
       └─ Chromium process group
```

The sealed render namespace contains no source or writable materialization
alias. Its private `/proc` exposes only processes whose roots are already
Source-Hidden. The staging process remains outside that PID namespace. The
execution tree is manifest-bound and read-only before any browser exec.

The production-shaped gate must enumerate every renderer-visible process root;
resolving any direct or ancestor source alias is a failure. Merely proving that
an alias rejects writes is insufficient.

## Internal Modules

These Modules are private implementation, not new caller Interfaces:

### `_FrozenBrowserTree`

Owns complete source traversal, canonical manifest comparison, exact copy,
freeze, and post-freeze verification. It yields an opaque frozen-tree value,
not a path.

### `_BrowserExecutionTree`

Owns the read-only execution mount and Source-Hidden transition. Its creation
either returns an opaque capability already safe for exec or a typed failure;
there is no public `mount`, `hide`, `detach`, or `close` ordering Interface.

### `_OwnedBrowserProcess`

Owns fixed argv, process group, readiness, listener ownership, live image and
version proofs, TERM-to-KILL, and final group-empty proof. It yields only an
opaque loopback CDP authority to the executor.

### `_CleanupLedger`

Owns cleanup arbitration for one authority owner:

```python
ledger.record_exact(owner, predicate)
ledger.record_retained(owner, predicate)
outcome = ledger.outcome()
```

The first exact predicate wins within an owner. The first positive Retained
Resource Proof overrides an ordinary predicate; proof errors and inability to
prove absence are ordinary failures. Parent and child owner outcomes are then
combined once: an independently failing outer cleanup overrides a private
diagnostic because the outer owner is responsible for terminal residue.

No other Module implements `if first or retained` logic.

### Cleanup contract source

One declarative contract defines valid owner/predicate pairs and schema
versions. Build tooling generates frozen tables for runtime, protocol, and
canonical/generated reviewers. Reviewers independently parse and validate
receipts; they never import producer implementation.

## Dependencies and Adapters

| Dependency | Category | Design |
| --- | --- | --- |
| Manifest, cleanup arbitration, evidence projection | In-process | Direct private Modules; no Adapter |
| Filesystem, mount, process, signal, socket, clock | Local-substitutable | One coarse package-private kernel Adapter plus scripted test Adapter |
| Darwin versus Linux execution | Local-substitutable | Two real `_BrowserExecutor` Adapters |
| Playwright CDP control | Local-substitutable | Concrete implementation for now; no Protocol until a second real control Adapter exists |
| Linux namespace and Chromium semantics | Kernel-dependent | Mandatory real Linux conformance gate; never replaced by fakes |

## Test surface

Tests cross the same private executor Seam or the public render Interface:

1. Characterization tests freeze current PNG, view, evidence, error, and Darwin
   behavior at the public Interface.
2. Executor contract tests use a scripted kernel Adapter for construction,
   interruption, first predicate, retained proof, and cleanup ordering.
3. Real Linux conformance proves Source-Hidden roots, read-only execution,
   manifest identity, loopback ownership, CDP version, process-group cleanup,
   and namespace discard.
4. Protocol and reviewer tests consume generated frozen contracts and retain
   independent duplicate, tamper, unknown, and binding adversaries.

As these tests turn green, delete tests that patch `_PinnedExecutable` private
fields or assert individual syscall ordering. Replace them; do not layer the
new suite on top of the old white-box suite.

## Migration

### Ticket A: close Source-Hidden

- RED the current direct and ancestor source visibility.
- Introduce the sealed render namespace topology.
- Keep public evidence and cleanup vocabulary unchanged.
- Pass real Linux, Darwin, affected matrix, parity, and dual review.

### Ticket B: deepen the Module

- Add the private sealed-job executor Seam and two real Adapters.
- Move tree, execution-image, process, and cleanup knowledge into their owners.
- Generate frozen cleanup contracts from one declaration.
- Split browser infrastructure from scenario orchestration.
- Replace white-box tests with Interface and Adapter conformance tests.
- Preserve serialized public behavior byte-for-byte.

Only after both tickets pass independent Standards and Spec review may one new
clean SHA proceed through the repository CVM workflow.

## Deletion test

Deleting the private Browser Executor would force caller and scenario code to
relearn platform selection, source hiding, manifest authority, process groups,
listener ownership, CDP, cleanup precedence, and evidence projection. That
complexity would reappear across several callers and reviewers, so the Module
earns its Depth. Deleting any internal owner should move one coherent authority
back into the executor, not scatter knowledge outside the Module.
