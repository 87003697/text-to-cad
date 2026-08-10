# Breaking VoxBlame contract cutover

Status: Accepted

Date: 2026-08-07

## Decision

The canonical workflow replaces adaptive first-error grading, `next_action`,
sampled distance metrics, and legacy experiment layouts without a migration
layer. Existing `voxblame.session/2`, `voxblame.report/2`, and
`voxblame.summary/1` names change in place. Closed structural validation rejects
old, mixed, corrupt, and unknown-field state with
`unsupported_or_invalid_voxblame_state`.

## Consequences

Every canonical repair workflow starts with fresh state. There is no marker,
compatibility facade, or mixed reader. The production cutover is complete:
execution skills, public commands, bundled runtimes, plugins, and reviewers
expose only the canonical Workspace protocol. Closed validators retain the
forbidden-name table solely to reject old or mixed documents.
