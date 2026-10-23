"""Antibodies: errors your team's agents have already fixed, with the fix that was proven to work.

Lifecycle
  1. A command fails. The watcher fingerprints the error and asks the store for a match.
     A match is attached to the tool result, so the model tries the proven fix first.
  2. With no match, the watcher remembers the failure and everything oomi changes afterwards.
  3. When the same check passes again, what changed in between becomes a *candidate* antibody:
     the error, a one-line description of the fix, the steps, and how long and how much it took.
  4. The person approves it. Only approved antibodies are ever matched, so an unproven or bad fix
     can't spread. Secrets, emails and home folders are redacted before anything is saved.
  5. When an antibody is offered and the check then passes, it's counted as a cure, with the time
     and money it saved. Antibodies that keep failing are retired.

Storage is MongoDB Atlas: `antibodies` (one document per proven fix) and `events` (everything that
happens, for the live outbreak map and the stream processor).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from . import atlas
from .fingerprint import command_base, command_key, extract_error, redact, signature
from .rerank import Reranker

RETIRE_AFTER_MISSES = 3
LOCAL_MATCH_THRESHOLD = 0.45  # token overlap, only used without Atlas Search
RERANK_THRESHOLD = 0.55


def now() -> datetime:
    return datetime.now(timezone.utc)


def _tokens(text: str) -> set:
    import re

    return {t for t in re.findall(r"[a-z0-9_.]+", (text or "").lower()) if len(t) > 1}


def overlap(a: str, b: str) -> float:
    sa, sb = _tokens(a), _tokens(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


@dataclass
class Match:
    id: ObjectId
    error_text: str
    fix: str
    steps: list
    author: str
    cures: int
    uses: int
    seconds_to_fix: float
    score: float
    how: str  # exact | hybrid | vector | local

    def note(self) -> str:
        """What the model sees, appended to the failing command's result."""
        steps = "".join(f"\n  - {s}" for s in self.steps[:6])
        record = f"cured it {self.cures} of {self.uses} times" if self.uses else "not used since it was proven"
        return (f"[antibody] Your team has already proved a fix for this error.\n"
                f"  Error it matched: {self.error_text}\n"
                f"  Fix: {self.fix}\n"
                f"  Steps that worked:{steps or ' (none recorded)'}\n"
                f"  Proven by {self.author}; {record}.\n"
                f"Try this fix first, then run the same check again.")


