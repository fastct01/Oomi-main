"""Oomi.exe, the Windows app. Double-click it and oomi opens in your browser; with arguments it's the `oomi` command
(`Oomi.exe gui`, `Oomi.exe setup`, ...). Built by .github/workflows/windows.yml with PyInstaller (oomi.spec)."""

import sys


def check() -> int:
    """`Oomi.exe --check`: the build runs this to make sure the packaged app has everything it needs."""
    from pathlib import Path

    import anthropic  # noqa: F401
    import dns.resolver  # noqa: F401  (mongodb+srv:// connection strings)
    import pymongo  # noqa: F401

    import oomi
    from oomi.gui import server

    static = Path(server.__file__).parent / "static"
    missing = [n for n in ("index.html", "app.js", "chat.js", "panels.js", "app.css") if not (static / n).exists()]
    try:  # Settings, Account checks time zones against this
        from zoneinfo import ZoneInfo

        ZoneInfo("Europe/London")
        tz = "ok"
    except Exception as e:  # noqa: BLE001
        tz = f"missing ({type(e).__name__})"
    print(f"oomi {oomi.__version__} utf8={sys.flags.utf8_mode} "
          f"static={'ok' if not missing else 'missing ' + ', '.join(missing)} timezones={tz}")
    return 0 if sys.flags.utf8_mode and not missing and tz == "ok" else 1


def main() -> None:
    if sys.argv[1:] == ["--check"]:
        sys.exit(check())
    from oomi.cli import main as oomi_main

    if len(sys.argv) == 1:  # double-clicked: the app, in the browser (there's no native window on Windows)
        sys.argv += ["gui", "--browser"]
    oomi_main()


if __name__ == "__main__":
    main()
