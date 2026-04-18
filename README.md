# SessionVault

SessionVault is a local-first, lossless memory plugin for Hermes Agent.

It stores raw conversation turns in a profile-scoped SQLite database and adds:
- cross-session search via SQLite FTS5
- time-range recall by `created_at`
- structured search filters for `kind`, `role`, and session metadata
- session lineage / continuity metadata across related sessions
- scoped recall by chat/workspace when available
- optional incremental summaries stored alongside raw messages
- model tools for `sessionvault_search`, `sessionvault_expand`, `sessionvault_timeline`, `sessionvault_lineage`, `sessionvault_status`, and `sessionvault_doctor`

## Why it exists

Hermes already has built-in profile memory (`MEMORY.md` / `USER.md`), but that is not the same thing as a lossless conversation vault.

SessionVault exists to give Hermes a durable, searchable, local conversation store that can:
- preserve raw turns verbatim
- recover context across sessions
- answer “what were we doing?” with evidence
- keep working offline for storage/search/expand/doctor operations

## Status

This is an external/local Hermes plugin extracted from a working local installation.
It is **not** a native built-in Hermes plugin.

Current runtime origin used for this extraction:
- runtime plugin: `~/.hermes/hermes-agent/plugins/memory/sessionvault`
- backup copy: `~/.hermes/local-plugins/sessionvault`
- helper scripts: `~/.hermes/scripts/*sessionvault*`

## Feature overview

### What SessionVault does today
- stores every synced user/assistant turn in SQLite
- keeps search indices in SQLite FTS5
- stores optional summaries in a separate table
- exposes model tools for search/expand/timeline/status/doctor
- supports structured search filters for `kind`, `role`, `session_id`, `platform`, `chat_id`, and `thread_id`
- tracks session continuity through `previous_session_id`, `split_from_session_id`, `split_reason`, and `resumed_from_session_id`
- preserves context snapshots before Hermes compression via `pre_compress_snapshot`
- scopes recall by chat/workspace when possible

### What it does **not** try to be
- a generic standalone Python package independent from Hermes internals
- a replacement for Hermes built-in user/profile memory
- a hosted/cloud memory service
- a versioned storage location for `vault.db`

## Repository layout

- `plugin/` — the plugin code installed into Hermes runtime
- `scripts/install.sh` — install plugin code into Hermes runtime without touching the DB
- `scripts/sync-from-runtime.sh` — refresh this repo from current runtime plugin
- `scripts/sync-to-runtime.sh` — push repo plugin code into Hermes runtime
- `scripts/sessionvault-doctor.sh` — inspect repo/runtime/data status
- `INSTALL.md` — installation and upgrade instructions

## Architecture

### Runtime model
SessionVault is a Hermes memory provider plugin. At runtime it is loaded by Hermes and participates in the memory-provider lifecycle.

Main hooks in the plugin:
- `initialize(...)`
- `sync_turn(user, assistant)`
- `queue_prefetch(query)`
- `prefetch(query)`
- `on_pre_compress(messages)`
- `on_session_end(messages)`

### Storage model
By default the plugin uses:
- `~/.hermes/sessionvault/vault.db`

High-level SQLite schema:
- `sessions` — session metadata and scope fields
- `messages` — raw messages (`role`, `turn_index`, `kind`, `created_at`)
- `summaries` — optional summary nodes over turn ranges
- `messages_fts` / `summaries_fts` — FTS5 indices for search

### Search model
Current search behaviour is:
- FTS-driven
- scoped to workspace/chat when the origin can be derived
- falls back to chat-level scope when workspace parsing is not reliable

### Summary model
SessionVault uses an LLM only for optional summaries.
All of the following remain local/offline:
- storage
- search
- expand
- doctor/status

## Database behaviour

Rules:
- if the DB already exists, SessionVault reuses it and preserves history
- if the DB does not exist, SessionVault creates the directory, SQLite file, and schema automatically on first initialization
- this repository does **not** version the DB
- install/sync scripts do **not** delete the DB

## Quick start

