"""Windows: which shell runs commands, what the model is told, and how a command is stopped. Run anywhere by
pretending to be Windows; .github/workflows/windows.yml builds and starts the real Oomi.exe on Windows."""

import os
import sys

from oomi import prompts, sandbox, tools


def test_commands_run_in_git_bash_not_wsl_and_else_powershell(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    for k in ("ProgramFiles", "ProgramW6432", "LOCALAPPDATA"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: r"C:\Windows\System32\bash.exe")  # WSL's bash
    argv = sandbox.shell_argv("echo hi")
    assert argv[0] == "powershell.exe" and argv[-1] == "echo hi"
    assert "PowerShell syntax" in sandbox.shell_name()

    git = tmp_path / "Git" / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    git.write_text("")
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    assert sandbox.shell_argv("echo hi") == [str(git), "-c", "echo hi"]
    assert sandbox.shell_name() == "Git Bash"


def test_the_model_is_told_it_is_on_windows_and_which_shell(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sandbox, "windows_bash", lambda: r"C:\Program Files\Git\bin\bash.exe")
    text = prompts.build_system_prompt("C:\\work", "", sandboxed=False)
    assert "working on the user's Windows PC" in text and "shell commands run in Git Bash" in text
    assert "Mac" not in text.split("\n")[0]


def test_the_prompt_on_a_mac_is_unchanged(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(prompts.platform, "mac_ver", lambda: ("15.4", ("", "", ""), ""))
    assert prompts.build_system_prompt("/w", "", sandboxed=True).startswith(
        "You are Oomi, a capable autonomous agent working on the user's Mac (macOS 15.4).")


def test_stopping_a_command_ends_its_whole_tree_with_taskkill(monkeypatch):
    calls = []

    class Proc:
        pid = 4242

        def poll(self):
            return None

        def kill(self):
            calls.append("kill")

    class Done:
        returncode = 0

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(tools.subprocess, "run", lambda argv, **kw: calls.append(argv) or Done())
    monkeypatch.setattr(os, "killpg", lambda *a: calls.append("killpg"), raising=False)
    tools._kill_group(Proc(), tools._SIGKILL)
    assert calls == [["taskkill", "/T", "/F", "/PID", "4242"]]
