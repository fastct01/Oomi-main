"""Client-side tools. Each one assesses its own risk; the agent's policy decides what needs approval."""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from . import sandbox
from .changes import IGNORED_DIRS
from .safety import (DANGER, EXEC, HIDDEN, READ, WRITE, Assessment, assess_shell, blocked_by, is_protected,
                     is_within, protected_in_command, protected_write, resolve_path, scan_text_for_risks)

_SIGKILL = getattr(signal, "SIGKILL", signal.SIGTERM)  # Windows has no SIGKILL (_kill_group ends the tree there)
# A command runs in a group of its own so stopping it also stops what it started
_NEW_GROUP = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32"
              else {"start_new_session": True})


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    taint: str = ""  # non-empty => the output contains untrusted content from this source
    # What happened, for the antibody watcher (oomi/antibodies.py): set by run_shell and the file tools.
    command: Optional[str] = None
    exit_code: Optional[int] = None
    output: str = ""
    changed_path: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ToolContext:
    workspace: Path
    cfg: object
    memory: object
    skills: object
    taint: object
    changes: object
    sandbox_info: dict = field(default_factory=dict)
    history: object = None
    # Runs a shell command through the full approval + sandbox path. Set by the agent.
    shell_executor: Optional[Callable[[str, str], tuple]] = None
    # Asks the user a yes/no question. Set by the agent.
    confirm: Optional[Callable[[str, str], bool]] = None
    # Set when the user stops the task from another thread; running commands are killed.
    cancel: Optional[threading.Event] = None
    procs: set = field(default_factory=set)

    def stop_processes(self) -> None:
        for proc in list(self.procs):
            _kill_group(proc, signal.SIGTERM)
        if self.procs:
            threading.Timer(2.0, lambda: [_kill_group(p, _SIGKILL) for p in list(self.procs)]).start()

    @property
    def sandboxed(self) -> bool:
        """Full trust runs commands unsandboxed; otherwise the sandbox is used whenever it works."""
        return bool(getattr(self.cfg, "sandbox", True) and self.sandbox_info.get("available")
                    and getattr(self.cfg, "trust", "project") != "full")


class Tool:
    name = ""
    description = ""
    schema: dict = {}

    def definition(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.schema}

    def summary(self, inp: dict) -> str:
        return ", ".join(f"{k}={str(v)[:60]!r}" for k, v in inp.items())

    def assess(self, inp: dict, ctx: ToolContext) -> Assessment:
        return Assessment(READ, self.summary(inp))

    def run(self, inp: dict, ctx: ToolContext, approved: bool = False) -> ToolResult:
        raise NotImplementedError


def _rel(p: Path, ws: Path) -> str:
    return str(p.relative_to(ws)) if is_within(p, ws) else str(p)


def _obj(props: dict, required: list) -> dict:
    return {"type": "object", "properties": props, "required": required}


def _path_assessment(level: str, path: str, ctx: ToolContext, summary: str) -> Assessment:
    p = resolve_path(path, ctx.workspace)
    spill = Path(ctx.cfg.home) / "spill"
    outside = not is_within(p, ctx.workspace) and not is_within(p, spill)
    a = Assessment(level, summary, outside_workspace=outside, protected=is_protected(p, ctx.cfg.protected_paths))
    if level == WRITE and ".git" in p.parts:
        a.ask_reason = "changes git internals (hooks and config there can run code on your next git command)"
    elif level == WRITE:
        pat = protected_write(p, getattr(ctx.cfg, "protected_write_names", []))
        if pat:
            a.ask_reason = (f"changes {p.name}, a protected file (instructions an agent follows, or secrets; "
                            f"\"{pat}\" is on the list in Settings)")
    return a


# ---------------------------------------------------------------------------------------------
# Files

