# SessionVault

SessionVault is a local-first, lossless memory plugin for Hermes Agent.

It stores raw conversation turns in a profile-scoped SQLite database and adds:
- cross-session search via SQLite FTS5
- scoped recall by chat/workspace when available
- optional incremental summaries stored alongside raw messages
- model tools for `sessionvault_search`, `sessionvault_expand`, `sessionvault_status`, and `sessionvault_doctor`

## Status

This is an external/local Hermes plugin extracted from a working local installation.
It is **not** a native built-in Hermes plugin.

Current runtime origin used for this extraction:
- runtime plugin: `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- backup copy: `~/.hermes/local-plugins/sessionvault`
- helper scripts: `~/.hermes/scripts/*sessionvault*`

## Repository layout

- `plugin/` — the plugin code installed into Hermes runtime
- `scripts/install.sh` — install plugin code into Hermes runtime without touching the DB
- `scripts/sync-from-runtime.sh` — refresh this repo from current runtime plugin
- `scripts/sync-to-runtime.sh` — push repo plugin code into Hermes runtime
- `scripts/sessionvault-doctor.sh` — inspect repo/runtime/data status
- `INSTALL.md` — installation and upgrade instructions

## Database behaviour

By default the plugin uses:
- `~/.hermes/sessionvault/vault.db`

Rules:
- if the DB already exists, SessionVault reuses it and preserves history
- if the DB does not exist, SessionVault creates the directory, SQLite file, and schema automatically on first initialization
- this repository does **not** version the DB
- install/sync scripts do **not** delete the DB

## Runtime dependency model

SessionVault depends on Hermes internals and is intended to run inside a Hermes checkout/runtime. For example, the plugin imports Hermes modules such as:
- `agent.memory_provider`
- `agent.auxiliary_client`
- Hermes plugin loading/CLI integration

So this repo should be treated as:
- standalone source control for the plugin code
- installed into an existing Hermes runtime
- not a generic independent Python package

## Typical workflow

1. Edit code in this repo.
2. Run `scripts/install.sh`.
3. Ensure `~/.hermes/config.yaml` has:

```yaml
memory:
  provider: sessionvault
```

4. Restart Hermes gateway or CLI session.
5. Verify with:

```bash
hermes memory status
hermes sessionvault status
```

## Safety

- The install flow only copies plugin code.
- The SQLite DB in `~/.hermes/sessionvault/` is preserved.
- If the DB is absent, the plugin creates it on first use.

## Next steps

Likely future improvements:
- time-range search by `created_at`
- structured filters (`kind`, `role`, `session_id`, `thread_id`)
- session lineage / split tracking
- higher-level workflow tools such as timeline and recent decisions
