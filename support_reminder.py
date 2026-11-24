import json
from datetime import date, timedelta

import certstore

SETTINGS_FILE = "support-reminder.json"
DAYS_BETWEEN_REMINDERS = 14


def _stored():
    try:
        stored = json.loads((certstore.config_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _store(values):
    (certstore.config_dir() / SETTINGS_FILE).write_text(json.dumps(values), encoding="utf-8")


def _last_shown(stored):
    try:
        return date.fromisoformat(stored.get("last_shown"))
    except (TypeError, ValueError):
        return None


def offer(today=None):
    today = today or date.today()
    stored = _stored()
    if stored.get("hidden") is True:
        return False
    last_shown = _last_shown(stored)
    if last_shown and today - last_shown < timedelta(days=DAYS_BETWEEN_REMINDERS) and last_shown <= today:
        return False
    _store({**stored, "last_shown": today.isoformat()})
    return True


def hide():
    _store({**_stored(), "hidden": True})


def hidden():
    return _stored().get("hidden") is True


def show_again():
    _store({name: value for name, value in _stored().items() if name != "hidden"})
