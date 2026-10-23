"""Risk assessment, approval policy and taint tracking.

The threat model is the "lethal trifecta": an agent that (1) can reach your private data,
(2) reads untrusted content such as web pages, and (3) can act on the outside world. Hermes has
all three. Oomi keeps all three too — it's a useful agent — but it tracks when untrusted
content has entered the conversation ("taint") and tightens what may run without asking:

* once tainted, shell commands run with the network switched off inside the macOS sandbox, and any
  command that needs the network has to be approved by you;
* destructive and macOS-sensitive commands always need approval, in every mode;
* a short list of catastrophic commands is refused outright;
* credential locations (~/.ssh, Keychains, browser profiles, ...) need approval to read and are
  unreadable from inside the shell sandbox.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

READ, WRITE, EXEC, DANGER = "read", "write", "exec", "danger"


@dataclass
class Assessment:
    level: str
    summary: str
    reasons: list = field(default_factory=list)
    outside_workspace: bool = False
    network: bool = False
    protected: bool = False
    hard_deny: str = ""
    readonly_shell: bool = False
    approval_key: str = ""
    ask_reason: str = ""  # forces an approval prompt with this explanation
    local_only: bool = False  # no argument reaches outside the working folder
    external: str = ""  # an action in another app (a connected one): asks with this reason unless always-allowed


class Taint:
    """Records which untrusted sources have entered the conversation."""

    def __init__(self) -> None:
        self.sources: list = []

    def add(self, source: str) -> bool:
        if source not in self.sources:
            self.sources.append(source)
            return True
        return False

    @property
    def active(self) -> bool:
        return bool(self.sources)

    def clear(self) -> None:
        self.sources.clear()


# ---------------------------------------------------------------------------------------------
# Paths

def resolve_path(path: str, workspace: Path) -> Path:
    p = Path(os.path.expanduser(path))
    if not p.is_absolute():
        p = workspace / p
    return Path(os.path.realpath(p))


def is_within(p: Path, root: Path) -> bool:
    try:
        Path(os.path.realpath(p)).relative_to(Path(os.path.realpath(root)))
        return True
    except ValueError:
        return False


def is_protected(p: Path, protected: list) -> bool:
    for raw in protected:
        root = Path(os.path.realpath(os.path.expanduser(raw)))
        if p == root or is_within(p, root):
            return True
    return False


# ---------------------------------------------------------------------------------------------
# Shell command analysis

DANGER_PATTERNS = [
    (r"\brm\b[^\n;&|]*\s(-[a-zA-Z]*[rRfP][a-zA-Z]*|--recursive|--force)\b", "recursive or forced delete"),
    (r"\bsudo\b|\bdoas\b", "runs as administrator"),
    (r"\bdiskutil\s+(erase|partition|zero|secure|reformat|unmount)", "modifies disks"),
    (r"\bcsrutil\b|\bspctl\s+--master-disable|\bspctl\s+--global-disable", "weakens macOS security"),
    (r"\bdd\b[^\n]*\bof=", "raw write with dd"),
    (r"\bmkfs\b|\bnewfs", "formats a filesystem"),
    (r"\bsecurity\s+(find|dump|export|delete|unlock|set-key)", "reads or changes the Keychain"),
    (r"\bosascript\b", "AppleScript can control other apps and send data"),
    (r"\blaunchctl\s+(load|bootstrap|submit|enable|kickstart)", "installs a background job"),
    (r"\bcrontab\b", "installs a scheduled job"),
    (r"\bdefaults\s+(write|delete|import)", "changes system or app preferences"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "pipes a download straight into a shell"),
    (r"\bgit\s+push\b[^\n]*(--force|\s-f\b|--mirror|--delete)", "force-pushes or deletes remote history"),
    (r"\bgit\s+(reset\s+--hard|clean\s+-\w*f|checkout\s+--\s|branch\s+-D)", "discards git work"),
    (r"(?i)\bgit\b[^\n;&|]*\s-c\s*[\"']?[\w.-]*(hookspath|fsmonitor|sshcommand|pager|editor|askpass|external|textconv|alias\.|filter\.|credential)",
     "overrides a git setting that can run programs"),
    (r"(?i)\bgit\s+config\b[^\n;&|]*(hookspath|fsmonitor|sshcommand|pager|editor|askpass|external|textconv|alias\.|filter\.|credential)",
     "changes a git setting that can run programs"),
    (r"\bchmod\s+-R|\bchown\s+-R|\bchmod\s+[0-7]*777", "broad permission change"),
    (r"\b(shutdown|reboot|halt)\b|\bpmset\b", "power management"),
    (r"\bkillall\b|\bpkill\b|\bkill\s+-9", "kills processes"),
    (r">\s*/(etc|System|Library|usr|bin|sbin|private/etc)/", "writes to system directories"),
    (r"\bnetworksetup\b|\bscutil\b|\bpfctl\b", "changes network configuration"),
    (r"\btmutil\s+(delete|disable)", "changes Time Machine"),
    (r"\bxattr\s+-\w*d\w*\s+com\.apple\.quarantine|\bxattr\s+-c", "removes Gatekeeper quarantine"),
    (r"\bnpm\s+publish\b|\btwine\s+upload\b|\bgh\s+(release|repo\s+delete|pr\s+merge)", "publishes or changes something public"),
    (r"\bfind\b[^\n]*\s-delete\b", "bulk delete with find"),
    (r"\btrash\s+-e", "permanent delete"),
]

NETWORK_PATTERNS = [
    r"\bcurl\b", r"\bwget\b", r"\bhttpie?\b", r"\bssh\b", r"\bscp\b", r"\bsftp\b", r"\brsync\b",
    r"\bnc\b", r"\bncat\b", r"\btelnet\b", r"\bftp\b",
    r"\bgit\s+(clone|fetch|pull|push|ls-remote|submodule\s+update)", r"\bpip3?\s+(install|download)",
    r"\buv\s+(pip\s+install|add|sync|tool\s+install|run)", r"\bnpm\s+(i|install|ci|add|publish|view)\b",
    r"\bnpx\b", r"\bpnpm\s+(i|install|add)\b", r"\byarn\s+(add|install)?\b", r"\bbrew\s+(install|upgrade|update|tap)",
    r"\bcargo\s+(install|fetch|build|update)", r"\bgo\s+(get|install|mod\s+download)", r"\bgem\s+install",
    r"\bdocker\s+(pull|push|login|run)", r"\bgh\b", r"https?://", r"\bopen\s+https?:",
    r"\bpython3?\s+-m\s+http", r"\bsoftwareupdate\b",
]

# Commands that only read. Each is also checked for the options that make it write (see _is_readonly).
READONLY_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "grep", "egrep", "rg", "file", "stat", "du", "df",
    "which", "whoami", "date", "echo", "printf", "tree", "sort", "uniq", "cut", "diff", "cmp",
    "sw_vers", "uname", "basename", "dirname", "realpath", "true", "test", "[", "nl",
    "md5", "shasum", "sha256sum", "jq", "column", "tr", "comm", "type", "mdls",
}
READONLY_GIT = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "blame", "describe", "tag", "shortlog"}
_FIND_ACTIONS = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf", "-fls"}
_PREFIX_COMMANDS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "env", "caffeinate", "timeout"}

_PROTECTED_RM_TARGETS = {
    "/", "/*", "~", "~/", "~/*", "$HOME", "$HOME/", "${HOME}", "/Users", "/Users/", "/System", "/Library",
    "/Applications", "/usr", "/bin", "/sbin", "/private", "/etc", "/var", "/opt", "/Volumes", ".", "./", "..", "*",
}

_FD_REDIRECT = re.compile(r"\d*[<>]&\d+")


def _segments(command: str) -> list:
    """Split into simple commands: on ; & && || | newlines, and into $( ), ` `, ( ), { }, <( ), >( )."""
    s = _FD_REDIRECT.sub(" ", command).replace("&>", ">")
    parts = re.split(r"\|\||&&|[;&|\n`(){}]|\$\(|<\(|>\(", s)
    return [p.strip() for p in parts if p.strip()]


def _argv(seg: str) -> list:
    try:
        argv = shlex.split(seg)
    except ValueError:
        return []
    while argv and (os.path.basename(argv[0]) in _PREFIX_COMMANDS or "=" in argv[0]):
        argv = argv[1:]
        while argv and (argv[0].startswith("-") or argv[0].replace(".", "").isdigit() or "=" in argv[0]):
            argv = argv[1:]
    return argv


def _hard_deny(command: str, home: str) -> str:
    if re.search(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", command):
        return "fork bomb"
    if re.search(r"\bdiskutil\s+(eraseDisk|eraseVolume|secureErase|zeroDisk|randomDisk|reformat)", command):
        return "erases a disk"
    if re.search(r"\bdd\b[^\n]*\bof=/dev/r?disk", command):
        return "raw write to a disk device"

    def catastrophic(target: str, relative_ok: bool = False) -> bool:
        t = target.rstrip("/") or "/"
        if relative_ok and t in (".", "..", "*", "./"):
            return False
        if target in _PROTECTED_RM_TARGETS or t in _PROTECTED_RM_TARGETS:
            return True
        expanded = os.path.realpath(os.path.expanduser(os.path.expandvars(target)))
        return expanded in ("/", home, os.path.dirname(home)) or expanded.startswith("/System")

    for seg in _segments(command):
        argv = _argv(seg)
        if not argv:
            continue
        cmd = os.path.basename(argv[0])
        if cmd == "rm":
            flags = "".join(a.lstrip("-") for a in argv[1:] if a.startswith("-"))
            if "r" not in flags.lower():
                continue
            for target in (a for a in argv[1:] if not a.startswith("-")):
                if catastrophic(target):
                    return f"recursive delete of {target}"
        elif cmd == "find" and "-delete" in argv:
            for target in (a for a in argv[1:] if not a.startswith("-")):
                if catastrophic(target, relative_ok=True):
                    return f"bulk delete under {target}"
                break
    return ""


def _is_readonly(command: str) -> bool:
    if any(t in command for t in ("$(", "`", "<(", ">(")):
        return False
    s = _FD_REDIRECT.sub(" ", command)
    s = re.sub(r"\d?>\s*/dev/null", " ", s)
    if ">" in s or re.search(r"\btee\b", s):
        return False
    if re.search(r"(?<!&)&(?!&)", s):  # background job: a second command hiding behind a harmless one
        return False
    for seg in _segments(command):
        argv = _argv(seg)
        if not argv:
            return False
        if argv[0] != os.path.basename(argv[0]) or "=" in seg.split()[0]:
            return False  # explicit paths or env prefixes: not worth guessing about
        cmd, args = argv[0], argv[1:]
        if cmd == "git":
            if any(a == "-c" or a.startswith(("-c", "--exec", "--output", "--ext-diff", "--config-env")) for a in args):
                return False
            sub = next((a for a in args if not a.startswith("-")), "")
            if sub not in READONLY_GIT:
                return False
            rest = args[args.index(sub) + 1:]
            if sub in ("branch", "tag") and any(
                    not a.startswith("-") or a not in ("-a", "-r", "-l", "--list", "-v", "-vv") for a in rest):
                return False
            continue
        if cmd not in READONLY_COMMANDS and cmd != "find":
            return False
        if cmd == "find" and _FIND_ACTIONS & set(args):
            return False
        if cmd == "rg" and any(a.startswith(("--pre", "--pre-glob")) for a in args):
            return False  # runs a preprocessor program on every file
        if cmd == "sort" and any(a.startswith("--compress-program") for a in args):
            return False
        if cmd == "sort" and any(a.startswith(("-o", "--output")) or (a.startswith("-") and not a.startswith("--") and "o" in a) for a in args):
            return False
        if cmd == "uniq" and len([a for a in args if not a.startswith("-")]) > 1:
            return False  # `uniq in out` writes `out`
        if cmd == "tree" and any(a in ("-o", "-R") for a in args):
            return False
        if cmd == "file" and any(a.startswith("-C") or a == "--compile" for a in args):
            return False
    return True


def _visibly_local(command: str) -> bool:
    """True if no argument reaches outside the current folder (used when there is no sandbox)."""
    if re.search(r"[~$]|\.\.", command):
        return False
    for seg in _segments(command):
        for a in _argv(seg)[1:]:
            if (a.startswith("/") and a != "/dev/null") or "=/" in a:
                return False
    return True


def _normalize(command: str) -> str:
    return command.replace("'", "").replace('"', "").replace("\\", "").replace("${HOME}", "$HOME")


def assess_shell(command: str, protected: list) -> Assessment:
    home = os.path.realpath(os.path.expanduser("~"))
    deny = _hard_deny(command, home)
    if deny:
        return Assessment(DANGER, command, [deny], hard_deny=deny, approval_key=f"shell:{command}")

    reasons = [why for pat, why in DANGER_PATTERNS if re.search(pat, command)]
    network = any(re.search(p, command) for p in NETWORK_PATTERNS)
    touches_protected = False
    norm = _normalize(command)
    for raw in protected:
        base = raw.rstrip("/")
        variants = {base, os.path.expanduser(base), base.replace("~", "$HOME", 1)}
        if any(v and v in norm for v in variants):
            touches_protected = True
            reasons.append(f"mentions protected path {raw}")
            break

    if reasons and not touches_protected:
        return Assessment(DANGER, command, reasons, network=network, approval_key=f"shell:{command}")
    return Assessment(
        EXEC,
        command,
        reasons,
        network=network,
        protected=touches_protected,
        readonly_shell=(not network and not touches_protected and _is_readonly(command)),
        approval_key=f"shell:{command}",
        local_only=_visibly_local(command),
    )


# ---------------------------------------------------------------------------------------------
# Policy

TRUST_LEVELS = ("read-only", "project", "full")
READ_ONLY_MSG = ("Oomi is in read-only mode, so nothing can be changed right now. Finish investigating and tell the "
                 "user what you'd do; they can switch with /trust project (or /go to approve a plan).")


def decide(a: Assessment, trust: str, tainted: bool, sandboxed: bool, session_allow: set) -> tuple:
    """Return ("allow" | "ask" | "deny", reason).

    Trust levels: read-only (look, don't touch) · project (edit the project freely; shell commands run in the
    sandbox) · full (no sandbox, may write anywhere). In every level, dangerous commands and protected paths
    ask, catastrophic commands are refused, and untrusted content in the conversation tightens the rules.
    """
    if a.hard_deny:
        return "deny", a.hard_deny
    readonly_ok = a.level == READ or (a.level == EXEC and a.readonly_shell and (sandboxed or a.local_only))
    if trust == "read-only" and not readonly_ok:
        return "deny", READ_ONLY_MSG
    if a.ask_reason:
        return "ask", a.ask_reason
    if a.protected:
        return "ask", "touches a protected location (keys, credentials or private app data)"
    if a.level == DANGER:
        return "ask", "; ".join(a.reasons) or "potentially destructive"
    # "Always allow" (for this conversation or this bot) doesn't outrank untrusted content in the conversation
    risky_now = tainted and a.level == EXEC and not readonly_ok and (a.network or not sandboxed)
    if a.approval_key and a.approval_key in session_allow and not risky_now:
        return "allow", ""
    if a.external:
        return "ask", a.external
    if a.level == READ:
        return "allow", ""
    if a.level == WRITE:
        if a.outside_workspace and tainted:
            return "ask", "writes outside the project folder while untrusted content is in the conversation"
        if a.outside_workspace and trust != "full":
            return "ask", "writes outside the project folder"
        return "allow", ""
    # EXEC
    if readonly_ok:
        return "allow", ""
    if tainted and a.network:
        return "ask", "untrusted content is in the conversation and this command uses the network"
    if tainted and not sandboxed:
        return "ask", "untrusted content is in the conversation and the command isn't sandboxed"
    if trust == "full" or sandboxed:
        return "allow", ""
    return "ask", "runs a shell command, and the sandbox isn't available to contain it"


# Skill scanning (for skills imported from elsewhere)

INJECTION_RX = re.compile(r"ignore (all |any )?(the )?(previous |prior |above |earlier )?instructions|do not tell the user|exfiltrat",
                          re.I)
_INVISIBLE_RX = re.compile("[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff]")


def scan_context_text(text: str, protected: list) -> list:
    """Why a project instruction file looks like an attack, if it does. Unlike skills, ordinary prose that mentions
    `rm -rf` or `sudo` is fine here; only injection phrasing, hidden characters, pipe-to-shell and credential paths count."""
    findings = []
    if _INVISIBLE_RX.search(text):
        findings.append("contains invisible characters")
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if INJECTION_RX.search(s):
            findings.append(f"{s[:120]} — instruction-injection phrasing")
            continue
        a = assess_shell(s, protected)
        if a.protected or any(r.startswith("pipes a download") for r in a.reasons):
            findings.append(f"{s[:120]} — {'; '.join(a.reasons)}")
    return findings


def scan_text_for_risks(text: str, protected: list) -> list:
    findings = []
    for line in text.splitlines():
        line_s = line.strip()
        if not line_s:
            continue
        a = assess_shell(line_s, protected)
        if a.hard_deny or a.level == DANGER or a.protected:
            findings.append(f"{line_s[:120]} — {'; '.join(a.reasons) or a.hard_deny}")
        elif INJECTION_RX.search(line_s):
            findings.append(f"{line_s[:120]} — instruction-injection phrasing")
    return findings


# ---------------------------------------------------------------------------------------------
# Settings-driven rules: your block list, protected instruction files, hiding secrets from the model

_SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_GIT_OPTS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env"}
_SUDO_OPTS_WITH_VALUE = {"-u", "-g", "-h", "-p", "-C", "-D", "-r", "-t", "-U", "-T"}
_REDIRECT = re.compile(r"^\d*(?:[<>]+&?|&>+)")


def _words(seg: str) -> list:
    try:
        return shlex.split(seg)
    except ValueError:  # unbalanced quotes: still look at the words
        return re.sub(r"[\"'\\]", " ", seg).split()


def _program_forms(command: str, depth: int = 0) -> list:
    """Each simple command, as "program arg arg…": quotes and extra spaces gone, the program's path dropped,
    and env/sudo/nohup/time/VAR=1 in front, subshells and `bash -c "…"` seen through."""
    out = []
    for seg in _segments(command):
        argv = _words(seg)
        while argv:
            head = os.path.basename(argv[0]).lower()
            if "=" in argv[0] and not argv[0].startswith("-"):
                argv = argv[1:]
            elif head in _PREFIX_COMMANDS:
                argv = argv[1:]
                while argv and (argv[0].startswith("-") or argv[0].replace(".", "").isdigit() or "=" in argv[0]):
                    argv = argv[2:] if head in ("sudo", "doas") and argv[0] in _SUDO_OPTS_WITH_VALUE else argv[1:]
            else:
                break
        if not argv:
            continue
        prog = os.path.basename(argv[0]).lower()
        args = argv[1:]
        if prog in _SHELLS and depth < 3:
            for i, a in enumerate(args):
                if a.startswith("-") and not a.startswith("--") and "c" in a[1:]:
                    out += _program_forms(" ".join(args[i + 1:]), depth + 1)
                    break
        if prog == "git":
            i = 0
            while i < len(args) and args[i].startswith("-"):
                i += 2 if args[i] in _GIT_OPTS_WITH_VALUE else 1
            args = args[i:]
        out.append(" ".join([prog] + args))
    return out


def blocked_by(command: str, patterns) -> str:
    """The block-list pattern a command matches ("" if none). Each simple command in it is checked (so
    `cd x && git push` matches `git push*`), with spacing, quotes, capitals, the program's path, prefixes like
    env, sudo or VAR=1, subshells and `bash -c "…"` evened out. It's pattern matching: a guard rail, not a sandbox."""
    pats = [(p.strip(), " ".join(p.split()).lower()) for p in (patterns or []) if isinstance(p, str) and p.strip()]
    if not pats:
        return ""
    unquoted = re.sub(r"[\"'\\]", "", command)
    forms = {" ".join(command.split()).lower()}
    forms.update(" ".join(x.split()).lower() for x in re.split(r"\|\||&&|[;|&\n]", command) if x.strip())
    for variant in (command, unquoted):
        forms.update(f.lower() for f in _program_forms(variant))
    for original, pat in pats:
        if any(fnmatch.fnmatchcase(f, pat) for f in forms):
            return original
    return ""


