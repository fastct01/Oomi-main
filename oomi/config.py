"""Configuration, paths and pricing."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .safety import PROTECTED_WRITE_DEFAULT


GHOST_COLORS = ("white", "blue", "pink", "purple", "green", "peach")  # oomi/assets/ghosts/<color>.png


def oomi_home() -> Path:
    return Path(os.environ.get("OOMI_HOME", str(Path.home() / ".oomi"))).expanduser()


# USD per million tokens: (input, output, 5-minute cache write, cache read).
# Source: platform.claude.com/docs/en/about-claude/pricing (checked Oct 2026).
PRICING = {
    "claude-fable-5-1": (10.0, 50.0, 12.50, 0.25),
    "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.125, 0.01),
}
# Haiku 5.5 is billed at a higher rate once a single prompt exceeds 100k tokens.
HAIKU_55_LONG = (0.50, 2.50, 0.625, 0.05)
WEB_SEARCH_USD = 10.0 / 1000  # per search


def cost_of(model: str, usage: dict, cache_ttl: str = "5m") -> float:
    """Dollar cost of one response, from its `usage` block.
    cache_ttl says how to price cache writes when the block doesn't split them by lifetime (5m is the default)."""
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cw = usage.get("cache_creation_input_tokens", 0) or 0
    # Newer responses split cache writes by lifetime; the flat field is their total.
    split = usage.get("cache_creation") or {}
    cw5 = split.get("ephemeral_5m_input_tokens", 0) or 0
    cw1 = split.get("ephemeral_1h_input_tokens", 0) or 0
    rest = max(cw - cw5 - cw1, 0)  # not accounted for by the split (or no split at all)
    if cache_ttl == "1h":
        cw1 += rest
    else:
        cw5 += rest
    cw = cw5 + cw1
    cr = usage.get("cache_read_input_tokens", 0) or 0
    rates = PRICING.get(model)
    if rates is None:
        rates = next((r for k, r in PRICING.items() if model.startswith(k)), None)
    if rates is None:
        # Unknown model: fall back to the most expensive known rate so budgets stay conservative.
        rates = max(PRICING.values(), key=lambda r: r[1])
    if model.startswith("claude-haiku-5-5") and inp + cw + cr > 100_000:
        rates = HAIKU_55_LONG
    r_in, r_out, r_cw, r_cr = rates
    # A one-hour cache write costs twice the base input rate; a five-minute one 1.25x (the table's third column).
    total = (inp * r_in + out * r_out + cw5 * r_cw + cw1 * 2 * r_in + cr * r_cr) / 1_000_000
    stu = usage.get("server_tool_use") or {}
    total += (stu.get("web_search_requests", 0) or 0) * WEB_SEARCH_USD
    return total


