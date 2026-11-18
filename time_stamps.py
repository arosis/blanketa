import base64
import http.client
import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from asn1crypto import cms, core, tsp
from pyhanko.sign.timestamps import TimeStamper
from pyhanko.sign.timestamps.common_utils import TimestampRequestError

import certstore
import public_services

PUBLIC_QUALIFIED_SERVICE = "http://timestamp.sectigo.com/qualified"
PUBLIC_SERVICE, OWN_SERVICE = "public", "own"
SETTINGS_FILE = "time-stamps.json"
ROOM_FOR_TOKEN = 16 * 1024
LONGEST_REPLY = 256 * 1024
SECONDS_TO_WAIT = 20
BYTES_READ_AT_ONCE = 16 * 1024
NO_SETTINGS = {"enabled": False, "service": PUBLIC_SERVICE, "url": "", "user": ""}


class TimeStampUnavailable(RuntimeError):
    pass


def settings():
    try:
        stored = json.loads((certstore.config_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(NO_SETTINGS)
    if not isinstance(stored, dict):
        return dict(NO_SETTINGS)
    kept = {name: stored[name] if isinstance(stored.get(name), type(default)) else default
            for name, default in NO_SETTINGS.items()}
    if kept["service"] not in (PUBLIC_SERVICE, OWN_SERVICE):
        kept["service"] = PUBLIC_SERVICE
    return kept


def store_settings(wanted):
    if not isinstance(wanted, dict):
        raise ValueError("Nastavení časových razítek nemá očekávaný tvar.")
    service = OWN_SERVICE if wanted.get("service") == OWN_SERVICE else PUBLIC_SERVICE
    url = str(wanted.get("url") or "").strip()
    user = str(wanted.get("user") or "").strip()
    if service == OWN_SERVICE and wanted.get("enabled"):
        _refuse_unusable_address(url, user)
    stored = {"enabled": bool(wanted.get("enabled")), "service": service, "url": url, "user": user}
    (certstore.config_dir() / SETTINGS_FILE).write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    return stored


def _refuse_unusable_address(url, user):
    address = urlsplit(url)
    if address.scheme not in ("http", "https") or not address.hostname:
        raise ValueError("Adresa služby časových razítek musí začínat http:// nebo https://.")
    if address.username or address.password:
        raise ValueError("Jméno a heslo do adresy služby nepatří, jméno zadejte zvlášť.")
    if user and address.scheme != "https":
        raise ValueError("Služba s přihlášením musí mít adresu začínající https://, jinak by heslo šlo sítí nechráněné.")


def service_time_stamper(password=None):
    chosen = settings()
    if chosen["service"] == PUBLIC_SERVICE:
        return ServiceTimeStamper(PUBLIC_QUALIFIED_SERVICE)
    _refuse_unusable_address(chosen["url"], chosen["user"])
    return ServiceTimeStamper(chosen["url"], chosen["user"], password)


def _stamped_digest(token):
    return token["content"]["encap_content_info"]["content"].parsed["message_imprint"]["hashed_message"].native


def _reply_read_until(give_up_at, response):
    # The connection's own timeout restarts with every byte; a service that drips its reply would hold the signing for hours.
    reply = b""
    while len(reply) <= LONGEST_REPLY:
        if time.monotonic() > give_up_at:
            raise TimeStampUnavailable("Služba časových razítek neodpověděla včas.")
        part = response.read1(BYTES_READ_AT_ONCE)
        if not part:
            break
        reply += part
    return reply


class _Reply(core.Sequence):
    # RFC 3161 leaves the token out of a refusal; asn1crypto's TimeStampResp cannot be read without one.
    _fields = [("status", tsp.PKIStatusInfo), ("time_stamp_token", cms.ContentInfo, {"optional": True})]


class ServiceTimeStamper(TimeStamper):
    def __init__(self, url, user="", password=None):
        super().__init__()
        self.url = url
        self.user = user
        self.password = password

    async def async_dummy_response(self, md_algorithm):
        # pyHanko would ask the service for a throwaway token just to measure it; a paid service charges for that one too.
        return cms.ContentInfo({"content_type": "data", "content": bytes(ROOM_FOR_TOKEN)})

    async def async_timestamp(self, message_digest, md_algorithm):
        try:
            token = await super().async_timestamp(message_digest, md_algorithm)
            stamped = _stamped_digest(token)
        except (TimestampRequestError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise TimeStampUnavailable("Služba časových razítek razítko nevydala.") from error
        if stamped != message_digest:
            raise TimeStampUnavailable("Služba časových razítek orazítkovala něco jiného než tento podpis.")
        if len(token.dump()) > ROOM_FOR_TOKEN:
            raise TimeStampUnavailable("Časové razítko od této služby je větší, než kolik místa na ně Blanketa v podpisu nechává.")
        return token

    async def async_request_tsa_response(self, req):
        headers = {"Content-Type": "application/timestamp-query", "Accept": "application/timestamp-reply"}
        if self.user:
            credentials = f"{self.user}:{self.password or ''}".encode()
            headers["Authorization"] = "Basic " + base64.b64encode(credentials).decode("ascii")
        request = urllib.request.Request(self.url, data=req.dump(), headers=headers)
        give_up_at = time.monotonic() + SECONDS_TO_WAIT
        try:
            with public_services.opener().open(request, timeout=SECONDS_TO_WAIT) as response:
                reply = _reply_read_until(give_up_at, response)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise TimeStampUnavailable("Služba časových razítek odmítla přihlášení. Zkontrolujte jméno a heslo.") from error
            raise TimeStampUnavailable(f"Služba časových razítek odpověděla chybou {error.code}.") from error
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise TimeStampUnavailable("Službu časových razítek se nepodařilo zastihnout.") from error
        if len(reply) > LONGEST_REPLY:
            raise TimeStampUnavailable("Služba časových razítek poslala příliš dlouhou odpověď.")
        try:
            granted = _Reply.load(reply)["status"]["status"].native == "granted"
        except (ValueError, TypeError, KeyError) as error:
            raise TimeStampUnavailable("Služba časových razítek neposlala časové razítko.") from error
        if not granted:
            raise TimeStampUnavailable("Služba časových razítek razítko nevydala.")
        return tsp.TimeStampResp.load(reply)