PROTECTED_WRITE_DEFAULT = [".oomi.md", "CLAUDE.md", "AGENTS.md", ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "id_ed25519*",
                           ".npmrc", ".pypirc"]


def _protected_name(name: str, names) -> str:
    low = name.lower()  # macOS disks ignore case: claude.md is CLAUDE.md
    for pat in names or []:
        if isinstance(pat, str) and pat.strip() and fnmatch.fnmatchcase(low, pat.strip().lower()):
            return pat.strip()
    return ""


def protected_write(path: Path, names) -> str:
    """Files whose changes always ask: instructions an agent follows, and files that usually hold secrets."""
    return _protected_name(path.name, names)


def protected_in_command(command: str, names) -> str:
    """A protected file a shell command mentions (as an argument or a redirect target), if any."""
    if not names:
        return ""
    for variant in (command, re.sub(r"[\"'\\]", "", command)):
        for seg in _segments(variant):
            for word in _words(seg):
                word = _REDIRECT.sub("", word).rstrip("/")
                if word and not word.startswith("-"):
                    pat = _protected_name(os.path.basename(word), names)
                    if pat:
                        return pat
    return ""


HIDDEN = "[secret hidden by oomi]"
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bhf_[A-Za-z0-9]{30,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\b[rs]k_live_[0-9A-Za-z]{16,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
]
# keep the part before the secret, hide the rest
_KEEP_PREFIX = [
    re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=-]{16,})"),  # Authorization: Bearer …
    re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+:)([^@\s/]+)(?=@)"),  # scheme://user:password@host
]
# name = value (DB_PASSWORD, accessToken, "api_key": …), only when the value looks like a literal secret, so code
# such as `token = get_token()` is left alone
_ASSIGNED = re.compile(
    r"(?i)((?:\b|(?<=[a-z0-9_]))(?:api[_-]?key|secret(?:[_-]?key)?|token|password|passwd|pwd|access[_-]?key|"
    r"client[_-]?secret|auth)[\"']?\s*[:=]\s*)(?:([\"'])([^\"'\s]{8,})\2|([A-Za-z0-9_\-/+=.~!@#$%^&*]{8,}))")


def _looks_secret(value: str) -> bool:
    if len(value) >= 16:
        return True
    return any(c.isdigit() for c in value) and any(c.isalpha() for c in value)


def redact(text: str) -> tuple:
    """(text with secrets replaced, how many) — applied to tool output before the model sees it."""
    n = 0
    for rx in _SECRET_PATTERNS:
        text, k = rx.subn(HIDDEN, text)
        n += k
    for rx in _KEEP_PREFIX:
        text, k = rx.subn(lambda m: m.group(1) + HIDDEN, text)
        n += k

    def assigned(m: re.Match) -> str:
        nonlocal n
        quote = m.group(2) or ""
        if not quote and not _looks_secret(m.group(4)):
            return m.group(0)
        if HIDDEN in m.group(0):
            return m.group(0)
        n += 1
        return f"{m.group(1)}{quote}{HIDDEN}{quote}"

    text = _ASSIGNED.sub(assigned, text)
    return text, n
