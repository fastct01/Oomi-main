---
name: commit-with-git
description: Review what changed in a git repository and commit it with a clear message, without pushing unless the user asks.
status: certified
---

## When to use
The user asks to commit, save their work to git, or write a commit message.

## Steps
1. `git status --short` and `git diff --stat` to see what changed; `git diff` (and `git diff --cached`) to read it.
2. Check for things that shouldn't be committed: secrets (`.env`, keys, tokens), build output, large binaries,
   editor files. Leave them out and mention them; suggest a `.gitignore` entry if one is missing.
3. If the changes are about unrelated things, offer to split them into separate commits.
4. Match the repository's message style: `git log --oneline -10`. Otherwise use a short summary line
   (under about 70 characters, imperative: "Fix login redirect") and a body explaining why, if it isn't obvious.
5. Stage exactly what belongs: `git add <paths>` (avoid `git add -A` when there are files you left out), then
   `git commit -m "<summary>" -m "<body>"`.
6. Confirm with `git log --oneline -1` and `git status --short`.

## Pitfalls
- Never push, force-push, rebase, reset or amend published commits unless the user asks for that specifically.
- Respect pre-commit hooks. If one fails, fix the problem it reports and commit again; don't bypass it with `--no-verify`.
- If you're on the main branch and the project uses pull requests, ask whether to make a branch first.
