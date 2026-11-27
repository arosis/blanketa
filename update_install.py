import os
import sys
from pathlib import Path

import certstore

KIND_FILES = {
    "mac-app": "mac",
    "windows-setup": "windows",
    "windows-portable": "windows-portable",
    "linux-folder": "linux",
}


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
    import update_check

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