class Store:
    def __init__(self, db, team: str, user: str = "", device: str = "", use_atlas_search: bool = True,
                 reranker: Optional[Reranker] = None, searcher: Optional[atlas.Searcher] = None,
                 rerank_threshold: float = RERANK_THRESHOLD):
        self.db = db
        self.team, self.user, self.device = team, user, device
        self.antibodies = db["antibodies"]
        self.events = db["events"]
        self.use_atlas_search = use_atlas_search
        self.reranker = reranker
        self.searcher = searcher or atlas.Searcher()
        self.rerank_threshold = rerank_threshold

    # -- events ------------------------------------------------------------------------------------

    def log(self, kind: str, **fields) -> ObjectId:
        doc = {"ts": now(), "kind": kind, "team": self.team, "user": self.user, "device": self.device, **fields}
        return self.events.insert_one(doc).inserted_id

    # -- saving and reviewing ----------------------------------------------------------------------

    def save(self, candidate: dict, approved: bool) -> ObjectId:
        """Store a candidate. Approved ones are matched from now on; pending ones wait for /review."""
        doc = {**candidate, "team": self.team, "author": self.user, "device": self.device,
               "status": "approved" if approved else "pending", "created_at": now(),
               "approved_at": now() if approved else None, "uses": 0, "cures": 0, "misses": 0,
               "saved_seconds": 0.0, "saved_usd": 0.0, "confirmations": 1}
        try:
            _id = self.antibodies.insert_one(doc).inserted_id
        except DuplicateKeyError:
            # The team already has an approved antibody for this exact error: count it as confirmed again.
            existing = self.antibodies.find_one_and_update(
                {"team": self.team, "signature": doc["signature"], "status": "approved"},
                {"$inc": {"confirmations": 1}})
            _id = existing["_id"]
        self.log("antibody_created" if approved else "antibody_pending", antibody_id=_id,
                 error_text=doc["error_text"], signature=doc["signature"])
        return _id

    def review(self, antibody_id, approve: bool) -> bool:
        status = "approved" if approve else "rejected"
        try:
            res = self.antibodies.update_one(
                {"_id": ObjectId(str(antibody_id)), "team": self.team, "status": "pending"},
                {"$set": {"status": status, "approved_at": now() if approve else None}})
        except DuplicateKeyError:
            self.antibodies.update_one({"_id": ObjectId(str(antibody_id))}, {"$set": {"status": "rejected"}})
            return False
        if res.modified_count:
            self.log(f"antibody_{status}", antibody_id=ObjectId(str(antibody_id)))
        return bool(res.modified_count)

    def list(self, status: Optional[str] = "approved", limit: int = 50) -> list:
        q = {"team": self.team}
        if status:
            q["status"] = status
        return list(self.antibodies.find(q, {"embedding": 0}).sort("created_at", -1).limit(limit))

    # -- matching ----------------------------------------------------------------------------------

    def _live(self) -> dict:
        """Approved, and not retired for failing too often."""
        return {"team": self.team, "status": "approved"}

    def candidates(self, error_text: str) -> tuple:
        """(docs best first, how they were found, whether Atlas already reranked them)."""
        if self.use_atlas_search:
            found = self.searcher.candidates(self.antibodies, self.team, error_text)
            return found.docs, found.how, found.reranked
        return self._local_candidates(error_text), "local", False

    def match(self, error_text: str, command: str = "") -> Optional[Match]:
        sig = signature(error_text)
        doc = self.antibodies.find_one({**self._live(), "signature": sig})
        if doc:
            return self._to_match(doc, 1.0, "exact")
        docs, how, reranked = self.candidates(error_text)
        if not docs:
            return None
        if reranked:
            best = docs[0]
            score = float(best.get("_rerank", 0.0))
            return self._to_match(best, score, how) if score >= self.rerank_threshold else None
        if self.reranker is not None and self.reranker.available:
            ranked = self.reranker.rank(error_text, [d["error_text"] for d in docs])
            if ranked:
                i, score = ranked[0]
                return self._to_match(docs[i], score, how + "+rerank") if score >= self.rerank_threshold else None
        best = docs[0]
        # Without a reranker, only accept the top result if it shares real words with the error;
        # vector search always returns *something*, and a wrong cure is worse than none.
        score = overlap(error_text, best["error_text"])
        threshold = LOCAL_MATCH_THRESHOLD if how == "local" else 0.25
        return self._to_match(best, score, how) if score >= threshold else None

    def _local_candidates(self, error_text: str) -> list:
        docs = list(self.antibodies.find(self._live()).limit(1000))
        docs.sort(key=lambda d: overlap(error_text, d.get("error_text", "")), reverse=True)
        return docs[:8]

    @staticmethod
    def _to_match(doc: dict, score: float, how: str) -> Match:
        return Match(id=doc["_id"], error_text=doc["error_text"], fix=doc.get("fix", ""),
                     steps=doc.get("steps", []), author=doc.get("author", "?"), cures=doc.get("cures", 0),
                     uses=doc.get("uses", 0), seconds_to_fix=doc.get("seconds_to_fix", 0.0), score=score, how=how)

    # -- outcomes ----------------------------------------------------------------------------------

    def record_offer(self, m: Match, error_text: str) -> None:
        self.antibodies.update_one({"_id": m.id}, {"$inc": {"uses": 1}, "$set": {"last_used_at": now()}})
        self.log("antibody_offered", antibody_id=m.id, error_text=error_text, score=round(m.score, 3), how=m.how)

    def record_cure(self, antibody_id: ObjectId, seconds: float, usd: float) -> dict:
        doc = self.antibodies.find_one({"_id": antibody_id}) or {}
        saved_s = max(0.0, doc.get("seconds_to_fix", 0.0) - seconds)
        saved_usd = max(0.0, doc.get("usd_to_fix", 0.0) - usd)
        self.antibodies.update_one({"_id": antibody_id}, {"$inc": {
            "cures": 1, "saved_seconds": saved_s, "saved_usd": saved_usd}})
        self.log("antibody_cured", antibody_id=antibody_id, seconds=round(seconds, 1), usd=round(usd, 4),
                 saved_seconds=round(saved_s, 1), saved_usd=round(saved_usd, 4))
        return {"saved_seconds": saved_s, "saved_usd": saved_usd, "seconds": seconds}

    def record_miss(self, antibody_id: ObjectId) -> None:
        doc = self.antibodies.find_one_and_update({"_id": antibody_id}, {"$inc": {"misses": 1}}, return_document=True)
        self.log("antibody_missed", antibody_id=antibody_id)
        if doc and doc.get("misses", 0) >= RETIRE_AFTER_MISSES and doc["misses"] > doc.get("cures", 0):
            self.antibodies.update_one({"_id": antibody_id}, {"$set": {"status": "retired"}})
            self.log("antibody_retired", antibody_id=antibody_id)

    def stats(self) -> dict:
        rows = list(self.antibodies.aggregate([
            {"$match": {"team": self.team, "status": "approved"}},
            {"$group": {"_id": None, "antibodies": {"$sum": 1}, "cures": {"$sum": "$cures"},
                        "saved_seconds": {"$sum": "$saved_seconds"}, "saved_usd": {"$sum": "$saved_usd"}}},
        ]))
        return rows[0] if rows else {"antibodies": 0, "cures": 0, "saved_seconds": 0, "saved_usd": 0}


