# Lookups a form asks of a public register (zfoengine.form_scripts.known_lookup), made by this server after the user
# confirmed them on the page. The address is checked against that list again here: the page only passes it on.
import os
import urllib.error
import urllib.request

import public_services
from zfoengine import form_scripts

TIMEOUT_SECONDS = 8
MAX_ANSWER_BYTES = 1024 * 1024
JSON_CONTENT_TYPE = "application/json"
UNAVAILABLE = "Registr ARES teď není dostupný. Údaje vyplňte ručně."


def _register_address(lookup):
    # Rebuilt from this program's own addresses: of what the page sent, only the choice of register and the digits remain.
    return form_scripts.ARES_REGISTERS[lookup["register"]] + f"{int(lookup['number']):08d}"


def ask(url):
    lookup = form_scripts.known_lookup(url)
    if lookup is None:
        raise ValueError("unknown lookup")
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    request = urllib.request.Request(_register_address(lookup), headers={"User-Agent": "Blanketa", "Accept": JSON_CONTENT_TYPE})
    try:
        with public_services.opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.headers.get_content_type() != JSON_CONTENT_TYPE:  # a proxy's or a Wi-Fi sign-in page, not the register
                raise OSError("not an answer of the register")
            status, data = response.status, response.read(MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as error:
        status, data = error.code, error.read(MAX_ANSWER_BYTES + 1)
    if len(data) > MAX_ANSWER_BYTES:
        raise OSError("answer too large")
    return {"status": status, "body": data.decode("utf-8", "replace")}
