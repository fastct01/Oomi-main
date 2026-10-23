"""Open oomi's window: a native macOS window around the local server, or a browser tab anywhere else."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import uuid
import webbrowser
from pathlib import Path

from .server import App, Server


def import_login_path() -> None:
    """Apps opened from the Dock get a bare PATH. Use the one your login shell sets up (Homebrew, nvm, ~/.local/bin…)."""
    shell = os.environ.get("SHELL") or "/bin/zsh"
    if not os.path.isabs(shell) or not os.access(shell, os.X_OK):
        return
    try:
        out = subprocess.run([shell, "-l", "-c", 'printf "__OOMI_PATH__%s" "$PATH"'], capture_output=True, text=True,
                             timeout=6, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired):
        return
    marker = out.stdout.rfind("__OOMI_PATH__")
    if marker >= 0:
        path = out.stdout[marker + len("__OOMI_PATH__"):].strip()
        if path and "/usr/bin" in path:
            os.environ["PATH"] = path


class MacNative:
    """The bits of the app that need macOS itself: dialogs, Dock badge, notifications, dictation, window look."""

    def __init__(self):
        self.window = None  # the main window
        self.windows: dict = {}  # name -> pop-out window
        self.url = ""
        self.app: App | None = None
        self._un = None

    def capabilities(self) -> dict:
        return {"pick_folder": True, "dictate": True, "reveal": True, "terminal": True, "windows": True}

    def _main(self, fn) -> None:
        from PyObjCTools import AppHelper

        AppHelper.callAfter(fn)

    # called by the server for POST /api/native
    def call(self, action: str, body: dict):
        if action == "pick_folder":
            import webview

            start = str(body.get("start") or Path.home())
            res = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
            return res[0] if res else None
        if action == "dictate":
            def go():
                import AppKit

                AppKit.NSApp.sendAction_to_from_("startDictation:", None, None)

            self._main(go)
            return True
        if action == "open_window":
            return self.open_window(body)
        if action == "float":
            win = self.windows.get(str(body.get("name") or ""))
            if win is not None:
                win.on_top = bool(body.get("on"))
            return bool(win)
        if action == "sound":
            self.play(str(body.get("name") or ""))
            return True
        raise ValueError(f"unknown action {action}")

    def open_window(self, body: dict) -> str:
        """A bot in a window of its own (compact), or another full workspace window."""
        import webview

        kind = "bot" if body.get("kind") == "bot" else "extra"
        bot = str(body.get("bot") or "")
        if kind == "bot" and not re.fullmatch(r"b[0-9a-f]{12}", bot):
            raise ValueError("bad bot id")
        name = f"bot-{bot}" if kind == "bot" else f"extra-{uuid.uuid4().hex[:6]}"
        old = self.windows.get(name)
        if old is not None:  # already open: bring it forward
            try:
                old.restore()
                old.show()
                return name
            except Exception:
                self.windows.pop(name, None)
        title = " ".join(str(body.get("title") or "oomi").split())[:60]
        query = f"&w={kind}&name={name}" + (f"&bot={bot}" if kind == "bot" else "")
        size = (460, 720, (340, 380)) if kind == "bot" else (1200, 800, (780, 520))
        win = webview.create_window(title, self.url + query, width=size[0], height=size[1], min_size=size[2],
                                    background_color="#000000", text_select=True)
        self.windows[name] = win
        win.events.before_show += lambda: self.style(win)
        win.events.closed += lambda: self.windows.pop(name, None)
        return name

    def set_badge(self, n: int) -> None:
        def go():
            import AppKit

            AppKit.NSApp.dockTile().setBadgeLabel_(str(n) if n else "")

        self._main(go)

    def play(self, name: str) -> None:
        if not name:
            return

        def go():
            import AppKit

            snd = AppKit.NSSound.soundNamed_(name)
            if snd is not None:
                snd.play()

        self._main(go)

    def set_icon(self, color: str) -> None:
        """Show oomi in this color in the Dock while it runs. White is the app's own icon."""
        def go():
            import AppKit

            path = Path(__file__).parent.parent / "assets" / "dock" / f"{color}.png"
            image = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path)) if color != "white" else None
            AppKit.NSApp.setApplicationIconImage_(image)

        self._main(go)

    def notify(self, title: str, body: str, kind: str = "") -> None:
        from ..config import Config

        cfg = Config.load()
        wanted = {"approval": cfg.notify_approval, "done": cfg.notify_done, "failed": cfg.notify_failed,
                  "test": True}.get(kind, True)
        if not (wanted and (cfg.notify or kind == "test")):
            return
        self.play(cfg.notify_sound)
        try:
            import UserNotifications as UN

            center = UN.UNUserNotificationCenter.currentNotificationCenter()
            if self._un is None:
                center.requestAuthorizationWithOptions_completionHandler_(
                    UN.UNAuthorizationOptionAlert | UN.UNAuthorizationOptionSound, lambda granted, err: None)
                self._un = True
            content = UN.UNMutableNotificationContent.alloc().init()
            content.setTitle_(title)
            content.setBody_(body)
            req = UN.UNNotificationRequest.requestWithIdentifier_content_trigger_(uuid.uuid4().hex, content, None)
            center.addNotificationRequest_withCompletionHandler_(req, None)
            return
        except Exception:
            pass
        try:  # older systems, or notifications unavailable to this build
            subprocess.Popen(["osascript", "-e", "on run argv", "-e",
                              "display notification (item 1 of argv) with title (item 2 of argv)", "-e", "end run",
                              body[:180], title], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

    def on_event(self, ev: dict) -> None:
        if ev.get("type") == "notify":
            self.notify(ev.get("title", "oomi"), ev.get("body", ""), ev.get("kind", ""))
        elif ev.get("type") == "bot" and self.app is not None:
            self.set_badge(sum(b.unread for b in self.app.registry.bots.values()))

    # window look: black, dark title bar, no title text
    def style(self, window=None) -> None:
        try:
            import AppKit

            win = (window or self.window).native
            black = AppKit.NSColor.blackColor()
            dark = AppKit.NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameDarkAqua)
            AppKit.NSApp.setAppearance_(dark)
            win.setAppearance_(dark)
            win.setTitlebarAppearsTransparent_(True)
            win.setTitleVisibility_(AppKit.NSWindowTitleHidden)
            win.setBackgroundColor_(black)
            bar = win.contentView().superview().subviews().lastObject()
            if bar is not None and bar.respondsToSelector_("setBackgroundColor:"):
                bar.setBackgroundColor_(black)
        except Exception:
            pass  # cosmetic only
        from ..config import GHOST_COLORS, Config

        color = Config.load().ghost_color
        if color in GHOST_COLORS and color != "white":
            self.set_icon(color)

    def should_close(self) -> bool:
        """Runs on the main thread when the window closes or the app quits (⌘Q). False keeps it open."""
        if self.app is None:
            return True
        busy = [rt.record.name for rt in self.app.runtimes.values() if rt.status != "idle"]
        if busy:
            try:
                import AppKit

                alert = AppKit.NSAlert.alloc().init()
                alert.setMessageText_("Quit oomi?")
                names = ", ".join(busy[:3]) + ("…" if len(busy) > 3 else "")
                alert.setInformativeText_(f"{names} {'is' if len(busy) == 1 else 'are'} still working. "
                                          f"It stops now; everything so far is saved.")
                alert.addButtonWithTitle_("Quit")
                alert.addButtonWithTitle_("Cancel")
                if alert.runModal() != AppKit.NSAlertFirstButtonReturn:
                    return False
            except Exception:
                pass
        # ⌘Q ends the process without returning to run(), so wrap up here: hide the window, then stop the bots,
        # let them learn from today's conversation and save (a few seconds at most).
        try:
            self.window.native.orderOut_(None)
        except Exception:
            pass
        for win in list(self.windows.values()):  # pop-outs go with the main window
            try:
                win.destroy()
            except Exception:
                pass
        self.windows.clear()
        self.app.shutdown()
        return True


