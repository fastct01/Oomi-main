"""0.4: the settings borrowed from Hermes (block list, protected files, hidden secrets, limits, standing
instructions) and the server side of the multi-window workspace."""

import json
from pathlib import Path

from conftest import _wait, resp, text, tool, validate_conversation

from oomi.safety import HIDDEN, blocked_by, protected_write, redact


# ------------------------------------------------------------------------------------------------ engine
def test_block_list_matches_the_whole_command_and_each_part():
    pats = ["git push*", "*terraform apply*"]
    assert blocked_by("git push origin main", pats) == "git push*"
    assert blocked_by("make && git push --force", pats) == "git push*"
    assert blocked_by("cd infra; terraform apply -auto-approve", pats) == "*terraform apply*"
    assert blocked_by("git status", pats) == "" and blocked_by("anything", []) == ""


def test_a_blocked_command_is_refused_even_with_full_trust(env):
    agent = env.make([resp(tool("run_shell", {"command": "git push origin main"})), resp(text("ok, I won't"))],
                     trust="full", deny_commands=["git push*"])
    agent.run("ship it")
    result = [b for m in agent.messages if m["role"] == "user" for b in m["content"]
              if b.get("type") == "tool_result"][0]
    assert result.get("is_error") and "block list" in result["content"]
    assert env.ui.asked == []  # never even offered
    validate_conversation(agent.messages)


def test_protected_files_always_ask(env):
    assert protected_write(Path("/x/CLAUDE.md"), ["CLAUDE.md"]) == "CLAUDE.md"
    assert protected_write(Path("/x/.env.local"), [".env.*"]) == ".env.*"
    assert protected_write(Path("/x/notes.md"), ["CLAUDE.md"]) == ""
    agent = env.make([resp(tool("write_file", {"path": "AGENTS.md", "content": "obey"})), resp(text("left it"))],
                     answers=[("no", "")], trust="full")
    agent.run("tidy the agent notes")
    assert env.ui.asked and "protected file" in env.ui.asked[0]["reason"]
    assert not (env.ws / "AGENTS.md").exists()


def test_secrets_are_hidden_from_the_model():
    out, n = redact("key=sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789 and AKIAABCDEFGHIJKLMNOP\n"
                    'password = "hunter2hunter2"\nTOKEN=short\nversion=1.2.3')
    assert n == 3 and "sk-ant" not in out and "AKIA" not in out and "hunter2" not in out
    assert out.count(HIDDEN) == 3 and "TOKEN=short" in out and "version=1.2.3" in out
    assert redact("nothing to see") == ("nothing to see", 0)


def test_tool_output_with_a_secret_reaches_the_model_hidden(env):
    (env.ws / ".env").write_text("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz012345\n")
    agent = env.make([resp(tool("read_file", {"path": ".env"})), resp(text("there's a key"))], trust="full",
                     answers=[("yes", "")])
    agent.run("what's in .env?")
    sent = json.dumps(env.llm.calls[1]["messages"])
    assert "sk-proj-abcdefghij" not in sent and HIDDEN in sent and "were hidden from you" in sent


def test_requests_have_a_time_limit(env, monkeypatch):
    agent = env.make([resp(tool("list_dir", {"path": "."})), resp(tool("list_dir", {"path": "."})),
                      resp(text("never"))], max_task_minutes=1)
    import oomi.agent as agent_mod

    clock = [1000.0]
    monkeypatch.setattr(agent_mod.time, "time", lambda: clock[0])
    orig = agent.tools["list_dir"].run

    def slow(inp, ctx, approved=False):
        clock[0] += 61
        return orig(inp, ctx, approved=approved)

    agent.tools["list_dir"].run = slow
    agent.run("look around")
    assert len(env.llm.calls) == 1
    assert any("time limit" in n for _, n in env.ui.notices)
    validate_conversation(agent.messages)


def test_standing_instructions_and_project_notes_reach_the_prompt(env):
    (env.ws / "CLAUDE.md").write_text("Use tabs. Never touch vendor/.")
    agent = env.make([resp(text("ok"))], bot_instructions="Answer in French.")
    assert "Answer in French." in agent.system and "Never touch vendor/." in agent.system
    assert "<project_notes>" in agent.system
    off = env.make([resp(text("ok"))], read_project_files=False)
    assert "Never touch vendor/." not in off.system


