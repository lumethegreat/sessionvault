import json

from test_timeline import load_provider_module


def test_provider_what_were_we_doing_returns_structured_recall(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("precisamos fechar isto", "Decidimos avançar com o event store.")
    provider.sync_turn("e agora?", "Próximo passo natural: integrar o patch no install workflow.")
    provider.sync_turn("ok podes avançar", "Estou a implementar what were we doing.")
    provider._db.insert_event("s1", "session_split", {"reason": "compression"}, created_at=250)
    with provider._db._lock:
        provider._db._conn.execute("UPDATE messages SET created_at=100 WHERE turn_index=1 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=101 WHERE turn_index=1 AND role='assistant'")
        provider._db._conn.execute("UPDATE messages SET created_at=200 WHERE turn_index=2 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=201 WHERE turn_index=2 AND role='assistant'")
        provider._db._conn.execute("UPDATE messages SET created_at=300 WHERE turn_index=3 AND role='user'")
        provider._db._conn.execute("UPDATE messages SET created_at=301 WHERE turn_index=3 AND role='assistant'")

    result = json.loads(provider.handle_tool_call("sessionvault_what_were_we_doing", {"scope": "global", "limit": 3, "scan_limit": 10}))

    assert result["scope"] == "global"
    assert result["latest_user_turn"]["turn_index"] == 3
    assert "ok podes avançar" in result["latest_user_turn"]["excerpt"].lower()
    assert result["latest_assistant_turn"]["turn_index"] == 3
    assert "what were we doing" in result["latest_assistant_turn"]["excerpt"].lower()
    assert result["recent_decisions"][0]["turn_index"] == 3
    assert any(event["event_type"] == "session_split" for event in result["recent_events"])
    assert any("último pedido" in line.lower() for line in result["summary_lines"])
    assert any("próximo passo" in line.lower() for line in result["summary_lines"])
    provider.shutdown()


def test_provider_what_were_we_doing_rejects_invalid_window(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")

    result = json.loads(provider.handle_tool_call("sessionvault_what_were_we_doing", {"from": 200, "to": 100}))

    assert "error" in result
    provider.shutdown()
