"""Antibodies in the app's agent: a fixed error becomes a candidate with proof, an approved one cures a teammate's
error, and nothing leaks across teams or into the database that shouldn't."""

import json
import time
from pathlib import Path

from conftest import FakeLLM, ScriptedUI, resp, text, tool
from oomi.agent import Agent
from oomi.antibodies import RETIRE_AFTER_MISSES, Store, Watcher
from oomi.config import Config

CHECK = """
import pathlib, sys
cfg = pathlib.Path("config.txt").read_text() if pathlib.Path("config.txt").exists() else ""
if "enabled=true" not in cfg:
    raise ValueError("feature flag 'enabled' is off in config.txt")
print("ok")
"""
ERROR = "ValueError: feature flag 'enabled' is off in config.txt"


def fix_script():
    return [resp(tool("run_shell", {"command": "python3 check.py"})),
            resp(tool("write_file", {"path": "config.txt", "content": "enabled=true\n"})),
            resp(tool("run_shell", {"command": "python3 check.py"})),
            resp(text("Fixed."))]


def project(tmp_path: Path, name: str, check: str = CHECK) -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "check.py").write_text(check)
    return root


def run(env, store: Store, root: Path, script):
    cfg = Config()
    cfg.sandbox, cfg.trust, cfg.memory_learn = False, "full", False
    llm, ui, notes = FakeLLM(script), ScriptedUI(), []
    agent = Agent(cfg, llm, ui, root, env.memory, env.skills)
    agent.watcher = Watcher(store, root, describe=lambda s, p: "Turn on the feature flag: set enabled=true in config.txt.",
                            notify=notes.append)
    agent.run("make check.py pass")
    return agent.watcher, llm, notes


def tool_results(llm) -> list:
    out = []
    for call in llm.calls:
        last = call["messages"][-1]
        if last["role"] == "user" and isinstance(last["content"], list):
            out += [b["content"] for b in last["content"] if b.get("type") == "tool_result"]
    return out


def test_fixing_an_error_makes_a_candidate_with_proof(env, team_store, tmp_path):
    watcher, llm, notes = run(env, team_store, project(tmp_path, "shop"), fix_script())
    assert len(watcher.candidates) == 1
    c = watcher.candidates[0]
    assert c["error_text"] == ERROR and c["proven_by"] == {"command": "python3 check.py", "exit_code": 0}
    assert c["fix"].startswith("Turn on the feature flag") and any("edited config.txt" in s for s in c["steps"])
    assert c["tokens_to_fix"] > 0 and c["project"] == "shop"
    assert team_store.list() == [] and any("ready for your review" in n for n in notes)  # nothing shared yet