@dataclass
class Config:
    # Models
    model: str = "claude-sonnet-5-5"
    small_model: str = "claude-haiku-5-5"  # summaries, memory learning, query expansion
    fallback_models: list = field(default_factory=lambda: ["claude-opus-5-5"])  # tried when the main model is down
    effort: str = "medium"  # low | medium | high | xhigh | max
    max_tokens: int = 16000
    show_thinking: bool = True
    # Other providers that speak the Messages API, as {"id", "name", "base_url"}; each key is the secret provider:<id>.
    # Anthropic itself is always there and isn't listed.
    providers: list = field(default_factory=list)

    # Safety
    trust: str = "project"  # read-only | project | full
    sandbox: bool = True
    sandbox_extra_writable: list = field(
        default_factory=lambda: ["~/Library/Caches", "~/.cache", "~/.npm", "~/.cargo/registry"]
    )
    protected_paths: list = field(
        default_factory=lambda: [
            "~/.ssh", "~/.aws", "~/.gnupg", "~/.config/gh", "~/.netrc", "~/.docker/config.json",
            "~/Library/Keychains", "~/Library/Cookies", "~/Library/Application Support/Google/Chrome",
            "~/Library/Application Support/Firefox", "~/Library/Messages", "~/Library/Mail",
        ]
    )
    shell_timeout: int = 120

    # Budgets
    max_steps_per_turn: int = 40
    max_session_cost_usd: float = 2.00
    loop_warn_at: int = 3
    loop_stop_at: int = 5
    max_consecutive_errors: int = 6

    # Context management
    compact_at_tokens: int = 160_000
    keep_recent_turns: int = 4
    turn_hard_limit_tokens: int = 600_000
    tool_output_limit_chars: int = 12_000

    # name -> shell command. A message that is exactly /name runs it in the bot's folder, without calling the model.
    quick_commands: dict = field(default_factory=dict)
    cache_ttl: str = "5m"  # prompt cache lifetime: "5m" or "1h" (1h writes cost twice the input rate, reads stay cheap)

    # Server tools
    web_search: bool = True
    web_search_max_uses: int = 5
    web_fetch: bool = True
    web_fetch_max_uses: int = 5
    web_fetch_max_content_tokens: int = 20_000

    # Memory
    memory_auto_k: int = 5
    memory_full_inject_chars: int = 6000  # below this, every memory is shown once per session
    memory_query_expansion: bool = True  # above it, a cheap Haiku call adds synonyms to the search
    memory_learn: bool = True  # learn facts automatically from what you say
    learn_every_turns: int = 6
    skills_index_limit: int = 10  # skills listed in the prompt; the rest are found by search
    voyage_model: str = "voyage-4-lite"
    voyage_endpoint: str = "https://ai.mongodb.com/v1/embeddings"

    # UX
    notify: bool = True  # macOS notification when a long task finishes or needs approval
    notify_after_seconds: int = 30
    notify_approval: bool = True  # the app: which events notify you while you're in another app or another bot
    notify_done: bool = True
    notify_failed: bool = True
    notify_sound: str = "Glass"  # a macOS system sound, or "" for none
    keep_awake: bool = True  # stop the Mac sleeping while a bot is working

    # Safety extras (from the app's Settings)
    deny_commands: list = field(default_factory=list)  # glob patterns that are never run, at any trust level
    protected_write_names: list = field(default_factory=lambda: list(PROTECTED_WRITE_DEFAULT))
    redact_secrets: bool = True  # hide keys, tokens and passwords in tool output from the model
    approval_timeout_minutes: int = 0  # decline an unanswered approval after this long (0: wait)
    max_task_minutes: int = 0  # stop a request that runs longer than this (0: no limit)
    read_project_files: bool = True  # CLAUDE.md / AGENTS.md in a bot's folder become part of its instructions
    bot_instructions: str = ""  # set per bot by the app; never saved here
    user_profile: str = ""  # what bots know about you, from Settings, Account (account.json); never saved here
    ghost_color: str = "white"  # oomi's color in the window and the Dock: one of GHOST_COLORS
    custom_colors: list = field(default_factory=list)  # colors added with + in a bot's color picker, as #rrggbb

    @property
    def home(self) -> Path:
        return oomi_home()

    @classmethod
    def load(cls) -> "Config":
        path = oomi_home() / "config.json"
        cfg = cls()
        if path.exists():
            try:
                data = json.loads(path.read_text())
            except json.JSONDecodeError as e:
                raise SystemExit(f"Could not parse {path}: {e}")
            known = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in known:
                    setattr(cfg, k, v)
        return cfg

    # The handful of settings people actually change. Everything else keeps its default unless added by hand.
    USER_FACING = ("model", "trust", "effort", "max_session_cost_usd", "fallback_models", "web_search", "notify",
                   "sandbox_extra_writable")

    def save(self, minimal: bool = True) -> Path:
        path = oomi_home() / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        if minimal:
            data = {k: data[k] for k in self.USER_FACING}
        path.write_text(json.dumps(data, indent=2) + "\n")
        return path


def ensure_dirs() -> None:
    home = oomi_home()
    for sub in ("skills", "spill", "sessions", "backups", "logs"):
        (home / sub).mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)  # conversations and memories are readable by you only
    except OSError:
        pass
