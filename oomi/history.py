"""Searchable history of past conversations (all sessions, all projects), stored locally in SQLite.

Used two ways: the `search_history` tool, so the agent can look things up on demand instead of
carrying a fixed block in every prompt; and to notice when you keep asking for the same kind of task,
which is the cue to turn it into a skill.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .memory import jaccard, tokens


class History:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(str(path), check_same_thread=False)  # one user at a time; may be a worker thread
        self.db.row_factory = sqlite3.Row
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY, session TEXT, workspace TEXT, ts REAL, "
            "role TEXT, text TEXT)")
        try:
            self.db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(text, content='turns', "
                            "content_rowid='id', tokenize='porter unicode61')")
            self.db.execute("CREATE TRIGGER IF NOT EXISTS turns_ai AFTER INSERT ON turns BEGIN "
                            "INSERT INTO turns_fts(rowid, text) VALUES (new.id, new.text); END;")
            self.fts = True
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()

    def index(self, session: str, workspace: str, messages: list) -> int:
        rows = []
        now = time.time()
        for m in messages:
            content = m.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            for b in blocks:
                if b.get("type") != "text":
                    continue
                text = b.get("text", "").strip()
                if (not text or text.startswith(("<memory_context>", "<conversation_summary>", "<oomi_notes>", "[oomi]"))
                        or text in ("(interrupted)", "(interrupted by the user)", "(no response)", "(working)")):
                    continue
                rows.append((session, workspace, now, m.get("role", "?"), text[:4000]))
        if rows:
            self.db.executemany("INSERT INTO turns(session, workspace, ts, role, text) VALUES (?,?,?,?,?)", rows)
            self.db.commit()
        return len(rows)

    def search(self, query: str, k: int = 8, role: str = "", exclude_session: str = "") -> list:
        toks = list(dict.fromkeys(tokens(query)))[:24]
        if not toks:
            return []
        where = ["turns_fts MATCH ?"] if self.fts else []
        params: list = [" OR ".join(f'"{t}"' for t in toks)] if self.fts else []
        if role:
            where.append("t.role = ?")
            params.append(role)
        if exclude_session:
            where.append("t.session != ?")
            params.append(exclude_session)
        if self.fts:
            sql = ("SELECT t.*, bm25(turns_fts) AS score FROM turns_fts JOIN turns t ON t.id = turns_fts.rowid "
                   f"WHERE {' AND '.join(where)} ORDER BY score LIMIT ?")
            return [dict(r) for r in self.db.execute(sql, params + [k]).fetchall()]
        rows = [dict(r) for r in self.db.execute("SELECT * FROM turns t" + (" WHERE " + " AND ".join(where) if where else ""),
                                                 params).fetchall()]
        q = set(toks)
        rows = [r for r in rows if q & set(tokens(r["text"]))]
        rows.sort(key=lambda r: -len(q & set(tokens(r["text"]))))
        return rows[:k]

    def similar_requests(self, text: str, exclude_session: str, threshold: float = 0.5) -> list:
        """Earlier sessions where the user asked for something close to `text`: [(date, session, text)]."""
        if len(tokens(text)) < 3:
            return []
        seen, out = set(), []
        for r in self.search(text, 30, role="user", exclude_session=exclude_session):
            if r["session"] in seen or jaccard(text, r["text"]) < threshold:
                continue
            seen.add(r["session"])
            out.append((time.strftime("%d %b", time.localtime(r["ts"])), r["session"], r["text"]))
        return out
