"""The app's local server: the window talks to it over 127.0.0.1 with a per-launch token.

Requests are JSON; live updates (streamed text, tool activity, approvals) arrive over one Server-Sent Events stream.
"""

from __future__ import annotations

import base64
import html
import json
import math
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__
from ..config import GHOST_COLORS, Config, oomi_home
from ..health import Health
from ..keychain import KEY_RX, delete_secret, get_key, get_secret, store_key, store_secret
from ..account import AccountError, AccountStore
from ..cloud import DataSync, apply_pending_restore
from ..connectors import CATEGORIES, ConnectorStore, McpError
from ..memory import Memory
from ..schedule import ScheduleError, ScheduleStore, parse_when, schedule_json
from ..safety import TRUST_LEVELS
from ..skills import SkillStore, general_store
from .runtime import BotRuntime, NoKey, Scheduler
from .store import AVATARS, BotRecord, Registry, new_id, private_folder_for

STATIC = Path(__file__).parent / "static"
INSTRUCTIONS_MAX = 8000  # characters of a bot's standing instructions (the runtime keeps this many)
ASSETS = Path(__file__).parent.parent / "assets"
MAX_BODY = 40 * 1024 * 1024
IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
# The API takes images up to 5 MB and requests up to 32 MB, both measured after base64 (which adds a third).
IMAGE_MAX = 3_750_000
PDF_MAX = 20 * 1024 * 1024
TEXT_MAX = 300 * 1024
CONVERSATION_FILES_MAX = 22_000_000  # base64 bytes of images and PDFs a conversation may carry in total
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SOUNDS = ("Glass", "Ping", "Pop", "Purr", "Tink", "Hero", "Submarine", "Funk", "Bottle", "Frog", "Morse", "Blow",
          "Basso", "Sosumi")
UI_MAX = 1_000_000  # bytes of saved window state (layouts, sections, display preferences)
# display and sidebar preferences a window may save: name -> allowed values (a tuple), or the type
PREFS = {"tool_steps": ("simple", "detailed", "hidden"), "text_size": ("small", "normal", "large"),
         "density": ("comfortable", "compact"), "chat_width": ("narrow", "normal", "wide", "full"),
         "timestamps": ("separators", "every", "off"), "side_group": ("section", "status", "folder", "none"),
         "side_sort": ("recent", "name", "cost", "manual"), "reply_cost": bool, "dim_unfocused": bool,
         "sidebar_hidden": bool, "side_collapsed": list}


def _finite(value, lo: float, hi: float, message: str) -> float:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        raise ApiError(400, message)
    if not math.isfinite(v) or not lo <= v <= hi:
        raise ApiError(400, message)
    return v


def _clean_prefs(prefs) -> dict:
    """Only known preferences with sensible values; anything else is dropped."""
    out = {}
    for k, v in (prefs.items() if isinstance(prefs, dict) else []):
        rule = PREFS.get(k)
        if v is None and rule is not None:
            out[k] = None  # forget it
        elif isinstance(rule, tuple) and v in rule:
            out[k] = v
        elif rule is bool and isinstance(v, bool):
            out[k] = v
        elif rule is list and isinstance(v, list):
            out[k] = [str(x)[:120] for x in v if isinstance(x, str)][:100]
    return out


def _clean_ui(updates: dict) -> dict:
    out = {}
    for k, v in updates.items():
        k = str(k)[:80]
        if v is None or k not in ("prefs", "layouts", "mode", "layout", "focus", "simple_bot"):
            out[k] = v
        elif k == "prefs":
            out[k] = _clean_prefs(v)
        elif k == "layouts":
            if not isinstance(v, list):
                raise ApiError(400, "Saved layouts must be a list.")
            out[k] = [{"name": str(x.get("name"))[:40], "shape": x.get("shape")} for x in v[:20]
                      if isinstance(x, dict) and isinstance(x.get("shape"), dict) and x.get("name")]
        elif k == "mode":
            out[k] = v if v in ("simple", "advanced") else "simple"
        elif k == "layout":
            out[k] = v if isinstance(v, dict) else None
        else:
            out[k] = str(v)[:80] if isinstance(v, str) else None
    return out
CLIENT_TTL = 120  # seconds a window counts as present without checking in
MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1")


class ApiError(Exception):
    def __init__(self, status: int, message: str, **extra):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra


class Bus:
    """Numbered events kept in a ring buffer, so a reconnecting window picks up exactly where it left off."""

    def __init__(self, keep: int = 5000):
        self.cond = threading.Condition()
        self.seq = 0
        self.events: deque = deque(maxlen=keep)
        self.listeners: list = []

    def emit(self, ev: dict) -> None:
        with self.cond:
            self.seq += 1
            ev = dict(ev, seq=self.seq)
            self.events.append(ev)
            self.cond.notify_all()
        for cb in list(self.listeners):
            try:
                cb(ev)
            except Exception:
                pass

    def since(self, seq: int, timeout: float) -> Optional[list]:
        """Events after seq; [] on timeout; None if some were already dropped (the window must reload)."""
        with self.cond:
            if seq < self.seq and self.events and self.events[0]["seq"] > seq + 1:
                return None
            if self.seq <= seq:
                self.cond.wait(timeout)
            if self.events and self.events[0]["seq"] > seq + 1 and seq < self.seq:
                return None
            return [e for e in self.events if e["seq"] > seq]