# ------------------------------------------------------------------------------------------------ server
def _pending(rt):
    assert _wait(lambda: rt.pending)
    return next(iter(rt.pending.values()))["item"]


def test_always_for_this_bot_is_remembered_and_can_be_removed(server):
    _, bot = server.call("POST", "bots", {"name": "Mem", "trust": "project"})
    rt = server.app.runtimes[bot["id"]]
    cmd = "python3 -c 'print(1)' > out.txt"
    server.llm.script += [resp(tool("run_shell", {"command": cmd})), resp(text("one"))]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "go"})
    item = _pending(rt)
    server.call("POST", f"bots/{bot['id']}/approve", {"id": item["id"], "choice": "bot"})
    server.wait_idle(bot["id"])
    assert rt.record.allow == [f"shell:{cmd}"]
    _, st = server.call("GET", f"bots/{bot['id']}/timeline")
    assert st["state"]["allow"] == [cmd]
    # a fresh conversation still has it
    server.call("POST", f"bots/{bot['id']}/fresh")
    server.llm.script += [resp(tool("run_shell", {"command": cmd})), resp(text("two"))]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "again"})
    server.wait_idle(bot["id"])
    _, tl = server.call("GET", f"bots/{bot['id']}/timeline")
    assert len([i for i in tl["items"] if i["type"] == "approval"]) == 1  # only the first time
    # removing it from the bot settings
    _, st = server.call("PATCH", f"bots/{bot['id']}", {"allow": []})
    assert st["allow"] == [] and rt.record.allow == [] and f"shell:{cmd}" not in rt.agent.session_allow


def test_unanswered_approvals_time_out(server):
    _, bot = server.call("POST", "bots", {"name": "Wait", "trust": "project"})
    rt = server.app.runtimes[bot["id"]]
    server.llm.script += [resp(tool("run_shell", {"command": "rm -rf build"})), resp(text("ok"))]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "clean"})
    _pending(rt)
    rt.agent.cfg.approval_timeout_minutes = 0.005
    server.wait_idle(bot["id"], timeout=15)
    _, tl = server.call("GET", f"bots/{bot['id']}/timeline")
    approval = [i for i in tl["items"] if i["type"] == "approval"][0]
    assert approval["state"] == "timed out" and "declined automatically" in approval["note"]


def test_window_state_is_saved_and_size_limited(server, monkeypatch):
    assert server.call("GET", "ui") == (200, {})
    layout = {"type": "split", "dir": "row", "children": [], "sizes": []}
    code, ui = server.call("POST", "ui", {"layout_advanced": layout, "prefs": {"density": "compact"}})
    assert code == 200 and ui["layout_advanced"] == layout
    server.call("POST", "ui", {"prefs": None})
    assert server.call("GET", "ui")[1] == {"layout_advanced": layout}
    import oomi.gui.server as srv

    monkeypatch.setattr(srv, "UI_MAX", 100)
    assert server.call("POST", "ui", {"big": "x" * 200})[0] == 413
    assert "big" not in json.loads((server.home / "ui.json").read_text())


def test_each_window_reports_what_it_shows(server):
    _, a = server.call("POST", "bots", {"name": "A"})
    _, b = server.call("POST", "bots", {"name": "B"})
    app = server.app
    server.call("POST", "focus", {"client": "main", "bots": [a["id"], b["id"]], "focused": a["id"], "active": True})
    assert app.attending(a["id"]) and app.attending(b["id"])
    server.call("POST", "focus", {"client": "main", "bots": [a["id"]], "active": False})
    assert not app.attending(a["id"])
    server.call("POST", "focus", {"client": "popout", "bots": [b["id"]], "active": True})
    assert app.attending(b["id"]) and not app.attending(a["id"])
    app.runtimes[a["id"]].record.unread = 3
    server.call("POST", "focus", {"client": "main", "bots": [a["id"]], "active": True})
    assert app.runtimes[a["id"]].record.unread == 0
    server.call("POST", "focus", {"client": "popout", "gone": True})
    assert "popout" not in app.clients and not app.attending(b["id"])


