#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE_NAME=""
TARGET_HERMES_HOME=""
PROJECT_ROOT="$ROOT_HERMES_HOME/hermes-agent"
REPO_PLUGIN="$REPO_ROOT/plugin"
RUNTIME_PLUGIN="$PROJECT_ROOT/plugins/memory/sessionvault"
GATEWAY_PATCH_SCRIPT="$REPO_ROOT/scripts/sessionvault-gateway-patch.sh"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--profile NAME]

Options:
  --profile NAME              Target Hermes profile for config/data validation.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      if [[ $# -lt 2 || -z "${2:-}" ]]; then
        echo "Missing value for --profile" >&2
        usage >&2
        exit 64
      fi
      PROFILE_NAME="$2"
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

resolve_target_home() {
  if [[ -z "$PROFILE_NAME" ]]; then
    printf '%s\n' "$ROOT_HERMES_HOME"
    return 0
  fi

  local profile_home="$ROOT_HERMES_HOME/profiles/$PROFILE_NAME"
  if [[ ! -d "$profile_home" ]]; then
    echo "✗ Profile not found: $PROFILE_NAME" >&2
    echo "  Expected: $profile_home" >&2
    exit 1
  fi
  printf '%s\n' "$profile_home"
}

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
  local config_yaml="$1"
  if [[ ! -f "$config_yaml" ]]; then
    return 0
  fi
  python3 - "$config_yaml" <<'PY' 2>/dev/null || true
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

TARGET_HERMES_HOME="$(resolve_target_home)"
CONFIG_YAML="$TARGET_HERMES_HOME/config.yaml"
VAULT_DB="$TARGET_HERMES_HOME/sessionvault/vault.db"
TARGET_PROFILE_LABEL="${PROFILE_NAME:-default}"

echo "sessionvault doctor"
echo "Repo: $REPO_PLUGIN"
echo "Runtime: $RUNTIME_PLUGIN"
echo "Target profile: $TARGET_PROFILE_LABEL"
echo "Target home: $TARGET_HERMES_HOME"
echo "Config: $CONFIG_YAML"
echo "DB: $VAULT_DB"
echo

echo "→ config memory.provider: '$(provider_value "$CONFIG_YAML")'"
echo

for path in "$REPO_PLUGIN" "$RUNTIME_PLUGIN"; do
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
if [[ -x "$GATEWAY_PATCH_SCRIPT" ]]; then
  set +e
  "$GATEWAY_PATCH_SCRIPT" --check --hermes-home "$ROOT_HERMES_HOME"
  patch_status=$?
  set -e
  case "$patch_status" in
    0)
      echo "✓ Gateway lifecycle patch status: applied"
      ;;
    1)
      echo "ℹ Gateway lifecycle patch status: not applied"
      echo "  Apply with: ./scripts/sessionvault-gateway-patch.sh --apply"
      ;;
    2)
      echo "✗ Gateway lifecycle patch status: runtime drift detected"
      ;;
    *)
      echo "✗ Gateway lifecycle patch check failed with exit code: $patch_status"
      ;;
  esac
else
  echo "✗ Gateway patch helper missing or not executable: $GATEWAY_PATCH_SCRIPT"
fi

echo
if [[ -d "$REPO_PLUGIN" && -d "$RUNTIME_PLUGIN" ]]; then
  TMP1="$(mktemp)"
  TMP2="$(mktemp)"
  hash_listing "$REPO_PLUGIN" > "$TMP1"
  hash_listing "$RUNTIME_PLUGIN" > "$TMP2"
  echo "→ repo vs runtime diff (hashes):"
  diff -u "$TMP1" "$TMP2" || true
  rm -f "$TMP1" "$TMP2"
fi
