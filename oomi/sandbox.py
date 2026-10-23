"""macOS Seatbelt sandbox for shell commands (the same mechanism Claude Code and Codex use on Mac).

Profile, in plain words:
* everything is allowed by default (so normal dev tools keep working) except:
* writes are denied everywhere except the workspace, temp dirs and a few tool caches;
* protected paths (SSH keys, Keychains, browser profiles, ...) can be neither read nor written;
* `.git/hooks` in the workspace is read-only, so a command can't plant code that runs on your next commit;
* when the conversation contains untrusted content, outbound network is denied.

`sandbox-exec` is deprecated by Apple but still ships with every macOS release. Oomi probes it
at start-up and falls back to approval-gated, unsandboxed execution when it isn't usable.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# `(deny network*)` is the widely used, known-working form. It also blocks localhost and Unix sockets, which is
# the safe side: it only applies while untrusted content is in the conversation.
NETWORK_DENY_CANDIDATES = ["(deny network*)"]

_probe_cache: dict = {}


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _q(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def build_profile(
    workspace: Path,
    extra_writable: list,
    protected: list,
    allow_network: bool,
    net_rule: str = NETWORK_DENY_CANDIDATES[0],
) -> str:
    writable = [str(workspace), _real(tempfile.gettempdir()), "/private/tmp", "/private/var/folders"]
    writable += [_real(p) for p in extra_writable]
    seen, uniq = set(), []
    for p in writable:
        rp = _real(p)
        if rp not in seen and rp != "/":
            seen.add(rp)
            uniq.append(rp)

    lines = ["(version 1)", "(allow default)", "(deny file-write*)", "(allow file-write*"]
    lines += [f"    (subpath {_q(p)})" for p in uniq]
    lines += [
        '    (literal "/dev/null")',
        '    (literal "/dev/zero")',
        '    (literal "/dev/dtracehelper")',
        '    (regex #"^/dev/tty")',
        '    (regex #"^/dev/fd/")',
        '    (literal "/dev/ptmx")',
        ")",
    ]
    # Later rules win in SBPL, so these denials override the allows above.
    hooks = Path(workspace) / ".git" / "hooks"
    lines.append(f"(deny file-write* (subpath {_q(_real(str(hooks)))}))")
    for p in protected:
        rp = _real(p)
        lines.append(f"(deny file-read* file-write* (subpath {_q(rp)}))")
    if not allow_network:
        lines.append(net_rule)
    return "\n".join(lines) + "\n"


def probe() -> dict:
    """Check whether sandbox-exec works here and which network rule it accepts."""
    if "result" in _probe_cache:
        return dict(_probe_cache["result"])  # a copy: callers keep their own and may change it
    result = {"available": False, "net_rule": None, "reason": ""}
    if sys.platform != "darwin":
        result["reason"] = "not macOS"
    elif not shutil.which("sandbox-exec"):
        result["reason"] = "sandbox-exec not found"
    else:
        base = "(version 1)\n(allow default)\n"
        try:
            ok = subprocess.run(["sandbox-exec", "-p", base, "/usr/bin/true"], capture_output=True, timeout=10)
            if ok.returncode != 0:
                result["reason"] = ok.stderr.decode(errors="replace").strip() or "sandbox-exec failed"
            else:
                result["available"] = True
                for rule in NETWORK_DENY_CANDIDATES:
                    r = subprocess.run(["sandbox-exec", "-p", base + rule + "\n", "/usr/bin/true"],
                                       capture_output=True, timeout=10)
                    if r.returncode == 0:
                        result["net_rule"] = rule
                        break
        except (OSError, subprocess.TimeoutExpired) as e:
            result["reason"] = str(e)
    _probe_cache["result"] = result
    return dict(result)


def shell_argv(command: str) -> list:
    # Non-login, non-interactive: no .zprofile/.zshrc, so user aliases can't change what a command means.
    # PATH comes from the environment Oomi was started in, so Homebrew tools are still found.
    if sys.platform == "darwin" and os.path.exists("/bin/zsh"):
        return ["/bin/zsh", "-c", command]
    return [shutil.which("bash") or "/bin/sh", "-c", command]


def wrap(command: str, profile: str | None) -> list:
    argv = shell_argv(command)
    if profile is None:
        return argv
    return ["sandbox-exec", "-p", profile] + argv
