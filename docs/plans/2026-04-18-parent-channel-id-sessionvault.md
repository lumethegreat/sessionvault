# Parent Channel ID Support for Discord Threads — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Extend SessionVault so Discord thread/forum-topic sessions persist the parent channel ID/name alongside the current thread/topic ID, enabling recall and filtering by parent channel (for example `#jsc`) across many topic sessions.

**Architecture:** Keep the source of truth in SessionVault's `sessions` table by adding `parent_chat_id` and `parent_chat_name` as first-class metadata. Populate them from Hermes session origin data when available, add migration support for existing DBs, expose them in status/meta/search filters, and document that the Discord gateway should pass them in the origin payload. Do not try to infer parent IDs from message text or channel names inside SessionVault.

**Tech Stack:** Python 3, SQLite, SessionVault plugin (`plugin/__init__.py`, `plugin/vault_db.py`, `plugin/cli.py`), pytest.

---

## Context and Acceptance Criteria

### Problem
Today, Discord conversations inside forum topics / thread-like channels are stored under the topic ID (`chat_id` / `thread_id`), but the parent channel ID (for example the `#jsc` channel) is lost. This makes it hard to ask SessionVault for "everything under parent channel X".

### Acceptance Criteria
- `sessions` table stores `parent_chat_id` and `parent_chat_name`.
- Existing DBs migrate cleanly without data loss.
- `OriginScope` carries parent channel metadata.
- `load_origin_from_sessions_index(...)` reads parent fields when present.
- `upsert_session(...)` persists parent fields.
- `get_session_meta()` / provider status output includes parent fields.
- `sessionvault_search` and CLI support filtering by `parent_chat_id`.
- Tests cover schema migration, metadata persistence, origin loading, and parent-channel filtering.
- Docs explain the feature and note that the Discord gateway must supply parent metadata in origin.

---

## Task 1: Add failing DB migration + persistence tests

**Objective:** Define the required parent-channel behaviour before changing production code.

**Files:**
- Create: `tests/test_parent_channel_metadata.py`
- Modify: none

**Step 1: Write failing tests**

Add tests for:
1. schema migration adds `parent_chat_id` and `parent_chat_name` columns to an existing DB.
2. `upsert_session(...)` persists parent metadata.
3. `load_origin_from_sessions_index(...)` reads parent metadata from `sessions.json` origin.
4. `search(..., parent_chat_id=...)` only returns sessions under that parent channel.

Suggested test skeleton:

```python
from pathlib import Path
import json

from plugin.vault_db import VaultDB, OriginScope, load_origin_from_sessions_index


def test_schema_migration_adds_parent_channel_columns(tmp_path):
    db_path = tmp_path / "vault.db"
    # create legacy schema without parent columns
    ...
    db = VaultDB(str(db_path))
    cols = {...}
    assert "parent_chat_id" in cols
    assert "parent_chat_name" in cols


def test_upsert_session_persists_parent_channel_metadata(tmp_path):
    db = VaultDB(str(tmp_path / "vault.db"))
    origin = OriginScope(
        platform="discord",
        chat_id="1491842817960710246",
        thread_id="1491842817960710246",
        chat_name="guild / #jsc / Trading Memphis topic",
        parent_chat_id="1491809690848596240",
        parent_chat_name="#jsc",
    )
    db.upsert_session("sess-1", origin)
    meta = db.get_session_meta("sess-1")
    assert meta["parent_chat_id"] == "1491809690848596240"
    assert meta["parent_chat_name"] == "#jsc"
```

**Step 2: Run test to verify failure**

Run:
```bash
pytest -q tests/test_parent_channel_metadata.py
```

Expected: FAIL because the schema, `OriginScope`, and search path do not support parent metadata yet.

**Step 3: Commit placeholder (optional only after tests exist)**

Do not commit yet unless you want a pure RED commit.

---

## Task 2: Extend `OriginScope` and schema

**Objective:** Add parent-channel fields to the in-memory origin model and SQLite schema/migration path.

**Files:**
- Modify: `plugin/vault_db.py`
- Test: `tests/test_parent_channel_metadata.py`

**Step 1: Update `OriginScope`**

In `plugin/vault_db.py`, extend the dataclass:

```python
@dataclass
class OriginScope:
    platform: str
    chat_id: str = ""
    thread_id: str = ""
    chat_type: str = ""
    chat_name: str = ""
    user_id: str = ""
    parent_chat_id: str = ""
    parent_chat_name: str = ""
    workspace_name: str = ""
    channel_name: str = ""
```

**Step 2: Extend the base schema**

In `SCHEMA_SQL`, add to `sessions`:

