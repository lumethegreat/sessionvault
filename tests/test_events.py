import json
import sqlite3

import pytest

from test_timeline import load_module, load_provider_module


@pytest.fixture()
def vault_db(tmp_path):
    module = load_module("sessionvault_vault_db_for_event_tests", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        yield db
    finally:
        db.close()


def test_existing_db_is_migrated_with_events_table(tmp_path):
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
        """
    )
    conn.close()

    module = load_module("sessionvault_vault_db_event_migration", "vault_db.py")
    db = module.VaultDB(str(db_path))
    try:
        with db._lock:
            tables = {row[0] for row in db._conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "events" in tables
    finally:
        db.close()


def test_event_roundtrip_and_scope_filtering(vault_db):
    module = load_module("sessionvault_vault_db_event_scope", "vault_db.py")
    discord_origin = module.OriginScope(platform="discord", chat_id="chat-1", thread_id="thread-1", workspace_name="ws", channel_name="#general")
    cli_origin = module.OriginScope(platform="cli", chat_id="cli", workspace_name="cli", channel_name="#cli")
    vault_db.upsert_session("s1", discord_origin)
    vault_db.upsert_session("s2", cli_origin)
    vault_db.insert_event("s1", "pre_compress", {"message_count": 10}, created_at=100)
    vault_db.insert_event("s2", "session_end", {"turns": 2}, created_at=200)

    events = vault_db.get_events(session_ids=["s1"], created_at_from=50, created_at_to=150, limit=10)

    assert events == [{
        "session_id": "s1",
        "event_type": "pre_compress",
        "payload": {"message_count": 10},
        "created_at": 100,
    }]


def test_provider_records_and_returns_lifecycle_events(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("alpha", "beta")
    provider.on_pre_compress([
        {"role": "user", "content": "alpha"},
        {"role": "assistant", "content": "beta"},
    ])
    provider.on_session_end([])

    result = json.loads(provider.handle_tool_call("sessionvault_events", {"scope": "global", "limit": 10}))

    event_types = [event["event_type"] for event in result["hits"]]
    assert "session_initialized" in event_types
    assert "pre_compress" in event_types
    assert "session_end" in event_types
    provider.shutdown()


def test_record_gateway_event_helper_writes_to_vault(tmp_path):
    module = load_module("sessionvault_vault_db_event_helper", "vault_db.py")
    hermes_home = tmp_path
    db_path = hermes_home / "sessionvault" / "vault.db"
    db = module.VaultDB(str(db_path))
    try:
        origin = module.OriginScope(platform="discord", chat_id="chat-1", thread_id="thread-1", workspace_name="ws", channel_name="#general")
        db.upsert_session("s1", origin)
    finally:
        db.close()

    written = module.record_gateway_event(str(hermes_home), "s1", "session_split", {"new_session_id": "s2"})
    assert written is True

    db2 = module.VaultDB(str(db_path))
    try:
        events = db2.get_events(session_ids=["s1"], limit=10)
    finally:
        db2.close()
    assert any(event["event_type"] == "session_split" for event in events)
