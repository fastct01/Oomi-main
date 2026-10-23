"""Usage ledger and health events, so problems show up in one place instead of a log you never read."""

from __future__ import annotations

import json
import time
from pathlib import Path


class Health:
    def __init__(self, home: Path):
        self.usage_path = Path(home) / "usage.jsonl"
        self.events_path = Path(home) / "health.json"
        self.tool_calls: dict = {}
        self.tool_errors: dict = {}

    # -- usage ------------------------------------------------------------------------------------
    def record_usage(self, session: str, model: str, usd: float, usage: dict) -> None:
        row = {"t": time.time(), "day": time.strftime("%Y-%m-%d"), "session": session, "model": model,
               "usd": round(usd, 6), "in": (usage.get("input_tokens", 0) or 0) + (usage.get("cache_read_input_tokens", 0) or 0)
               + (usage.get("cache_creation_input_tokens", 0) or 0), "out": usage.get("output_tokens", 0) or 0}
        try:
            with self.usage_path.open("a") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass

    def _usage_rows(self, since: float) -> list:
        if not self.usage_path.exists():
            return []
        rows = []
        for line in self.usage_path.read_text().splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(r, dict) and isinstance(r.get("t"), (int, float)) and r["t"] >= since:
                rows.append(r)
        return rows

    def by_day(self, days: int = 14) -> list:
        """[(day, usd, calls, input_tokens, output_tokens)], oldest first."""
        out: dict = {}
        for r in self._usage_rows(time.time() - days * 86400):
            d = out.setdefault(r.get("day", "?"), [0.0, 0, 0, 0])
            d[0] += r.get("usd", 0) or 0
            d[1] += 1
            d[2] += r.get("in", 0)
            d[3] += r.get("out", 0)
        return [(day, *vals) for day, vals in sorted(out.items())]

    def spent(self, days: int) -> float:
        today = time.strftime("%Y-%m-%d")
        rows = self._usage_rows(time.time() - days * 86400)
        if days == 1:
            rows = [r for r in rows if r.get("day") == today]
        return sum(r.get("usd", 0) or 0 for r in rows)

    # -- events -----------------------------------------------------------------------------------
    def _events(self) -> list:
        if not self.events_path.exists():
            return []
        try:
            data = json.loads(self.events_path.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        return [e for e in data if isinstance(e, dict) and isinstance(e.get("t"), (int, float)) and "kind" in e]

    def event(self, kind: str, detail: str = "") -> None:
        ev = self._events()
        ev.append({"t": time.time(), "kind": kind, "detail": detail[:300]})
        try:
            self.events_path.write_text(json.dumps(ev[-200:]))
        except OSError:
            pass

    def recent(self, hours: float = 24, kinds=None) -> list:
        since = time.time() - hours * 3600
        return [e for e in self._events() if e["t"] >= since and (kinds is None or e["kind"] in kinds)]

    def key_status(self) -> tuple:
        """("ok" | "rejected" | "unknown", timestamp)."""
        for e in reversed(self._events()):
            if e["kind"] == "key_rejected":
                return "rejected", e["t"]
            if e["kind"] == "key_ok":
                return "ok", e["t"]
        return "unknown", 0.0

    # -- tools ------------------------------------------------------------------------------------
    def tool(self, name: str, ok: bool) -> None:
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1
        if not ok:
            self.tool_errors[name] = self.tool_errors.get(name, 0) + 1
