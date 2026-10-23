"""Entry point inside Oomi.app.

Contents/MacOS/Oomi runs this with `--app` to open the window. The `oomi` command in Resources/bin runs it without,
for the terminal version.
"""

import os
import platform
import subprocess
import sys
import time

RES = os.path.dirname(os.path.realpath(__file__))
APP = os.path.dirname(os.path.dirname(RES))  # …/Oomi.app
ARCH = "arm64" if platform.machine() == "arm64" else "x86_64"
sys.path[:0] = [os.path.join(RES, "app"), os.path.join(RES, "site-packages", ARCH)]
os.environ["OOMI_APP"] = "1"
os.environ["OOMI_RESOURCES"] = RES


def alert(title: str, message: str) -> None:
    if sys.platform == "darwin":
        subprocess.run(["/usr/bin/osascript", "-e", "on run argv", "-e",
                        "display alert (item 1 of argv) message (item 2 of argv)", "-e", "end run", title, message],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def prepare_app() -> None:
    from oomi import __version__
    from oomi.config import oomi_home

    home = oomi_home()
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / "app.log"
    try:
        if log.exists() and log.stat().st_size > 2_000_000:
            log.replace(logs / "app.log.1")
        out = open(log, "a", buffering=1, encoding="utf-8")
        out.write(f"\n--- oomi {__version__} started {time.strftime('%Y-%m-%d %H:%M:%S')} ({ARCH}) ---\n")
        sys.stdout = sys.stderr = out
        os.dup2(out.fileno(), 2)  # crashes in native code land here too
    except OSError:
        pass
    if sys.platform != "darwin":
        return
    if "/AppTranslocation/" in APP:
        # macOS runs apps opened straight from Downloads from a hidden, read-only copy.
        alert("Move Oomi to Applications first",
              "Drag Oomi from your Downloads into the Applications folder, then open it from there.")
        sys.exit(0)
    marker = home / f".ready-{__version__}"
    if not marker.exists():
        # You approved Oomi once in Privacy & Security. Clear the download flag from the files inside it too, so the
        # bundled Python and the `oomi` terminal command run without asking again.
        subprocess.run(["/usr/bin/xattr", "-dr", "com.apple.quarantine", APP],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        marker.touch()


if sys.argv[1:2] == ["--app"]:
    prepare_app()
    from oomi.gui.app import run

    run()
else:
    from oomi.cli import main

    main()
