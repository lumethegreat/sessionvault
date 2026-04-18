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
    parent_chat_id: str = ""
    parent_chat_name: str = ""
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
  parent_chat_id TEXT,
  parent_chat_name TEXT,
  user_id TEXT,
  workspace_name TEXT,
  channel_name TEXT,
  previous_session_id TEXT,
  split_from_session_id TEXT,
  split_reason TEXT,
  resumed_from_session_id TEXT,
  suspended_at INTEGER,
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

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json TEXT,
  created_at INTEGER NOT NULL,
  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
);
CREATE INDEX IF NOT EXISTS idx_events_session_time ON events(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at);

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
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        required_session_columns = {
            "parent_chat_id": "TEXT",
            "parent_chat_name": "TEXT",
            "previous_session_id": "TEXT",
            "split_from_session_id": "TEXT",
            "split_reason": "TEXT",
            "resumed_from_session_id": "TEXT",
            "suspended_at": "INTEGER",
        }
        with self._lock:
            existing = {row[1] for row in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
            for column, coltype in required_session_columns.items():
                if column not in existing:
                    self._conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {coltype}")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  session_id TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  payload_json TEXT,
                  created_at INTEGER NOT NULL,
                  FOREIGN KEY(session_id) REFERENCES sessions(session_id)
                )
                """
            )
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_parent_chat_updated ON sessions(platform, parent_chat_id, updated_at)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session_time ON events(session_id, created_at)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at)")

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def upsert_session(
        self,
        session_id: str,
        origin: OriginScope,
        *,
        previous_session_id: str = "",
        split_from_session_id: str = "",
        split_reason: str = "",
        resumed_from_session_id: str = "",
        suspended_at: Optional[int] = None,
    ) -> None:
        now = int(time.time())
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sessions(session_id, platform, chat_id, thread_id, chat_type, chat_name, parent_chat_id,
                                     parent_chat_name, user_id, workspace_name, channel_name, previous_session_id,
                                     split_from_session_id, split_reason, resumed_from_session_id, suspended_at,
                                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                  platform=excluded.platform,
                  chat_id=excluded.chat_id,
                  thread_id=excluded.thread_id,
                  chat_type=excluded.chat_type,
                  chat_name=excluded.chat_name,
                  parent_chat_id=excluded.parent_chat_id,
                  parent_chat_name=excluded.parent_chat_name,
                  user_id=excluded.user_id,
                  workspace_name=excluded.workspace_name,
                  channel_name=excluded.channel_name,
                  previous_session_id=CASE WHEN excluded.previous_session_id != '' THEN excluded.previous_session_id ELSE sessions.previous_session_id END,
                  split_from_session_id=CASE WHEN excluded.split_from_session_id != '' THEN excluded.split_from_session_id ELSE sessions.split_from_session_id END,
                  split_reason=CASE WHEN excluded.split_reason != '' THEN excluded.split_reason ELSE sessions.split_reason END,
                  resumed_from_session_id=CASE WHEN excluded.resumed_from_session_id != '' THEN excluded.resumed_from_session_id ELSE sessions.resumed_from_session_id END,
                  suspended_at=COALESCE(excluded.suspended_at, sessions.suspended_at),
                  updated_at=excluded.updated_at
                """,
                (
                    session_id,
                    origin.platform,
                    origin.chat_id,
                    origin.thread_id,
                    origin.chat_type,
                    origin.chat_name,
                    origin.parent_chat_id,
                    origin.parent_chat_name,
                    origin.user_id,
                    origin.workspace_name,
                    origin.channel_name,
                    previous_session_id,
                    split_from_session_id,
                    split_reason,
                    resumed_from_session_id,
                    suspended_at,
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

    def insert_event(
        self,
        session_id: str,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        created_at: Optional[int] = None,
    ) -> int:
        ts = int(created_at if created_at is not None else time.time())
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events(session_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?)",
                (session_id, event_type, payload_json, ts),
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

    def timeline(
        self,
        *,
        created_at_from: Optional[int] = None,
        created_at_to: Optional[int] = None,
        session_ids: Optional[List[str]] = None,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 25), 200))
        where = []
        params: List[Any] = []

        if created_at_from is not None:
            where.append("created_at >= ?")
            params.append(int(created_at_from))
        if created_at_to is not None:
            where.append("created_at <= ?")
            params.append(int(created_at_to))
        if session_ids:
            placeholders = ", ".join("?" for _ in session_ids)
            where.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)

        sql = "SELECT session_id, turn_index, role, content, kind, created_at FROM messages"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC, id ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        out = []
        for session_id, turn_index, role, content, kind, created_at in rows:
            out.append({
                "session_id": session_id,
                "turn_index": int(turn_index),
                "role": role,
                "content": content,
                "kind": kind,
                "created_at": int(created_at),
            })
        return out

    def recent_messages(
        self,
        *,
        session_ids: Optional[List[str]] = None,
        created_at_from: Optional[int] = None,
        created_at_to: Optional[int] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        where = []
        params: List[Any] = []

        if created_at_from is not None:
            where.append("created_at >= ?")
            params.append(int(created_at_from))
        if created_at_to is not None:
            where.append("created_at <= ?")
            params.append(int(created_at_to))
        if session_ids:
            placeholders = ", ".join("?" for _ in session_ids)
            where.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)

        sql = "SELECT session_id, turn_index, role, content, kind, created_at FROM messages"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        out = []
        for session_id, turn_index, role, content, kind, created_at in rows:
            out.append({
                "session_id": session_id,
                "turn_index": int(turn_index),
                "role": role,
                "content": content,
                "kind": kind,
                "created_at": int(created_at),
            })
        return out

    def get_events(
        self,
        *,
        session_ids: Optional[List[str]] = None,
        created_at_from: Optional[int] = None,
        created_at_to: Optional[int] = None,
        event_type: str = "",
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 25), 200))
        where = []
        params: List[Any] = []
        if created_at_from is not None:
            where.append("created_at >= ?")
            params.append(int(created_at_from))
        if created_at_to is not None:
            where.append("created_at <= ?")
            params.append(int(created_at_to))
        if session_ids:
            placeholders = ", ".join("?" for _ in session_ids)
            where.append(f"session_id IN ({placeholders})")
            params.extend(session_ids)
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)

        sql = "SELECT session_id, event_type, payload_json, created_at FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC, id ASC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()

        out = []
        for session_id, event_type_value, payload_json, created_at in rows:
            try:
                payload = json.loads(payload_json or "{}")
            except Exception:
                payload = {"raw": payload_json}
            out.append({
                "session_id": session_id,
                "event_type": event_type_value,
                "payload": payload,
                "created_at": int(created_at),
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
        kind: Optional[List[str]] = None,
        role: Optional[List[str]] = None,
        session_id: str = "",
        platform: str = "",
        chat_id: str = "",
        thread_id: str = "",
        parent_chat_id: str = "",
    ) -> Dict[str, List[Dict[str, Any]]]:
        """FTS search across messages + summaries.

        Filters:
          - If workspace_name+channel_name are provided, use them.
          - Else if scope_chat_key is provided, filter by platform/chat_id.
          - Structured filters can further constrain sessions/messages.
        """
        q = (query or "").strip()
        if not q:
            return {"summaries": [], "messages": []}
        limit = max(1, min(int(limit or 8), 25))

        session_where = []
        params: List[Any] = []
        kind_filter = {str(v).strip() for v in (kind or []) if str(v).strip()}
        role_filter = {str(v).strip() for v in (role or []) if str(v).strip()}

        if workspace_name and channel_name:
            session_where.extend(["workspace_name=?", "channel_name=?"])
            params.extend([workspace_name, channel_name])
        elif scope_chat_key and ":" in scope_chat_key:
            scoped_platform, scoped_chat_id = scope_chat_key.split(":", 1)
            session_where.extend(["platform=?", "chat_id=?"])
            params.extend([scoped_platform, scoped_chat_id])

        if session_id:
            session_where.append("session_id=?")
            params.append(session_id)
        if platform:
            session_where.append("platform=?")
            params.append(platform)
        if chat_id:
            session_where.append("chat_id=?")
            params.append(chat_id)
        if thread_id:
            session_where.append("thread_id=?")
            params.append(thread_id)
        if parent_chat_id:
            session_where.append("parent_chat_id=?")
            params.append(parent_chat_id)

        with self._lock:
            if session_where:
                sql = "SELECT session_id FROM sessions WHERE " + " AND ".join(session_where)
                srows = self._conn.execute(sql, tuple(params)).fetchall()
                allowed = {r[0] for r in srows}
            else:
                allowed = None

            out_summaries: List[Dict[str, Any]] = []
            out_messages: List[Dict[str, Any]] = []

            candidates = [q] + _fts_fallback_queries(q)
            last_err: Optional[Exception] = None
            success = False

            for cq in candidates:
                try:
                    out_summaries = []
                    out_messages = []

                    if include_summaries and not (kind_filter or role_filter):
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
                            SELECT m.id, m.session_id, m.turn_index, m.role, m.kind,
                                   snippet(messages_fts, 0, '[', ']', '…', 12) AS snip
                            FROM messages_fts
                            JOIN messages m ON m.id = messages_fts.rowid
                            WHERE messages_fts MATCH ?
                            ORDER BY rank
                            LIMIT ?
                            """,
                            (cq, limit * 4),
                        ).fetchall()
                        for mid, sess, turn_idx, msg_role, msg_kind, snip in rows:
                            if allowed is not None and sess not in allowed:
                                continue
                            if role_filter and msg_role not in role_filter:
                                continue
                            if kind_filter and msg_kind not in kind_filter:
                                continue
                            out_messages.append({
                                "id": int(mid),
                                "session_id": sess,
                                "turn_index": int(turn_idx),
                                "role": msg_role,
                                "kind": msg_kind,
                                "snippet": snip,
                            })
                            if len(out_messages) >= limit:
                                break

                    success = True
                    break
                except sqlite3.OperationalError as e:
                    last_err = e
                    continue

            if not success and last_err is not None:
                logger.debug("SessionVault FTS query failed (raw=%r): %s", q, last_err)
                out_summaries = []
                out_messages = []

        return {"summaries": out_summaries, "messages": out_messages}

    def infer_previous_session_id(self, origin: OriginScope, current_session_id: str) -> str:
        where = ["session_id != ?"]
        params: List[Any] = [current_session_id]
        if origin.platform:
            where.append("platform=?")
            params.append(origin.platform)
        if origin.chat_id:
            where.append("chat_id=?")
            params.append(origin.chat_id)
        if origin.thread_id:
            where.append("thread_id=?")
            params.append(origin.thread_id)
        sql = "SELECT session_id FROM sessions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(sql, tuple(params)).fetchone()
        return str(row[0]) if row and row[0] else ""

    def get_lineage(self, session_id: str) -> Dict[str, Any]:
        current = self.get_session_meta(session_id)
        if not current:
            return {}

        ancestors: List[Dict[str, Any]] = []
        seen = {session_id}
        cursor = current
        while cursor:
            next_session_id = ""
            relation = ""
            reason = cursor.get("split_reason") or ""
            if cursor.get("resumed_from_session_id"):
                next_session_id = str(cursor.get("resumed_from_session_id") or "")
                relation = "resumed_from"
            elif cursor.get("split_from_session_id"):
                next_session_id = str(cursor.get("split_from_session_id") or "")
                relation = "split_from"
            elif cursor.get("previous_session_id"):
                next_session_id = str(cursor.get("previous_session_id") or "")
                relation = "previous"
            if not next_session_id or next_session_id in seen:
                break
            parent = self.get_session_meta(next_session_id)
            if not parent:
                break
            ancestors.append({
                "session_id": next_session_id,
                "relation": relation,
                "reason": reason,
                "created_at": parent.get("created_at"),
                "updated_at": parent.get("updated_at"),
            })
            seen.add(next_session_id)
            cursor = parent

        descendants: List[Dict[str, Any]] = []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, previous_session_id, split_from_session_id, resumed_from_session_id, split_reason, created_at, updated_at
                FROM sessions
                WHERE previous_session_id=? OR split_from_session_id=? OR resumed_from_session_id=?
                ORDER BY created_at ASC, updated_at ASC
                """,
                (session_id, session_id, session_id),
            ).fetchall()
        for sid, prev_sid, split_sid, resumed_sid, split_reason, created_at, updated_at in rows:
            relation = "previous" if prev_sid == session_id else "split_from" if split_sid == session_id else "resumed_from"
            descendants.append({
                "session_id": sid,
                "relation": relation,
                "reason": split_reason or "",
                "created_at": created_at,
                "updated_at": updated_at,
            })

        return {
            "session_id": session_id,
            "session": current,
            "ancestors": ancestors,
            "descendants": descendants,
        }

    def get_session_meta(self, session_id: str) -> Dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT session_id, platform, chat_id, thread_id, chat_type, chat_name, parent_chat_id,
                       parent_chat_name, user_id, workspace_name, channel_name, previous_session_id,
                       split_from_session_id, split_reason, resumed_from_session_id, suspended_at,
                       created_at, updated_at
                FROM sessions WHERE session_id=?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return {}
        keys = [
            "session_id", "platform", "chat_id", "thread_id", "chat_type", "chat_name", "parent_chat_id",
            "parent_chat_name", "user_id", "workspace_name", "channel_name", "previous_session_id",
            "split_from_session_id", "split_reason", "resumed_from_session_id", "suspended_at", "created_at",
            "updated_at"
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
        parent_chat_id = str(origin.get("parent_chat_id") or "")
        parent_chat_name = str(origin.get("parent_chat_name") or "")
        user_id = str(origin.get("user_id") or "")

        ws, ch = parse_workspace_channel(platform, chat_name)
        return OriginScope(
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            chat_type=chat_type,
            chat_name=chat_name,
            parent_chat_id=parent_chat_id,
            parent_chat_name=parent_chat_name,
            user_id=user_id,
            workspace_name=ws,
            channel_name=ch,
        )

    return OriginScope(platform="", chat_id="")


def resolve_sessionvault_db_path(hermes_home: str) -> str:
    cfg_path = Path(hermes_home) / "sessionvault" / "config.json"
    default_path = Path(hermes_home) / "sessionvault" / "vault.db"
    if not cfg_path.exists():
        return str(default_path)
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return str(default_path)
    db_path = str(cfg.get("db_path") or "").strip()
    if not db_path:
        return str(default_path)
    return db_path.replace("$HERMES_HOME", str(hermes_home))


def record_gateway_event(hermes_home: str, session_id: str, event_type: str, payload: Optional[Dict[str, Any]] = None) -> bool:
    if not hermes_home or not session_id or not event_type:
        return False
    db = None
    try:
        db = VaultDB(resolve_sessionvault_db_path(hermes_home))
        db.insert_event(session_id, event_type, payload or {})
        return True
    except Exception:
        return False
    finally:
        try:
            if db:
                db.close()
        except Exception:
            pass
