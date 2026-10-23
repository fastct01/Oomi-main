"""Everything Oomi changed this session, and how to take it back.

* File tools back up a file before every write, so each edit can be undone.
* Shell commands are watched: the project is scanned before and after, so /changes lists files a command
  created, modified or deleted, even though Oomi didn't write them itself.
* Before a risky command (one you had to approve as dangerous, or one run outside the sandbox) the whole
  project is snapshotted. On macOS that's an APFS clone (copy-on-write: instant, no extra disk space
  until files change). /undo then puts the project back exactly as it was.
"""

from __future__ import annotations

import difflib
import os
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
                ".ruff_cache", ".next", ".turbo", "dist", "build", "target", ".idea", ".gradle", ".DS_Store"}
MAX_SCAN_FILES = 20_000
MAX_COPY_BYTES = 500 * 1024 * 1024  # without APFS clones, don't copy more than this per snapshot


def _walk(root: Path):
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.name in IGNORED_DIRS:
                continue
            if e.is_dir(follow_symlinks=False):
                stack.append(Path(e.path))
            elif e.is_file(follow_symlinks=False):
                yield e


class UndoError(Exception):
    """An undo step couldn't be done safely. Nothing was changed and the journal is intact."""


def _new_id() -> str:
    return f"{time.time_ns():x}-{uuid.uuid4().hex[:6]}"