def test_sidebar_fields_and_instructions(server):
    _, bot = server.call("POST", "bots", {"name": "Side"})
    code, st = server.call("PATCH", f"bots/{bot['id']}", {"pinned": True, "section": "  Client   work ",
                                                          "order": 2.5, "instructions": "Be brief."})
    assert code == 200 and st["pinned"] and st["section"] == "Client work" and st["order"] == 2.5
    assert st["instructions"] == "Be brief."
    reg = json.loads((server.home / "bots.json").read_text())["bots"][0]
    assert reg["pinned"] and reg["instructions"] == "Be brief."
    assert server.call("PATCH", f"bots/{bot['id']}", {"order": "first"})[0] == 400
    server.llm.script += [resp(text("ok"))]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "hi"})
    server.wait_idle(bot["id"])
    assert "Be brief." in server.llm.calls[-1]["system"]


def test_clone_export_and_import(server):
    _, bot = server.call("POST", "bots", {"name": "Orig", "trust": "full"})
    server.call("PATCH", f"bots/{bot['id']}", {"instructions": "Use pnpm.", "effort": "high", "section": "Web"})
    server.app.runtimes[bot["id"]].record.allow = ["shell:npm test"]
    code, copy = server.call("POST", f"bots/{bot['id']}/clone", {})
    assert code == 200 and copy["name"] == "Orig copy" and copy["id"] != bot["id"]
    assert copy["instructions"] == "Use pnpm." and copy["trust"] == "full" and copy["section"] == "Web"
    assert copy["folder"] != bot["folder"]  # a private folder isn't shared
    _, exported = server.call("GET", f"bots/{bot['id']}/export")
    assert exported["oomi_bot"] == 1 and "allow" not in exported and exported["folder"] == ""
    exported["allow"] = ["shell:rm -rf ~"]  # a file can't pre-approve anything
    code, imp = server.call("POST", "bots/import", {"data": exported})
    assert code == 200 and imp["name"] == "Orig" and imp["instructions"] == "Use pnpm." and imp["allow"] == []
    assert server.call("POST", "bots/import", {"data": {"name": "x"}})[0] == 400


def test_read_all_stop_all_and_notify_test(server):
    _, a = server.call("POST", "bots", {"name": "A"})
    _, b = server.call("POST", "bots", {"name": "B"})
    for x in (a, b):
        server.app.runtimes[x["id"]].record.unread = 2
    server.call("POST", "bots/read-all")
    assert all(rt.record.unread == 0 for rt in server.app.runtimes.values())
    assert server.call("POST", "bots/stop-all") == (200, {"stopped": 0})
    seq = server.app.bus.seq
    server.call("POST", "notify/test")
    assert [e["kind"] for e in server.app.bus.since(seq, 0) if e["type"] == "notify"] == ["test"]


def test_context_breakdown_and_compact(server):
    _, bot = server.call("POST", "bots", {"name": "Ctx"})
    code, ctx = server.call("GET", f"bots/{bot['id']}/context")
    assert code == 200 and ctx["messages"] == 0
    names = {p["name"] for p in ctx["parts"]}
    assert {"Instructions (system prompt)", "Tools"} <= names
    server.llm.script += [resp(text("a fairly long reply " * 50), usage={"input_tokens": 12000, "output_tokens": 300})]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "hello there"})
    server.wait_idle(bot["id"])
    _, ctx = server.call("GET", f"bots/{bot['id']}/context")
    parts = {p["name"]: p["tokens"] for p in ctx["parts"]}
    assert ctx["messages"] == 2 and parts["Replies"] > parts["Your messages"] > 0 and ctx["actual"] > 0
    code, out = server.call("POST", f"bots/{bot['id']}/compact")
    assert code == 200 and out["compacted"] is False  # too short to compact; nothing breaks


