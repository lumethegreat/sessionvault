#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUNTIME_DIR="$HERMES_HOME/hermes-agent/plugins/memory/sessionvault"
BACKUP_DIR="$HERMES_HOME/local-plugins/sessionvault"
DATA_DIR="$HERMES_HOME/sessionvault"
SRC_DIR="$REPO_ROOT/plugin"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "✗ Plugin source not found at: $SRC_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$RUNTIME_DIR")" "$BACKUP_DIR" "$DATA_DIR"
rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"
rsync -a --delete --exclude '__pycache__/' "$SRC_DIR/" "$RUNTIME_DIR/"

rm -rf "$BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
rsync -a --delete --exclude '__pycache__/' "$SRC_DIR/" "$BACKUP_DIR/"

DB_PATH="$DATA_DIR/vault.db"
if [[ -f "$DB_PATH" ]]; then
  echo "✓ Preserved existing DB: $DB_PATH"
else
  echo "ℹ No existing DB found at: $DB_PATH"
  echo "  SessionVault will create it automatically on first initialization."
fi

echo "✓ Installed SessionVault plugin to: $RUNTIME_DIR"
echo "✓ Refreshed backup copy at: $BACKUP_DIR"
echo "→ Next steps:"
echo "  1) Ensure ~/.hermes/config.yaml has memory.provider: sessionvault"
echo "  2) Restart Hermes gateway or CLI"
echo "  3) Verify with: hermes memory status && hermes sessionvault status"
