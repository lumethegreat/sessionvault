"""SessionVault memory provider.

Local-first, lossless per-session memory stored in SQLite, with:
- Cross-session search (default scope: workspace/chat when derivable; fallback: chat)
- Incremental summaries stored alongside raw messages (raw is never deleted)

Activation:
  Set `memory.provider: sessionvault` in ~/.hermes/config.yaml

Design notes:
- This is a *memory provider plugin* (runs alongside builtin MEMORY.md / USER.md).
- Only one external provider can be active at a time.
- Workspace is best-effort when the platform exposes it in chat_name; otherwise
  workspace defaults to chat.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.memory_provider import MemoryProvider

from .summarizer import summarize_turns
from .vault_db import OriginScope, VaultDB, load_origin_from_sessions_index

logger = logging.getLogger(__name__)


SEARCH_SCHEMA = {
    "name": "sessionvault_search",
    "description": (
        "Search SessionVault memory (raw messages + summaries). "
        "Default scope is workspace/chat when available; otherwise chat. "
        "Use scope='global' to search all sessions in this profile."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "scope": {"type": "string", "enum": ["default", "chat", "workspace", "global"], "description": "Scope (default: default)."},
            "limit": {"type": "integer", "description": "Max results (default: 8, max: 25)."},
            "include_summaries": {"type": "boolean", "description": "Include summary nodes (default: true)."},
            "include_messages": {"type": "boolean", "description": "Include raw messages (default: true)."},
            "kind": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional raw-message kind filter (for example: ['turn']).",
            },
            "role": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional raw-message role filter (for example: ['user', 'assistant']).",
            },
            "session_id": {"type": "string", "description": "Optional exact session_id filter."},
            "platform": {"type": "string", "description": "Optional platform filter."},
            "chat_id": {"type": "string", "description": "Optional chat_id filter."},
            "thread_id": {"type": "string", "description": "Optional thread_id filter."},
        },
        "required": ["query"],
    },
}

EXPAND_SCHEMA = {
    "name": "sessionvault_expand",
    "description": (
        "Expand raw messages from a session by turn range. "
        "Use results from sessionvault_search (session_id + turn_index) to pick a range."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Session ID to expand."},
            "start_turn": {"type": "integer", "description": "Start turn index (inclusive)."},
            "end_turn": {"type": "integer", "description": "End turn index (inclusive)."},
            "max_chars": {"type": "integer", "description": "Safety cap on output size (default: 8000)."},
        },
        "required": ["session_id", "start_turn", "end_turn"],
    },
}

STATUS_SCHEMA = {
    "name": "sessionvault_status",
    "description": "Show SessionVault status (DB path, counts, current scope metadata).",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

DOCTOR_SCHEMA = {
    "name": "sessionvault_doctor",
    "description": "Run SessionVault integrity checks (SQLite + FTS).",
    "parameters": {"type": "object", "properties": {}, "required": []},
}

EVENTS_SCHEMA = {
    "name": "sessionvault_events",
    "description": "List structured SessionVault lifecycle events by scope and time range.",
    "parameters": {
        "type": "object",
        "properties": {
            "from": {
                "description": "Inclusive start of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "to": {
                "description": "Inclusive end of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "scope": {"type": "string", "enum": ["default", "chat", "workspace", "global"], "description": "Scope (default: default)."},
            "limit": {"type": "integer", "description": "Max results (default: 25, max: 200)."},
            "event_type": {"type": "string", "description": "Optional event_type filter."},
        },
        "required": [],
    },
}

TIMELINE_SCHEMA = {
    "name": "sessionvault_timeline",
    "description": (
        "Retrieve raw SessionVault messages by created_at time range. "
        "Useful for answering questions like 'what happened between X and Y?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from": {
                "description": "Inclusive start of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "to": {
                "description": "Inclusive end of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "scope": {"type": "string", "enum": ["default", "chat", "workspace", "global"], "description": "Scope (default: default)."},
            "limit": {"type": "integer", "description": "Max results (default: 25, max: 200)."},
        },
        "required": [],
    },
}

LINEAGE_SCHEMA = {
    "name": "sessionvault_lineage",
    "description": "Show lineage/continuity metadata for a SessionVault session.",
    "parameters": {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Optional target session_id. Defaults to current session."},
        },
        "required": [],
    },
}

RECENT_DECISIONS_SCHEMA = {
    "name": "sessionvault_recent_decisions",
    "description": (
        "Extract recent decision-like turns from SessionVault using deterministic rules. "
        "Useful for answering questions like 'what did we decide recently?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "from": {
                "description": "Inclusive start of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "to": {
                "description": "Inclusive end of time window. Accepts unix epoch seconds or ISO datetime string.",
                "anyOf": [{"type": "integer"}, {"type": "string"}],
            },
            "scope": {"type": "string", "enum": ["default", "chat", "workspace", "global"], "description": "Scope (default: default)."},
            "limit": {"type": "integer", "description": "Max decisions to return (default: 8, max: 50)."},
            "scan_limit": {"type": "integer", "description": "How many recent raw messages to scan before filtering (default: 80, max: 500)."},
        },
        "required": [],
    },
}

_DECISION_RULES = [
    ("decid", re.compile(r"\b(decid\w*|decis(?:ion|ão|oes|ões))\b", re.IGNORECASE)),
    ("próximo passo", re.compile(r"\bpr[oó]ximo passo\b", re.IGNORECASE)),
    ("vamos avançar", re.compile(r"\bvamos avan[çc]ar\b", re.IGNORECASE)),
    ("avançar", re.compile(r"\bpodes avan[çc]ar\b|\bavan[çc]ar\b", re.IGNORECASE)),
    ("ficou decidido", re.compile(r"\bficou decidido\b|\bagreed\b|\bwe will\b|\bwe should\b", re.IGNORECASE)),
    ("implementar", re.compile(r"\bimplement(?:ar|ed|ing)?\b", re.IGNORECASE)),
]


@dataclass
class _Config:
    db_path: str
    leaf_chunk_turns: int = 24
    leaf_min_turns: int = 10
    summary_model: str = ""
    summary_provider: str = ""


def _default_config(hermes_home: str) -> _Config:
    base = Path(hermes_home) / "sessionvault"
    return _Config(db_path=str(base / "vault.db"))


def _load_config(hermes_home: str) -> _Config:
    cfg_path = Path(hermes_home) / "sessionvault" / "config.json"
    cfg = {}
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception:
            cfg = {}
    d = _default_config(hermes_home)
    if isinstance(cfg.get("db_path"), str) and cfg.get("db_path"):
        d.db_path = cfg["db_path"].replace("$HERMES_HOME", str(hermes_home))
    for k in ("leaf_chunk_turns", "leaf_min_turns"):
        if cfg.get(k) is not None:
            try:
                setattr(d, k, int(cfg[k]))
            except Exception:
                pass
    if isinstance(cfg.get("summary_model"), str):
        d.summary_model = cfg.get("summary_model", "")
    if isinstance(cfg.get("summary_provider"), str):
        d.summary_provider = cfg.get("summary_provider", "")
    return d


def _parse_time_value(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean time values are not supported")
    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)

    normalized = text.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(normalized).timestamp())
    except ValueError as e:
        raise ValueError(f"invalid datetime: {value}") from e


def _normalize_str_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        candidates = [value]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = [value]

    out: List[str] = []
    for item in candidates:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


class SessionVaultMemoryProvider(MemoryProvider):
    def __init__(self):
        self._session_id = ""
        self._hermes_home = ""
        self._platform = ""
        self._agent_context = "primary"
        self._agent_identity = ""

        self._cfg: Optional[_Config] = None
        self._db: Optional[VaultDB] = None
        self._origin: OriginScope = OriginScope(platform="", chat_id="")

        self._turn_counter = 0

        self._prefetch_lock = threading.Lock()
        self._prefetch_cached = ""

        self._work_q: "queue.Queue[tuple]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def name(self) -> str:
        return "sessionvault"

    def is_available(self) -> bool:
        # Local SQLite always available.
        return True

    def get_config_schema(self):
        from hermes_constants import display_hermes_home
        default_db = f"{display_hermes_home()}/sessionvault/vault.db"
        return [
            {"key": "db_path", "description": "SQLite DB path (profile-scoped). Supports $HERMES_HOME.", "default": default_db},
            {"key": "leaf_chunk_turns", "description": "Turns per leaf summary chunk (default: 24)", "default": "24"},
            {"key": "leaf_min_turns", "description": "Minimum turns before summarizing (default: 10)", "default": "10"},
            {"key": "summary_model", "description": "Optional summary model override (default: use auxiliary compression defaults)", "default": ""},
            {"key": "summary_provider", "description": "Optional summary provider override", "default": ""},
        ]

    def save_config(self, values: Dict[str, Any], hermes_home: str) -> None:
        cfg_dir = Path(hermes_home) / "sessionvault"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "config.json"
        cfg_path.write_text(json.dumps(values, indent=2, ensure_ascii=False), encoding="utf-8")

    def initialize(self, session_id: str, **kwargs) -> None:
        self._session_id = str(session_id or "")
        self._hermes_home = str(kwargs.get("hermes_home") or "")
        self._platform = str(kwargs.get("platform") or "")
        self._agent_context = str(kwargs.get("agent_context") or "primary")
        self._agent_identity = str(kwargs.get("agent_identity") or "")

        self._cfg = _load_config(self._hermes_home) if self._hermes_home else _default_config(".")
        self._db = VaultDB(self._cfg.db_path)

        explicit_previous_session_id = str(kwargs.get("previous_session_id") or "").strip()
        split_from_session_id = str(kwargs.get("split_from_session_id") or "").strip()
        split_reason = str(kwargs.get("split_reason") or "").strip()
        resumed_from_session_id = str(kwargs.get("resumed_from_session_id") or "").strip()
        suspended_at_raw = kwargs.get("suspended_at")
        suspended_at = int(suspended_at_raw) if suspended_at_raw not in (None, "") else None

        # Derive origin / scope metadata
        if self._platform == "cli":
            self._origin = OriginScope(
                platform="cli",
                chat_id="cli",
                thread_id="",
                chat_type="cli",
                chat_name="CLI",
                user_id=self._agent_identity or "cli",
                workspace_name=self._agent_identity or "cli",
                channel_name="#cli",
            )
        else:
            self._origin = load_origin_from_sessions_index(self._hermes_home, self._session_id)
            if not self._origin.platform:
                self._origin.platform = self._platform or ""

            # Fallback workspace/channel when not parseable
            if not self._origin.workspace_name or not self._origin.channel_name:
                # workspace defaults to chat key; channel defaults to chat_name or chat_id
                self._origin.workspace_name = self._origin.workspace_name or self._origin.scope_chat_key()
                self._origin.channel_name = self._origin.channel_name or (self._origin.chat_name or self._origin.chat_id or "")

        inferred_previous_session_id = ""
        if self._db and not explicit_previous_session_id:
            try:
                inferred_previous_session_id = self._db.infer_previous_session_id(self._origin, self._session_id)
            except Exception:
                inferred_previous_session_id = ""

        self._db.upsert_session(
            self._session_id,
            self._origin,
            previous_session_id=explicit_previous_session_id or inferred_previous_session_id,
            split_from_session_id=split_from_session_id,
            split_reason=split_reason,
            resumed_from_session_id=resumed_from_session_id,
            suspended_at=suspended_at,
        )
        try:
            event_payload = {
                "platform": self._origin.platform,
                "chat_id": self._origin.chat_id,
                "thread_id": self._origin.thread_id,
            }
            if explicit_previous_session_id or inferred_previous_session_id:
                event_payload["previous_session_id"] = explicit_previous_session_id or inferred_previous_session_id
            if split_from_session_id:
                event_payload["split_from_session_id"] = split_from_session_id
            if split_reason:
                event_payload["split_reason"] = split_reason
            if resumed_from_session_id:
                event_payload["resumed_from_session_id"] = resumed_from_session_id
            self._db.insert_event(self._session_id, "session_initialized", event_payload)
        except Exception:
            pass
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        def _run():
            while not self._stop.is_set():
                try:
                    item = self._work_q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if item is None:
                    break
                try:
                    kind = item[0]
                    if kind == "prefetch":
                        self._do_prefetch(item[1])
                    elif kind == "summarize_leaf":
                        self._do_summarize_leaf(*item[1:])
                except Exception as e:
                    logger.debug("SessionVault worker task failed: %s", e)
                finally:
                    try:
                        self._work_q.task_done()
                    except Exception:
                        pass

        self._worker = threading.Thread(target=_run, daemon=True, name="sessionvault-worker")
        self._worker.start()

    # -- Core operations -------------------------------------------------

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        if self._agent_context != "primary":
            return
        if not self._db:
            return

        # Turn index increments once per *exchange* (user+assistant)
        self._turn_counter += 1
        t = self._turn_counter

        try:
            self._db.append_message(self._session_id, t, "user", (user_content or "").strip(), kind="turn")
            self._db.append_message(self._session_id, t, "assistant", (assistant_content or "").strip(), kind="turn")
        except Exception as e:
            logger.debug("SessionVault sync_turn failed: %s", e)
            return

        # Summarization trigger (cheap heuristic by turn count)
        try:
            if self._cfg and t >= max(self._cfg.leaf_min_turns, self._cfg.leaf_chunk_turns):
                # Summarize the oldest unsummarized chunk: [1..leaf_chunk_turns], then [leaf_chunk_turns+1..2*leaf_chunk_turns], etc.
                chunk = int(self._cfg.leaf_chunk_turns)
                end_turn = (t // chunk) * chunk
                start_turn = end_turn - chunk + 1
                if start_turn >= 1:
                    self._work_q.put(("summarize_leaf", start_turn, end_turn))
        except Exception:
            pass

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        # Schedule background prefetch for next turn.
        if not query:
            return
        self._ensure_worker()
        try:
            self._work_q.put(("prefetch", str(query)[:5000]))
        except Exception:
            pass

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        with self._prefetch_lock:
            return self._prefetch_cached or ""

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        # Ensure we persist a snapshot of what is about to be compacted.
        if not self._db or not messages:
            return ""
        try:
            text_parts = []
            for m in messages:
                r = m.get("role")
                c = m.get("content")
                if r in ("user", "assistant") and isinstance(c, str) and c.strip():
                    text_parts.append(f"{r}: {c.strip()}")
            snapshot = "\n".join(text_parts)
            if snapshot.strip():
                self._db.insert_event(
                    self._session_id,
                    "pre_compress",
                    {"message_count": len(text_parts), "chars": len(snapshot)},
                )
                # Store as a special kind under a synthetic turn index
                t = self._db.last_turn_index(self._session_id) + 1
                self._db.append_message(self._session_id, t, "system", snapshot[:20000], kind="pre_compress_snapshot")
        except Exception:
            pass
        return ""

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        # Optional: run one more summarize job.
        try:
            if self._db:
                self._db.insert_event(
                    self._session_id,
                    "session_end",
                    {"turn_count": int(self._turn_counter), "message_count": len(messages or [])},
                )
            if self._cfg and self._turn_counter >= self._cfg.leaf_min_turns:
                chunk = int(self._cfg.leaf_chunk_turns)
                t = int(self._turn_counter)
                end_turn = (t // chunk) * chunk
                start_turn = end_turn - chunk + 1
                if start_turn >= 1:
                    self._work_q.put(("summarize_leaf", start_turn, end_turn))
        except Exception:
            pass

    # -- Background tasks ------------------------------------------------

    def _do_prefetch(self, query: str) -> None:
        if not self._db:
            return

        # Default scope logic:
        # - If workspace/channel parseable, use it.
        # - Else, use chat_key.
        scope_chat_key = self._origin.scope_chat_key()
        ws = self._origin.workspace_name
        ch = self._origin.channel_name

        hits = self._db.search(
            query,
            workspace_name=ws,
            channel_name=ch,
            scope_chat_key=scope_chat_key,
            limit=6,
            include_summaries=True,
            include_messages=True,
        )

        lines = []
        if hits.get("summaries"):
            lines.append("### SessionVault (summaries)")
            for h in hits["summaries"][:4]:
                lines.append(
                    f"- ({h['session_id']} t{h['start_turn']}..t{h['end_turn']}) {h['snippet']}"
                )
        if hits.get("messages"):
            lines.append("### SessionVault (messages)")
            for h in hits["messages"][:4]:
                lines.append(
                    f"- ({h['session_id']} t{h['turn_index']} {h['role']}) {h['snippet']}"
                )

        block = "\n".join(lines).strip()
        with self._prefetch_lock:
            self._prefetch_cached = block

    def _do_summarize_leaf(self, start_turn: int, end_turn: int) -> None:
        if not self._db or not self._cfg:
            return
        # Serialize a compact view of the chunk
        turns = self._db.get_messages_range(self._session_id, start_turn, end_turn)
        if not turns:
            return

        # Avoid summarizing mostly-empty chunks
        payload_parts = []
        for t in turns:
            role = t.get("role", "")
            content = (t.get("content") or "").strip()
            if not content:
                continue
            if role in ("user", "assistant"):
                payload_parts.append(f"[{role}] {content[:1800]}")
        if len(payload_parts) < max(4, int(self._cfg.leaf_min_turns // 2)):
            return

        serialized = "\n\n".join(payload_parts)
        summary, src_hash = summarize_turns(
            serialized,
            model_override=self._cfg.summary_model,
            provider_override=self._cfg.summary_provider,
            timeout=120.0,
        )
        if not summary:
            return

        try:
            self._db.insert_summary(
                self._session_id,
                start_turn,
                end_turn,
                summary_text=summary,
                depth=0,
                model=self._cfg.summary_model,
                source_hash=src_hash,
            )
        except Exception as e:
            logger.debug("SessionVault insert_summary failed: %s", e)

    # -- Tooling ---------------------------------------------------------

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        return [SEARCH_SCHEMA, EXPAND_SCHEMA, STATUS_SCHEMA, DOCTOR_SCHEMA, EVENTS_SCHEMA, TIMELINE_SCHEMA, LINEAGE_SCHEMA, RECENT_DECISIONS_SCHEMA]

    def handle_tool_call(self, tool_name: str, args: Dict[str, Any], **kwargs) -> str:
        try:
            if tool_name == "sessionvault_search":
                return self._tool_search(args)
            if tool_name == "sessionvault_expand":
                return self._tool_expand(args)
            if tool_name == "sessionvault_status":
                return self._tool_status()
            if tool_name == "sessionvault_doctor":
                return self._tool_doctor()
            if tool_name == "sessionvault_events":
                return self._tool_events(args)
            if tool_name == "sessionvault_timeline":
                return self._tool_timeline(args)
            if tool_name == "sessionvault_lineage":
                return self._tool_lineage(args)
            if tool_name == "sessionvault_recent_decisions":
                return self._tool_recent_decisions(args)
        except Exception as e:
            return json.dumps({"error": str(e)})
        return json.dumps({"error": f"Unknown tool: {tool_name}"})

    def _tool_status(self) -> str:
        if not self._db or not self._cfg:
            return json.dumps({"error": "not initialized"})
        d = self._db.doctor()
        meta = self._db.get_session_meta(self._session_id)
        return json.dumps({
            "provider": self.name,
            "session_id": self._session_id,
            "db": d,
            "origin": {
                "platform": self._origin.platform,
                "chat_id": self._origin.chat_id,
                "thread_id": self._origin.thread_id,
                "workspace_name": self._origin.workspace_name,
                "channel_name": self._origin.channel_name,
                "chat_name": self._origin.chat_name,
            },
            "session_meta": meta,
            "config": {
                "db_path": self._cfg.db_path,
                "leaf_chunk_turns": self._cfg.leaf_chunk_turns,
                "leaf_min_turns": self._cfg.leaf_min_turns,
                "summary_model": self._cfg.summary_model,
                "summary_provider": self._cfg.summary_provider,
            }
        }, ensure_ascii=False)

    def _tool_doctor(self) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})
        return json.dumps(self._db.doctor(), ensure_ascii=False)

    def _tool_events(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})
        scope = str(args.get("scope") or "default").strip().lower()
        limit = max(1, min(int(args.get("limit") or 25), 200))
        created_at_from = _parse_time_value(args.get("from"))
        created_at_to = _parse_time_value(args.get("to"))
        event_type = str(args.get("event_type") or "").strip()
        if created_at_from is not None and created_at_to is not None and created_at_to < created_at_from:
            return json.dumps({"error": "'to' must be greater than or equal to 'from'"})

        ws, ch, chat_key = self._resolve_scope_filters(scope)
        session_ids = self._db.list_sessions_by_scope(
            workspace_name=ws,
            channel_name=ch,
            scope_chat_key=chat_key,
        )
        if scope == "global":
            session_ids = []

        hits = self._db.get_events(
            session_ids=session_ids,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            event_type=event_type,
            limit=limit,
        )
        return json.dumps({
            "scope": scope,
            "filters": {"workspace_name": ws, "channel_name": ch, "chat_key": chat_key, "event_type": event_type},
            "window": {"from_epoch": created_at_from, "to_epoch": created_at_to},
            "hits": hits,
        }, ensure_ascii=False)

    def _tool_lineage(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})
        session_id = str(args.get("session_id") or self._session_id).strip()
        if not session_id:
            return json.dumps({"error": "session_id is required"})
        lineage = self._db.get_lineage(session_id)
        if not lineage:
            return json.dumps({"error": f"session not found: {session_id}"}, ensure_ascii=False)
        return json.dumps(lineage, ensure_ascii=False)

    def _tool_recent_decisions(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})

        scope = str(args.get("scope") or "default").strip().lower()
        limit = max(1, min(int(args.get("limit") or 8), 50))
        scan_limit = max(limit, min(int(args.get("scan_limit") or 80), 500))
        created_at_from = _parse_time_value(args.get("from"))
        created_at_to = _parse_time_value(args.get("to"))
        if created_at_from is not None and created_at_to is not None and created_at_to < created_at_from:
            return json.dumps({"error": "'to' must be greater than or equal to 'from'"})

        ws, ch, chat_key = self._resolve_scope_filters(scope)
        session_ids = self._db.list_sessions_by_scope(
            workspace_name=ws,
            channel_name=ch,
            scope_chat_key=chat_key,
        )
        if scope == "global":
            session_ids = []

        rows = self._db.recent_messages(
            session_ids=session_ids,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=scan_limit,
        )

        hits = []
        for row in rows:
            if row.get("kind") != "turn":
                continue
            role = str(row.get("role") or "").strip().lower()
            if role not in {"user", "assistant"}:
                continue
            content = str(row.get("content") or "").strip()
            if not content:
                continue
            matched_rules = [name for name, pattern in _DECISION_RULES if pattern.search(content)]
            if not matched_rules:
                continue
            excerpt = re.sub(r"\s+", " ", content).strip()
            if len(excerpt) > 240:
                excerpt = excerpt[:237] + "..."
            hits.append({
                "session_id": row["session_id"],
                "turn_index": row["turn_index"],
                "role": role,
                "created_at": row["created_at"],
                "excerpt": excerpt,
                "matched_rules": matched_rules,
            })
            if len(hits) >= limit:
                break

        return json.dumps({
            "scope": scope,
            "filters": {"workspace_name": ws, "channel_name": ch, "chat_key": chat_key},
            "window": {"from_epoch": created_at_from, "to_epoch": created_at_to},
            "scan_limit": scan_limit,
            "hits": hits,
        }, ensure_ascii=False)

    def _resolve_scope_filters(self, scope: str) -> Tuple[str, str, str]:
        ws = self._origin.workspace_name
        ch = self._origin.channel_name
        chat_key = self._origin.scope_chat_key()

        if scope == "global":
            ws = ""
            ch = ""
            chat_key = ""
        elif scope == "workspace":
            if not ws:
                ws = self._origin.scope_chat_key()
            ch = ""
        elif scope == "chat":
            ws = ""
            ch = ""
        return ws, ch, chat_key

    def _tool_search(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})
        query = str(args.get("query") or "").strip()
        scope = str(args.get("scope") or "default").strip().lower()
        limit = int(args.get("limit") or 8)
        include_summaries = bool(args.get("include_summaries", True))
        include_messages = bool(args.get("include_messages", True))
        kind_filters = _normalize_str_list(args.get("kind"))
        role_filters = _normalize_str_list(args.get("role"))
        session_id = str(args.get("session_id") or "").strip()
        platform = str(args.get("platform") or "").strip()
        chat_id = str(args.get("chat_id") or "").strip()
        thread_id = str(args.get("thread_id") or "").strip()

        ws, ch, chat_key = self._resolve_scope_filters(scope)

        hits = self._db.search(
            query,
            workspace_name=ws,
            channel_name=ch,
            scope_chat_key=chat_key,
            limit=limit,
            include_summaries=include_summaries,
            include_messages=include_messages,
            kind=kind_filters,
            role=role_filters,
            session_id=session_id,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
        )
        return json.dumps({
            "query": query,
            "scope": scope,
            "filters": {
                "workspace_name": ws,
                "channel_name": ch,
                "chat_key": chat_key,
                "kind": kind_filters,
                "role": role_filters,
                "session_id": session_id,
                "platform": platform,
                "chat_id": chat_id,
                "thread_id": thread_id,
            },
            "hits": hits,
        }, ensure_ascii=False)

    def _tool_timeline(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})

        scope = str(args.get("scope") or "default").strip().lower()
        limit = max(1, min(int(args.get("limit") or 25), 200))
        created_at_from = _parse_time_value(args.get("from"))
        created_at_to = _parse_time_value(args.get("to"))
        if created_at_from is None and created_at_to is None:
            return json.dumps({"error": "at least one of 'from' or 'to' is required"})
        if created_at_from is not None and created_at_to is not None and created_at_to < created_at_from:
            return json.dumps({"error": "'to' must be greater than or equal to 'from'"})

        ws, ch, chat_key = self._resolve_scope_filters(scope)
        session_ids = self._db.list_sessions_by_scope(
            workspace_name=ws,
            channel_name=ch,
            scope_chat_key=chat_key,
        )
        if scope == "global":
            session_ids = []

        hits = self._db.timeline(
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            session_ids=session_ids,
            limit=limit,
        )
        return json.dumps({
            "scope": scope,
            "filters": {"workspace_name": ws, "channel_name": ch, "chat_key": chat_key},
            "window": {"from_epoch": created_at_from, "to_epoch": created_at_to},
            "hits": hits,
        }, ensure_ascii=False)

    def _tool_expand(self, args: Dict[str, Any]) -> str:
        if not self._db:
            return json.dumps({"error": "not initialized"})
        sid = str(args.get("session_id") or "").strip()
        start_turn = int(args.get("start_turn") or 0)
        end_turn = int(args.get("end_turn") or 0)
        max_chars = int(args.get("max_chars") or 8000)
        if not sid or start_turn <= 0 or end_turn <= 0 or end_turn < start_turn:
            return json.dumps({"error": "session_id, start_turn, end_turn required"})

        msgs = self._db.get_messages_range(sid, start_turn, end_turn)
        # Format as a readable block and cap
        text_lines = []
        for m in msgs:
            text_lines.append(f"t{m['turn_index']} {m['role']}: {m['content']}")
        blob = "\n".join(text_lines)
        if len(blob) > max_chars:
            blob = blob[: max_chars - 200] + "\n...[truncated]...\n" + blob[-150:]
        return json.dumps({
            "session_id": sid,
            "start_turn": start_turn,
            "end_turn": end_turn,
            "text": blob,
        }, ensure_ascii=False)

    def shutdown(self) -> None:
        try:
            self._stop.set()
            try:
                self._work_q.put(None)
            except Exception:
                pass
            if self._worker and self._worker.is_alive():
                self._worker.join(timeout=2.0)
        except Exception:
            pass
        try:
            if self._db:
                self._db.close()
        except Exception:
            pass


def register(ctx) -> None:
    ctx.register_memory_provider(SessionVaultMemoryProvider())
