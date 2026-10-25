"""Backing up everything oomi keeps (bots, their settings and conversations, memories, skills, schedules, usage,
attachments) to the MongoDB Atlas database your account syncs with, and restoring it on another Mac.

* The profile has its own document (account.py). This copies the rest of ~/.oomi, file by file.
* Never copied: API keys, app sign-ins and the connection string (they live in the Keychain, not in files), caches
  and logs. Your bots' own folders aren't copied either: they're yours, wherever they are.
* Storage: `data_manifests` holds one document per account (which files, from which Mac, when); `data_files` holds
  the contents, in chunks named by their SHA-256, so a file that hasn't changed is never uploaded again.
* SQLite databases are copied through SQLite's backup API, so a copy is consistent even while a bot is writing.
* A restore downloads into ~/.oomi/restore and is applied the next time oomi starts, before anything is open;
  what it replaces is kept in ~/.oomi/backups.
* Two Macs on one account share one backup. An automatic upload never replaces another Mac's: you choose.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from .account import AccountError, AccountStore

MANIFESTS, FILES = "data_manifests", "data_files"
CHUNK = 4 * 1024 * 1024  # well under MongoDB's 16 MB document limit
MAX_FILE = 64 * 1024 * 1024  # bigger files (a huge attachment) are skipped and counted
SKIP_DIRS = {"webview", "pycache", "launch", "logs", "spill", "backups", "restore"}  # caches, and the restore's own
SKIP_FILES = {"account.json", "cloud_sync.json", "last_folder", ".DS_Store"}  # synced on its own, or about this Mac only
SKIP_SUFFIXES = (".tmp", "-wal", "-shm", "-journal")  # the backup API folds the WAL into the copy
SQLITE_MAGIC = b"SQLite format 3\x00"


class DataSync:
    def __init__(self, home: Path, account: AccountStore):
        self.home = Path(home)
        self.account = account
        self.state_path = self.home / "cloud_sync.json"
        self.lock = threading.Lock()  # one upload or download at a time

    # -- local state: on or off, and what the last upload was ------------------------------------------------
    def _load(self) -> dict:
        try:
            d = json.loads(self.state_path.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        return d if isinstance(d, dict) else {}

    def _save(self, d: dict) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(self.state_path)

    def _update(self, **kw) -> dict:
        d = self._load()
        d.update(kw)
        self._save(d)
        return d

    @property
    def enabled(self) -> bool:
        return self._load().get("enabled", True) and self.account.load()["sync"].get("enabled", False)

    def set_enabled(self, on: bool) -> dict:
        self._update(enabled=bool(on))
        return self.state()

    def state(self) -> dict:
        d = self._load()
        return {"enabled": d.get("enabled", True), "last_push": d.get("last_push"), "files": d.get("files", 0),
                "bytes": d.get("bytes", 0), "skipped": d.get("skipped", []), "error": d.get("error", ""),
                "other": d.get("other"), "pending_restore": (self.home / "restore" / "manifest.json").exists()}

    # -- the database ------------------------------------------------------------------------------------------
    def _db(self):
        a = self.account.load()
        if not a["sync"].get("enabled"):
            raise AccountError("Connect MongoDB Atlas first.")
        return self.account._mirror(a).database_handle(), a

    # -- which files -----------------------------------------------------------------------------------------
    def files(self) -> list:
        out = []
        for root, dirs, names in os.walk(self.home):
            rel_root = Path(root).relative_to(self.home)
            if rel_root == Path("."):
                dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for n in names:
                if n in SKIP_FILES or n.endswith(SKIP_SUFFIXES) or ".tmp" in n or n.startswith("."):
                    continue
                p = Path(root) / n
                if p.is_file() and not p.is_symlink():
                    out.append((rel_root / n).as_posix())
        return sorted(out)

    def _read(self, rel: str) -> bytes:
        p = self.home / rel
        with p.open("rb") as f:
            head = f.read(16)
        if head != SQLITE_MAGIC:
            return p.read_bytes()
        with tempfile.TemporaryDirectory() as t:
            copy = Path(t) / "copy.db"
            src = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
            dst = sqlite3.connect(copy)
            try:
                src.backup(dst)
            finally:
                src.close()
                dst.close()
            return copy.read_bytes()

    # -- uploading ----------------------------------------------------------------------------------------------
    def push(self, auto: bool = False) -> dict:
        """Upload what changed. auto: don't replace a backup another Mac made (the person decides)."""
        if not self.lock.acquire(blocking=not auto):
            return self.state()
        try:
            try:
                db, a = self._db()
            except AccountError as e:
                if self.account.load()["sync"].get("enabled"):  # connected, but unreachable now
                    self._update(error=str(e))
                raise
            try:
                remote = db[MANIFESTS].find_one({"_id": a["id"]}, {"device": 1, "host": 1, "updated": 1})
                if auto and remote and remote.get("device") != a["device"]:
                    self._update(other={"host": remote.get("host", "another Mac"), "updated": remote.get("updated")})
                    return self.state()
                entries, skipped, total = [], [], 0
                for rel in self.files():
                    try:
                        if (self.home / rel).stat().st_size > MAX_FILE:
                            skipped.append(rel)
                            continue
                        data = self._read(rel)
                    except (OSError, sqlite3.Error):
                        continue  # deleted or locked mid-walk: the next upload gets it
                    sha = hashlib.sha256(data).hexdigest()
                    n = max(1, -(-len(data) // CHUNK))
                    # chunks go up in order, so the last one being there means the whole file is
                    if not db[FILES].find_one({"_id": f"{a['id']}:{sha}:{n - 1}"}, {"_id": 1}):
                        for i in range(n):
                            db[FILES].replace_one({"_id": f"{a['id']}:{sha}:{i}"},
                                                  {"_id": f"{a['id']}:{sha}:{i}", "account": a["id"], "sha": sha, "n": i,
                                                   "data": data[i * CHUNK:(i + 1) * CHUNK]}, upsert=True)
                    entries.append({"path": rel, "sha": sha, "size": len(data), "chunks": n})
                    total += len(data)
                now = time.time()
                db[MANIFESTS].replace_one({"_id": a["id"]}, {
                    "_id": a["id"], "device": a["device"], "host": platform.node() or "a Mac", "home": str(self.home),
                    "updated": now, "files": entries}, upsert=True)
                db[FILES].delete_many({"account": a["id"], "sha": {"$nin": [e["sha"] for e in entries]}})
            except AccountError:
                raise
            except Exception as e:  # noqa: BLE001  (auth, network, DNS: all reported the same way)
                self._update(error=f"Couldn't back up: {str(e)[:200]}")
                raise AccountError(f"Couldn't back up: {str(e)[:200]}")
            self._update(last_push=now, files=len(entries), bytes=total, skipped=skipped, error="", other=None)
            return self.state()
        finally:
            self.lock.release()

    # -- restoring ----------------------------------------------------------------------------------------------
    def remote(self) -> Optional[dict]:
        """What the backup in the cloud is: from which Mac, when, how big."""
        db, a = self._db()
        try:
            m = db[MANIFESTS].find_one({"_id": a["id"]})
        except Exception as e:  # noqa: BLE001
            raise AccountError(f"Couldn't reach the database: {str(e)[:200]}")
        if not m:
            return None
        return {"host": m.get("host", ""), "updated": m.get("updated"), "this_mac": m.get("device") == a["device"],
                "files": len(m.get("files") or []), "bytes": sum(f.get("size", 0) for f in m.get("files") or [])}

    def restore(self) -> dict:
        """Download the backup into ~/.oomi/restore; it replaces the files here when oomi next starts."""
        with self.lock:
            db, a = self._db()
            stage = self.home / "restore"
            shutil.rmtree(stage, ignore_errors=True)
            try:
                m = db[MANIFESTS].find_one({"_id": a["id"]})
                if not m or not m.get("files"):
                    raise AccountError("There's no backup in the database for this account yet.")
                for f in m["files"]:
                    rel = Path(f["path"])
                    if rel.is_absolute() or ".." in rel.parts:
                        raise AccountError(f"The backup has a file outside oomi's folder: {f['path']}")
                    parts = [db[FILES].find_one({"_id": f"{a['id']}:{f['sha']}:{i}"}) for i in range(f["chunks"])]
                    if any(p is None for p in parts):
                        raise AccountError(f"The backup is missing part of {f['path']}; upload it again from that Mac.")
                    data = b"".join(bytes(p["data"]) for p in parts)
                    if hashlib.sha256(data).hexdigest() != f["sha"]:
                        raise AccountError(f"{f['path']} didn't download intact; try again.")
                    dest = stage / "files" / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(data)
            except AccountError:
                shutil.rmtree(stage, ignore_errors=True)
                raise
            except Exception as e:  # noqa: BLE001
                shutil.rmtree(stage, ignore_errors=True)
                raise AccountError(f"Couldn't download the backup: {str(e)[:200]}")
            (stage / "manifest.json").write_text(json.dumps({  # written last: its presence means the download is whole
                "home": m.get("home", ""), "host": m.get("host", ""), "updated": m.get("updated"),
                "files": [f["path"] for f in m["files"]]}))
            self._update(other=None)
            return self.state()

    def cancel_restore(self) -> dict:
        shutil.rmtree(self.home / "restore", ignore_errors=True)
        return self.state()


def apply_pending_restore(home: Path) -> bool:
    """At startup, before anything is open: put a downloaded backup in place. The files it replaces go to backups."""
    home = Path(home)
    stage = home / "restore"
    try:
        m = json.loads((stage / "manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    keep = home / "backups" / time.strftime("before-restore-%Y%m%d-%H%M%S")
    for rel in m.get("files") or []:
        src, dest = stage / "files" / rel, home / rel
        if not src.is_file():
            continue
        for old in (dest, Path(f"{dest}-wal"), Path(f"{dest}-shm")):  # a stale WAL would be replayed onto the new db
            if old.exists():
                (keep / old.relative_to(home)).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old), str(keep / old.relative_to(home)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
    _rehome_bots(home, m.get("home") or "")
    shutil.rmtree(stage, ignore_errors=True)
    return True


def _rehome_bots(home: Path, old_home: str) -> None:
    """Bot folders under the old Mac's home folder (/Users/them/Oomi/…) move to the same place under this one's."""
    old_user = str(Path(old_home).parent) if old_home else ""  # ~/.oomi -> the home folder
    new_user = str(Path.home())
    path = home / "bots.json"
    if not old_user or old_user == new_user or not path.exists():
        return
    try:
        d = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    for b in d.get("bots") or []:
        folder = b.get("folder")
        if isinstance(folder, str) and (folder == old_user or folder.startswith(old_user + "/")):
            b["folder"] = new_user + folder[len(old_user):]
    path.write_text(json.dumps(d, indent=1))
