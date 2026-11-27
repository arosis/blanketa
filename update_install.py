import contextlib
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import urlsplit

import certstore
import public_services
import update_check
import update_signature

KIND_FILES = {
    "mac-app": "mac",
    "windows-setup": "windows",
    "windows-portable": "windows-portable",
    "linux-folder": "linux",
}
APP_FOLDERS = {"mac-app": "Blanketa.app", "windows-portable": "Blanketa", "linux-folder": "Blanketa"}
APP_PROGRAMS = {"mac-app": "Contents/MacOS/Blanketa", "windows-portable": "Blanketa.exe", "linux-folder": "Blanketa"}
STAGING_FOLDERS = {"mac-app": ".Blanketa-update-{version}"}
OTHER_STAGING_FOLDER = ".blanketa-update-{version}"

READY_FILE = "ready.json"
LOCK_FILE = "download.lock"
INSTALL_LOCK_FILE = "install.lock"
RUNNING_FOLDER = "running"
HELPER_SCRIPTS = {"mac-app": "swap.sh", "linux-folder": "swap.sh", "windows-setup": "swap.ps1", "windows-portable": "swap.ps1"}
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
SECONDS_PER_READ = 30
SECONDS_TO_WRITE_A_LOCK = 10
SECONDS_TO_WAIT_FOR_CANCEL = SECONDS_PER_READ + 5
BYTES_PER_READ = 1 << 16
MAX_DOWNLOAD_BYTES = 1 << 30
MAX_REDIRECTS = 3
RELEASE_ASSET_HOSTS = ("release-assets.githubusercontent.com", "objects.githubusercontent.com")
READY_FIELDS = {"version", "kind", "app", "staged", "all_users"}

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
ERROR_ACCESS_DENIED = 5
STILL_ACTIVE = 259

IDLE = {"state": "idle", "version": None, "percent": None, "error": None}
LOCK_BEING_WRITTEN = {"pid": None, "version": None, "percent": None}

_RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_STAGING_FOLDER = re.compile(r"\.[Bb]lanketa-update-[0-9]+\.[0-9]+\.[0-9]+")
_guard = threading.Lock()
_state = dict(IDLE)
_worker = None
_cancelled = threading.Event()


class _Refused(ValueError):
    pass


class _Cancelled(Exception):
    pass


class _ReleaseAssetRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        if not _is_release_asset_host(new_url):
            raise urllib.error.HTTPError(request.full_url, code, "Redirect leaves the release asset hosts", headers, fp)
        return super().redirect_request(request, fp, code, msg, headers, new_url)


def _is_release_asset_host(url):
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.netloc in RELEASE_ASSET_HOSTS


def updates_dir():
    return certstore.config_dir() / "updates"


def _mac_app():
    parents = Path(sys.executable).parents
    if len(parents) < 4:
        return None, False
    app = parents[3]
    path = str(app)
    replaceable = (
        app.suffix == ".app"
        and "/AppTranslocation/" not in path
        and not path.startswith("/Volumes/")
        and os.access(app.parent, os.W_OK)
    )
    return app, replaceable


def _inside(path, folder):
    return folder == path or folder in path.parents


def _windows_target():
    folder = Path(sys.executable).parent
    if update_check.portable_windows():
        return {"kind": "windows-portable", "app": folder, "all_users": False}
    program_files = os.environ.get("ProgramFiles")
    all_users = bool(program_files) and _inside(folder, Path(program_files))
    return {"kind": "windows-setup", "app": folder, "all_users": all_users}


def _linux_target():
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    folder = Path(sys.executable).parent
    if folder != data_home / "blanketa" or not os.access(folder, os.W_OK):
        return None
    return {"kind": "linux-folder", "app": folder, "all_users": False}


def target():
    if not getattr(sys, "frozen", False):
        return None
    if certstore.IS_MAC:
        app, replaceable = _mac_app()
        return {"kind": "mac-app", "app": app, "all_users": False} if replaceable else None
    if certstore.IS_WINDOWS:
        return _windows_target()
    return _linux_target()