def test_new_settings_validate_export_import_and_reset(server):
    code, st = server.call("POST", "settings", {"deny_commands": ["git push*", " "], "approval_timeout_minutes": 10,
                                                "notify_sound": "Ping", "keep_awake": False, "redact_secrets": False})
    assert code == 200 and st["deny_commands"] == ["git push*"] and st["approval_timeout_minutes"] == 10
    assert st["notify_sound"] == "Ping" and not st["keep_awake"] and not st["redact_secrets"]
    assert server.call("POST", "settings", {"notify_sound": "Airhorn"})[0] == 400
    assert server.call("POST", "settings", {"max_steps_per_turn": 2})[0] == 400
    assert server.call("POST", "settings", {"deny_commands": "git push"})[0] == 400
    server.call("POST", "ui", {"prefs": {"density": "compact"}})
    _, exported = server.call("GET", "settings/export")
    assert exported["config"]["deny_commands"] == ["git push*"] and exported["ui"] == {"density": "compact"}
    _, st = server.call("POST", "settings/reset")
    assert st["deny_commands"] == [] and st["notify_sound"] == "Glass" and st["redact_secrets"]
    assert "prefs" not in server.call("GET", "ui")[1]
    _, st = server.call("POST", "settings/import", {"data": exported})
    assert st["deny_commands"] == ["git push*"] and server.call("GET", "ui")[1]["prefs"] == {"density": "compact"}
    assert server.call("POST", "settings/import", {"data": {"config": {}}})[0] == 400
    cfg = json.loads((server.home / "config.json").read_text())
    assert cfg["deny_commands"] == ["git push*"]


def test_settings_reach_running_bots(server):
    _, bot = server.call("POST", "bots", {"name": "Live"})
    server.llm.script += [resp(text("hi"))]
    server.call("POST", f"bots/{bot['id']}/send", {"text": "hi"})
    server.wait_idle(bot["id"])
    server.call("POST", "settings", {"deny_commands": ["make deploy"], "max_task_minutes": 30})
    cfg = server.app.runtimes[bot["id"]].agent.cfg
    assert cfg.deny_commands == ["make deploy"] and cfg.max_task_minutes == 30


# ------------------------------------------------------------------------------------------------ after review
def test_block_list_sees_through_disguises():
    pats = ["git push*"]
    for cmd in ("git  push", "git\tpush", "GIT push", '"git" push', "(git push)", "$(git push)", "echo `git push`",
                'bash -c "git push"', "sh -lc 'cd x; git push origin'", "FOO=1 git push", "/usr/bin/git push",
                "env git push", "sudo -u root git push", "nohup git push &", "time git push", "git -C . push"):
        assert blocked_by(cmd, pats) == "git push*", cmd
    for cmd in ("git status", 'grep "git push" notes.txt', "echo git push", "git pull"):
        assert blocked_by(cmd, pats) == "", cmd


def test_shell_commands_that_touch_protected_files_ask(env):
    from oomi.safety import protected_in_command

    names = ["CLAUDE.md", ".env"]
    for cmd in ("echo x >> CLAUDE.md", "sed -i s/a/b/ .env", "cp x claude.md", "cat x 2>CLAUDE.md"):
        assert protected_in_command(cmd, names), cmd
    assert protected_in_command("ls -la", names) == ""
    assert protected_write(Path("/x/.ENV"), [".env"]) == ".env"  # macOS ignores case
    agent = env.make([resp(tool("run_shell", {"command": "echo 'obey me' >> CLAUDE.md"})), resp(text("ok"))],
                     answers=[("no", "")], trust="full")
    agent.run("note it")
    assert env.ui.asked and "protected file" in env.ui.asked[0]["reason"]
    assert not (env.ws / "CLAUDE.md").exists()


def test_more_kinds_of_secret_are_hidden():
    for raw in ("DB_PASSWORD=hunter2hunter", "accessToken: 'abcdefgh1234'", "postgres://u:pw1234@host/db",
                "Authorization: Bearer abcdefghijklmnop1234", "glpat-abcdefghijklmnopqrstuv", "hf_" + "a" * 34):
        out, n = redact(raw)
        assert n == 1 and HIDDEN in out, raw
    for code in ("token = get_token()", "max_tokens = 4096", "password = config.password", "TOKEN_LIMIT=4096"):
        assert redact(code) == (code, 0), code


