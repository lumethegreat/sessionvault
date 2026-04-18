#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUNTIME_DIR="$HERMES_HOME/hermes-agent/plugins/memory/sessionvault"
DATA_DIR="$HERMES_HOME/sessionvault"
SRC_DIR="$REPO_ROOT/plugin"
GATEWAY_PATCH_MODE="check"
GATEWAY_PATCH_SCRIPT="$REPO_ROOT/scripts/sessionvault-gateway-patch.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--with-gateway-patch]

Options:
  --with-gateway-patch        Apply the SessionVault gateway patch after installing plugin code.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --with-gateway-patch)
      GATEWAY_PATCH_MODE="apply"
      shift
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

if [[ ! -d "$SRC_DIR" ]]; then
  echo "✗ Plugin source not found at: $SRC_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$RUNTIME_DIR")" "$DATA_DIR"
rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"
rsync -a --delete --exclude '__pycache__/' "$SRC_DIR/" "$RUNTIME_DIR/"

DB_PATH="$DATA_DIR/vault.db"
if [[ -f "$DB_PATH" ]]; then
  echo "✓ Preserved existing DB: $DB_PATH"
else
  echo "ℹ No existing DB found at: $DB_PATH"
  echo "  SessionVault will create it automatically on first initialization."
fi

case "$GATEWAY_PATCH_MODE" in
  apply)
    if "$GATEWAY_PATCH_SCRIPT" --apply --hermes-home "$HERMES_HOME"; then
      echo "✓ Gateway lifecycle patch ensured"
    else
      echo "✗ Failed to apply gateway lifecycle patch" >&2
      exit 1
    fi
    ;;
  check)
    set +e
    "$GATEWAY_PATCH_SCRIPT" --check --hermes-home "$HERMES_HOME"
    patch_status=$?
    set -e
    case "$patch_status" in
      0)
        echo "✓ Gateway lifecycle patch already applied"
        ;;
      1)
        echo "ℹ Gateway lifecycle patch not applied"
        echo "  Apply it with: ./scripts/sessionvault-gateway-patch.sh --apply"
        echo "  Or rerun install with: ./scripts/install.sh --with-gateway-patch"
        ;;
      2)
        echo "✗ Gateway runtime drift detected; patch needs manual review" >&2
        exit 1
        ;;
      *)
        echo "✗ Gateway patch verification failed unexpectedly" >&2
        exit "$patch_status"
        ;;
    esac
    ;;
  *)
    echo "✗ Unknown gateway patch mode: $GATEWAY_PATCH_MODE" >&2
    exit 64
    ;;
esac

echo "✓ Installed SessionVault plugin to: $RUNTIME_DIR"
echo "→ Next steps:"
echo "  1) Ensure ~/.hermes/config.yaml has memory.provider: sessionvault"
echo "  2) Restart Hermes gateway or CLI"
echo "  3) Verify with: hermes memory status && hermes sessionvault status"
