import platform
import sys
from urllib.parse import quote

ADDRESS = "podpora@blanketa.cz"


def system_description():
    machine = platform.machine()
    if sys.platform == "darwin":
        return f"macOS {platform.mac_ver()[0]} ({machine})"
    if sys.platform == "win32":
        release, build = platform.release(), platform.version().rsplit(".", 1)[-1]
        if release == "10" and build.isdigit() and int(build) >= 22000:
            release = "11"
        return f"Windows {release} (build {build}, {machine})"
    try:
        name = platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
    except OSError:
        name = "Linux"
    return f"{name} ({machine})"


def mail_link(subject, body):
    if not isinstance(subject, str) or not isinstance(body, str):
        return None
    return f"mailto:{ADDRESS}?subject={quote(subject, safe='')}&body={quote(body, safe='')}"
