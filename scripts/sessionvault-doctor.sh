#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROJECT_ROOT="$HERMES_HOME/hermes-agent"
REPO_PLUGIN="$REPO_ROOT/plugin"
RUNTIME_PLUGIN="$PROJECT_ROOT/plugins/memory/sessionvault"
BACKUP_PLUGIN="$HERMES_HOME/local-plugins/sessionvault"
CONFIG_YAML="$HERMES_HOME/config.yaml"
VAULT_DB="$HERMES_HOME/sessionvault/vault.db"

hash_listing() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    (
      cd "$dir"
      find . -type f \( -name '*.py' -o -name 'plugin.yaml' -o -name '*.md' \) -print0 \
        | sort -z \
        | xargs -0 shasum -a 256
    )
  fi
}

provider_value() {
  if [[ ! -f "$CONFIG_YAML" ]]; then
    return 0
  fi
  python3 - "$CONFIG_YAML" <<'PY' 2>/dev/null || true
import sys
try:
    import yaml
except Exception:
    print("")
    raise SystemExit(0)
path = sys.argv[1]
try:
    with open(path, 'r', encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
except Exception:
    print("")
    raise SystemExit(0)
print(((cfg.get('memory') or {}).get('provider')) or "")
PY
}

echo "sessionvault doctor"
echo "  repo:    $REPO_PLUGIN"
echo "  runtime: $RUNTIME_PLUGIN"
echo "  backup:  $BACKUP_PLUGIN"
echo "  db:      $VAULT_DB"
echo

echo "→ config memory.provider: '$(provider_value)'"
echo

for path in "$REPO_PLUGIN" "$RUNTIME_PLUGIN" "$BACKUP_PLUGIN"; do
  if [[ -d "$path" ]]; then
    echo "✓ Present: $path"
  else
    echo "✗ Missing: $path"
  fi
done
echo

if [[ -f "$VAULT_DB" ]]; then
  echo "✓ DB present: $VAULT_DB"
  if command -v sqlite3 >/dev/null 2>&1; then
    echo "  sessions:  $(sqlite3 "$VAULT_DB" 'SELECT COUNT(*) FROM sessions;' 2>/dev/null || echo '?')"
    echo "  messages:  $(sqlite3 "$VAULT_DB" 'SELECT COUNT(*) FROM messages;' 2>/dev/null || echo '?')"
    echo "  summaries: $(sqlite3 "$VAULT_DB" 'SELECT COUNT(*) FROM summaries;' 2>/dev/null || echo '?')"
  fi
else
  echo "ℹ DB not present yet: $VAULT_DB"
  echo "  This is acceptable on a fresh install; SessionVault creates it on first initialization."
fi

echo
if [[ -d "$REPO_PLUGIN" && -d "$RUNTIME_PLUGIN" ]]; then
  TMP1="/tmp/.sessionvault_repo.sha"
  TMP2="/tmp/.sessionvault_runtime.sha"
  hash_listing "$REPO_PLUGIN" > "$TMP1"
  hash_listing "$RUNTIME_PLUGIN" > "$TMP2"
  echo "→ repo vs runtime diff (hashes):"
  diff -u "$TMP1" "$TMP2" || true
  rm -f "$TMP1" "$TMP2"
fi
