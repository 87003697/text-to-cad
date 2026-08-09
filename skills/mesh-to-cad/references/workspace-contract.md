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
  measurement, Canonical Reference, canonical frame, and preview profile before
  publishing steps/000000/.
- publish-cycle publishes a marker-last transaction containing both
  steps/NNNNNN/ and cycles/NNNNNN/. Its plan identity, Region Diff edge,
  source-change evidence, assessment, ancestry, and Observable Geometry
  identities must agree.
- record-attempt publishes failed or strategy-changed Attempts without
  creating a Measured Step.
- step_index.json is a compact derived graph. rebuild-index recreates it
  from immutable step, cycle, and attempt authority.

Step numbers do not imply ancestry. Every nonzero measurement, Measured Step,
Repair Cycle, Region Diff, assessment, and source-change document names the
same explicit earlier parent. This allows later cycles to branch from history.

## Bounds and recovery

The Workspace permits five successful Repair Cycles. Each intended step permits
three Attempts, at most two of which may end as actual tool failures. Failed
Attempts consume no cycle. A successfully published geometric no-op consumes
one cycle.

run executes argv directly without a shell. It permits eight commands per
Attempt, caps time at 900 seconds, stores at most 64 KiB from each output
stream using a versioned head/tail policy, and redacts known secret-bearing
arguments and Authorization headers.

Setup, Measured Step, Repair Cycle, Attempt, and index writes use validated
temporary staging and atomic rename or replacement. A marker-last transaction
interrupted between Step and Cycle rename is invalid authority; recover
finishes only a staged transaction whose identities cross-check. Unknown staged
state fails closed.

## Git and telemetry boundary

The experiment directory must already be a Git repository root. Initialization
installs the repository-local Git LFS hooks and adds, without overwriting
existing rules, LFS attributes for protocol binary artifacts. Publication:

- rejects pre-existing staged paths;
- stages only the paths declared by that protocol transaction;
- verifies the LFS filter before committing binary artifacts; and
- binds Workspace, Attempt, Step, Cycle, plan, candidate, and Observable
  Geometry identities through commit trailers.

The helper never uses broad staging or disables LFS filters. run/ and work/
are ignored mutable areas. Runner logs or transfer manifests are never
Workspace authority and cannot change validation, acceptance facts, ancestry,
or budget.