def test_a_hidden_secret_is_never_written_back(env):
    (env.ws / "settings.ini").write_text("password = hunter2hunter\nmode = fast\n")
    agent = env.make([resp(tool("write_file", {"path": "settings.ini", "content": f"password = {HIDDEN}\nmode = slow\n"})),
                      resp(tool("edit_file", {"path": "settings.ini", "old_text": "mode = fast", "new_text": f"x = {HIDDEN}"})),
                      resp(text("ok"))], trust="full")
    agent.run("set mode to slow")
    assert (env.ws / "settings.ini").read_text() == "password = hunter2hunter\nmode = fast\n"
    results = [b for m in agent.messages if m["role"] == "user" for b in m["content"] if b.get("type") == "tool_result"]
    assert all(r.get("is_error") and "secret oomi hid" in r["content"] for r in results)


def test_always_allow_doesnt_outrank_untrusted_content():
    from oomi.safety import EXEC, Assessment, decide

    a = Assessment(EXEC, "deploy", approval_key="shell:deploy", network=True)
    assert decide(a, "project", tainted=False, sandboxed=True, session_allow={"shell:deploy"})[0] == "allow"
    assert decide(a, "project", tainted=True, sandboxed=True, session_allow={"shell:deploy"})[0] == "ask"
    b = Assessment(EXEC, "make", approval_key="shell:make")
    assert decide(b, "full", tainted=True, sandboxed=False, session_allow={"shell:make"})[0] == "ask"
    assert decide(b, "project", tainted=True, sandboxed=True, session_allow={"shell:make"})[0] == "allow"


def test_numbers_must_be_real_numbers(server):
    _, bot = server.call("POST", "bots", {"name": "Num"})
    for bad in ("NaN", "inf", "-Infinity"):
        assert server.call("PATCH", f"bots/{bot['id']}", {"budget": bad})[0] == 400
        assert server.call("PATCH", f"bots/{bot['id']}", {"order": bad})[0] == 400
    reg = json.loads((server.home / "bots.json").read_text())
    reg["bots"][0]["budget"] = float("nan")
    (server.home / "bots.json").write_text(json.dumps(reg))
    from oomi.gui.store import Registry

    assert Registry(server.home).bots[bot["id"]].budget == 0.0


def test_imported_bots_never_get_full_trust(server):
    code, st = server.call("POST", "bots/import", {"data": {"oomi_bot": 1, "name": "Wild", "trust": "full"}})
    assert code == 200 and st["trust"] == "project"
    code, st = server.call("POST", "bots/import", {"data": {"oomi_bot": 1, "name": "Calm", "trust": "read-only"}})
    assert st["trust"] == "read-only"


def test_window_preferences_merge_and_are_checked(server):
    seq = server.app.bus.seq
    server.call("POST", "ui", {"prefs": {"text_size": "large"}})
    server.call("POST", "ui", {"prefs": {"density": "compact", "side_collapsed": 5, "bogus": 1, "chat_width": "huge"}})
    ui = server.call("GET", "ui")[1]
    assert ui["prefs"] == {"text_size": "large", "density": "compact"}
    assert [e for e in server.app.bus.since(seq, 0) if e["type"] == "ui"]
    server.call("POST", "ui", {"prefs": {"text_size": None}})
    assert server.call("GET", "ui")[1]["prefs"] == {"density": "compact"}
    assert server.call("POST", "ui", {"layouts": "nope"})[0] == 400
    server.call("POST", "ui", {"layouts": [{"name": "Two", "shape": {"type": "group", "tabs": []}}, {"bad": 1}]})
    assert [x["name"] for x in server.call("GET", "ui")[1]["layouts"]] == ["Two"]


def test_compacting_a_bot_that_never_started_does_nothing(server):
    _, bot = server.call("POST", "bots", {"name": "Quiet"})
    code, out = server.call("POST", f"bots/{bot['id']}/compact")
    assert code == 200 and out["compacted"] is False
    assert server.app.runtimes[bot["id"]].agent is None and not server.app.runtimes[bot["id"]].record.session_id