# -- the watcher: one per session ----------------------------------------------------------------

@dataclass
class Failure:
    command: str
    error_text: str
    signature: str
    started: float
    usd_at_start: float
    tokens_at_start: int
    steps: list = field(default_factory=list)
    changed: bool = False
    offered: Optional[Match] = None
    missed: bool = False


FIX_SYSTEM = ("You write one-line notes for a team's shared bug fixes. Given an error and what was changed until "
              "the check passed, state the fix in at most two short plain sentences: the actual cause and the "
              "change that fixed it. No preamble.")


class Watcher:
    def __init__(self, store: Store, project: Path, describe: Optional[Callable[[str, str], str]] = None,
                 notify: Callable[[str], None] = lambda msg: None):
        self.store = store
        self.project = project.name
        self.describe = describe  # (system, prompt) -> text; the small model
        self.notify = notify
        self.open: dict = {}  # command base -> Failure
        self.candidates: list = []  # waiting for the person's approval

    def observe(self, tool: str, args: dict, result, usage) -> Optional[str]:
        if tool in ("write_file", "edit_file") and not result.is_error and result.changed_path:
            step = f"edited {result.changed_path}"
            if result.extra.get("diff"):
                step += ":\n" + result.extra["diff"]
            self._add_step(step, changed=True)
            return None
        if tool not in ("run_command", "run_shell") or result.command is None:
            return None
        base = command_base(result.command)
        if result.exit_code == 0:
            failure = self.open.pop(base, None)
            self._add_step(f"ran `{command_key(result.command)}` (passed)", changed=True)
            if failure:
                self._closed(failure, result, usage)
            return None
        return self._failed(base, result, usage)

    def _add_step(self, step: str, changed: bool) -> None:
        for f in self.open.values():
            if len(f.steps) < 20:
                f.steps.append(redact(step)[:800])
            f.changed = f.changed or changed

    def _failed(self, base: str, result, usage) -> Optional[str]:
        error = extract_error(result.output)
        if not error:
            return None
        error = redact(error)
        sig = signature(error)
        prev = self.open.get(base)
        if prev and prev.signature == sig:
            if prev.offered and prev.changed and not prev.missed:
                prev.missed = True  # the cure was tried and the same error is still there
                self.store.record_miss(prev.offered.id)
            prev.changed = False
            return None
        self._add_step(f"ran `{command_key(result.command)}` (failed: {error[:200]})", changed=False)
        failure = Failure(command=redact(command_key(result.command)), error_text=error, signature=sig,
                          started=time.time(), usd_at_start=usage.cost,
                          tokens_at_start=usage.input_tokens + usage.output_tokens)
        self.open[base] = failure
        self.store.log("error", error_text=error, signature=sig, command=failure.command, project=self.project)
        match = self.store.match(error, result.command)
        if match is None:
            return None
        failure.offered = match
        self.store.record_offer(match, error)
        self.notify(f"antibody ▸ this error matches a fix proven by {match.author} ({match.how}, {match.score:.2f}): "
                    f"{match.fix}")
        return match.note()

    def _closed(self, f: Failure, result, usage) -> None:
        seconds = time.time() - f.started
        usd = usage.cost - f.usd_at_start
        if f.offered:
            saved = self.store.record_cure(f.offered.id, seconds, usd)
            msg = f"antibody ▸ cured in {seconds:.0f}s"
            if saved["saved_seconds"] >= 1:
                msg += f", about {saved['saved_seconds']:.0f}s and ${saved['saved_usd']:.2f} less than the first fix"
            self.notify(msg)
            return
        if not f.changed or not f.steps:
            return
        steps = f.steps
        prompt = f"Error: {f.error_text}\nCheck that failed then passed: {f.command}\nWhat changed:\n" + \
                 "\n".join(steps[-12:])
        fix = (self.describe(FIX_SYSTEM, prompt) if self.describe else "") or "; ".join(
            s.splitlines()[0] for s in steps[:4])
        self.candidates.append({
            "error_text": f.error_text, "signature": f.signature, "command": f.command,
            "fix": redact(fix)[:500], "steps": [s.splitlines()[0][:200] for s in steps[-8:]],
            "proven_by": {"command": redact(command_key(result.command)), "exit_code": 0},
            "seconds_to_fix": round(seconds, 1), "usd_to_fix": round(usd, 4),
            "tokens_to_fix": usage.input_tokens + usage.output_tokens - f.tokens_at_start,
            "project": self.project,
        })
        self.notify("antibody ▸ fixed an error; a new antibody is ready for your review")


