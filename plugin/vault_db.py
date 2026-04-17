from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


logger = logging.getLogger(__name__)


@dataclass
class OriginScope:
    platform: str
    # Stable identifiers
    chat_id: str = ""
    thread_id: str = ""
    chat_type: str = ""
    # Human names (best-effort)
    chat_name: str = ""
    user_id: str = ""

    # Derived / heuristic
    workspace_name: str = ""
    channel_name: str = ""

    def scope_chat_key(self) -> str:
        # Most stable cross-platform unit.
        if self.platform and self.chat_id:
            return f"{self.platform}:{self.chat_id}"
        return f"{self.platform or 'unknown'}:"

    def scope_thread_key(self) -> str:
        if self.thread_id:
            return f"{self.scope_chat_key()}:{self.thread_id}"
        return ""


_WS_SPLIT_RE = re.compile(r"\s*/\s*")


def parse_workspace_channel(platform: str, chat_name: str) -> Tuple[str, str]:
    """Best-effort parse of workspace/channel from a human chat name.

    This is heuristic and intentionally conservative. If parsing fails,
    return ("", "").

    Examples:
      "My Server / #hermes" -> ("My Server", "#hermes")
      "My Server / #hermes / thread-name" -> ("My Server", "#hermes")

    For platforms without workspace notions, this may return empty.
    """
    if not chat_name:
        return "", ""

    # Discord typically: "Guild / #channel" or "Guild / #thread".
    parts = [p.strip() for p in _WS_SPLIT_RE.split(chat_name) if p.strip()]
    if len(parts) >= 2 and parts[1].startswith("#"):
        return parts[0], parts[1]

    return "", ""


def _dedupe_preserve_order(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for it in items:
        if not it:
            continue
        if it in seen:
            continue
        seen.add(it)
        out.append(it)
    return out


def _fts_fallback_queries(raw: str) -> List[str]:
    """Generate safer fallback FTS5 queries.

    SQLite FTS5 has its own query syntax. Some user-provided strings (unbalanced
    quotes, punctuation-heavy tokens, etc.) can cause MATCH parse errors.

    This function returns a short list of alternatives so search can succeed
    without requiring users to manually sanitize/escape their query.
    """
    q = (raw or "").strip()
    if not q:
        return []

    cands: List[str] = []

    # 1) Treat the entire input as a phrase. Escape embedded quotes by doubling.
    escaped = q.replace('"', '""')
    cands.append(f'"{escaped}"')

    # 2) Tokenize aggressively and AND remaining tokens.
    stripped = re.sub(r"[^\w\s]", " ", q, flags=re.UNICODE)
    tokens = [t for t in re.findall(r"\w+", stripped, flags=re.UNICODE) if t]
    if tokens:
        cands.append(" AND ".join(tokens[:16]))
        cands.append(" ".join(tokens[:16]))
        if len(tokens[0]) >= 3:
            cands.append(tokens[0] + "*")

    # Drop duplicates and the raw query itself.
    return _dedupe_preserve_order([c for c in cands if c and c != q])


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        check_same_thread=False,
        isolation_level=None,  # autocommit
    )
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
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

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  turn_index INTEGER NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'turn',
  created_at INTEGER NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_messages_session_turn ON messages(session_id, turn_index);

CREATE TABLE IF NOT EXISTS summaries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  start_turn INTEGER NOT NULL,
  end_turn INTEGER NOT NULL,
  depth INTEGER NOT NULL DEFAULT 0,
  summary_text TEXT NOT NULL,
  model TEXT,
  created_at INTEGER NOT NULL,
  source_hash TEXT,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_summaries_session_range ON summaries(session_id, start_turn, end_turn, depth);

-- Full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
  content,
  session_id UNINDEXED,
  turn_index UNINDEXED,
  role UNINDEXED,
  kind UNINDEXED,
  content='messages',
  content_rowid='id'
);

