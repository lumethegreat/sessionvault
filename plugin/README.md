# SessionVault (Hermes memory provider)

SessionVault is a **local-first, lossless** memory provider for Hermes Agent.

It stores every user/assistant turn **verbatim** in a profile-scoped SQLite database and provides:

- Cross-session search via **SQLite FTS5** (fast, offline)
- Scoped retrieval (default: **workspace+chat** when derivable; fallback: **chat**) 
- Optional **incremental summaries** stored alongside raw messages (raw is never deleted)

> SessionVault runs **alongside** Hermes built-in memory (`MEMORY.md` / `USER.md`).
> Hermes supports **at most one external memory provider** at a time.

---

## Activation

Enable SessionVault by setting the external provider in:

- `~/.hermes/config.yaml`

```yaml
memory:
  provider: sessionvault
```

Then restart the gateway (or restart your CLI session):

```bash
hermes gateway restart
```

Verify:

```bash
hermes memory status
```

You should see `sessionvault  (local) ← active`.

---

## Where data is stored

By default, SessionVault uses a **SQLite database** at:

- `~/.hermes/sessionvault/vault.db`

SQLite is configured with:

- WAL mode (`PRAGMA journal_mode=WAL;`) for better resilience
- FTS5 virtual tables for search

Schema (high level):

- `sessions` — session metadata (platform/chat/thread + derived workspace/channel)
- `messages` — raw messages (lossless turns)
- `summaries` — optional summaries linked to turn ranges
- `messages_fts` / `summaries_fts` — full-text search indices

---

## How it works (runtime lifecycle)

SessionVault is a **MemoryProvider** plugin with the following hooks (declared in `plugin.yaml`):

- `initialize(...)`
- `sync_turn(user, assistant)`
- `queue_prefetch(query)`
- `prefetch(query)`
- `on_pre_compress(messages)`
- `on_session_end(messages)`

### 1) initialize()

On session start, Hermes calls `initialize(session_id=..., platform=..., hermes_home=..., agent_identity=...)`.

SessionVault:

- Loads optional plugin config from `~/.hermes/sessionvault/config.json` (if present)
- Opens/initializes the SQLite DB
- Derives origin/scoping metadata (platform/chat_id/thread_id + best-effort workspace/channel)
- Upserts a row in `sessions`
- Starts a small background worker thread

### 2) sync_turn()

On every exchange (user + assistant), SessionVault appends **two rows** to `messages`:

- `role=user`, `kind=turn`
- `role=assistant`, `kind=turn`

The plugin keeps its own `turn_index` counter (1 per user+assistant exchange).

### 3) queue_prefetch() + prefetch()

Hermes may call `queue_prefetch(query)` before the next model turn.

SessionVault performs an **FTS search** (no LLM) and caches a short, scoped block.

Later, Hermes calls `prefetch(query)` and SessionVault returns the cached block.

### 4) on_pre_compress()

When Hermes is about to compact context (automatic compression), it calls `on_pre_compress(messages)`.

SessionVault persists a **snapshot** of the messages that are about to be compacted as:

- `role=system`, `kind=pre_compress_snapshot`

This ensures the “about to be dropped” context remains losslessly stored in the vault.

---

## When SessionVault uses an LLM

SessionVault uses an LLM **only** to generate optional summaries.

Summaries are produced by a background job that:

1. Serializes a transcript chunk (turn range)
2. Calls Hermes **auxiliary LLM routing** via:
   - `agent.auxiliary_client.call_llm(task="compression", ...)`
3. Stores the summary in `summaries` (+ FTS index) with `source_hash` for auditability

Everything else (storage, search, expand, doctor/status) is **offline**.

### Summary triggering

By default, summarization is triggered by turn count thresholds:

- `leaf_min_turns` (minimum turns before summarizing)
- `leaf_chunk_turns` (turns per summary chunk)

When the session has accumulated enough turns, the plugin schedules a job to summarize the oldest unsummarized chunk.

---

## Configuration

SessionVault has **two** configuration layers:

### A) Provider selection (global)

In `~/.hermes/config.yaml`:

```yaml
memory:
  provider: sessionvault
```

This is how Hermes decides which external memory provider is active.

### B) SessionVault plugin config (plugin-specific)

Optional file:

- `~/.hermes/sessionvault/config.json`

Supported keys (current):

```json
{
  "db_path": "$HERMES_HOME/sessionvault/vault.db",
  "leaf_chunk_turns": 24,
  "leaf_min_turns": 10,
  "summary_model": "",
  "summary_provider": ""
}
```

Notes:

- `db_path` supports `$HERMES_HOME` substitution.
- `summary_model` / `summary_provider` are optional overrides.
  - If empty, SessionVault uses Hermes auxiliary `task="compression"` defaults.

---

## CLI usage

When `memory.provider == sessionvault`, Hermes registers:

```bash
hermes sessionvault status
hermes sessionvault search "query" --scope default --limit 8
hermes sessionvault doctor
```

Additionally:

```bash
hermes memory status
```

is the canonical way to confirm which external provider is active.

---

## Tools exposed to the model

When active, SessionVault injects 4 tool schemas into Hermes’ tool surface:

- `sessionvault_search`
- `sessionvault_expand`
- `sessionvault_status`
- `sessionvault_doctor`

These are called by the model automatically when it needs cross-session recall.

---

## Scoping rules (workspace/chat)

SessionVault stores and filters by:

- `platform`
- `chat_id`
- `thread_id`
- derived `workspace_name` and `channel_name`

**Default search scope** tries to restrict results to the same workspace+chat when it can parse it from `chat_name`.
If parsing fails, it falls back to a stable `chat_key = "<platform>:<chat_id>"`.

This is intentionally best-effort because Hermes gateways do not always provide a stable workspace identifier (e.g. Discord guild_id) to plugins.

---

## Troubleshooting

### “SessionVault is installed but not active”

- Run: `hermes memory status`
- Ensure `~/.hermes/config.yaml` has `memory.provider: sessionvault`
- Restart the gateway

### “I don’t see the `hermes sessionvault ...` command”

That command group is only registered when SessionVault is the active provider.

### Integrity check

```bash
hermes sessionvault doctor
```

### Update safety

If you run `hermes update`, local modifications inside `~/.hermes/hermes-agent/` may be overwritten.
In this environment we mitigate this with:

- `~/.hermes/scripts/backup-sessionvault.sh`
- `~/.hermes/scripts/restore-sessionvault.sh`
- `~/.hermes/scripts/sessionvault-doctor.sh`

---

## Privacy

SessionVault is local-first:

- raw messages and summaries are stored locally in SQLite
- no embeddings are generated
- LLM calls occur only if summarization is enabled/triggered

If you enable summarization, the serialized chunk text is sent to the configured auxiliary LLM provider.
