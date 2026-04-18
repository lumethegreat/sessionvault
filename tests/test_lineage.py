import json
import sqlite3

import pytest

from test_timeline import load_module, load_provider_module


@pytest.fixture()
def vault_db(tmp_path):
    module = load_module("sessionvault_vault_db_for_lineage_tests", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        yield db
    finally:
        db.close()


def test_existing_db_is_migrated_with_lineage_columns(tmp_path):
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

    module = load_module("sessionvault_vault_db_lineage_migration", "vault_db.py")
    db = module.VaultDB(str(db_path))
    try:
        meta = db.get_session_meta("missing")
        assert meta == {}
        with db._lock:
            cols = {row[1] for row in db._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        assert {"previous_session_id", "split_from_session_id", "split_reason", "resumed_from_session_id", "suspended_at"}.issubset(cols)
    finally:
        db.close()



def test_lineage_walks_parent_chain(vault_db):
    module = load_module("sessionvault_vault_db_lineage_chain", "vault_db.py")
    origin = module.OriginScope(platform="discord", chat_id="chat-1", thread_id="thread-1", workspace_name="ws", channel_name="#general")
    vault_db.upsert_session("root", origin)
    vault_db.upsert_session("split", origin, previous_session_id="root", split_from_session_id="root", split_reason="compression")
    vault_db.upsert_session("resumed", origin, previous_session_id="split", resumed_from_session_id="split", split_reason="resume")

    lineage = vault_db.get_lineage("resumed")

    assert lineage["session_id"] == "resumed"
    assert lineage["ancestors"][0]["session_id"] == "split"
    assert lineage["ancestors"][0]["relation"] == "resumed_from"
    assert lineage["ancestors"][1]["session_id"] == "root"
    assert lineage["ancestors"][1]["relation"] == "split_from"



def test_provider_infers_previous_session_for_same_scope(tmp_path):
    provider_mod = load_provider_module()

    first = provider_mod.SessionVaultMemoryProvider()
    first.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    first.shutdown()

    second = provider_mod.SessionVaultMemoryProvider()
    second.initialize(session_id="s2", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")

    status = json.loads(second.handle_tool_call("sessionvault_status", {}))
    lineage = json.loads(second.handle_tool_call("sessionvault_lineage", {"session_id": "s2"}))

    assert status["session_meta"]["previous_session_id"] == "s1"
    assert lineage["ancestors"][0]["session_id"] == "s1"
    assert lineage["ancestors"][0]["relation"] == "previous"
    second.shutdown()