def not_replaceable_reason():
    if getattr(sys, "frozen", False) and certstore.IS_MAC and not _mac_app()[1]:
        return "move-to-applications"
    return None


def start_download(current):
    global _worker
    with _guard:
        if _worker is not None and _worker.is_alive():
            return dict(_state)
        latest = update_check._latest(update_check._stored())
        if latest is None or not update_check._is_newer(latest, current):
            _state.update(IDLE)
            return dict(_state)
        plan = _plan(latest)
        if plan is None:
            _state.update(IDLE, state="failed", error="download")
            return dict(_state)
        found = ready(current)
        if found is not None and _same_update(found, plan):
            return _ready_progress(found)
        holder = _other_holder()
        if holder is not None:
            return _holder_progress(holder)
        try:
            is_locked = _take_lock(plan["version"])
        except OSError:
            _state.update(IDLE, state="failed", version=plan["version"], error="download")
            return dict(_state)
        if not is_locked:
            return _holder_progress(_other_holder() or {})
        _remove_leftovers(plan["target"]["app"])
        _cancelled.clear()
        _state.update(IDLE, state="downloading", version=plan["version"])
        _worker = threading.Thread(target=_download_in_background, args=(plan,), daemon=True)
        _worker.start()
        return dict(_state)


def progress(current):
    with _guard:
        local = dict(_state)
    if local["state"] == "downloading":
        return local
    holder = _other_holder()
    if holder is not None:
        return _holder_progress(holder)
    found = ready(current)
    if found is not None:
        return _ready_progress(found)
    return local if local["state"] == "failed" else dict(IDLE)


def cancel(current):
    with _guard:
        worker = _worker
    if worker is not None and worker.is_alive():
        _cancelled.set()
        worker.join(SECONDS_TO_WAIT_FOR_CANCEL)
    return progress(current)


def ready(current):
    path = updates_dir() / READY_FILE
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        record = None
    if not _is_usable_ready_record(record, current):
        _remove(path)
        return None
    return record


def _is_usable_ready_record(record, current):
    if not isinstance(record, dict) or record.get("kind") not in KIND_FILES:
        return False
    fields = READY_FIELDS | {"sha256"} if record["kind"] == "windows-setup" else READY_FIELDS
    version, running = update_check.version_tuple(record.get("version")), update_check.version_tuple(current)
    return (
        set(record) == fields
        and isinstance(record["version"], str) and _RELEASE_VERSION.fullmatch(record["version"]) is not None
        and running is not None and version > running
        and isinstance(record["app"], str) and isinstance(record["staged"], str) and Path(record["staged"]).exists()
        and isinstance(record["all_users"], bool)
        and isinstance(record.get("sha256", ""), str)
    )


def _same_update(record, plan):
    destination = plan["target"]
    return (record["version"], record["kind"], record["app"]) == (plan["version"], destination["kind"], str(destination["app"]))


def _ready_progress(record):
    return {"state": "ready", "version": record["version"], "percent": 100, "error": None}


def _holder_progress(holder):
    return {"state": "downloading", "version": holder.get("version"), "percent": holder.get("percent"), "error": None}


def _plan(latest):
    destination = target()
    if destination is None:
        return None
    version = latest["version"]
    if not _RELEASE_VERSION.fullmatch(version):
        return None
    platform = KIND_FILES[destination["kind"]]
    names = {
        "manifest": update_signature.MANIFEST_NAME.format(version=version),
        "signature": update_signature.SIGNATURE_NAME.format(version=version),
        "file": update_signature.PLATFORM_FILES[platform].format(version=version),
    }
    urls = {part: update_check._release_asset(latest, name) for part, name in names.items()}
    if not all(urls.values()):
        return None
    return {"version": version, "platform": platform, "target": destination, "file_name": names["file"], "urls": urls}


