import json
import sqlite3

from test_timeline import load_module, load_provider_module


def test_existing_db_is_migrated_with_parent_channel_columns(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions (
          session_id TEXT PRIMARY KEY,
          platform TEXT,
          chat_id TEXT,
          thread_id TEXT,
          chat_type TEXT,
          chat_name TEXT,
          user_id TEXT,
          workspace_name TEXT,
          channel_name TEXT,
          created_at INTEGER,
          updated_at INTEGER
        );
        CREATE TABLE messages (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          turn_index INTEGER NOT NULL,
          role TEXT NOT NULL,
          content TEXT NOT NULL,
          kind TEXT NOT NULL DEFAULT 'turn',
          created_at INTEGER NOT NULL
        );
        CREATE VIRTUAL TABLE messages_fts USING fts5(
          content,
          session_id UNINDEXED,
          turn_index UNINDEXED,
          role UNINDEXED,
          kind UNINDEXED
        );
        CREATE TABLE summaries (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          session_id TEXT NOT NULL,
          start_turn INTEGER NOT NULL,
          end_turn INTEGER NOT NULL,
          depth INTEGER NOT NULL DEFAULT 0,
          summary_text TEXT NOT NULL,
          model TEXT,
          created_at INTEGER NOT NULL,
          source_hash TEXT
        );
        CREATE VIRTUAL TABLE summaries_fts USING fts5(
          summary_text,
          session_id UNINDEXED,
          start_turn UNINDEXED,
          end_turn UNINDEXED,
          depth UNINDEXED
        );
        """
    )
    conn.close()

    module = load_module("sessionvault_vault_db_parent_migration", "vault_db.py")
    db = module.VaultDB(str(db_path))
    try:
        with db._lock:
            cols = {row[1] for row in db._conn.execute("PRAGMA table_info(sessions)").fetchall()}
            indexes = {row[1] for row in db._conn.execute("PRAGMA index_list(sessions)").fetchall()}
        assert {"parent_chat_id", "parent_chat_name"}.issubset(cols)
        assert "idx_sessions_parent_chat_updated" in indexes
    finally:
        db.close()



def test_upsert_session_persists_parent_channel_metadata(tmp_path):
    module = load_module("sessionvault_vault_db_parent_persist", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        origin = module.OriginScope(
            platform="discord",
            chat_id="1491842817960710246",
            thread_id="1491842817960710246",
            chat_type="forum",
            chat_name="asGirafasVaoPaNeve's server / #jsc / Trading Memphis topic",
            parent_chat_id="1491809690848596240",
            parent_chat_name="#jsc",
            workspace_name="asGirafasVaoPaNeve's server",
            channel_name="#jsc",
        )
        db.upsert_session("sess-1", origin)
        meta = db.get_session_meta("sess-1")
        assert meta["parent_chat_id"] == "1491809690848596240"
        assert meta["parent_chat_name"] == "#jsc"
    finally:
        db.close()



def test_load_origin_reads_parent_channel_metadata(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "entry": {
                    "session_id": "sess-1",
                    "origin": {
                        "platform": "discord",
                        "chat_id": "1491842817960710246",
                        "thread_id": "1491842817960710246",
                        "chat_type": "forum",
                        "chat_name": "asGirafasVaoPaNeve's server / #jsc / Trading Memphis topic",
                        "parent_chat_id": "1491809690848596240",
                        "parent_chat_name": "#jsc",
                        "user_id": "283297056422887424",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    module = load_module("sessionvault_vault_db_parent_origin", "vault_db.py")
    origin = module.load_origin_from_sessions_index(str(tmp_path), "sess-1")

    assert origin.chat_id == "1491842817960710246"
    assert origin.thread_id == "1491842817960710246"
    assert origin.parent_chat_id == "1491809690848596240"
    assert origin.parent_chat_name == "#jsc"



def test_search_filters_by_parent_chat_id(tmp_path):
    module = load_module("sessionvault_vault_db_parent_search", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        origin_one = module.OriginScope(
            platform="discord",
            chat_id="thread-1",
            thread_id="thread-1",
            chat_name="guild / #jsc / topic one",
            parent_chat_id="parent-jsc",
            parent_chat_name="#jsc",
            workspace_name="guild",
            channel_name="#jsc",
        )
        origin_two = module.OriginScope(
            platform="discord",
            chat_id="thread-2",
            thread_id="thread-2",
            chat_name="guild / #ops / topic two",
            parent_chat_id="parent-ops",
            parent_chat_name="#ops",
            workspace_name="guild",
            channel_name="#ops",
        )
        db.upsert_session("s-jsc", origin_one)
        db.upsert_session("s-ops", origin_two)
        db.append_message("s-jsc", 1, "user", "qualitative signal")
        db.append_message("s-ops", 1, "user", "qualitative signal")

        hits = db.search(
            "qualitative",
            include_summaries=False,
            include_messages=True,
            parent_chat_id="parent-jsc",
        )

        assert [hit["session_id"] for hit in hits["messages"]] == ["s-jsc"]
    finally:
        db.close()



def test_provider_status_and_search_expose_parent_chat_id(tmp_path):
    provider_mod = load_provider_module()

    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "sessions.json").write_text(
        json.dumps(
            {
                "entry": {
                    "session_id": "discord-s1",
                    "origin": {
                        "platform": "discord",
                        "chat_id": "1491842817960710246",
                        "thread_id": "1491842817960710246",
                        "chat_type": "forum",
                        "chat_name": "asGirafasVaoPaNeve's server / #jsc / Trading Memphis topic",
                        "parent_chat_id": "1491809690848596240",
                        "parent_chat_name": "#jsc",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="discord-s1", hermes_home=str(tmp_path), platform="discord", agent_context="primary", agent_identity="kimi")
    provider.sync_turn("qualitative signal", "assistant answer")

    status = json.loads(provider.handle_tool_call("sessionvault_status", {}))
    result = json.loads(provider.handle_tool_call("sessionvault_search", {
        "query": "qualitative",
        "scope": "global",
        "include_summaries": False,
        "include_messages": True,
        "parent_chat_id": "1491809690848596240",
    }))

    assert status["origin"]["parent_chat_id"] == "1491809690848596240"
    assert status["origin"]["parent_chat_name"] == "#jsc"
    assert result["filters"]["parent_chat_id"] == "1491809690848596240"
    assert [hit["session_id"] for hit in result["hits"]["messages"]] == ["discord-s1"]
    provider.shutdown()