```sql
parent_chat_id TEXT,
parent_chat_name TEXT,
```

Recommended placement: after `chat_name TEXT` and before `user_id TEXT` to keep chat metadata grouped.

**Step 3: Extend migration**

In `_migrate_schema()`, add required columns:

```python
required_session_columns = {
    "parent_chat_id": "TEXT",
    "parent_chat_name": "TEXT",
    ...
}
```

**Step 4: Add an index**

Either in `SCHEMA_SQL` or `_migrate_schema()`, add:

```sql
CREATE INDEX IF NOT EXISTS idx_sessions_parent_chat_updated
ON sessions(platform, parent_chat_id, updated_at);
```

**Step 5: Run targeted tests**

Run:
```bash
pytest -q tests/test_parent_channel_metadata.py::test_schema_migration_adds_parent_channel_columns
```

Expected: PASS

**Step 6: Commit**

```bash
git add plugin/vault_db.py tests/test_parent_channel_metadata.py
git commit -m "feat: add parent channel schema for session metadata"
```

---

## Task 3: Persist parent metadata in `upsert_session()` and meta retrieval

**Objective:** Make SessionVault actually store and return parent-channel fields.

**Files:**
- Modify: `plugin/vault_db.py`
- Test: `tests/test_parent_channel_metadata.py`

**Step 1: Extend `upsert_session(...)` SQL**

Update the insert/upsert statement to include:
- `parent_chat_id`
- `parent_chat_name`

Example shape:

```python
INSERT INTO sessions(
  session_id, platform, chat_id, thread_id, chat_type, chat_name,
  parent_chat_id, parent_chat_name, user_id,
  workspace_name, channel_name, ...
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ...)
ON CONFLICT(session_id) DO UPDATE SET
  ...,
  parent_chat_id=excluded.parent_chat_id,
  parent_chat_name=excluded.parent_chat_name,
  ...
```

**Step 2: Pass new values from `origin`**

Add `origin.parent_chat_id` and `origin.parent_chat_name` to the parameter tuple.

**Step 3: Extend metadata retrieval**

Ensure `get_session_meta(...)` selects and returns the new fields.

**Step 4: Run targeted tests**

Run:
```bash
pytest -q tests/test_parent_channel_metadata.py::test_upsert_session_persists_parent_channel_metadata
```

Expected: PASS

**Step 5: Commit**

```bash
git add plugin/vault_db.py tests/test_parent_channel_metadata.py
git commit -m "feat: persist parent channel metadata in sessions"
```

---

## Task 4: Load parent metadata from Hermes session origin

**Objective:** Read parent channel metadata from `sessions/sessions.json` when Hermes provides it.

**Files:**
- Modify: `plugin/vault_db.py`
- Test: `tests/test_parent_channel_metadata.py`

**Step 1: Extend `load_origin_from_sessions_index(...)`**

When reading `origin = entry.get("origin") or {}` add:

```python
parent_chat_id = str(origin.get("parent_chat_id") or "")
parent_chat_name = str(origin.get("parent_chat_name") or "")
```

And return them in `OriginScope(...)`.

**Step 2: Write/complete test fixture**

Create a fake `sessions/sessions.json` with:

```json
{
  "abc": {
    "session_id": "sess-1",
    "origin": {
      "platform": "discord",
      "chat_id": "1491842817960710246",
      "thread_id": "1491842817960710246",
      "chat_name": "guild / #jsc / Trading Memphis topic",
      "parent_chat_id": "1491809690848596240",
      "parent_chat_name": "#jsc"
    }
  }
}
```

Assert the loader returns both parent fields.

**Step 3: Run targeted test**

Run:
```bash
pytest -q tests/test_parent_channel_metadata.py::test_load_origin_reads_parent_channel_metadata
```

Expected: PASS

**Step 4: Commit**

```bash
git add plugin/vault_db.py tests/test_parent_channel_metadata.py
git commit -m "feat: load parent channel metadata from session origin"
```

---

## Task 5: Add parent-channel filtering to DB search + tool surface

**Objective:** Make the new metadata actionable in search/CLI instead of being passive data.

**Files:**
- Modify: `plugin/vault_db.py`
- Modify: `plugin/__init__.py`
- Modify: `plugin/cli.py`
- Test: `tests/test_parent_channel_metadata.py`

**Step 1: Extend DB search signature**

In `plugin/vault_db.py`, extend `search(...)` with:

```python
def search(..., parent_chat_id: str = "", ...):
```

Apply it in the session filter stage:

```python
if parent_chat_id:
    session_where.append("parent_chat_id=?")
    params.append(parent_chat_id)
```