### 1) Install plugin code into Hermes runtime
From the repo root:

```bash
./scripts/install.sh
```

### 2) Activate SessionVault in Hermes config
Ensure `~/.hermes/config.yaml` contains:

```yaml
memory:
  provider: sessionvault
```

### 3) Restart Hermes

```bash
hermes gateway restart
```

If you are using the CLI instead of the gateway, restart the CLI session.

### 4) Verify

```bash
hermes memory status
hermes sessionvault status
hermes sessionvault doctor
```

## Typical workflow

1. Edit code in this repo.
2. Run `scripts/install.sh`.
3. Restart Hermes gateway or CLI.
4. Verify with `hermes memory status` and `hermes sessionvault status`.
5. Use `scripts/sessionvault-doctor.sh` if repo/runtime/data drift is suspected.

## CLI and tool usage

### CLI
When active, SessionVault registers:

```bash
hermes sessionvault status
hermes sessionvault search "query" --scope default --limit 8
hermes sessionvault timeline --from "2026-04-13 08:05:00" --to "2026-04-13 08:10:00" --scope chat
hermes sessionvault lineage
hermes sessionvault doctor
```

### Model tools
When active, SessionVault exposes these tools to the model:
- `sessionvault_search`
- `sessionvault_expand`
- `sessionvault_timeline`
- `sessionvault_lineage`
- `sessionvault_status`
- `sessionvault_doctor`

## Examples

### Confirm the provider is active
```bash
hermes memory status
```

Expected shape:
- built-in memory active
- provider: `sessionvault`
- plugin available

### Search previous context
```bash
hermes sessionvault search "session split" --scope chat --limit 5
```

### Search only real assistant turns
```bash
hermes sessionvault search "compression" --scope global --kind turn --role assistant
```

### Build a time-based timeline
```bash
hermes sessionvault timeline --from "2026-04-13 08:05:00" --to "2026-04-13 08:10:00" --scope chat
```

### Inspect continuity for the current session
```bash
hermes sessionvault lineage
```

### Run health checks
```bash
hermes sessionvault doctor
./scripts/sessionvault-doctor.sh
```

## Safety and operational notes

- The install flow only copies plugin code.
- The SQLite DB in `~/.hermes/sessionvault/` is preserved.
- If the DB is absent, the plugin creates it on first use.
- Runtime edits under `~/.hermes/hermes-agent/` can drift from this repo; use the sync scripts to reconcile.
- Because SessionVault imports Hermes internals, compatibility should be checked after Hermes updates.

## Troubleshooting

### “SessionVault is installed but not active”
- check `~/.hermes/config.yaml`
- verify `memory.provider: sessionvault`
- restart Hermes gateway or CLI
- run `hermes memory status`

### “The DB does not exist yet”
That is acceptable on a fresh install.
SessionVault creates `~/.hermes/sessionvault/vault.db` automatically on first initialization.

### “Runtime and repo may be out of sync”
Run:

```bash
./scripts/sessionvault-doctor.sh
```

Then, depending on the direction you want:

```bash
./scripts/sync-from-runtime.sh
# or
./scripts/sync-to-runtime.sh
```

### “Will this repo overwrite my history?”
No. The repo versions plugin code only.
The database stays in `~/.hermes/sessionvault/` and is not deleted by install/sync scripts.

## Development notes

SessionVault depends on Hermes internals and is intended to run inside a Hermes checkout/runtime. For example, the plugin imports:
- `agent.memory_provider`
- `agent.auxiliary_client`
- Hermes plugin loading/CLI integration

So this repo should be treated as:
- standalone source control for the plugin code
- installed into an existing Hermes runtime
- not a generic independent Python package

## Roadmap

Near-term priorities:
- higher-level workflow tools such as recent decisions and plan recovery
- richer temporal/structured recall on top of the new timeline + filter foundations
- deeper lifecycle capture for explicit suspend/restart/compression event modeling

## Related docs

- `INSTALL.md` — installation and upgrade flow
- `plugin/README.md` — plugin-specific internals and lifecycle notes
