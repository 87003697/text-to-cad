#!/usr/bin/env bash
# Snapshot Mac working tree (incl. .git) → s3://.../<group>/_snapshot/.
# Symlinks are dereferenced (snapshot = concrete state, not layout).
# Idempotent: skip if S3 target already populated.
# See .agents/plans/pilot-group-and-snapshot.md.
#
# Flow: rsync repo → local /tmp staging (symlinks dereferenced) →
#       aws s3 cp --recursive to S3 → rclone vfs refresh → rm staging.
# The rclone mount can't create symlinks and openrsync tempfile writes
# fail through FUSE; staging locally sidesteps both.
#
# Usage:
#   scripts/pilot/snapshot-batch.sh <group>
# Example:
#   scripts/pilot/snapshot-batch.sh 20260724-093000-baseline
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

GROUP="${1:?Usage: snapshot-batch.sh <group>  (e.g. 20260724-093000-baseline)}"

# group 名格式 YYYYMMDD-HHMMSS-<slug>
[[ "$GROUP" =~ ^[0-9]{8}-[0-9]{6}-[a-z0-9-]+$ ]] \
  || { echo "Bad group format: '$GROUP'. Expect YYYYMMDD-HHMMSS-<slug>." >&2; exit 1; }

S3_PREFIX="s3://arcwm-code-us-west-2/ericzyma/text-to-cad/outputs"
MOUNT_PATH="$HOME/threed-code/ericzyma/text-to-cad/outputs"
SNAPSHOT_RECEIPT_PYTHON="${SNAPSHOT_RECEIPT_PYTHON:-python3}"

# --- 预检: 若 S3 上已有 snapshot → 幂等跳过
if aws s3 ls "$S3_PREFIX/$GROUP/_snapshot/" 2>/dev/null | grep -q .; then
    echo "[snapshot] $S3_PREFIX/$GROUP/_snapshot/ already exists on S3. Skip."
    exit 0
fi

STAGING="/tmp/snapshot-batch-$$"
trap 'rm -rf "$STAGING"' EXIT
mkdir -p "$STAGING"

echo "[snapshot] Stage 1/3: rsync working tree → $STAGING (dereference symlinks, ~258MB w/ .git)"
# -L = deref symlinks (dev symlinks become real files/dirs in snapshot)
rsync -aL --exclude-from=.snapshotignore ./ "$STAGING/"

git rev-parse HEAD                        > "$STAGING/HEAD.sha"
git diff HEAD                             > "$STAGING/dirty.diff"
git ls-files --others --exclude-standard  > "$STAGING/untracked.txt"
"$SNAPSHOT_RECEIPT_PYTHON" "$REPO_ROOT/scripts/pilot/snapshot_receipt.py" write "$STAGING" >/dev/null
"$SNAPSHOT_RECEIPT_PYTHON" "$REPO_ROOT/scripts/pilot/snapshot_receipt.py" check "$STAGING" >/dev/null

HEAD_SHA="$(cat "$STAGING/HEAD.sha")"
DIRTY_LINES="$(wc -l < "$STAGING/dirty.diff" | tr -d ' ')"
UNTRACKED_LINES="$(wc -l < "$STAGING/untracked.txt" | tr -d ' ')"
STAGING_SIZE="$(du -sh "$STAGING" | awk '{print $1}')"
LOCAL_OBJECT_COUNT="$(find "$STAGING" -type f | wc -l | tr -d ' ')"
LOCAL_RECEIPT_SHA="$(shasum -a 256 "$STAGING/snapshot-receipt.json" | awk '{print $1}')"
echo "[snapshot] Staged: HEAD=$HEAD_SHA, dirty.diff=$DIRTY_LINES lines, untracked=$UNTRACKED_LINES files, objects=$LOCAL_OBJECT_COUNT, receipt=$LOCAL_RECEIPT_SHA, size=$STAGING_SIZE"

echo "[snapshot] Stage 2/3: aws s3 cp → $S3_PREFIX/$GROUP/_snapshot/  (~1min for 250MB @ 5MB/s)"
aws s3 cp --recursive --only-show-errors "$STAGING/" "$S3_PREFIX/$GROUP/_snapshot/"
REMOTE_OBJECT_COUNT="$(aws s3 ls "$S3_PREFIX/$GROUP/_snapshot/" --recursive | wc -l | tr -d ' ')"
REMOTE_RECEIPT_SHA="$(aws s3 cp --only-show-errors "$S3_PREFIX/$GROUP/_snapshot/snapshot-receipt.json" - | shasum -a 256 | awk '{print $1}')"
if [ "$REMOTE_OBJECT_COUNT" != "$LOCAL_OBJECT_COUNT" ] || [ "$REMOTE_RECEIPT_SHA" != "$LOCAL_RECEIPT_SHA" ]; then
    echo "[snapshot] Uploaded shipped-tree receipt verification failed: local_objects=$LOCAL_OBJECT_COUNT remote_objects=$REMOTE_OBJECT_COUNT local_receipt=$LOCAL_RECEIPT_SHA remote_receipt=$REMOTE_RECEIPT_SHA" >&2
    exit 5
fi
echo "[snapshot] Uploaded receipt verified: objects=$REMOTE_OBJECT_COUNT sha256=$REMOTE_RECEIPT_SHA"

echo "[snapshot] Stage 3/3: rclone vfs refresh so Mac sees the new _snapshot/"
rclone rc --rc-addr=127.0.0.1:5572 vfs/refresh \
    dir="ericzyma/text-to-cad" recursive=false 2>/dev/null || true

echo "[snapshot] Done. Verify: ls $MOUNT_PATH/$GROUP/_snapshot/"
