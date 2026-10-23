"""Turn messy command output into an error fingerprint that matches across machines.

The same bug looks different on every laptop: different home folders, line numbers, memory addresses,
timestamps. `extract_error` picks the lines that describe the error, `normalize` strips what changes
from run to run, and `signature` is a short hash of the result for exact matches. `redact` removes
secrets and personal details before anything is shared with a team.
"""

from __future__ import annotations

import hashlib
import re

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ERROR_WORDS = re.compile(
    r"(error|exception|traceback|fatal|failed|failure|not found|cannot|can't|unable to|denied|refused|"
    r"undefined|no such|segmentation fault|panic|assert|invalid|missing|unexpected|conflict|exit status)", re.I)
NOISE = re.compile(r"^\s*(=+|-+|_+|\^+|~+|\s*\d+ (passed|warnings?)[^a-z]*|platform |rootdir|plugins:|collected |"
                   r"traceback \(most recent call last\))", re.I)
PY_EXC = re.compile(r"^\s*(E\s+)?([A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt|Warning))(:|$)")

PATH = re.compile(r"(?<![\w:/])(?:~|[A-Za-z]:)?(?:[/\\][\w.@+-]+){2,}")
FILE_LINE = re.compile(r"(\.\w{1,6}):\d+(?::\d+)?")
LINE_NO = re.compile(r"\bline \d+", re.I)
HEX = re.compile(r"\b0x[0-9a-fA-F]+\b")
UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
ISO_TIME = re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\b")
CLOCK = re.compile(r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b")
DURATION = re.compile(r"\b\d+(\.\d+)?\s?(ms|s|sec|seconds)\b")
LONG_NUM = re.compile(r"\b\d{4,}\b")
SPACES = re.compile(r"\s+")

SECRETS = [
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\boomi_[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"mongodb(\+srv)?://[^\s'\"]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b\d{13,19}\b"),  # card-like numbers
]
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
HOME = re.compile(r"(/Users|/home)/[^/\s]+")


def redact(text: str) -> str:
    text = HOME.sub("~", text or "")
    for rx in SECRETS:
        text = rx.sub("<secret>", text)
    return EMAIL.sub("<email>", text)


def _basename(m: re.Match) -> str:
    return re.split(r"[/\\]", m.group(0))[-1]


def normalize(text: str) -> str:
    text = ANSI.sub("", text or "")
    text = UUID.sub("<id>", text)
    text = ISO_TIME.sub("<time>", text)
    text = CLOCK.sub("<time>", text)
    text = PATH.sub(_basename, text)
    text = FILE_LINE.sub(r"\1:N", text)
    text = LINE_NO.sub("line N", text)
    text = HEX.sub("0xX", text)
    text = DURATION.sub("<dur>", text)
    text = LONG_NUM.sub("N", text)
    return SPACES.sub(" ", text).strip()


def extract_error(output: str, max_lines: int = 3) -> str:
    """The few lines that say what went wrong, normalized. Empty if nothing looks like an error."""
    lines = [ANSI.sub("", l).rstrip() for l in (output or "").splitlines()]
    lines = [l for l in lines if l.strip() and not NOISE.match(l)]
    if not lines:
        return ""
    # Exception lines ("ModuleNotFoundError: ...", pytest's "E   AssertionError") say the most; when
    # there are any, the rest of a traceback (file paths, source lines) is just noise.
    picked = [l for l in lines if PY_EXC.match(l)]
    if picked:
        max_lines = min(max_lines, 2)
    else:
        picked = [l for l in lines if ERROR_WORDS.search(normalize(l))] or lines[-max_lines:]
    seen, out = set(), []
    for line in reversed(picked):  # the last occurrences are usually the root cause
        n = normalize(re.sub(r"^\s*E\s+", "", line))
        if n and n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
        if len(out) >= max_lines:
            break
    return " | ".join(reversed(out))[:600]


def signature(error_text: str) -> str:
    core = re.sub(r"['\"`]", "", normalize(error_text).lower())
    return hashlib.sha1(core.encode()).hexdigest()[:16]


TEST_RUNNERS = re.compile(r"^(pytest|python3? -m pytest|python3? -m unittest|npm (run )?test|npx (jest|vitest)|"
                          r"cargo test|go test|make test|yarn test|pnpm test)\b")


def command_key(command: str) -> str:
    return SPACES.sub(" ", (command or "").strip())


def command_base(command: str) -> str:
    """For test runners, the runner itself ("pytest"), so `pytest tests/a.py` failing then `pytest`
    passing counts as the same check. For anything else, the whole command."""
    key = command_key(command)
    m = TEST_RUNNERS.match(key)
    if not m:
        return key
    return re.sub(r"^python3? -m ", "", m.group(0))  # `python -m pytest` and `pytest` are the same check
