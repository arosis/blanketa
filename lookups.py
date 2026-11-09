# Lookups a form asks of a public register (zfoengine.form_scripts.known_lookup), made by this server after the user
# confirmed them on the page. The address is checked against that list again here: the page only passes it on.
import os
import ssl
import urllib.error
import urllib.request

import certifi

from zfoengine import form_scripts

TIMEOUT_SECONDS = 8
MAX_ANSWER_BYTES = 1024 * 1024
JSON_CONTENT_TYPE = "application/json"
UNAVAILABLE = "Registr ARES teď není dostupný. Údaje vyplňte ručně."


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):  # a redirect would lead the lookup off the address the user confirmed
        return None


def _opener():
    # The system's certificates too: offices with a TLS-inspecting proxy have its authority only there.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return urllib.request.build_opener(_NoRedirects(), urllib.request.HTTPSHandler(context=context))


def ask(url):
    if form_scripts.known_lookup(url) is None:
        raise ValueError("unknown lookup")
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    request = urllib.request.Request(url, headers={"User-Agent": "Blanketa", "Accept": JSON_CONTENT_TYPE})
    try:
        with _opener().open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.headers.get_content_type() != JSON_CONTENT_TYPE:  # a proxy's or a Wi-Fi sign-in page, not the register
                raise OSError("not an answer of the register")
            status, data = response.status, response.read(MAX_ANSWER_BYTES + 1)
    except urllib.error.HTTPError as error:
        status, data = error.code, error.read(MAX_ANSWER_BYTES + 1)
    if len(data) > MAX_ANSWER_BYTES:
        raise OSError("answer too large")
    return {"status": status, "body": data.decode("utf-8", "replace")}
