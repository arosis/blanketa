import base64
import copy
import functools
import hashlib
import io
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from cryptography import x509
from lxml import etree

import asic_verify
import barcode
import certstore
import codelists
import isds_message
import lookups
import pdf_form_data
import pdf_sign
import ruian
import zfo_sign
import zfo_verify
from zfoengine import api as engine
from zfoengine import form_scripts, js602

ROOT = Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, "_MEIPASS", ROOT))
EDITOR = RESOURCES / "vendor/zfo-editor-src/zfo-editor-0.3.2"
PDFJS = RESOURCES / "vendor/pdfjs"
DS = "http://www.w3.org/2000/09/xmldsig#"
FM = "http://software602.cz/forms"
FO = "http://www.w3.org/1999/XSL/Format"
xslt_extension_functions = etree.FunctionNamespace("urn:blanketa")
xslt_extension_functions["barcode"] = lambda _context, *args: barcode.svg_data_uri(*args)
OPENING_PLACEHOLDER_STYLE = ('<style>body:not(.zfo-app) > * { display: none; } body:not(.zfo-app)::before { content: "Otevírám formulář…"; '
                             'display: block; margin-top: 30vh; text-align: center; font: 14px sans-serif; color: #6e6e73; }</style>')
XSLT = etree.XSLT(etree.parse(str(RESOURCES / "ui/fo2html.xsl")))
FORMS = {}
SINGLE_FILE = False
DAMAGED_PAGE = ("<!doctype html><meta charset=utf-8><title>Soubor nelze otevřít</title>"
                "<body style='font:14px system-ui,sans-serif;padding:24px'><h1 style='font-size:18px'>Soubor nelze otevřít</h1>"
                "<p>Soubor může být poškozený nebo nejde o formulář, který Blanketa umí přečíst. Může jít i o chybu Blankety; "
                "pokud ho jiná aplikace otevře, dejte nám prosím vědět.</p>")
