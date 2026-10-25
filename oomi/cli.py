"""Terminal interface."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__, sandbox
from .agent import Agent, NullUI, load_session, quick_command_for
from .config import PRICING, Config, ensure_dirs
from .health import Health
from .history import History
from .keychain import get_key, store_key_interactive
from .llm import ModelError
from .account import AccountStore
from .memory import Memory, VoyageEmbedder
from .safety import TRUST_LEVELS
from .skills import SkillStore, general_store

GHOST = [
    "   ▄▄████▄▄   ",
    " ▄██████████▄ ",
    " ███▄████▄███ ",
    "▄█████▄▄█████▄",
    "██████████████",
    " ████████████ ",
    " ▀██▀▀██▀▀██▀ ",
]

HELP = """[bold]Commands[/]
  /status               health card: key, models, sandbox, cost today, memory, skills
  /changes              every file touched this session · /changes diff [file]
  /undo                 undo the last change · /undo all
  /trust <level>        read-only · project (default) · full
  /plan                 investigate read-only and propose a plan · /go [tweaks] to approve it
  /memory [query]       what oomi remembers · /memory review · /memory edit
  /remember <fact>      teach it something · /forget <id>
  /skills               saved skills · /skills show|approve|remove <name>
  /model <id>           switch model · /effort low|medium|high|xhigh|max
  /budget <usd>         session spending limit
  /compact              summarise older turns now
  /taint                why shell commands are offline · /untaint to clear
  /<name>               a quick command from the quick_commands setting (runs it, no model)
  /reset                fresh session · /exit (Ctrl-D) to quit
Type \"\"\" on its own line to start and end a multi-line message.
Ctrl-C while it's working stops it, then lets you steer with a note."""


def notify(title: str, message: str) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    try:
        subprocess.Popen(
            ["osascript", "-e", "on run argv", "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
             "-e", "end run", message[:180], title],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class ConsoleUI(NullUI):
    def __init__(self, console: Console, cfg: Config):
        self.c, self.cfg = console, cfg
        self.mode = None  # None | "text" | "thinking"
        self.turn_started = time.time()

    def _close(self):
        if self.mode:
            self.c.print()
            self.mode = None

    def text(self, s):
        if self.mode == "thinking":
            self.c.print()
        if self.mode != "text":
            self.mode = "text"
        self.c.print(s, end="", markup=False, highlight=False, soft_wrap=True)

    def thinking(self, s):
        if not self.cfg.show_thinking:
            return
        if self.mode == "text":
            self.c.print()
        if self.mode != "thinking":
            self.c.print("✻ ", style="dim", end="")
            self.mode = "thinking"
        self.c.print(s, end="", style="dim italic", markup=False, highlight=False, soft_wrap=True)

    def end_message(self):
        self._close()

    def tool_start(self, name, summary):
        self._close()
        summary = summary.replace("\n", " ⏎ ")
        self.c.print(f"[cyan]⏺ {name}[/] [dim]{escape(summary[:220])}[/]")

    def tool_end(self, name, result, seconds):
        lines = result.text.strip().splitlines() or [""]
        if lines[0].startswith("exit code:") and len(lines) > 1:
            lines = [f"{lines[0].replace('exit code: ', 'exit ')} · {lines[1]}"] + lines[2:]
        first = lines[0][:160]
        more = f" (+{len(lines) - 1} lines)" if len(lines) > 1 else ""
        style = "red" if result.is_error else "dim"
        took = f" · {seconds:.1f}s" if seconds >= 1 else ""
        self.c.print(f"  [{style}]⎿ {escape(first)}{more}{took}[/]")

    def notice(self, msg, level="info"):
        self._close()
        style = {"warn": "yellow", "error": "bold red"}.get(level, "blue")
        self.c.print(f"[{style}]● {escape(msg)}[/]")

    def approve(self, tool, summary, reason, danger):
        self._close()
        if self.cfg.notify and time.time() - self.turn_started > self.cfg.notify_after_seconds:
            notify("Oomi needs you", summary)
        if tool == "confirm":
            self.c.print(Panel(f"[bold]{escape(summary)}[/]\n\n[dim]{escape(reason)}[/]", border_style="cyan", expand=False))
            opts = "\\[y] yes   \\[n] no"
        else:
            body = f"[bold]{escape(summary)}[/]\n\n[dim]Why I'm asking: {escape(reason)}[/]"
            self.c.print(Panel(body, title=f"Approve {tool}?", border_style="red" if danger else "yellow", expand=False))
            opts = "\\[y] yes   \\[n] no" if danger else "\\[y] yes   \\[a] always this session   \\[n] no"
            opts += "   — or type a note to decline with feedback"
        self.c.print(f"[dim]{opts}[/]")
        try:
            ans = input("› ").strip()
        except (EOFError, KeyboardInterrupt):
            self.c.print()
            return "no", ""
        low = ans.lower()
        if low in ("y", "yes"):
            return "yes", ""
        if low in ("a", "always") and tool != "confirm":
            return ("yes" if danger else "always"), ""
        if low in ("", "n", "no"):
            return "no", ""
        return "no", ans


# ---------------------------------------------------------------------------------------------
# Building blocks

def build_memory(cfg: Config) -> Memory:
    vkey = get_key("voyage")
    embedder = VoyageEmbedder(vkey, cfg.voyage_model, cfg.voyage_endpoint) if vkey else None
    return Memory(cfg.home / "memory.db", embedder=embedder)


def key_problem_panel() -> Panel:
    return Panel("[bold]Your Anthropic API key was rejected.[/]\nReplace it with [bold]oomi setup --force-key[/] "
                 "(get one at platform.claude.com → API keys).", border_style="red", title="Key problem", expand=False)


def build_agent(cfg: Config, console: Console, workspace: Path, resume: bool) -> Agent:
    key = get_key("anthropic")
    if not key and IN_APP:
        key = first_run(cfg, console)
    if not key:
        console.print("[red]No Anthropic API key found.[/] Run [bold]oomi setup[/] (stores it in your Keychain) "
                      "or set ANTHROPIC_API_KEY.")
        sys.exit(1)
    from .llm import LLM

    session = load_session(cfg.home, workspace) if resume else None
    if resume and not session:
        console.print("[dim]No previous session for this folder; starting fresh.[/]")
    cfg.user_profile = AccountStore(cfg.home).prompt_text()
    try:  # ~/.oomi/INSTRUCTIONS.md: standing instructions for the terminal (the app has them per bot)
        cfg.bot_instructions = (cfg.home / "INSTRUCTIONS.md").read_text(errors="replace").strip()[:4000]
    except OSError:
        pass
    agent = Agent(cfg, LLM(key), ConsoleUI(console, cfg), workspace, build_memory(cfg), general_store(cfg.home),
                  session, history=History(cfg.home / "history.db"), health=Health(cfg.home))
    attach_antibodies(agent, cfg, console, workspace)
    return agent


# -- antibodies: the team's proven fixes on MongoDB Atlas -------------------------------------------------------
def open_team(cfg: Config, console: Console, quiet: bool = False):
    """The team's antibody store, or None (no connection set up, or Atlas unreachable: said once, never fatal)."""
    from .antibodies import TeamSettings, open_store

    s = TeamSettings.load(cfg.home, Path.cwd())
    if not s.mongodb_uri:
        if not quiet:
            console.print("[dim]No MongoDB Atlas connection (MONGODB_URI, or Settings, Account in the app): "
                          "running without antibodies.[/]")
        return None, s
    try:
        return open_store(s), s
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]Couldn't reach MongoDB Atlas ({escape(str(e))[:200]}); running without antibodies.[/]")
        return None, s


