#!/usr/bin/env bash
set -euo pipefail

# Dereference development symlink roots before a production bundle. Existing
# content is needed by pre-bundle metadata checks, then the bundle scripts
# overwrite the generated outputs. This runs only in release/smoke staging
# trees; develop keeps symlinks.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TREE_ROOT="$REPO_ROOT"

usage() {
  cat <<'EOF'
Usage: scripts/bundle/materialize-production-layout.sh [--tree DIR]

Dereferences symlinked generated-output roots in DIR into physical content so
scripts/bundle/bundle.sh can populate a provider-safe production layout.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tree)
      shift
      TREE_ROOT="$1"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [ ! -d "$TREE_ROOT" ]; then
  echo "Tree root does not exist: $TREE_ROOT" >&2
  exit 2
fi
TREE_ROOT="$(cd "$TREE_ROOT" && pwd -P)"

materialized=0
generated_output_text="$("$TREE_ROOT/scripts/bundle/bundle-skill.sh" --all --print-outputs)"
while IFS= read -r generated_path; do
  [ -n "$generated_path" ] || continue
  output="$TREE_ROOT/$generated_path"
  if [ -L "$output" ]; then
    resolved="$(cd "$(dirname "$output")" && realpath "$(basename "$output")")"
    case "$resolved" in
      "$TREE_ROOT"/*) ;;
      *)
        echo "Generated output symlink escapes the staging tree: $generated_path -> $resolved" >&2
        exit 1
        ;;
    esac
    temporary="$(mktemp -d "${output}.materialized-XXXXXX")"
    rsync -aL \
      --exclude node_modules \
      --exclude __pycache__ \
      --exclude .pytest_cache \
      --exclude '*.pyc' \
      "$resolved/" "$temporary/"
    rm "$output"
    mv "$temporary" "$output"
    echo "Materialized generated output root: $generated_path"
    materialized=$((materialized + 1))
  fi
done < <(printf '%s\n' "$generated_output_text")

# A tracked skill symlink not declared by a bundle script has no production
# owner and must not be silently replaced with an empty directory.
first_unowned=""
while IFS= read -r -d '' tracked_path; do
  if [ -L "$TREE_ROOT/$tracked_path" ]; then
    first_unowned="$tracked_path"
    break
  fi
done < <(git -C "$TREE_ROOT" ls-files -z -- skills)
if [ -n "$first_unowned" ]; then
  echo "Tracked skill symlink is not owned by a generated bundle output:" >&2
  echo "  $first_unowned" >&2
  exit 1
fi

echo "Production layout materialized ($materialized symlink roots replaced)."
