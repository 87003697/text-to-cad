# Architecture and research map

The documentation under this directory has four distinct roles. Keep a fact in
the most authoritative role that owns it; link to that source instead of
copying it into several documents.

## Authority order

1. [`CONTEXT.md`](../CONTEXT.md) defines canonical domain language only.
2. [`adr/`](adr/) records accepted architectural decisions and their trade-offs.
3. [`design/`](design/) describes interfaces, seams, contracts, and execution
   plans. Each design document must state whether it is current, historical, or
   superseded.
4. [`research/`](research/) preserves dated evidence and decision inputs. It is
   not normative after an ADR or implementation supersedes it.

Runbooks and specifications remain operational or normative only within the
scope they declare. Generated receipts and model fixtures belong under
`models/`, not in research notes.

## Current runtime thread

- [Provider-free browser lifecycle decision](adr/0004-own-provider-free-browser-lifecycle-by-authority.md)
  — long-term Formal architecture.
- [Development browser runtime](../packages/browser_runtime/README.md) — current
  per-job Playwright MCP implementation; intentionally not Formal conformance.
- [Fallback browser deep-module design](design/provider-free-browser-runtime-deep-module.md)
  — superseded design evidence retained for comparison.
- [Sealed Agent runtime implementation specification](design/sealed-agent-runtime-implementation-spec.md)
  — implementation boundary and terminology for the Formal Agent artifact.

## Research and learning

- [Agent-sandbox browser options](research/agent-sandbox-browser-options.md)
  records the market and architecture investigation behind ADR 0004.
- [Reviewer-instruction comparison](research/review-agent-instructions-comparison-2026-08-17.md)
  records the first-party evidence behind the compact reviewer contract.
- [Provider-free browser design lesson](learning/provider-free-browser-design/README.md)
  is a self-contained learning artifact; it makes no implementation or Formal
  verification claim.
