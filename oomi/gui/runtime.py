"""One running bot: its own oomi agent on its own thread, reporting what happens as events for the window."""

from __future__ import annotations

import queue
import re
import threading
import time
import traceback
from concurrent.futures import Future
from pathlib import Path
from typing import Callable, Optional

from ..agent import Agent, NullUI, load_session, quick_command_for
from ..config import Config
from ..health import Health
from ..history import History
from ..keychain import get_key, get_secret
from ..llm import ModelError
from ..account import AccountStore
from ..memory import Memory, VoyageEmbedder
from ..schedule import ScheduleStore, parse_when
from ..safety import TRUST_LEVELS
from ..skills import SkillStore, general_store
from ..tools import server_tools
from .store import BotRecord, Registry, Timeline, new_id

PREVIEW_CHARS = 120
# A message that is only this, sent while the bot is working, stops the task instead of being queued as a note.
STOP_WORDS = re.compile(
    r"^\s*(?:(?:please|pls|plz)\s+)?(?:stop|halt|cancel|abort|quit|enough|wait|hold on|never ?mind|nvm)"
    r"(?:\s+(?:it|that|this|now|everything|please|pls|plz|right now))*\s*[.!]*\s*$", re.I)
OUTPUT_KEEP = 4000  # characters of each tool's output kept for the activity row's details
APPROVAL_MAX = 50_000
STEP_LABELS = {"run_shell": "Running", "read_file": "Reading", "write_file": "Writing", "edit_file": "Editing",
               "list_dir": "Looking in", "grep": "Searching", "web_search": "Searching the web",
               "web_fetch": "Reading a web page", "remember": "Saving a memory", "recall": "Checking its memory",
               "forget": "Forgetting", "load_skill": "Using a skill", "find_skill": "Looking for a skill",
               "propose_skill": "Proposing a skill", "skill_feedback": "Noting how a skill went",
               "search_history": "Searching past conversations"}  # an approval card shows the whole request up to this; longer ones can only be declined


class NoKey(Exception):
    pass


def build_memory(cfg: Config, scope: str = "") -> Memory:
    vkey = get_key("voyage")
    embedder = VoyageEmbedder(vkey, cfg.voyage_model, cfg.voyage_endpoint) if vkey else None
    return Memory(cfg.home / "memory.db", embedder=embedder, scope=scope)


# Notices from the engine mention terminal commands; in the app they become buttons.
_NOTICE_ACTIONS = [
    (re.compile(r"\s*\(raise it with /budget\)\.?"), "budget"),
    (re.compile(r"\s*\(/memory review\)"), "memory"),
    (re.compile(r"\s*\(/status\)"), "status"),
]


def notice_for_app(msg: str) -> tuple:
    msg = msg.replace("the session budget", "today's budget")  # in the app the limit is per bot per day
    for rx, action in _NOTICE_ACTIONS:
        if rx.search(msg):
            return rx.sub("", msg).strip(), action
    return msg, None


class GuiUI(NullUI):
    def __init__(self, rt: "BotRuntime"):
        self.rt = rt

    def text(self, s):
        self.rt.on_text(s)

    def thinking(self, s):
        self.rt.on_thinking(s)

    def end_message(self):
        self.rt.on_end_message()

    def tool_start(self, name, summary):
        self.rt.on_tool_start(name, summary)

    def tool_end(self, name, result, seconds):
        self.rt.on_tool_end(name, result.text, result.is_error, seconds)

    def server_tool(self, name, detail):
        self.rt.on_server_tool(name, detail)

    def notice(self, msg, level="info"):
        self.rt.on_notice(msg, level)

    def approve(self, tool, summary, reason, danger):
        return self.rt.on_approve(tool, summary, reason, danger)


