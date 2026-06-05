import json

from test_timeline import load_provider_module


def _init_provider(tmp_path, *, session_id="current"):
    provider_mod = load_provider_module()
    provider = provider_mod.SessionVaultMemoryProvider()
    provider.initialize(
        session_id=session_id,
        hermes_home=str(tmp_path),
        platform="cli",
        agent_context="primary",
        agent_identity="cli",
    )
    return provider_mod, provider


def _insert_previous_message(provider_mod, provider, content, *, session_id="previous", turn_index=1, role="user"):
    provider._db.upsert_session(
        session_id,
        provider_mod.OriginScope(
            platform="cli",
            chat_id="cli",
            workspace_name="cli",
            channel_name="#cli",
        ),
    )
    provider._db.append_message(session_id, turn_index, role, content, kind="turn")


def test_prefetch_returns_previous_session_context_synchronously(tmp_path):
    provider_mod, provider = _init_provider(tmp_path)
    _insert_previous_message(
        provider_mod,
        provider,
        "session vault auto recall between threads",
    )

    ctx = provider.prefetch("auto recall between threads")

    assert "SessionVault auto-recall" in ctx
    assert "previous" in ctx
    assert "session vault auto recall" in ctx
    provider.shutdown()


def test_auto_recall_excludes_recent_current_session_turns(tmp_path):
    _, provider = _init_provider(tmp_path)
    provider.sync_turn(
        "session vault auto recall between threads",
        "assistant reply",
    )

    ctx = provider.prefetch("auto recall between threads")

    assert ctx == ""
    provider.shutdown()


def test_auto_recall_can_include_older_current_session_turns(tmp_path):
    _, provider = _init_provider(tmp_path)
    provider.sync_turn(
        "important old topic about session vault continuity",
        "assistant reply",
    )
    provider.sync_turn("unrelated filler one", "assistant reply")
    provider.sync_turn("unrelated filler two", "assistant reply")
    provider.sync_turn("unrelated filler three", "assistant reply")

    ctx = provider.prefetch("session vault continuity")

    assert "SessionVault auto-recall" in ctx
    assert "current" in ctx
    assert "important old topic" in ctx
    provider.shutdown()


def test_auto_recall_ignores_low_signal_queries(tmp_path):
    provider_mod, provider = _init_provider(tmp_path)
    _insert_previous_message(provider_mod, provider, "sim")

    assert provider.prefetch("sim") == ""
    assert provider.prefetch("ok") == ""
    provider.shutdown()


def test_auto_recall_can_be_disabled(tmp_path):
    cfg_dir = tmp_path / "sessionvault"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "db_path": str(tmp_path / "sessionvault" / "vault.db"),
        "auto_recall_enabled": False,
    }))

    provider_mod, provider = _init_provider(tmp_path)
    _insert_previous_message(
        provider_mod,
        provider,
        "session vault auto recall between threads",
    )

    assert provider.prefetch("session vault auto recall between threads") == ""
    provider.shutdown()


def test_auto_recall_dedupes_recent_hits(tmp_path):
    provider_mod, provider = _init_provider(tmp_path)
    _insert_previous_message(
        provider_mod,
        provider,
        "hermes remembers context across sessions",
    )

    first = provider.prefetch("remembers context across sessions")
    second = provider.prefetch("remembers context across sessions")

    assert "previous" in first
    assert second == ""
    provider.shutdown()