def test_an_approved_antibody_cures_a_teammate(env, team_store, db, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    team_store.save(watcher.candidates[0], approved=True)

    olga = Store(db, team="encode-london", user="Olga", device="olga-mac", use_atlas_search=False)
    _, llm, notes = run(env, olga, project(tmp_path, "api"), fix_script())
    first = tool_results(llm)[0]
    assert "[antibody]" in first and "set enabled=true in config.txt" in first
    assert any("cured" in n for n in notes)
    doc = team_store.list()[0]
    assert doc["uses"] == 1 and doc["cures"] == 1
    kinds = [e["kind"] for e in db.events.find().sort("ts", 1)]
    assert kinds.count("error") == 2 and "antibody_offered" in kinds and "antibody_cured" in kinds


def test_other_teams_never_see_it(env, team_store, db, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    team_store.save(watcher.candidates[0], approved=True)
    rival = Store(db, team="other-team", user="Max", device="x", use_atlas_search=False)
    _, llm, _ = run(env, rival, project(tmp_path, "rival"), fix_script())
    assert "[antibody]" not in tool_results(llm)[0]


def test_pending_antibodies_are_not_matched_until_approved(env, team_store, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    _id = team_store.save(watcher.candidates[0], approved=False)
    assert team_store.match(ERROR) is None
    assert team_store.review(_id, approve=True) and team_store.match(ERROR) is not None


def test_saving_the_same_fix_twice_confirms_it(env, team_store, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    cand = watcher.candidates[0]
    a = team_store.save(cand, approved=True)
    b = team_store.save(dict(cand), approved=True)
    assert a == b and team_store.list()[0]["confirmations"] == 2


def test_similar_but_not_identical_errors_still_match(env, team_store, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    team_store.save(watcher.candidates[0], approved=True)
    m = team_store.match("ValueError: feature flag 'enabled' is off in settings config.txt")
    assert m is not None and m.how == "local"
    assert team_store.match("PermissionError: cannot write to /var/log") is None


def test_an_antibody_that_keeps_failing_is_retired(env, team_store, tmp_path):
    watcher, _, _ = run(env, team_store, project(tmp_path, "shop"), fix_script())
    _id = team_store.save(watcher.candidates[0], approved=True)
    for _ in range(RETIRE_AFTER_MISSES):
        team_store.record_miss(_id)
    assert team_store.list() == [] and team_store.list(status="retired")[0]["_id"] == _id


def test_secrets_in_errors_never_reach_the_database(env, team_store, tmp_path):
    root = project(tmp_path, "leaky", 'import pathlib\n'
                   'if not pathlib.Path("ok").exists():\n'
                   '    raise RuntimeError("auth failed for token=sk-ant-REALSECRET1234567890 at /Users/ivan/keys")\n')
    script = [resp(tool("run_shell", {"command": "python3 check.py"})),
              resp(tool("write_file", {"path": "ok", "content": "1"})),
              resp(tool("run_shell", {"command": "python3 check.py"})), resp(text("Done."))]
    watcher, _, _ = run(env, team_store, root, script)
    team_store.save(watcher.candidates[0], approved=True)
    blob = str(list(team_store.db.antibodies.find())) + str(list(team_store.db.events.find()))
    assert "REALSECRET" not in blob and "/Users/ivan" not in blob


def test_a_database_hiccup_never_breaks_the_task(env, team_store, tmp_path):
    class Broken(Watcher):
        def observe(self, *a, **k):
            raise ConnectionError("Atlas went away")

    cfg = Config()
    cfg.sandbox, cfg.trust, cfg.memory_learn = False, "full", False
    root = project(tmp_path, "shop")
    ui = ScriptedUI()
    agent = Agent(cfg, FakeLLM(fix_script()), ui, root, env.memory, env.skills)
    agent.watcher = Broken(team_store, root)
    assert agent.run("make it pass") == "Fixed."
    assert any("Antibodies unavailable" in m for _, m in ui.notices)


def test_keys_in_the_oomi_home_env_work_in_any_project(tmp_path, monkeypatch):
    import os

    from oomi.antibodies import TeamSettings

    home, project = tmp_path / "home", tmp_path / "project"
    home.mkdir(), project.mkdir()
    (home / ".env").write_text("MONGODB_URI=mongodb+srv://u:p@c.example.net\nOOMI_TEAM=from-home\n")
    (project / ".env").write_text("OOMI_TEAM=from-project\n")
    for k in ("MONGODB_URI", "OOMI_TEAM"):
        monkeypatch.delenv(k, raising=False)
    try:
        s = TeamSettings.load(home, project)
    finally:
        for k in ("MONGODB_URI", "OOMI_TEAM"):
            os.environ.pop(k, None)  # .env files set real environment variables
    assert s.mongodb_uri.startswith("mongodb+srv://") and s.team == "from-project"  # the project's own wins


# ------------------------------------------------------------------------------------------------ the app
def test_the_app_saves_fixes_for_review_and_shares_them(server, db, tmp_path):
    server.app.connect_team(db=db)
    code, st = server.call("GET", "antibodies")
    assert code == 200 and st["connected"] and st["mode"] in ("hybrid+rerank", "hybrid", "vector", "text", "local")
    server.app.team.use_atlas_search = False  # mongomock has no Atlas Search
    _, bot = server.call("POST", "bots", {"name": "Shop", "trust": "full"})
    (Path(bot["folder"]) / "check.py").write_text(CHECK)
    server.llm.script += fix_script()
    server.call("POST", f"bots/{bot['id']}/send", {"text": "make check.py pass"})
    server.wait_idle(bot["id"])
    _, st = server.call("GET", "antibodies")
    assert len(st["pending"]) == 1 and st["pending"][0]["error_text"] == ERROR and st["approved"] == []
    pid = st["pending"][0]["id"]
    code, st = server.call("POST", f"antibodies/{pid}/review", {"approve": True})
    assert code == 200 and st["pending"] == [] and st["approved"][0]["id"] == pid
    assert server.call("POST", f"antibodies/{pid}/review", {"approve": True})[0] == 400
    code, m = server.call("POST", "antibodies/match", {"error": ERROR})
    assert code == 200 and m["match"]["how"] == "exact"
    assert server.call("POST", "antibodies/match", {"error": ""})[0] == 400


def test_a_proven_fix_is_shared_from_the_chat_and_deleted_in_settings(server, db, tmp_path):
    server.app.connect_team(db=db)
    server.app.team.use_atlas_search = False  # mongomock has no Atlas Search
    _, bot = server.call("POST", "bots", {"name": "Shop", "trust": "full"})
    (Path(bot["folder"]) / "check.py").write_text(CHECK)
    server.llm.script += fix_script()
    server.call("POST", f"bots/{bot['id']}/send", {"text": "make check.py pass"})
    server.wait_idle(bot["id"])
    _, tl = server.call("GET", f"bots/{bot['id']}/timeline")
    note = next(i for i in tl["items"] if i.get("action") == "antibody")
    assert len(note["antibodies"]) == 1 and not note.get("closed")
    code, r = server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})
    assert code == 200 and r["shared"] == 1 and r["item"]["closed"]
    _, st = server.call("GET", "antibodies")
    assert st["pending"] == [] and st["approved"][0]["id"] == note["antibodies"][0]
    _, r = server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})  # clicked twice: nothing more to share
    assert r["shared"] == 0
    assert server.call("POST", f"bots/{bot['id']}/share", {"item": "n-nope"})[0] == 404
    code, st = server.call("DELETE", f"antibodies/{note['antibodies'][0]}")
    assert code == 200 and st["approved"] == []


def test_an_older_chat_notice_shares_that_bots_waiting_fix(server, db):
    server.app.connect_team(db=db)
    _, bot = server.call("POST", "bots", {"name": "Invoices", "trust": "project"})
    rt = server.app.runtimes[bot["id"]]
    project = Path(bot["folder"]).name
    mine = server.app.team.save({"error_text": ERROR, "signature": "s1", "fix": "f", "project": project}, approved=False)
    other = server.app.team.save({"error_text": "ValueError: x", "signature": "s2", "fix": "g", "project": "elsewhere"}, approved=False)
    store = server.app.team  # a teammate's unshared fix in a folder with the same name: not ours to share
    teammate = store.antibodies.insert_one({"error_text": "KeyError: k", "signature": "s3", "fix": "h", "project": project,
                                            "team": store.team, "author": "someone-else", "device": "their-mac",
                                            "status": "pending", "created_at": store.antibodies.find_one({"_id": mine})["created_at"]}).inserted_id
    note = rt.put({"id": "n-old", "type": "notice", "level": "info", "ts": time.time(),
                   "text": "A fix is ready to share with your team: review in Settings, Antibodies."})
    code, r = server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})
    assert code == 200 and r["shared"] == 1 and r["item"]["closed"]
    _, st = server.call("GET", "antibodies")
    assert [d["id"] for d in st["approved"]] == [str(mine)]
    assert sorted(d["id"] for d in st["pending"]) == sorted([str(other), str(teammate)])