def attach_antibodies(agent: Agent, cfg: Config, console: Console, workspace: Path) -> None:
    store, _ = open_team(cfg, console, quiet=True)
    if store is None:
        return
    from .antibodies import Watcher
    from .llm import text_of

    def describe(system: str, prompt: str) -> str:
        r = agent._complete(system=system, prompt=prompt, max_tokens=300)
        return text_of(r).strip() if r else ""

    agent.watcher = Watcher(store, workspace, describe=describe,
                            notify=lambda msg: console.print(f"[bold green]{escape(msg)}[/]"))
    console.print(f"[dim]Antibodies on: team {escape(store.team)}.[/]")


def show_antibody(console: Console, doc: dict) -> None:
    from rich.panel import Panel

    steps = "\n".join(f"  • {escape(x)}" for x in doc.get("steps", [])[:6])
    body = (f"[bold]Error[/]  {escape(doc['error_text'])}\n[bold]Fix[/]    {escape(doc.get('fix', ''))}\n"
            f"[bold]Proof[/]  `{escape(doc.get('proven_by', {}).get('command', '?'))}` passed after the fix\n"
            f"[bold]Took[/]   {doc.get('seconds_to_fix', 0):.0f}s, ${doc.get('usd_to_fix', 0):.2f}")
    if steps:
        body += f"\n[bold]Steps[/]\n{steps}"
    console.print(Panel(body, title="antibody", border_style="green"))


