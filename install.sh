#!/usr/bin/env bash
# Installs the `oomi` command for your user, then runs first-time setup.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Note: oomi is built for macOS. It runs elsewhere, but the shell sandbox only works on a Mac."
fi

if command -v uv >/dev/null 2>&1; then
  if [[ "$(uname)" == "Darwin" ]]; then uv tool install --force --python 3.12 ".[gui]"; else uv tool install --force --python 3.12 .; fi
  BIN="$(uv tool dir --bin)"
  uv tool update-shell >/dev/null 2>&1 || true
elif command -v pipx >/dev/null 2>&1; then
  if [[ "$(uname)" == "Darwin" ]]; then pipx install --force ".[gui]"; else pipx install --force .; fi
  BIN="$HOME/.local/bin"
else
  echo "oomi installs with uv (a fast Python tool manager). Install it first:"
  echo "    brew install uv"
  echo "or  curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "then run ./install.sh again."
  exit 1
fi

echo
"$BIN/oomi" setup
echo
if ! command -v oomi >/dev/null 2>&1; then
  echo "Open a new terminal tab so the 'oomi' command is on your PATH."
fi
echo "Then: run  oomi gui  for the app window, or cd into a project folder and run  oomi  in the terminal."
