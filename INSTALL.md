# Installing SessionVault

This repo manages the **plugin code**.
The conversation history lives separately in a **profile-scoped** DB and is preserved.

## Path model

SessionVault now distinguishes between:

- **shared Hermes runtime** — `~/.hermes/hermes-agent`
- **target profile home** — either:
  - default profile: `~/.hermes`
  - named profile: `~/.hermes/profiles/<name>`

Default paths in this environment:
- repo: `~/projects/sessionvault`
- shared runtime plugin destination: `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- default profile data directory: `~/.hermes/sessionvault`
- named profile data directory: `~/.hermes/profiles/<name>/sessionvault`
- default profile DB: `~/.hermes/sessionvault/vault.db`
- named profile DB: `~/.hermes/profiles/<name>/sessionvault/vault.db`

## Behaviour guarantees

### Existing DB
If the target profile DB already exists:
- installation reuses it
- history is preserved
- scripts do not remove it

### Fresh install with no DB
If the target profile DB does not exist:
- installation still succeeds
- the plugin creates the DB and schema automatically on first initialization

### Idempotent shared-runtime handling
The install flow now checks the shared runtime plugin first:
- if runtime code already matches the repo, install **skips reinstall**
- if runtime code differs, install re-aligns it from `plugin/`

The gateway patch helper is also idempotent:
- if already applied, it skips
- if missing, it applies cleanly when possible
- if the runtime has drifted, it exits with an explicit error

## Prerequisites

- Hermes Agent already installed via the normal Hermes install flow
- a valid Hermes runtime checkout at `~/.hermes/hermes-agent`
- write access to that runtime
- if using `--profile NAME`, the profile must already exist at `~/.hermes/profiles/NAME`

## Install from this repo

From the repo root:

### Default profile
```bash
./scripts/install.sh
./scripts/install.sh --with-gateway-patch
```

### Named profile
```bash
./scripts/install.sh --profile kimi
./scripts/install.sh --profile kimi --with-gateway-patch
```

This will:
- verify whether shared runtime plugin code is already aligned
- skip reinstall if already aligned
- otherwise copy `plugin/` into `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- prepare the target profile data dir under `<target-hermes-home>/sessionvault/`
- preserve any existing DB for that target profile
- optionally ensure the documented gateway lifecycle patch on the shared runtime

## Gateway lifecycle patch

The deeper gateway/session-control event integration lives in:
- `references/hermes-gateway-run-sessionvault-events.patch`

Use the helper script to verify or apply it:

```bash
./scripts/sessionvault-gateway-patch.sh --check
./scripts/sessionvault-gateway-patch.sh --apply
```

The helper is shared-runtime only; it does **not** target a specific profile.

Exit codes:
- `0` → patch already present (or just applied)
- `1` → patch not applied yet
- `2` → runtime drift detected; review `gateway/run.py` before forcing anything

## Activate the provider

Ensure the **target profile config** contains:

### Default profile
`~/.hermes/config.yaml`

### Named profile
`~/.hermes/profiles/<name>/config.yaml`

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

### Default profile
```bash
./scripts/sessionvault-doctor.sh
```

### Named profile
```bash
./scripts/sessionvault-doctor.sh --profile kimi
```

This checks:
- repo plugin files
- shared runtime plugin files
- target profile DB presence/counts
- configured provider in the target profile `config.yaml`
- gateway lifecycle patch status (`applied` / `not applied` / `drift detected`)

## Notes

- This repo does not ship or back up `vault.db`.
- If you want DB backups, do that separately from code versioning.
- Because SessionVault imports Hermes internals, compatibility should be tested after Hermes updates.
