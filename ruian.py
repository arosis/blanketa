# Addresses of MPSV forms checked in RÚIAN, the register of addresses kept by ČÚZK, after the user agreed on the page. The
# form would ask MPSV's own service; this server asks ČÚZK's public search instead and sends only the address.
import json
import os
import re
import urllib.parse
import urllib.request

import public_services

SEARCH = "https://ags.cuzk.gov.cz/arcgis/rest/services/RUIAN/MapServer/exts/GeocodeSOE/findAddressCandidates"
ADDRESS_POINT = "AdresniMisto"
CANDIDATES_ASKED = 5
CANDIDATES_SHOWN = 3
TIMEOUT_SECONDS = 8
MAX_ANSWER_BYTES = 256 * 1024
JSON_CONTENT_TYPE = "application/json"
UNAVAILABLE = "Registr RÚIAN teď není dostupný, adresy nebyly ověřeny. Před odesláním je zkontrolujte."
KNOWN, SIMILAR, UNKNOWN = "known", "similar", "unknown"
# How the register writes an address: "Na Poříčním právu 376/1, Nové Město, 12800 Praha 2", "Lhotka 12, 26301 Obořiště",
# "č.ev. 12, 27731 Lhotka". The search finds an address even from a part of it, so what it finds is compared part by part.
BUILDING = re.compile(r"(?:(?P<name>(?!č\.ev\.).*?) )?(?P<registry>č\.ev\. )?(?P<number>\d+)(?:/(?P<orientation>\d+[a-zA-Z]?))?")
POST_OFFICE = re.compile(r"(?P<postcode>\d{3} ?\d{2}) (?P<municipality>.+)")
MUNICIPALITY_DISTRICT = re.compile(r" \d+")


def _same(first, second):
    return " ".join((first or "").split()).casefold() == " ".join((second or "").split()).casefold()


def _digits(text):
    return re.sub(r"\D", "", text or "")


def written(address, with_post_office=True):
    registry = not address["CisloPopisne"] and address["CisloEvidencni"]
    number = (f"č.ev. {address['CisloEvidencni']}" if registry else address["CisloPopisne"]) + (
        f"/{address['CisloOrientacni']}" if address["CisloOrientacni"] else "")
    place = address["Ulice"] if registry else address["Ulice"] or address["CastObce"] or address["Obec"]
    building = " ".join(part for part in (place, number) if part)
    part_of_municipality = address["CastObce"] if address["CastObce"] not in (place, address["Obec"]) else ""
    post_office = " ".join(part for part in (_digits(address["Psc"]) if with_post_office else "", address["Obec"]) if part)
    return ", ".join(part for part in (building, part_of_municipality if with_post_office else "", post_office) if part)


def _registered(text):
    parts = text.split(", ")
    building, post_office = BUILDING.fullmatch(parts[0]), POST_OFFICE.fullmatch(parts[-1])
    if len(parts) < 2 or building is None or post_office is None:
        return None
    return {**building.groupdict(), **post_office.groupdict(), "part": parts[1] if len(parts) == 3 else ""}


def _same_municipality(registered, written_name):
    # Prague's addresses name its district too ("Praha 2"); the form may have only "Praha".
    return _same(registered, written_name) or (
        _same(registered[:len(written_name)], written_name) and MUNICIPALITY_DISTRICT.fullmatch(registered[len(written_name):]))


def agrees(address, text):
    registered = _registered(text)
    if registered is None:
        return False
    registry = not address["CisloPopisne"] and bool(address["CisloEvidencni"])
    street, part = address["Ulice"], address["CastObce"]
    names_of_place = (part, address["Obec"]) if not street else (street,)
    return (registered["number"] == (address["CisloEvidencni"] if registry else address["CisloPopisne"])
            and bool(registered["registry"]) == registry
            and _same(registered["orientation"], address["CisloOrientacni"])
            and _digits(registered["postcode"]) == _digits(address["Psc"])
            and bool(_same_municipality(registered["municipality"], address["Obec"]))
            and (registered["name"] is None if registry and not street else any(_same(registered["name"], name) for name in names_of_place))
            and (not part or any(_same(part, name) for name in (registered["part"], registered["name"], registered["municipality"]))))


def _candidates(text):
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    query = urllib.parse.urlencode({"SingleLine": text, "f": "json", "maxLocations": CANDIDATES_ASKED})
    request = urllib.request.Request(f"{SEARCH}?{query}", headers={"User-Agent": "Blanketa", "Accept": JSON_CONTENT_TYPE})
    with public_services.opener().open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.headers.get_content_type() != JSON_CONTENT_TYPE:  # a proxy's or a Wi-Fi sign-in page, not the register
            raise OSError("not an answer of the register")
        data = response.read(MAX_ANSWER_BYTES + 1)
    if len(data) > MAX_ANSWER_BYTES:
        raise OSError("answer too large")
    try:
        candidates = json.loads(data).get("candidates")
    except (ValueError, AttributeError) as error:
        raise OSError("not an answer of the register") from error
    if not isinstance(candidates, list):
        raise OSError("not an answer of the register")
    return [candidate["address"] for candidate in candidates
            if isinstance(candidate, dict) and isinstance(candidate.get("address"), str)
            and isinstance(candidate.get("attributes"), dict) and candidate["attributes"].get("Type") == ADDRESS_POINT]


def check(address):
    text = written(address)
    # A wrong postcode or part of the municipality finds nothing; without them the register offers the address it knows.
    found = _candidates(text) or _candidates(written(address, with_post_office=False))
    matching = [candidate for candidate in found if agrees(address, candidate)]
    if matching:
        return {"address": text, "verdict": KNOWN, "registered": matching[:1]}
    return {"address": text, "verdict": SIMILAR if found else UNKNOWN, "registered": found[:CANDIDATES_SHOWN]}
