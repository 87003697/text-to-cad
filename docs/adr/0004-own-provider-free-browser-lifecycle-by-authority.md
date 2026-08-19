# Own provider-free browser lifecycle with a job sidecar

Status: Accepted

Date: 2026-08-14

Scope: Long-term Formal provider-free runtime. The current
[`browser_runtime`](../../packages/browser_runtime/README.md) is a
Development-only, general-purpose Playwright MCP container; it deliberately
does not claim this ADR's fixed-program authority, sealed evidence, or Formal
conformance.

## Decision

The outer job authority owns one digest-pinned Local Rendering Browser sidecar.
The sidecar is pre-provisioned on Colima and CVM, starts outside the Agent
sandbox, receives no source mount or external network, and exposes only a
job-private browser connection. Missing image, identity mismatch, startup
failure, or terminal cleanup failure is closed; there is no fallback to an
Agent-owned Chromium process.

The Local Rendering Browser is not a general web agent. It accepts only
versioned, repository-owned Render Programs. Its intended programs include the
interactive CAD Viewer and the formal eight-view residual renderer.

There is one sidecar per job. A sidecar may execute multiple Render Programs
during that job, but every render starts in a fresh Playwright browser context
and page. Jobs never share a browser process, profile, connection, or context.
Render Programs receive structured inputs and fixed option schemas; they cannot
express arbitrary HTML, JavaScript, URLs, browser arguments, or endpoints.

The sidecar runs a version-matched Playwright Server and clients use
`playwright.connect()`. Raw CDP is not a parallel fallback. It may be retained
only if a prototype proves that a required fixed rendering behavior is not
available through the Playwright protocol.

The first release does not publish a human-facing live sidecar URL. The Agent
may exercise the complete CAD Viewer UI through Playwright and capture bounded
screenshots and inspection results. Human interactive handoff continues to use
the existing CAD Viewer workflow, avoiding a new authenticated port and session
lifecycle in the provider-free job.

Built CAD Viewer and residual-render assets ship inside the digest-pinned
sidecar image. A job supplies structured model data and validated options only;
the sidecar does not mount the source workspace, run a build, or accept a script
bundle from the Agent.

The first migration preserves `render_residual_preview`, PNG bytes, view order,
and public evidence schemas. After the sidecar is proven, a separate migration
may move the CAD Viewer and residual renderer onto shared `cadjs` rendering
primitives without changing their distinct observable behavior.

The sidecar artifact may be installed before a job and pinned by immutable
image digest; Provider-Free forbids runtime browser-provider calls and browser
downloads, not pre-provisioned trusted artifacts. Cleanup vocabulary has one
declarative source that generates frozen tables for isolated runtimes and
reviewers. Reviewers continue to parse and validate evidence independently.

The only production execution Adapter is an OCI Browser Sidecar using the same
`linux/amd64` child-image digest in Colima and CVM. There is no host-process or
bwrap Sidecar fallback. Colima is the local conformance environment and CVM is
the production environment.

Before production implementation, a throwaway prototype must prove that the
outer runtime can own the sidecar, the nested Agent can render without browser
spawn authority, and terminal cleanup can be projected into closed evidence.
No new production CVM run occurs before the resulting implementation passes
independent Standards and Spec review.

## Considered Options

- Continue predicate-by-predicate patches inside `_PinnedExecutable`: rejected
  because browser lifecycle is naturally owned by the outer runtime and one
  cleanup change repeatedly requires synchronized edits across producers,
  projectors, reviewers, and tests.
- Run Chromium inside the Agent sandbox: rejected because it gives untrusted
  nested code browser spawn and lifecycle authority and recreates the current
  sandbox conflict.
- Use a managed browser provider: rejected for the Provider-Free production
  path because it requires runtime provider access and egress.
- Treat visible read-only source aliases as hidden: rejected because visibility
  and mutation authority are different security guarantees.

## Consequences

The browser image becomes a deployment artifact rather than a tree reconstructed
inside every render job. The outer runtime, not application Python, owns browser
startup, isolation, termination, and retained-resource accounting. Repository
code still binds the approved image and job identity, verifies Source-Hidden,
and maps outer terminal facts into closed evidence.

Migration parity is byte-for-byte between legacy and Sidecar paths when both
run in the same environment. Colima and CVM retain the existing formal render
contract but are not required to emit byte-identical WebGL pixels across
different virtual CPU/GPU implementations.

The sidecar does not make the CAD Viewer and formal residual renderer identical:
they share `cadjs` geometry, camera, WebGL-renderer, and disposal primitives,
but retain separate Render Programs and observable contracts. The Viewer keeps
its interactive behavior; the residual program keeps fixed cameras, dimensions,
view order, and pixel semantics.

After sidecar parity, independent review, and one successful production CVM
run, the legacy `_PinnedExecutable` production path is removed rather than kept
as a fallback. Git history and narrowly scoped parity fixtures remain sufficient
for comparison.

A follow-up `cadjs` convergence ticket is mandatory before the browser-runtime
program is closed. It must prove pixel and interaction parity, move only truly
identical rendering primitives, and record why any non-equivalent behavior
remains in its separate Render Program. The convergence ticket does not block
the first sidecar release.

The first Sidecar release preserves the existing public evidence schema. A
subsequent versioned migration replaces file-, mount-, and descriptor-level
browser cleanup details with the Sidecar Artifact digest, Render Program
digest, isolation result, and outer terminal-cleanup result. The compatibility
window is explicit; legacy low-level evidence is then retired rather than
simulated indefinitely.