def test_sharing_a_fix_the_team_already_has_counts_as_a_confirmation(server, db):
    server.app.connect_team(db=db)
    store = server.app.team
    first = store.save({"error_text": ERROR, "signature": "same", "fix": "f", "project": "a"}, approved=False)
    again = store.save({"error_text": ERROR, "signature": "same", "fix": "f2", "project": "b"}, approved=False)
    assert store.review(first, approve=True) and store.review(again, approve=True)  # the second used to come back rejected
    docs = list(store.antibodies.find({"team": store.team, "signature": "same"}))
    assert len(docs) == 1 and docs[0]["_id"] == first and docs[0]["status"] == "approved" and docs[0]["confirmations"] == 2


def test_fixes_are_shared_automatically_when_that_setting_is_on(server, db):
    server.app.connect_team(db=db)
    server.app.team.use_atlas_search = False
    code, s = server.call("POST", "settings", {"antibody_auto_share": True})
    assert code == 200 and s["antibody_auto_share"] is True
    _, bot = server.call("POST", "bots", {"name": "Shop", "trust": "full"})
    (Path(bot["folder"]) / "check.py").write_text(CHECK)
    server.llm.script += fix_script()
    server.call("POST", f"bots/{bot['id']}/send", {"text": "make check.py pass"})
    server.wait_idle(bot["id"])
    _, tl = server.call("GET", f"bots/{bot['id']}/timeline")
    note = next(i for i in tl["items"] if i.get("action") == "antibody")
    assert note["closed"] and note["auto"] and note["shared"] == 1 and "automatically" in note["text"]
    assert not any("ready for your review" in (i.get("text") or "") for i in tl["items"])  # one line, not two
    _, st = server.call("GET", "antibodies")
    assert st["pending"] == [] and [d["id"] for d in st["approved"]] == note["antibodies"]
    assert server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})[1]["shared"] == 0


