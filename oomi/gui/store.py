"""What the app keeps on disk: the list of bots and each bot's timeline (what you see in the chat).

The conversation the model sees lives in the bot's oomi session (sessions/<id>.json). The timeline is the
human-facing record: your messages, replies, activity rows and notices, in the order they happened.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

AVATARS = 4  # poses of the ghost: facing you, mirrored, leaning, leaning mirrored


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


@dataclass
class BotRecord:
    id: str
    name: str
    folder: str
    avatar: int = 0
    color: str = ""  # one of GHOST_COLORS; empty: the app's color
    trust: str = "project"
    model: str = ""  # empty: the app default
    provider: str = ""  # the id of one of Config.providers; empty: Anthropic
    effort: str = ""
    budget: float = 0.0  # 0: the app default (dollars per day)
    session_id: str = ""
    sessions: list = field(default_factory=list)  # every conversation it has had (Start fresh begins a new one)
    pinned: bool = False
    section: str = ""  # a sidebar section you made ("" = none)
    order: float = 0.0  # position when you sort by hand
    instructions: str = ""  # standing instructions, added to its system prompt
    allow: list = field(default_factory=list)  # commands you chose "always allow for this bot" for
    private_folder: bool = False  # oomi created the folder for this bot
    created: float = field(default_factory=time.time)
    last_at: float = 0.0
    preview: str = ""
    unread: int = 0
    instructions: str = ""  # standing instructions for this bot, added to its system prompt (max 4,000 characters)
    apps_off: list = field(default_factory=list)  # connected apps this bot may not use

    @classmethod
    def from_dict(cls, d: dict) -> "BotRecord":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


class Registry:
    """~/.oomi/bots.json"""

    def __init__(self, home: Path):
        self.path = Path(home) / "bots.json"
        self.lock = threading.RLock()
        self.bots: dict[str, BotRecord] = {}
        self.load()

    def load(self) -> None:
        with self.lock:
            self.bots = {}
            try:
                data = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                return
            for d in data.get("bots", []) if isinstance(data, dict) else []:
                try:
                    b = BotRecord.from_dict(d)
                except TypeError:
                    continue
                for name in ("budget", "order", "created", "last_at"):  # NaN or infinity would break the window's JSON
                    v = getattr(b, name)
                    if not isinstance(v, (int, float)) or not math.isfinite(v):
                        setattr(b, name, 0.0)
                self.bots[b.id] = b

    def save(self) -> None:
        with self.lock:
            _atomic_write(self.path, json.dumps({"bots": [asdict(b) for b in self.bots.values()]}, indent=1))

    def add(self, b: BotRecord) -> BotRecord:
        with self.lock:
            self.bots[b.id] = b
            self.save()
        return b

    def remove(self, bot_id: str) -> None:
        with self.lock:
            self.bots.pop(bot_id, None)
            self.save()

    def ordered(self) -> list:
        with self.lock:
            return sorted(self.bots.values(), key=lambda b: -(b.last_at or b.created))


def slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.-]+", "", name).strip().strip(".")
    return s[:60] or "bot"


def private_folder_for(name: str, root: Optional[Path] = None) -> Path:
    """A folder of its own for a bot you didn't point at a project: ~/Oomi/<name> (or <name> 2, …)."""
    root = root or Path.home() / "Oomi"
    base = slug(name)
    p, n = root / base, 2
    while p.exists():
        p, n = root / f"{base} {n}", n + 1
    return p


class Timeline:
    """Append-only JSON lines. An item written again (same id) replaces the earlier version in place."""

    MAX_LOAD = 600

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.lines = None

    def write(self, item: dict) -> None:
        line = json.dumps(item, ensure_ascii=False)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
            if self.lines is not None:
                self.lines += 1

    def items(self, limit: int = MAX_LOAD) -> list:
        with self.lock:
            out: dict = {}
            n = 0
            try:
                with self.path.open(encoding="utf-8") as f:
                    for line in f:
                        n += 1
                        try:
                            item = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(item, dict) and item.get("id"):
                            if item.get("deleted"):
                                out.pop(item["id"], None)
                            else:
                                out[item["id"]] = item
            except OSError:
                return []
            self.lines = n
            items = list(out.values())
            if n > 2 * len(items) + 200:  # mostly superseded versions: rewrite compactly
                _atomic_write(self.path, "".join(json.dumps(i, ensure_ascii=False) + "\n" for i in items))
                self.lines = len(items)
            return items[-limit:]

    def get(self, item_id: str) -> Optional[dict]:
        for item in self.items(10_000):
            if item.get("id") == item_id:
                return item
        return None