Keep the change narrow; do not add parent-name filtering unless needed.

**Step 2: Extend tool schema**

In `plugin/__init__.py`, add `parent_chat_id` to `SEARCH_SCHEMA`:

```python
"parent_chat_id": {
  "type": "string",
  "description": "Optional parent channel ID filter (useful for Discord threads/forum topics)."
}
```

**Step 3: Wire provider tool handler**

In `_tool_search(...)`, read and pass through:

```python
parent_chat_id = str(args.get("parent_chat_id") or "").strip()
```

and include it in the call to `self._db.search(...)`.

**Step 4: Extend CLI**

In `plugin/cli.py`, add:

```python
s.add_argument("--parent-chat-id", default="", help="Filter parent_chat_id")
```

and forward it in the CLI payload.

**Step 5: Add search test**

Create two sessions with different `parent_chat_id`, same query term in messages, and assert filtering only returns the matching parent channel.

**Step 6: Run targeted tests**

Run:
```bash
pytest -q tests/test_parent_channel_metadata.py::test_search_filters_by_parent_chat_id
```

Expected: PASS

**Step 7: Commit**

```bash
git add plugin/vault_db.py plugin/__init__.py plugin/cli.py tests/test_parent_channel_metadata.py
git commit -m "feat: support parent channel filtering in sessionvault search"
```

---

## Task 6: Expose parent metadata in status and docs

**Objective:** Make the new metadata visible for inspection and discoverable for users.

**Files:**
- Modify: `plugin/__init__.py`
- Modify: `README.md`
- Modify: `plugin/README.md`
- Modify: `INSTALL.md` (only if you mention search filters there)

**Step 1: Extend status output**

In `_tool_status()`, add to `origin`:

```python
"parent_chat_id": self._origin.parent_chat_id,
"parent_chat_name": self._origin.parent_chat_name,
```

If `get_session_meta()` already returns them, no further work is needed there beyond ensuring they are visible.

**Step 2: Update docs**

Document:
- SessionVault can now store Discord parent channel metadata.
- `parent_chat_id` is useful for forum/thread recall.
- Search/CLI examples.

Suggested README snippet:

```bash
hermes sessionvault search "Trading Memphis" --parent-chat-id 1491809690848596240 --scope global
```

**Step 3: Clarify dependency on gateway origin**

Add one sentence like:

> Parent channel metadata is only available when the gateway origin payload includes `parent_chat_id` / `parent_chat_name` (recommended for Discord threads/forum topics).

**Step 4: Commit**

```bash
git add plugin/__init__.py README.md plugin/README.md INSTALL.md
git commit -m "docs: document parent channel metadata support"
```

---

## Task 7: Full verification

**Objective:** Prove the feature works end-to-end at the plugin level.

**Files:**
- Modify: none (verification only)

**Step 1: Run targeted suite**

```bash
pytest -q tests/test_parent_channel_metadata.py
```

Expected: PASS

**Step 2: Run full suite**

```bash
pytest -q
```

Expected: all tests pass.

**Step 3: Sanity-check runtime-facing outputs**

Optional but recommended after install to runtime:

```bash
./scripts/install.sh --profile kimi
HERMES_HOME=/Users/mestre/.hermes/profiles/kimi hermes sessionvault status
HERMES_HOME=/Users/mestre/.hermes/profiles/kimi hermes sessionvault search "analise qualitativa" --parent-chat-id 1491809690848596240 --scope global
```

Expected:
- status includes parent fields when origin data contains them
- search can be narrowed by parent channel ID

**Step 4: Final commit**

```bash
git add .
git commit -m "feat: add parent channel metadata for discord thread sessions"
```

---

## Notes / Non-Goals

- Do **not** try to retroactively infer `parent_chat_id` from message content.
- Do **not** overreach into generic multi-platform parent/child hierarchies yet; implement the metadata generically, but the immediate driver is Discord threads/forum topics.
- Do **not** rely on `chat_name` parsing for parent IDs.
- If the current Hermes runtime does not yet emit `parent_chat_id` in session origin, add a follow-up issue/plan in the Hermes gateway repo/runtime patch — SessionVault should be ready to consume the field first.

---

## Suggested Follow-up (outside this patch if needed)

If you confirm Hermes `sessions/sessions.json` origin currently lacks parent metadata for Discord threads, open a follow-up implementation in Hermes Agent:
- Discord adapter should include:
  - `parent_chat_id`
  - `parent_chat_name`
- Session persistence should carry those fields into `sessions.json`

Without that upstream origin field, SessionVault can store and filter the data only when manually supplied or when the runtime is patched.