class ReadFile(Tool):
    name = "read_file"
    description = ("Read a text file with line numbers. Use offset/limit to page through long files "
                   "(including spill files of truncated tool output).")
    schema = _obj({
        "path": {"type": "string", "description": "Path, relative to the workspace or absolute."},
        "offset": {"type": "integer", "description": "1-based line to start from (default 1)."},
        "limit": {"type": "integer", "description": "Maximum lines to return (default 400)."},
    }, ["path"])

    def summary(self, inp):
        return inp.get("path", "")

    def assess(self, inp, ctx):
        return _path_assessment(READ, inp.get("path", ""), ctx, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        p = resolve_path(inp["path"], ctx.workspace)
        if not p.exists():
            return ToolResult(f"No such file: {p}", True)
        if p.is_dir():
            return ToolResult(f"{p} is a directory; use list_dir.", True)
        raw = p.read_bytes()
        if b"\x00" in raw[:8192]:
            return ToolResult(f"{p} looks binary ({len(raw):,} bytes); not shown.", True)
        lines = raw.decode("utf-8", errors="replace").splitlines()
        offset = max(int(inp.get("offset") or 1), 1)
        limit = min(max(int(inp.get("limit") or 400), 1), 2000)
        chunk = lines[offset - 1: offset - 1 + limit]
        body = "\n".join(f"{i:>6}\t{line[:2000]}" for i, line in enumerate(chunk, start=offset))
        end = offset + len(chunk) - 1
        footer = f"\n(lines {offset}-{end} of {len(lines)})" if len(lines) > len(chunk) else ""
        taint = "file in Downloads" if is_within(p, Path.home() / "Downloads") else ""
        return ToolResult((body or "(empty file)") + footer, taint=taint)


HIDDEN_WRITE = (f"Not written: the text contains {HIDDEN}, which stands for a secret oomi hid from you, so writing it "
                "would replace the real secret. Leave that part of the file as it is (edit around it), or ask the user "
                "to fill it in.")


class WriteFile(Tool):
    name = "write_file"
    description = ("Create or completely overwrite a file. Prefer edit_file for changes to existing files. "
                   "The previous version is backed up so the user can /undo.")
    schema = _obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"])

    def summary(self, inp):
        return f"{inp.get('path', '')} ({len(inp.get('content', '')):,} chars)"

    def assess(self, inp, ctx):
        return _path_assessment(WRITE, inp.get("path", ""), ctx, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        p = resolve_path(inp["path"], ctx.workspace)
        if p.is_dir():
            return ToolResult(f"{p} is a folder; give a file path.", True)
        if HIDDEN in inp.get("content", ""):
            return ToolResult(HIDDEN_WRITE, True)
        existed = p.exists()
        ctx.changes.before_write(p, "write_file")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(inp["content"])
        except OSError as e:
            ctx.changes.discard_last()
            return ToolResult(f"Couldn't write {p}: {e}", True)
        n = inp["content"].count("\n") + (0 if inp["content"].endswith("\n") else 1)
        return ToolResult(f"{'Overwrote' if existed else 'Created'} {_rel(p, ctx.workspace)} ({n} lines).",
                          changed_path=_rel(p, ctx.workspace))


class EditFile(Tool):
    name = "edit_file"
    description = ("Replace exact text in a file. old_text must match exactly once (include surrounding lines to "
                   "make it unique) unless replace_all is true. Backs up the file first.")
    schema = _obj({
        "path": {"type": "string"},
        "old_text": {"type": "string", "description": "Exact text to replace, including whitespace."},
        "new_text": {"type": "string"},
        "replace_all": {"type": "boolean"},
    }, ["path", "old_text", "new_text"])

    def summary(self, inp):
        return inp.get("path", "")

    def assess(self, inp, ctx):
        return _path_assessment(WRITE, inp.get("path", ""), ctx, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        p = resolve_path(inp["path"], ctx.workspace)
        if not p.is_file():
            return ToolResult(f"No such file: {p}", True)
        text = p.read_text()
        old, new = inp["old_text"], inp["new_text"]
        if HIDDEN in new or HIDDEN in old:
            return ToolResult(HIDDEN_WRITE, True)
        if not old:
            return ToolResult("old_text is empty; use write_file to create files.", True)
        n = text.count(old)
        if n == 0:
            return ToolResult("old_text was not found. Re-read the file and copy the exact text (whitespace matters).", True)
        if n > 1 and not inp.get("replace_all"):
            return ToolResult(f"old_text appears {n} times. Include more surrounding text or set replace_all.", True)
        ctx.changes.before_write(p, "edit_file")
        updated = text.replace(old, new) if inp.get("replace_all") else text.replace(old, new, 1)
        try:
            p.write_text(updated)
        except OSError as e:
            ctx.changes.discard_last()
            return ToolResult(f"Couldn't write {p}: {e}", True)
        start = updated[: updated.find(new)].count("\n") + 1 if new else 1
        lines = updated.splitlines()
        lo, hi = max(start - 3, 1), min(start + new.count("\n") + 3, len(lines))
        snippet = "\n".join(f"{i:>6}\t{lines[i - 1]}" for i in range(lo, hi + 1))
        return ToolResult(f"Edited {_rel(p, ctx.workspace)} ({n if inp.get('replace_all') else 1} replacement(s)).\n{snippet}",
                          changed_path=_rel(p, ctx.workspace))


class ListDir(Tool):
    name = "list_dir"
    description = "List a directory as a tree (skips .git, node_modules, virtualenvs, caches)."
    schema = _obj({"path": {"type": "string"}, "depth": {"type": "integer", "description": "1-4, default 2"}}, [])

    def summary(self, inp):
        return inp.get("path", ".")

    def assess(self, inp, ctx):
        return _path_assessment(READ, inp.get("path", "."), ctx, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        root = resolve_path(inp.get("path") or ".", ctx.workspace)
        if not root.is_dir():
            return ToolResult(f"Not a directory: {root}", True)
        depth = min(max(int(inp.get("depth") or 2), 1), 4)
        out, count = [f"{root}/"], 0

        def walk(d: Path, level: int, prefix: str):
            nonlocal count
            try:
                entries = sorted(d.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
            except PermissionError:
                out.append(prefix + "(permission denied)")
                return
            for e in entries:
                if e.name in IGNORED_DIRS:
                    continue
                count += 1
                if count > 400:
                    return
                if e.is_dir():
                    out.append(f"{prefix}{e.name}/")
                    if level < depth:
                        walk(e, level + 1, prefix + "  ")
                else:
                    try:
                        size = e.stat().st_size
                    except OSError:
                        size = 0
                    out.append(f"{prefix}{e.name}  ({size:,} B)")

        walk(root, 1, "  ")
        if count > 400:
            out.append("  … (truncated at 400 entries; list a subdirectory)")
        return ToolResult("\n".join(out))


class Grep(Tool):
    name = "grep"
    description = "Search file contents with a regex (ripgrep if installed). Returns path:line:text matches."
    schema = _obj({
        "pattern": {"type": "string"},
        "path": {"type": "string", "description": "Directory or file (default: workspace)."},
        "glob": {"type": "string", "description": "Filter files, e.g. '*.py'."},
        "ignore_case": {"type": "boolean"},
    }, ["pattern"])

    def summary(self, inp):
        return f"/{inp.get('pattern', '')}/ in {inp.get('path', '.')}"

    def assess(self, inp, ctx):
        return _path_assessment(READ, inp.get("path", "."), ctx, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        root = resolve_path(inp.get("path") or ".", ctx.workspace)
        pattern = inp["pattern"]
        rg = shutil.which("rg")
        if rg:
            argv = [rg, "-n", "--no-heading", "--color", "never", "-m", "50", "--max-columns", "300"]
            if inp.get("ignore_case"):
                argv.append("-i")
            if inp.get("glob"):
                argv += ["-g", inp["glob"]]
            argv += ["--", pattern, str(root)]
            r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
            if r.returncode == 1:
                return ToolResult("No matches.")
            if r.returncode not in (0, 1):
                return ToolResult(r.stderr.strip() or "grep failed", True)
            return ToolResult(r.stdout.strip())
        try:
            rx = re.compile(pattern, re.I if inp.get("ignore_case") else 0)
        except re.error as e:
            return ToolResult(f"Invalid regex: {e}", True)
        hits = []
        files = [root] if root.is_file() else root.rglob(inp.get("glob") or "*")
        for f in files:
            if any(part in IGNORED_DIRS for part in f.parts) or not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{f}:{i}:{line[:300]}")
                        if len(hits) >= 500:
                            break
            except OSError:
                continue
        return ToolResult("\n".join(hits) if hits else "No matches.")


# ---------------------------------------------------------------------------------------------
# Shell

def run_command(command: str, ctx: ToolContext, timeout: int, allow_network: bool, cwd: Optional[Path] = None,
                use_sandbox: bool = True) -> tuple:
    """Run a command (sandboxed when possible). Returns (exit_code, output, sandboxed)."""
    info = ctx.sandbox_info
    profile = None
    if ctx.sandboxed and use_sandbox:
        profile = sandbox.build_profile(
            ctx.workspace, ctx.cfg.sandbox_extra_writable, ctx.cfg.protected_paths,
            allow_network=allow_network or not info.get("net_rule"),
            net_rule=info.get("net_rule") or sandbox.NETWORK_DENY_CANDIDATES[0],
        )
    argv = sandbox.wrap(command, profile)
    env = dict(os.environ)
    env.update(PAGER="cat", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0", TERM="dumb", NO_COLOR="1",
               HOMEBREW_NO_AUTO_UPDATE="1", PYTHONUNBUFFERED="1")
    for secret in ("ANTHROPIC_API_KEY", "VOYAGE_API_KEY"):
        env.pop(secret, None)
    proc = subprocess.Popen(argv, cwd=str(cwd or ctx.workspace), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, **_NEW_GROUP)
    ctx.procs.add(proc)
    try:
        if ctx.cancel is not None and ctx.cancel.is_set():
            _kill_group(proc, _SIGKILL)
        out, _ = proc.communicate(timeout=timeout)
        rc = proc.returncode
        # killed by a signal (negative) on a Mac; on Windows taskkill leaves an ordinary non-zero exit code
        if ctx.cancel is not None and ctx.cancel.is_set() and rc is not None and rc != 0:
            out = (out or b"") + b"\n[oomi] Stopped by the user."
    except subprocess.TimeoutExpired:
        _kill_group(proc, _SIGKILL)
        out, _ = proc.communicate()
        out = (out or b"") + f"\n[oomi] Timed out after {timeout}s and was killed.".encode()
        rc = 124
    finally:
        ctx.procs.discard(proc)
    return rc, (out or b"").decode("utf-8", errors="replace"), profile is not None


def _kill_group(proc, sig) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":  # no process groups or SIGKILL: end the command and everything it started
        r = subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
        if r.returncode != 0:
            try:
                proc.kill()
            except OSError:
                pass
        return
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except OSError:
            pass


class RunShell(Tool):
    name = "run_shell"
    description = (
        "Run a shell command (zsh on macOS) in the workspace and return exit code + combined output. "
        "Non-interactive: stdin is closed. Runs inside the macOS sandbox: it can write only in the workspace, "
        "temp dirs and tool caches; credentials are unreadable; while untrusted web content is in the "
        "conversation, network access is off unless the user approves it."
    )
    schema = _obj({
        "command": {"type": "string"},
        "timeout": {"type": "integer", "description": "Seconds (default 120, max 600)."},
        "cwd": {"type": "string", "description": "Working directory relative to the workspace."},
        "outside_sandbox": {"type": "boolean", "description": (
            "Run without the macOS sandbox. Only after a command failed because the sandbox blocked something "
            "legitimate (e.g. brew install, writing to ~/Library). Always needs the user's approval.")},
    }, ["command"])

    def summary(self, inp):
        return inp.get("command", "")

    def assess(self, inp, ctx):
        a = assess_shell(inp.get("command", ""), ctx.cfg.protected_paths)
        pat = blocked_by(inp.get("command", ""), getattr(ctx.cfg, "deny_commands", []))
        if pat:
            a.hard_deny = f"it matches \"{pat}\" on your block list"
            a.level = DANGER
        elif not a.readonly_shell:
            pat = protected_in_command(inp.get("command", ""), getattr(ctx.cfg, "protected_write_names", []))
            if pat:
                a.ask_reason = a.ask_reason or (f"uses a protected file, which could change it or send it "
                                                f"somewhere (\"{pat}\" is on the list in Settings)")
        if inp.get("outside_sandbox") and ctx.sandboxed and not a.hard_deny:
            a.level = DANGER
            a.reasons = ["runs outside the macOS sandbox (full access to your files and the network)"] + a.reasons
            if ctx.taint.active:
                a.reasons.append("untrusted web content is in the conversation")
        cwd = inp.get("cwd")
        if cwd:
            where = resolve_path(cwd, ctx.workspace)
            if is_protected(where, ctx.cfg.protected_paths):
                a.protected = True
            if not is_within(where, ctx.workspace):
                a.outside_workspace = True
                a.local_only = False  # relative paths now point outside the project
                if a.level == EXEC and not a.readonly_shell:
                    a.ask_reason = a.ask_reason or "runs outside the project folder"
        return a

    def run(self, inp, ctx, approved=False):
        command = inp["command"]
        timeout = min(int(inp.get("timeout") or ctx.cfg.shell_timeout), 600)
        cwd = resolve_path(inp["cwd"], ctx.workspace) if inp.get("cwd") else ctx.workspace
        if not cwd.is_dir():
            return ToolResult(f"cwd does not exist: {cwd}", True)
        a = assess_shell(command, ctx.cfg.protected_paths)
        # Under taint, network is granted only to a command that visibly uses it and that the user approved
        # knowing that. A command that hides its network use (e.g. a script) stays offline.
        allow_network = (not ctx.taint.active) or (approved and a.network)
        unsandboxed = bool(inp.get("outside_sandbox")) and approved
        rc, out, sandboxed = run_command(command, ctx, timeout, allow_network, cwd, use_sandbox=not unsandboxed)
        text = f"exit code: {rc}\n{out.rstrip()}" if out.strip() else f"exit code: {rc} (no output)"
        if sandboxed and re.search(r"Operation not permitted|sandbox|Could not resolve host|Network is unreachable", out):
            text += ("\n[oomi] The macOS sandbox may have blocked this: writes are limited to the workspace and temp "
                     "dirs, credential paths are unreadable" +
                     ("" if allow_network else ", and network is off because untrusted content is in the conversation") +
                     ". If it's genuinely needed, retry with outside_sandbox=true (the user must approve) or explain why to "
                     "the user. Don't try to work around the sandbox in other ways.")
        taint = f"network command: {command[:80]}" if a.network else ""
        return ToolResult(text, is_error=rc != 0, taint=taint, command=command, exit_code=rc, output=out)


# ---------------------------------------------------------------------------------------------
# Memory

def _taint_gate(ctx, summary: str, what: str) -> Assessment:
    if ctx.taint.active:
        return Assessment(WRITE, summary, ask_reason=(
            f"{what} while untrusted web content is in the conversation (a prompt injection could be behind it)"))
    return Assessment(READ, summary)


class Remember(Tool):
    name = "remember"
    description = (
        "Save one durable fact for future sessions: the user's preferences, decisions, people, project facts. "
        "Not for transient task state. To correct an existing memory pass replaces=<id>. "
        "Tag preferences with 'preference' so they're always loaded. Facts are personal to you (this bot) unless "
        "shared=true: share what every bot should know about the user; keep your own project and role notes personal."
    )
    schema = _obj({
        "fact": {"type": "string", "description": "One short, self-contained statement."},
        "tags": {"type": "array", "items": {"type": "string"}},
        "replaces": {"type": "integer", "description": "Id of a memory this corrects."},
        "shared": {"type": "boolean", "description": "Save it for every bot, not just you."},
    }, ["fact"])

    def summary(self, inp):
        return inp.get("fact", "")[:1000]  # shown in full when approval is needed

    def assess(self, inp, ctx):
        return _taint_gate(ctx, self.summary(inp), "saving to long-term memory")

    def run(self, inp, ctx, approved=False):
        try:
            fid, status = ctx.memory.add(inp["fact"], inp.get("tags") or [], source="agent", replaces=inp.get("replaces"),
                                         personal=bool(getattr(ctx.memory, "scope", "")) and not inp.get("shared"))
        except ValueError as e:
            return ToolResult(str(e), True)
        return ToolResult(f"Memory #{fid} {status}.")


class Recall(Tool):
    name = "recall"
    description = "Search long-term memory. The most relevant memories are already injected each turn; use this for more."
    schema = _obj({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"])

    def summary(self, inp):
        return inp.get("query", "")

    def run(self, inp, ctx, approved=False):
        facts = ctx.memory.search(inp["query"], min(int(inp.get("k") or 8), 25))
        if not facts:
            return ToolResult("No matching memories.")
        return ToolResult("\n".join(f"#{f.id} [{f.tags}] {f.text}" if f.tags else f"#{f.id} {f.text}" for f in facts))


class Forget(Tool):
    name = "forget"
    description = "Delete a memory by id (when it's wrong, outdated or the user asks)."
    schema = _obj({"id": {"type": "integer"}}, ["id"])

    def summary(self, inp):
        return f"#{inp.get('id')}"

    def assess(self, inp, ctx):
        try:
            f = ctx.memory.get(int(inp.get("id")))
        except (TypeError, ValueError):
            f = None
        return _taint_gate(ctx, f"forget #{inp.get('id')}: {f.text if f else '(unknown)'}", "deleting a memory")

    def run(self, inp, ctx, approved=False):
        return ToolResult(f"Forgot #{inp['id']}." if ctx.memory.forget(int(inp["id"])) else f"No memory #{inp['id']}.")


# ---------------------------------------------------------------------------------------------
# Skills

class LoadSkill(Tool):
    name = "load_skill"
    description = "Load a saved skill's full instructions (see the skills index in the system prompt)."
    schema = _obj({"name": {"type": "string"}}, ["name"])

    def summary(self, inp):
        return inp.get("name", "")

    def run(self, inp, ctx, approved=False):
        s = ctx.skills.get(inp["name"])
        if not s:
            return ToolResult(f"No skill named {inp['name']}.", True)
        ctx.skills.record_use(s.name)
        taint = f"imported skill {s.name}" if s.status == "imported" else ""
        return ToolResult(ctx.skills.render_for_agent(s), taint=taint)


class ProposeSkill(Tool):
    name = "propose_skill"
    description = (
        "Save a reusable procedure as a skill, after you've completed a multi-step task successfully and it's "
        "likely to come up again. verify_command must be a shell command that exits 0 only if the procedure "
        "actually worked (e.g. runs the tests, checks the output file), not a no-op. The skill is promoted only "
        "if verification passes; otherwise it stays an unverified candidate. Revising an existing skill uses the same name."
    )
    schema = _obj({
        "name": {"type": "string", "description": "kebab-case, e.g. 'deploy-flask-to-render'"},
        "description": {"type": "string", "description": "When to use this skill, in one sentence."},
        "instructions": {"type": "string", "description": "Markdown steps, commands and pitfalls."},
        "verify_command": {"type": "string"},
    }, ["name", "description", "instructions", "verify_command"])

    def summary(self, inp):
        return f"{inp.get('name', '')} (verify: {inp.get('verify_command', '')})"

    def assess(self, inp, ctx):
        if ctx.taint.active:
            return Assessment(WRITE, self.summary(inp), ask_reason=(
                "saving a skill while untrusted web content is in the conversation"))
        return Assessment(READ, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        try:
            s = ctx.skills.propose(inp["name"], inp["description"], inp["instructions"], inp["verify_command"])
        except ValueError as e:
            return ToolResult(f"Not saved: {e}", True)
        if ctx.shell_executor is None:
            return ToolResult(f"Saved {s.name} as an unverified candidate (no shell available to verify).")
        rc, output, ran = ctx.shell_executor(s.verify, f"verify skill {s.name}")
        if not ran:
            ctx.skills.record_verify_failure(s.name, None, output)
            return ToolResult(f"Saved {s.name} as an unverified candidate: verification didn't run ({output}).")
        if rc == 0:
            if ctx.confirm and not ctx.confirm(f"Save skill '{s.name}'?",
                                               f"{s.description}\nVerified: `{s.verify}` exited 0."):
                ctx.skills.discard_candidate(s.name)
                return ToolResult(f"Verification passed, but the user chose not to save {s.name}.")
            ctx.skills.promote(s.name, "verified", rc, output)
            return ToolResult(f"Skill {s.name} verified (exit 0) and saved with the user's approval.")
        ctx.skills.record_verify_failure(s.name, rc, output)
        tail = "\n".join(output.strip().splitlines()[-15:])
        return ToolResult(
            f"Verification failed (exit {rc}); {s.name} stays an unverified candidate. Fix the procedure or the "
            f"verify command and propose again.\n{tail}", True)


class SkillFeedback(Tool):
    name = "skill_feedback"
    description = "Report whether a skill you loaded worked this time, with a short note on anything that differed."
    schema = _obj({"name": {"type": "string"}, "worked": {"type": "boolean"}, "note": {"type": "string"}}, ["name", "worked"])

    def summary(self, inp):
        note = f" — note: {inp.get('note')}" if inp.get("note") else ""
        return f"{inp.get('name', '')}: {'worked' if inp.get('worked') else 'failed'}{note}"

    def assess(self, inp, ctx):
        note = inp.get("note") or ""
        if note and scan_text_for_risks(note, ctx.cfg.protected_paths):
            return Assessment(WRITE, self.summary(inp), ask_reason="the note contains commands or instructions that "
                              "would be shown to future sessions")
        if note:
            return _taint_gate(ctx, self.summary(inp), "saving a note future sessions will read")
        return Assessment(READ, self.summary(inp))

    def run(self, inp, ctx, approved=False):
        try:
            s = ctx.skills.feedback(inp["name"], bool(inp["worked"]), inp.get("note", ""))
        except ValueError as e:
            return ToolResult(str(e), True)
        ok, bad = s.outcomes
        extra = ""
        if s.status == "retired":
            extra = (" It kept failing, so it has been retired and won't be offered again. If you know how to fix it, "
                     "propose a revised version with the same name.")
        elif s.unreliable:
            extra = " It is now flagged unreliable; consider proposing a revised version."
        return ToolResult(f"Recorded. {s.name}: worked {ok}/{ok + bad}.{extra}")


class FindSkill(Tool):
    name = "find_skill"
    description = "Search saved skills by what you're about to do. Use it when the skills index says there are more."
    schema = _obj({"query": {"type": "string"}}, ["query"])

    def summary(self, inp):
        return inp.get("query", "")

    def run(self, inp, ctx, approved=False):
        hits = ctx.skills.search(inp["query"], 5)
        if not hits:
            return ToolResult("No matching skills.")
        return ToolResult("\n".join(f"- {s.name}: {s.description}" for s in hits))


class SearchHistory(Tool):
    name = "search_history"
    description = ("Search past conversations with the user (every session and project). Use it when they refer to "
                   "something discussed before, or you need context from earlier work.")
    schema = _obj({"query": {"type": "string"}, "k": {"type": "integer"}}, ["query"])

    def summary(self, inp):
        return inp.get("query", "")

    def run(self, inp, ctx, approved=False):
        if ctx.history is None:
            return ToolResult("History isn't available.", True)
        rows = ctx.history.search(inp["query"], min(int(inp.get("k") or 8), 20))
        if not rows:
            return ToolResult("Nothing found in past conversations.")
        # Past assistant replies may have repeated web content, so they count as untrusted.
        taint = "past conversations" if any(r["role"] != "user" for r in rows) else ""
        out = []
        for r in rows:
            when = time.strftime("%Y-%m-%d", time.localtime(r["ts"]))
            text = " ".join(r["text"].split())
            out.append(f"[{when} · {os.path.basename(r['workspace'] or '')} · {r['role']}] {text[:400]}")
        return ToolResult("\n".join(out), taint=taint)


def all_tools() -> list:
    return [ReadFile(), WriteFile(), EditFile(), ListDir(), Grep(), RunShell(),
            Remember(), Recall(), Forget(), SearchHistory(), LoadSkill(), FindSkill(), ProposeSkill(), SkillFeedback()]


def server_tools(cfg) -> list:
    tools = []
    if cfg.web_search:
        tools.append({"type": "web_search_20250305", "name": "web_search", "max_uses": cfg.web_search_max_uses})
    if cfg.web_fetch:
        tools.append({"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": cfg.web_fetch_max_uses,
                      "max_content_tokens": cfg.web_fetch_max_content_tokens})
    return tools


SERVER_RESULT_TYPES = {"web_search_tool_result": "web search", "web_fetch_tool_result": "web page"}
