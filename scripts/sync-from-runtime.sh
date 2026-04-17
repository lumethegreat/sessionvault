#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUNTIME_DIR="$HERMES_HOME/hermes-agent/plugins/memory/sessionvault"
REPO_PLUGIN_DIR="$REPO_ROOT/plugin"

if [[ ! -d "$RUNTIME_DIR" ]]; then
  echo "✗ Runtime plugin not found at: $RUNTIME_DIR" >&2
  exit 1
fi

mkdir -p "$REPO_PLUGIN_DIR"
rsync -a --delete --exclude '__pycache__/' "$RUNTIME_DIR/" "$REPO_PLUGIN_DIR/"

echo "✓ Synced runtime plugin into repo: $REPO_PLUGIN_DIR"
