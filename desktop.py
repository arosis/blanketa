import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

FROZEN = hasattr(sys, "_MEIPASS")
IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SERVER_START_ATTEMPTS = 300
MAX_RECENT_FILES = 10
ATTACHMENT_URL = re.compile(r"/(?:attachment|container-file)\?s=[A-Za-z0-9_-]+&i=\d+")
ATTACHMENT_DOWNLOAD_SECONDS = 120
OPEN_DIALOG_FILE_TYPES = ("Formuláře PDF a kontejnery ASiC (*.zfo;*.fo;*.pdf;*.asice;*.sce;*.asics;*.scs)", "Všechny soubory (*.*)")


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


def print_pdf_file(path, window):
    if IS_WINDOWS:
        open_with_system(path)
        return
    import linux_print
    linux_print.run_on_gui_thread(lambda: show_print_preview_or_open(path, window))


def show_print_preview_or_open(path, window):
    import linux_print
    try:
        linux_print.show_print_preview(path, window.title, window.native)
    except Exception:  # whatever breaks the preview, the system viewer still prints the file
        open_with_system(path)


def reveal_in_file_manager(path):
    if IS_WINDOWS:
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_with_system(path.parent)


def attachment_url_is_allowed(url):
    return isinstance(url, str) and ATTACHMENT_URL.fullmatch(url) is not None


def is_plain_file_name(name):
    return isinstance(name, str) and name not in ("", ".", "..") and not any(character in name for character in "/\\:\0")


def free_target(folder, name):
    if not is_plain_file_name(name):
        raise ValueError(f"unsafe file name {name!r}")
    stem, suffix = Path(name).stem, Path(name).suffix
    candidate, copy_number = folder / name, 2
    while candidate.exists():
        candidate, copy_number = folder / f"{stem} ({copy_number}){suffix}", copy_number + 1
    return candidate


def write_downloaded(url, target):
    with urllib.request.urlopen(url, timeout=ATTACHMENT_DOWNLOAD_SECONDS) as response:  # noqa: S310 - only our own server
        partial = target.with_name(target.name + ".part")
        try:
            with open(partial, "wb") as file:
                while chunk := response.read(1 << 20):
                    file.write(chunk)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)


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

    def _ask_for_save_target(self, window, folder, name, file_types):
        chosen = window.create_file_dialog(self._app.webview.FileDialog.SAVE, directory=folder, save_filename=name,
                                           file_types=file_types)
        return Path(chosen if isinstance(chosen, str) else chosen[0]) if chosen else None

    def _save_file_from_dialog(self, window, request):
        contents = request.get("contents") or {}
        default_extension = Path(request["name"]).suffix.lstrip(".").lower()
        extensions = [default_extension, *sorted(set(contents) - {default_extension})] if contents else ["xml"]
        file_types = tuple(f"{extension.upper()} (*.{extension})" for extension in extensions)
        folder, name = request.get("folder") or "", request["name"]
        while (target := self._ask_for_save_target(window, folder, name, file_types)) is not None:
            if not contents or target.suffix.lstrip(".").lower() in contents:
                break
            completed = target.with_name(f"{target.name}.{default_extension}")
            if not completed.exists():
                target = completed
                break
            # The dialog only asked about the name without the extension; asked again, it asks before replacing.
            folder, name = str(completed.parent), completed.name
        if target is None:
            return
        target.write_text(contents.get(target.suffix.lstrip(".").lower(), request["content"]), encoding="utf-8")
        window.evaluate_js(f"window.zfoDataSaved && zfoDataSaved({json.dumps(str(target))})")

    def _save_attachments(self, window, request):
        files = request.get("files")
        if not isinstance(files, list) or not files or not all(
                isinstance(file, dict) and attachment_url_is_allowed(file.get("url")) and is_plain_file_name(file.get("name"))
                for file in files):
            return
        open_saved = request.get("open") is True
        if open_saved and (request.get("folder") or len(files) != 1):
            return
        server = self._form.home.rstrip("/")
        try:
            saved = self._save_downloads(window, server, files, request.get("folder"))
        except OSError as error:
            window.evaluate_js(f"window.zfoAttachmentFailed && zfoAttachmentFailed({json.dumps(str(error))})")
            return
        if saved is not None:
            window.evaluate_js(f"window.zfoAttachmentSaved && zfoAttachmentSaved({json.dumps(str(saved))})")
            if open_saved:
                self._app.open_window(saved)

    def _save_downloads(self, window, server, files, into_folder):
        if into_folder:
            chosen = window.create_file_dialog(self._app.webview.FileDialog.FOLDER)
            if not chosen:
                return None
            folder = Path(chosen if isinstance(chosen, str) else chosen[0])
            for file in files:
                write_downloaded(server + file["url"], free_target(folder, file["name"]))
            return folder
        saved = self._ask_for_save_target(window, "", files[0]["name"], ())
        if saved is not None:
            write_downloaded(server + files[0]["url"], saved)
        return saved

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
            print_pdf_file(Path(msg["path"]), window)
        elif action == "openUrl" and str(msg.get("url", "")).startswith(("https://", "http://")):
            webbrowser.open(msg["url"])
        elif action == "saveFile":
            self._save_file_from_dialog(window, msg)
        elif action == "saveAttachment":
            self._save_attachments(window, msg)


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
                                           file_types=OPEN_DIALOG_FILE_TYPES)
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
