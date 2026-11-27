# Requests to services whose address this program chooses itself: ARES (lookups.py) and MPSV's code lists
# (codelists.py); and to the time stamp service the user set up (time_stamps.py). Never to an address a form gives.
import ssl
import urllib.request

import certifi


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_):  # a redirect would lead off the address this program chose
        return None


def tls_context():
    # The system's certificates too: offices with a TLS-inspecting proxy have its authority only there.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return context


def opener():
    return urllib.request.build_opener(_NoRedirects(), urllib.request.HTTPSHandler(context=tls_context()))
