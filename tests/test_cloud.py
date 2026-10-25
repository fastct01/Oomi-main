"""Backing up everything oomi keeps to MongoDB Atlas, and restoring it on another Mac."""

import json
import sqlite3
from pathlib import Path

import mongomock
import pytest

from oomi import cloud
from oomi.account import AccountError, AccountStore
from oomi.cloud import DataSync, apply_pending_restore

URI = "mongodb+srv://u:p@cluster0.example.mongodb.net/"


@pytest.fixture
def atlas():
    client = mongomock.MongoClient()
    return lambda uri: client


def mac(home: Path, atlas, vault=None):
    vault = {} if vault is None else vault
    acct = AccountStore(home, vault.get, lambda k, v: vault.__setitem__(k, v) or True, lambda k: vault.pop(k, None),
                        client_factory=atlas)
    acct.connect(URI)
    return DataSync(home, acct), acct


def fill(home: Path, user: Path):
    home.mkdir(parents=True, exist_ok=True)
    (home / "bots.json").write_text(json.dumps({"bots": [{"id": "b1", "name": "db bot", "folder": f"{user}/Oomi/db bot"},
                                                         {"id": "b2", "name": "elsewhere", "folder": "/Volumes/x/y"}]}))
    (home / "config.json").write_text('{"model": "claude-sonnet-5-5"}')
    (home / "sessions").mkdir(exist_ok=True)
    (home / "sessions" / "s1.json").write_text('{"messages": []}')
    (home / "bots" / "b1").mkdir(parents=True, exist_ok=True)
    (home / "bots" / "b1" / "timeline.jsonl").write_text('{"type": "user", "text": "hi"}\n')
    for skipped in ("logs/app.log", "webview/cache", "pycache/x.pyc", "launch/a.command", "bots.json.123.tmp"):
        (home / skipped).parent.mkdir(parents=True, exist_ok=True)
        (home / skipped).write_text("not data")
    db = sqlite3.connect(home / "memory.db")
    db.execute("pragma journal_mode=wal")
    db.execute("create table facts (text)")
    db.execute("insert into facts values ('uses pnpm')")
    db.commit()
    return db  # left open, as a running bot would


def test_everything_is_backed_up_and_restored_on_another_mac(tmp_path, atlas, monkeypatch):
    old_user, new_user = tmp_path / "Users" / "ada", tmp_path / "Users" / "ada2"
    open_db = fill(old_user / ".oomi", old_user)
    sync, acct = mac(old_user / ".oomi", atlas)
    st = sync.push()
    assert st["files"] == 5 and not st["error"]  # bots.json, config.json, memory.db, a session, a timeline
    paths = sorted(f["path"] for f in atlas("")["oomi"]["data_manifests"].find_one({"_id": acct.load()["id"]})["files"])
    assert paths == ["bots.json", "bots/b1/timeline.jsonl", "config.json", "memory.db", "sessions/s1.json"]

    # the other Mac adopts the account, downloads, and the backup goes in place when oomi next starts
    home2 = new_user / ".oomi"
    home2.mkdir(parents=True)
    (home2 / "config.json").write_text('{"model": "old"}')
    sync2, acct2 = mac(home2, atlas)
    acct2.pull(acct.load()["id"])
    assert sync2.remote()["files"] == 5 and not sync2.remote()["this_mac"]
    assert sync2.restore()["pending_restore"]
    assert json.loads((home2 / "config.json").read_text())["model"] == "old"  # nothing changes until a restart
    monkeypatch.setattr(Path, "home", staticmethod(lambda: new_user))
    assert apply_pending_restore(home2)
    assert json.loads((home2 / "config.json").read_text())["model"] == "claude-sonnet-5-5"
    assert [b["folder"] for b in json.loads((home2 / "bots.json").read_text())["bots"]] == [f"{new_user}/Oomi/db bot", "/Volumes/x/y"]
    assert (home2 / "bots" / "b1" / "timeline.jsonl").read_text().startswith('{"type": "user"')
    rows = sqlite3.connect(home2 / "memory.db").execute("select text from facts").fetchall()
    assert rows == [("uses pnpm",)]  # committed rows still in the WAL made it into the copy
    kept = list((home2 / "backups").glob("before-restore-*/config.json"))
    assert kept and json.loads(kept[0].read_text())["model"] == "old"
    assert not (home2 / "restore").exists() and not apply_pending_restore(home2)
    open_db.close()


