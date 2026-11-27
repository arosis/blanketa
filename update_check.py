import json
import re
import sys
import threading
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import certstore
import public_services
import update_install
import update_signature

SETTINGS_FILE = "update-check.json"
SECONDS_TO_WAIT = 5
DAYS_POSTPONED = 7
HOURS_BETWEEN_CHECKS = 24

LATEST_RELEASE_URL = "https://api.github.com/repos/arosis/blanketa/releases/latest"
RELEASES_URL = "https://github.com/arosis/blanketa/releases"
NEWS_URL = "https://blanketa.cz/novinky/#"
MAX_ANSWER_BYTES = 1_000_000
FAILED_FILE = "failed.json"

PLATFORM = "mac" if certstore.IS_MAC else "windows" if certstore.IS_WINDOWS else "linux"

_TAG = re.compile(r"v?([0-9]+)\.([0-9]+)\.([0-9]+)")
_background = threading.Lock()
_background_running = False
_window_considered = False


def version_tuple(text):
    match = _TAG.fullmatch(text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.groups()) if match else None


def portable_windows():
    if not (getattr(sys, "frozen", False) and certstore.IS_WINDOWS):
        return False
    return not (Path(sys.executable).parent / "unins000.exe").exists()


def _stored():
    try:
        stored = json.loads((certstore.config_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


def _store(values):
    certstore.replace_file(certstore.config_dir() / SETTINGS_FILE, json.dumps(values))


def _try_store(values):
    try:
        _store(values)
    except OSError:
        pass


def _moment(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def enabled():
    return _stored().get("disabled") is not True


def set_enabled(wanted):
    _store({**_stored(), "disabled": not wanted})


def fetch_latest():
    try:
        request = urllib.request.Request(LATEST_RELEASE_URL, headers={"User-Agent": "Blanketa"})
        with public_services.opener().open(request, timeout=SECONDS_TO_WAIT) as answer:
            release = json.loads(answer.read(MAX_ANSWER_BYTES))
        tag = release["tag_name"]
        if not isinstance(tag, str) or not tag.startswith("v") or version_tuple(tag) is None:
            return None
        assets = {
            asset["name"]: asset["browser_download_url"]
            for asset in release.get("assets", [])
            if isinstance(asset["name"], str) and isinstance(asset["browser_download_url"], str)
        }
        return {"version": tag[1:], "assets": assets}
    except Exception:
        return None


def _latest(stored):
    latest = stored.get("latest")
    if not isinstance(latest, dict) or version_tuple(latest.get("version")) is None:
        return None
    assets = latest.get("assets")
    return {"version": latest["version"], "assets": assets if isinstance(assets, dict) else {}}


def _asset_name(version):
    if PLATFORM == "mac":
        return f"Blanketa-{version}-macos.dmg"
    if PLATFORM == "windows":
        return f"Blanketa-{version}-windows-x64.zip" if portable_windows() else f"Blanketa-{version}-setup.exe"
    return None


def _release_asset(latest, name):
    url = latest["assets"].get(name)
    return url if url == f"{RELEASES_URL}/download/v{latest['version']}/{name}" else None


def _can_install(latest, target):
    if target is None:
        return False
    version = latest["version"]
    names = [
        update_signature.PLATFORM_FILES[update_install.KIND_FILES[target["kind"]]],
        update_signature.MANIFEST_NAME,
        update_signature.SIGNATURE_NAME,
    ]
    return all(_release_asset(latest, name.format(version=version)) for name in names)


def _read_failed():
    try:
        failed = json.loads((update_install.updates_dir() / FAILED_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    version = failed.get("version") if isinstance(failed, dict) else None
    return {"version": version} if version_tuple(version) is not None else None


def _failed_install():
    failed = _read_failed()
    try:
        (update_install.updates_dir() / FAILED_FILE).unlink()
    except OSError:
        pass
    if failed is not None:
        update_install.discard_version(failed["version"])
    return failed


def _offer(latest, current):
    version = latest["version"]
    name = _asset_name(version)
    install = _can_install(latest, update_install.target())
    return {
        "version": version,
        "current": current,
        "download_url": _release_asset(latest, name) or f"{RELEASES_URL}/tag/v{version}",
        "news_url": NEWS_URL + version,
        "linux": PLATFORM == "linux" and not install,
        "install": install,
        "reason": update_install.not_replaceable_reason(),
        "failed": _failed_install(),
    }


def _ready_offer(version, current):
    return {
        "version": version,
        "current": current,
        "download_url": f"{RELEASES_URL}/tag/v{version}",
        "news_url": NEWS_URL + version,
        "linux": False,
        "install": False,
        "reason": None,
        "failed": None,
    }


def _is_newer(latest, current):
    latest_version, current_version = version_tuple(latest["version"]), version_tuple(current)
    return current_version is not None and latest_version > current_version


def _postponed(stored, version, now):
    postponed = stored.get("postponed")
    if not isinstance(postponed, dict) or postponed.get("version") != version:
        return False
    until = _moment(postponed.get("until"))
    return until is not None and now < until <= now + timedelta(days=DAYS_POSTPONED)


def _postpone(stored, version, now):
    until = now + timedelta(days=DAYS_POSTPONED)
    _try_store({**stored, "postponed": {"version": version, "until": until.isoformat()}})


def _stale(stored, now):
    checked_at = _moment(stored.get("checked_at"))
    return checked_at is None or not checked_at <= now < checked_at + timedelta(hours=HOURS_BETWEEN_CHECKS)


def _remember(stored, latest, now):
    values = {**stored, "checked_at": now.isoformat()}
    if latest is not None:
        values["latest"] = latest
    _try_store(values)


def _refresh():
    global _background_running
    try:
        latest = fetch_latest()
        _remember(_stored(), latest, datetime.now())
    except Exception:
        pass
    finally:
        with _background:
            _background_running = False


def refresh_in_background():
    global _background_running
    with _background:
        if _background_running:
            return
        _background_running = True
    threading.Thread(target=_refresh, daemon=True).start()


def offer_for_page(current, now=None):
    global _window_considered
    first_render, _window_considered = not _window_considered, True
    try:
        now = now or datetime.now()
        stored = _stored()
        failed = _read_failed() if first_render else None
        if stored.get("disabled") is True:
            return _offer({"version": failed["version"], "assets": {}}, current) if failed else None
        if _stale(stored, now):
            refresh_in_background()
        latest = _latest(stored)
        if not first_render:
            return None
        if latest is None or not _is_newer(latest, current) or _postponed(stored, latest["version"], now):
            return _offer({"version": failed["version"], "assets": {}}, current) if failed else None
        _postpone(stored, latest["version"], now)
        return _offer(latest, current)
    except Exception:
        return None


def check_now(current, now=None):
    try:
        now = now or datetime.now()
        prepared = update_install.ready_for_this_app(current)
        if prepared is not None:
            return {"status": "newer", "ready": True, "offer": _ready_offer(prepared["version"], current)}
        latest = fetch_latest()
        if latest is None:
            return {"status": "unknown"}
        stored = _stored()
        _remember(stored, latest, now)
        if not _is_newer(latest, current):
            return {"status": "current"}
        _postpone(_stored(), latest["version"], now)
        return {"status": "newer", "offer": _offer(latest, current)}
    except Exception:
        return {"status": "unknown"}
