import copy
import itertools
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from oomi.agent import Agent, NullUI
from oomi.config import Config
from oomi.gui.server import App, Server
from oomi.memory import Memory
from oomi.skills import SkillStore

_ids = itertools.count(1)


def text(t):
    return {"type": "text", "text": t}


def thinking(t="(thought)"):
    return {"type": "thinking", "thinking": t, "signature": "sig-" + t[:8]}


def tool(name, inp, tid=None):
    return {"type": "tool_use", "id": tid or f"toolu_{next(_ids):04d}", "name": name, "input": inp}


def resp(*blocks, stop=None, usage=None, model="claude-sonnet-5-5"):
    if stop is None:
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"model": model, "stop_reason": stop, "content": list(blocks),
            "usage": usage or {"input_tokens": 1000, "output_tokens": 100}}


class FakeLLM:
    """Plays back scripted responses. An entry may be a dict or a callable(messages) -> dict."""

    def __init__(self, script, summary=None, complete_script=None):
        self.script = list(script)
        self.calls = []
        self.complete_calls = []
        self.summary = summary or {"summary": "## Goal\nTest goal", "durable_facts": []}
        self.complete_script = list(complete_script or [])

    def stream(self, **kw):
        self.calls.append({k: copy.deepcopy(v) for k, v in kw.items() if not callable(v)})
        if not self.script:
            raise AssertionError("FakeLLM script exhausted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        r = item(kw["messages"]) if callable(item) else item
        for b in r["content"]:
            if kw.get("check"):
                kw["check"]()
            if b["type"] == "text" and kw.get("on_text"):
                kw["on_text"](b["text"])
        return copy.deepcopy(r)

    def complete(self, **kw):
        import json

        self.complete_calls.append(kw)
        if self.complete_script:
            item = self.complete_script.pop(0)
            if isinstance(item, Exception):
                raise item
            payload = item(kw) if callable(item) else item
        else:
            payload = self.summary
        body = payload if isinstance(payload, str) else json.dumps(payload)
        return {"model": kw["model"], "stop_reason": "end_turn", "content": [text(body)],
                "usage": {"input_tokens": 5000, "output_tokens": 500}}


class ScriptedUI(NullUI):
    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.asked = []
        self.notices = []
        self.out = []

    def text(self, s):
        self.out.append(s)

    def notice(self, msg, level="info"):
        self.notices.append((level, msg))

    def approve(self, tool, summary, reason, danger):
        self.asked.append({"tool": tool, "summary": summary, "reason": reason, "danger": danger})
        return self.answers.pop(0) if self.answers else ("no", "")


def validate_conversation(messages):
    """Assert the history is something the Messages API would accept."""
    assert messages, "empty conversation"
    assert messages[0]["role"] == "user"
    for i, m in enumerate(messages):
        assert m["content"], f"message {i} has empty content"
        if i:
            assert m["role"] != messages[i - 1]["role"], f"roles don't alternate at {i}"
        if m["role"] == "assistant":
            ids = {b["id"] for b in m["content"] if b.get("type") == "tool_use"}
            if ids:
                assert i + 1 < len(messages), f"tool_use at {i} has no results"
                nxt = messages[i + 1]
                got = {b["tool_use_id"] for b in nxt["content"] if b.get("type") == "tool_result"}
                assert got == ids, f"tool_result ids {got} != tool_use ids {ids} at {i}"
        if m["role"] == "user" and isinstance(m["content"], list):
            res = {b["tool_use_id"] for b in m["content"] if b.get("type") == "tool_result"}
            if res:
                prev = messages[i - 1]
                assert res <= {b["id"] for b in prev["content"] if b.get("type") == "tool_use"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("OOMI_HOME", str(home))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("print('hello')\n")

    class Env:
        pass

    e = Env()
    e.home, e.ws, e.tmp = home, Path(os.path.realpath(ws)), tmp_path
    home.mkdir(parents=True, exist_ok=True)
    e.memory = Memory(home / "memory.db")
    e.skills = SkillStore(home / "skills")

    def make(script, answers=None, summary=None, complete_script=None, history=False, **overrides):
        cfg = Config()
        cfg.sandbox = False
        cfg.memory_auto_k = 5
        for k, v in overrides.items():
            setattr(cfg, k, v)
        e.cfg = cfg
        e.llm = FakeLLM(script, summary, complete_script)
        e.ui = ScriptedUI(answers)
        from oomi.history import History

        e.history = History(home / "history.db") if history else None
        e.agent = Agent(cfg, e.llm, e.ui, ws, e.memory, e.skills, history=e.history)
        return e.agent

    e.make = make
    return e


# ------------------------------------------------------------------------------------------------ the app's server
def _wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def server(tmp_path, monkeypatch):
    home = tmp_path / "oomi"
    user = tmp_path / "user"
    user.mkdir()
    monkeypatch.setenv("OOMI_HOME", str(home))
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-test-0000000000000000000000")
    llm = FakeLLM([])
    app = App(home=home, llm_factory=lambda key: llm, key_checker=lambda k: (True, "Key accepted."), connect_team=False)
    srv = Server(app).start()

    class S:
        pass

    s = S()
    s.app, s.llm, s.home, s.user, s.port = app, llm, home, user, srv.port
    s.base = f"http://127.0.0.1:{srv.port}"

    def call(method, path, body=None, token=True, host=None, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(s.base + "/api/" + path, data=data, method=method)
        if token:
            req.add_header("X-Oomi-Token", app.token)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                out = r.read()
                return r.status, (out if raw else json.loads(out or b"{}"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def wait_idle(bot_id, timeout=10):
        rt = app.runtimes[bot_id]
        end = time.time() + timeout
        time.sleep(0.05)
        while time.time() < end:
            if rt.status == "idle" and rt.jobs.empty():
                return
            time.sleep(0.02)
        raise AssertionError(f"bot still {rt.status}")

    s.call, s.wait_idle = call, wait_idle
    yield s
    srv.stop()
    for rt in app.runtimes.values():
        rt.jobs.put(None)


# ------------------------------------------------------------------------------------------------ antibodies
@pytest.fixture
def db():
    """A team database in memory (mongomock): Atlas Search isn't there, so stores match in Python."""
    import mongomock

    from oomi import atlas

    database = mongomock.MongoClient(tz_aware=True)["oomi_test"]
    atlas.ensure_collections(database)
    return database


@pytest.fixture
def team_store(db):
    from oomi.antibodies import Store

    return Store(db, team="encode-london", user="Ivan", device="mac", use_atlas_search=False)
