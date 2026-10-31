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
SERVER_START_ATTEMPTS = 300
MAX_RECENT_FILES = 10


def server_command(args):
    if FROZEN:
        return [sys.executable, "--serve", *args]
    return [sys.executable, str(Path(__file__).with_name("app.py")), *args]


def is_print_file(path):
    path = Path(path).resolve()
    return (path.parent == Path(tempfile.gettempdir()).resolve() and path.name.startswith("zfo-tisk-")
            and path.suffix.lower() == ".pdf" and path.is_file())


def open_with_system(path):
    if IS_WINDOWS:
        os.startfile(path)  # noqa: S606 - a file the app itself wrote
    else:
        subprocess.Popen(["xdg-open", str(path)])


def reveal_in_file_manager(path):
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_with_system(path.parent)


def free_local_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Bridge:
    # pywebview exposes public attributes to the pages as window.pywebview.api, so everything else stays private.

    def __init__(self, app, form):
        self._app, self._form = app, form

    def _is_showing_form_server(self, window):
        return (window.get_current_url() or "").startswith(self._form.home)

    def _save_file_from_dialog(self, window, request):
        chosen = window.create_file_dialog(self._app.webview.FileDialog.SAVE, directory=request.get("folder") or "",
                                           save_filename=request["name"], file_types=("XML (*.xml)",))
        if chosen:
            target = chosen if isinstance(chosen, str) else chosen[0]
            Path(target).write_text(request["content"], encoding="utf-8")
            window.evaluate_js(f"window.zfoDataSaved && zfoDataSaved({json.dumps(str(target))})")

    def message(self, msg):
        action, window = msg.get("action"), self._form.window
        if not self._is_showing_form_server(window):
            return
        if action == "open":
            self._app.open_dialog(window)
        elif action == "openFile":
            self._app.open_window(Path(msg["path"]))
        elif action == "recent":
            window.evaluate_js(f"window.zfoRecent && zfoRecent({json.dumps(self._app.recent_files())})")
        elif action == "reveal":
            reveal_in_file_manager(Path(msg["path"]))
        elif action == "printPdf" and is_print_file(msg["path"]):
            open_with_system(msg["path"])
        elif action == "openUrl" and str(msg.get("url", "")).startswith(("https://", "http://")):
            webbrowser.open(msg["url"])
        elif action == "saveFile":
            self._save_file_from_dialog(window, msg)


class FormWindow:
    def __init__(self, app, path=None):
        self.path = path
        # The port is chosen here: a windowed Windows build has no stdout to report one on.
        port = free_local_port()
        self.server = self._start_server(port)
        self.home = f"http://127.0.0.1:{port}/"
        self.window = app.webview.create_window(path.name if path else "Blanketa", self.home,
                                                js_api=Bridge(app, self), width=1280, height=900, text_select=True)
        self.window.events.closed += self.stop_server
        self.window.events.loaded += self.return_to_form_server

    def _start_server(self, port):
        import certstore
        env = dict(os.environ, ZFO_DESKTOP="1", ZFO_WATCH_STDIN="1")
        args = (["--file", str(self.path)] if self.path else ["--empty"]) + [str(port)]
        log = certstore.config_dir() / "server.log"
        with open(log, "ab") as errors:
            server = subprocess.Popen(server_command(args), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                      stderr=errors, env=env, creationflags=NO_WINDOW)
        self._wait_for_server(server, port, log)
        return server

    @staticmethod
    def _wait_for_server(server, port, log):
        for _ in range(SERVER_START_ATTEMPTS):
            if server.poll() is not None:
                raise RuntimeError(f"Server formuláře se nespustil, podrobnosti jsou v {log}")
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)

    def return_to_form_server(self):
        if not (self.window.get_current_url() or self.home).startswith(self.home):
            self.window.load_url(self.home)

    def stop_server(self):
        if self.server.poll() is None:
            self.server.stdin.close()
            self.server.terminate()


class DesktopApp:
    def __init__(self):
        import webview
        self.webview = webview
        self.windows = []

    def _recent_files_path(self):
        import certstore
        return certstore.config_dir() / "recent.json"

    def recent_files(self):
        path = self._recent_files_path()
        remembered = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [file for file in remembered if Path(file).exists()][:MAX_RECENT_FILES]

    def _remember_recent_file(self, path):
        others = [file for file in self.recent_files() if file != str(path)]
        self._recent_files_path().write_text(json.dumps([str(path)] + others), encoding="utf-8")

    def open_window(self, path=None):
        path = path.resolve() if path else None
        empty_windows = [form for form in self.windows if form.path is None] if path else []
        form = FormWindow(self, path)
        self.windows.append(form)
        form.window.events.closed += lambda: self.windows.remove(form)
        if path:
            self._remember_recent_file(path)
        for empty_window in empty_windows:
            empty_window.window.destroy()

    def open_dialog(self, window):
        chosen = window.create_file_dialog(self.webview.FileDialog.OPEN, allow_multiple=True,
                                           file_types=("Formuláře ZFO a PDF (*.zfo;*.pdf)", "Všechny soubory (*.*)"))
        for path in chosen or []:
            self.open_window(Path(path))

    def _send_action_to_page(self, name):
        window = self.webview.active_window()
        if window:
            window.evaluate_js(f"window.zfoAction && zfoAction({json.dumps(name)})")

    def build_menu(self):
        from webview.menu import Menu, MenuAction, MenuSeparator

        def page_action(title, name):
            return MenuAction(title, lambda: self._send_action_to_page(name))

        return [
            Menu("Soubor", [
                MenuAction("Otevřít…", lambda: self.open_dialog(self.webview.active_window())),
                MenuSeparator(),
                page_action("Uložit bez podpisu (Ctrl+S)", "save-plain"),
                page_action("Podepsat a uložit… (Ctrl+Shift+S)", "save"),
                MenuSeparator(),
                page_action("Tisk… (Ctrl+P)", "print"),
            ]),
            Menu("Formulář", [
                page_action("Přepočítat hodnoty (F9)", "recalc"),
                page_action("Zkontrolovat formulář (Shift+F9)", "check"),
                MenuSeparator(),
                page_action("Obnovit výchozí hodnotu pole", "reset-field"),
                page_action("Obnovit výchozí hodnoty v celém formuláři…", "reset"),
                MenuSeparator(),
                page_action("Import dat do formuláře…", "import-data"),
                page_action("Uložit data do souboru…", "export-data"),
            ]),
            Menu("Zobrazení", [
                page_action("Najít… (Ctrl+F)", "find"),
                MenuSeparator(),
                page_action("Zvětšit (Ctrl++)", "zoom-in"),
                page_action("Zmenšit (Ctrl+-)", "zoom-out"),
                page_action("Přizpůsobit šířce okna", "zoom-fit"),
                MenuSeparator(),
                page_action("Nápověda k poli", "help"),
                page_action("Informace o formuláři a podpisech", "panel"),
            ]),
            Menu("Nápověda", [page_action("Podpořit Blanketu…", "support")]),
        ]

    def run(self, paths):
        import certstore
        for path in paths or [None]:
            self.open_window(path)
        self.webview.start(gui="qt" if sys.platform.startswith("linux") else None, menu=self.build_menu(), private_mode=False,
                           storage_path=str(certstore.config_dir() / "webview"))


def main(argv):
    if argv[:1] == ["--serve"]:
        import app
        app.main(argv[1:])
        return
    DesktopApp().run([Path(argument) for argument in argv if not argument.startswith("-")])


if __name__ == "__main__":
    main(sys.argv[1:])