def review_candidates(agent: Agent, console: Console, interactive: bool) -> None:
    """Fixes found during the last request: share now, keep for later review, or drop."""
    w = agent.watcher
    while w is not None and w.candidates:
        cand = w.candidates.pop(0)
        show_antibody(console, cand)
        if not interactive:
            w.store.save(cand, approved=False)
            console.print("[dim]Saved for review: run `oomi antibodies review`.[/]")
            continue
        try:
            answer = input(f"Share this fix with team '{w.store.team}'? [y]es / [n]o / [l]ater ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "l"
        if answer in ("y", "yes"):
            w.store.save(cand, approved=True)
            console.print("[green]Shared. Every oomi on your team is now protected from this error.[/]")
        elif answer in ("l", "later"):
            w.store.save(cand, approved=False)
        else:
            console.print("[dim]Discarded.[/]")


def antibodies_command(args: list, cfg: Config, console: Console) -> None:
    """oomi setup-atlas · oomi antibodies [review] · oomi match <error text>"""
    from . import atlas

    cmd, rest = args[0], args[1:]
    store, s = open_team(cfg, console)
    if store is None:
        sys.exit(1)
    if cmd == "setup-atlas":
        for line in atlas.setup(store.db, embed_model=s.embed_model):
            console.print(line)
        return
    if cmd == "match":
        error = " ".join(rest).strip()
        if not error:
            console.print('usage: oomi match "<error text>"')
            sys.exit(2)
        docs, how, _ = store.candidates(error)
        console.print(f"[bold]mode[/] {how} · {len(docs)} candidates · threshold {s.rerank_threshold}")
        for d in docs[:5]:
            score = d.get("_rerank", d.get("_score", 0.0))
            console.print(f"  {score:.3f}  {escape(d['error_text'][:100])}\n         [dim]{escape(d.get('fix', '')[:120])}[/]")
        m = store.match(error)
        console.print(f"[green]Would offer:[/] {escape(m.fix)} ({m.how}, {m.score:.2f})" if m else
                      "[dim]Nothing sure enough to offer.[/]")
        return
    review = rest[:1] == ["review"]
    docs = store.list("pending" if review else "approved")
    if review:
        if not docs:
            console.print("[dim]Nothing waiting for review.[/]")
        for doc in docs:
            show_antibody(console, doc)
            try:
                answer = input("Approve and share with the team? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return
            store.review(doc["_id"], approve=answer in ("y", "yes"))
        return
    for doc in docs:
        show_antibody(console, doc)
    st = store.stats()
    console.print(f"[bold]{len(docs)} antibodies[/] for team '{escape(s.team)}' · {st['cures']} cures · "
                  f"{st['saved_seconds'] / 60:.0f} min and ${st['saved_usd']:.2f} saved")


IN_APP = os.environ.get("OOMI_APP") == "1"  # launched from Oomi.app (self-contained bundle)


def first_run(cfg: Config, console: Console):
    """Welcome screen inside Oomi.app: one question (the API key), then straight into a session."""
    console.print("[bold]Welcome to oomi.[/] One thing before we start: oomi talks to Claude with your own "
                  "Anthropic API key.\nGet one at [bold]platform.claude.com[/] → API keys (it starts with "
                  "[dim]sk-ant-[/]). It's stored in your Mac's Keychain, never in a file.\n")
    for _ in range(3):
        if store_key_interactive("anthropic"):
            break
        console.print("[yellow]Let's try that again.[/]")
    key = get_key("anthropic")
    if key:
        if not (cfg.home / "config.json").exists():
            cfg.save()
        console.print("[green]Saved.[/] Type what you'd like done, or /help. Ctrl-D quits.\n")
    return key


def banner(agent: Agent, cfg: Config) -> Group:
    sandbox_label = ("[green]sandbox on[/]" if agent.ctx.sandboxed else
                     "[yellow]no sandbox (full trust)[/]" if cfg.trust == "full" and agent.sandbox_info.get("available")
                     else f"[yellow]no sandbox ({escape(agent.sandbox_info.get('reason', ''))})[/]")
    review = len(agent.memory.unreviewed())
    mem = f"{agent.memory.count()} memories" + (f" ([bold]{review} to review[/]: /memory review)" if review else "")
    info = [
        f"[bold]oomi[/] {__version__} · {cfg.model}",
        f"[dim]{escape(str(agent.workspace))}[/]",
        f"{sandbox_label} · trust: [bold]{cfg.trust}[/] · budget ${cfg.max_session_cost_usd:.2f}",
        f"{mem} · {len(agent.skills._ranked())} skills",
    ]
    warnings = health_warnings(agent.health)
    info += [f"[yellow]{escape(w)}[/]" for w in warnings] or ["[dim]/help for commands[/]"]
    grid = Table.grid(padding=(0, 3))
    grid.add_column()
    grid.add_column()
    ghost = Text("\n".join(GHOST), style="bold white")
    grid.add_row(ghost, Text.from_markup("\n".join(info)))
    return Group(grid)


def health_warnings(health: Health) -> list:
    out = []
    status, _ = health.key_status()
    if status == "rejected":
        out.append("Last time, your API key was rejected. Run oomi setup --force-key if that's still the case.")
    unavailable = health.recent(24, {"model_unavailable"})
    if unavailable:
        out.append(f"{len(unavailable)} model failure(s) in the last 24h; Oomi fell back automatically (/status).")
    return out


def status_card(agent: Agent, cfg: Config) -> Panel:
    h = agent.health
    ok, bad, warn = "[green]✓[/]", "[red]✗[/]", "[yellow]![/]"
    rows = []
    status, when = h.key_status()
    t = time.strftime("%H:%M %d %b", time.localtime(when)) if when else ""
    rows.append(f"{ok} API key accepted ({t})" if status == "ok" else
                f"{bad} API key rejected at {t}: run oomi setup --force-key" if status == "rejected" else
                f"{warn} API key not used yet")
    rows.append(f"{ok} model {cfg.model} · fallback {', '.join(cfg.fallback_models) or 'none'} · background {cfg.small_model}")
    fails = h.recent(24, {"model_unavailable"})
    fallbacks = h.recent(24, {"fallback_used"})
    if fails:
        rows.append(f"{warn} {len(fails)} model failure(s) in 24h (latest: {escape(fails[-1]['detail'])}); "
                    f"fell back {len(fallbacks)}×")
    rows.append((f"{ok} sandbox on" if agent.ctx.sandboxed else f"{warn} sandbox off") + f" · trust {cfg.trust}"
                + (" · [yellow]untrusted content in this session[/]" if agent.taint.active else ""))
    rows.append(f"$ today ${h.spent(1):.2f} · this session ${agent.cost.spent_usd:.2f} of ${agent.cost.limit_usd:.2f} · "
                f"last 7 days ${h.spent(7):.2f}")
    rows.append(f"  context ≈ {agent.last_context_tokens:,} tokens (compacts at {cfg.compact_at_tokens:,})")
    if h.tool_calls:
        parts = [f"{n} {c}" + (f" ([red]{h.tool_errors[n]} failed[/])" if h.tool_errors.get(n) else "")
                 for n, c in sorted(h.tool_calls.items(), key=lambda x: -x[1])]
        rows.append("⚙ tools this session: " + " · ".join(parts))
    skills = agent.skills.list()
    unreliable = sum(1 for s in skills if s.unreliable and s.status != "retired")
    retired = sum(1 for s in skills if s.status == "retired")
    review = len(agent.memory.unreviewed())
    rows.append(f"◆ memory: {agent.memory.count()} facts" + (f", {review} to review" if review else "")
                + f" · skills: {len(agent.skills._ranked())}" + (f" ({unreliable} unreliable)" if unreliable else "")
                + (f", {retired} retired" if retired else ""))
    return Panel("\n".join(rows), title="oomi status", border_style="cyan", expand=False)


def review_memory(memory: Memory, console: Console) -> None:
    new = memory.unreviewed()
    if not new:
        console.print("Nothing new to review. [dim](/memory edit to see and change everything)[/]")
        return
    console.print("[bold]New things oomi learned[/] — keep or drop:")
    for i, f in enumerate(new, 1):
        until = f" [dim](until {time.strftime('%Y-%m-%d', time.localtime(f.expires_at))})[/]" if f.expires_at else ""
        tags = f" [dim][{f.tags}][/]" if f.tags else ""
        console.print(f"  {i}. {escape(f.text)}{tags}{until}")
    console.print("[dim]Enter keeps all · numbers drop those (e.g. 2 3) · e edits everything[/]")
    try:
        ans = input("› ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans == "e":
        edit_memory(memory, console)
        return
    drop = {int(x) for x in ans.replace(",", " ").split() if x.isdigit() and 1 <= int(x) <= len(new)}
    for i, f in enumerate(new, 1):
        if i in drop:
            memory.forget(f.id)
    memory.mark_reviewed([f.id for i, f in enumerate(new, 1) if i not in drop])
    console.print(f"Kept {len(new) - len(drop)}, dropped {len(drop)}.")


def edit_memory(memory: Memory, console: Console) -> None:
    text, exported = memory.export_text()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", prefix="oomi-memory-", delete=False) as tf:
        tf.write(text)
        path = tf.name
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    argv = shlex.split(editor) + [path] if editor else (["open", "-W", "-t", path] if sys.platform == "darwin" else ["nano", path])
    console.print(f"[dim]Opening your memory in {argv[0]}; save and close to apply.[/]")
    try:
        subprocess.call(argv)
        res = memory.apply_text(Path(path).read_text(), exported)
    finally:
        os.unlink(path)
    console.print(f"Updated {res['updated']}, added {res['added']}, forgot {res['forgotten']}."
                  + (f" Skipped {len(res['skipped'])} line(s) that looked like secrets or were empty." if res["skipped"] else ""))


def show_changes(agent: Agent, console: Console, arg: str) -> None:
    if arg.startswith("diff"):
        d = agent.changes.diff(arg[4:].strip())
        if not d:
            console.print("No file edits to diff (shell-command changes are listed by /changes).")
            return
        for line in d.splitlines():
            style = "green" if line.startswith("+") and not line.startswith("+++") else \
                    "red" if line.startswith("-") and not line.startswith("---") else \
                    "cyan" if line.startswith("@@") else "dim" if line.startswith(("+++", "---")) else ""
            console.print(Text(line, style=style))
        return
    items = agent.changes.summary()
    if not items:
        console.print("Nothing changed in this session yet.")
        return
    t = Table(box=None, title="Changed this session")
    for col in ("file", "", "how"):
        t.add_column(col)
    colors = {"created": "green", "modified": "yellow", "deleted": "red"}
    for rel, status, detail in items:
        t.add_row(escape(rel), f"[{colors.get(status, '')}]{status}[/]", escape(detail))
    console.print(t)
    snaps = agent.changes.snapshots()
    if snaps:
        console.print("[dim]Snapshots: " + ", ".join(f"before `{escape(s['label'])}`" for s in snaps) + "[/]")
    nxt = agent.changes.peek()
    console.print(f"[dim]/undo would {escape(nxt)} · /changes diff for details[/]" if nxt else "")


def _confirm_restore(agent: Agent, console: Console) -> bool:
    try:
        return input(f"Next step will {agent.changes.peek()}, removing files created since then (including any you "
                     f"made yourself). Continue? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def handle_command(line: str, agent: Agent, console: Console, cfg: Config):
    parts = line.strip().split(maxsplit=1)
    cmd, arg = parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")
    if cmd in ("/exit", "/quit", "/q"):
        return "exit"
    if cmd == "/help":
        console.print(HELP)
    elif cmd in ("/status", "/cost"):
        console.print(status_card(agent, cfg))
    elif cmd == "/changes":
        show_changes(agent, console, arg)
    elif cmd == "/undo":
        from .changes import UndoError

        if arg == "all":
            n = 0
            try:
                while agent.changes.journal:
                    if agent.changes.next_kind() == "snapshot" and not _confirm_restore(agent, console):
                        break
                    msg = agent.undo()
                    if not msg:
                        break
                    console.print(escape(msg))
                    n += 1
            except UndoError as e:
                console.print(f"[yellow]Stopped:[/] {escape(str(e))}")
            console.print(f"Done ({n} step(s))." if n else "Nothing to undo in this session.")
        else:
            nxt = agent.changes.peek()
            if agent.changes.next_kind() == "snapshot":
                try:
                    if input(f"This will {nxt}, removing files created since then (including any you made "
                             f"yourself). Continue? [y/N] ").strip().lower() not in ("y", "yes"):
                        return None
                except (EOFError, KeyboardInterrupt):
                    return None
            try:
                console.print(escape(agent.undo() or "Nothing to undo in this session."))
            except UndoError as e:
                console.print(f"[yellow]Couldn't undo:[/] {escape(str(e))}")
    elif cmd in ("/trust", "/mode"):
        if arg not in TRUST_LEVELS:
            console.print(f"Trust is [bold]{cfg.trust}[/]. Options: read-only (look, don't touch) · project (edit this "
                          f"project, commands sandboxed) · full (no sandbox; dangerous commands still ask).")
        else:
            agent.set_trust(arg)
            console.print(f"Trust: [bold]{arg}[/]")
    elif cmd == "/plan":
        agent.plan()
        console.print("Plan mode: oomi will look around read-only and propose a plan. Approve it with [bold]/go[/] "
                      "(optionally followed by tweaks).")
    elif cmd == "/go":
        return ("run_go", arg)
    elif cmd == "/model":
        if not arg:
            console.print(f"Model: [bold]{cfg.model}[/]. Known: {', '.join(PRICING)}")
        else:
            cfg.model = arg
            console.print(f"Model: [bold]{arg}[/]")
    elif cmd == "/effort":
        if arg in ("low", "medium", "high", "xhigh", "max"):
            cfg.effort = arg
            console.print(f"Effort: [bold]{arg}[/]")
        else:
            console.print(f"Effort is {cfg.effort}. Options: low, medium, high, xhigh, max.")
    elif cmd == "/budget":
        try:
            agent.cost.limit_usd = float(arg.lstrip("$"))
            agent.cost.warned = False
            console.print(f"Session budget: ${agent.cost.limit_usd:.2f}")
        except ValueError:
            console.print("Usage: /budget 5")
    elif cmd == "/compact":
        if not agent.compact():
            console.print("Not enough history to compact yet.")
    elif cmd == "/memory":
        if arg == "review":
            review_memory(agent.memory, console)
        elif arg == "edit":
            edit_memory(agent.memory, console)
        else:
            facts = agent.memory.search(arg, 15, touch=False) if arg else agent.memory.list(40)
            if not facts:
                console.print("No memories." if not arg else "No matches.")
            for f in facts:
                tags = f" [dim][{f.tags}][/]" if f.tags else ""
                new = " [yellow]new[/]" if not f.reviewed else ""
                console.print(f"[bold]#{f.id}[/]{tags}{new} {escape(f.text)}")
            if facts and not arg:
                console.print("[dim]/memory edit to change anything · /memory review for new ones[/]")
    elif cmd == "/remember":
        if arg:
            try:
                fid, status = agent.memory.add(arg, source="user")
                console.print(f"Memory #{fid} {status}.")
            except ValueError as e:
                console.print(str(e))
    elif cmd == "/forget":
        try:
            console.print("Forgotten." if agent.memory.forget(int(arg.lstrip("#"))) else "No such memory.")
        except ValueError:
            console.print("Usage: /forget 12")
    elif cmd == "/skills":
        skills_command(arg.split(), agent.skills, console, cfg)
    elif cmd == "/taint":
        if agent.taint.active:
            console.print("Untrusted sources in this conversation:\n" + "\n".join(f"  • {escape(s)}" for s in agent.taint.sources))
        else:
            console.print("No untrusted content in this conversation.")
    elif cmd == "/untaint":
        agent.taint.clear()
        console.print("Cleared. Shell commands may use the network again without asking.")
    elif cmd == "/reset":
        return "reset"
    elif quick_command_for(cfg, line):
        name, command = quick_command_for(cfg, line)
        try:
            out = agent.quick_command(command)  # same trust and approval rules as run_shell; no model call
        except KeyboardInterrupt:
            out = "Stopped."
        console.print(escape(out), markup=False, highlight=False, soft_wrap=True)
    else:
        console.print(f"Unknown command {escape(cmd)}. /help lists them.")
    return None


def read_message(prompt: str) -> str:
    first = input(prompt)
    if first.strip() != '"""':
        return first
    lines = []
    while True:
        line = input("… ")
        if line.strip() == '"""':
            return "\n".join(lines)
        lines.append(line)


def run_turn(agent: Agent, console: Console, cfg: Config, text: str, go: bool = False) -> None:
    ui = agent.ui
    if isinstance(ui, ConsoleUI):
        ui.turn_started = time.time()
    before = agent.cost.spent_usd
    t0 = time.time()
    try:
        agent.go(text) if go else agent.run(text)
    except ModelError as e:
        if e.kind == "auth":
            console.print(key_problem_panel())
        else:
            console.print(f"[bold red]The model call failed:[/] {escape(str(e))} [dim](/status)[/]")
        return
    except Exception as e:  # the agent already repaired its history
        console.print(f"[bold red]Error:[/] {escape(str(e))}")
        return
    took = time.time() - t0
    console.print(f"[dim]  ${agent.cost.spent_usd - before:.4f} · session ${agent.cost.spent_usd:.2f} · "
                  f"{took:.0f}s · ctx {agent.last_context_tokens / 1000:.0f}k[/]")
    if cfg.notify and took > cfg.notify_after_seconds:
        notify("Oomi finished", text[:120] or "Done")
    review_candidates(agent, console, interactive=sys.stdin.isatty())
    if agent.interrupted:
        console.print("[yellow]Stopped.[/] Type a note to steer it (it carries on with your note), or press Enter to "
                      "leave it here.")
        try:
            note = input("steer › ").strip()
        except (EOFError, KeyboardInterrupt):
            note = ""
        if note:
            run_turn(agent, console, cfg, note)


def wrap_up(agent: Agent, console: Console) -> None:
    if not agent.messages:
        return
    with console.status("[dim]Saving what I learned…[/]", spinner="dots"):
        try:
            agent.finish()
        except Exception:
            agent.save()


def repl(cfg: Config, console: Console, workspace: Path, resume: bool) -> None:
    try:
        import readline  # noqa: F401  (line editing + history)

        hist = cfg.home / "history"
        try:
            readline.read_history_file(str(hist))
        except (FileNotFoundError, OSError):
            pass
        import atexit

        atexit.register(lambda: _save_history(readline, hist))
    except ImportError:
        pass

    agent = build_agent(cfg, console, workspace, resume)
    console.print(banner(agent, cfg))
    if agent.messages:
        console.print(f"[dim]Resumed session {agent.session_id} ({len(agent.messages)} messages).[/]")

    while True:
        marker = "\001\033[33m\002⚠ \001\033[0m\002" if agent.taint.active else ""
        plan = "\001\033[35m\002plan \001\033[0m\002" if cfg.trust == "read-only" and agent.prev_trust else ""
        try:
            console.print()
            line = read_message(f"{marker}{plan}\001\033[1;36m\002›\001\033[0m\002 ")
        except EOFError:
            console.print()
            break
        except KeyboardInterrupt:
            console.print()
            continue
        if not line.strip():
            continue
        if line.startswith("/"):
            r = handle_command(line, agent, console, cfg)
            if r == "exit":
                break
            if r == "reset":
                wrap_up(agent, console)
                agent = build_agent(cfg, console, workspace, resume=False)
                console.print("[dim]New session.[/]")
            elif isinstance(r, tuple) and r[0] == "run_go":
                run_turn(agent, console, cfg, r[1], go=True)
            continue
        run_turn(agent, console, cfg, line)
    wrap_up(agent, console)


def _save_history(readline, path: Path) -> None:
    try:
        readline.set_history_length(2000)
        readline.write_history_file(str(path))
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------
# Subcommands

def skills_command(args: list, store: SkillStore, console: Console, cfg: Config) -> None:
    sub = args[0] if args else "list"
    name = args[1] if len(args) > 1 else ""
    if sub == "list":
        skills = store.list()
        if not skills:
            console.print("No skills yet. Oomi offers to save one after it finishes a task you're likely to repeat.")
            return
        t = Table(box=None)
        for col in ("name", "status", "record", "description"):
            t.add_column(col)
        for s in skills:
            ok, bad = s.outcomes
            status = "unreliable" if s.unreliable and s.status != "retired" else s.status
            t.add_row(s.name, status, f"{ok}/{ok + bad}" if ok + bad else "-", escape(s.description[:80]))
        console.print(t)
    elif sub == "show" and name:
        s = store.get(name)
        console.print(escape(store.render_for_agent(s)) if s else "No such skill.")
        if s and s.stats.get("last_verify"):
            lv = s.stats["last_verify"]
            console.print(f"[dim]Last verification: exit {lv.get('rc')}[/]")
    elif sub == "approve" and name:
        try:
            s = store.approve(name)
            console.print(f"{s.name} is now [bold]{s.status}[/].")
        except ValueError as e:
            console.print(str(e))
    elif sub == "remove" and name:
        try:
            console.print("Removed." if store.remove(name) else "No such skill.")
        except ValueError as e:
            console.print(str(e))
    elif sub == "import" and name:
        try:
            s, findings = store.import_dir(Path(name), cfg.protected_paths)
        except ValueError as e:
            console.print(f"[red]{escape(str(e))}[/]")
            return
        console.print(f"Imported [bold]{s.name}[/] as [yellow]untrusted[/]. Review it with "
                      f"`oomi skills show {s.name}`, then `oomi skills approve {s.name}`.")
        if findings:
            console.print("[yellow]Things to check before trusting it:[/]")
            for f in findings[:20]:
                console.print(f"  • {escape(f)}")
    else:
        console.print("Usage: oomi skills [list | show <name> | approve <name> | remove <name> | import <path>]")


def memory_command(args: list, cfg: Config, console: Console) -> None:
    mem = build_memory(cfg)
    sub = args[0] if args else "list"
    if sub == "list":
        if not mem.count():
            console.print("No memories yet.")
        for f in mem.list(500):
            new = " [yellow]new[/]" if not f.reviewed else ""
            console.print(f"#{f.id} [dim]{f.tags}[/]{new} {escape(f.text)}")
    elif sub == "search" and len(args) > 1:
        for f in mem.search(" ".join(args[1:]), 10, touch=False):
            console.print(f"#{f.id} ({f.score:.2f}) {escape(f.text)}")
    elif sub == "forget" and len(args) > 1:
        console.print("Forgotten." if mem.forget(int(args[1].lstrip("#"))) else "No such memory.")
    elif sub == "review":
        review_memory(mem, console)
    elif sub == "edit":
        edit_memory(mem, console)
    elif sub == "history":
        for f, why in mem.history(100):
            console.print(f"[dim]#{f.id}[/] {escape(f.text)} [dim]— retired: {escape(why)}[/]")
    elif sub == "export":
        print(json.dumps([f.__dict__ for f in mem.list(100000)], indent=2))
    else:
        console.print("Usage: oomi memory [list | review | edit | search <query> | forget <id> | history | export]")


def stats_command(cfg: Config, console: Console) -> None:
    rows = Health(cfg.home).by_day(14)
    if not rows:
        console.print("No usage recorded yet.")
        return
    t = Table(title="Spending, last 14 days", box=None)
    for col in ("day", "cost", "calls", "tokens in", "tokens out"):
        t.add_column(col, justify="right" if col != "day" else "left")
    for day, usd, calls, tin, tout in rows:
        t.add_row(day, f"${usd:.2f}", str(calls), f"{tin:,}", f"{tout:,}")
    t.add_row("[bold]total[/]", f"[bold]${sum(r[1] for r in rows):.2f}[/]", str(sum(r[2] for r in rows)), "", "")
    console.print(t)


def eval_command(args: list, cfg: Config, console: Console) -> None:
    if not args or args[0] != "memory":
        console.print("Usage: oomi eval memory")
        return
    from .evals import run_memory_eval

    runs = [run_memory_eval()]
    akey = get_key("anthropic")
    if akey:
        from .llm import LLM
        from .memory import make_llm_expander

        llm = LLM(akey)

        def complete(**kw):
            try:
                return llm.complete(model=cfg.small_model, **kw)
            except ModelError:
                return None

        console.print(f"[dim]Running query expansion with {cfg.small_model} (60 tiny calls, well under $0.01)…[/]")
        runs.append(run_memory_eval(expander=make_llm_expander(complete)))
    vkey = get_key("voyage")
    if vkey:
        runs.append(run_memory_eval(VoyageEmbedder(vkey, cfg.voyage_model, cfg.voyage_endpoint)))
    for r in runs:
        t = Table(title=f"Memory recall — {r['mode']} · {r['facts']} facts", box=None)
        t.add_column("query type")
        for c in ("recall@1", "recall@3", "recall@5"):
            t.add_column(c, justify="right")
        for kind in ("keyword", "paraphrase"):
            t.add_row(kind, *(f"{r[kind][c]:.0%}" for c in ("recall@1", "recall@3", "recall@5")))
        console.print(t)
    if not vkey:
        console.print("[dim]Add a Voyage key (oomi setup) to also measure embedding-based hybrid retrieval.[/]")
    console.print(f"[dim]Note: while your memory store is under {cfg.memory_full_inject_chars:,} characters, oomi shows "
                  f"every memory once per session, so nothing depends on search until it grows past that.[/]")


APP_SCRIPT = """on run
\tset f to choose folder with prompt "Open a project folder in Oomi"
\tlaunchOomi(POSIX path of f)
end run

on open theItems
\trepeat with f in theItems
\t\tlaunchOomi(POSIX path of f)
\tend repeat
end open

on launchOomi(p)
\ttell application "Terminal"
\t\tactivate
\t\tdo script "cd " & quoted form of p & " && " & quoted form of "{exe}"
\tend tell
end launchOomi
"""


def build_app(console: Console) -> None:
    """~/Applications/Oomi.app: click it (or drop a folder on it) to open Oomi in Terminal, with the ghost icon."""
    if sys.platform != "darwin":
        console.print("Oomi.app is macOS-only.")
        return
    exe = shutil.which("oomi") or os.path.realpath(sys.argv[0])
    app = Path.home() / "Applications" / "Oomi.app"
    app.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as tf:
        tf.write(APP_SCRIPT.replace("{exe}", exe.replace("\\", "\\\\").replace('"', '\\"')))
        script = tf.name
    try:
        if app.exists():
            shutil.rmtree(app)
        r = subprocess.run(["osacompile", "-o", str(app), script], capture_output=True, text=True)
        if r.returncode != 0:
            console.print(f"[red]Couldn't build Oomi.app:[/] {escape(r.stderr.strip())}")
            return
    finally:
        os.unlink(script)
    icon = Path(__file__).parent / "assets" / "oomi.icns"
    shutil.copy2(icon, app / "Contents" / "Resources" / "applet.icns")
    plist = app / "Contents" / "Info.plist"
    for k, v in (("CFBundleName", "Oomi"), ("CFBundleDisplayName", "Oomi"), ("CFBundleIdentifier", "dev.oomi.launcher")):
        if subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Set :{k} {v}", str(plist)], capture_output=True).returncode:
            subprocess.run(["/usr/libexec/PlistBuddy", "-c", f"Add :{k} string {v}", str(plist)], capture_output=True)
    os.utime(app, None)  # nudge Finder to pick up the new icon
    console.print(f"Built [bold]{app}[/]. Open it from Launchpad or Spotlight, or drop a project folder on it. "
                  f"[dim](macOS will ask once to let Oomi control Terminal.)[/]")


def doctor(cfg: Config, console: Console) -> None:
    ok = lambda b: "[green]✓[/]" if b else "[red]✗[/]"  # noqa: E731
    console.print(f"[bold]oomi {__version__}[/] · Python {sys.version.split()[0]} · {sys.platform}")
    import anthropic

    console.print(f"{ok(True)} anthropic SDK {anthropic.__version__}")
    key = get_key("anthropic")
    console.print(f"{ok(bool(key))} Anthropic API key {'found' if key else 'missing — run oomi setup'}")
    health = Health(cfg.home)
    if key:
        from .llm import LLM

        llm = LLM(key, max_retries=1)
        for model in dict.fromkeys([cfg.model, cfg.small_model] + list(cfg.fallback_models)):
            try:
                r = llm.complete(model=model, system="Reply with exactly: OK", prompt="ping", max_tokens=200)
                console.print(f"{ok(True)} {model} reachable ({r['usage'].get('input_tokens', 0)} input tokens)")
                health.event("key_ok")
            except ModelError as e:
                if e.kind == "auth":
                    health.event("key_rejected", str(e))
                    console.print(key_problem_panel())
                    break
                console.print(f"{ok(False)} {model}: {escape(e.short)} — {escape(str(e)[:160])}")
    console.print(f"{ok(bool(get_key('voyage')))} Voyage key (optional, for semantic memory)")

    m = Memory(":memory:")
    console.print(f"{ok(m.fts)} SQLite FTS5 {'available' if m.fts else 'missing (falls back to slower search)'}")
    console.print(f"{ok(bool(shutil.which('rg')))} ripgrep {'found' if shutil.which('rg') else '(optional: brew install ripgrep)'}")

    info = sandbox.probe()
    console.print(f"{ok(info['available'])} macOS sandbox {'works' if info['available'] else escape(info['reason'])}"
                  + (f" · network rule: {info['net_rule']}" if info.get("net_rule") else ""))
    if info["available"]:
        _sandbox_selftest(cfg, console, info, ok)
    if sys.platform == "darwin":
        app = Path.home() / "Applications" / "Oomi.app"
        console.print(f"{ok(app.exists())} Oomi.app {'installed' if app.exists() else '(optional: oomi app)'}")


def _sandbox_selftest(cfg: Config, console: Console, info: dict, ok) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(os.path.realpath(tmp))
        outside = Path.home() / f".oomi-sandbox-probe-{os.getpid()}"

        def run(cmd: str, allow_network: bool) -> int:
            prof = sandbox.build_profile(ws, cfg.sandbox_extra_writable, cfg.protected_paths, allow_network,
                                         info.get("net_rule") or sandbox.NETWORK_DENY_CANDIDATES[0])
            return subprocess.run(sandbox.wrap(cmd, prof), cwd=ws, capture_output=True, timeout=30).returncode

        console.print(f"  {ok(run('echo hi > inside.txt', True) == 0)} can write inside the workspace")
        blocked = run(f"echo hi > {outside}", True) != 0
        if outside.exists():
            outside.unlink()
        console.print(f"  {ok(blocked)} cannot write outside it")
        ssh = Path.home() / ".ssh"
        if ssh.exists():
            console.print(f"  {ok(run('ls ~/.ssh', True) != 0)} cannot read ~/.ssh")
        if info.get("net_rule"):
            console.print(f"  {ok(run('curl -sS -m 8 -o /dev/null https://example.com', False) != 0)} "
                          f"network off when the conversation is tainted")
            console.print(f"  {ok(run('curl -sS -m 8 -o /dev/null https://example.com', True) == 0)} "
                          f"network on otherwise [dim](fails if you're offline)[/]")


def setup(cfg: Config, console: Console) -> None:
    ensure_dirs()
    if not (cfg.home / "config.json").exists():
        path = cfg.save()
        console.print(f"Wrote {path} (just the settings you're likely to change).")
    if get_key("anthropic") and "--force-key" not in sys.argv:
        console.print("Anthropic key already stored. [dim](oomi setup --force-key replaces it.)[/]")
    else:
        console.print("Get a key at https://platform.claude.com → API keys.")
        if not store_key_interactive("anthropic"):
            console.print("[red]Key not saved.[/]")
            return
    try:
        want = input("Add a Voyage AI key for semantic memory search? (optional) [y/N] ").strip().lower()
    except EOFError:
        want = ""
    if want in ("y", "yes"):
        store_key_interactive("voyage")
    if sys.platform == "darwin" and not IN_APP:
        build_app(console)  # the self-contained Oomi.app doesn't need a separate launcher
    console.print()
    doctor(cfg, console)


def utf8_on_windows(argv: list) -> None:
    """Windows Python reads and writes files in the old local code page unless it runs in UTF-8 mode, and oomi's
    files (memory, chats, settings) are UTF-8. Restart in UTF-8 mode; Oomi.exe is built with it already on."""
    if sys.platform == "win32" and not sys.flags.utf8_mode and not getattr(sys, "frozen", False):
        sys.exit(subprocess.call([sys.executable, "-X", "utf8", "-m", "oomi", *argv]))


def main(argv=None) -> None:
    if argv is None:
        utf8_on_windows(sys.argv[1:])
    argv = list(sys.argv[1:] if argv is None else argv)
    console = Console()
    ensure_dirs()
    cfg = Config.load()
    if argv and argv[0] == "gui":
        g = argparse.ArgumentParser(prog="oomi gui", description="Open the oomi window.")
        g.add_argument("--browser", action="store_true", help="open in your web browser instead of a native window")
        g.add_argument("--port", type=int, default=0, help="local port (default: any free one)")
        ga = g.parse_args(argv[1:])
        from .gui.app import run

        run(browser=ga.browser, port=ga.port)
        return
    if argv and argv[0] in ("setup-atlas", "antibodies", "match"):
        antibodies_command(argv, cfg, console)
        return
    if argv and argv[0] in ("setup", "doctor", "eval", "skills", "memory", "stats", "app"):
        cmd, rest = argv[0], argv[1:]
        if cmd == "setup":
            setup(cfg, console)
        elif cmd == "doctor":
            doctor(cfg, console)
        elif cmd == "eval":
            eval_command(rest, cfg, console)
        elif cmd == "skills":
            skills_command(rest, general_store(cfg.home), console, cfg)
        elif cmd == "memory":
            memory_command(rest, cfg, console)
        elif cmd == "stats":
            stats_command(cfg, console)
        elif cmd == "app":
            build_app(console)
        return

    p = argparse.ArgumentParser(prog="oomi", description="A lean, self-improving terminal agent for macOS.")
    p.add_argument("prompt", nargs="*", help="run one request and exit")
    p.add_argument("-p", "--print", dest="ptext", metavar="PROMPT", help="run one request and exit")
    p.add_argument("-w", "--workspace", default=".", help="folder oomi works in (default: current)")
    p.add_argument("-m", "--model", help="model id, e.g. claude-opus-5-5")
    p.add_argument("-c", "--continue", dest="resume", action="store_true", help="resume the last session here")
    p.add_argument("--trust", choices=TRUST_LEVELS, help="read-only · project (default) · full")
    p.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--budget", type=float, help="session spending limit in USD")
    p.add_argument("--version", action="version", version=f"oomi {__version__}")
    a = p.parse_args(argv)

    if a.model:
        cfg.model = a.model
    if a.trust:
        cfg.trust = a.trust
    if a.effort:
        cfg.effort = a.effort
    if a.budget is not None:
        cfg.max_session_cost_usd = a.budget
    if cfg.trust not in TRUST_LEVELS:
        console.print(f"[yellow]Unknown trust level {cfg.trust!r} in config; using project.[/]")
        cfg.trust = "project"
    workspace = Path(a.workspace).expanduser().resolve()
    if not workspace.is_dir():
        console.print(f"[red]Not a folder: {workspace}[/]")
        sys.exit(1)
    if workspace == Path.home().resolve():
        console.print("[yellow]Heads-up: the workspace is your whole home folder, so the sandbox lets commands "
                      "write anywhere in it. Consider running oomi inside a project folder.[/]")

    one_shot = a.ptext or " ".join(a.prompt)
    if one_shot:
        agent = build_agent(cfg, console, workspace, a.resume)
        run_turn(agent, console, cfg, one_shot)
        wrap_up(agent, console)
        return
    repl(cfg, console, workspace, a.resume)
