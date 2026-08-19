# Immutable Workspace helper

scripts/mesh-to-cad-workspace is the public workflow-state boundary for the
canonical repair protocol. It is self-contained and uses no imports from peer
skills or the repository root.

Invoke it with the active project Python:

    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace init ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace begin-attempt ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace run ... -- <argv>
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace record-attempt ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-step-zero ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace publish-cycle ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace finalize \
      --workspace <EXP_DIR> --selection <final-selection.json> --notes <notes.md> \
      --rebuild-entrypoint <registered-cad-or-implicit-adapter> \
      --geometry-entrypoint <mesh-compare-entrypoint> \
      --tool-registry <trusted-tool-registry.json>
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace status ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace rebuild-index ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace recover ...
    python skills/mesh-to-cad/scripts/mesh-to-cad-workspace validate ...

Every machine response is exactly one JSON object on stdout. Contract failures
return exit 2 with error.classification, error.path, and error.detail.
run returns the wrapped command's exit code.

## Publication model

- init accepts a prepared directory containing input/, setup/, and a
  closed mesh-to-cad.experiment/1 manifest.
- begin-attempt freezes either an initial plan or a validated
  voxblame.repair-batch/1.
- publish-step-zero cross-checks the candidate mesh, formal preview,
  canonical `voxblame.summary/1` measurement, Canonical Reference, canonical
  frame, and preview profile before publishing steps/000000/. Objective facts
  are recomputed from depth-8 and exterior evidence; the preview identity is
  recomputed from its canonical metadata.
- publish-cycle publishes a marker-last transaction containing both
  steps/NNNNNN/ and cycles/NNNNNN/. Its plan identity, Region Diff edge,
  source-change evidence, assessment, ancestry, and Observable Geometry
  identities must agree.
- record-attempt publishes failed or strategy-changed Attempts without
  creating a Measured Step.
- finalize validates Agent-owned selection evidence, copies every selected
  recipe input into isolated staging, executes the explicitly supplied
  registered CAD or implicit rebuild adapter, proves the complete source to
  primary artifact to measurement-mesh provenance chain, runs non-publishing
  VoxBlame equivalence through the supplied geometry entrypoint, renders the
  final preview, and atomically publishes `final/`. Explicit entrypoints keep
  the helper installable without locating or importing sibling skills. The
  caller's trusted registry is the explicit authority boundary: it binds the
  route-specific registered adapter and VoxBlame IDs to the exact entrypoint
  digests executed. Do not generate or alter this registry from model output;
  installation/orchestration must supply it.
  `measurement.json` is an unchanged Selected Step summary; verification is a
  separate non-step artifact.
- step_index.json is a compact derived graph. rebuild-index recreates it
  from immutable step, cycle, attempt, and Final Delivery authority.

Step numbers do not imply ancestry. Every nonzero measurement, Measured Step,
Repair Cycle, Region Diff, assessment, and source-change document names the
same explicit earlier parent. This allows later cycles to branch from history.

## Bounds and recovery

The Workspace permits five successful Repair Cycles. Each intended step permits
three Attempts, at most two of which may end as actual tool failures. Failed
Attempts consume no cycle. A successfully published geometric no-op consumes
one cycle.

run executes argv directly without a shell. It permits eight commands per
Attempt, defaults to and caps time at 1800 seconds (30 minutes), stores at most
64 KiB from each output stream using a versioned head/tail policy, and redacts known secret-bearing
arguments and Authorization headers.

Setup, Measured Step, Repair Cycle, Attempt, Final Delivery, and index writes use validated
temporary staging and atomic rename or replacement. A marker-last transaction
interrupted between Step and Cycle rename is invalid authority; recover
finishes only a staged transaction whose identities cross-check. The marker is
removed only after index publication and the scoped Git commit succeed, so a
post-rename interruption remains recoverable, including failed Attempt and
Final Delivery publication. Recovery keeps an already committed Final Delivery
or rolls an uncommitted rename back to the exact pre-finalization notes/index,
so the operation can be retried. Protocol-scoped VoxBlame paths are checked
before any authority rename. Unknown staged state fails closed.

Final Delivery contains `source/` with every recipe-declared input at its
reproducible relative path, rebuilt `artifacts/`, `build.json`, the pinned
`tool-registry.json`,
`rebuild.json`, the unchanged Selected Step `measurement.json`, independent
`verification.json`, final `preview.png`/`preview.json`, `selection.json`, and
`manifest.json`. Rebuild success never upgrades an unaccepted selection.
Source mutation, network-enabled recipes, provenance or Observable Geometry
mismatch, Agent semantic conflict, and exhausted render retries publish no
`final/`; historical artifacts and previews are never fallbacks. After the
scoped final commit succeeds, mutable `work/` contents are removed.

## Git and telemetry boundary

The experiment directory must already be a Git repository root. Initialization
installs the repository-local Git LFS hooks and adds, without overwriting
existing rules, LFS attributes for protocol binary artifacts. Publication:

- rejects pre-existing staged paths;
- stages only the paths declared by that protocol transaction;
- verifies the LFS filter before committing binary artifacts; and
- binds Workspace, Attempt, Step, Cycle, plan, candidate, and Observable
  Geometry identities through commit trailers. Final publication additionally
  binds Selected Step, inherited acceptance, and Final Delivery identity.
  Validation checks the current publishing commit for each authority path, not
  any older matching message.

`workspace.json` also freezes input and setup tree identities. Validation
recomputes them so mutation of prepared authority is reported as corruption.

The helper never uses broad staging or disables LFS filters. run/ and work/
are ignored mutable areas. Runner logs or transfer manifests are never
Workspace authority and cannot change validation, acceptance facts, ancestry,
or budget.