def _download_in_background(plan):
    outcome = {**IDLE, "state": "failed", "version": plan["version"], "error": "download"}
    try:
        outcome = _outcome(plan)
    finally:
        with _guard:
            _state.update(outcome)
        _release_lock()


def _outcome(plan):
    try:
        record = _download_verify_and_stage(plan)
        certstore.replace_file(updates_dir() / READY_FILE, json.dumps(record))
        return _ready_progress(record)
    except _Cancelled:
        _discard(plan)
        return dict(IDLE)
    except _Refused:
        _discard(plan)
        return {**IDLE, "state": "failed", "version": plan["version"], "error": "verify"}
    except Exception:
        _discard(plan)
        return {**IDLE, "state": "failed", "version": plan["version"], "error": "download"}


def _download_verify_and_stage(plan):
    manifest = _download_small(plan["urls"]["manifest"])
    signature = _download_small(plan["urls"]["signature"])
    try:
        verified = update_signature.verified_manifest(manifest, signature.decode("ascii"))
    except ValueError as error:
        raise _Refused("Manifest is not verified") from error
    if verified["version"] != plan["version"]:
        raise _Refused("Manifest is for another version")
    entry = verified["files"].get(plan["file_name"])
    if entry is None or entry["platform"] != plan["platform"]:
        raise _Refused("Manifest does not list this platform's file")
    folder = updates_dir() / plan["version"]
    folder.mkdir(parents=True, exist_ok=True)
    downloaded = _download_verified(plan["urls"]["file"], folder / plan["file_name"], entry["sha256"])
    destination = plan["target"]
    staged = _stage(destination["kind"], downloaded, _staging_folder(destination, plan["version"]))
    if _cancelled.is_set():
        raise _Cancelled
    record = {"version": plan["version"], "kind": destination["kind"], "app": str(destination["app"]),
              "staged": str(staged), "all_users": destination["all_users"]}
    if destination["kind"] == "windows-setup":
        record["sha256"] = entry["sha256"]
    return record


def _update_opener():
    return urllib.request.build_opener(_ReleaseAssetRedirects(), urllib.request.HTTPSHandler(context=public_services.tls_context()))