def test_only_changes_are_uploaded_and_old_contents_are_removed(tmp_path, atlas, monkeypatch):
    monkeypatch.setattr(cloud, "CHUNK", 10)  # small chunks: files span several documents
    home = tmp_path / ".oomi"
    fill(home, tmp_path).close()
    sync, acct = mac(home, atlas)
    files = atlas("")["oomi"]["data_files"]
    sync.push()
    before = files.count_documents({})
    writes = []
    real = type(files).replace_one
    monkeypatch.setattr(type(files), "replace_one", lambda self, q, doc, **k: (self.name == "data_files" and writes.append(doc)) or real(self, q, doc, **k))
    sync.push()
    assert writes == [] and files.count_documents({}) == before  # nothing changed, nothing sent
    (home / "sessions" / "s1.json").write_text('{"messages": [1]}')
    sync.push()
    assert [w["n"] for w in writes] == [0, 1] and len({w["sha"] for w in writes}) == 1  # only that file, in two chunks
    (home / "sessions" / "s1.json").unlink()
    sync.push()
    shas = {f["sha"] for f in atlas("")["oomi"]["data_manifests"].find_one({})["files"]}
    assert {d["sha"] for d in files.find({})} == shas  # the removed file's contents are gone from the database


def test_automatic_backups_never_replace_another_macs(tmp_path, atlas):
    a_home, b_home = tmp_path / "a", tmp_path / "b"
    fill(a_home, tmp_path).close()
    sync_a, acct_a = mac(a_home, atlas)
    sync_a.push()
    b_home.mkdir()
    (b_home / "config.json").write_text("{}")
    sync_b, acct_b = mac(b_home, atlas)
    acct_b.pull(acct_a.load()["id"])
    st = sync_b.push(auto=True)
    assert st["other"]["host"] and not st["last_push"]
    assert sync_b.remote()["files"] == 5  # A's backup is untouched
    assert sync_b.push()["files"] == 1  # uploading on purpose does replace it
    assert sync_a.push(auto=True)["other"]  # and now A holds back in turn


def test_restore_refuses_a_backup_that_reaches_outside_oomi(tmp_path, atlas):
    home = tmp_path / ".oomi"
    home.mkdir()
    sync, acct = mac(home, atlas)
    with pytest.raises(AccountError, match="no backup"):
        sync.restore()
    atlas("")["oomi"]["data_manifests"].insert_one({"_id": acct.load()["id"], "device": "x", "files": [
        {"path": "../../.ssh/authorized_keys", "sha": "0" * 64, "size": 1, "chunks": 1}]})
    with pytest.raises(AccountError, match="outside"):
        sync.restore()
    assert not (home / "restore").exists()


def test_off_until_atlas_is_connected(tmp_path):
    home = tmp_path / ".oomi"
    home.mkdir()
    sync = DataSync(home, AccountStore(home, lambda k: None, lambda k, v: True, lambda k: None))
    assert not sync.enabled
    with pytest.raises(AccountError, match="Connect"):
        sync.push()


def test_an_unreachable_cluster_is_reported_not_raised_raw(tmp_path, atlas):
    home = tmp_path / ".oomi"
    fill(home, tmp_path).close()
    sync, acct = mac(home, atlas)

    def offline(uri):
        raise OSError("All nameservers failed to answer the query _mongodb._tcp.cluster-oomi.example.mongodb.net")
    acct.client_factory = offline
    with pytest.raises(AccountError, match="Couldn't reach the database"):
        sync.push(auto=True)
    assert "nameservers" in sync.state()["error"]
