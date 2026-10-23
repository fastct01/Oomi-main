"""Scheduled tasks: a small natural-language schedule parser, a five-field cron matcher (local time, no dependencies)
and the per-bot list kept in ~/.oomi/schedules.json."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

MIN_INTERVAL_S = 300  # "every minute" would spend money faster than anyone means to
DAYS = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]  # cron numbers 0..6
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
HELP = ("Try 'every day at 8:00', 'weekdays at 18:30', 'mondays at 9', 'every 30 minutes', 'every 2 hours' "
        "or a cron line like '0 9 * * 1-5'.")


class ScheduleError(ValueError):
    pass


def _field(tok: str, lo: int, hi: int, names: Optional[list] = None, first_name: int = 0) -> set:
    """One cron field ('*', '*/5', '1-5', '1,15', 'mon-fri') as the set of values it allows."""
    def num(x: str) -> int:
        x = x.lower()
        if names and x[:3] in [n[:3] for n in names]:
            return [n[:3] for n in names].index(x[:3]) + first_name
        if not x.isdigit():
            raise ScheduleError(f"'{tok}' isn't a valid cron field.")
        return int(x)

    out: set = set()
    for part in tok.split(","):
        base, _, step = part.partition("/")
        if step and (not step.isdigit() or int(step) < 1):
            raise ScheduleError(f"'{part}' has a bad step.")
        st = int(step) if step else 1
        if base == "*":
            a, b = lo, hi
        elif "-" in base:
            x, y = base.split("-", 1)
            a, b = num(x), num(y)
        else:
            a = num(base)
            b = hi if step else a
        if not (lo <= a <= hi and lo <= b <= hi and a <= b):
            raise ScheduleError(f"'{part}' is out of range ({lo}-{hi}).")
        out.update(range(a, b + 1, st))
    return out


def _time(t: str) -> tuple:
    t = t.strip()
    if t == "noon":
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        raise ScheduleError(f"I couldn't read the time '{t}'. {HELP}")
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            raise ScheduleError(f"'{t}' isn't a time of day.")
        h = h % 12 + (12 if ap == "pm" else 0)
    if h > 23 or mi > 59:
        raise ScheduleError(f"'{t}' isn't a time of day.")
    return h, mi


def _days(spec: str) -> str:
    """'day', 'weekdays', 'mondays and fridays' -> a cron day-of-week field."""
    s = spec.strip()
    if s in ("day", "daily", "days"):
        return "*"
    if s in ("weekday", "weekdays"):
        return "1-5"
    if s in ("weekend", "weekends"):
        return "0,6"
    nums = []
    for w in re.split(r"\s*(?:,|&|\band\b|\s)\s*", s):
        if not w:
            continue
        w = w[:-1] if w.endswith("s") and w[:-1] in DAYS else w
        if w not in DAYS:
            raise ScheduleError(f"I don't know what '{w}' is. {HELP}")
        nums.append(DAYS.index(w))
    if not nums:
        raise ScheduleError(HELP)
    return ",".join(str(n) for n in sorted(set(nums)))


@dataclass
class When:
    every: int = 0  # seconds, for "every N minutes/hours"
    expr: str = ""  # normalized five-field cron otherwise

    def __post_init__(self):
        self.sets = None
        if self.expr:
            f = self.expr.split()
            dow = _field(f[4], 0, 7, DAYS, 0)
            self.sets = (_field(f[0], 0, 59), _field(f[1], 0, 23), _field(f[2], 1, 31), _field(f[3], 1, 12, MONTHS, 1),
                         {d % 7 for d in dow})
            self.dom_star, self.dow_star = f[2].startswith("*"), f[4].startswith("*")

    def next_after(self, ts: float) -> Optional[float]:
        if self.every:
            return ts + self.every
        minutes, hours, doms, months, dows = self.sets
        t = datetime.fromtimestamp(ts).replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = t + timedelta(days=366 * 5)
        while t < end:
            if t.month not in months:
                t = (t.replace(day=1) + timedelta(days=32)).replace(day=1, hour=0, minute=0)
                continue
            dom_ok, dow_ok = t.day in doms, (t.isoweekday() % 7) in dows
            if not ((dom_ok and dow_ok) if (self.dom_star or self.dow_star) else (dom_ok or dow_ok)):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            elif t.hour not in hours:
                t = (t + timedelta(hours=1)).replace(minute=0)
            elif t.minute not in minutes:
                t += timedelta(minutes=1)
            else:
                return t.timestamp()
        return None

    def describe(self) -> str:
        if self.every:
            n, unit = (self.every // 3600, "hour") if self.every % 3600 == 0 else (self.every // 60, "minute")
            return f"Every {unit}" if n == 1 else f"Every {n} {unit}s"
        f = self.expr.split()
        if f[0].isdigit() and f[1].isdigit() and f[2] == "*" and f[3] == "*":
            days = sorted(self.sets[4])
            at = f"at {int(f[1])}:{int(f[0]):02d}"
            if len(days) == 7:
                return f"Every day {at}"
            if days == [1, 2, 3, 4, 5]:
                return f"Weekdays {at}"
            if days == [0, 6]:
                return f"Weekends {at}"
            names = [DAYS[d].capitalize() + "s" for d in days]
            return (", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else names[0]) + f" {at}"
        return f"Cron {self.expr}"


def parse_when(text: str) -> When:
    s = re.sub(r"\s+", " ", (text or "").strip().lower().rstrip("."))
    if not s:
        raise ScheduleError(f"Say when it should run. {HELP}")
    f = s.split()
    if len(f) == 5 and re.fullmatch(r"[\d*/,\-]+", f[0]) and all(re.fullmatch(r"[\d*/,\-a-z]+", x) for x in f):
        w = When(expr=" ".join(f))
    elif s == "hourly":
        w = When(every=3600)
    elif m := re.fullmatch(r"every (?:(\d+) ?)?(minutes?|mins?|m|hours?|hrs?|h)", s):
        n = int(m.group(1) or 1)
        secs = n * (3600 if m.group(2).startswith("h") else 60)
        if n < 1 or secs < MIN_INTERVAL_S:
            raise ScheduleError("Intervals must be at least 5 minutes.")
        w = When(every=secs)
    elif m := re.fullmatch(r"(?:every )?(.+?) at (.+)", s):
        h, mi = _time(m.group(2))
        w = When(expr=f"{mi} {h} * * {_days(m.group(1))}")
    else:
        raise ScheduleError(f"I couldn't read that schedule. {HELP}")
    if w.next_after(time.time()) is None:
        raise ScheduleError("That schedule never happens (check the day and month).")
    return w


# ---------------------------------------------------------------------------------------------
@dataclass
class Schedule:
    id: str
    bot: str
    when: str
    prompt: str
    enabled: bool = True
    next_run: float = 0.0
    last_run: Optional[float] = None
    last_status: str = ""  # "", "ok", "failed: …", "skipped: …", "stopped"
    fails: int = 0  # failures in a row; three turn the schedule off
    created: float = field(default_factory=time.time)


def schedule_json(s: Schedule) -> dict:
    try:
        desc = parse_when(s.when).describe()
    except ScheduleError:
        desc = s.when
    d = asdict(s)
    d.pop("created")
    return dict(d, describe=desc)


class ScheduleStore:
    """~/.oomi/schedules.json"""

    def __init__(self, home: Path):
        self.path = Path(home) / "schedules.json"
        self.lock = threading.RLock()
        self.items: dict = {}
        try:
            data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            data = {}
        known = {f.name for f in fields(Schedule)}
        for d in data.get("schedules", []) if isinstance(data, dict) else []:
            try:
                s = Schedule(**{k: v for k, v in d.items() if k in known})
            except TypeError:
                continue
            self.items[s.id] = s

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"schedules": [asdict(s) for s in self.items.values()]}, indent=1))
        os.replace(tmp, self.path)

    def add(self, bot: str, when: str, prompt: str, now: Optional[float] = None) -> Schedule:
        w = parse_when(when)
        s = Schedule(id="s" + uuid.uuid4().hex[:12], bot=bot, when=when.strip(), prompt=prompt.strip(),
                     next_run=w.next_after(now or time.time()))
        with self.lock:
            self.items[s.id] = s
            self._save()
        return s

    def get(self, sid: str) -> Optional[Schedule]:
        return self.items.get(sid)

    def all(self) -> list:
        with self.lock:
            return list(self.items.values())

    def for_bot(self, bot: str) -> list:
        return sorted((s for s in self.all() if s.bot == bot), key=lambda s: s.created)

    def update(self, sid: str, **changes) -> Optional[Schedule]:
        """Change fields. A new `when`, or turning a schedule back on, recomputes next_run (and forgets failures)."""
        with self.lock:
            s = self.items.get(sid)
            if s is None:
                return None
            w = parse_when(changes["when"]) if "when" in changes else None
            reschedule = w is not None or (changes.get("enabled") and not s.enabled)
            for k, v in changes.items():
                setattr(s, k, v.strip() if isinstance(v, str) and k in ("when", "prompt") else v)
            if reschedule and "next_run" not in changes:
                s.next_run = (w or parse_when(s.when)).next_after(time.time())
                s.fails = 0
            self._save()
            return s

    def remove(self, sid: str) -> bool:
        with self.lock:
            gone = self.items.pop(sid, None) is not None
            if gone:
                self._save()
            return gone

    def remove_bot(self, bot: str) -> None:
        with self.lock:
            for s in [s for s in self.items.values() if s.bot == bot]:
                del self.items[s.id]
            self._save()

    def due(self, now: float) -> list:
        return sorted((s for s in self.all() if s.enabled and s.next_run <= now), key=lambda s: s.next_run)