CREATE VIRTUAL TABLE IF NOT EXISTS summaries_fts USING fts5(
  summary_text,
  session_id UNINDEXED,
  start_turn UNINDEXED,
  end_turn UNINDEXED,
  depth UNINDEXED,
  content='summaries',
  content_rowid='id'
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
  INSERT INTO messages_fts(rowid, content, session_id, turn_index, role, kind)
  VALUES (new.id, new.content, new.session_id, new.turn_index, new.role, new.kind);
END;
CREATE TRIGGER IF NOT EXISTS summaries_ai AFTER INSERT ON summaries BEGIN
  INSERT INTO summaries_fts(rowid, summary_text, session_id, start_turn, end_turn, depth)
  VALUES (new.id, new.summary_text, new.session_id, new.start_turn, new.end_turn, new.depth);
END;
"""


class VaultDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = _connect(db_path)
        self._lock = threading.RLock()
        self._conn.executescript(SCHEMA_SQL)

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def upsert_session(self, session_id: str, origin: OriginScope) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_id, platform, chat_id, thread_id, chat_type, chat_name, user_id,
                                     workspace_name, channel_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  platform=excluded.platform,
                  chat_id=excluded.chat_id,
                  thread_id=excluded.thread_id,
                  chat_type=excluded.chat_type,
                  chat_name=excluded.chat_name,
                  user_id=excluded.user_id,
                  workspace_name=excluded.workspace_name,
                  channel_name=excluded.channel_name,
                  updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    origin.platform,
                    origin.chat_id,
                    origin.thread_id,
                    origin.chat_type,
                    origin.chat_name,
                    origin.user_id,
                    origin.workspace_name,
                    origin.channel_name,
                    now,
                    now,
                ),
            )

    def append_message(self, session_id: str, turn_index: int, role: str, content: str, *, kind: str = "turn") -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(session_id, turn_index, role, content, kind, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (session_id, int(turn_index), role, content, kind, now),
            )
            return int(cur.lastrowid)

    def last_turn_index(self, session_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(turn_index) FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            return int(row[0] or 0)

    def get_messages_range(self, session_id: str, start_turn: int, end_turn: int) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT turn_index, role, content, kind, created_at
                FROM messages
                WHERE session_id=? AND turn_index BETWEEN ? AND ?
                ORDER BY turn_index ASC, id ASC
                """,
                (session_id, int(start_turn), int(end_turn)),
            ).fetchall()
        out = []
        for t, role, content, kind, ts in rows:
            out.append({
                "turn_index": int(t),
                "role": role,
                "content": content,
                "kind": kind,
                "created_at": int(ts),
            })
        return out

    def insert_summary(
        self,
        session_id: str,
        start_turn: int,
        end_turn: int,
        summary_text: str,
        *,
        depth: int = 0,
        model: str = "",
        source_hash: str = "",
    ) -> int:
        now = int(time.time())
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO summaries(session_id, start_turn, end_turn, depth, summary_text, model, created_at, source_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, int(start_turn), int(end_turn), int(depth), summary_text, model or None, now, source_hash or None),
            )
            return int(cur.lastrowid)

    def search(
        self,
        query: str,
        *,
        scope_chat_key: str = "",
        workspace_name: str = "",
        channel_name: str = "",
        limit: int = 8,
        include_summaries: bool = True,
        include_messages: bool = True,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """FTS search across messages + summaries.

        Filters:
          - If workspace_name+channel_name are provided, use them.
          - Else if scope_chat_key is provided, filter by platform/chat_id.
        """
        q = (query or "").strip()
        if not q:
            return {"summaries": [], "messages": []}
        limit = max(1, min(int(limit or 8), 25))

        session_filter_sql = ""
        params: List[Any] = []

        if workspace_name and channel_name:
            session_filter_sql = "WHERE workspace_name=? AND channel_name=?"
            params.extend([workspace_name, channel_name])
        elif scope_chat_key:
            # scope_chat_key is platform:chat_id
            if ":" in scope_chat_key:
                platform, chat_id = scope_chat_key.split(":", 1)
                session_filter_sql = "WHERE platform=? AND chat_id=?"
                params.extend([platform, chat_id])

        # Resolve allowed session_ids first (small set)
        with self._lock:
            if session_filter_sql:
                srows = self._conn.execute(
                    f"SELECT session_id FROM sessions {session_filter_sql}",
                    tuple(params),
                ).fetchall()
                allowed = {r[0] for r in srows}
            else:
                allowed = None

            out_summaries: List[Dict[str, Any]] = []
            out_messages: List[Dict[str, Any]] = []

            # FTS5 query parsing can fail depending on user input.
            # Retry with a small set of safer fallback queries so callers don't
            # have to manually escape/sanitize punctuation-heavy terms.
            candidates = [q] + _fts_fallback_queries(q)
            last_err: Optional[Exception] = None
            success = False

            for cq in candidates:
                try:
                    out_summaries = []
                    out_messages = []

                    if include_summaries:
                        rows = self._conn.execute(
                            """
                            SELECT s.id, s.session_id, s.start_turn, s.end_turn, s.depth,
                                   snippet(summaries_fts, 0, '[', ']', '…', 12) AS snip
                            FROM summaries_fts
                            JOIN summaries s ON s.id = summaries_fts.rowid
                            WHERE summaries_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (cq, limit),
                        ).fetchall()
                        for sid, sess, st, et, depth, snip in rows:
                            if allowed is not None and sess not in allowed:
                                continue
                            out_summaries.append({
                                "id": int(sid),
                                "session_id": sess,
                                "start_turn": int(st),
                                "end_turn": int(et),
                                "depth": int(depth),
                                "snippet": snip,
                            })

                    if include_messages:
                        rows = self._conn.execute(
                            """
                            SELECT m.id, m.session_id, m.turn_index, m.role,
                                   snippet(messages_fts, 0, '[', ']', '…', 12) AS snip
                            FROM messages_fts
                            JOIN messages m ON m.id = messages_fts.rowid
                            WHERE messages_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (cq, limit),
                        ).fetchall()
                        for mid, sess, turn_idx, role, snip in rows:
                            if allowed is not None and sess not in allowed:
                                continue
                            out_messages.append({
                                "id": int(mid),
                                "session_id": sess,
                                "turn_index": int(turn_idx),
                                "role": role,
                                "snippet": snip,
                            })

                    success = True
                    break
                except sqlite3.OperationalError as e:
                    # Common case: "fts5: syntax error near ..." from MATCH parsing.
                    last_err = e
                    continue

            if not success and last_err is not None:
                logger.debug("SessionVault FTS query failed (raw=%r): %s", q, last_err)
                out_summaries = []
                out_messages = []

        return {"summaries": out_summaries, "messages": out_messages}

    def get_session_meta(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT session_id, platform, chat_id, thread_id, chat_type, chat_name, user_id,
                       workspace_name, channel_name, created_at, updated_at
                FROM sessions WHERE session_id=?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        keys = [
            "session_id", "platform", "chat_id", "thread_id", "chat_type", "chat_name", "user_id",
            "workspace_name", "channel_name", "created_at", "updated_at"
        ]
        return {k: row[i] for i, k in enumerate(keys)}

    def list_sessions_by_scope(self, *, workspace_name: str = "", channel_name: str = "", scope_chat_key: str = "") -> List[str]:
        where = []
        params = []
        if workspace_name and channel_name:
            where.append("workspace_name=?")
            where.append("channel_name=?")
            params.extend([workspace_name, channel_name])
        elif scope_chat_key and ":" in scope_chat_key:
            p, cid = scope_chat_key.split(":", 1)
            where.append("platform=?")
            where.append("chat_id=?")
            params.extend([p, cid])
        sql = "SELECT session_id FROM sessions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [r[0] for r in rows]

    def doctor(self) -> Dict[str, Any]:
        with self._lock:
            # Basic integrity checks
            issues = []
            try:
                self._conn.execute("SELECT 1 FROM messages_fts LIMIT 1").fetchone()
            except Exception as e:
                issues.append({"type": "fts", "error": str(e)})

            try:
                self._conn.execute("PRAGMA quick_check").fetchone()
            except Exception as e:
                issues.append({"type": "sqlite", "error": str(e)})

            counts = {}
            try:
                counts["sessions"] = int(self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
                counts["messages"] = int(self._conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
                counts["summaries"] = int(self._conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0])
            except Exception:
                pass

        size = 0
        try:
            size = int(os.path.getsize(self.db_path))
        except Exception:
            pass

        return {
            "db_path": self.db_path,
            "db_size_bytes": size,
            "counts": counts,
            "issues": issues,
            "ok": len(issues) == 0,
        }


def load_origin_from_sessions_index(hermes_home: str, session_id: str) -> OriginScope:
    """Best-effort: map a runtime session_id -> origin metadata from gateway sessions index."""
    idx_path = Path(hermes_home) / "sessions" / "sessions.json"
    if not idx_path.exists():
        return OriginScope(platform="", chat_id="")
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
    except Exception:
        return OriginScope(platform="", chat_id="")

    # Find entry whose session_id matches
    for _k, entry in (data or {}).items():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("session_id") or "") != str(session_id):
            continue
        origin = entry.get("origin") or {}
        platform = str(origin.get("platform") or entry.get("platform") or "")
        chat_id = str(origin.get("chat_id") or "")
        thread_id = str(origin.get("thread_id") or "") if origin.get("thread_id") else ""
        chat_type = str(origin.get("chat_type") or entry.get("chat_type") or "")
        chat_name = str(origin.get("chat_name") or entry.get("display_name") or entry.get("display_name") or "")
        user_id = str(origin.get("user_id") or "")

        ws, ch = parse_workspace_channel(platform, chat_name)
        return OriginScope(
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            chat_type=chat_type,
            chat_name=chat_name,
            user_id=user_id,
            workspace_name=ws,
            channel_name=ch,
        )

    return OriginScope(platform="", chat_id="")
