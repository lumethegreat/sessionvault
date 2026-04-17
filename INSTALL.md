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
./scripts/install.sh
```

This will:
- copy `plugin/` into `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- refresh the backup copy in `~/.hermes/local-plugins/sessionvault`
- preserve any existing DB under `~/.hermes/sessionvault/`
- create `~/.hermes/sessionvault/` if missing

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

## Sync workflows

### Pull current runtime plugin into this repo
Useful if you made emergency runtime edits and want to capture them here.

```bash
./scripts/sync-from-runtime.sh
```

### Push repo plugin code into runtime

```bash
./scripts/sync-to-runtime.sh
```

`install.sh` already does this, plus backup/data-dir checks.

## Doctor

```bash
./scripts/sessionvault-doctor.sh
```

This checks:
- repo plugin files
- runtime plugin files
- backup plugin files
- DB presence/counts
- configured provider in `~/.hermes/config.yaml`

## Notes

- This repo does not ship or back up `vault.db`.
- If you want DB backups, do that separately from code versioning.
- Because SessionVault imports Hermes internals, compatibility should be tested after Hermes updates.
