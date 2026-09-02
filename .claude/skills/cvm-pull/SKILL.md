---
name: cvm-pull
description: >-
  Archive terminal CVM pilot outputs, upload one archive to S3, materialize it
  locally, then reclaim CVM data when authorized. Trigger: "cvm-pull",
  "拉 outputs", "拉 pilot", "拉 CVM pilot", "从 CVM 拿 exp",
  "CVM 跑完拿结果", "sync from CVM".
---

# CVM pull

Use the repository wrapper:

```bash
scripts/pilot/cvm-pull.sh \
  [--exp <group>/<exp> | --group <group>] \
  [--include-byproducts [--retain-cvm-source] | --discard-postmortem]
```

To recover public handles without qualifying, archiving, transferring, or
cleaning anything, use the read-only listing mode:

```bash
scripts/pilot/cvm-pull.sh --list --group <group>
```

`--list` requires `--group` and cannot be combined with an experiment handle
or a publication policy. It prints only canonical, sorted `<group>/<exp>`
handles, one per line, and prints nothing when the group has no public
experiments. Supervisor-private `.agent-*` directories are excluded from both
group and global public discovery; every other unsafe discovered name still
fails closed. Listing does not require `rclone` and performs no qualification,
archiving, S3 operation, materialization, cleanup, or local file write.

The command performs one transaction per experiment:

1. Read the completed CVM experiment and its Workspace terminal evidence or
   closed provider-free evidence.
2. Build one compressed archive on CVM. Normal and discard publication apply
   `.cvmignore.pull` plus the fixed disposable runtime exclusions; explicit
   `--include-byproducts` publication applies only the fixed exclusions.
3. Upload that archive to the fixed S3 outputs prefix.
4. Download and materialize the archive under `tmp/cvm-pull/outputs/`.
5. Validate the archive handle, member names and sizes. Restore the external
   Terminal Validation Handoff only for a materialized Workspace experiment.
6. Clean the exact CVM experiment only after materialization succeeds, unless
   `--retain-cvm-source` was requested.

This archive path replaces per-file upload, listing, and verification. Do not
add a second transfer path or checksum pass.

## Publication rules

- A successful Workspace experiment requires a valid
  `run/terminal-validation-locator.json` and its external
  `.internal-terminal-validation/<child>/terminal-validation.json` handoff.
  A closed provider-free artifact manifest with supported provider-free
  evidence may be materialized without Workspace terminal evidence; audit its
  materialized `provider-free-evidence.json` and artifact manifest directly,
  rather than sending it to pilot review.
- A failed experiment is preserved by default. Include it only with
  `--include-byproducts`; use `--retain-cvm-source` when the CVM copy must
  remain for postmortem review.
- `--discard-postmortem` publishes the normal filtered artifact set, then
  removes the experiment source; it does not include diagnostic byproducts.
- A planned early failure may omit terminal evidence only in
  `--include-byproducts --retain-cvm-source` mode.
- `--discard-postmortem` requires explicit user authorization.
- `run/rollout.jsonl`, stderr files, and experiment Git authority are part of
  the archive. Playwright browser installations and `.git/lfs/` payload cache
  remain excluded.
- Never use raw `rsync`, `scp`, recursive S3 downloads, or `rsync --delete`.
- Never automatically resubmit a pilot when pull or monitoring fails.

## Exit contract

- `0`: nothing to do or all requested experiments completed.
- `4`: transport, S3, rclone, upload, download, or CVM cleanup failed.
- `5`: archive construction or materialization contract failed.
- `6`: uploaded archive is not present in the materialized output tree.
- `7`: unsafe experiment handle or cleanup target.
- `9`: terminal artifact manifest is missing or invalid.

On failure, preserve the CVM source and report the exact handle and exit class.
On success, report the materialized path and whether the CVM source was cleaned
or retained. Send materialized Workspace outputs to `cad:pilot-review`; audit
provider-free outputs from their materialized evidence and artifact manifest.
