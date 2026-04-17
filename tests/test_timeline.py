import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "plugin"


def load_module(module_name: str, relative_path: str):
    path = PLUGIN_DIR / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_provider_module():
    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = []
    sys.modules["agent"] = agent_pkg

    memory_provider_mod = types.ModuleType("agent.memory_provider")
    memory_provider_mod.MemoryProvider = type("MemoryProvider", (), {})
    sys.modules["agent.memory_provider"] = memory_provider_mod

    auxiliary_client_mod = types.ModuleType("agent.auxiliary_client")
    auxiliary_client_mod.call_llm = lambda **kwargs: {"content": []}
    auxiliary_client_mod.extract_content_or_reasoning = lambda resp: ""
    sys.modules["agent.auxiliary_client"] = auxiliary_client_mod

    hermes_constants_mod = types.ModuleType("hermes_constants")
    hermes_constants_mod.display_hermes_home = lambda: "~/.hermes"
    sys.modules["hermes_constants"] = hermes_constants_mod

    package_name = "sessionvault_plugin"
    spec = importlib.util.spec_from_file_location(
        package_name,
        PLUGIN_DIR / "__init__.py",
        submodule_search_locations=[str(PLUGIN_DIR)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def vault_db(tmp_path):
    module = load_module("sessionvault_vault_db", "vault_db.py")
    db = module.VaultDB(str(tmp_path / "vault.db"))
    try:
        yield db
    finally:
        db.close()



def test_timeline_returns_only_rows_in_requested_time_window(vault_db):
    vault_db.upsert_session("s1", load_module("sessionvault_vault_db_reuse", "vault_db.py").OriginScope(platform="discord", chat_id="c1", workspace_name="ws", channel_name="#general"))
    vault_db.append_message("s1", 1, "user", "first turn")
    vault_db.append_message("s1", 2, "assistant", "second turn")
    vault_db.append_message("s1", 3, "user", "third turn")
    with vault_db._lock:
        vault_db._conn.execute("UPDATE messages SET created_at=100 WHERE turn_index=1")
        vault_db._conn.execute("UPDATE messages SET created_at=200 WHERE turn_index=2")
        vault_db._conn.execute("UPDATE messages SET created_at=300 WHERE turn_index=3")

    rows = vault_db.timeline(created_at_from=150, created_at_to=250, session_ids=["s1"], limit=10)

    assert rows == [
        {
            "session_id": "s1",
            "turn_index": 2,
            "role": "assistant",
            "content": "second turn",
            "kind": "turn",
            "created_at": 200,
        }
    ]



def test_provider_timeline_tool_returns_iso_filtered_rows(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")
    provider.sync_turn("alpha", "beta")
    provider.sync_turn("gamma", "delta")
    with provider._db._lock:
        provider._db._conn.execute("UPDATE messages SET created_at=100 WHERE turn_index=1")
        provider._db._conn.execute("UPDATE messages SET created_at=200 WHERE turn_index=2")

    result = json.loads(provider.handle_tool_call("sessionvault_timeline", {
        "from": 150,
        "to": 250,
        "limit": 10,
    }))

    assert result["scope"] == "default"
    assert result["window"]["from_epoch"] == 150
    assert result["window"]["to_epoch"] == 250
    assert [row["turn_index"] for row in result["hits"]] == [2, 2]
    assert {row["role"] for row in result["hits"]} == {"user", "assistant"}
    provider.shutdown()



def test_provider_timeline_tool_rejects_invalid_time_range(tmp_path):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(session_id="s1", hermes_home=str(tmp_path), platform="cli", agent_context="primary", agent_identity="cli")

    result = json.loads(provider.handle_tool_call("sessionvault_timeline", {"from": "not-a-date"}))

    assert "error" in result
    provider.shutdown()
