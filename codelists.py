# Code lists MPSV forms download when they open: the form only names a list of MPSV's own list service, the address is
# this program's. Nothing the user filled in is sent, and a list is kept in the settings for use without the internet.
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import certifi
from lxml import etree

from zfoengine.form_scripts import WHISPERER_FIELD_TYPE, WHISPERER_LIST_ATTRIBUTE

SOURCE = "https://mpsv.gov.cz/fas-adapter-codelists/"
FM = "http://software602.cz/forms"
LIST_NAME = r"([A-Z][A-Za-z0-9_]{0,63})"
# What MPSV forms put in fm:enum/@updateurl: the address of their list service, from the settings of MPSV's portal.
NAMED_IN_UPDATE_ADDRESS = re.compile(r'\$\(Eval\("StrCat\(GetCustomConfigValue\((?:VratCiselnik\)|url_base\),fas-adapter-codelists/),'
                                     + LIST_NAME + r'\.xml\)"\)\)?')
# The offices a form is sent to through MPSV's portal, named by the form's data. Sending is the portal's, not Blanketa's.
PORTAL_RECIPIENTS = re.compile(r'\$\(Eval\("StrCat\(GetCustomConfigValue\(url_base\),fas-adapter-codelists/,DataXpath\(')
WHISPERER_LISTS = {"CISELNIK_OBEC": "Obec", "ČÍSELNÍK - OBEC": "Obec"}
WHISPERER_CALL = re.compile(r"""Naseptavac\(\s*['"]([^'"]+)['"]\s*\)""")
WHISPERER_ENUM_PREFIX = "blanketa-naseptavac-"
FRESH_SECONDS = 7 * 24 * 3600
TIMEOUT_SECONDS = 6
MAX_LIST_BYTES = 8 * 1024 * 1024
XML_CONTENT_TYPES = {"application/xml", "text/xml"}
_refreshing = set()
_refreshing_lock = threading.Lock()


def _enum_list_name(enum):
    match = NAMED_IN_UPDATE_ADDRESS.fullmatch((enum.get("updateurl") or "").strip())
    return match.group(1) if match and not len(enum.findall(f"{{{FM}}}list/{{{FM}}}listitem")) else None


def _whisperer_list_name(field):
    match = WHISPERER_CALL.search(field.get("updateurl") or "")
    return WHISPERER_LISTS.get(match.group(1)) if match else None


def _whisperers(root):
    return [(field, name) for field in root.iter() if isinstance(field.tag, str) and field.get("form602ct") == WHISPERER_FIELD_TYPE
            for name in [_whisperer_list_name(field)] if name]


def needed(root):
    enums = {name for enum in root.iter(f"{{{FM}}}enum") for name in [_enum_list_name(enum)] if name}
    return enums | {name for _, name in _whisperers(root)}


def _opener():
    class NoRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_):  # a redirect would lead off MPSV's list service
            return None
    # The system's certificates too: offices with a TLS-inspecting proxy have its authority only there.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return urllib.request.build_opener(NoRedirects(), urllib.request.HTTPSHandler(context=context))


def _items_of(data, name):
    # A list is a hostile file like a form: only fm:listitem with its two texts is taken over, nothing else of it.
    root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
    if root.tag != f"{{{FM}}}enum" or root.get("name") != name:
        raise ValueError(f"not the list {name}")
    lists = root.findall(f"{{{FM}}}list")
    if not lists:
        raise ValueError(f"list {name} is empty")
    clean = etree.Element(f"{{{FM}}}enum", nsmap={"fm": FM}, name=name)
    for found in lists:
        kept = etree.SubElement(clean, f"{{{FM}}}list", lid=found.get("lid") or "")
        for item in found.findall(f"{{{FM}}}listitem"):
            etree.SubElement(kept, f"{{{FM}}}listitem", itemtext=item.get("itemtext") or "", value=item.get("value") or "")
    return clean


def _download(name):
    if os.environ.get("ZFO_OFFLINE"):
        raise OSError("offline")
    request = urllib.request.Request(SOURCE + name + ".xml", headers={"User-Agent": "Blanketa"})
    with _opener().open(request, timeout=TIMEOUT_SECONDS) as response:
        if response.headers.get_content_type() not in XML_CONTENT_TYPES:  # a proxy's or a Wi-Fi sign-in page
            raise OSError(f"list {name} is not XML")
        data = response.read(MAX_LIST_BYTES + 1)
    if len(data) > MAX_LIST_BYTES:
        raise OSError(f"list {name} too large")
    return data


def _store(folder, name, data):
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=folder)
    with os.fdopen(handle, "wb") as out:
        out.write(data)
    os.replace(temporary, folder / f"{name}.xml")


def _fetch(folder, name):
    try:
        data = _download(name)
        items = _items_of(data, name)
    except (OSError, ValueError, etree.LxmlError):
        return None
    _store(folder, name, etree.tostring(items, xml_declaration=True, encoding="UTF-8"))
    return items


def _refresh_later(folder, name):
    with _refreshing_lock:
        if name in _refreshing:
            return
        _refreshing.add(name)

    def refresh():
        try:
            _fetch(folder, name)
        finally:
            with _refreshing_lock:
                _refreshing.discard(name)
    threading.Thread(target=refresh, daemon=True).start()


def _stored(folder, name):
    path = folder / f"{name}.xml"
    try:
        return _items_of(path.read_bytes(), name), path.stat().st_mtime
    except (OSError, ValueError, etree.LxmlError):
        return None, 0


def load(names, folder):
    # A stored list serves at once and an old one is renewed for next time; only a list never stored is waited for.
    lists, missing = {}, []
    for name in sorted(names):
        items, stored_at = _stored(folder, name)
        if items is None:
            missing.append(name)
            continue
        lists[name] = items
        if time.time() - stored_at > FRESH_SECONDS:
            _refresh_later(folder, name)
    if missing:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for name, items in zip(missing, pool.map(lambda name: _fetch(folder, name), missing), strict=True):
                if items is not None:
                    lists[name] = items
    return lists


def fill(root, lists):
    # Into the form as it is open, never into the file: the lists are no part of what the office signed.
    unfilled = set()
    for enum in root.iter(f"{{{FM}}}enum"):
        name = _enum_list_name(enum)
        if name is None:
            address = enum.get("updateurl") or ""
            if address and not PORTAL_RECIPIENTS.match(address) and not len(enum.findall(f"{{{FM}}}list/{{{FM}}}listitem")):
                unfilled.add(enum.get("name") or "")
            continue
        if name not in lists:
            unfilled.add(name)
            continue
        for found in lists[name].findall(f"{{{FM}}}list"):
            enum.append(etree.fromstring(etree.tostring(found)))
    params = root.find(f".//{{{FM}}}formparams")
    added = set()
    for field, name in _whisperers(root):
        if name not in lists or params is None:
            unfilled.add(name)
            continue
        field.set(WHISPERER_LIST_ATTRIBUTE, WHISPERER_ENUM_PREFIX + name)
        if name not in added:
            added.add(name)
            enum = etree.SubElement(params, f"{{{FM}}}enum", name=WHISPERER_ENUM_PREFIX + name)
            for found in lists[name].findall(f"{{{FM}}}list"):
                enum.append(etree.fromstring(etree.tostring(found)))
    return unfilled
