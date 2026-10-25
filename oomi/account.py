"""Your account: a personal profile (name, email, a few lines about you, time zone, reply language, units) that
bots can know, kept in ~/.oomi/account.json and, if you connect one, mirrored to MongoDB Atlas.

* This Mac is the source of truth; the cloud is a copy. Saving writes here first, then pushes. Pulling replaces
  the profile here with the cloud's, so a second Mac can start from the same one.
* This file syncs the profile only; cloud.py backs up everything else to the same database. API keys and app
  sign-ins never leave the Mac.
* The Atlas connection string holds a password, so it lives in the Keychain, never in account.json.
* pymongo is optional (`pip install 'oomi[cloud]'`) and imported only when sync is used.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from . import keychain

PROFILE_FIELDS = ("name", "email", "about", "timezone", "language", "units", "share_with_bots")
LIMITS = {"name": 80, "email": 254, "about": 1500, "timezone": 64, "language": 40}
UNITS = ("", "metric", "imperial")
EMAIL_RX = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
NAME_RX = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")  # Atlas database and collection names (kept simple)
URI_SECRET = "account:mongodb"


class AccountError(ValueError):
    pass


def blank_profile() -> dict:
    return {"name": "", "email": "", "about": "", "timezone": "", "language": "", "units": "", "share_with_bots": True}


def clean_profile(patch: dict, base: Optional[dict] = None) -> dict:
    """base updated with the known fields of patch, checked. Raises AccountError with a message for the window."""
    out = dict(base or blank_profile())
    for k, v in patch.items():
        if k not in PROFILE_FIELDS:
            continue
        if k == "share_with_bots":
            if not isinstance(v, bool):
                raise AccountError("That switch takes on or off.")
            out[k] = v
            continue
        if not isinstance(v, str):
            raise AccountError(f"The {k} must be text.")
        v = v.strip() if k == "about" else " ".join(v.split())
        if len(v) > LIMITS.get(k, 40):
            raise AccountError(f"The {k} can be at most {LIMITS[k]:,} characters.")
        if k == "email" and v and not EMAIL_RX.match(v):
            raise AccountError("That doesn't look like an email address.")
        if k == "timezone" and v:
            try:
                from zoneinfo import ZoneInfo

                ZoneInfo(v)
            except Exception:  # noqa: BLE001  (unknown zone, or a path-like name)
                raise AccountError("Use a time zone name like Europe/London or America/New_York.")
        if k == "units" and v not in UNITS:
            raise AccountError("Units are metric or imperial.")
        out[k] = v
    return out


def profile_prompt(p: dict) -> str:
    """What bots are told about you (empty when sharing is off or there's nothing to say)."""
    if not p.get("share_with_bots", True):
        return ""
    lines = []
    if p.get("name"):
        lines.append(f"- Name: {p['name']}")
    if p.get("email"):
        lines.append(f"- Email: {p['email']}")
    if p.get("timezone"):
        lines.append(f"- Time zone: {p['timezone']}")
    if p.get("language"):
        lines.append(f"- Reply in: {p['language']}")
    if p.get("units"):
        lines.append(f"- Units: {p['units']}")
    if p.get("about"):
        about = p["about"].replace("</about_user", "< /about_user")
        lines.append(f"- About them, in their words: {about}")
    return "\n".join(lines)


class MongoMirror:
    """One document per account in an Atlas collection: {_id, profile, updated, device}."""

    def __init__(self, uri: str, database: str, collection: str, client_factory: Optional[Callable] = None):
        self.uri, self.database, self.collection = uri, database, collection
        self.client_factory = client_factory
        self._db = None

    def database_handle(self):
        """The database itself: the backup of everything else (cloud.py) keeps its own collections next to this one."""
        if self._db is None:
            if self.client_factory is not None:
                client = self.client_factory(self.uri)
            else:
                try:
                    from pymongo import MongoClient
                except ImportError:
                    raise AccountError("Cloud sync needs pymongo. Install it with: pip install 'oomi[cloud]'")
                try:
                    client = MongoClient(self.uri, serverSelectionTimeoutMS=8000, appname="oomi")
                except Exception as e:  # noqa: BLE001  (e.g. offline: the mongodb+srv:// DNS lookup fails right here)
                    raise AccountError(f"Couldn't reach the database: {str(e)[:200]}")
            self._db = client[self.database]
        return self._db

    def _collection(self):
        return self.database_handle()[self.collection]

    def ping(self) -> None:
        try:
            self._collection().find_one({"_id": "__oomi_ping__"})
        except AccountError:
            raise
        except Exception as e:  # noqa: BLE001  (auth, network, DNS: all reported the same way)
            raise AccountError(f"Couldn't reach the database: {str(e)[:200]}")

    def push(self, account_id: str, profile: dict, device: str) -> float:
        now = time.time()
        try:
            self._collection().replace_one({"_id": account_id},
                                           {"_id": account_id, "profile": profile, "updated": now, "device": device},
                                           upsert=True)
        except AccountError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AccountError(f"Couldn't save to the database: {str(e)[:200]}")
        return now

    def pull(self, account_id: str) -> Optional[dict]:
        try:
            doc = self._collection().find_one({"_id": account_id})
        except AccountError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AccountError(f"Couldn't read from the database: {str(e)[:200]}")
        return doc


class AccountStore:
    def __init__(self, home: Path, secrets_get=None, secrets_put=None, secrets_del=None, client_factory=None):
        self.path = Path(home) / "account.json"
        self.get_secret = secrets_get or keychain.get_secret
        self.put_secret = secrets_put or keychain.store_secret
        self.del_secret = secrets_del or keychain.delete_secret
        self.client_factory = client_factory  # tests: a fake MongoClient

    # -- the file --------------------------------------------------------------------------------------------
    def load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        d["profile"] = clean_profile({k: v for k, v in (d.get("profile") or {}).items() if k in PROFILE_FIELDS}) \
            if isinstance(d.get("profile"), dict) else blank_profile()
        if not isinstance(d.get("id"), str) or not d["id"]:
            d["id"] = "acct_" + uuid.uuid4().hex  # the document's key in the cloud; the same on every Mac you pull to
        d.setdefault("device", "mac_" + uuid.uuid4().hex[:8])
        d["sync"] = d.get("sync") if isinstance(d.get("sync"), dict) else {}
        return d

    def save(self, d: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1))
        tmp.replace(self.path)

    def profile(self) -> dict:
        return self.load()["profile"]

    def prompt_text(self) -> str:
        return profile_prompt(self.profile())

    # -- reading and changing it -----------------------------------------------------------------------------
    def state(self) -> dict:
        d = self.load()
        s = d["sync"]
        return {"profile": d["profile"], "id": d["id"],
                "sync": {"provider": "mongodb" if s.get("enabled") else "", "database": s.get("database", "oomi"),
                         "collection": s.get("collection", "accounts"), "has_uri": bool(self.get_secret(URI_SECRET)),
                         "last_push": s.get("last_push"), "last_pull": s.get("last_pull"), "error": s.get("error", "")}}

    def update(self, patch: dict) -> dict:
        d = self.load()
        d["profile"] = clean_profile(patch, d["profile"])
        d["updated"] = time.time()
        self.save(d)
        if d["sync"].get("enabled"):
            try:
                self.push()
            except AccountError:
                pass  # saved here; the error shows in the sync section
        return self.state()

    # -- the cloud ---------------------------------------------------------------------------------------------
    def _mirror(self, d: dict, uri: Optional[str] = None) -> MongoMirror:
        uri = uri or self.get_secret(URI_SECRET)
        if not uri:
            raise AccountError("Add your MongoDB Atlas connection string first.")
        s = d["sync"]
        return MongoMirror(uri, s.get("database", "oomi"), s.get("collection", "accounts"), self.client_factory)

    def connect(self, uri: str, database: str = "oomi", collection: str = "accounts") -> dict:
        """Check the database answers, keep the connection string in the Keychain, then push this profile."""
        uri = (uri or "").strip()
        d = self.load()
        if uri:
            if not re.match(r"^mongodb(\+srv)?://", uri) or any(c.isspace() for c in uri) or len(uri) > 1000:
                raise AccountError("Paste the connection string from Atlas (it starts with mongodb+srv://).")
        elif not self.get_secret(URI_SECRET):
            raise AccountError("Paste the connection string from Atlas (it starts with mongodb+srv://).")
        for what, v in (("database", database), ("collection", collection)):
            if not NAME_RX.match(v or ""):
                raise AccountError(f"The {what} name can use letters, digits, dots, dashes and underscores.")
        d["sync"].update(database=database, collection=collection)
        mirror = self._mirror(d, uri or None)
        mirror.ping()
        if uri and not self.put_secret(URI_SECRET, uri):
            raise AccountError("Couldn't save the connection string to the Keychain.")
        d["sync"].update(enabled=True, error="")
        self.save(d)
        self.push()
        return self.state()

    def push(self) -> dict:
        d = self.load()
        try:
            d["sync"]["last_push"] = self._mirror(d).push(d["id"], d["profile"], d["device"])
            d["sync"]["error"] = ""
        except AccountError as e:
            d["sync"]["error"] = str(e)
            self.save(d)
            raise
        self.save(d)
        return self.state()

    def pull(self, account_id: str = "") -> dict:
        """Replace the profile here with the cloud's. account_id: another Mac's account to adopt (shown in its window)."""
        d = self.load()
        if account_id:
            if not re.fullmatch(r"acct_[0-9a-f]{32}", account_id):
                raise AccountError("That account ID isn't right; copy it from the other Mac's Account settings.")
        doc = self._mirror(d).pull(account_id or d["id"])
        if not doc or not isinstance(doc.get("profile"), dict):
            raise AccountError("There's no profile in the database for this account yet.")
        d["profile"] = clean_profile({k: v for k, v in doc["profile"].items() if k in PROFILE_FIELDS})
        if account_id:
            d["id"] = account_id
        d["sync"].update(last_pull=time.time(), error="")
        self.save(d)
        return self.state()

    def disconnect(self) -> dict:
        d = self.load()
        d["sync"] = {"database": d["sync"].get("database", "oomi"), "collection": d["sync"].get("collection", "accounts")}
        self.save(d)
        self.del_secret(URI_SECRET)
        return self.state()
