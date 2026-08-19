# Copyable `/goal`: implement and verify the sealed Agent runtime

Status: superseded. Do not run this goal; its CAD workload and paid pilot were
retired. The remaining text is retained only as non-executable design history.

```text
/goal

Objective
Implement the reviewed sealed Agent runtime from SAI-001 through SAI-015,
ending only when either (a) the exact Agent Runtime Verified root and separate
Formal Pilot Integrated receipt are published, or (b) a closed terminal blocker
is proven and retained. Do not treat prototypes, unit tests, Colima-only runs,
CVM-only runs, an image build, or Agent Runtime Verified alone as Formal Pilot
Integrated.

Authoritative inputs
- Planning branch: codex/sealed-agent-runtime-plan at the exact HEAD recorded
  when this runbook is handed off.
- Implementation spec:
  docs/design/sealed-agent-runtime-implementation-spec.md
- Ticket graph:
  docs/design/sealed-agent-runtime-ticket-graph.md
- Verification receipt contract:
  docs/design/agent-runtime-verification-receipt.md
- Supply/lock/rollback contract:
  docs/design/agent-runtime-supply-lock-and-rollback.md
- Codex artifact research:
  docs/research/sealed-agent-runtime-codex-artifact.md
- SAR-003/SAR-007 prototype evidence is decision evidence only, never production
  code or acceptance evidence.

Roadmap state
- Decision readiness: READY; Wayfinder map is MAP_CLEAR.
- Current phase: implementation-ready, not implemented.
- Destination: production Agent runtime -> Agent Runtime Verified -> separate
  provider-free pilot integration -> paid Formal pilot.

Operating model
- Discover live repo/worktree/branch/tracker state before acting; preserve the
  dirty develop root and unrelated work.
- Use one stable ticket-owner subagent per SAI ticket. Use isolated worktrees.
- Runtime capacity is four slots including the parent; at most three ticket
  owners may run concurrently. Reviews run after an owner frees a slot.
- Execute one dependency group at a time in the exact order in the ticket graph.
- The parent owns the authorization ledger, budget ledger, integration branch,
  serial cherry-picks, final acceptance and all external side effects.
- Each ticket starts from its group's fixed integration SHA. It must return exact
  HEAD/base, changed files, evidence, PASS/FAIL/NOT RUN, cleanup state and resume
  locator. Review corrections stay with the same owner and use new commits.
- Every ticket gets parallel Standards and Spec review against its fixed base.
  Integrate only after both are PASS and the worktree is clean.

Authorization ledger
- Authorized: scoped code/design/test changes; isolated worktrees and topic
  commits; dependency download/admission; image build; Colima linux/amd64;
  named S3 immutable objects; CVM push/snapshot/provision/run/pull through the
  repo skills; Mac mount verification; reviewed topic-branch push; retained
  diagnostic artifacts.
- CVM push/pull/snapshot operations must use the repository skills, never raw
  rsync/scp/aws copy commands.
- Credentials are injected by reference only and never printed, copied into an
  image, committed or placed in public receipts.
- No destructive cleanup of historical images, S3 objects, outputs or receipts.
  Successful handle-scoped scratch cleanup requires terminal publication,
  exact reread, supply verification and Mac visibility. Otherwise retain.
- Do not merge into a dirty develop checkout. Land serially in a clean
  integration worktree and report the resulting SHA.

Paid-run ledger
- SAI-001 through SAI-014 must make zero model/provider dispatches.
- SAI-015 is authorized for at most 20 model-reaching jobs and USD 1000 total.
- Per job: at most 45 minutes and 48 upstream model requests.
- Every possibly dispatched attempt counts. There is no automatic retry; a retry
  needs a fresh handle, a written hypothesis and a new ledger row.
- This job-level rule does not hide Proxy transport attempts: every allowed
  upstream attempt, including may-have-reached ambiguity, consumes one of 48
  request slots and its full reserved token/cost envelope.
- Before each dispatch, admit exact provider pricing and hard proxy-enforced
  token ceilings, then reserve their worst-case USD cost for all requests; or
  use trusted cumulative USD telemetry. Unresolved reservations remain spent.
  If neither proof exists, dispatch count is zero.

Phase boundaries
1. SAI-001 canonical evidence kernel.
2. SAI-002 + SAI-006 in parallel.
3. SAI-003 + SAI-004 in parallel.
4. SAI-005 deterministic offline image.
5. SAI-007, then SAI-008 + SAI-009 in parallel.
6. SAI-010 provider-proxy mock conformance and SAI-011 dual-environment
   verification may run in parallel after dependencies.
7. SAI-012 separate real four-container release qualification.
8. SAI-013 supply/promotion/reconciliation/rollback drill.
9. SAI-014 provider-free production pilot integration.
10. SAI-015 paid pilot and review.

Global stop conditions
- any identity, source, lock, schema or verification-plan drift;
- any browser surface inside Agent;
- any unverified returned ID acquiring delete authority;
- any shared writable job authority or slot released before absence proof;
- any failed/ambiguous channel reconciliation or missing exact S3/Mac evidence;
- any secret exposure, dirty-worktree overlap, failed review or ancestry break;
- CVM capacity/disk danger or paid budget ambiguity.

AFK ceiling
Run for at most 12 hours from goal creation. At the ceiling, finish the current
safe bounded operation, publish a truthful handoff and stop. Do not start a paid
job that cannot finish inside the remaining window.

Completion report
Return integrated SHA(s), ticket/review table, exact artifacts/receipts, Colima
and CVM evidence, S3 keys and VersionIds, paid ledger, retained failures,
cleanup/absence proof, remaining NOT RUN items and a precise resume command.
Mark the goal complete only if no required work remains; otherwise leave a
closed terminal blocker or an active resumable state without overstating status.
```
