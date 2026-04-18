# Installing SessionVault

This repo manages the **plugin code**.
The conversation history lives separately in the profile DB and is preserved.

## Paths

Default paths in this environment:
- repo: `~/projects/sessionvault`
- Hermes runtime plugin destination: `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- SessionVault data directory: `~/.hermes/sessionvault`
- SQLite DB: `~/.hermes/sessionvault/vault.db`

## Behaviour guarantees

### Existing DB
If `~/.hermes/sessionvault/vault.db` already exists:
- installation reuses it
- history is preserved
- scripts do not remove it

### Fresh install with no DB
If the DB does not exist:
- installation still succeeds
- the plugin creates the DB and schema automatically on first initialization

## Prerequisites

- Hermes Agent already installed via the normal Hermes install flow
- a valid Hermes runtime checkout at `~/.hermes/hermes-agent`
- write access to that runtime

## Install from this repo

From the repo root:

```bash
./scripts/install.sh --with-gateway-patch
```

This will:
- copy `plugin/` into `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- preserve any existing DB under `~/.hermes/sessionvault/`
- create `~/.hermes/sessionvault/` if missing
- apply the documented gateway lifecycle patch idempotently

If you want to install only the plugin code and merely verify gateway patch status:

```bash
./scripts/install.sh
```

## Gateway lifecycle patch

The deeper gateway/session-control event integration lives in:
- `references/hermes-gateway-run-sessionvault-events.patch`

Use the helper script to verify or apply it:

```bash
./scripts/sessionvault-gateway-patch.sh --check
./scripts/sessionvault-gateway-patch.sh --apply
```

The helper is idempotent:
- exit `0` → patch already present (or just applied)
- exit `1` → patch not applied yet
- exit `2` → runtime drift detected; review `gateway/run.py` before forcing anything

## Activate the provider

Ensure `~/.hermes/config.yaml` contains:

```yaml
memory:
  provider: sessionvault
```

Then restart Hermes:

```bash
hermes gateway restart
```

Or restart the CLI session if you are using Hermes locally.

## Verify

```bash
hermes memory status
hermes sessionvault status
hermes sessionvault doctor
```

## Doctor

```bash
./scripts/sessionvault-doctor.sh
```

This checks:
- repo plugin files
- runtime plugin files
- DB presence/counts
- configured provider in `~/.hermes/config.yaml`
- gateway lifecycle patch status (`applied` / `not applied` / `drift detected`)

## Notes

- This repo does not ship or back up `vault.db`.
- If you want DB backups, do that separately from code versioning.
- Because SessionVault imports Hermes internals, compatibility should be tested after Hermes updates.