PREFERRED_PORT = 47821  # a steady address, so an app that wants an exact redirect address (Gmail) is set up once


def start_server(app: App, port: int = 0) -> Server:
    """On the port asked for, else the steady one, else any free port (when another oomi already has it)."""
    for p in [port] if port else [PREFERRED_PORT, 0]:
        try:
            return Server(app, p).start()
        except OSError:
            if port or p == 0:
                raise
    raise OSError("no free port")


def run(browser: bool = False, port: int = 0) -> None:
    from .. import __version__  # noqa: F401

    mac = sys.platform == "darwin" and not browser
    if mac and os.environ.get("OOMI_APP") == "1":
        import_login_path()
    native = None
    if mac:
        try:
            import webview  # noqa: F401
            native = MacNative()
        except ImportError:
            mac = False
    app = App(native=native)
    server = start_server(app, port)
    if not mac:
        print(f"oomi is running at {server.url}\nPress Ctrl-C to quit.", flush=True)
        webbrowser.open(server.url)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        print("Saving what oomi learned…", flush=True)
        app.shutdown()
        server.stop()
        return

    import webview

    native.app = app
    app.bus.listeners.append(native.on_event)
    window = webview.create_window("oomi", server.url, width=1200, height=800, min_size=(780, 520),
                                   background_color="#000000", text_select=True)
    native.window = window
    native.url = server.url
    window.events.before_show += lambda: native.style()
    window.events.closing += lambda: native.should_close()
    storage = app.home / "webview"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(storage))
    app.shutdown()  # already done in should_close, unless the window went away some other way
    server.stop()
