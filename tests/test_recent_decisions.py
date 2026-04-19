import json

from test_timeline import load_module, load_provider_module


def test_vault_db_recent_messages_returns_newest_first(tmp_path):
    module = load_module("sessionvault_vault_db_recent_messages", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        origin = module.OriginScope(platform="discord", chat_id="chat-1", thread_id="thread-1", workspace_name="ws", channel_name="#general")
        db.upsert_session("s1", origin)
        db.append_message("s1", 1, "user", "older")
        db.append_message("s1", 2, "assistant", "newer")
        with db._lock:
            db._conn.execute("UPDATE messages SET created_at=100 WHERE turn_index=1")
            db._conn.execute("UPDATE messages SET created_at=200 WHERE turn_index=2")

        rows = db.recent_messages(session_ids=["s1"], limit=10)

        assert [row["turn_index"] for row in rows] == [2, 1]
        assert [row["content"] for row in rows] == ["newer", "older"]
    finally:
        db.close()


def test_provider_recent_decisions_extracts_decision_like_turns(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("Can we postpone this?", "We decided to ship the provider cleanup today.")
    provider.sync_turn("ok podes avançar", "Próximo passo natural: implementar recent decisions.")
    provider.sync_turn("just chatting", "This sentence is descriptive only.")
    with provider._db._lock:
        provider._db._conn.execute("UPDATE messages SET created_at=100 WHERE turn_index=1 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=101 WHERE turn_index=1 AND role='assistant'")
        provider._db._conn.execute("UPDATE messages SET created_at=200 WHERE turn_index=2 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=201 WHERE turn_index=2 AND role='assistant'")
        provider._db._conn.execute("UPDATE messages SET created_at=300 WHERE turn_index=3 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=301 WHERE turn_index=3 AND role='assistant'")

    result = json.loads(provider.handle_tool_call("sessionvault_recent_decisions", {"scope": "global", "limit": 5, "scan_limit": 10}))

    assert result["scope"] == "global"
    assert len(result["hits"]) == 3
    assert [hit["turn_index"] for hit in result["hits"]] == [2, 2, 1]
    assert result["hits"][0]["role"] == "assistant"
    assert "próximo passo" in result["hits"][0]["excerpt"].lower()
    assert result["hits"][1]["role"] == "user"
    assert "avançar" in " ".join(result["hits"][1]["matched_rules"]).lower()
    assert result["hits"][2]["role"] == "assistant"
    assert "decid" in " ".join(result["hits"][2]["matched_rules"]).lower()
    provider.shutdown()


def test_provider_recent_decisions_rejects_invalid_window(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")

    result = json.loads(provider.handle_tool_call("sessionvault_recent_decisions", {"from": 200, "to": 100}))

    assert "error" in result
    provider.shutdown()
