#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

function usage() {
  cat <<'EOF'
Usage:
  VIEWER_VERCEL_BLOB_PREFIX=<prefix> \
  BLOB_READ_WRITE_TOKEN=<token> \
  scripts/catalog/upload-models-catalog.sh [upload options]

Uploads the models catalog and CAD Viewer-supported assets to Vercel Blob.
The uploader excludes mechbench/, mechbench2/, 7dof_arm/, and Python source
files by default.

Environment:
  VIEWER_VERCEL_BLOB_PREFIX            Required. Blob path prefix, for example: models2
  BLOB_READ_WRITE_TOKEN                Required. Vercel Blob read/write token.
  VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN  Optional override for BLOB_READ_WRITE_TOKEN.
  VIEWER_LOCAL_ROOT_DIR                Optional upload root. Defaults to models/.
  VIEWER_ASSET_BACKEND                 Optional. Defaults to vercel-blob.

Options are passed through to npm --prefix viewer run upload:blob.
EOF
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

: "${VIEWER_VERCEL_BLOB_PREFIX:?Set VIEWER_VERCEL_BLOB_PREFIX before uploading to Vercel Blob.}"
if [[ -z "${VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN:-}" && -z "${BLOB_READ_WRITE_TOKEN:-}" ]]; then
  echo "Set BLOB_READ_WRITE_TOKEN or VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN before uploading to Vercel Blob." >&2
  exit 1
fi

export VIEWER_ASSET_BACKEND="${VIEWER_ASSET_BACKEND:-vercel-blob}"
export VIEWER_LOCAL_ROOT_DIR="${VIEWER_LOCAL_ROOT_DIR:-$REPO_ROOT/models}"

npm --prefix "$REPO_ROOT/viewer" run upload:blob -- \
  --root-dir "$VIEWER_LOCAL_ROOT_DIR" \
  "$@"
