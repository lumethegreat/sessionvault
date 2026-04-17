import json

import pytest

from test_timeline import load_module, load_provider_module


@pytest.fixture()
def vault_db(tmp_path):
    module = load_module("sessionvault_vault_db_for_search_tests", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        yield db
    finally:
        db.close()


def test_search_filters_by_kind_and_role(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("alpha mention", "assistant mention")
    provider.on_pre_compress([
        {"role": "user", "content": "alpha mention"},
        {"role": "assistant", "content": "assistant mention"},
    ])

    result = json.loads(provider.handle_tool_call("sessionvault_search", {
        "query": "mention",
        "scope": "global",
        "kind": ["turn"],
        "role": ["assistant"],
        "include_summaries": False,
        "include_messages": True,
    }))

    hits = result["hits"]["messages"]
    assert result["hits"]["summaries"] == []
    assert len(hits) == 1
    assert hits[0]["role"] == "assistant"
    assert hits[0]["kind"] == "turn"
    provider.shutdown()



def test_search_filters_by_session_and_platform_metadata(vault_db):
    module = load_module("sessionvault_vault_db_filters", "vault_db.py")
    vault_db.upsert_session("discord-s1", module.OriginScope(platform="discord", chat_id="chat-1", thread_id="thread-1", workspace_name="ws", channel_name="#general"))
    vault_db.upsert_session("cli-s2", module.OriginScope(platform="cli", chat_id="cli", workspace_name="cli", channel_name="#cli"))
    vault_db.append_message("discord-s1", 1, "user", "shared token")
    vault_db.append_message("cli-s2", 1, "user", "shared token")

    hits = vault_db.search(
        "shared",
        include_summaries=False,
        include_messages=True,
        platform="discord",
        session_id="discord-s1",
        chat_id="chat-1",
        thread_id="thread-1",
    )

    assert [hit["session_id"] for hit in hits["messages"]] == ["discord-s1"]



def test_search_without_filters_remains_backwards_compatible(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("alpha mention", "assistant mention")

    result = json.loads(provider.handle_tool_call("sessionvault_search", {
        "query": "mention",
        "scope": "global",
        "include_summaries": False,
        "include_messages": True,
    }))

    roles = {hit["role"] for hit in result["hits"]["messages"]}
    assert roles == {"user", "assistant"}
    provider.shutdown()