class Changes:
    def __init__(self, root: Path, workspace: Path):
        self.root = Path(root)
        self.workspace = Path(os.path.realpath(workspace))
        self.journal: list = []

    # -- file tools -------------------------------------------------------------------------------
    def before_write(self, path: Path, tool: str = "") -> None:
        path = Path(path)
        if path.is_dir():
            raise IsADirectoryError(f"{path} is a directory")
        backup = None
        if path.is_file():
            self.root.mkdir(parents=True, exist_ok=True)
            backup = self.root / f"{_new_id()}-{path.name}"
            shutil.copy2(path, backup)
        self.journal.append({"kind": "file", "path": str(path), "backup": str(backup) if backup else None,
                             "parent_real": os.path.realpath(path.parent), "tool": tool, "t": time.time()})

    def discard_last(self) -> None:
        """Forget the last file entry (its write failed, so there's nothing to undo)."""
        if self.journal and self.journal[-1]["kind"] == "file":
            self.journal.pop()

    # -- shell commands ---------------------------------------------------------------------------
    def scan(self) -> Optional[dict]:
        out = {}
        for i, e in enumerate(_walk(self.workspace)):
            if i >= MAX_SCAN_FILES:
                return None
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            out[os.path.relpath(e.path, self.workspace)] = (st.st_mtime_ns, st.st_size)
        return out

    def record_shell(self, command: str, before: Optional[dict], after: Optional[dict]) -> dict:
        if before is None or after is None:
            return {}
        created = sorted(set(after) - set(before))
        deleted = sorted(set(before) - set(after))
        modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
        if created or deleted or modified:
            self.journal.append({"kind": "shell", "command": command, "created": created, "modified": modified,
                                 "deleted": deleted, "t": time.time()})
        return {"created": created, "modified": modified, "deleted": deleted}

    def snapshot(self, label: str) -> Optional[str]:
        """Copy the project (minus caches and dependencies) aside. Returns None when it's too big to copy."""
        files = []
        total = 0
        for i, e in enumerate(_walk(self.workspace)):
            if i >= MAX_SCAN_FILES:
                return None
            files.append(e.path)
            try:
                total += e.stat(follow_symlinks=False).st_size
            except OSError:
                pass
        dest = self.root / f"snapshot-{_new_id()}"  # never reused, so an old snapshot can't leak into a new one
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        cloned = False
        complete = True
        if sys.platform == "darwin":
            cloned = self._apfs_clone(files, dest)
        if not cloned:
            if total > MAX_COPY_BYTES:
                shutil.rmtree(dest, ignore_errors=True)
                return None
            for f in files:
                target = dest / os.path.relpath(f, self.workspace)
                target.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, target, follow_symlinks=False)
                except OSError:
                    complete = False
        self.journal.append({"kind": "snapshot", "dir": str(dest), "label": label, "files": len(files),
                             "complete": complete, "t": time.time()})
        return str(dest)

    def _apfs_clone(self, files: list, dest: Path) -> bool:
        """cp -c uses clonefile(2): instant and free on APFS. Batched to keep process count low."""
        try:
            by_dir: dict = {}
            for f in files:
                rel_dir = os.path.dirname(os.path.relpath(f, self.workspace))
                by_dir.setdefault(rel_dir, []).append(f)
            for rel_dir, group in by_dir.items():
                target = dest / rel_dir
                target.mkdir(parents=True, exist_ok=True)
                for i in range(0, len(group), 200):
                    r = subprocess.run(["cp", "-c", "-p", "--", *group[i:i + 200], str(target)],
                                       capture_output=True, timeout=120)
                    if r.returncode != 0:
                        shutil.rmtree(dest, ignore_errors=True)
                        dest.mkdir(parents=True)
                        return False
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    # -- safety helpers ---------------------------------------------------------------------------
    def _inside(self, p: Path) -> bool:
        """True if writing or deleting `p` can only affect the project: no symlinked folder on the way, and the
        resolved parent is still inside the workspace."""
        try:
            rel = Path(p).relative_to(self.workspace)
        except ValueError:
            return False
        cur = self.workspace
        for part in rel.parts[:-1]:
            cur = cur / part
            if cur.is_symlink():
                return False
        parent = os.path.realpath(Path(p).parent)
        return parent == str(self.workspace) or parent.startswith(str(self.workspace) + os.sep)

    @staticmethod
    def _make_writable(p: Path) -> None:
        if p.exists() and not os.access(p, os.W_OK):
            os.chmod(p, p.stat().st_mode | stat.S_IWUSR)

    # -- undo -------------------------------------------------------------------------------------
    def _plan(self) -> Optional[tuple]:
        """What the next /undo will do: (index of the entry it acts on, kind, description)."""
        i = len(self.journal) - 1
        while i >= 0:
            e = self.journal[i]
            if e["kind"] == "file":
                return i, "file", f"{'remove' if not e['backup'] else 'restore'} {self._rel(e['path'])}"
            if e["kind"] == "snapshot":
                return i, "snapshot", f"put the whole project back as it was before `{e['label']}`"
            prev = self.journal[i - 1] if i else None
            if prev and prev["kind"] == "snapshot" and e["command"].startswith(prev["label"]):
                return i - 1, "snapshot", f"put the whole project back as it was before `{prev['label']}`"
            existing = [r for r in e["created"] if (self.workspace / r).is_file() or (self.workspace / r).is_symlink()]
            if existing:
                return i, "shell", f"remove {len(existing)} file(s) created by `{e['command'][:60]}`"
            i -= 1
        return None

    def can_undo(self) -> bool:
        return self._plan() is not None

    def peek(self) -> Optional[str]:
        plan = self._plan()
        return plan[2] if plan else None

    def next_kind(self) -> Optional[str]:
        plan = self._plan()
        return plan[1] if plan else None

    def undo(self) -> Optional[str]:
        """Undo one step. Raises UndoError (leaving everything as it was) if it can't be done safely."""
        plan = self._plan()
        lost = []
        if not plan or plan[1] != "snapshot":  # a snapshot restore reverts everything after it, too
            for e in self.journal[(plan[0] + 1) if plan else 0:]:
                if e["kind"] == "shell" and (e["modified"] or e["deleted"]):
                    lost.append(f"`{e['command'][:60]}` changed {', '.join((e['modified'] + e['deleted'])[:4])}")
        note = ("; couldn't restore what " + "; ".join(lost) + " (only risky commands are snapshotted)") if lost else ""
        if plan is None:
            had = bool(self.journal)
            self.journal.clear()
            return ("Nothing else can be undone" + note + ".") if had and lost else None
        idx, kind, _ = plan
        entry = self.journal[idx]
        try:
            if kind == "file":
                msg = self._undo_file(entry)
            elif kind == "snapshot":
                msg = self._restore_snapshot(entry)
            else:
                msg = self._undo_created(entry)
        except UndoError:
            raise
        except OSError as e:
            raise UndoError(f"couldn't undo ({e}); nothing else was changed") from e
        del self.journal[idx:]
        return msg + note

    def _undo_file(self, e: dict) -> str:
        path, rel = Path(e["path"]), self._rel(e["path"])
        if e.get("parent_real") and os.path.realpath(path.parent) != e["parent_real"]:
            raise UndoError(f"{rel} now sits behind a link to somewhere else, so it was left alone")
        if e["backup"] is None:
            if path.is_symlink() or path.is_file():
                path.unlink()
                return f"removed {rel} (Oomi created it)"
            if path.is_dir():
                raise UndoError(f"{rel} is now a folder, so it was left alone")
            return f"{rel} was already gone"
        if not Path(e["backup"]).is_file():
            raise UndoError(f"the backup of {rel} is missing, so it was left alone")
        if path.is_dir() and not path.is_symlink():
            raise UndoError(f"{rel} is now a folder, so it was left alone")
        if path.is_symlink():
            path.unlink()  # never write through a link that appeared after the edit
        path.parent.mkdir(parents=True, exist_ok=True)
        self._make_writable(path)
        shutil.copy2(e["backup"], path)
        return f"restored {rel}"

    def _undo_created(self, e: dict) -> str:
        removed, skipped = [], []
        for r in e["created"]:
            p = self.workspace / r
            if not self._inside(p):
                skipped.append(r)
            elif p.is_symlink() or p.is_file():
                p.unlink()
                removed.append(r)
        msg = f"removed {', '.join(removed[:5])}{' …' if len(removed) > 5 else ''} (created by `{e['command'][:60]}`)"
        if e["modified"] or e["deleted"]:
            msg += f"; couldn't restore {', '.join((e['modified'] + e['deleted'])[:5])} (no snapshot was taken)"
        if skipped:
            msg += f"; left {len(skipped)} path(s) that now lead outside the project"
        return msg

    def _restore_snapshot(self, e: dict) -> str:
        snap = Path(e["dir"])
        if not snap.is_dir():
            raise UndoError("the snapshot for this step is missing, so nothing was changed")
        snap_files = {os.path.relpath(os.path.join(dp, f), snap) for dp, _, fs in os.walk(snap) for f in fs}
        if e.get("files") and not snap_files:
            raise UndoError("the snapshot for this step is empty, so nothing was changed")
        # Deleting files that aren't in the snapshot is only safe if the snapshot is known to be complete.
        complete = e.get("complete", True) and len(snap_files) >= e.get("files", 0)
        removed, restored, skipped = 0, 0, []
        if complete:
            for de in list(_walk(self.workspace)):
                rel = os.path.relpath(de.path, self.workspace)
                if rel not in snap_files:
                    if self._inside(Path(de.path)):
                        os.unlink(de.path)
                        removed += 1
                    else:
                        skipped.append(rel)
        for rel in sorted(snap_files):
            target = self.workspace / rel
            if not self._inside(target):
                skipped.append(rel)
                continue
            if target.is_symlink():
                target.unlink()  # a link planted after the snapshot: replace it, never write through it
            elif target.is_dir():
                skipped.append(rel)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            self._make_writable(target)
            shutil.copy2(snap / rel, target, follow_symlinks=False)
            restored += 1
        msg = f"put the project back as it was before `{e['label']}` ({restored} files restored"
        msg += f", {removed} newer files removed)" if removed else ")"
        if not complete:
            msg += "; the snapshot was incomplete, so no files were deleted"
        if skipped:
            msg += f"; left {len(skipped)} path(s) that now lead outside the project or became folders"
        return msg

    # -- the "what did it change" view -------------------------------------------------------------
    def _rel(self, path: str) -> str:
        try:
            return os.path.relpath(path, self.workspace) if Path(path).is_relative_to(self.workspace) else path
        except (AttributeError, ValueError):
            return path

    def summary(self) -> list:
        """[(path, status, detail)] for every file touched this session, oldest first."""
        files: dict = {}
        for e in self.journal:
            if e["kind"] == "file":
                rel = self._rel(e["path"])
                first = rel not in files
                if first:
                    files[rel] = {"status": "created" if not e["backup"] else "modified", "via": e.get("tool") or "edit",
                                  "original": e["backup"], "path": e["path"]}
            elif e["kind"] == "shell":
                for kind in ("created", "modified", "deleted"):
                    for rel in e[kind]:
                        files.setdefault(rel, {"status": kind, "via": f"`{e['command'][:60]}`", "original": None, "path": None})
        out = []
        for rel, info in files.items():
            detail = info["via"]
            if info["path"]:
                a, b = self._line_counts(info["original"], info["path"])
                detail += f"  +{a} −{b}"
            out.append((rel, info["status"], detail))
        return out

    def snapshots(self) -> list:
        return [e for e in self.journal if e["kind"] == "snapshot"]

    def _line_counts(self, original: Optional[str], current: str) -> tuple:
        old = Path(original).read_text(errors="replace").splitlines() if original else []
        new = Path(current).read_text(errors="replace").splitlines() if Path(current).exists() else []
        add = rem = 0
        for line in difflib.unified_diff(old, new, lineterm="", n=0):
            if line.startswith("+") and not line.startswith("+++"):
                add += 1
            elif line.startswith("-") and not line.startswith("---"):
                rem += 1
        return add, rem

    def diff(self, only: str = "") -> str:
        firsts: dict = {}
        for e in self.journal:
            if e["kind"] == "file":
                firsts.setdefault(e["path"], e["backup"])
        chunks = []
        for path, backup in firsts.items():
            rel = self._rel(path)
            if only and only not in (rel, path):
                continue
            old = Path(backup).read_text(errors="replace").splitlines() if backup else []
            new = Path(path).read_text(errors="replace").splitlines() if Path(path).exists() else []
            chunks += list(difflib.unified_diff(old, new, f"a/{rel}", f"b/{rel}", lineterm=""))
        return "\n".join(chunks)
