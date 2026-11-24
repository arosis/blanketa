import json
import traceback
from datetime import date
from pathlib import Path

import certstore

SETTINGS_FILE = "crash-report.json"
PROJECT_ROOT = Path(__file__).resolve().parent
MAX_FRAMES = 8


def settings_path():
    return certstore.config_dir() / SETTINGS_FILE


def _stored():
    try:
        stored = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _store(values):
    certstore.replace_file(settings_path(), json.dumps(values, ensure_ascii=False))


def enabled():
    return _stored().get("disabled") is not True


def set_enabled(wanted):
    stored = {**_stored(), "disabled": not wanted}
    if not wanted:
        stored.pop("pending", None)
    _store(stored)


def _where(file_name):
    path = Path(file_name)
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except (OSError, ValueError):
        pass
    if "site-packages" in path.parts:
        return "/".join(path.parts[path.parts.index("site-packages") + 1:])
    return path.name


def _frames(error):
    frames = traceback.extract_tb(error.__traceback__)[-MAX_FRAMES:]
    return [f"{_where(frame.filename)}:{frame.lineno} {frame.name}" for frame in frames]


def _record(pending):
    stored = _stored()
    if stored.get("disabled") is True:
        return
    try:
        _store({**stored, "pending": {**pending, "date": date.today().isoformat()}})
    except OSError:
        pass


def record_error(error, request):
    _record({"kind": "error", "request": request, "error": type(error).__name__, "frames": _frames(error)})


def record_server_exit(exit_code):
    _record({"kind": "server-exit", "exit_code": exit_code})


def _description(pending):
    if pending.get("kind") == "server-exit":
        return [f"Server okna nečekaně skončil (kód {pending.get('exit_code')}, {pending.get('date')})."]
    lines = [f"Chyba serveru: {pending.get('error')} při {pending.get('request')} ({pending.get('date')})"]
    return lines + [f"  {frame}" for frame in pending.get("frames", [])]


def take_pending():
    stored = _stored()
    pending = stored.pop("pending", None)
    if not isinstance(pending, dict) or stored.get("disabled") is True:
        return None
    try:
        _store(stored)
    except OSError:
        return None
    return "\n".join(str(line) for line in _description(pending))