NEVER_RUN_SCRIPT_ATTRIBUTES = ("form602attchuploadstartaction",)
SESSIONS = {}
SHOWN_CERTIFICATES_BY_ID = {}
IMPORTS = {}
LOCK = threading.Lock()
MAX_SESSIONS = 8
PLAIN_TEXT = "text/plain"
PLAIN_TEXT_UTF8 = "text/plain; charset=utf-8"
HTML_UTF8 = "text/html; charset=utf-8"
JSON = "application/json"
STATIC_CONTENT_TYPES = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}
PDFJS_CONTENT_TYPES = {".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                       ".png": "image/png"}

SHELL = (RESOURCES / "ui/shell.html").read_text(encoding="utf-8")
SHELL_STYLE = re.search(r"<style>.*?</style>", SHELL, re.S).group(0).replace("__FONTS__", "")
PDF_PAGE = (RESOURCES / "ui/pdf.html").read_text(encoding="utf-8").replace("__SHELL_STYLE__", SHELL_STYLE)
MESSAGE_PAGE = (RESOURCES / "ui/message.html").read_text(encoding="utf-8").replace("__SHELL_STYLE__", SHELL_STYLE)
CONTAINER_PAGE = (RESOURCES / "ui/container.html").read_text(encoding="utf-8").replace("__SHELL_STYLE__", SHELL_STYLE)
CODELISTS_PAGE = (RESOURCES / "ui/codelists.html").read_text(encoding="utf-8").replace("__SHELL_STYLE__", SHELL_STYLE)
PAGE_SCRIPTS = {f"/{name}": RESOURCES / "ui" / name
                for name in ("files.js", "shell.js", "pdf.mjs", "message.js", "container.js", "codelists.js")}


def json_for_script(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def page_with_data(template, data, fonts=""):
    values = {"__PAGE_DATA__": json_for_script(data), "__FONTS__": fonts}
    # One pass: a placeholder name inside a file name, a message or a font must stay text.
    return re.sub("__PAGE_DATA__|__FONTS__", lambda match: values[match.group(0)], template)


def new_session(content):
    session = secrets.token_urlsafe(12)
    with LOCK:
        SESSIONS[session] = content
        while len(SESSIONS) > MAX_SESSIONS:
            SESSIONS.pop(next(iter(SESSIONS)))
    return session


def form_path(name):
    if name not in FORMS:
        raise FileNotFoundError(name)
    return FORMS[name]


def output_path(name):
    source = FORMS[name]
    if not SINGLE_FILE:
        return ROOT / "out" / f"{name}-podepsany{source.suffix}"
    stem = source.stem.removesuffix("-podepsany")
    candidate, copy_number = source.with_name(f"{stem}-podepsany{source.suffix}"), 2
    while candidate.exists() and candidate != source:
        candidate, copy_number = source.with_name(f"{stem}-podepsany-{copy_number}{source.suffix}"), copy_number + 1
    return candidate


def parse_fo(fo):
    return etree.fromstring(fo, etree.XMLParser(huge_tree=True))


def publisher_layer(doc):
    while not is_publisher_signature(doc):
        doc = doc.find(f"{{{DS}}}Object/{{{DS}}}Signature")
    return doc


def is_publisher_signature(signature):
    certificate = signature.find(f"{{{DS}}}KeyInfo//{{{DS}}}X509Certificate").text
    subject = x509.load_der_x509_certificate(base64.b64decode(certificate)).subject.rfc4514_string()
    return "602XML" in subject


def remove_data_objects(layer):
    for data_object in layer.xpath("dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
        layer.remove(data_object)


def render_empty_window():
    return page_with_data(SHELL, {"session": "", "file": "", "empty": True})


def judge_time_stamps(signature):
    problem = ""
    proofs = []
    unverifiable = False
    for stamp in signature.pop("stamps", ()):
        der = stamp.pop("der")
        if not stamp["intact"]:
            problem = problem or "Časové razítko je poškozené nebo nepatří k tomuto dokumentu."
            continue
        verdict = certstore.stamp_trust_verdict(der, datetime.fromisoformat(stamp["at"]))
        if verdict["qualified"]:
            proofs.append(stamp)
        else:
            problem = problem or verdict["reason"]
            unverifiable = unverifiable or verdict["unverifiable"]
    signature["proof"] = min(proofs, key=lambda stamp: stamp["at"], default=None)
    signature["stamp_problem"] = "" if proofs else problem
    signature["stamp_unverifiable"] = unverifiable and not proofs


def add_trust_verdicts(signatures):
    ders = [signature.pop("der") for signature in signatures]
    for signature in signatures:
        judge_time_stamps(signature)
    for signature, der, verdict in zip(signatures, ders, certstore.trust_verdicts(ders), strict=True):
        signature.update(trusted=verdict["trusted"], reason=verdict.get("reason", ""), trust_source=verdict["source"],
                         unverifiable=verdict["unverifiable"], certificate=hashlib.sha256(der).hexdigest())
        SHOWN_CERTIFICATES_BY_ID[signature["certificate"]] = der
    return signatures


def pdf_signatures(pdf):
    return add_trust_verdicts(pdf_sign.describe_signatures(pdf))


def pdf_xfa_usage(reader, fields):
    acroform = reader.trailer["/Root"].get_object().get("/AcroForm")
    acroform = acroform.get_object() if acroform is not None else None
    if not isinstance(acroform, dict) or "/XFA" not in acroform:
        return ""
    return "hybrid" if fields else "dynamic"


def pdf_info(pdf):
    reader = pdf_sign.PdfReader(io.BytesIO(pdf))
    metadata = reader.metadata or {}
    fields = pdf_sign.form_fields(pdf)
    return {"title": metadata.get("/Title") or "", "author": metadata.get("/Author") or "", "pages": len(reader.pages),
            "fields": len(fields), "producer": metadata.get("/Producer") or "",
            "signature_field": pdf_sign.empty_signature_field(pdf), "xfa": pdf_xfa_usage(reader, fields)}


def render_pdf_page(name):
    pdf = form_path(name).read_bytes()
    session = new_session({"name": name, "pdf": pdf})
    return page_with_data(PDF_PAGE, {"session": session, "file": form_path(name).name,
                                     "signatures": pdf_signatures(pdf), "info": pdf_info(pdf)})


def container_signatures(signatures):
    readable = [signature for signature in signatures if signature["der"] is not None]
    for signature in signatures:
        if signature["der"] is None:
            del signature["der"]
            judge_time_stamps(signature)
            signature.update(trusted=False, unverifiable=False, reason="", trust_source="", certificate="")
    add_trust_verdicts(readable)
    return signatures


def render_container_page(name):
    path = form_path(name)
    description, entries = asic_verify.describe(path.read_bytes())
    description["signatures"] = container_signatures(description["signatures"])
    content = {"name": name, "entries": entries, "names": [file["name"] for file in description["files"]]}
    return page_with_data(CONTAINER_PAGE, {"session": new_session(content), "file": path.name, "container": description})


def file_start(path):
    with open(path, "rb") as file:
        return file.read(1)


def render_message_page(name):
    message = isds_message.read_message(form_path(name).read_bytes())
    signatures = add_trust_verdicts([isds_message.describe_seal(message)])
    session = new_session({"name": name, "message": message})
    return page_with_data(MESSAGE_PAGE, {"session": session, "file": form_path(name).name, "message": message.info,
                                         "signatures": signatures, "submitted": isds_message.submission_stamp(message)})


def save_pdf(session, values, identity_id=None, placement=None, password=None):
    name = session["name"]
    pdf = filled_pdf(session, values)
    if identity_id:
        pdf = pdf_sign.sign_pdf(pdf, *certstore.load_identity(identity_id, password), placement=placement)
        target = output_path(name)
    else:
        target = form_path(name) if SINGLE_FILE else ROOT / "out" / f"{name}-ulozeny.pdf"
    target.write_bytes(pdf)
    session["pdf"] = pdf
    if SINGLE_FILE:
        FORMS[name] = target
    return {"saved": str(target), "signatures": pdf_signatures(pdf), "signature_field": pdf_sign.empty_signature_field(pdf)}


def filled_pdf(session, values):
    return pdf_sign.fill_form_fields(session["pdf"], values) if values else session["pdf"]


def write_pdf_for_printing(session, values):
    pdf = filled_pdf(session, values)
    target = Path(tempfile.gettempdir()) / f"zfo-tisk-{secrets.token_hex(4)}-{form_path(session['name']).name}"
    target.write_bytes(pdf)
    return {"path": str(target)}


def data_file_folder(session):
    return folder_for_data(session["name"])


def folder_for_data(name):
    return str(form_path(name).parent if SINGLE_FILE else ROOT / "out")


def data_file_stem(session):
    return form_path(session["name"]).stem if SINGLE_FILE else session["name"]


def export_pdf_data(session, values):
    pdf = filled_pdf(session, values)
    current = pdf_form_data.form_data_values(pdf)
    pdf_name = form_path(session["name"]).name
    return {"name": f"{data_file_stem(session)}-data.xfdf", "folder": data_file_folder(session),
            "contents": {"xfdf": pdf_form_data.write_xfdf(current, pdf_name), "fdf": pdf_form_data.write_fdf(current, pdf_name)}}


def import_pdf_data(session, data):
    is_document_locked, field_locks = pdf_sign.signature_locks(session["pdf"])
    if is_document_locked:
        raise ValueError("Podpis dokumentu nedovoluje žádné další změny, data do něj nelze načíst.")
    fields = pdf_form_data.form_fields(session["pdf"])
    values, missing, unchangeable, not_offered, several_values = {}, [], [], [], []
    for name, value in pdf_form_data.read_form_data(data).items():
        field = fields.get(name)
        if field is None:
            missing.append(name)
            continue
        if not pdf_form_data.is_fillable_kind(field.kind) or field.is_read_only or any(lock.is_locked(name) for lock in field_locks):
            unchangeable.append(name)
            continue
        text = pdf_form_data.single_text(field.kind, value)
        if text is None:
            several_values.append(name)
            continue
        if not field.offers(text):
            not_offered.append({"field": name, "value": text})
            continue
        values[name] = pdf_form_data.page_value(field.kind, text)
    return {"values": values, "missing": missing, "unchangeable": unchangeable, "not_offered": not_offered,
            "several_values": several_values}


def published_form_with_data(fo, data):
    published = publisher_layer(parse_fo(fo))
    remove_data_objects(published)
    if data is not None:
        etree.SubElement(published, f"{{{DS}}}Object").append(data)
    return etree.tostring(published)


def render_codelists_page(names, folder):
    return page_with_data(CODELISTS_PAGE, {"token": codelists.start_download(names, folder)})


def render_zfo_page(name, blank=False, data=None, lists_waited_for=False):
    fo = zfo_verify.read_form_fo(form_path(name))
    if blank or data is not None:
        fo = published_form_with_data(fo, data)
    engine_doc = engine.open_document(fo)
    doc = parse_fo(fo)
    data_text = functools.partial(engine.data_text, engine_doc)
    folder = certstore.config_dir() / "codelists"
    names = codelists.needed(doc, data_text)
    # The first opening shows how the lists download; imported data waits on this request, it cannot come back for a second.
    never_stored = codelists.never_stored(names, folder)
    if never_stored and data is None and not lists_waited_for and not os.environ.get("ZFO_OFFLINE"):
        return render_codelists_page(never_stored, folder)
    session = new_session({"name": name, "doc": engine_doc})
    lists = codelists.load(names, folder, wait_seconds=0 if lists_waited_for else None)
    codelists.fill(engine_doc.root, lists, data_text)
    unfilled_lists = codelists.fill(doc, lists, data_text)
    form_scripts.name_buttons_for_page(doc)
    form_scripts.name_repeating_sections_by_bind(doc)
    for hidden in doc.xpath("//dsig:SignatureProperties | //dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
        hidden.getparent().remove(hidden)
    html = etree.tostring(XSLT(etree.ElementTree(doc), PATH_TO_JS="'/static/scripts/'",
                               PATH_TO_CSS="'/static/styles/'"), method="html", encoding="unicode")
    shell = page_with_data(SHELL, {
        "session": session, "file": form_path(name).name if SINGLE_FILE else name, "folder": folder_for_data(name), "empty": False,
        "signatures": zfo_signatures(fo), "page": page_geometry(doc), "colors": field_colors(doc),
        "info": {**form_info(name), "missing_lists": bool(unfilled_lists)},
        "blank": blank, "buttons": user_buttons(doc), "codes": binds_of_lists_showing_codes(doc)}, fonts=embedded_fonts(doc))
    return html.replace("</head>", OPENING_PLACEHOLDER_STYLE + "</head>", 1).replace("</body>", shell + "</body>", 1)


def binds_of_lists_showing_codes(doc):
    return sorted({cell.get("form602bind") for cell in doc.iter(f"{{{FO}}}table-cell")
                   if cell.get("form602displaycode") == "true" and cell.get("form602bind")})


def user_buttons(doc):
    settings = doc.find(f".//{{{FM}}}formsettings")
    numbered_buttons = []
    for element in (settings if settings is not None else []):
        number = re.fullmatch(r"userbutton(\d+)", etree.QName(element).localname if isinstance(element.tag, str) else "")
        if number and element.get("express"):
            numbered_buttons.append((int(number.group(1)), {"caption": element.get("caption", ""), "tooltip": element.get("tooltip", ""),
                                                            "icon": element.get("iconid", ""), "text": element.get("showtext", "yes") != "no",
                                                            "express": element.get("express")}))
    return [button for _, button in sorted(numbered_buttons, key=lambda pair: pair[0])]


def is_font_safe_in_style_block(name, data):
    return name and data and re.fullmatch(r"[\w .-]+", name) and re.fullmatch(r"[A-Za-z0-9+/]+=*", data)


def embedded_fonts(doc):
    rules = []
    for font in doc.iter(f"{{{FM}}}font"):
        name, data = font.get("name"), "".join((font.text or "").split())
        if is_font_safe_in_style_block(name, data):
            rules.append(f'@font-face {{ font-family: {json.dumps(name)}; src: url(data:font/ttf;base64,{data}) format("truetype"); }}')
    return "\n  ".join(rules)


def attribute_or_default(element, attribute, default=""):
    return element.get(attribute, default) if element is not None else default


def field_colors(doc):
    params = doc.find(f".//{{{FM}}}formparams")
    return {"field": attribute_or_default(params, "proprequiredformfieldbkcolor", "rgb(255,255,180)"),
            "readonly": attribute_or_default(params, "proproformfieldbkcolor", "rgb(220,220,220)")}


def page_geometry(doc):
    master = next(doc.iter(f"{{{FO}}}simple-page-master"), None)
    if master is None:
        return {}
    margins = (master.get(f"margin-{side}", "15mm") for side in ("top", "right", "bottom", "left"))
    return {"width": master.get("page-width", "21cm"), "padding": " ".join(margins)}


def zfo_signatures(fo):
    return add_trust_verdicts(zfo_verify.describe_signature_layers(parse_fo(fo)))


def binds_with_unsupported_scripts(doc):
    # Tried on empty answers, so branches an empty form does not reach stay untried: the form check reports those it met
    # while the form was being filled.
    library = [element.text for element in doc.iter(f"{{{FM}}}script") if element.text]
    interpreter = js602.Interpreter(library)
    found = []
    for bind in doc.iter(f"{{{FM}}}bind"):
        for attribute, value in bind.attrib.items():
            if not js602.is_script(value):
                continue
            if attribute not in ("calculate", "constraint"):
                found.append(bind.get("Id"))
                break
            try:
                js602.evaluate(interpreter, value, lambda expression: "", "", lambda *args: "")
            except js602.Unsupported:
                found.append(bind.get("Id"))
                break
            except js602.Exhausted:  # one runaway script is enough to know: a form may have thousands
                return [*found, bind.get("Id")]
            except js602.Thrown:  # empty answers can trip a script up, that says nothing about what it needs
                continue
    return found


def count_never_run_scripts(doc):
    return sum(1 for element in doc.iter() if isinstance(element.tag, str) for attribute in NEVER_RUN_SCRIPT_ATTRIBUTES
               if element.get(attribute, "").lstrip().startswith("{"))


def summary_and_modification_time(path):
    if not zfo_verify.is_zip_form(path):
        return None, datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    with zipfile.ZipFile(path) as archive:
        summary = etree.fromstring(archive.read("summary/summary.xml")).find("summary") if "summary/summary.xml" in archive.namelist() else None
        # The forms carry no modification date; Form Filler stamps form.fo in the archive when it saves.
        return summary, datetime(*archive.getinfo("form.fo").date_time).isoformat()


def form_info(name):
    summary, modified = summary_and_modification_time(form_path(name))
    fo = parse_fo(zfo_verify.read_form_fo(form_path(name)))
    params = fo.find(f".//{{{FM}}}formparams")
    return {"title": attribute_or_default(summary, "title"), "subject": attribute_or_default(summary, "subject"),
            "author": attribute_or_default(summary, "author"), "keywords": attribute_or_default(summary, "keywords"),
            "comment": attribute_or_default(summary, "comment"), "code": attribute_or_default(summary, "formcode"),
            "form_id": attribute_or_default(params, "form-id") or attribute_or_default(summary, "form-id"),
            "created": attribute_or_default(params, "creationdate"), "modified": modified,
            "version": attribute_or_default(params, "xmlcore-version"), "scripts": len(binds_with_unsupported_scripts(fo)),
            "actions": count_never_run_scripts(fo)}


def filled_form(session, key=None, cert=None):
    doc = publisher_layer(parse_fo(zfo_verify.read_form_fo(form_path(session["name"]))))
    remove_data_objects(doc)
    etree.SubElement(doc, f"{{{DS}}}Object").append(copy.deepcopy(engine.data_element(session["doc"])))
    if key is None:
        return etree.tostring(doc, xml_declaration=True, encoding="UTF-8")
    return zfo_sign.sign_form(etree.tostring(doc), key, cert, "sha256")


def stamped_with_saving_time(zip_info):
    stamped = copy.copy(zip_info)
    stamped.date_time = time.localtime()[:6]
    return stamped


def write_form(name, fo, target):
    if zfo_verify.is_zip_form(form_path(name)):
        write_zfo(name, fo, target)
    else:
        target.write_bytes(fo)


def write_zfo(name, fo, target):
    out = io.BytesIO()
    with zipfile.ZipFile(form_path(name)) as source, zipfile.ZipFile(out, "w") as saved:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "form.fo":
                info, data = stamped_with_saving_time(info), fo
            saved.writestr(info, data, compress_type=info.compress_type)
    target.write_bytes(out.getvalue())


def export_data(session):
    xml = etree.tostring(engine.data_element(session["doc"]), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return {"name": f"{data_file_stem(session)}-data.xml", "folder": data_file_folder(session), "xml": xml.decode("utf-8")}


def import_data(session, xml):
    try:
        data = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as error:
        raise ValueError(f"Soubor není platné XML: {error}") from None
    expected = engine.data_element(session["doc"])
    if data.tag != expected.tag:
        raise ValueError(f"Data patří k jinému formuláři (kořen {etree.QName(data).localname}, "
                         f"formulář čeká {etree.QName(expected).localname})")
    token = secrets.token_urlsafe(12)
    IMPORTS[token] = data
    return {"import": token}


def save_zfo(session, identity_id=None, password=None):
    name = session["name"]
    if identity_id:
        fo = filled_form(session, *certstore.load_identity(identity_id, password))
        target = output_path(name)
    else:
        fo = filled_form(session)
        target = form_path(name) if SINGLE_FILE else ROOT / "out" / f"{name}-ulozeny{form_path(name).suffix}"
    write_form(name, fo, target)
    return {"saved": str(target), "signatures": zfo_signatures(fo)}


def first_query_value(query, key, default=""):
    return parse_qs(query).get(key, [default])[0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # A windowed Windows build has no stderr.
        if sys.stderr:
            super().log_message(format, *args)

    def _send_response(self, code, body, content_type, headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        for header, value in (headers or {}).items():
            self.send_header(header, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value):
        self._send_response(200, json.dumps(value, ensure_ascii=False).encode(), JSON)

    def _send_not_found(self):
        self._send_response(404, b"not found", PLAIN_TEXT)

    def _send_error_message(self, error):
        self._send_response(400, str(error).encode(), PLAIN_TEXT_UTF8)

    def _read_body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def _is_foreign_request(self):
        # Any web page in the user's browser can reach localhost: DNS rebinding changes the Host, a forged form the Origin.
        port = self.server.server_address[1]
        own_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in own_hosts:
            return True
        origin = self.headers.get("Origin")
        return self.command == "POST" and origin is not None and origin not in {f"http://{host}" for host in own_hosts}

    def _serve_file_inside(self, root, relative_path, content_types):
        path = os.path.realpath(root / relative_path)
        if path.startswith(os.path.realpath(root) + os.sep) and os.path.isfile(path):
            content_type = content_types.get(Path(path).suffix, "application/octet-stream")
            return self._send_response(200, Path(path).read_bytes(), content_type)
        return self._send_not_found()

    def _serve_bridge_script(self):
        desktop_flag = b"window.ZFO_DESKTOP = true;\n" if os.environ.get("ZFO_DESKTOP") else b""
        self._send_response(200, desktop_flag + (RESOURCES / "ui/bridge.js").read_bytes(), "text/javascript")

    def _serve_page_script(self, path):
        self._send_response(200, path.read_bytes(), "text/javascript")

    def _serve_session_pdf(self, query):
        session = SESSIONS.get(first_query_value(query, "s"))
        if session is None or "pdf" not in session:
            return self._send_not_found()
        return self._send_response(200, session["pdf"], "application/pdf")

    def _serve_container_file(self, query):
        session = SESSIONS.get(first_query_value(query, "s"))
        index = first_query_value(query, "i")
        if session is None or "entries" not in session or not index.isascii() or not index.isdigit() or len(index) > 6:
            return self._send_not_found()
        index = int(index)
        if index >= len(session["entries"]):
            return self._send_not_found()
        entry_name = session["names"][index]
        # Never rendered by the app window, PDFs included: PDF.js fetches the bytes itself.
        return self._send_response(200, session["entries"][index], "application/octet-stream",
                                   {"X-Content-Type-Options": "nosniff",
                                    "Content-Disposition": f"attachment; filename*=UTF-8''{quote(entry_name.rsplit('/', 1)[-1])}"})

    def _serve_attachment(self, query):
        session = SESSIONS.get(first_query_value(query, "s"))
        index = first_query_value(query, "i")
        if session is None or "message" not in session or not (index.isascii() and index.isdecimal()):
            return self._send_not_found()
        message = session["message"]
        if int(index) >= len(message.attachments):
            return self._send_not_found()
        name = message.info["attachments"][int(index)]["name"]
        body = message.attachments[int(index)]
        self.send_response(200)
        # Never rendered by the app window: a hostile attachment must not run as a page of the local server.
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(name, safe='')}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_form_list(self):
        links = "".join(f'<li><a href="/?form={quote(name)}">{name}</a></li>' for name in sorted(FORMS))
        self._send_response(200, f"<meta charset=utf-8><h1>Formuláře</h1><ul>{links}</ul>".encode(), HTML_UTF8)

    def _render_form_page(self, name, query, data):
        path = form_path(name)
        if path.suffix.lower() == ".pdf":
            return render_pdf_page(name)
        if isds_message.looks_like_message(file_start(path)):
            return render_message_page(name)
        if asic_verify.is_signed_path(path):
            return render_container_page(name)
        return render_zfo_page(name, first_query_value(query, "blank") == "1", data, first_query_value(query, "codelists") == "1")

    def _serve_form_page(self, name, query):
        try:
            data = IMPORTS.pop(first_query_value(query, "import"), None)
            page = self._render_form_page(name, query, data)
            self._send_response(200, page.encode(), HTML_UTF8)
        except FileNotFoundError:
            self._send_response(404, b"form not found", PLAIN_TEXT)
        except Exception as error:  # noqa: BLE001 - a damaged or hostile file must end in a message, not an empty window
            # Quoted with %r: the text can carry pieces of the file, which must not forge log lines.
            self.log_message("cannot open %r: %r", name, str(error)[:200])
            self._send_response(422, DAMAGED_PAGE.encode(), HTML_UTF8)

    def do_GET(self):
        if self._is_foreign_request():
            return self._send_response(403, b"forbidden", PLAIN_TEXT)
        url = urlparse(self.path)
        if url.path.startswith("/static/"):
            return self._serve_file_inside(EDITOR, url.path[len("/static/"):], STATIC_CONTENT_TYPES)
        if url.path.startswith("/pdfjs/"):
            return self._serve_file_inside(PDFJS, url.path[len("/pdfjs/"):], PDFJS_CONTENT_TYPES)
        if url.path == "/bridge.js":
            return self._serve_bridge_script()
        if url.path in PAGE_SCRIPTS:
            return self._serve_page_script(PAGE_SCRIPTS[url.path])
        if url.path == "/pdf":
            return self._serve_session_pdf(url.query)
        if url.path == "/container-file":
            return self._serve_container_file(url.query)
        if url.path == "/attachment":
            return self._serve_attachment(url.query)
        if url.path == "/identities":
            return self._send_json(certstore.signing_identities())
        if url.path == "/codelists-progress":
            progress = codelists.download_progress(first_query_value(url.query, "t"))
            return self._send_json(progress) if progress else self._send_not_found()
        name = first_query_value(url.query, "form", None)
        if not name and SINGLE_FILE and not FORMS:
            return self._send_response(200, render_empty_window().encode(), HTML_UTF8)
        if not name and SINGLE_FILE:
            name = next(iter(FORMS))
        if not name:
            return self._serve_form_list()
        return self._serve_form_page(name, url.query)

    def _add_certificate(self):
        request = json.loads(self._read_body() or b"{}")
        try:
            certstore.add_pfx(base64.b64decode(request["data"]), request.get("password") or "")
        except (KeyError, ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(certstore.signing_identities())

    def _report_revocation(self, body):
        try:
            certificate_ids = [certificate_id for certificate_id in json.loads(body or b"[]")
                               if isinstance(certificate_id, str) and certificate_id in SHOWN_CERTIFICATES_BY_ID]
        except (ValueError, TypeError):
            return self._send_response(400, b"bad request", PLAIN_TEXT)
        return self._send_json({certificate_id: certstore.revocation_status(SHOWN_CERTIFICATES_BY_ID[certificate_id])
                                for certificate_id in certificate_ids})

    def _answer_lookup(self, body):
        try:
            url = json.loads(body or b"{}").get("url")
            return self._send_json(lookups.ask(url))
        except (ValueError, TypeError, AttributeError):
            return self._send_response(400, b"bad request", PLAIN_TEXT)
        except OSError:
            return self._send_json({"error": lookups.UNAVAILABLE})

    def _check_addresses(self, session):
        # Only the addresses the form's own check collected (form_scripts.czech_addresses): the page cannot add any.
        with LOCK:
            addresses = list(session["doc"].addresses_to_check) if "doc" in session else []
        try:
            return self._send_json({"results": ruian.check_all(addresses)})
        except OSError:
            with LOCK:  # not checked after all: the next check of the form asks about them again
                session["doc"].addresses_asked.difference_update(form_scripts.address_text(address) for address in addresses)
            return self._send_json({"error": ruian.UNAVAILABLE})

    def _answer_engine_message(self, session, body):
        message = json.loads(body)
        self._send_json(engine.handle(session["doc"], message.get("type"), message.get("data")))

    def _save_zfo(self, session, query, body):
        try:
            result = save_zfo(session, first_query_value(query, "cert") or None, json.loads(body or b"{}").get("password"))
        except (LookupError, RuntimeError, ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(result)

    def _save_or_print_pdf(self, session, url, body):
        try:
            request = json.loads(body or b"{}")
            values = request.get("values") or {}
            result = (write_pdf_for_printing(session, values) if url.path == "/pdf-print"
                      else save_pdf(session, values, first_query_value(url.query, "cert") or None,
                                    request.get("placement"), request.get("password")))
        except (LookupError, RuntimeError, ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(result)

    def _send_pdf_for_printing(self, session, body):
        try:
            pdf = filled_pdf(session, json.loads(body or b"{}").get("values") or {})
        except (LookupError, ValueError) as error:
            return self._send_error_message(error)
        return self._send_response(200, pdf, "application/pdf")

    def _export_or_import_data(self, session, path, body):
        try:
            result = export_data(session) if path == "/export-data" else import_data(session, body)
        except (ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(result)

    def _export_or_import_pdf_data(self, session, path, body):
        try:
            result = (export_pdf_data(session, json.loads(body or b"{}").get("values") or {}) if path == "/pdf-export-data"
                      else import_pdf_data(session, body))
        except (LookupError, ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(result)

    def do_POST(self):
        if self._is_foreign_request():
            return self._send_response(403, b"forbidden", PLAIN_TEXT)
        url = urlparse(self.path)
        session = SESSIONS.get(first_query_value(url.query, "s"))
        if session is None:
            return self._send_response(404, "Relace vypršela, načti formulář znovu".encode(), PLAIN_TEXT_UTF8)
        if ("message" in session or "entries" in session) and url.path != "/revocation":
            return self._send_not_found()
        if url.path == "/add-certificate":
            return self._add_certificate()
        body = self._read_body()
        if url.path == "/revocation":  # outside the lock: it may wait for a download, the form must stay usable
            return self._report_revocation(body)
        if url.path == "/lookup":  # outside the lock for the same reason
            return self._answer_lookup(body)
        if url.path == "/check-addresses":
            return self._check_addresses(session)
        with LOCK:
            if url.path == "/ipc":
                return self._answer_engine_message(session, body)
            if url.path == "/save":
                return self._save_zfo(session, url.query, body)
            if url.path in ("/pdf-save", "/pdf-print"):
                return self._save_or_print_pdf(session, url, body)
            if url.path == "/pdf-print-document":
                return self._send_pdf_for_printing(session, body)
            if url.path in ("/export-data", "/import-data"):
                return self._export_or_import_data(session, url.path, body)
            if url.path in ("/pdf-export-data", "/pdf-import-data"):
                return self._export_or_import_pdf_data(session, url.path, body)
        return self._send_not_found()


def exit_when_stdin_closes():
    # The descriptor is read directly: a windowed Windows build has no sys.stdin object.
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        return
    os._exit(0)


def exit_when_parent_changes():
    parent = os.getppid()
    while os.getppid() == parent:
        threading.Event().wait(2)
    os._exit(0)


def exit_together_with_host_app():
    if os.environ.get("ZFO_WATCH_STDIN"):
        exit_when_stdin_closes()
    else:
        exit_when_parent_changes()


def main(args):
    global SINGLE_FILE
    if args[:1] in (["--file"], ["--empty"]):
        if args[0] == "--file":
            path = Path(args[1]).expanduser().resolve()
            FORMS[path.stem] = path
            args = args[1:]
        SINGLE_FILE, args = True, args[1:]
        threading.Thread(target=exit_together_with_host_app, daemon=True).start()
    else:
        FORMS.update({path.parent.name: path for path in sorted((ROOT / "samples").glob("*/form.zfo"))})
        FORMS.update({path.stem: path for path in sorted((ROOT / "samples-pdf").glob("*.pdf"))})
    server = ThreadingHTTPServer(("127.0.0.1", int(args[0]) if args else 8602), Handler)
    if sys.stdout:
        print(f"PORT {server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
