"""Long-term memory: SQLite + FTS5 (BM25 with Porter stemming), optional Voyage embeddings.

* It learns on its own: after every few turns and at the end of a session a cheap model reads the
  conversation and adds, updates or expires facts (see learn.py). New facts are marked for review.
* Old and contradicted facts expire: time-bound facts carry an expiry date, contradicted ones are
  retired with a reason. Expired facts stay in the database for history but are never shown.
* Retrieval, not a fixed block: small stores are shown once per session; larger ones are searched
  per turn, with optional LLM query expansion and embeddings.
* One editable view: `oomi memory edit` opens everything it believes about you in your editor.
* Saving while untrusted web content is in context needs approval, so an injected page can't
  plant instructions for future sessions.
* General and personal: facts with an empty scope are shared by every bot; a bot's own facts carry its id
  and only it sees them. A Memory opened for a bot reads both and writes general facts unless told otherwise.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

STOPWORDS = set(
    """a an and are as at be but by can could did do does for from had has have how i i'm if in into
    is it its me my of on or our should so that the their them then there these they this to was we
    were what when where which who why will with would you your about any all also am been being
    just more most no not now only other over same some such than too very can't don't""".split()
)


def tokens(text: str) -> list:
    return [t for t in re.findall(r"[a-z0-9][a-z0-9_.+-]*[a-z0-9]|[a-z0-9]", text.lower()) if t not in STOPWORDS]


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


SECRET_RX = re.compile(
    r"(api[_ -]?key|password|passwd|passphrase|secret|token)\s*(is\s+|[:=])|sk-[A-Za-z0-9_-]{12,}|\b\d{13,19}\b"
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|\bAKIA[0-9A-Z]{16}\b|xox[abpr]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY", re.I)


def clean_tags(tags) -> str:
    out = set()
    for t in tags or ():
        if isinstance(t, str):
            t = re.sub(r"[^\w.\- ]", "", t).strip().lower()
            if t:
                out.add(t[:30])
    return ",".join(sorted(out))


@dataclass
class Fact:
    id: int
    text: str
    tags: str
    created: float
    hits: int
    score: float = 0.0
    source: str = "agent"
    reviewed: bool = True
    expires_at: Optional[float] = None
    scope: str = ""


Embedder = Callable[[list, str], Optional[list]]

EXPANSION_SYSTEM = (
    "Output 8-15 search keywords, synonyms and related terms someone might have used when writing notes "
    "relevant to the user's message (people, places, tools, topics). Words only, space-separated, nothing else."
)


def make_llm_expander(complete: Callable[..., Optional[dict]]) -> Callable[[str], str]:
    """`complete(system=, prompt=, max_tokens=)` returns a response dict or None."""

    def expand(query: str) -> str:
        r = complete(system=EXPANSION_SYSTEM, prompt=query[:2000], max_tokens=300)
        if not r:
            return ""
        return "".join(b.get("text", "") for b in r.get("content", []) if b.get("type") == "text")[:600]

    return expand


def http_module():
    """The HTTP client the Anthropic SDK ships with (httpx2 in SDK 1.x, httpx before)."""
    try:
        import httpx2 as h
    except ImportError:  # pragma: no cover
        import httpx as h
    return h


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, endpoint: str):
        self.api_key, self.model, self.endpoint = api_key, model, endpoint
        self.failed = False

    def __call__(self, texts: list, input_type: str) -> Optional[list]:
        if self.failed or not texts:
            return None
        try:
            r = http_module().post(
                self.endpoint,
                json={"input": texts, "model": self.model, "input_type": input_type},
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=20,
            )
            r.raise_for_status()
            data = sorted(r.json()["data"], key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except Exception:
            self.failed = True  # don't retry on every call in this session
            return None


EDIT_HEADER = """# Everything oomi remembers about you. Edit freely, then save and close.
#   • change a line's text to correct it      • delete a line to forget it
#   • add a new line (without a #number) to teach it something, optionally starting with [tags]
#   • tag preferences with [preference] so they're always taken into account
# Format of existing lines: #<id> [tags] fact text"""