def _open(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Blanketa"})
    return _update_opener().open(request, timeout=SECONDS_PER_READ)


def _download_small(url):
    with _open(url) as answer:
        body = answer.read(update_check.MAX_ANSWER_BYTES + 1)
    if len(body) > update_check.MAX_ANSWER_BYTES:
        raise _Refused("Release file is too large")
    return body


def _declared_length(answer):
    try:
        return int(answer.headers.get("Content-Length"))
    except (TypeError, ValueError):
        return None


def _download_verified(url, path, sha256):
    partial = path.with_name(path.name + ".part")
    received = 0
    with _open(url) as answer, partial.open("wb") as stream:
        length = _declared_length(answer)
        if length is not None and not 0 <= length <= MAX_DOWNLOAD_BYTES:
            raise OSError("Download has an impossible length")
        while chunk := answer.read(BYTES_PER_READ):
            if _cancelled.is_set():
                raise _Cancelled
            received += len(chunk)
            if received > MAX_DOWNLOAD_BYTES:
                raise OSError("Download is too large")
            stream.write(chunk)
            _report_percent(received, length)
    if _cancelled.is_set():
        raise _Cancelled
    if length is not None and received != length:
        raise OSError("Download ended early")
    if update_signature.file_digest(partial) != sha256:
        raise _Refused("Downloaded file has another digest")
    os.replace(partial, path)
    return path


def _report_percent(received, length):
    percent = min(100, received * 100 // length) if length else None
    with _guard:
        if _state["percent"] == percent:
            return
        _state["percent"] = percent
        version = _state["version"]
    with contextlib.suppress(OSError):
        certstore.replace_file(updates_dir() / LOCK_FILE, _lock_text(version, percent))


def _staging_folder(destination, version):
    name = STAGING_FOLDERS.get(destination["kind"], OTHER_STAGING_FOLDER)
    return Path(destination["app"]).parent / name.format(version=version)


def _stage(kind, downloaded, staging):
    if kind == "windows-setup":
        return downloaded
    _remove(staging)
    staging.mkdir()
    try:
        _unpack(kind, downloaded, staging)
    except (tarfile.TarError, zipfile.BadZipFile, subprocess.CalledProcessError, UnicodeDecodeError) as error:
        raise _Refused("Archive cannot be unpacked safely") from error
    refuse_links_out_of(staging)
    staged = staging / APP_FOLDERS[kind]
    if not (staged / APP_PROGRAMS[kind]).is_file():
        raise _Refused("Archive does not contain the app")
    return staged


def _unpack(kind, archive, staging):
    if kind == "linux-folder":
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                _refuse_escaping_name(member.name)
            bundle.extractall(staging, filter="data")
        return
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            _refuse_escaping_name(member.filename)
            if stat.S_ISLNK(member.external_attr >> 16):
                _refuse_escaping_link(member.filename, bundle.read(member).decode("utf-8"))
        if kind == "mac-app":
            subprocess.run(["ditto", "-x", "-k", str(archive), str(staging)], check=True, capture_output=True)
        else:
            bundle.extractall(staging)


def _refuse_escaping_name(name):
    parts = re.split(r"[\\/]", name)
    if PurePosixPath(name).is_absolute() or PureWindowsPath(name).anchor or ".." in parts:
        raise _Refused(f"Archive entry leaves its folder: {name}")


def _refuse_escaping_link(name, link):
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
    if posixpath.isabs(link) or resolved == ".." or resolved.startswith("../"):
        raise _Refused(f"Archive link leaves its folder: {name}")


def refuse_links_out_of(folder):
    root = os.path.realpath(folder)
    for parent, folders, files in os.walk(folder):
        for name in folders + files:
            path = os.path.join(parent, name)
            if os.path.islink(path) and not _inside(Path(os.path.realpath(path)), Path(root)):
                raise _Refused(f"Link leaves the staged folder: {path}")


def _discard(plan):
    _remove(updates_dir() / plan["version"])
    _remove(_staging_folder(plan["target"], plan["version"]))
    _remove(updates_dir() / READY_FILE)


def _remove_leftovers(app):
    for folder, pattern in ((updates_dir(), _RELEASE_VERSION), (Path(app).parent, _STAGING_FOLDER)):
        with contextlib.suppress(OSError):
            for path in folder.iterdir():
                if pattern.fullmatch(path.name):
                    _remove(path)
    _remove(updates_dir() / READY_FILE)


def _remove(path):
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
        return
    with contextlib.suppress(OSError):
        path.unlink()


def running_version():
    return (certstore.RESOURCES / "VERSION").read_text(encoding="utf-8").strip()


def register_running():
    folder = updates_dir() / RUNNING_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / str(os.getpid())).touch()


def unregister_running():
    _remove(updates_dir() / RUNNING_FOLDER / str(os.getpid()))


def others_running():
    return _running_besides({os.getpid()})


def _running_besides(own):
    try:
        entries = list((updates_dir() / RUNNING_FOLDER).iterdir())
    except OSError:
        return False
    for entry in entries:
        if not (entry.name.isascii() and entry.name.isdigit()) or int(entry.name) in own:
            continue
        if process_alive(int(entry.name)):
            return True
        _remove(entry)
    return False


def helper_script(kind):
    name = HELPER_SCRIPTS[kind]
    if not getattr(sys, "frozen", False):
        return certstore.ROOT / "packaging/update" / name
    if certstore.IS_MAC:
        return Path(sys.executable).parents[1] / "update" / name
    return certstore.RESOURCES / "update" / name


def start_pending_install(host_pid, relaunch, files):
    try:
        record = ready(running_version())
    except OSError:
        return False
    if record is None or not _matches_this_app(record):
        return False
    lock = updates_dir() / INSTALL_LOCK_FILE
    try:
        if not _claim(INSTALL_LOCK_FILE, json.dumps({"pid": host_pid})):
            return False
    except OSError:
        return False
    if _running_besides({os.getpid(), host_pid}):
        _remove(lock)
        return False
    try:
        script = updates_dir() / HELPER_SCRIPTS[record["kind"]]
        shutil.copyfile(helper_script(record["kind"]), script)
        swap = _start_detached(_install_command(record, script, host_pid, relaunch, files))
    except OSError:
        _remove(lock)
        return False
    with contextlib.suppress(OSError):
        certstore.replace_file(lock, json.dumps({"pid": swap.pid}))
    return True


def _matches_this_app(record):
    destination = target()
    if destination is None or (record["kind"], record["app"]) != (destination["kind"], str(destination["app"])):
        return False
    if record["kind"] == "windows-setup":
        allowed, digest = updates_dir(), record["sha256"]
        if _SHA256.fullmatch(digest) is None:
            return False
    else:
        allowed = Path(record["app"]).parent
        expected = _staging_folder(destination, record["version"]) / APP_FOLDERS[record["kind"]]
        if Path(record["staged"]) != expected:
            return False
    staged = Path(os.path.realpath(record["staged"]))
    return Path(os.path.realpath(allowed)) in staged.parents


def _install_command(record, script, host_pid, relaunch, files):
    files = [file for file in files if isinstance(file, str) and os.path.isabs(file)]
    if not record["kind"].startswith("windows"):
        return ["/bin/sh", str(script), str(host_pid), record["staged"], record["app"], str(updates_dir()),
                record["version"], "1" if relaunch else "0", *files]
    powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    command = [str(powershell), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
               "-WaitPid", str(host_pid), "-Kind", record["kind"], "-Staged", record["staged"], "-Target", record["app"],
               "-UpdatesDir", str(updates_dir()), "-Version", record["version"]]
    if record["kind"] == "windows-setup":
        command += ["-Sha256", record["sha256"]]
    if record["all_users"]:
        command.append("-AllUsers")
    if relaunch:
        command.append("-Relaunch")
    return command + files


def _start_detached(command):
    streams = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "cwd": updates_dir()}
    if certstore.IS_WINDOWS:
        return subprocess.Popen(command, creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, **streams)
    return subprocess.Popen(command, start_new_session=True, **streams)


