"""API keys: environment first, then the macOS Keychain (or a 0600 file elsewhere)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from .config import oomi_home

SERVICE = "oomi"
ENV = {"anthropic": "ANTHROPIC_API_KEY", "voyage": "VOYAGE_API_KEY"}


def _is_mac() -> bool:
    return sys.platform == "darwin"


def get_key(kind: str = "anthropic") -> str | None:
    env = os.environ.get(ENV[kind])
    if env:
        return env.strip()
    if _is_mac():
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", SERVICE, "-a", kind, "-w"],
                capture_output=True, text=True, timeout=10,
            )
            if out.returncode == 0 and out.stdout.strip():
                return out.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
        return None
    path = oomi_home() / "credentials.json"
    if path.exists():
        try:
            return json.loads(path.read_text()).get(kind)
        except (OSError, json.JSONDecodeError):
            return None
    return None


KEY_RX = re.compile(r"^[A-Za-z0-9_\-]{20,300}$")


def store_key_interactive(kind: str = "anthropic") -> bool:
    """Ask for a key once (input hidden) and store it."""
    import getpass

    secret = getpass.getpass(f"Paste your {kind} API key (input is hidden), then press Enter: ").strip()
    if not secret:
        return False
    if not KEY_RX.match(secret):
        print("That doesn't look like an API key (letters, digits, - and _ only). Nothing was saved.")
        return False
    return store_key(kind, secret)


def store_key(kind: str, secret: str) -> bool:
    """Store a key. On macOS it goes into the Keychain through `security -i`, which reads the command from stdin,
    so the key never appears in a process listing."""
    secret = secret.strip()
    if not KEY_RX.match(secret):
        return False
    if _is_mac():
        cmd = f'add-generic-password -U -s {SERVICE} -a {kind} -l "Oomi ({kind})" -w {secret}\n'
        r = subprocess.run(["security", "-i"], input=cmd, text=True, capture_output=True)
        return r.returncode == 0 and get_key(kind) == secret
    path = oomi_home() / "credentials.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            data = {}
    data[kind] = secret
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    return True


# -- other secrets (connected apps' tokens) ---------------------------------------------------------------
SECRET_NAME = re.compile(r"^[a-z0-9][a-z0-9:_.-]{0,80}$")


def get_secret(name: str) -> str | None:
    """A stored secret by name (Keychain on a Mac, a 0600 file elsewhere). Values are kept base64-encoded so any
    text round-trips through `security`."""
    import base64

    if not SECRET_NAME.match(name):
        return None
    if _is_mac():
        try:
            out = subprocess.run(["security", "find-generic-password", "-s", SERVICE, "-a", name, "-w"],
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode != 0 or not out.stdout.strip():
            return None
        try:
            return base64.urlsafe_b64decode(out.stdout.strip()).decode()
        except (ValueError, UnicodeDecodeError):
            return None
    return _secret_file().get(name)


def store_secret(name: str, value: str) -> bool:
    import base64

    if not SECRET_NAME.match(name):
        return False
    if _is_mac():
        enc = base64.urlsafe_b64encode(value.encode()).decode()
        cmd = f'add-generic-password -U -s {SERVICE} -a {name} -l "Oomi ({name})" -w {enc}\n'
        r = subprocess.run(["security", "-i"], input=cmd, text=True, capture_output=True)
        return r.returncode == 0
    data = _secret_file()
    data[name] = value
    _write_secret_file(data)
    return True


def delete_secret(name: str) -> None:
    if not SECRET_NAME.match(name):
        return
    if _is_mac():
        subprocess.run(["security", "delete-generic-password", "-s", SERVICE, "-a", name], capture_output=True)
        return
    data = _secret_file()
    if data.pop(name, None) is not None:
        _write_secret_file(data)


def _secret_file() -> dict:
    path = oomi_home() / "secrets.json"
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_secret_file(data: dict) -> None:
    path = oomi_home() / "secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
