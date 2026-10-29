"""Blanketa for Windows and Linux: the pages of the macOS app in pywebview windows (WebView2 on Windows,
Qt WebEngine on Linux). As in the macOS app, every window runs its own server process (app.py) for its file.

    blanketa [FILE ...]          opens the forms, or one empty window
    blanketa --serve ARGS ...    the server of one window (app.main), started by the windows themselves
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import webbrowser
from pathlib import Path

FROZEN = hasattr(sys, "_MEIPASS")
IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def server_command(args):
    if FROZEN:
        return [sys.executable, "--serve", *args]
    return [sys.executable, str(Path(__file__).with_name("app.py")), *args]


def is_print_file(path):
    """A PDF that app.print_pdf wrote for printing; nothing else is handed to the system's default program."""
    path = Path(path).resolve()
    return (path.parent == Path(tempfile.gettempdir()).resolve() and path.name.startswith("zfo-tisk-")
            and path.suffix.lower() == ".pdf" and path.is_file())


def open_with_system(path):
    """Hands a file or folder to the system (default PDF viewer for printing, file manager)."""
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - a file the app itself wrote
    else:
        subprocess.Popen(["xdg-open", str(path)])


class Bridge:
    """What the pages reach as window.pywebview.api (see ui/bridge.js). Private attributes stay hidden from them."""

    def __init__(self, app, form):
        self._app, self._form = app, form

    def message(self, msg):
        action, window = msg.get("action"), self._form.window
        # Only the form server's own pages may drive the app (see FormWindow.keep_home).
        if not (window.get_current_url() or "").startswith(self._form.home):
            return
        if action == "open":
            self._app.open_dialog(window)
        elif action == "openFile":
            self._app.open(Path(msg["path"]))
        elif action == "recent":
            window.evaluate_js(f"window.zfoRecent && zfoRecent({json.dumps(self._app.recent())})")
        elif action == "reveal":
            path = Path(msg["path"])
            if IS_WINDOWS:
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                open_with_system(path.parent)
        elif action == "printPdf" and is_print_file(msg["path"]):
            open_with_system(msg["path"])
        elif action == "openUrl" and str(msg.get("url", "")).startswith(("https://", "http://")):
            webbrowser.open(msg["url"])
        elif action == "saveFile":
            chosen = window.create_file_dialog(self._app.webview.FileDialog.SAVE, directory=msg.get("folder") or "",
                                               save_filename=msg["name"], file_types=("XML (*.xml)",))
            if chosen:
                target = chosen if isinstance(chosen, str) else chosen[0]
                Path(target).write_text(msg["content"], encoding="utf-8")
                window.evaluate_js(f"window.zfoDataSaved && zfoDataSaved({json.dumps(str(target))})")


class FormWindow:
    def __init__(self, app, path=None):
        self.path = path
        env = dict(os.environ, ZFO_DESKTOP="1", ZFO_WATCH_STDIN="1")
        # The port is chosen here: a windowed Windows build has no stdout to report one on.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        args = (["--file", str(path)] if path else ["--empty"]) + [str(port)]
        import certstore
        log = certstore.config_dir() / "server.log"
        with open(log, "ab") as errors:
            self.server = subprocess.Popen(server_command(args), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                           stderr=errors, env=env, creationflags=NO_WINDOW)
        for _ in range(300):
            if self.server.poll() is not None:
                raise RuntimeError(f"Server formuláře se nespustil, podrobnosti jsou v {log}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        self.home = f"http://127.0.0.1:{port}/"
        self.window = app.webview.create_window(path.name if path else "Blanketa", self.home,
                                                js_api=Bridge(app, self), width=1280, height=900, text_select=True)
        self.window.events.closed += self.close
        self.window.events.loaded += self.keep_home

    def keep_home(self):
        """The pages turn links into the browser; should anything still take the window elsewhere, it comes back."""
        if not (self.window.get_current_url() or self.home).startswith(self.home):
            self.window.load_url(self.home)

    def close(self):
        if self.server.poll() is None:
            self.server.stdin.close()
            self.server.terminate()


class DesktopApp:
    def __init__(self):
        import webview
        self.webview = webview
        self.windows = []

    # Recently opened files, newest first, as in the macOS app.
    def _recent_path(self):
        import certstore
        return certstore.config_dir() / "recent.json"

    def recent(self):
        path = self._recent_path()
        found = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [p for p in found if Path(p).exists()][:10]

    def _remember(self, path):
        self._recent_path().write_text(json.dumps([str(path)] + [p for p in self.recent() if p != str(path)]), encoding="utf-8")

    def open(self, path=None):
        path = path.resolve() if path else None
        empty = [w for w in self.windows if w.path is None] if path else []
        form = FormWindow(self, path)
        self.windows.append(form)
        form.window.events.closed += lambda: self.windows.remove(form)
        if path:
            self._remember(path)
        for window in empty:
            window.window.destroy()

    def open_dialog(self, window):
        chosen = window.create_file_dialog(self.webview.FileDialog.OPEN, allow_multiple=True,
                                           file_types=("Formuláře ZFO a PDF (*.zfo;*.pdf)", "Všechny soubory (*.*)"))
        for path in chosen or []:
            self.open(Path(path))

    def _action(self, name):
        window = self.webview.active_window()
        if window:
            window.evaluate_js(f"window.zfoAction && zfoAction({json.dumps(name)})")

    def menu(self):
        from webview.menu import Menu, MenuAction, MenuSeparator
        act = lambda title, name: MenuAction(title, lambda: self._action(name))  # noqa: E731
        return [
            Menu("Soubor", [
                MenuAction("Otevřít…", lambda: self.open_dialog(self.webview.active_window())),
                MenuSeparator(),
                act("Uložit bez podpisu (Ctrl+S)", "save-plain"),
                act("Podepsat a uložit… (Ctrl+Shift+S)", "save"),
                MenuSeparator(),
                act("Tisk… (Ctrl+P)", "print"),
            ]),
            Menu("Formulář", [
                act("Přepočítat hodnoty (F9)", "recalc"),
                act("Zkontrolovat formulář (Shift+F9)", "check"),
                MenuSeparator(),
                act("Obnovit výchozí hodnotu pole", "reset-field"),
                act("Obnovit výchozí hodnoty v celém formuláři…", "reset"),
                MenuSeparator(),
                act("Import dat do formuláře…", "import-data"),
                act("Uložit data do souboru…", "export-data"),
            ]),
            Menu("Zobrazení", [
                act("Najít… (Ctrl+F)", "find"),
                MenuSeparator(),
                act("Zvětšit (Ctrl++)", "zoom-in"),
                act("Zmenšit (Ctrl+-)", "zoom-out"),
                act("Přizpůsobit šířce okna", "zoom-fit"),
                MenuSeparator(),
                act("Nápověda k poli", "help"),
                act("Informace o formuláři a podpisech", "panel"),
            ]),
            Menu("Nápověda", [act("Podpořit Blanketu…", "support")]),
        ]

    def run(self, paths):
        import certstore
        for path in paths or [None]:
            self.open(path)
        self.webview.start(gui="qt" if sys.platform.startswith("linux") else None, menu=self.menu(), private_mode=False,
                           storage_path=str(certstore.config_dir() / "webview"))


def main(argv):
    if argv[:1] == ["--serve"]:
        import app
        app.main(argv[1:])
        return
    DesktopApp().run([Path(a) for a in argv if not a.startswith("-")])


if __name__ == "__main__":
    main(sys.argv[1:])