class Memory:
    PINNED_TAGS = ("preference", "profile")

    def __init__(self, path: Path | str, embedder: Optional[Embedder] = None, scope: str = "",
                 personal_only: bool = False, everyone: bool = False):
        """scope: the bot whose personal facts are visible besides the general ones ("" = general only).
        personal_only: show just that bot's own facts (its settings page), and save new ones as personal.
        everyone: read every bot's facts as well as the general ones (the overview in Settings); each Fact's
        `scope` says whose it is. Write through a Memory opened for the owner, not this one."""
        if not re.fullmatch(r"[A-Za-z0-9_-]{0,64}", scope):
            raise ValueError("bad memory scope")
        self.db = sqlite3.connect(str(path), check_same_thread=False)  # one user at a time; may be a worker thread
        self.db.row_factory = sqlite3.Row
        self.embedder = embedder
        self.scope, self.personal_only = scope, personal_only and bool(scope)
        self.everyone = everyone
        self.fts = self._init_schema()

    # -- schema ---------------------------------------------------------------------------------
    def _init_schema(self) -> bool:
        c = self.db
        c.execute(
            """CREATE TABLE IF NOT EXISTS facts(
                id INTEGER PRIMARY KEY, text TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'agent', created REAL, updated REAL,
                hits INTEGER NOT NULL DEFAULT 0, embedding TEXT)"""
        )
        cols = {r["name"] for r in c.execute("PRAGMA table_info(facts)")}
        for name, ddl in (("reviewed", "INTEGER NOT NULL DEFAULT 1"), ("expired", "REAL"),
                          ("expire_reason", "TEXT"), ("expires_at", "REAL"), ("scope", "TEXT NOT NULL DEFAULT ''")):
            if name not in cols:
                c.execute(f"ALTER TABLE facts ADD COLUMN {name} {ddl}")
        try:
            c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, tags, content='facts', "
                "content_rowid='id', tokenize='porter unicode61')"
            )
            c.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
                  INSERT INTO facts_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
                CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
                  INSERT INTO facts_fts(facts_fts, rowid, text, tags) VALUES('delete', old.id, old.text, old.tags); END;
                CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE OF text, tags ON facts BEGIN
                  INSERT INTO facts_fts(facts_fts, rowid, text, tags) VALUES('delete', old.id, old.text, old.tags);
                  INSERT INTO facts_fts(rowid, text, tags) VALUES (new.id, new.text, new.tags); END;
                """
            )
            fts = True
        except sqlite3.OperationalError:
            fts = False
        c.commit()
        return fts

    def _visible(self, alias: str = "f") -> str:
        # scope is checked against a strict pattern in __init__, so it is safe to inline
        if self.everyone:
            return "1=1"
        if self.personal_only:
            return f"{alias}.scope = '{self.scope}'"
        return f"{alias}.scope IN ('', '{self.scope}')" if self.scope else f"{alias}.scope = ''"

    @staticmethod
    def _live(alias: str = "f") -> str:
        return f"({alias}.expired IS NULL AND ({alias}.expires_at IS NULL OR {alias}.expires_at > {time.time():.0f}))"

    def _active(self, alias: str = "f") -> str:
        return f"({self._live(alias)} AND {self._visible(alias)})"

    # -- writes ---------------------------------------------------------------------------------
    def add(self, text: str, tags=(), source: str = "agent", replaces: Optional[int] = None,
            expires_at: Optional[float] = None, reviewed: Optional[bool] = None,
            personal: Optional[bool] = None) -> tuple:
        """personal: keep it to this Memory's bot (default: only when opened personal_only)."""
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty memory")
        if len(text) > 1000:
            raise ValueError("Memories should be one short fact (under 1000 characters). Split it up.")
        if SECRET_RX.search(text):
            raise ValueError("That looks like it contains a secret (key, password, token or card number); not saved.")
        tag_str = clean_tags(tags)
        now = time.time()
        rev = int(source not in ("auto", "compaction")) if reviewed is None else int(reviewed)
        scope = self.scope if (self.personal_only if personal is None else personal) else ""
        emb = self._embed_doc(text)

        if replaces is not None:
            old = self.get(replaces)
            if not old:
                raise ValueError(f"memory #{replaces} does not exist")
            merged = tag_str or old.tags
            self.db.execute(
                "UPDATE facts SET text=?, tags=?, updated=?, embedding=?, reviewed=?, expires_at=? WHERE id=?",
                (text, merged, now, emb, rev, expires_at, replaces),
            )
            self.db.commit()
            return replaces, "replaced"

        dup = self._near_duplicate(text)
        if dup is not None and dup.reviewed and source != "user":
            return dup.id, "exists"  # never quietly rewrite something the user has confirmed
        if dup is not None:
            merged_tags = ",".join(sorted(set(filter(None, (dup.tags + "," + tag_str).split(",")))))
            self.db.execute(
                "UPDATE facts SET text=?, tags=?, updated=?, embedding=COALESCE(?, embedding) WHERE id=?",
                (text, merged_tags, now, emb, dup.id),
            )
            self.db.commit()
            return dup.id, "merged"

        cur = self.db.execute(
            "INSERT INTO facts(text, tags, source, created, updated, embedding, reviewed, expires_at, scope) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (text, tag_str, source, now, now, emb, rev, expires_at, scope),
        )
        self.db.commit()
        return cur.lastrowid, "saved"

    def forget(self, fact_id: int) -> bool:
        cur = self.db.execute(f"DELETE FROM facts WHERE id=? AND {self._visible('facts')}", (fact_id,))
        self.db.commit()
        return cur.rowcount > 0

    def expire(self, fact_id: int, reason: str = "") -> bool:
        """Retire a fact that's no longer true. It stays in history but is never shown again."""
        cur = self.db.execute(f"UPDATE facts SET expired=?, expire_reason=? WHERE id=? AND expired IS NULL "
                              f"AND {self._visible('facts')}",
                              (time.time(), reason[:300], fact_id))
        self.db.commit()
        return cur.rowcount > 0

    def share(self, fact_id: int) -> bool:
        """Make one of this bot's personal facts general, so every bot sees it."""
        if not self.scope:
            return False
        cur = self.db.execute("UPDATE facts SET scope='' WHERE id=? AND scope=?", (fact_id, self.scope))
        self.db.commit()
        return cur.rowcount > 0

    def drop_scope(self) -> int:
        """Delete every personal fact of this Memory's bot (when the bot is deleted)."""
        if not self.scope:
            return 0
        cur = self.db.execute("DELETE FROM facts WHERE scope=?", (self.scope,))
        self.db.commit()
        return cur.rowcount

    def mark_reviewed(self, ids=None) -> None:
        if ids is None:
            self.db.execute(f"UPDATE facts SET reviewed=1 WHERE reviewed=0 AND {self._visible('facts')}")
        else:
            self.db.executemany(f"UPDATE facts SET reviewed=1 WHERE id=? AND {self._visible('facts')}", [(i,) for i in ids])
        self.db.commit()

    def _near_duplicate(self, text: str) -> Optional[Fact]:
        for f in self._lexical(text, 5):
            if jaccard(text, f.text) >= 0.7:
                return f
        return None

    def _embed_doc(self, text: str) -> Optional[str]:
        if not self.embedder:
            return None
        vecs = self.embedder([text], "document")
        return json.dumps(vecs[0]) if vecs else None

    # -- reads ----------------------------------------------------------------------------------
    def get(self, fact_id: int) -> Optional[Fact]:
        r = self.db.execute(f"SELECT * FROM facts f WHERE id=? AND {self._active()}", (fact_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, limit: int = 100) -> list:
        rows = self.db.execute(f"SELECT * FROM facts f WHERE {self._active()} ORDER BY updated DESC LIMIT ?",
                               (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def history(self, limit: int = 100) -> list:
        """Expired facts, newest first: (fact, reason)."""
        rows = self.db.execute(
            f"SELECT * FROM facts f WHERE NOT {self._live()} AND {self._visible()} ORDER BY COALESCE(expired, expires_at) DESC LIMIT ?",
            (limit,)).fetchall()
        return [(self._row(r), r["expire_reason"] or "date passed") for r in rows]

    def unreviewed(self) -> list:
        rows = self.db.execute(f"SELECT * FROM facts f WHERE reviewed=0 AND {self._active()} ORDER BY created",
                               ()).fetchall()
        return [self._row(r) for r in rows]

    def count(self) -> int:
        return self.db.execute(f"SELECT COUNT(*) FROM facts f WHERE {self._active()}").fetchone()[0]

    def total_chars(self) -> int:
        return self.db.execute(f"SELECT COALESCE(SUM(LENGTH(text)), 0) FROM facts f WHERE {self._active()}").fetchone()[0]

    def search(self, query: str, k: int = 5, touch: bool = True, semantic: bool = True,
               expand: Optional[Callable[[str], str]] = None) -> list:
        lex_query = query
        if expand:
            try:
                lex_query = f"{query} {expand(query) or ''}"
            except Exception:
                lex_query = query
        lexical = self._lexical(lex_query, k * 4)
        ranked = lexical
        if semantic and self.embedder:
            sem = self._semantic(query, k * 4)
            if sem:
                ranked = _rrf([lexical, sem])
        out = ranked[:k]
        if touch and out:
            self.db.executemany("UPDATE facts SET hits = hits + 1 WHERE id=?", [(f.id,) for f in out])
            self.db.commit()
        return out

    def _lexical(self, query: str, n: int) -> list:
        toks = list(dict.fromkeys(tokens(query)))[:40]
        if not toks:
            return []
        if self.fts:
            terms = []
            for t in toks:
                safe = t.replace('"', "")
                terms.append(f'"{safe}"*' if len(safe) >= 4 else f'"{safe}"')
            try:
                rows = self.db.execute(
                    "SELECT f.*, bm25(facts_fts, 1.0, 0.5) AS score FROM facts_fts "
                    f"JOIN facts f ON f.id = facts_fts.rowid WHERE facts_fts MATCH ? AND {self._active()} "
                    "ORDER BY score LIMIT ?",
                    (" OR ".join(terms), n),
                ).fetchall()
                return [self._row(r, -r["score"]) for r in rows]
            except sqlite3.OperationalError:
                pass
        # Fallback: token overlap with a rough IDF weight.
        rows = self.db.execute(f"SELECT * FROM facts f WHERE {self._active()}").fetchall()
        total = max(len(rows), 1)
        df: dict = {}
        docs = []
        for r in rows:
            ts = set(tokens(r["text"] + " " + r["tags"]))
            docs.append((r, ts))
            for t in ts:
                df[t] = df.get(t, 0) + 1
        scored = []
        q = set(toks)
        for r, ts in docs:
            s = sum(math.log(1 + total / df[t]) for t in q & ts)
            if s > 0:
                scored.append(self._row(r, s))
        scored.sort(key=lambda f: -f.score)
        return scored[:n]

    def _semantic(self, query: str, n: int) -> list:
        vecs = self.embedder([query], "query") if self.embedder else None
        if not vecs:
            return []
        q = vecs[0]
        rows = self.db.execute(f"SELECT * FROM facts f WHERE embedding IS NOT NULL AND {self._active()}").fetchall()
        scored = []
        for r in rows:
            v = json.loads(r["embedding"])
            dot = sum(a * b for a, b in zip(q, v))
            scored.append(self._row(r, dot))
        scored.sort(key=lambda f: -f.score)
        return scored[:n]

    def pinned(self, limit: int = 10) -> list:
        clauses = " OR ".join("(',' || tags || ',') LIKE ?" for _ in self.PINNED_TAGS)
        rows = self.db.execute(
            f"SELECT * FROM facts f WHERE ({clauses}) AND reviewed=1 AND {self._active()} ORDER BY updated DESC LIMIT ?",
            tuple(f"%,{t},%" for t in self.PINNED_TAGS) + (limit,),
        ).fetchall()
        return [self._row(r) for r in rows]

    def auto_context(self, query: str, k: int = 5, exclude=(), full_threshold_chars: int = 6000,
                     expander: Optional[Callable[[str], str]] = None) -> tuple:
        """Memory block for the start of a user turn. Returns (text, ids shown).

        * Small store (under `full_threshold_chars`): everything not yet shown this session, so recall is
          complete by construction and each fact costs tokens once per session, not once per turn.
        * Larger store: pinned preferences + the k most relevant facts not yet shown, with optional LLM
          query expansion to catch paraphrases that plain keyword search misses.
        """
        exclude = set(exclude)
        if self.total_chars() <= full_threshold_chars:
            facts = self.pinned(50) + self.list(500)
        else:
            pinned = self.pinned()
            hits = self.search(query, k + len(exclude) + len(pinned), expand=expander)
            pinned_ids = {f.id for f in pinned}
            facts = pinned + [f for f in hits if f.id not in pinned_ids and f.id not in exclude][:k]
        seen, fresh = set(), []
        for f in facts:
            if f.id not in exclude and f.id not in seen:
                seen.add(f.id)
                fresh.append(f)
        if not fresh:
            return "", []
        lines = [
            "<memory_context>",
            "Notes saved in earlier sessions (preferences first). Use them where they help; if one is wrong or "
            "outdated, fix it with remember(replaces=id) or forget(id).",
        ]
        for f in fresh:
            tag = f" [{f.tags}]" if f.tags else ""
            until = f" (until {time.strftime('%Y-%m-%d', time.localtime(f.expires_at))})" if f.expires_at else ""
            unconfirmed = " (learned automatically, not yet confirmed by the user)" if not f.reviewed else ""
            mine = " (yours only)" if f.scope else ""
            lines.append(f"- #{f.id}{tag} {f.text}{until}{unconfirmed}{mine}")
        lines.append("</memory_context>")
        return "\n".join(lines), [f.id for f in fresh]

    # -- one editable view ------------------------------------------------------------------------
    def export_text(self) -> tuple:
        """Returns (text, exported ids). Existing facts are written as `#<id> [<tags>] <text>`."""
        pinned_ids = {p.id for p in self.pinned(1000)}
        facts = sorted(self.list(100_000), key=lambda f: (f.id not in pinned_ids, f.id))
        lines = [EDIT_HEADER, ""] + [f"#{f.id} [{f.tags}] {f.text}" for f in facts]
        return "\n".join(lines) + "\n", [f.id for f in facts]

    def apply_text(self, text: str, exported_ids) -> dict:
        """Apply an edited export. Only facts that were exported can be changed or forgotten, so facts added
        elsewhere while the editor was open are left alone."""
        exported = set(exported_ids)
        current = {f.id: f for f in self.list(100_000)}
        seen, out = set(), {"updated": 0, "added": 0, "forgotten": 0, "skipped": []}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line == "#" or line.startswith("# "):
                continue
            m = re.match(r"^#(\d+)\s+\[([^\]]*)\]\s?(.*)$", line)
            if m and int(m.group(1)) in exported:
                fid, body = int(m.group(1)), " ".join(m.group(3).split())
                seen.add(fid)
                old = current.get(fid)
                if old is None:
                    continue  # deleted elsewhere meanwhile
                new_tags = clean_tags(m.group(2).split(","))
                if not body:
                    out["skipped"].append(line)
                elif SECRET_RX.search(body):
                    out["skipped"].append(line)
                elif body != old.text or new_tags != old.tags:
                    self.db.execute("UPDATE facts SET text=?, tags=?, updated=?, reviewed=1 WHERE id=?",
                                    (body, new_tags, time.time(), fid))
                    out["updated"] += 1
                continue
            m = re.match(r"^(?:\[([^\]]*)\]\s*)?(.+)$", line)
            if m:
                try:
                    self.add(m.group(2), (m.group(1) or "").split(","), source="user", personal=self.personal_only)
                    out["added"] += 1
                except ValueError:
                    out["skipped"].append(line)
        for fid in exported - seen:
            if fid in current:
                self.forget(fid)
                out["forgotten"] += 1
        self.db.commit()
        self.mark_reviewed([i for i in exported if i in current])
        return out

    @staticmethod
    def _row(r, score: float = 0.0) -> Fact:
        keys = r.keys()
        return Fact(id=r["id"], text=r["text"], tags=r["tags"], created=r["created"] or 0, hits=r["hits"], score=score,
                    source=r["source"], reviewed=bool(r["reviewed"]) if "reviewed" in keys else True,
                    expires_at=r["expires_at"] if "expires_at" in keys else None,
                    scope=r["scope"] if "scope" in keys else "")


def _rrf(rankings: list, k: int = 60) -> list:
    scores: dict = {}
    facts: dict = {}
    for ranking in rankings:
        for pos, f in enumerate(ranking):
            scores[f.id] = scores.get(f.id, 0.0) + 1.0 / (k + pos + 1)
            facts[f.id] = f
    order = sorted(scores, key=lambda i: -scores[i])
    out = []
    for i in order:
        f = facts[i]
        f.score = scores[i]
        out.append(f)
    return out