class BotRuntime:
    def __init__(self, record: BotRecord, registry: Registry, home: Path, emit: Callable[[dict], None],
                 attention: Callable[[str], bool], llm_factory: Optional[Callable[[str], object]] = None,
                 events_lock=None, connectors=None, antibodies=None):
        self.record, self.registry, self.home = record, registry, Path(home)
        self.connectors = connectors  # connected apps (ConnectorStore); their tools are refreshed every turn
        self.antibodies = antibodies  # () -> the team's antibody Store on Atlas, or None while it isn't reachable
        self.emit_raw = emit
        # Held while live text changes and its event is sent, so a window reloading mid-reply gets a snapshot
        # that matches the event number it resumes from.
        self.events_lock = events_lock or threading.RLock()
        self.attention = attention  # attention(bot_id) -> True if the person is looking at this bot right now
        self.llm_factory = llm_factory
        self.timeline = Timeline(self.home / "bots" / record.id / "timeline.jsonl")
        self.agent: Optional[Agent] = None
        self.status = "idle"  # idle | working | waiting | stopping
        self.lock = threading.RLock()
        self.jobs: queue.Queue = queue.Queue()
        self.ui = GuiUI(self)
        # Live state of the current turn (also served to a window that reloads mid-turn)
        self.live_text_id: Optional[str] = None
        self.live_text = ""
        self.live_activity: Optional[dict] = None
        self.live_thinking = ""
        self.turn_started = 0.0
        self.pending: dict = {}  # approval id -> {"event", "answer"}
        self.new_llm = False
        self.budget_day = ""  # the day the agent's spending counts from (the limit starts over at midnight)
        self.running: Optional[Agent] = None  # the agent while a turn is executing (messages sent now join it)
        self.step = ""  # what it's doing right now, in a few words (the overview shows it)
        self.turn_reply_id: Optional[str] = None  # last reply written this turn (its cost is added at the end)
        self.stop_requested = False  # Stop pressed; applies to the current turn, or the one about to start
        self.deleted = False
        self.unattended = False  # a scheduled run is in progress: nobody is there to answer approval cards
        self.turn_error = ""  # why the last turn failed, if it did (read by scheduled runs)
        self.thread = threading.Thread(target=self._loop, name=f"bot-{record.id}", daemon=True)
        self.thread.start()

    # -- events -------------------------------------------------------------------------------------
    def emit(self, event_type: str, /, **data) -> None:
        if not self.deleted:
            self.emit_raw({"type": event_type, "bot": self.record.id, **data})

    def put(self, item: dict) -> dict:
        """Persist a timeline item (new or updated) and show it."""
        if not self.deleted:
            self.timeline.write(item)
            self.emit("item", item=item)
        return item

    def set_status(self, status: str) -> None:
        self.status = status
        self.emit("bot", bot_state=self.state())

    def state(self) -> dict:
        a = self.agent
        r = self.record
        return {
            "id": r.id, "name": r.name, "folder": r.folder, "avatar": r.avatar, "color": r.color, "trust": a.cfg.trust if a else r.trust,
            "model": r.model, "provider": r.provider, "effort": r.effort, "budget": r.budget, "private_folder": r.private_folder,
            "last_at": r.last_at, "preview": r.preview, "unread": r.unread, "status": self.status,
            "plan_pending": bool(a and a.prev_trust is not None),
            "tainted": bool(a and a.taint.active), "taint_sources": list(a.taint.sources) if a else [],
            "cost": round(a.cost.spent_usd, 4) if a else 0.0, "limit": a.cost.limit_usd if a else None,
            "changes": len(a.changes.journal) if a else 0, "folder_exists": Path(r.folder).is_dir(),
            "context_tokens": a.last_context_tokens if a else 0,
            "pinned": r.pinned, "section": r.section, "order": r.order, "instructions": r.instructions,
            "allow": [k[len("shell:"):] if k.startswith("shell:") else k for k in r.allow],
            "step": self.step if self.status != "idle" else "",
            "approval": self._first_approval(),
            "apps_off": list(r.apps_off),
        }

    def _first_approval(self) -> Optional[dict]:
        for entry in list(self.pending.values()):
            it = entry["item"]
            return {"id": it["id"], "tool": it["tool"], "summary": it["summary"][:400], "reason": it["reason"],
                    "danger": it["danger"], "too_long": bool(it.get("too_long")), "length": it.get("length", 0)}
        return None

    def set_step(self, step: str) -> None:
        if step != self.step:
            self.step = step[:160]
            self.emit("bot", bot_state=self.state())

    def live(self) -> dict:
        return {"text_id": self.live_text_id, "text": self.live_text, "activity": self.live_activity,
                "thinking": self.live_thinking[-300:], "status": self.status,
                "approvals": [p["item"] for p in self.pending.values()]}

    # -- engine callbacks (worker thread) ----------------------------------------------------------------
    def on_text(self, s: str) -> None:
        with self.events_lock:
            if self.live_text_id is None:
                self.live_text_id = new_id("a-")
                self.live_text = ""
                self.live_activity = None  # the next tool starts a new activity row
            self.live_text += s
            self.live_thinking = ""
            self.emit("delta", id=self.live_text_id, text=s)
        if self.step != "Writing a reply":
            self.set_step("Writing a reply")

    def on_thinking(self, s: str) -> None:
        self.live_thinking = (self.live_thinking + s)[-2000:]
        self.emit("thinking", text=s)
        if self.step != "Thinking":
            self.set_step("Thinking")

    def on_end_message(self) -> None:
        with self.events_lock:
            if self.live_text_id and self.live_text.strip():
                a = self.agent
                item = {"id": self.live_text_id, "type": "assistant", "text": self.live_text, "ts": time.time()}
                if a is not None and a.prev_trust is not None:
                    item["plan"] = True  # written in plan mode: the window offers Go ahead
                self.put(item)
                self.turn_reply_id = item["id"]
            elif self.live_text_id:
                self.emit("drop", id=self.live_text_id)
            self.live_text_id, self.live_text = None, ""

    MAX_TOOLS_PER_ROW = 10  # long tasks get several rows (and each saved row stays small)

    def _activity(self) -> dict:
        if self.live_activity is not None and len(self.live_activity["tools"]) >= self.MAX_TOOLS_PER_ROW and not any(
                t["state"] == "running" for t in self.live_activity["tools"]):
            self.live_activity = None
        if self.live_activity is None:
            self.live_activity = {"id": new_id("t-"), "type": "activity", "ts": time.time(), "tools": []}
        return self.live_activity

    def on_tool_start(self, name: str, summary: str) -> None:
        self.on_end_message()
        act = self._activity()
        act["tools"].append({"name": name, "summary": summary[:600], "state": "running"})
        self.put(act)
        self.set_step(STEP_LABELS.get(name, name) + (": " + " ".join(summary.split())[:120] if summary else ""))

    def on_tool_end(self, name: str, text: str, is_error: bool, seconds: float) -> None:
        act = self._activity()
        for t in reversed(act["tools"]):
            if t["name"] == name and t["state"] == "running":
                break
        else:
            t = {"name": name, "summary": ""}
            act["tools"].append(t)
        t["state"] = "error" if is_error else "ok"
        t["secs"] = round(seconds, 1)
        out = text.strip()
        t["out"] = out[:OUTPUT_KEEP] + (f"\n… ({len(out) - OUTPUT_KEEP:,} more characters)" if len(out) > OUTPUT_KEEP else "")
        self.put(act)

    def on_server_tool(self, name: str, detail: str) -> None:
        act = self._activity()
        act["tools"].append({"name": name, "summary": detail[:600], "state": "ok"})
        self.put(act)

    def on_notice(self, msg: str, level: str) -> None:
        self.on_end_message()
        text, action = notice_for_app(msg)
        item = {"id": new_id("n-"), "type": "notice", "text": text, "level": level, "ts": time.time()}
        if action:
            item["action"] = action
        self.put(item)
        if text.startswith("Memory:"):
            self.emit("memory_changed")

    def on_approve(self, tool: str, summary: str, reason: str, danger: bool) -> tuple:
        self.on_end_message()
        aid = new_id("q-")
        if self.unattended:
            # Hermes' unattended policy: nobody is watching a scheduled run, so anything that needs a yes is a no.
            note = "declined: scheduled runs can't ask for approval"
            self.put({"id": aid, "type": "approval", "tool": tool, "summary": summary[:APPROVAL_MAX], "reason": reason,
                      "danger": bool(danger), "state": "declined", "note": note, "ts": time.time(), "length": len(summary)})
            return "no", note
        # The card shows exactly what will run. Something too long to show in full can only be declined.
        item = {"id": aid, "type": "approval", "tool": tool, "summary": summary[:APPROVAL_MAX], "reason": reason,
                "danger": bool(danger), "state": "pending", "ts": time.time(), "length": len(summary)}
        if len(summary) > APPROVAL_MAX:
            item["too_long"] = True
        ev = threading.Event()
        with self.lock:
            self.pending[aid] = {"event": ev, "answer": ("no", ""), "item": item}
        self.put(item)
        self.set_status("waiting")
        if not self.attention(self.record.id):
            self.emit("notify", kind="approval", title=f"{self.record.name} needs your OK", body=summary[:160])
        agent = self.agent
        self.set_step("Waiting for your OK")
        asked_at, timed_out, timeout_min = time.time(), False, 0
        while not ev.wait(0.2):
            if self.stop_requested or (agent is not None and agent.cancel.is_set()):
                break
            # read each time round, so changing the setting applies to an approval that's already waiting
            timeout_min = getattr(agent.cfg, "approval_timeout_minutes", 0) if agent is not None else 0
            if timeout_min and time.time() - asked_at > timeout_min * 60:
                timed_out = True
                break
        with self.lock:
            entry = self.pending.pop(aid, None)
        choice, note = entry["answer"] if entry and ev.is_set() else ("no", "")
        item = dict(item, state={"yes": "allowed", "always": "always", "bot": "always for this bot"}.get(choice, "declined"))
        if timed_out and not ev.is_set():
            item["state"] = "timed out"
            note = f"Nobody answered within {timeout_min} minutes, so it was declined automatically."
        elif not ev.is_set():
            item["state"] = "stopped"
        if note:
            item["note"] = note
        self.put(item)
        self.set_status("stopping" if self.stop_requested else "working")
        return choice, note

    def answer(self, aid: str, choice: str, note: str = "") -> bool:
        with self.lock:
            entry = self.pending.get(aid)
            if not entry:
                return False
            item = entry["item"]
            if choice in ("always", "bot") and (item["danger"] or item["tool"] == "confirm"):
                choice = "yes"
            if choice not in ("yes", "always", "bot", "no") or item.get("too_long"):
                choice = "no"
            entry["answer"] = (choice, note.strip())
            entry["event"].set()
        return True

    # -- the worker -----------------------------------------------------------------------------------
    def submit(self, kind: str, *args) -> Future:
        fut: Future = Future()
        self.jobs.put((kind, args, fut))
        return fut

    def _loop(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            kind, args, fut = job
            try:
                fut.set_result(getattr(self, "_job_" + kind)(*args))
            except BaseException as e:  # noqa: BLE001 - report everything to the caller, keep the worker alive
                fut.set_exception(e)

    def shutdown(self, timeout: float = 15.0) -> None:
        """Stop work, learn from the conversation, save. Called when the app quits."""
        self.stop()
        fut = self.submit("finish")
        self.jobs.put(None)
        try:
            fut.result(timeout)
        except Exception:
            pass

    def _config(self) -> Config:
        cfg = Config.load()
        r = self.record
        cfg.trust = r.trust if r.trust in TRUST_LEVELS else cfg.trust
        if r.model:
            cfg.model = r.model
        if r.effort:
            cfg.effort = r.effort
        if r.budget and r.budget > 0:
            cfg.max_session_cost_usd = float(r.budget)
        return cfg

    def _llm(self):
        # A provider that has since been removed falls back to Anthropic
        p = next((p for p in Config.load().providers if p.get("id") == self.record.provider), None) if self.record.provider else None
        key = get_secret(f"provider:{p['id']}") if p else get_key("anthropic")
        if not key:
            raise NoKey(f"There's no API key for {p['name']}. Remove it in Settings, Providers, and add it again." if p else "")
        if self.llm_factory:
            return self.llm_factory(key)
        from ..llm import LLM

        return LLM(key, base_url=p["base_url"] if p else None)

    def ensure_agent(self, fresh: bool = False) -> Agent:
        """Worker thread only (the agent's SQLite connections belong to it)."""
        if self.agent is not None and not fresh:
            if self.new_llm:
                self.agent.llm = self._llm()
                self.new_llm = False
            return self.agent
        cfg = self._config()
        cfg.bot_instructions = self.record.instructions
        cfg.user_profile = AccountStore(cfg.home).prompt_text()
        llm = self._llm()
        folder = Path(self.record.folder)
        folder.mkdir(parents=True, exist_ok=True)
        session = None if fresh or not self.record.session_id else load_session(cfg.home, folder, self.record.session_id)
        skills = SkillStore(cfg.home / "bots" / self.record.id / "skills", shared=general_store(cfg.home))
        agent = Agent(cfg, llm, self.ui, folder, build_memory(cfg, self.record.id), skills, session,
                      history=History(cfg.home / "history.db"), health=Health(cfg.home))
        self.budget_day = time.strftime("%Y-%m-%d")
        agent.cost.spent_usd = self._spent_today()  # in the app, the budget is per day, across conversations
        if session:
            agent.cfg.trust = self.record.trust if self.record.trust in TRUST_LEVELS else agent.cfg.trust
        agent.session_allow.update(k for k in self.record.allow if isinstance(k, str) and k.startswith("shell:"))
        agent.on_always_allow = self._remember_allow
        self.agent = agent
        self.new_llm = False
        if self.record.session_id != agent.session_id or agent.session_id not in self.record.sessions:
            self.record.session_id = agent.session_id
            if agent.session_id not in self.record.sessions:
                self.record.sessions.append(agent.session_id)  # usage is counted per bot across all of them
            self.registry.save()
        return agent

    def _spent_today(self) -> float:
        sessions = {s for s in [*self.record.sessions, self.record.session_id] if s}
        today = time.strftime("%Y-%m-%d")
        rows = Health(self.home)._usage_rows(time.time() - 86400)
        return sum(float(r.get("usd") or 0) for r in rows if r.get("day") == today and r.get("session") in sessions)

    def _new_day(self, agent: Agent) -> None:
        """A new day: the spending limit starts over, and any extra allowed yesterday is dropped."""
        if self.budget_day == time.strftime("%Y-%m-%d"):
            return
        self.budget_day = time.strftime("%Y-%m-%d")
        agent.cost.spent_usd = self._spent_today()
        agent.cost.limit_usd = self.record.budget or Config.load().max_session_cost_usd
        agent.cost.warned = False

    def _attach_watcher(self, agent: Agent) -> None:
        """Give the agent the team's antibodies once Atlas is reachable: proven fixes for errors it hits, and
        candidates from errors it fixes."""
        if self.antibodies is None:
            return
        store = self.antibodies()
        # Keep the watcher on the team's current connection: after a reconnect, fixes must go where Share looks
        if store is None or (agent.watcher is not None and agent.watcher.store is store):
            return
        from ..antibodies import Watcher
        from ..llm import text_of

        def describe(system: str, prompt: str) -> str:
            r = agent._complete(system=system, prompt=prompt, max_tokens=300)
            return text_of(r).strip() if r else ""

        agent.watcher = Watcher(store, Path(self.record.folder), describe=describe,
                                notify=self._watcher_notice)

    def _watcher_notice(self, msg: str) -> None:
        # The app posts its own "ready to share" line with a Share button at the end of the turn
        if "ready for your review" not in msg:
            self.ui.notice(msg, "info")

    def _save_antibody_candidates(self, agent: Agent) -> None:
        """Fixes found this turn wait for you to click Share on the notice in the chat, unless Settings, Antibodies
        says to share them automatically."""
        w = agent.watcher
        if w is None or not w.candidates:
            return
        auto = Config.load().antibody_auto_share
        saved = []
        while w.candidates:
            cand = w.candidates.pop(0)
            try:
                saved.append(str(w.store.save(cand, approved=auto)))
            except Exception as e:  # noqa: BLE001
                self.ui.notice(f"Couldn't save the antibody: {e}", "warn")
        if saved:
            self.emit("antibodies", pending=0 if auto else len(saved))
            many = f"{len(saved)} fixes" if len(saved) > 1 else "a fix"
            item = {"id": new_id("n-"), "type": "notice", "level": "info", "ts": time.time(), "action": "antibody",
                    "antibodies": saved, "text": f"{'A fix is' if len(saved) == 1 else f'{len(saved)} fixes are'} "
                    "ready to share with your team."}
            if auto:  # already shared: no button
                item.update(closed=True, shared=len(saved), auto=True, text=f"Shared {many} with your team automatically.")
            self.put(item)

    def _turn(self, fn) -> None:
        """Run one request (and any messages sent while it worked) with the window kept up to date."""
        self.turn_started = time.time()
        self.turn_error = ""
        if self._consume_stop():
            return
        self.set_status("working")
        try:
            agent = self.ensure_agent()
            self._new_day(agent)
            if self.connectors is not None:
                agent.set_app_tools(self.connectors.tools(off=set(self.record.apps_off)))
            self._attach_watcher(agent)
        except NoKey as e:
            self.turn_error = "no API key"
            self._error(str(e) or "Add your Anthropic API key in Settings to start.", action="settings")
            self._go_idle()
            return
        except Exception as e:  # e.g. the folder is on a drive that isn't connected
            self.turn_error = f"couldn't start: {type(e).__name__}: {e}"[:200]
            self._error(f"{self.record.name} couldn't start: {type(e).__name__}: {e}")
            traceback.print_exc()
            self._go_idle()
            return
        with self.lock:
            self.running = agent
        if self._consume_stop():
            return
        cost0 = agent.cost.spent_usd
        try:
            while True:
                try:
                    fn(agent)
                except ModelError as e:
                    self.turn_error = f"model call failed: {e}"[:200]
                    self.on_end_message()
                    if e.kind == "auth":
                        self._error("Your Anthropic API key was rejected. Replace it in Settings.", action="settings")
                    else:
                        self._error(f"The model call failed: {e}", action="status")
                    self._notify_failed(str(e))
                except Exception as e:  # the agent repaired its history already
                    self.turn_error = f"{type(e).__name__}: {e}"[:200]
                    self.on_end_message()
                    self._error(f"Something went wrong: {type(e).__name__}: {e}")
                    traceback.print_exc()
                    self._notify_failed(f"{type(e).__name__}: {e}")
                self.on_end_message()
                self.live_activity = None
                with self.lock:
                    if agent.interrupted or self.stop_requested:
                        dropped = agent.take_inbox()  # stopped: don't carry on with messages sent meanwhile
                        self.put({"id": new_id("n-"), "type": "notice", "level": "info", "ts": time.time(),
                                  "text": "Stopped." + (" The message you sent while it was working wasn't used; "
                                                        "send it again if you still want it." if dropped else "")})
                        self._go_idle_locked()
                        break
                    leftover = agent.take_inbox()
                    if not leftover:
                        self._go_idle_locked()
                        break
                text = "\n\n".join(leftover)
                fn = lambda a, t=text: a.run(t)  # noqa: E731
        finally:
            with self.lock:
                if self.status != "idle":  # something unexpected escaped; never leave the bot stuck
                    self._go_idle_locked()
            self._save_antibody_candidates(agent)
        self.live_thinking = ""
        try:
            self._after_turn(agent, cost0)
        except Exception:
            traceback.print_exc()

    def _notify_failed(self, detail: str) -> None:
        if not self.attention(self.record.id):
            self.emit("notify", kind="failed", title=f"{self.record.name} hit a problem", body=detail[:160])

    def _remember_allow(self, key: str) -> None:
        """You chose "always allow for this bot": keep it for every future conversation of this bot."""
        if key not in self.record.allow:
            self.record.allow.append(key)
            if not self.deleted:
                self.registry.save()

    def _after_turn(self, agent: Agent, cost0: float) -> None:
        took = time.time() - self.turn_started
        self.step = ""
        if self.turn_reply_id:  # the cost of the whole request goes under its last reply
            item = self.timeline.get(self.turn_reply_id)
            if item is not None:
                self.put(dict(item, cost=round(agent.cost.spent_usd - cost0, 4), secs=round(took, 1)))
            self.turn_reply_id = None
        last = next((i for i in reversed(self.timeline.items(30)) if i.get("type") in ("assistant", "notice")), None)
        if last is not None:
            self.record.preview = last.get("text", "")[:PREVIEW_CHARS]
        self.record.last_at = time.time()
        if not self.attention(self.record.id):
            self.record.unread += 1
            if took >= 8 and not self.unattended:  # a scheduled run announces itself
                self.emit("notify", kind="done", title=f"{self.record.name} finished",
                          body=self.record.preview[:160] or "Done")
        if not self.deleted:
            self.registry.save()
        self.emit("turn_end", cost=round(agent.cost.spent_usd - cost0, 4), seconds=round(took, 1))
        self.emit("bot", bot_state=self.state())

    def _consume_stop(self) -> bool:
        """Stop was pressed after this request was sent but before it started: show that and do nothing."""
        with self.lock:
            if not self.stop_requested:
                return False
            self.put({"id": new_id("n-"), "type": "notice", "level": "info", "ts": time.time(), "text": "Stopped."})
            self._go_idle_locked()
        self.emit("bot", bot_state=self.state())
        return True

    def _go_idle_locked(self) -> None:
        self.status = "idle"
        self.running = None
        self.stop_requested = False
        if self.agent is not None:
            self.agent.cancel.clear()  # a stop that arrived as the turn ended mustn't hit the next one
            self.agent.take_inbox()

    def _go_idle(self) -> None:
        with self.lock:
            self._go_idle_locked()
        self.emit("bot", bot_state=self.state())

    def _error(self, text: str, action: Optional[str] = None) -> None:
        item = {"id": new_id("n-"), "type": "notice", "text": text, "level": "error", "ts": time.time()}
        if action:
            item["action"] = action
        self.put(item)

    # -- jobs (run on the worker thread) ------------------------------------------------------------------
    def _job_run(self, text: str, attachments: list, plan: bool, quote: str = "") -> None:
        def go(agent: Agent) -> None:
            if plan:
                agent.plan()
            agent.run(text, attachments, quote)

        self._turn(go)

    def _job_quick(self, command: str) -> None:
        self._turn(lambda agent: agent.quick_command(command))

    def _job_scheduled(self, sid: str, describe: str, prompt: str) -> dict:
        """A due schedule: the prompt runs as a turn in this bot's conversation, with approvals declined."""
        self.unattended = True
        try:
            self.put({"id": new_id("n-"), "type": "notice", "level": "info", "ts": time.time(),
                      "text": f"Scheduled: {describe}", "scheduled": sid})
            self.put({"id": new_id("u-"), "type": "user", "text": prompt, "ts": time.time(), "scheduled": True})
            self._turn(lambda agent: agent.run(prompt))
            a = self.agent
            if self.turn_error:
                res = {"ok": False, "error": self.turn_error}
            elif a is not None and a.interrupted:
                res = {"ok": True, "stopped": True}
            elif a is not None and a.cost.exhausted:
                res = {"ok": False, "error": "today's budget is used up"}
            else:
                res = {"ok": True}
            if a is None or a.cfg.notify:
                self.emit("notify", title=f"{self.record.name}: " + ("scheduled task done" if res["ok"] else "scheduled task failed"),
                          body=(self.record.preview or describe)[:160])
            return res
        finally:
            self.unattended = False

    def begin_scheduled(self, sid: str, describe: str, prompt: str):
        """Start a scheduled run if the bot is idle right now (a Future), else None: the scheduler tries again."""
        with self.lock:
            if self.deleted or self.status != "idle" or not self.jobs.empty():
                return None
            self.status = "working"
        self.emit("bot", bot_state=self.state())
        return self.submit("scheduled", sid, describe, prompt)

    def _job_go(self, edits: str) -> None:
        self._turn(lambda agent: agent.go(edits))

    def _job_finish(self) -> None:
        if self.agent is not None and self.agent.messages:
            try:
                self.agent.finish()
            except Exception:
                self.agent.save()

    def _job_compact(self) -> bool:
        """Summarize older turns now (Compact now in bot settings)."""
        if self.agent is None and not self.record.session_id:
            return False  # nothing to compact, and no reason to start a conversation for it
        agent = self.ensure_agent()
        self.set_status("working")
        self.set_step("Summarizing older turns")
        try:
            return bool(agent.compact())
        finally:
            self.step = ""
            with self.lock:
                self.status = "idle"
            self.emit("bot", bot_state=self.state())

    def _job_fresh(self) -> None:
        if self.agent is not None and self.agent.messages:
            try:
                self.agent.finish()
            except Exception:
                self.agent.save()
        self.agent = None
        self.record.session_id = ""
        self.registry.save()
        self.put({"id": new_id("n-"), "type": "divider", "text": "Started fresh. Earlier messages are no longer "
                  "part of the conversation, but what oomi learned about you stays.", "ts": time.time()})
        self.emit("bot", bot_state=self.state())

    def _job_undo(self, everything: bool, confirmed: bool) -> dict:
        from ..changes import UndoError

        if self.agent is None:
            return {"done": [], "message": "Nothing to undo."}
        done = []
        try:
            while self.agent.changes.journal:
                if self.agent.changes.next_kind() == "snapshot" and not confirmed:
                    return {"done": done, "needs_confirm": self.agent.changes.peek()}
                msg = self.agent.undo()
                if not msg:
                    break
                done.append(msg)
                confirmed = False  # each restore asks separately
                if not everything:
                    break
        except UndoError as e:
            return {"done": done, "error": str(e)}
        finally:
            if done:
                self.put({"id": new_id("n-"), "type": "notice", "level": "info", "ts": time.time(),
                          "text": "Undid: " + "; ".join(done)})
                self.emit("bot", bot_state=self.state())
        return {"done": done}

    def _job_call(self, fn):
        return fn()

    # -- actions from the window (any thread) ---------------------------------------------------------------
    def send(self, text: str, attachments: list, plan: bool = False, display: Optional[dict] = None,
             quote: str = "") -> dict:
        """display: the timeline item to show for this message (text, attachments, reply)."""
        item = {"id": new_id("u-"), "type": "user", "text": text, "ts": time.time()}
        if display:
            item.update({k: v for k, v in display.items() if k in ("attachments", "reply", "shown")})
        quick = None if (attachments or plan or quote) else quick_command_for(Config.load(), text)
        with self.lock:
            running = self.running
            asked_to_stop = (running is not None and self.status in ("working", "waiting") and not attachments
                             and not plan and not quote and bool(STOP_WORDS.match(text)))
            if asked_to_stop:
                item["while_working"] = True
                self.put(item)
        if asked_to_stop:
            self.stop()  # "stop" typed in the chat means stop, not "add a note after the current step"
            return item
        with self.lock:
            running = self.running
            # Join the task that's running right now. Anything else (a turn still queued, a fresh start, a stop in
            # progress, a file or a quote) waits its turn as a request of its own.
            if (running is not None and self.status in ("working", "waiting") and not self.stop_requested and not quick
                    and not attachments and not plan and not quote):
                item["while_working"] = True
                self.put(item)
                running.interject(text)
                return item
            self.put(item)
            if self.status == "idle":
                self.status = "working"
        self.record.preview = "You: " + (display or {}).get("shown", text)[:PREVIEW_CHARS]
        self.record.last_at = time.time()
        self.registry.save()
        self.emit("bot", bot_state=self.state())
        if quick:
            self.submit("quick", quick[1])  # a /name from quick_commands: run its command, no model call
        else:
            self.submit("run", text, attachments, plan, quote)
        return item

    def go(self, edits: str = "") -> dict:
        shown = "Go ahead with the plan." + (f" Changes: {edits}" if edits else "")
        item = self.put({"id": new_id("u-"), "type": "user", "text": shown, "ts": time.time()})
        with self.lock:
            if self.status == "idle":
                self.status = "working"
        self.submit("go", edits)
        return item

    def stop(self) -> None:
        with self.lock:
            if self.status == "idle":
                return
            self.status = "stopping"
            self.stop_requested = True
            agent = self.running or self.agent
            if agent is not None:
                agent.request_stop()  # under the lock, so it can't land after the turn has gone idle
        self.emit("bot", bot_state=self.state())

    def set_instructions(self, text: str) -> None:
        text = text.strip()[:8000]
        if text == self.record.instructions:
            return
        self.record.instructions = text
        if self.agent is not None:
            self.agent.cfg.bot_instructions = text
            # The system prompt stays fixed for this conversation (it's cached), so tell it once in the next turn.
            self.agent.pending_notes.append(
                "The user changed your standing instructions for this bot. They now are: " + (text or "(none)"))

    def set_trust(self, level: str) -> None:
        if level not in TRUST_LEVELS:
            raise ValueError("unknown trust level")
        self.record.trust = level
        if self.agent is not None:
            self.agent.set_trust(level)

    def apply_settings(self, cfg: Config) -> None:
        """App-wide settings changed: update the running agent (per-bot choices still win)."""
        a = self.agent
        if a is None:
            return
        for name in ("fallback_models", "web_search", "notify", "memory_learn", "sandbox_extra_writable", "small_model",
                     "deny_commands", "protected_write_names", "redact_secrets", "approval_timeout_minutes",
                     "max_task_minutes", "compact_at_tokens", "max_steps_per_turn", "show_thinking"):
            setattr(a.cfg, name, getattr(cfg, name))
        if not self.record.model:
            a.cfg.model = cfg.model
        if not self.record.effort:
            a.cfg.effort = cfg.effort
        if not self.record.budget:
            a.cost.limit_usd = cfg.max_session_cost_usd
        a.tool_defs = [t.definition() for t in a.tools.values()] + server_tools(a.cfg)


class Scheduler:
    """Ticks every 30 seconds and starts the schedules that are due, on bots that are idle."""

    MISSED_MAX = 12 * 3600  # a run missed by longer than this (app closed, Mac asleep) is skipped, not run late
    MAX_FAILS = 3

    def __init__(self, store: ScheduleStore, runtimes: Callable[[], dict], emit: Callable[[dict], None],
                 tick_seconds: float = 30.0):
        self.store, self.runtimes, self.emit, self.tick_seconds = store, runtimes, emit, tick_seconds
        self.stopping = threading.Event()
        self.thread: Optional[threading.Thread] = None
        if tick_seconds > 0:
            self.thread = threading.Thread(target=self._loop, name="oomi-schedules", daemon=True)
            self.thread.start()

    def _loop(self) -> None:
        while not self.stopping.is_set():  # the first pass is the startup catch-up
            try:
                self.tick()
            except Exception:
                traceback.print_exc()
            self.stopping.wait(self.tick_seconds)

    def stop(self) -> None:
        self.stopping.set()

    def changed(self, bot_id: str) -> None:
        self.emit({"type": "schedules", "bot": bot_id})

    def tick(self, now: Optional[float] = None) -> int:
        now = now or time.time()
        started = 0
        for s in self.store.due(now):
            rt = self.runtimes().get(s.bot)
            if rt is None or rt.deleted:
                continue
            w = parse_when(s.when)
            if now - s.next_run > self.MISSED_MAX:
                self.store.update(s.id, next_run=w.next_after(now), last_status="skipped: missed while oomi was closed")
                self.changed(s.bot)
                continue
            before = {"last_run": s.last_run, "next_run": s.next_run, "last_status": s.last_status}
            self.store.update(s.id, last_run=now, next_run=w.next_after(now), last_status="running")
            fut = rt.begin_scheduled(s.id, w.describe(), s.prompt)
            if fut is None:
                self.store.update(s.id, **before)  # busy: try again next tick
                continue
            started += 1
            fut.add_done_callback(lambda f, sid=s.id, rt=rt, desc=w.describe(): self._finished(sid, rt, desc, f))
            self.changed(s.bot)
        return started

    def _finished(self, sid: str, rt: BotRuntime, describe: str, fut: Future) -> None:
        s = self.store.get(sid)
        if s is None:
            return
        try:
            res = fut.result()
        except BaseException as e:  # noqa: BLE001
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("stopped"):
            self.store.update(sid, last_status="stopped")
        elif res.get("ok"):
            self.store.update(sid, last_status="ok", fails=0)
        else:
            fails = s.fails + 1
            off = fails >= self.MAX_FAILS
            self.store.update(sid, last_status=f"failed: {res.get('error', 'unknown error')}", fails=fails,
                              **({"enabled": False} if off else {}))
            if off:
                rt.put({"id": new_id("n-"), "type": "notice", "level": "warn", "ts": time.time(),
                        "text": f"The schedule \"{describe}\" was turned off after {self.MAX_FAILS} failures in a row. "
                                f"Turn it back on when you've looked at why."})
        self.changed(s.bot)
