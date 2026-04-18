# SessionVault

SessionVault is a local-first, lossless memory plugin for Hermes Agent.

It stores raw conversation turns in a profile-scoped SQLite database and adds:
- cross-session search via SQLite FTS5
- time-range recall by `created_at`
- structured search filters for `kind`, `role`, and session metadata
- session lineage / continuity metadata across related sessions
- structured lifecycle events (`session_initialized`, `pre_compress`, `session_end`, ...)
- scoped recall by chat/workspace when available
- optional incremental summaries stored alongside raw messages
- model tools for `sessionvault_search`, `sessionvault_expand`, `sessionvault_timeline`, `sessionvault_lineage`, `sessionvault_status`, and `sessionvault_doctor`

## Why it exists

Hermes already has built-in profile memory (`MEMORY.md` / `USER.md`), but that is not the same thing as a lossless conversation vault.

SessionVault exists to give Hermes a durable, searchable, local conversation store that can:
- preserve raw turns verbatim
- recover context across sessions
- reconstruct conversations and lifecycle changes with evidence
- keep working offline for storage/search/expand/doctor operations

### Scope boundary
SessionVault is intended to stay **minimalist and specific**:
- storage of raw turns and metadata
- deterministic retrieval / filtering / expansion
- temporal recall and lifecycle forensics

It should **not** grow into a high-level workflow/planning layer.
If richer workflow helpers are needed in the future, they should live above SessionVault and consume its deterministic outputs rather than expand the plugin's core mandate.

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
- stores structured lifecycle events in an `events` table for operational forensics
- preserves context snapshots before Hermes compression via `pre_compress_snapshot`
- scopes recall by chat/workspace when possible

### What it does **not** try to be
- a generic standalone Python package independent from Hermes internals
- a replacement for Hermes built-in user/profile memory
- a hosted/cloud memory service
- a versioned storage location for `vault.db`
- a high-level workflow or planning layer

## Repository layout

- `plugin/` — the plugin code installed into Hermes runtime
- `scripts/install.sh` — install plugin code into Hermes runtime and verify gateway patch status
- `scripts/sessionvault-gateway-patch.sh` — apply/check the local gateway lifecycle patch idempotently
- `scripts/sync-from-runtime.sh` — refresh this repo from current runtime plugin
- `scripts/sync-to-runtime.sh` — push repo plugin code into Hermes runtime
- `scripts/sessionvault-doctor.sh` — inspect repo/runtime/data status
- `references/hermes-gateway-run-sessionvault-events.patch` — local gateway patch that records gateway/session-control events into SessionVault
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
./scripts/install.sh --with-gateway-patch
```

If you only want to install plugin code and verify patch status without modifying `gateway/run.py`:

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
2. Run `scripts/install.sh --with-gateway-patch` when you want to keep the runtime patch in sync.
3. Restart Hermes gateway or CLI.
4. Verify with `hermes memory status`, `hermes sessionvault status`, and `./scripts/sessionvault-doctor.sh`.
5. Use `./scripts/sessionvault-gateway-patch.sh --check` if gateway/runtime drift is suspected.

## CLI and tool usage

### CLI
When active, SessionVault registers core retrieval/forensics commands:

```bash
hermes sessionvault status
hermes sessionvault search "query" --scope default --limit 8
hermes sessionvault events --scope global --limit 20
hermes sessionvault timeline --from "2026-04-13 08:05:00" --to "2026-04-13 08:10:00" --scope chat
hermes sessionvault lineage
hermes sessionvault doctor
```

The current build also contains a small number of deterministic convenience views derived from the same raw data:

```bash
hermes sessionvault recent-decisions --scope chat --limit 5
hermes sessionvault what-were-we-doing --scope chat --limit 5
```

These are intentionally treated as edge helpers, not as the growth direction of the plugin.

### Model tools
When active, SessionVault exposes these core tools to the model:
- `sessionvault_search`
- `sessionvault_expand`
- `sessionvault_events`
- `sessionvault_timeline`
- `sessionvault_lineage`
- `sessionvault_status`
- `sessionvault_doctor`

The current build also includes deterministic convenience views derived from the same stored data:
- `sessionvault_recent_decisions`
- `sessionvault_what_were_we_doing`

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

### Inspect lifecycle events
```bash
hermes sessionvault events --scope global --event-type pre_compress --limit 10
```

### Inspect continuity for the current session
```bash
hermes sessionvault lineage
```

### Optional convenience views (edge helpers, not core scope)
```bash
hermes sessionvault recent-decisions --scope chat --limit 5
hermes sessionvault what-were-we-doing --scope chat --limit 5
```

### Ensure the gateway patch is present
```bash
./scripts/sessionvault-gateway-patch.sh --check
./scripts/sessionvault-gateway-patch.sh --apply
```

### Run health checks
```bash
hermes sessionvault doctor
./scripts/sessionvault-doctor.sh
```

## Safety and operational notes

- The install flow only copies plugin code unless you opt into `--with-gateway-patch`.
- The SQLite DB in `~/.hermes/sessionvault/` is preserved.
- If the DB is absent, the plugin creates it on first use.
- Runtime edits under `~/.hermes/hermes-agent/` can drift from this repo; use the sync scripts to reconcile.
- Gateway/session-control integration currently also uses a local Hermes runtime patch; see `references/hermes-gateway-run-sessionvault-events.patch`.
- `scripts/sessionvault-gateway-patch.sh` can verify whether that patch is already present or apply it idempotently.
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
./scripts/sessionvault-gateway-patch.sh --check
```

Then, depending on the direction you want:

```bash
./scripts/sync-from-runtime.sh
# or
./scripts/sync-to-runtime.sh
# or ensure the gateway patch is present
./scripts/sessionvault-gateway-patch.sh --apply
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
- keep the plugin narrowly focused on deterministic storage / retrieval / forensics
- harden temporal and structured recall on top of the current timeline + filter foundations
- broaden lifecycle capture from gateway/session-control paths (stop, split, restart, expiry)
- improve maintainability and compatibility checks after Hermes updates

## Related docs

- `INSTALL.md` — installation and upgrade flow
- `plugin/README.md` — plugin-specific internals and lifecycle notes