def _lock_text(version, percent):
    return json.dumps({"pid": os.getpid(), "version": version, "percent": percent})


def _take_lock(version):
    return _claim(LOCK_FILE, _lock_text(version, None))


def _claim(name, text):
    lock = updates_dir() / name
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if _other_holder(name) is not None:
                return False
            _remove(lock)
            continue
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        return True
    return False


def _read_holder(name=LOCK_FILE):
    lock = updates_dir() / name
    try:
        content = lock.read_bytes()
        age = time.time() - lock.stat().st_mtime
    except (FileNotFoundError, NotADirectoryError):
        return None, None
    except OSError:
        return dict(LOCK_BEING_WRITTEN), 0
    try:
        holder = json.loads(content)
    except ValueError:
        holder = None
    is_valid = (
        isinstance(holder, dict) and type(holder.get("pid")) is int and holder["pid"] > 0
        and isinstance(holder.get("version"), (str, type(None)))
        and type(holder.get("percent")) in (int, type(None))
    )
    return (holder if is_valid else None), age


def _other_holder(name=LOCK_FILE):
    holder, age = _read_holder(name)
    if holder is None:
        is_being_written = age is not None and age < SECONDS_TO_WRITE_A_LOCK
        return dict(LOCK_BEING_WRITTEN) if is_being_written else None
    if holder["pid"] is None:
        return holder
    if holder["pid"] == os.getpid() or not process_alive(holder["pid"]):
        return None
    return holder


def _release_lock():
    holder, _ = _read_holder()
    if holder is not None and holder["pid"] == os.getpid():
        _remove(updates_dir() / LOCK_FILE)


def process_alive(pid):
    if certstore.IS_WINDOWS:
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _windows_process_alive(pid):
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)
