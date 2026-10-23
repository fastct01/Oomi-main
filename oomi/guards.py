"""Loop detection and budgets.

Hermes' most-reported failure is the agent that never stops calling tools. A plain iteration cap
either cuts off legitimate long tasks or lets a stuck agent burn money. Oomi watches for the
thing that actually defines a loop: the *same call producing the same result* again and again.
Re-running `pytest` after an edit is progress (the output changes); re-running it with nothing
changed is a loop.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Optional


def _digest(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", "replace"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


@dataclass
class LoopVerdict:
    action: str  # "ok" | "warn" | "stop"
    message: str = ""


class LoopGuard:
    def __init__(self, warn_at: int = 3, stop_at: int = 5, window: int = 16, max_consecutive_errors: int = 6):
        self.warn_at = warn_at
        self.stop_at = stop_at
        self.recent: deque = deque(maxlen=window)
        self.max_consecutive_errors = max_consecutive_errors
        self.consecutive_errors = 0

    def record(self, name: str, tool_input: dict, result_text: str, is_error: bool) -> LoopVerdict:
        call = _digest(name, json.dumps(tool_input, sort_keys=True, default=str))
        key = (call, _digest(result_text))
        self.recent.append(key)
        repeats = Counter(self.recent)[key]

        self.consecutive_errors = self.consecutive_errors + 1 if is_error else 0

        if repeats >= self.stop_at:
            return LoopVerdict(
                "stop",
                f"Stopped: `{name}` returned the identical result {repeats} times with identical input.",
            )
        if self.consecutive_errors >= self.max_consecutive_errors:
            return LoopVerdict("stop", f"Stopped: {self.consecutive_errors} tool calls in a row failed.")
        if repeats >= self.warn_at:
            return LoopVerdict(
                "warn",
                f"[oomi] Loop warning: this exact `{name}` call has now returned the same result "
                f"{repeats} times. Repeating it will not change anything. Try a different approach, "
                f"or stop and explain what is blocking you. The turn ends at {self.stop_at} repeats.",
            )
        return LoopVerdict("ok")


@dataclass
class CostMeter:
    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    by_model: dict = field(default_factory=dict)
    warned: bool = False
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, model: str, usd: float, usage: Optional[dict] = None) -> None:
        self.spent_usd += usd
        self.calls += 1
        self.by_model[model] = self.by_model.get(model, 0.0) + usd
        u = usage or {}
        self.input_tokens += (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
        self.output_tokens += u.get("output_tokens") or 0

    @property
    def cost(self) -> float:
        """Dollars so far, under the name the antibody watcher reads."""
        return self.spent_usd

    @property
    def exhausted(self) -> bool:
        return self.limit_usd > 0 and self.spent_usd >= self.limit_usd

    def should_warn(self, fraction: float = 0.8) -> bool:
        if self.warned or self.limit_usd <= 0:
            return False
        if self.spent_usd >= self.limit_usd * fraction:
            self.warned = True
            return True
        return False
