# Code lists MPSV forms download when they open: the form only names a list of MPSV's own list service, the address is
# this program's. Nothing the user filled in is sent, and a list is kept in the settings for use without the internet.
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait

from lxml import etree

import public_services
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
OPENING_WAIT_SECONDS = 6  # for all missing lists together, when no page shows the progress: the window stays empty meanwhile
MAX_LIST_BYTES = 8 * 1024 * 1024
XML_CONTENT_TYPES = {"application/xml", "text/xml"}
MAX_DOWNLOADS_FOLLOWED = 20
_downloads = {}
_downloads_lock = threading.Lock()
_refreshing = set()
_refreshing_lock = threading.Lock()


def _enum_list_name(enum):
    match = NAMED_IN_UPDATE_ADDRESS.fullmatch((enum.get("updateurl") or "").strip())
    return match.group(1) if match else None


def _is_downloaded(enum):
    return bool(enum.get("updateurl")) and not len(enum.findall(f"{{{FM}}}list/{{{FM}}}listitem"))


def _named_by_the_form(root):
    texts = [value for element in root.iter() if isinstance(element.tag, str) and element.tag != f"{{{FM}}}enum"
             for value in element.attrib.values()]
    return "\n".join(texts + [script.text or "" for script in root.iter(f"{{{FM}}}script")])


def _used_downloaded_enums(root):
    # A form may carry lists it neither shows nor reads (GetListItemData in a calculation reads one by its name). Those are
    # left alone: nothing is downloaded for them, and nothing is missing without them.
    enums = [enum for enum in root.iter(f"{{{FM}}}enum") if _is_downloaded(enum)]
    shown = {element.get("form602listname") for element in root.iter() if isinstance(element.tag, str) and element.get("form602listname")}
    unshown = [enum for enum in enums if enum.get("name") not in shown]
    named = _named_by_the_form(root) if unshown else ""
    read = {enum.get("name") for enum in unshown if re.search(f"""['"]{re.escape(enum.get("name") or "")}['"]""", named)}
    return [enum for enum in enums if enum.get("name") in shown or enum.get("name") in read]


def _whisperer_list_name(field):
    match = WHISPERER_CALL.search(field.get("updateurl") or "")
    return WHISPERER_LISTS.get(match.group(1)) if match else None


def _whisperers(root):
    return [(field, name) for field in root.iter() if isinstance(field.tag, str) and field.get("form602ct") == WHISPERER_FIELD_TYPE
            for name in [_whisperer_list_name(field)] if name]


def needed(root):
    enums = {name for enum in _used_downloaded_enums(root) for name in [_enum_list_name(enum)] if name}
    return enums | {name for _, name in _whisperers(root)}


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
    with public_services.opener().open(request, timeout=TIMEOUT_SECONDS) as response:
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


def never_stored(names, folder):
    return sorted(name for name in names if not (folder / f"{name}.xml").exists())


class _Download:
    # The lists one opening lacks, fetched in the background while a page shows how far it got.
    def __init__(self, names, folder):
        self.total, self.done, self.finished = len(names), 0, False
        threading.Thread(target=self._run, args=(names, folder), daemon=True).start()

    def _run(self, names, folder):
        with ThreadPoolExecutor(max_workers=4) as pool:
            for _ in pool.map(lambda name: _fetch(folder, name), names):
                self.done += 1
        self.finished = True


def start_download(names, folder):
    token = secrets.token_urlsafe(12)
    with _downloads_lock:
        _downloads[token] = _Download(names, folder)
        while len(_downloads) > MAX_DOWNLOADS_FOLLOWED:
            del _downloads[next(iter(_downloads))]
    return token


def download_progress(token):
    download = _downloads.get(token)
    return None if download is None else {"total": download.total, "done": download.done, "finished": download.finished}


def load(names, folder, wait_seconds=None):
    # A stored list serves at once and an old one is renewed for next time; only a list never stored is waited for, and
    # one still downloading when the wait is over is stored for the next opening.
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
        pool = ThreadPoolExecutor(max_workers=4)
        fetching = {pool.submit(_fetch, folder, name): name for name in missing}
        done, _ = wait(fetching, timeout=OPENING_WAIT_SECONDS if wait_seconds is None else wait_seconds)
        pool.shutdown(wait=False)
        for future in done:
            if future.result() is not None:
                lists[fetching[future]] = future.result()
    return lists


def fill(root, lists):
    # Into the form as it is open, never into the file: the lists are no part of what the office signed.
    unfilled = set()
    for enum in _used_downloaded_enums(root):
        name = _enum_list_name(enum)
        if name is None:
            if not PORTAL_RECIPIENTS.match(enum.get("updateurl")):
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