def _read_config_file() -> dict:
    try:
        data = json.loads((oomi_home() / "config.json").read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def bot_color(c) -> bool:
    """A bot wears one of the ghost colors, or any custom one as #rrggbb (the window tints the ghost to it)."""
    return isinstance(c, str) and (c in GHOST_COLORS or bool(re.fullmatch(r"#[0-9a-fA-F]{6}", c)))


def _write_config_file(updates: dict) -> None:
    data = _read_config_file()  # keep anything added by hand
    data.update(updates)
    path = oomi_home() / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def check_key(key: str) -> tuple:
    """(ok, message). Listing models is free and tells us whether the key is accepted."""
    try:
        import anthropic

        anthropic.Anthropic(api_key=key, max_retries=1, timeout=15).models.list(limit=1)
        return True, "Key accepted."
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if name in ("AuthenticationError", "PermissionDeniedError"):
            return False, "Anthropic rejected this key. Check that you copied all of it."
        return True, "Saved. Couldn't reach Anthropic to check it just now; it will be tried on your first message."


FILE_MEMORY_MAX = 200  # memories taken from one file


def memory_chunks(text: str, limit: int = 900) -> list:
    """A text file as memories: its paragraphs, short ones joined up to `limit` characters, long ones split."""
    out, cur = [], ""
    for para in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        para = " ".join(para.split())
        while len(para) > limit:
            cut = para.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            out.append(para[:cut])
            para = para[cut:].lstrip()
        if not para:
            continue
        if cur and len(cur) + 1 + len(para) > limit:
            out.append(cur)
            cur = ""
        cur = f"{cur} {para}".strip()
    if cur:
        out.append(cur)
    return out


class App:
    def __init__(self, home: Optional[Path] = None, native=None, llm_factory=None,
                 key_checker: Callable[[str], tuple] = check_key, tick_seconds: float = 30.0, connect_team: bool = True):
        self.home = Path(home or oomi_home())
        for sub in ("skills", "spill", "sessions", "backups", "logs", "bots", "uploads"):
            (self.home / sub).mkdir(parents=True, exist_ok=True)
        try:
            apply_pending_restore(self.home)  # a backup downloaded last time goes in place before anything opens
        except OSError:
            traceback.print_exc()
        try:
            self.home.chmod(0o700)  # chats, memories and attachments: yours only, not other accounts on the Mac
        except OSError:
            pass
        self.token = secrets.token_urlsafe(32)
        self.bus = Bus()
        self.registry = Registry(self.home)
        self.native = native  # the window bridge (folder picker, notifications…) when running as the Mac app
        self.llm_factory = llm_factory
        self.key_checker = key_checker
        self.clients: dict = {}  # window id -> {"bots": set of visible bots, "focused", "active", "t"}
        self.lock = threading.RLock()
        self.closed = False
        self.awake = None  # `caffeinate` while a bot works (Mac)
        self.awake_lock = threading.Lock()
        try:
            self.keep_awake_pref = Config.load().keep_awake
        except SystemExit:  # an unreadable config file is reported elsewhere
            self.keep_awake_pref = True
        self.bus.listeners.append(self._on_event)
        self.runtimes: dict = {}
        self.connectors = ConnectorStore(self.home)
        self.account = AccountStore(self.home)
        self.data_sync = DataSync(self.home, self.account)
        self.backup_stop = threading.Event()
        threading.Thread(target=self._backup_loop, name="oomi-backup", daemon=True).start()
        # The team's antibodies on MongoDB Atlas: connected in the background so a slow cluster never holds up
        # the window; bots pick the store up on their next request.
        self.team = None
        self.team_settings = None
        self.team_error = ""
        if connect_team:
            threading.Thread(target=self.connect_team, daemon=True).start()
        self.no_browser = False  # tests: don't open sign-in pages
        for rec in self.registry.ordered():
            self._runtime(rec)
        self.started = time.time()
        self.schedules = ScheduleStore(self.home)
        # Starts last: its first pass runs anything missed while oomi was closed (by under 12 hours).
        self.scheduler = Scheduler(self.schedules, lambda: self.runtimes, self.bus.emit, tick_seconds)

    # -- bots ----------------------------------------------------------------------------------------------
    def _runtime(self, rec: BotRecord) -> BotRuntime:
        with self.lock:
            rt = self.runtimes.get(rec.id)
            if rt is None:
                rt = BotRuntime(rec, self.registry, self.home, self.bus.emit, self.attending, self.llm_factory,
                                events_lock=self.bus.cond, connectors=self.connectors, antibodies=lambda: self.team)
                self.runtimes[rec.id] = rt
            return rt

    def rt(self, bot_id: str) -> BotRuntime:
        rt = self.runtimes.get(bot_id)
        if rt is None:
            raise ApiError(404, "No such bot.")
        return rt

    def attending(self, bot_id: str) -> bool:
        """Is the bot on screen in a window you're using right now? (Then nothing is unread and nothing notifies.)"""
        now = time.time()
        with self.lock:
            return any(c["active"] and bot_id in c["bots"] and now - c["t"] < CLIENT_TTL for c in self.clients.values())

    def set_focus(self, body: dict) -> None:
        client = str(body.get("client") or "default")[:64]
        bots = body.get("bots") if isinstance(body.get("bots"), list) else [body.get("bot")]
        visible = {str(b) for b in bots if b}
        active = bool(body.get("active"))
        with self.lock:
            if body.get("gone"):
                self.clients.pop(client, None)
            else:
                self.clients[client] = {"bots": visible, "focused": body.get("focused") or body.get("bot"),
                                        "active": active, "t": time.time()}
            for k in [k for k, c in self.clients.items() if time.time() - c["t"] > CLIENT_TTL]:
                self.clients.pop(k, None)
        if active and not body.get("gone"):
            changed = False
            for bot_id in visible:
                rt = self.runtimes.get(bot_id)
                if rt is not None and rt.record.unread:
                    rt.record.unread = 0
                    changed = True
                    self.bus.emit({"type": "bot", "bot": bot_id, "bot_state": rt.state()})
            if changed:
                self.registry.save()
        self.update_badge()

    def update_badge(self) -> None:
        if self.native:
            self.native.set_badge(sum(b.unread for b in self.registry.bots.values()))

    def _on_event(self, ev: dict) -> None:
        if ev.get("type") == "bot":
            self._keep_awake()

    def _keep_awake(self) -> None:
        """While any bot is working (and the setting is on), keep the Mac from sleeping."""
        if sys.platform != "darwin":
            return
        busy = any(rt.status != "idle" for rt in list(self.runtimes.values()))
        with self.awake_lock:
            want = busy and self.keep_awake_pref and not self.closed
            running = self.awake is not None and self.awake.poll() is None
            if want and not running:
                try:
                    self.awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(os.getpid())],
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except OSError:
                    self.awake = None
            elif not want and running:
                self.awake.terminate()
                self.awake = None

    # -- saved window state ---------------------------------------------------------------------------------
    def ui(self) -> dict:
        try:
            data = json.loads((self.home / "ui.json").read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def save_ui(self, updates: dict) -> dict:
        """Merge into the saved window state. Preferences merge one by one, so windows don't undo each other."""
        updates = _clean_ui(updates if isinstance(updates, dict) else {})
        with self.lock:
            data = self.ui()
            for k, v in updates.items():
                if v is None:
                    data.pop(k, None)
                elif k == "prefs":
                    prefs = data.get("prefs") if isinstance(data.get("prefs"), dict) else {}
                    for pk, pv in v.items():
                        if pv is None:
                            prefs.pop(pk, None)
                        else:
                            prefs[pk] = pv
                    data["prefs"] = prefs
                else:
                    data[k] = v
            text = json.dumps(data)
            if len(text) > UI_MAX:
                raise ApiError(413, "Too much window state to save.")
            path = self.home / "ui.json"
            tmp = path.with_suffix(".tmp")
            tmp.write_text(text)
            os.replace(tmp, path)
        if any(k in updates for k in ("prefs", "layouts")):
            self.bus.emit({"type": "ui", "keys": sorted(updates)})
        return data

    def bot_states(self) -> list:
        return [self.runtimes[b.id].state() for b in self.registry.ordered() if b.id in self.runtimes]

    @staticmethod
    def check_instructions(value) -> str:
        if not isinstance(value, str):
            raise ApiError(400, "The instructions must be text.")
        value = value.strip()
        if len(value) > INSTRUCTIONS_MAX:
            raise ApiError(400, f"Instructions can be at most {INSTRUCTIONS_MAX:,} characters (yours are {len(value):,}).")
        return value

    def create_bot(self, body: dict) -> dict:
        name = " ".join(str(body.get("name") or "").split())[:40]
        if not name:
            raise ApiError(400, "Give the bot a name.")
        folder = str(body.get("folder") or "").strip()
        private = not folder
        if folder:
            p = Path(os.path.expanduser(folder))
            if not p.is_absolute():
                raise ApiError(400, "Use a full folder path, like ~/code/my-project.")
            if not p.is_dir():
                raise ApiError(400, f"There's no folder at {p}.")
            if p.resolve() == Path.home().resolve() or str(p.resolve()) == "/":
                raise ApiError(400, "Pick a project folder rather than your whole home folder.")
            path = p.resolve()
        else:
            path = private_folder_for(name)
            path.mkdir(parents=True, exist_ok=True)
        instructions = self.check_instructions(body["instructions"]) if "instructions" in body else ""
        trust = body.get("trust") if body.get("trust") in TRUST_LEVELS else Config.load().trust
        used = {b.avatar for b in self.registry.bots.values()}
        avatar = body.get("avatar")
        if not isinstance(avatar, int) or not 0 <= avatar < AVATARS:
            avatar = next((i for i in range(AVATARS) if i not in used), len(self.registry.bots) % AVATARS)
        color = body.get("color")
        if not bot_color(color):  # a color no other bot wears yet
            app_color = Config.load().ghost_color
            taken = {b.color or app_color for b in self.registry.bots.values()}
            color = next((c for c in GHOST_COLORS if c not in taken), GHOST_COLORS[len(self.registry.bots) % len(GHOST_COLORS)])
        rec = self.registry.add(BotRecord(id=new_id("b"), name=name, folder=str(path), avatar=avatar, color=color.lower(),
                                          trust=trust, private_folder=private, last_at=time.time(),
                                          instructions=instructions))
        rt = self._runtime(rec)
        self.bus.emit({"type": "bots"})
        return rt.state()

    def update_bot(self, bot_id: str, body: dict) -> dict:
        rt = self.rt(bot_id)
        rec = rt.record
        if "name" in body:
            name = " ".join(str(body["name"] or "").split())[:40]
            if not name:
                raise ApiError(400, "The name can't be empty.")
            rec.name = name
        if "avatar" in body and isinstance(body["avatar"], int) and 0 <= body["avatar"] < AVATARS:
            rec.avatar = body["avatar"]
        if "color" in body:
            if not bot_color(body["color"]):
                raise ApiError(400, "Pick one of the colors, or a custom one like #ff8800.")
            rec.color = body["color"].lower()
        if "trust" in body:
            if body["trust"] not in TRUST_LEVELS:
                raise ApiError(400, "Unknown trust level.")
            rt.set_trust(body["trust"])
        if "apps_off" in body:
            off = body["apps_off"]
            if not isinstance(off, list) or not all(isinstance(x, str) and len(x) < 40 for x in off) or len(off) > 200:
                raise ApiError(400, "Bad list of apps.")
            rec.apps_off = sorted(set(off))
        if "model" in body:
            if body["model"] and body["model"] not in MODELS:
                raise ApiError(400, "Unknown model.")
            rec.model = body["model"] or ""
            if rt.agent is not None:
                rt.agent.cfg.model = rec.model or Config.load().model
        if "provider" in body:
            if body["provider"] and not any(p["id"] == body["provider"] for p in Config.load().providers):
                raise ApiError(400, "Unknown provider.")
            if (body["provider"] or "") != rec.provider:
                rec.provider = body["provider"] or ""
                rt.new_llm = True  # used from its next message
        if "effort" in body:
            if body["effort"] and body["effort"] not in EFFORTS:
                raise ApiError(400, "Unknown effort level.")
            rec.effort = body["effort"] or ""
            if rt.agent is not None:
                rt.agent.cfg.effort = rec.effort or Config.load().effort
        if "budget" in body:
            budget = _finite(body["budget"], 0, 1000, "Pick a budget between $0 and $1,000.")
            rec.budget = budget
            if rt.agent is not None:
                rt.agent.cost.limit_usd = budget or Config.load().max_session_cost_usd
                rt.agent.cost.warned = False
        if "pinned" in body:
            rec.pinned = bool(body["pinned"])
        if "section" in body:
            rec.section = " ".join(str(body["section"] or "").split())[:40]
        if "order" in body:
            rec.order = _finite(body["order"], -1e9, 1e9, "Bad order.")
        if "instructions" in body:
            rt.set_instructions(str(body["instructions"] or ""))
        if "allow" in body:  # only ever removes saved approvals; adding happens from an approval card
            keep = {f"shell:{c}" for c in body["allow"] if isinstance(c, str)} if isinstance(body["allow"], list) else set()
            rec.allow = [k for k in rec.allow if k in keep]
            if rt.agent is not None:
                rt.agent.session_allow -= {k for k in list(rt.agent.session_allow) if k not in keep}
        if "folder" in body and body["folder"] != rec.folder:
            if rt.status != "idle":
                raise ApiError(409, "Stop the bot before moving it to another folder.")
            p = Path(os.path.expanduser(str(body["folder"] or "")))
            if not p.is_absolute() or not p.is_dir():
                raise ApiError(400, "Choose an existing folder.")
            rec.folder, rec.private_folder = str(p.resolve()), False
            rt.submit("fresh").result(30)
        self.registry.save()
        self.bus.emit({"type": "bot", "bot": bot_id, "bot_state": rt.state()})
        return rt.state()

    def clone_bot(self, bot_id: str, body: dict) -> dict:
        """A new bot with the same settings and instructions (not the conversation)."""
        src = self.rt(bot_id).record
        name = " ".join(str(body.get("name") or f"{src.name} copy").split())[:40]
        folder = "" if src.private_folder or not Path(src.folder).is_dir() else src.folder
        st = self.create_bot({"name": name, "folder": folder, "trust": src.trust})
        rec = self.runtimes[st["id"]].record
        rec.model, rec.effort, rec.budget, rec.instructions = src.model, src.effort, src.budget, src.instructions
        rec.provider = src.provider
        rec.allow, rec.section = list(src.allow), src.section
        self.registry.save()
        self.bus.emit({"type": "bots"})
        return self.runtimes[rec.id].state()

    def export_bot(self, bot_id: str) -> dict:
        r = self.rt(bot_id).record
        return {"oomi_bot": 1, "name": r.name, "avatar": r.avatar, "trust": r.trust, "model": r.model,
                "effort": r.effort, "budget": r.budget, "instructions": r.instructions,
                "folder": "" if r.private_folder else r.folder}

    def import_bot(self, data: dict) -> dict:
        if not isinstance(data, dict) or data.get("oomi_bot") != 1:
            raise ApiError(400, "That isn't an oomi bot file.")
        folder = str(data.get("folder") or "")
        if folder and not Path(os.path.expanduser(folder)).is_dir():
            folder = ""  # not on this Mac: it gets a folder of its own
        trust = data.get("trust") if data.get("trust") in ("read-only", "project") else "project"  # never full from a file
        st = self.create_bot({"name": data.get("name") or "Imported bot", "folder": folder, "trust": trust})
        # Saved approvals are deliberately not imported: a file shouldn't be able to pre-approve commands.
        patch = {k: data[k] for k in ("avatar", "model", "effort", "budget", "instructions") if k in data}
        try:
            return self.update_bot(st["id"], patch)
        except ApiError:
            return st

    def context(self, bot_id: str) -> dict:
        """Roughly what fills the bot's conversation, by kind (estimated at 3.5 characters a token)."""
        rt = self.rt(bot_id)
        cfg = Config.load()
        a = rt.agent
        if a is not None:
            system, messages, tool_defs = a.system, a.messages, a.tool_defs
            actual, compact_at = a.last_context_tokens, a.cfg.compact_at_tokens
        else:
            from ..agent import load_session
            from ..tools import all_tools, server_tools

            sess = load_session(self.home, Path(rt.record.folder), rt.record.session_id) if rt.record.session_id else None
            system, messages = (sess or {}).get("system", ""), (sess or {}).get("messages", [])
            if not system:  # not started yet: what it will start with
                from ..agent import project_notes
                from ..prompts import build_system_prompt
                from ..sandbox import probe as sandbox_probe

                folder = Path(rt.record.folder)
                system = build_system_prompt(
                    str(folder), general_store(self.home).index_text(cfg.skills_index_limit),
                    bool(cfg.sandbox and sandbox_probe().get("available")),
                    instructions=rt.record.instructions,
                    project_notes=project_notes(folder) if cfg.read_project_files and folder.is_dir() else "")
            tool_defs = [t.definition() for t in all_tools()] + server_tools(cfg)
            actual, compact_at = (sess or {}).get("last_context_tokens", 0), cfg.compact_at_tokens
        parts = {"Instructions (system prompt)": len(system), "Tools": len(json.dumps(tool_defs)), "Your messages": 0,
                 "Replies": 0, "Tool calls and results": 0, "Memory and notes": 0, "Web results": 0, "Files": 0}
        files = 0
        for m in messages:
            content = m.get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
            for b in blocks:
                t = b.get("type")
                if t == "text":
                    text = b.get("text", "")
                    if text.startswith(("<memory_context>", "<oomi_notes>", "[oomi]", "<conversation_summary>")):
                        parts["Memory and notes"] += len(text)
                    elif text.startswith("<attachment"):
                        parts["Files"] += len(text)
                    else:
                        parts["Your messages" if m.get("role") == "user" else "Replies"] += len(text)
                elif t in ("tool_use", "tool_result", "server_tool_use"):
                    parts["Tool calls and results"] += len(json.dumps(b.get("input") or b.get("content") or ""))
                elif t in ("web_search_tool_result", "web_fetch_tool_result"):
                    parts["Web results"] += len(json.dumps(b.get("content") or ""))
                elif t in ("image", "document"):
                    files += 1
                elif t == "thinking":
                    parts["Replies"] += len(b.get("thinking", ""))
        est = {k: int(v / 3.5) for k, v in parts.items()}
        est["Files"] += files * 1600  # images and PDF pages cost roughly this much each
        return {"actual": actual, "compact_at": compact_at, "messages": len(messages),
                "parts": [{"name": k, "tokens": v} for k, v in est.items() if v]}

    def stop_all(self) -> int:
        n = 0
        for rt in list(self.runtimes.values()):
            if rt.status != "idle":
                rt.stop()
                n += 1
        return n

    def read_all(self) -> None:
        for rt in list(self.runtimes.values()):
            if rt.record.unread:
                rt.record.unread = 0
                self.bus.emit({"type": "bot", "bot": rt.record.id, "bot_state": rt.state()})
        self.registry.save()
        self.update_badge()

    def delete_bot(self, bot_id: str) -> None:
        rt = self.rt(bot_id)
        rt.deleted = True  # from now on it neither writes nor announces anything
        threading.Thread(target=rt.shutdown, daemon=True).start()
        with self.lock:
            self.runtimes.pop(bot_id, None)
        self.registry.remove(bot_id)
        self.schedules.remove_bot(bot_id)
        shutil.rmtree(self.home / "bots" / bot_id, ignore_errors=True)  # its chat history and skills, never its folder
        mem = Memory(self.home / "memory.db", scope=bot_id)
        try:
            mem.drop_scope()
        finally:
            mem.db.close()
        self.bus.emit({"type": "bots"})

    # -- attachments ---------------------------------------------------------------------------------------
    def upload(self, body: dict) -> dict:
        name = Path(str(body.get("name") or "file")).name[:120] or "file"
        try:
            data = base64.b64decode(str(body.get("data") or ""), validate=True)
        except ValueError:
            raise ApiError(400, "The file didn't arrive intact.")
        mtype = str(body.get("type") or "") or mimetypes.guess_type(name)[0] or ""
        if mtype in IMAGE_TYPES:
            kind = "image"
            if len(data) > IMAGE_MAX:
                raise ApiError(400, f"{name} is larger than 3.7 MB. Use a smaller image or a screenshot.")
        elif mtype == "application/pdf" or name.lower().endswith(".pdf"):
            kind, mtype = "pdf", "application/pdf"
            if len(data) > PDF_MAX:
                raise ApiError(400, f"{name} is larger than 20 MB, the most the API accepts in one message.")
        else:
            try:
                data.decode("utf-8")
            except UnicodeDecodeError:
                raise ApiError(400, f"oomi can read images, PDFs and text files. {name} is none of those; "
                                    f"put it in the bot's folder and ask about it there.")
            kind = "text"
            if len(data) > TEXT_MAX:
                raise ApiError(400, f"{name} is over 300 KB. Put it in the bot's folder and ask oomi to read it.")
        att_id = new_id("f")
        d = self.home / "uploads" / att_id
        d.mkdir(parents=True, mode=0o700)
        (d / "data").write_bytes(data)
        meta = {"id": att_id, "name": name, "kind": kind, "type": mtype, "size": len(data)}
        (d / "meta.json").write_text(json.dumps(meta))
        return meta

    def attachment(self, att_id: str) -> tuple:
        if not re.fullmatch(r"f[0-9a-f]{12}", att_id or ""):
            raise ApiError(404, "No such file.")
        d = self.home / "uploads" / att_id
        try:
            meta = json.loads((d / "meta.json").read_text())
            return meta, (d / "data").read_bytes()
        except (OSError, json.JSONDecodeError):
            raise ApiError(404, "That attachment is no longer available.")

    def blocks_for(self, att_ids: list) -> tuple:
        blocks, shown = [], []
        for att_id in att_ids[:20]:
            meta, data = self.attachment(str(att_id))
            name = meta["name"]
            if meta["kind"] == "image":
                block = {"type": "image", "source": {"type": "base64", "media_type": meta["type"],
                                                     "data": base64.b64encode(data).decode()}}
            elif meta["kind"] == "pdf":
                block = {"type": "document", "title": name[:200],
                         "source": {"type": "base64", "media_type": "application/pdf",
                                    "data": base64.b64encode(data).decode()}}
            else:
                safe = name.replace('"', "'")
                block = {"type": "text", "text": f'<attachment name="{safe}">\n{data.decode("utf-8")}\n</attachment>'}
            blocks.append({"name": name, "block": block})
            shown.append({"id": meta["id"], "name": name, "kind": meta["kind"], "size": meta["size"]})
        return blocks, shown

    @staticmethod
    def check_conversation_room(rt: BotRuntime, blocks: list) -> None:
        """Files stay in the conversation, and every request carries them all; refuse before the API would."""
        def size(block: dict) -> int:
            src = block.get("source") if isinstance(block, dict) else None
            return len(src.get("data", "")) if isinstance(src, dict) else 0

        carried = 0
        for m in (rt.agent.messages if rt.agent is not None else []):
            if m.get("role") == "user" and isinstance(m.get("content"), list):
                carried += sum(size(b) for b in m["content"])
        adding = sum(size(a["block"]) for a in blocks)
        if carried + adding > CONVERSATION_FILES_MAX:
            raise ApiError(400, f"This conversation already carries {carried / 1e6:.0f} MB of images and PDFs, and the "
                                f"API takes about {CONVERSATION_FILES_MAX / 1e6:.0f} MB per message. Start fresh "
                                f"(bot menu) to attach more, or put the file in the bot's folder and ask about it.")

    # -- settings ------------------------------------------------------------------------------------------
    def settings(self) -> dict:
        cfg = Config.load()
        key = get_key("anthropic") or ""
        return {
            "has_key": bool(key), "key_hint": (key[:7] + "…" + key[-4:]) if len(key) > 16 else "",
            "key_from_env": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "model": cfg.model, "fallback_models": cfg.fallback_models, "effort": cfg.effort,
            "max_session_cost_usd": cfg.max_session_cost_usd, "web_search": cfg.web_search, "notify": cfg.notify,
            "memory_learn": cfg.memory_learn, "models": list(MODELS), "efforts": list(EFFORTS),
            "providers": [{"id": p["id"], "name": p["name"], "base_url": p["base_url"], "has_key": bool(get_secret(f"provider:{p['id']}"))}
                          for p in cfg.providers],
            "small_model": cfg.small_model, "show_thinking": cfg.show_thinking,
            "deny_commands": list(cfg.deny_commands), "protected_write_names": list(cfg.protected_write_names),
            "redact_secrets": cfg.redact_secrets, "approval_timeout_minutes": cfg.approval_timeout_minutes,
            "max_task_minutes": cfg.max_task_minutes, "max_steps_per_turn": cfg.max_steps_per_turn,
            "read_project_files": cfg.read_project_files, "compact_at_tokens": cfg.compact_at_tokens,
            "notify_approval": cfg.notify_approval, "notify_done": cfg.notify_done, "notify_failed": cfg.notify_failed,
            "notify_sound": cfg.notify_sound, "keep_awake": cfg.keep_awake, "sounds": list(SOUNDS),
            "ghost_color": cfg.ghost_color if cfg.ghost_color in GHOST_COLORS else "white",
            "ghost_colors": list(GHOST_COLORS),
            "custom_colors": [c for c in cfg.custom_colors if isinstance(c, str) and c.startswith("#") and bot_color(c)],
        }

    def save_settings(self, body: dict) -> dict:
        updates = {}
        if "model" in body:
            if body["model"] not in MODELS:
                raise ApiError(400, "Unknown model.")
            updates["model"] = body["model"]
        if "fallback_models" in body:
            fb = body["fallback_models"]
            if not isinstance(fb, list) or any(m not in MODELS for m in fb):
                raise ApiError(400, "Unknown fallback model.")
            updates["fallback_models"] = fb
        if "effort" in body:
            if body["effort"] not in EFFORTS:
                raise ApiError(400, "Unknown effort level.")
            updates["effort"] = body["effort"]
        if "max_session_cost_usd" in body:
            try:
                v = float(body["max_session_cost_usd"])
            except (TypeError, ValueError):
                raise ApiError(400, "The budget must be a number of dollars.")
            if not 0 < v <= 1000:
                raise ApiError(400, "Pick a budget between $0 and $1,000.")
            updates["max_session_cost_usd"] = v
        for flag in ("web_search", "notify", "memory_learn", "show_thinking", "redact_secrets", "read_project_files",
                     "notify_approval", "notify_done", "notify_failed", "keep_awake"):
            if flag in body:
                updates[flag] = bool(body[flag])
        if "small_model" in body:
            if body["small_model"] not in MODELS:
                raise ApiError(400, "Unknown model.")
            updates["small_model"] = body["small_model"]
        if "notify_sound" in body:
            if body["notify_sound"] not in ("",) + SOUNDS:
                raise ApiError(400, "Unknown sound.")
            updates["notify_sound"] = body["notify_sound"]
        for name, lo, hi, what in (("approval_timeout_minutes", 0, 1440, "minutes"), ("max_task_minutes", 0, 1440, "minutes"),
                                   ("max_steps_per_turn", 5, 500, "steps"), ("compact_at_tokens", 20_000, 900_000, "tokens")):
            if name in body:
                try:
                    v = int(body[name])
                except (TypeError, ValueError):
                    raise ApiError(400, f"That needs a whole number of {what}.")
                if not lo <= v <= hi:
                    raise ApiError(400, f"Pick between {lo:,} and {hi:,} {what}.")
                updates[name] = v
        for name in ("deny_commands", "protected_write_names"):
            if name in body:
                items = body[name]
                if not isinstance(items, list) or len(items) > 200 or any(not isinstance(x, str) or len(x) > 300 for x in items):
                    raise ApiError(400, "That list has something odd in it.")
                updates[name] = [x.strip() for x in items if x.strip()]
        if "ghost_color" in body:
            if body["ghost_color"] not in GHOST_COLORS:
                raise ApiError(400, "Unknown color.")
            updates["ghost_color"] = body["ghost_color"]
            if self.native:
                self.native.set_icon(body["ghost_color"])
        if "custom_colors" in body:
            cc = body["custom_colors"]
            if not isinstance(cc, list) or len(cc) > 24 or not all(isinstance(c, str) and c.startswith("#") and bot_color(c) for c in cc):
                raise ApiError(400, "Custom colors look like #ff8800.")
            updates["custom_colors"] = list(dict.fromkeys(c.lower() for c in cc))
        if updates:
            _write_config_file(updates)
            cfg = Config.load()
            self.keep_awake_pref = cfg.keep_awake
            for rt in list(self.runtimes.values()):
                rt.apply_settings(cfg)
        return self.settings()

    MANAGED = ("model", "fallback_models", "effort", "max_session_cost_usd", "web_search", "notify",
               "memory_learn", "show_thinking", "redact_secrets", "read_project_files", "notify_approval", "notify_done",
               "notify_failed", "keep_awake", "small_model", "notify_sound", "approval_timeout_minutes",
               "max_task_minutes", "max_steps_per_turn", "compact_at_tokens", "deny_commands", "protected_write_names")

    def export_settings(self) -> dict:
        cur = self.settings()
        return {"oomi_settings": 1, "config": {k: cur[k] for k in self.MANAGED}, "ui": self.ui().get("prefs", {})}

    def import_settings(self, data: dict) -> dict:
        if not isinstance(data, dict) or data.get("oomi_settings") != 1:
            raise ApiError(400, "That isn't an oomi settings file.")
        config = data.get("config") if isinstance(data.get("config"), dict) else {}
        self.save_settings({k: v for k, v in config.items() if k in self.MANAGED})
        if isinstance(data.get("ui"), dict):
            self.save_ui({"prefs": None})
            self.save_ui({"prefs": data["ui"]})
        return self.settings()

    def reset_settings(self) -> dict:
        d = Config()
        _write_config_file({k: getattr(d, k) for k in self.MANAGED})
        cfg = Config.load()
        self.keep_awake_pref = cfg.keep_awake
        for rt in list(self.runtimes.values()):
            rt.apply_settings(cfg)
        self.save_ui({"prefs": None})
        return self.settings()

    def save_key(self, key: str) -> dict:
        key = (key or "").strip()
        if not KEY_RX.match(key):
            raise ApiError(400, "That doesn't look like an API key. It starts with sk-ant- and has no spaces.")
        ok, message = self.key_checker(key)
        if not ok:
            raise ApiError(400, message)
        if not store_key("anthropic", key):
            raise ApiError(500, "Couldn't save the key to your Keychain.")
        if os.environ.get("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = key  # the environment wins over the Keychain; keep them in step
        if not (self.home / "config.json").exists():
            Config().save()
        for rt in list(self.runtimes.values()):
            rt.new_llm = True
        Health(self.home).event("key_ok")
        return {"ok": True, "message": message, "settings": self.settings()}

    def add_provider(self, body: dict) -> dict:
        """Another provider that speaks the Messages API. Its key isn't checked here: gateways differ in what
        they answer besides messages, so a wrong key shows on the first message of a bot that uses it."""
        name = " ".join(str(body.get("name") or "").split())[:40]
        if not name:
            raise ApiError(400, "Give the provider a name.")
        url = str(body.get("base_url") or "").strip().rstrip("/")
        url = url[:-3] if url.endswith("/v1") else url  # the SDK adds /v1/messages itself
        local = re.match(r"http://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?(/|$)", url)
        if not (re.match(r"https://[^\s/]+", url) or local) or len(url) > 300 or re.search(r"\s", url):
            raise ApiError(400, "Use the provider's https:// address, like https://gateway.example.com.")
        key = str(body.get("key") or "").strip()
        if not key or len(key) > 500 or re.search(r"\s", key):
            raise ApiError(400, "Paste the provider's API key, with no spaces.")
        providers = Config.load().providers
        if any(p["name"].lower() == name.lower() for p in providers) or name.lower() == "anthropic":
            raise ApiError(400, f"There's already a provider called {name}.")
        pid = new_id("p")
        if not store_secret(f"provider:{pid}", key):
            raise ApiError(500, "Couldn't save the key to your Keychain.")
        _write_config_file({"providers": providers + [{"id": pid, "name": name, "base_url": url}]})
        return self.settings()

    def remove_provider(self, pid: str) -> dict:
        providers = Config.load().providers
        if not any(p["id"] == pid for p in providers):
            raise ApiError(404, "No such provider.")
        _write_config_file({"providers": [p for p in providers if p["id"] != pid]})
        delete_secret(f"provider:{pid}")
        for rt in list(self.runtimes.values()):  # its bots go back to Anthropic
            if rt.record.provider == pid:
                rt.record.provider = ""
                rt.new_llm = True
                self.bus.emit({"type": "bot", "bot": rt.record.id, "bot_state": rt.state()})
        self.registry.save()
        return self.settings()

    # -- status ----------------------------------------------------------------------------------------------
    def status(self) -> dict:
        cfg = Config.load()
        h = Health(self.home)
        status, when = h.key_status()
        mem = Memory(self.home / "memory.db")
        try:
            memory = {"count": mem.count(), "unreviewed": len(mem.unreviewed())}
        finally:
            mem.db.close()
        skills = general_store(self.home).list()
        from .. import sandbox

        try:
            sb = sandbox.probe()
        except Exception as e:  # noqa: BLE001
            sb = {"available": False, "reason": str(e)}
        fails = h.recent(24, {"model_unavailable"})
        return {
            "version": __version__,
            "key": {"status": status if status != "unknown" else ("missing" if not get_key("anthropic") else "unused"),
                    "when": when},
            "model": cfg.model, "fallback": cfg.fallback_models, "background": cfg.small_model,
            "failures_24h": len(fails), "latest_failure": fails[-1]["detail"] if fails else "",
            "fallbacks_24h": len(h.recent(24, {"fallback_used"})),
            "spent_today": round(h.spent(1), 4), "spent_7d": round(h.spent(7), 4),
            "by_day": h.by_day(14),
            "sandbox": {"available": bool(sb.get("available")), "reason": sb.get("reason", "")},
            "memory": memory,
            "skills": {"count": sum(1 for s in skills if s.status not in ("retired", "candidate")),
                       "candidates": sum(1 for s in skills if s.status == "candidate"),
                       "unreliable": sum(1 for s in skills if s.unreliable and s.status != "retired"),
                       "retired": sum(1 for s in skills if s.status == "retired")},
            "bots": [{"id": s["id"], "name": s["name"], "cost": s["cost"], "limit": s["limit"],
                      "context_tokens": s["context_tokens"], "tainted": s["tainted"]} for s in self.bot_states()],
        }

    # -- usage statistics ---------------------------------------------------------------------------------------
    def usage(self) -> dict:
        """Spending and tokens: overall, per day, per model and per bot (across every conversation it has had)."""
        rows = Health(self.home)._usage_rows(0)
        now, today = time.time(), time.strftime("%Y-%m-%d")

        def blank() -> dict:
            return {"usd": 0.0, "in": 0, "out": 0, "calls": 0}

        def add(acc: dict, r: dict) -> None:
            acc["usd"] += float(r.get("usd") or 0)
            acc["in"] += int(r.get("in") or 0)
            acc["out"] += int(r.get("out") or 0)
            acc["calls"] += 1

        def periods() -> dict:
            return {"today": blank(), "week": blank(), "month": blank(), "all": blank(), "last": 0.0}

        def count(target: dict, r: dict) -> None:
            age = now - r["t"]
            add(target["all"], r)
            if r.get("day") == today:
                add(target["today"], r)
            if age < 7 * 86400:
                add(target["week"], r)
            if age < 30 * 86400:
                add(target["month"], r)
            target["last"] = max(target["last"], r["t"])

        owner = {}
        bots = self.registry.ordered()
        for b in bots:
            for sid in [*b.sessions, b.session_id]:
                if sid:
                    owner[sid] = b.id
        totals, other = periods(), periods()
        per_bot = {b.id: periods() for b in bots}
        days, models = {}, {}
        for r in rows:
            count(totals, r)
            count(per_bot.get(owner.get(r.get("session")), other), r)
            if now - r["t"] < 30 * 86400:
                add(days.setdefault(str(r.get("day")), blank()), r)
                add(models.setdefault(str(r.get("model")), blank()), r)

        def rounded(d: dict) -> dict:
            return {k: ({**v, "usd": round(v["usd"], 4)} if isinstance(v, dict) else v) for k, v in d.items()}

        return {
            "totals": rounded(totals),
            "days": [{"day": d, **v, "usd": round(v["usd"], 4)} for d, v in sorted(days.items())],
            "models": sorted(({"model": m, **v, "usd": round(v["usd"], 4)} for m, v in models.items()),
                             key=lambda x: -x["usd"]),
            "bots": [{"id": b.id, "name": b.name, "avatar": b.avatar, "color": b.color, **rounded(per_bot[b.id]),
                      **self.brain_counts(b.id)}
                     for b in bots],
            "general": self.brain_counts(""),
            "other": rounded(other),
        }

    # -- antibodies: the team's proven fixes on MongoDB Atlas ----------------------------------------------------
    def connect_team(self, db=None) -> None:
        from ..antibodies import TeamSettings, open_store

        try:
            s = TeamSettings.load(self.home)
            if not s.mongodb_uri and db is None:
                self.team, self.team_error = None, ""
                return
            self.team, self.team_settings, self.team_error = open_store(s, db), s, ""
        except Exception as e:  # noqa: BLE001  (unreachable, bad password, no pymongo: shown in Settings)
            self.team, self.team_error = None, f"{type(e).__name__}: {e}"[:300]
        self.bus.emit({"type": "antibodies"})

    def antibodies_state(self) -> dict:
        from ..antibodies import public
        from .. import atlas

        store = self.team
        if store is None:
            return {"connected": False, "error": self.team_error}
        mode = (atlas.MODES[min(store.searcher.mode_index, len(atlas.MODES) - 1)] if store.use_atlas_search
                else "local")
        try:  # connected at startup, but the cluster can stop answering since (network, Atlas IP access list)
            st = store.stats()
            pending, approved = store.list("pending", 50), store.list("approved", 100)
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "error": f"{type(e).__name__}: {e}"}
        return {"connected": True, "team": store.team, "user": store.user, "mode": mode,
                "search_error": store.searcher.last_error if store.use_atlas_search else "",
                "stats": {k: st.get(k, 0) for k in ("antibodies", "cures", "saved_seconds", "saved_usd")},
                "pending": [public(d) for d in pending], "approved": [public(d) for d in approved]}

    # -- connected apps -----------------------------------------------------------------------------------------
    def apps(self) -> dict:
        apps = self.connectors.catalog()
        return {"apps": apps, "categories": CATEGORIES, "connected": sum(1 for a in apps if a["connected"])}

    # -- memory and skills --------------------------------------------------------------------------------------
    # bot="" is what every bot shares; a bot id is that bot's personal memory and skills.
    def _bot_scope(self, bot: str) -> str:
        if bot and bot not in self.registry.bots:
            raise ApiError(404, "No such bot.")
        return bot

    def memory(self, bot: str = "") -> Memory:
        bot = self._bot_scope(bot)
        return Memory(self.home / "memory.db", scope=bot, personal_only=bool(bot))

    def skill_store(self, bot: str = "") -> SkillStore:
        general = general_store(self.home)
        bot = self._bot_scope(bot)
        return SkillStore(self.home / "bots" / bot / "skills", shared=general) if bot else general

    def brain_counts(self, bot: str) -> dict:
        """How much a bot (or, with "", every bot together) has learned: memories, skills, skill uses."""
        mem = Memory(self.home / "memory.db", scope=bot, personal_only=bool(bot))
        try:
            memories = mem.count()
        finally:
            mem.db.close()
        root = self.home / "bots" / bot / "skills" if bot else self.home / "skills"
        skills = SkillStore(root).own(include_candidates=False) if root.is_dir() else []
        live = [s for s in skills if s.status != "retired"]
        return {"memories": memories, "skills": len(live), "skill_uses": sum(s.stats.get("uses", 0) for s in live),
                "skill_wins": sum(s.outcomes[0] for s in live), "skill_fails": sum(s.outcomes[1] for s in live)}

    @staticmethod
    def fact(f) -> dict:
        return {"id": f.id, "text": f.text, "tags": f.tags, "created": f.created, "source": f.source,
                "reviewed": bool(f.reviewed), "expires_at": f.expires_at, "personal": bool(f.scope),
                "owner": f.scope}

    def memory_list(self, q: str = "", bot: str = "", everyone: bool = False) -> dict:
        """bot: one bot's own memories. everyone (with no bot): the shared ones and every bot's, each with its owner."""
        everyone = everyone and not bot
        if everyone:
            m = Memory(self.home / "memory.db", everyone=True)
        else:
            m = self.memory(bot)
        try:
            facts = m.search(q, 40, touch=False, semantic=False) if q else m.list(500)
            out = {"facts": [self.fact(f) for f in facts], "unreviewed": [self.fact(f) for f in m.unreviewed()],
                   "history": [dict(self.fact(f), reason=r) for f, r in m.history(30)], "count": m.count()}
        finally:
            m.db.close()
        if everyone:
            names = {b.id: b.name for b in self.registry.bots.values()}
            for group in ("facts", "unreviewed", "history"):
                for f in out[group]:
                    f["owner_name"] = names.get(f["owner"], "A deleted bot") if f["owner"] else ""
        return out

    def skills(self, bot: str = "") -> list:
        store = self.skill_store(bot)
        skills, certified = store.own(), []
        if store.certified:  # the shared list also shows the certified ones, unless a saved skill replaces one
            mine = {s.name for s in skills}
            certified = [s for s in store.certified.own() if s.name not in mine]
        out = []
        for s in skills + certified:
            ok, bad = s.outcomes
            out.append({"name": s.name, "description": s.description, "status": s.status, "successes": ok,
                        "failures": bad, "unreliable": s.unreliable, "verify": s.verify, "body": s.body[:6000],
                        "uses": s.stats.get("uses", 0), "personal": s.personal, "certified": s in certified})
        return out

    # -- search -------------------------------------------------------------------------------------------
    def search(self, q: str) -> list:
        q = q.strip().lower()
        if len(q) < 2:
            return []
        out = []
        for rec in self.registry.ordered():
            rt = self.runtimes.get(rec.id)
            if rt is None:
                continue
            for item in reversed(rt.timeline.items(5000)):
                if item.get("type") not in ("user", "assistant"):
                    continue
                text = item.get("text", "")
                i = text.lower().find(q)
                if i < 0:
                    continue
                start = max(0, i - 50)
                out.append({"bot": rec.id, "item": item["id"], "ts": item.get("ts"), "role": item["type"],
                            "snippet": ("…" if start else "") + text[start:i + len(q) + 80]})
                if len(out) >= 60:
                    return out
        return out

    # -- Mac conveniences -----------------------------------------------------------------------------------
    def reveal(self, bot_id: str) -> None:
        rt = self.rt(bot_id)
        if sys.platform == "darwin":
            subprocess.Popen(["/usr/bin/open", rt.record.folder])
        else:
            raise ApiError(400, "Showing folders works on a Mac.")

    def _backup_loop(self) -> None:
        """Copy oomi's data to Atlas a couple of minutes after starting, then every 15 minutes, while sync is on."""
        wait = 120
        while not self.backup_stop.wait(wait):
            wait = 15 * 60
            try:
                if self.data_sync.enabled:
                    self.data_sync.push(auto=True)
            except AccountError:
                pass  # shown in Settings, Account
            except Exception:  # never let one bad pass stop the backups that follow
                traceback.print_exc()

    def shutdown(self) -> None:
        """Stop every bot, let each learn from its conversation and save. Safe to call more than once."""
        with self.lock:
            if self.closed:
                return
            self.closed = True
        if self.awake is not None:
            self.awake.terminate()
        self.scheduler.stop()
        threads = [threading.Thread(target=rt.shutdown, daemon=True) for rt in list(self.runtimes.values())]
        for t in threads:
            t.start()
        for t in threads:
            t.join(20)
        self.backup_stop.set()
        if self.data_sync.enabled:  # a last copy with what they just saved
            t = threading.Thread(target=lambda: self._quiet(lambda: self.data_sync.push(auto=True)), daemon=True)
            t.start()
            t.join(20)

    @staticmethod
    def _quiet(fn) -> None:
        try:
            fn()
        except AccountError:
            pass


# ---------------------------------------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "oomi"
    app: App = None  # type: ignore[assignment]

    def log_message(self, fmt, *args):  # quiet
        pass

    # -- plumbing ------------------------------------------------------------------------------------------
    def _host_ok(self) -> bool:
        host = self.headers.get("Host", "")
        port = self.server.server_address[1]
        return host in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, status: int, body: bytes, ctype: str, extra: Optional[dict] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ApiError(413, "That's too large to send.")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            raise ApiError(400, "Bad request.")
        if not isinstance(data, dict):
            raise ApiError(400, "Bad request.")
        return data

    def _authorized(self, query: dict) -> bool:
        token = self.headers.get("X-Oomi-Token") or (query.get("t") or [""])[0]
        return secrets.compare_digest(token, self.app.token)

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        if not self._host_ok():  # a page on another site pointing its own hostname at 127.0.0.1
            self._json({"error": "Not allowed."}, 403)
            return
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if method == "GET" and path in ("/", "/index.html"):
                if not self._authorized(query):
                    self._send(403, b"Open oomi from the app.", "text/plain")
                    return
                self._static("index.html")
                return
            if method == "GET" and path == "/favicon.ico":  # browsers ask for this one directly
                self._static("favicon.ico")
                return
            if method == "GET" and path.startswith("/static/"):
                self._static(unquote(path[len("/static/"):]))
                return
            if method == "GET" and path == "/oauth/callback":
                self._oauth_callback(query)
                return
            if not path.startswith("/api/"):
                raise ApiError(404, "Not found.")
            if not self._authorized(query):
                raise ApiError(403, "Not allowed.")
            if method != "GET" and self.headers.get("Origin") not in (None, f"http://{self.headers.get('Host')}"):
                raise ApiError(403, "Not allowed.")
            if path == "/api/events":
                self._events(query)
                return
            self._api(method, path[5:], query)
        except ApiError as e:
            self._json({"error": e.message, **e.extra}, e.status)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            try:
                self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            except OSError:
                pass

    def _redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/oauth/callback"

    def _oauth_callback(self, query: dict) -> None:
        q = lambda k: (query.get(k) or [""])[0]  # noqa: E731
        if q("error"):
            ok, msg = False, f"{q('error_description') or q('error')}"
        else:
            try:
                app = self.app.connectors.finish_oauth(q("state"), q("code"))
                ok, msg = True, f"{app['name']} is connected, with {len(app['tools'])} tools. You can close this tab."
            except McpError as e:
                ok, msg = False, str(e)
        self.app.bus.emit({"type": "apps", "ok": ok, "message": msg})
        page = ("<!doctype html><meta charset=utf-8><title>oomi</title><body style='font:16px -apple-system,system-ui;"
                "display:grid;place-items:center;height:90vh;margin:0;background:#111;color:#eee'><div style='max-width:420px;"
                "text-align:center'><h2>" + ("Connected" if ok else "Couldn't connect") + "</h2><p>"
                + html.escape(msg) + "</p></div>")
        self._send(200 if ok else 400, page.encode(), "text/html; charset=utf-8")

    def _antibodies(self, method: str, path: str, b: dict) -> None:
        app = self.app
        if (method, path) == ("GET", ""):
            return self._json(app.antibodies_state())
        if (method, path) == ("POST", "connect"):
            app.connect_team()
            return self._json(app.antibodies_state())
        store = app.team
        if store is None:
            raise ApiError(400, "Connect MongoDB Atlas first (Settings, Account, or MONGODB_URI).")
        if (method, path) == ("POST", "setup"):
            from .. import atlas

            try:
                report = atlas.setup(store.db, embed_model=app.team_settings.embed_model if app.team_settings else
                                     "voyage-4", wait_s=int(b.get("wait") or 20))
            except Exception as e:  # noqa: BLE001
                raise ApiError(400, f"Setting up Atlas failed: {e}")
            return self._json(dict(app.antibodies_state(), report=report))
        if (method, path) == ("POST", "match"):
            from ..antibodies import public

            error = str(b.get("error") or "").strip()[:4000]
            if not error:
                raise ApiError(400, "Paste an error to match.")
            try:
                docs, how, _ = store.candidates(error)
                m = store.match(error)
            except Exception as e:  # noqa: BLE001
                raise ApiError(400, str(e))
            return self._json({"how": how, "candidates": [public(d) for d in docs[:5]],
                               "match": {"fix": m.fix, "score": round(m.score, 3), "how": m.how, "author": m.author,
                                         "error_text": m.error_text} if m else None})
        m = re.fullmatch(r"([0-9a-f]{24})/review", path)
        if method == "POST" and m:
            if not store.review(m.group(1), approve=bool(b.get("approve"))):
                raise ApiError(400, "That antibody isn't waiting for review any more.")
            app.bus.emit({"type": "antibodies"})
            return self._json(app.antibodies_state())
        if (method, path) == ("POST", "add"):
            # A fix you know works, typed in by hand. You're the reviewer, so it's shared straight away.
            from ..fingerprint import redact, signature

            kind = " ".join(str(b.get("error_type") or "").split())[:80]
            message = str(b.get("error_message") or "").strip()[:4000]
            fix = " ".join(str(b.get("fix") or "").split())[:500]
            command = " ".join(str(b.get("command") or "").split())[:300]
            if not message:
                raise ApiError(400, "Paste the error message.")
            if not fix:
                raise ApiError(400, "Say what fixed it.")
            error = redact(f"{kind}: {message}" if kind and not message.startswith(kind) else message)
            try:
                store.save({"error_text": error, "signature": signature(error), "command": redact(command), "fix": redact(fix),
                            "steps": [], "proven_by": {"command": redact(command), "exit_code": 0} if command else None,
                            "project": " ".join(str(b.get("project") or "").split())[:80], "source": "manual"}, approved=True)
            except Exception as e:  # noqa: BLE001 - Atlas unreachable
                raise ApiError(400, f"Couldn't save the fix: {e}"[:300])
            app.bus.emit({"type": "antibodies"})
            return self._json(app.antibodies_state())
        if method == "DELETE" and re.fullmatch(r"[0-9a-f]{24}", path):
            if not store.delete(path):
                raise ApiError(404, "That fix is already gone.")
            app.bus.emit({"type": "antibodies"})
            return self._json(app.antibodies_state())
        raise ApiError(404, "Not found.")

    def _account(self, method: str, path: str, b: dict) -> None:
        """Your profile, and its copy in MongoDB Atlas. A changed profile reaches each bot's next conversation."""
        acct = self.app.account
        try:
            data = self.app.data_sync
            if (method, path) == ("GET", "account"):
                out = acct.state()
            elif (method, path) == ("PATCH", "account"):
                out = acct.update(b)
            elif (method, path) == ("POST", "account/sync/connect"):
                out = acct.connect(str(b.get("uri") or ""), str(b.get("database") or "oomi"),
                                   str(b.get("collection") or "accounts"))
                threading.Thread(target=self.app.connect_team, daemon=True).start()  # the same cluster holds antibodies
            elif (method, path) == ("POST", "account/sync/push"):
                out = acct.push()
                if data.enabled:
                    data.push()
            elif (method, path) == ("POST", "account/sync/pull"):
                out = acct.pull(str(b.get("account_id") or ""))
            elif (method, path) == ("POST", "account/sync/disconnect"):
                out = acct.disconnect()
            elif (method, path) == ("GET", "account/data/remote"):
                out = {"remote": data.remote()}
            elif (method, path) == ("POST", "account/data/enabled"):
                data.set_enabled(bool(b.get("on")))
                out = acct.state()
            elif (method, path) == ("POST", "account/data/restore"):
                data.restore()
                out = acct.state()
            elif (method, path) == ("POST", "account/data/cancel_restore"):
                data.cancel_restore()
                out = acct.state()
            else:
                raise ApiError(404, "Not found.")
        except AccountError as e:
            raise ApiError(400, str(e))
        if "profile" in out:
            out["data"] = data.state()
        self._json(out)

    def _apps(self, method: str, path: str, b: dict) -> None:
        store = self.app.connectors
        try:
            if method == "GET" and path == "":
                pass
            elif method == "POST" and path == "custom":
                app = store.custom(b.get("name"), b.get("url"))
                return self._connect(app, str(b.get("token") or ""))
            elif method == "POST" and path.endswith("/connect"):
                return self._connect(store.find(path[:-8]), str(b.get("token") or ""),
                                     {"client_id": b.get("client_id"), "client_secret": b.get("client_secret")})
            elif method == "POST" and path.endswith("/disconnect"):
                store.disconnect(path[:-11])
                self.app.bus.emit({"type": "apps"})
            elif method == "POST" and path.endswith("/refresh"):
                store.refresh_tools(path[:-8])
            else:
                raise ApiError(404, "Not found.")
        except McpError as e:
            raise ApiError(400, str(e))
        self._json(dict(self.app.apps(), redirect_uri=self._redirect_uri()))

    def _connect(self, app: dict, token: str, client: Optional[dict] = None) -> None:
        res = self.app.connectors.connect(app, self._redirect_uri(), token, client)
        if "auth_url" in res:
            if not self.app.no_browser:
                import webbrowser

                webbrowser.open(res["auth_url"])
            self._json(dict(self.app.apps(), auth_url=res["auth_url"]))
            return
        self.app.bus.emit({"type": "apps"})
        self._json(dict(self.app.apps(), connected=res["connected"]["id"]))

    def _static(self, rel: str) -> None:
        base = STATIC
        if rel == "favicon.png":
            base = ASSETS
        elif rel == "oomi-icon-256.png":
            base = ASSETS
        elif re.fullmatch(r"ghosts/[a-z]+\.png", rel):
            base = ASSETS
        p = (base / rel).resolve()
        if not str(p).startswith(str(base.resolve()) + os.sep) or not p.is_file():
            raise ApiError(404, "Not found.")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8"
        extra = {}
        if p.name == "index.html":
            extra["Content-Security-Policy"] = (
                "default-src 'none'; script-src 'self' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; connect-src 'self'; font-src 'self'; media-src 'self' blob:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            extra["Referrer-Policy"] = "no-referrer"
        self._send(200, p.read_bytes(), ctype, extra)

    def _events(self, query: dict) -> None:
        bus = self.app.bus
        try:
            last = int(self.headers.get("Last-Event-ID") or (query.get("since") or ["0"])[0])
        except ValueError:
            last = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        self.wfile.write(b"retry: 1000\n\n")
        self.wfile.flush()
        while True:
            events = bus.since(last, 15.0)
            if events is None:
                last = bus.seq
                self.wfile.write(f"id: {last}\nevent: reset\ndata: {{}}\n\n".encode())
            elif not events:
                self.wfile.write(b": ping\n\n")
            else:
                chunks = []
                for ev in events:
                    last = ev["seq"]
                    chunks.append(f"id: {last}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n")
                self.wfile.write("".join(chunks).encode())
            self.wfile.flush()

    # -- routes -------------------------------------------------------------------------------------------
    def _api(self, method: str, path: str, query: dict) -> None:
        app = self.app
        q = lambda k, d="": (query.get(k) or [d])[0]  # noqa: E731
        m = re.fullmatch(r"bots/(b[0-9a-f]{12})(?:/([a-z]+)(?:/(s[0-9a-f]{12}))?)?", path)
        if m:
            bot_id, action = m.group(1), m.group(2) or ""
            self._bot(method, app, bot_id, action, q, m.group(3) or "")
            return
        key = (method, path)
        if key == ("GET", "state"):
            self._json({"seq": app.bus.seq, "bots": app.bot_states(), "settings": app.settings(),
                        "version": __version__, "native": app.native.capabilities() if app.native else {},
                        "home": str(Path.home())})
        elif key == ("POST", "bots"):
            self._json(app.create_bot(self._body()))
        elif key == ("POST", "focus"):
            app.set_focus(self._body())
            self._json({"ok": True})
        elif key == ("GET", "ui"):
            self._json(app.ui())
        elif key == ("POST", "ui"):
            self._json(app.save_ui(self._body()))
        elif key == ("POST", "bots/import"):
            self._json(app.import_bot(self._body().get("data")))
        elif key == ("POST", "bots/stop-all"):
            self._json({"stopped": app.stop_all()})
        elif key == ("POST", "bots/read-all"):
            app.read_all()
            self._json({"ok": True})
        elif key == ("GET", "settings/export"):
            self._json(app.export_settings())
        elif key == ("POST", "settings/import"):
            self._json(app.import_settings(self._body().get("data")))
        elif key == ("POST", "settings/reset"):
            self._json(app.reset_settings())
        elif key == ("POST", "notify/test"):
            app.bus.emit({"type": "notify", "kind": "test", "title": "oomi", "body": "Notifications are working."})
            self._json({"ok": True})
        elif key == ("POST", "upload"):
            self._json(app.upload(self._body()))
        elif method == "GET" and path.startswith("attachment/"):
            meta, data = app.attachment(path.split("/", 1)[1])
            if meta["kind"] != "image":
                raise ApiError(404, "Only images can be previewed.")
            self._send(200, data, meta["type"], {"Content-Disposition": "inline"})
        elif key == ("GET", "search"):
            self._json({"results": app.search(q("q"))})
        elif key == ("GET", "settings"):
            self._json(app.settings())
        elif key == ("POST", "settings"):
            self._json(app.save_settings(self._body()))
        elif key == ("POST", "key"):
            self._json(app.save_key(str(self._body().get("key") or "")))
        elif key == ("POST", "providers"):
            self._json(app.add_provider(self._body()))
        elif method == "DELETE" and re.fullmatch(r"providers/p[0-9a-f]{12}", path):
            self._json(app.remove_provider(path.split("/", 1)[1]))
        elif key == ("GET", "status"):
            self._json(app.status())
        elif key == ("GET", "usage"):
            self._json(app.usage())
        elif key == ("GET", "memory"):
            self._json(app.memory_list(q("q"), q("bot"), everyone=q("all") == "1"))
        elif method == "POST" and path.startswith("memory/"):
            self._memory(path[7:], self._body())
        elif key == ("GET", "memory/text"):
            mem = app.memory(q("bot"))
            try:
                text, ids = mem.export_text()
            finally:
                mem.db.close()
            self._json({"text": text, "ids": ids})
        elif path == "antibodies" or path.startswith("antibodies/"):
            self._antibodies(method, path[len("antibodies"):].strip("/"), self._body() if method == "POST" else {})
        elif path == "account" or path.startswith("account/"):
            self._account(method, path, self._body() if method in ("POST", "PATCH") else {})
        elif path == "apps" or path.startswith("apps/"):
            self._apps(method, path[5:], self._body() if method == "POST" else {})
        elif key == ("GET", "skills"):
            self._json({"skills": app.skills(q("bot"))})
        elif method == "POST" and path in ("skills/approve", "skills/remove", "skills/share"):
            b = self._body()
            name, bot = str(b.get("name") or ""), str(b.get("bot") or "")
            store = app.skill_store(bot)
            try:
                if path.endswith("approve"):
                    store.approve(name)
                elif path.endswith("share"):
                    store.share(name)
                elif not store.remove(name):
                    raise ValueError(f"No skill named {name}.")
            except ValueError as e:
                raise ApiError(400, str(e))
            self._json({"skills": app.skills(bot)})
        elif key == ("POST", "native"):
            if not app.native:
                raise ApiError(400, "Only in the Mac app.")
            b = self._body()
            self._json({"result": app.native.call(str(b.get("action") or ""), b)})
        else:
            raise ApiError(404, "Not found.")

    def _memory(self, action: str, b: dict) -> None:
        bot = str(b.get("bot") or "")
        mem = self.app.memory(bot)
        try:
            if action == "keep":
                ids = [int(i) for i in b.get("ids", [])]
                mem.mark_reviewed(ids)
            elif action == "forget":
                mem.forget(int(b.get("id")))
            elif action == "share":
                if not mem.share(int(b.get("id"))):
                    raise ApiError(400, "Only a bot's own memories can be shared.")
            elif action == "add":
                try:
                    mem.add(str(b.get("text") or ""), tags=b.get("tags") or (), source="user")
                except ValueError as e:
                    raise ApiError(400, str(e))
            elif action == "edit":
                try:
                    mem.add(str(b.get("text") or ""), source="user", replaces=int(b.get("id")))  # keeps its scope
                except (ValueError, TypeError) as e:
                    raise ApiError(400, str(e))
            elif action == "file":
                name = Path(str(b.get("name") or "file")).name[:60] or "file"
                text = str(b.get("text") or "")
                if len(text) > TEXT_MAX:
                    raise ApiError(400, f"{name} is over 300 KB. Add the part you want remembered.")
                if "\x00" in text:
                    raise ApiError(400, f"{name} isn't a text file.")
                chunks = memory_chunks(text)
                if not chunks:
                    raise ApiError(400, f"{name} has nothing to remember.")
                saved = skipped = 0
                for chunk in chunks[:FILE_MEMORY_MAX]:
                    try:
                        mem.add(chunk, source="user")
                        saved += 1
                    except ValueError:  # a secret, or something too odd to keep
                        skipped += 1
                skipped += max(0, len(chunks) - FILE_MEMORY_MAX)
                self.app.bus.emit({"type": "memory_changed", "bot": bot})
                self._json(dict(self.app.memory_list(bot=bot), result={"name": name, "saved": saved, "skipped": skipped}))
                return
            elif action == "text":
                res = mem.apply_text(str(b.get("text") or ""), [int(i) for i in b.get("ids", [])])
                self._json(dict(self.app.memory_list(bot=bot), result=res))
                return
            else:
                raise ApiError(404, "Not found.")
        finally:
            mem.db.close()
        self.app.bus.emit({"type": "memory_changed", "bot": bot})
        self._json(self.app.memory_list(bot=bot, everyone=bool(b.get("all")) and not bot))

    def _schedules(self, method: str, app: App, bot_id: str, sid: str) -> None:
        store = app.schedules
        s = store.get(sid) if sid else None
        if sid and (s is None or s.bot != bot_id):
            raise ApiError(404, "No such schedule.")
        if method == "GET" and not sid:
            self._json({"schedules": [schedule_json(x) for x in store.for_bot(bot_id)]})
            return
        b = self._body() if method in ("POST", "PATCH") else {}
        changes = {}
        if "when" in b or method == "POST":
            changes["when"] = str(b.get("when") or "")
        if "prompt" in b or method == "POST":
            changes["prompt"] = str(b.get("prompt") or "").strip()
            if not changes["prompt"]:
                raise ApiError(400, "Say what it should do when the time comes.")
            if len(changes["prompt"]) > 4000:
                raise ApiError(400, "The prompt can be at most 4,000 characters.")
        if "enabled" in b:
            changes["enabled"] = bool(b["enabled"])
        try:
            if method == "POST" and not sid:
                if len(store.for_bot(bot_id)) >= 20:
                    raise ApiError(400, "A bot can have up to 20 schedules.")
                s = store.add(bot_id, changes["when"], changes["prompt"])
            elif method == "PATCH" and sid:
                s = store.update(sid, **changes)
            elif method == "DELETE" and sid:
                store.remove(sid)
                app.scheduler.changed(bot_id)
                self._json({"ok": True})
                return
            else:
                raise ApiError(404, "Not found.")
        except ScheduleError as e:
            raise ApiError(400, str(e))
        app.scheduler.changed(bot_id)
        self._json(schedule_json(s))

    def _bot(self, method: str, app: App, bot_id: str, action: str, q, sid: str = "") -> None:
        rt = app.rt(bot_id)
        key = (method, action)
        if action == "schedules":
            self._schedules(method, app, bot_id, sid)
        elif key == ("GET", "timeline"):
            with app.bus.cond:  # the live reply and the event number it corresponds to, taken together
                seq, live = app.bus.seq, rt.live()
            self._json({"items": rt.timeline.items(), "live": live, "state": rt.state(), "seq": seq})
        elif key == ("PATCH", ""):
            self._json(app.update_bot(bot_id, self._body()))
        elif key == ("POST", "clone"):
            self._json(app.clone_bot(bot_id, self._body()))
        elif key == ("GET", "export"):
            self._json(app.export_bot(bot_id))
        elif key == ("GET", "context"):
            self._json(app.context(bot_id))
        elif key == ("POST", "compact"):
            if rt.status != "idle":
                raise ApiError(409, "Wait until it finishes, or stop it first.")
            try:
                done = rt.submit("compact").result(300)
            except NoKey as e:
                raise ApiError(400, str(e) or "Add your Anthropic API key in Settings first.")
            except Exception as e:  # noqa: BLE001 - the model may be unavailable
                raise ApiError(502, f"Couldn't compact: {e}")
            self._json({"compacted": done, "context": app.context(bot_id)})
        elif key == ("DELETE", ""):
            app.delete_bot(bot_id)
            self._json({"ok": True})
        elif key == ("POST", "send"):
            b = self._body()
            text = str(b.get("text") or "").strip()
            att_ids = [str(x) for x in b.get("attachments") or []]
            if not text and not att_ids:
                raise ApiError(400, "Type a message first.")
            blocks, shown = app.blocks_for(att_ids) if att_ids else ([], [])
            if blocks:
                app.check_conversation_room(rt, blocks)
            display = {"shown": text}
            if shown:
                display["attachments"] = shown
            reply = b.get("reply") if isinstance(b.get("reply"), dict) else None
            quote = ""
            if reply and reply.get("text"):
                quote = str(reply["text"])[:600]
                display["reply"] = {"id": str(reply.get("id") or ""), "text": quote[:200],
                                    "role": str(reply.get("role") or "")}
            self._json({"item": rt.send(text or "Here's a file.", blocks, plan=bool(b.get("plan")), display=display,
                                         quote=quote)})
        elif key == ("POST", "stop"):
            rt.stop()
            self._json({"ok": True})
        elif key == ("POST", "approve"):
            b = self._body()
            if not rt.answer(str(b.get("id") or ""), str(b.get("choice") or "no"), str(b.get("note") or "")):
                raise ApiError(409, "That request was already answered.")
            self._json({"ok": True})
        elif key == ("POST", "go"):
            if rt.agent is None or rt.agent.prev_trust is None:
                raise ApiError(409, "There's no plan waiting for approval.")
            if rt.status != "idle":
                raise ApiError(409, "Wait until it finishes, or stop it first.")
            self._json({"item": rt.go(str(self._body().get("edits") or "").strip())})
        elif key == ("POST", "fresh"):
            if rt.status != "idle":
                raise ApiError(409, "Stop the bot before starting fresh.")
            rt.submit("fresh").result(60)
            self._json(rt.state())
        elif key == ("POST", "untaint"):
            if rt.agent is not None:
                rt.agent.taint.clear()
            app.bus.emit({"type": "bot", "bot": bot_id, "bot_state": rt.state()})
            self._json(rt.state())
        elif key == ("POST", "budget"):
            if rt.agent is None:
                raise ApiError(409, "Nothing has been spent yet.")
            try:
                add = float(self._body().get("add") or Config.load().max_session_cost_usd)
            except (TypeError, ValueError):
                raise ApiError(400, "The amount must be a number of dollars.")
            if not 0 < add <= 1000:
                raise ApiError(400, "Pick an amount between $0 and $1,000.")
            rt.agent.cost.limit_usd = max(rt.agent.cost.limit_usd, rt.agent.cost.spent_usd) + add
            rt.agent.cost.warned = False
            app.bus.emit({"type": "bot", "bot": bot_id, "bot_state": rt.state()})
            self._json(rt.state())
        elif key == ("GET", "changes"):
            a = rt.agent
            if a is None:
                self._json({"items": [], "snapshots": [], "next": None, "next_kind": None})
                return
            self._json({"items": [{"path": p, "status": s, "detail": d} for p, s, d in a.changes.summary()],
                        "snapshots": [{"label": s.get("label"), "at": s.get("t")}
                                      for s in a.changes.snapshots()],
                        "next": a.changes.peek(), "next_kind": a.changes.next_kind()})
        elif key == ("GET", "diff"):
            a = rt.agent
            self._json({"diff": a.changes.diff(q("path")) if a else ""})
        elif key == ("POST", "undo"):
            if rt.status != "idle":
                raise ApiError(409, "Wait until it finishes, or stop it first.")
            b = self._body()
            self._json(rt.submit("undo", bool(b.get("all")), bool(b.get("confirm"))).result(120))
        elif key == ("POST", "share"):
            # The Share button on a chat notice: the fixes that turn proved go to the team
            item = rt.timeline.get(str(self._body().get("item") or ""))
            if item is None or item.get("action") != "antibody":
                raise ApiError(404, "No such fix.")
            if app.team is None:
                raise ApiError(400, "Connect MongoDB Atlas first (Settings, Account).")
            shared = sum(1 for i in item.get("antibodies") or [] if app.team.review(i, approve=True))
            item = dict(item, closed=True, shared=shared)
            rt.put(item)
            app.bus.emit({"type": "antibodies"})
            self._json({"item": item, "shared": shared})
        elif key == ("POST", "react"):
            b = self._body()
            item = rt.timeline.get(str(b.get("item") or ""))
            if item is None:
                raise ApiError(404, "No such message.")
            emoji = str(b.get("emoji") or "")[:8]
            item = dict(item, reaction="" if item.get("reaction") == emoji else emoji)
            rt.put(item)
            self._json({"item": item})
        elif key == ("POST", "reveal"):
            app.reveal(bot_id)
            self._json({"ok": True})
        else:
            raise ApiError(404, "Not found.")


class Server:
    def __init__(self, app: App, port: int = 0):
        handler = type("BoundHandler", (Handler,), {"app": app})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.app = app
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="oomi-http", daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/?t={self.app.token}"

    def start(self) -> "Server":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