# -- connecting: one store per process, from the environment or the Account settings --------------------

@dataclass
class TeamSettings:
    """Who you share antibodies with, and how they're matched. From environment variables (or a .env file in
    ~/.oomi or the folder you run oomi from); the connection string can also come from Settings, Account."""
    mongodb_uri: str = ""
    db_name: str = "oomi"
    team: str = "default"
    user: str = ""
    device: str = ""
    atlas_search: bool = True  # False: match in Python (local MongoDB, tests)
    embed_model: str = "voyage-4"
    rerank_model: str = "rerank-2.5"
    rerank_threshold: float = 0.5
    voyage_api_key: str = ""
    rerank_url: str = ""

    @classmethod
    def load(cls, home: Path, folder: Optional[Path] = None) -> "TeamSettings":
        import getpass
        import os
        import socket

        for env_file in ([Path(folder) / ".env"] if folder else []) + [Path(home) / ".env"]:
            _load_dotenv(env_file)
        env = os.environ.get
        s = cls()
        s.mongodb_uri = env("MONGODB_URI", "")
        if not s.mongodb_uri:  # the Atlas connection saved in Settings, Account
            from .keychain import get_secret

            s.mongodb_uri = get_secret("account:mongodb") or ""
        s.db_name = env("OOMI_DB", s.db_name)
        s.team = env("OOMI_TEAM", s.team)
        s.user = env("OOMI_USER", "") or getpass.getuser()
        s.device = env("OOMI_DEVICE", "") or socket.gethostname().split(".")[0]
        s.atlas_search = env("OOMI_ATLAS_SEARCH", "1") not in ("0", "false", "no")
        s.embed_model = env("OOMI_EMBED_MODEL", s.embed_model)
        s.rerank_model = env("OOMI_RERANK_MODEL", s.rerank_model)
        try:
            s.rerank_threshold = float(env("OOMI_RERANK_THRESHOLD", s.rerank_threshold))
        except ValueError:
            pass
        s.voyage_api_key = env("VOYAGE_API_KEY", "")
        s.rerank_url = env("OOMI_RERANK_URL", "")
        return s


def _load_dotenv(path: Path) -> None:
    """KEY=value lines and # comments. Real environment variables win."""
    import os

    try:
        lines = path.read_text().splitlines() if path.is_file() else []
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def open_store(s: TeamSettings, db=None) -> Store:
    """Connect (raises if Atlas can't be reached) and return the team's store. db: an existing database (tests)."""
    if db is None:
        if not s.mongodb_uri:
            raise RuntimeError("No MongoDB Atlas connection: set MONGODB_URI, or connect Atlas in Settings, Account.")
        db = atlas.connect(s.mongodb_uri, s.db_name)
    atlas.ensure_collections(db)
    return Store(db, team=s.team, user=s.user, device=s.device, use_atlas_search=s.atlas_search,
                 reranker=Reranker(s.voyage_api_key, s.rerank_model, s.rerank_url),
                 searcher=atlas.Searcher(s.rerank_model, s.embed_model), rerank_threshold=s.rerank_threshold)


def public(doc: dict) -> dict:
    """An antibody for the window: ids and dates as plain values."""
    out = {}
    for k, v in doc.items():
        if k == "embedding":
            continue
        if isinstance(v, ObjectId):
            v = str(v)
        elif isinstance(v, datetime):
            v = v.timestamp()
        out["id" if k == "_id" else k] = v
    return out
