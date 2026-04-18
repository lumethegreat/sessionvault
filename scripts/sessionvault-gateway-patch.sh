#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PATCH_FILE="$REPO_ROOT/references/hermes-gateway-run-sessionvault-events.patch"
RUNTIME_ROOT=""
ACTION=""

usage() {
  cat <<EOF
Usage:
  $(basename "$0") --check [--hermes-home PATH] [--patch-file PATH]
  $(basename "$0") --apply [--hermes-home PATH] [--patch-file PATH]

Checks whether the SessionVault gateway patch is already applied to:
  <hermes-home>/hermes-agent/gateway/run.py

Exit codes:
  0 = patch already applied (or was just applied)
  1 = patch not applied yet
  2 = runtime drift / patch cannot be applied cleanly
  64 = usage error
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)
      ACTION="check"
      shift
      ;;
    --apply)
      ACTION="apply"
      shift
      ;;
    --hermes-home)
      HERMES_HOME="$2"
      shift 2
      ;;
    --patch-file)
      PATCH_FILE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 64
      ;;
  esac
done

if [[ -z "$ACTION" ]]; then
  echo "Either --check or --apply is required." >&2
  usage >&2
  exit 64
fi

RUNTIME_ROOT="$HERMES_HOME/hermes-agent"
TARGET_FILE="$RUNTIME_ROOT/gateway/run.py"

if [[ ! -f "$PATCH_FILE" ]]; then
  echo "✗ Patch file not found: $PATCH_FILE"
  exit 64
fi

if [[ ! -f "$TARGET_FILE" ]]; then
  echo "✗ Runtime target not found: $TARGET_FILE"
  exit 64
fi

patch_is_applied() {
  (
    cd "$RUNTIME_ROOT"
    git apply --check --reverse "$PATCH_FILE" >/dev/null 2>&1
  )
}

patch_can_apply_cleanly() {
  (
    cd "$RUNTIME_ROOT"
    git apply --check "$PATCH_FILE" >/dev/null 2>&1
  )
}

if [[ "$ACTION" == "check" ]]; then
  if patch_is_applied; then
    echo "✓ Patch already applied: $TARGET_FILE"
    exit 0
  fi
  if patch_can_apply_cleanly; then
    echo "ℹ Patch not applied: $TARGET_FILE"
    exit 1
  fi
  echo "✗ Runtime file has drifted and does not match either the original or patched state: $TARGET_FILE"
  exit 2
fi

if patch_is_applied; then
  echo "✓ Patch already applied: $TARGET_FILE"
  exit 0
fi

if ! patch_can_apply_cleanly; then
  echo "✗ Runtime file has drifted and the patch cannot be applied cleanly: $TARGET_FILE"
  exit 2
fi

(
  cd "$RUNTIME_ROOT"
  git apply "$PATCH_FILE"
)

echo "✓ Patch applied: $TARGET_FILE"
