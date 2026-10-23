#!/bin/bash
# Run oomi from any terminal using the copy inside Oomi.app. Link it onto your PATH with:
#   ln -s /Applications/Oomi.app/Contents/Resources/bin/oomi /usr/local/bin/oomi
SRC="$0"
while [ -L "$SRC" ]; do
  T="$(readlink "$SRC")"
  case "$T" in /*) SRC="$T" ;; *) SRC="$(dirname "$SRC")/$T" ;; esac
done
RES="$(cd "$(dirname "$SRC")/.." && pwd -P)"
case "$(uname -m)" in
  arm64) PY="$RES/runtime/arm64/python/bin/python3" ;;
  *)     PY="$RES/runtime/x86_64/python/bin/python3" ;;
esac
# Compiled files go to ~/.oomi/pycache rather than into the (signed) app.
exec "$PY" -I -X pycache_prefix="$HOME/.oomi/pycache" "$RES/boot.py" "$@"
