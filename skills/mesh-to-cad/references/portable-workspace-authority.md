# Portable Workspace authority

`workspace-authority.bundle` and `workspace-authority.json` preserve the Git
objects required to audit a terminal canonical Workspace after transfer omits
`.git`. The Git objects plus the canonical Workspace schemas remain authority.
The receipt only routes verification and never substitutes telemetry for an
authority fact.

## Publication

After the unchanged public `mesh-to-cad-workspace validate` process succeeds
and reports a Final Delivery, run:

```bash
python scripts/pilot/workspace_authority.py create \
  --workspace <EXP_DIR> \
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace
```

The helper temporarily creates exactly
`refs/workspace-authority/portable-v1` at the validated `HEAD`, writes a Git
bundle for that one ref, removes the temporary ref, then atomically publishes
the receipt last. A bundle has exactly one advertised ref and contains only
objects reachable from that ref. It contains no repository configuration,
remotes, credentials, hooks, reflogs, or unrelated branch names. The authority
files are ignored transfer products and do not change the Workspace tree or
HEAD.

Git LFS media are not duplicated inside the Git bundle. Their pointer blobs are
reachable Git objects; the already transferred working-tree media are staged
locally and must clean-filter to those exact pointers before validation. Audit
never fetches LFS media from a remote.

## Receipt schema

The receipt schema is `mesh-to-cad.workspace-authority/1`. Every object is
closed. JSON is UTF-8, uses lexicographically sorted keys, no insignificant
whitespace, and one trailing LF. Digests are lowercase SHA-256 hex over exact
bytes.

```json
{
  "schema": "mesh-to-cad.workspace-authority/1",
  "bundle": {
    "path": "workspace-authority.bundle",
    "sha256": "<64 lowercase hex>",
    "size_bytes": 123
  },
  "created_by": {
    "name": "text-to-cad.workspace-authority",
    "sha256": "<creator file SHA-256>",
    "version": 1
  },
  "protocol_commits": ["<publishing commit>"],
  "required_commits": [
    {
      "commit": "<40 lowercase hex>",
      "parents": ["<parent commit>"],
      "tree": "<40 lowercase hex>"
    }
  ],
  "validation": {
    "classification": "valid",
    "graph_sha256": "<canonical validator graph SHA-256>"
  },
  "workspace": {
    "head": "<40 lowercase hex>",
    "id": "<workspace_id>",
    "publication_ref": "refs/workspace-authority/portable-v1",
    "schema": "mesh-to-cad.workspace/1",
    "tree": "<40 lowercase hex>"
  }
}
```

`required_commits` lists every commit reachable from the publication ref in
reverse topological order, with its exact parents and tree. `protocol_commits`
is the ordered subset that most recently published tracked Workspace authority
paths. Schema changes require a new schema version and publication ref; version
1 readers reject unknown fields or encodings.

The terminal `artifact_manifest.json` includes both package paths with exact
`size_bytes` and `sha256`. This transfer manifest remains telemetry; its role is
to fail closed before upload or cleanup, not to redefine Workspace authority.

## Retained-copy audit

Audit a `.git`-less retained experiment with:

```bash
python scripts/pilot/workspace_authority.py audit \
  --source <PULLED_EXP> \
  --workspace-helper skills/mesh-to-cad/scripts/mesh-to-cad-workspace \
  --timeout-seconds 120 --max-files 20000 --max-bytes 5368709120
```

The helper copies the mounted source into bounded local temporary storage,
verifies canonical receipt encoding, bundle size/digest, the sole ref and HEAD,
every required commit parent/tree, protocol commit membership, and every
transferred tracked file. It materializes a temporary detached repository from
the bundle and invokes the existing Workspace validator unchanged. Temporary
Git configuration, refs, and reflogs stay inside that staging directory. The
mounted or retained experiment is never mutated.

An input with live `.git` is validated directly and reported with authority
mode `live`. A verified portable input is reported as `materialized` with exact
receipt and bundle evidence pointers. Missing legacy packages return
`not_auditable`; they are never migrated from telemetry.

Stable portable failure classes are `authority_missing`,
`authority_corrupt_receipt`, `authority_digest_mismatch`,
`authority_invalid_bundle`, `authority_wrong_ref`,
`authority_parent_mismatch`, `authority_tree_mismatch`,
`authority_commit_mismatch`, `authority_partial`,
`authority_dirty_artifact`, `authority_workspace_mismatch`,
`authority_validation_mismatch`, `authority_stage_bounds`, and
`authority_timeout`. All fail closed. `cvm-pull` must complete the mounted-copy
audit before deleting the CVM source; timeouts are reported separately and
also preserve the source.
