#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

function usage() {
  cat <<'EOF'
Usage:
  VIEWER_VERCEL_BLOB_PREFIX=<prefix> \
  VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN=<token> \
  scripts/catalog/upload-models-catalog.sh [upload options]

Uploads the models catalog and CAD Viewer-supported assets to Vercel Blob.

Environment:
  VIEWER_VERCEL_BLOB_PREFIX            Required. Blob path prefix, for example: models2
  VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN  Required. Vercel Blob read/write token.
  VIEWER_LOCAL_WORKSPACE_ROOT          Optional. Defaults to the repository root.
  VIEWER_LOCAL_ROOT_DIR                Optional. Defaults to models.
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
: "${VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN:?Set VIEWER_VERCEL_BLOB_READ_WRITE_TOKEN before uploading to Vercel Blob.}"

export VIEWER_ASSET_BACKEND="${VIEWER_ASSET_BACKEND:-vercel-blob}"
export VIEWER_LOCAL_WORKSPACE_ROOT="${VIEWER_LOCAL_WORKSPACE_ROOT:-$REPO_ROOT}"
export VIEWER_LOCAL_ROOT_DIR="${VIEWER_LOCAL_ROOT_DIR:-models}"

npm --prefix "$REPO_ROOT/viewer" run upload:blob -- "$VIEWER_LOCAL_ROOT_DIR" \
  --workspace-root "$VIEWER_LOCAL_WORKSPACE_ROOT" \
  "$@"
