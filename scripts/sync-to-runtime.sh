#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
RUNTIME_DIR="$HERMES_HOME/hermes-agent/plugins/memory/sessionvault"
REPO_PLUGIN_DIR="$REPO_ROOT/plugin"

if [[ ! -d "$REPO_PLUGIN_DIR" ]]; then
  echo "✗ Repo plugin source not found at: $REPO_PLUGIN_DIR" >&2
  exit 1
fi

mkdir -p "$(dirname "$RUNTIME_DIR")"
rm -rf "$RUNTIME_DIR"
mkdir -p "$RUNTIME_DIR"
rsync -a --delete --exclude '__pycache__/' "$REPO_PLUGIN_DIR/" "$RUNTIME_DIR/"

echo "✓ Synced repo plugin into runtime: $RUNTIME_DIR"
