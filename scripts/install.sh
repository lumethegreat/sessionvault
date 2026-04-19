#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ROOT_HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PROFILE_NAME=""
TARGET_HERMES_HOME=""
RUNTIME_DIR="$ROOT_HERMES_HOME/hermes-agent/plugins/memory/sessionvault"
SRC_DIR="$REPO_ROOT/plugin"

usage() {
  cat <<EOF
Usage:
  $(basename "$0") [--profile NAME]

Options:
  --profile NAME              Target Hermes profile for config/data paths.
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

runtime_plugin_aligned() {
  if [[ ! -d "$SRC_DIR" || ! -d "$RUNTIME_DIR" ]]; then
    return 1
  fi

  local src_hash runtime_hash
  src_hash="$(mktemp)"
  runtime_hash="$(mktemp)"
  trap 'rm -f "$src_hash" "$runtime_hash"' RETURN

  hash_listing "$SRC_DIR" > "$src_hash"
  hash_listing "$RUNTIME_DIR" > "$runtime_hash"

  if diff -q "$src_hash" "$runtime_hash" >/dev/null 2>&1; then
    return 0
  fi
  return 1
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
DATA_DIR="$TARGET_HERMES_HOME/sessionvault"
DB_PATH="$DATA_DIR/vault.db"
CONFIG_YAML="$TARGET_HERMES_HOME/config.yaml"
TARGET_PROFILE_LABEL="${PROFILE_NAME:-default}"

if [[ ! -d "$SRC_DIR" ]]; then
  echo "✗ Plugin source not found at: $SRC_DIR" >&2
  exit 1
fi

echo "SessionVault install"
echo "Repo plugin: $SRC_DIR"
echo "Runtime plugin: $RUNTIME_DIR"
echo "Target profile: $TARGET_PROFILE_LABEL"
echo "Target home: $TARGET_HERMES_HOME"
echo "Target config: $CONFIG_YAML"
echo "Target DB: $DB_PATH"
echo

mkdir -p "$(dirname "$RUNTIME_DIR")" "$DATA_DIR"
if runtime_plugin_aligned; then
  echo "✓ Runtime plugin already aligned; skipping reinstall"
else
  rm -rf "$RUNTIME_DIR"
  mkdir -p "$RUNTIME_DIR"
  rsync -a --delete --exclude '__pycache__/' "$SRC_DIR/" "$RUNTIME_DIR/"
  echo "✓ Installed SessionVault plugin to: $RUNTIME_DIR"
fi

if [[ -f "$DB_PATH" ]]; then
  echo "✓ Preserved existing DB: $DB_PATH"
else
  echo "ℹ No existing DB found at: $DB_PATH"
  echo "  SessionVault will create it automatically on first initialization."
fi

echo "ℹ memory.provider in $CONFIG_YAML: '$(provider_value "$CONFIG_YAML")'"
echo "→ Next steps:"
echo "  1) Ensure $CONFIG_YAML has memory.provider: sessionvault"
echo "  2) Restart Hermes gateway or CLI"
echo "  3) Verify with: hermes memory status && hermes sessionvault status"