def test_share_on_a_closed_notice_changes_nothing(server, db):
    server.app.connect_team(db=db)
    _, bot = server.call("POST", "bots", {"name": "Invoices", "trust": "project"})
    rt = server.app.runtimes[bot["id"]]
    project = Path(bot["folder"]).name
    first = server.app.team.save({"error_text": ERROR, "signature": "s1", "fix": "f", "project": project}, approved=False)
    note = rt.put({"id": "n-old", "type": "notice", "level": "info", "ts": time.time(),
                   "text": "A fix is ready to share with your team: review in Settings, Antibodies."})
    assert server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})[1]["shared"] == 1
    later = server.app.team.save({"error_text": "ValueError: x", "signature": "s2", "fix": "g", "project": project}, approved=False)
    _, r = server.call("POST", f"bots/{bot['id']}/share", {"item": note["id"]})  # clicked again later
    assert r["shared"] == 0 and r["item"]["shared"] == 1 and r["item"]["antibodies"] == [str(first)]
    _, st = server.call("GET", "antibodies")
    assert [d["id"] for d in st["pending"]] == [str(later)]  # a fix it never announced stays waiting


def test_a_fix_added_by_hand_is_shared_matched_and_can_be_deleted(server, db):
    server.app.connect_team(db=db)
    server.app.team.use_atlas_search = False  # mongomock has no Atlas Search
    assert server.call("POST", "antibodies/add", {"error_type": "AttributeError", "error_message": "", "fix": "x"})[0] == 400
    assert server.call("POST", "antibodies/add", {"error_message": "boom", "fix": " "})[0] == 400
    code, st = server.call("POST", "antibodies/add", {
        "error_type": "AttributeError", "error_message": "module 'collections' has no attribute 'Mapping'",
        "fix": "Import Mapping from collections.abc.", "command": "pytest -q", "project": "invoices"})
    assert code == 200 and st["pending"] == []
    d = st["approved"][0]
    assert d["error_text"] == "AttributeError: module 'collections' has no attribute 'Mapping'" and d["source"] == "manual"
    assert d["proven_by"]["command"] == "pytest -q" and d["project"] == "invoices"
    _, m = server.call("POST", "antibodies/match", {"error": d["error_text"]})
    assert m["match"]["fix"] == "Import Mapping from collections.abc."
    code, st = server.call("DELETE", f"antibodies/{d['id']}")
    assert code == 200 and st["approved"] == []
    assert server.call("DELETE", f"antibodies/{d['id']}")[0] == 404


def test_the_app_without_atlas_says_how_to_connect(server):
    code, st = server.call("GET", "antibodies")
    assert code == 200 and st["connected"] is False
    assert server.call("POST", "antibodies/setup", {})[0] == 400
