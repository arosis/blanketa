import base64
import copy
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

import barcode
import certstore
import pdf_sign
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
PDF_PAGE = (RESOURCES / "ui/pdf.html").read_text(encoding="utf-8").replace(
    "__SHELL_STYLE__", re.search(r"<style>.*?</style>", SHELL, re.S).group(0).replace("__FONTS__", ""))


def json_for_script(value):
    return json.dumps(value, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


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


def read_form_fo(path):
    return zipfile.ZipFile(path).read("form.fo")


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
    return SHELL.replace("__SESSION__", "").replace("__FILE__", '""').replace("__FONTS__", "").replace("__EMPTY__", "true")


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
    return (PDF_PAGE.replace("__SESSION__", session)
            .replace("__FILE__", json_for_script(form_path(name).name))
            .replace("__SIGNATURES__", json_for_script(pdf_signatures(pdf)))
            .replace("__INFO__", json_for_script(pdf_info(pdf))))


def save_pdf(session, values, identity_id=None, placement=None, password=None):
    name = session["name"]
    pdf = pdf_sign.fill_form_fields(session["pdf"], values) if values else session["pdf"]
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


def write_pdf_for_printing(session, values):
    pdf = pdf_sign.fill_form_fields(session["pdf"], values) if values else session["pdf"]
    target = Path(tempfile.gettempdir()) / f"zfo-tisk-{secrets.token_hex(4)}-{form_path(session['name']).name}"
    target.write_bytes(pdf)
    return {"path": str(target)}


def published_form_with_data(fo, data):
    published = publisher_layer(parse_fo(fo))
    remove_data_objects(published)
    if data is not None:
        etree.SubElement(published, f"{{{DS}}}Object").append(data)
    return etree.tostring(published)


def render_zfo_page(name, blank=False, data=None):
    fo = read_form_fo(form_path(name))
    if blank or data is not None:
        fo = published_form_with_data(fo, data)
    session = new_session({"name": name, "doc": engine.open_document(fo)})
    doc = parse_fo(fo)
    form_scripts.name_buttons_for_page(doc)
    form_scripts.name_repeating_sections_by_bind(doc)
    for hidden in doc.xpath("//dsig:SignatureProperties | //dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
        hidden.getparent().remove(hidden)
    html = etree.tostring(XSLT(etree.ElementTree(doc), PATH_TO_JS="'/static/scripts/'",
                               PATH_TO_CSS="'/static/styles/'"), method="html", encoding="unicode")
    shell = (SHELL.replace("__SESSION__", session)
             .replace("__FILE__", json_for_script(form_path(name).name if SINGLE_FILE else name))
             .replace("__SIGNATURES__", json_for_script(zfo_signatures(fo)))
             .replace("__PAGE__", json_for_script(page_geometry(doc)))
             .replace("__COLORS__", json_for_script(field_colors(doc)))
             .replace("__INFO__", json_for_script(form_info(name)))
             .replace("__BLANK__", json_for_script(blank))
             .replace("__FONTS__", embedded_fonts(doc))
             .replace("__BUTTONS__", json_for_script(user_buttons(doc)))
             .replace("__CODES__", json_for_script(binds_of_lists_showing_codes(doc)))
             .replace("__EMPTY__", "false"))
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


def has_downloaded_lists(doc):
    return any(element.get("updateurl") for element in doc.iter(f"{{{FM}}}enum"))


def form_info(name):
    with zipfile.ZipFile(form_path(name)) as archive:
        summary = etree.fromstring(archive.read("summary/summary.xml")).find("summary") if "summary/summary.xml" in archive.namelist() else None
        # The forms carry no modification date; Form Filler stamps form.fo in the archive when it saves.
        modified = datetime(*archive.getinfo("form.fo").date_time).isoformat()
    fo = parse_fo(read_form_fo(form_path(name)))
    params = fo.find(f".//{{{FM}}}formparams")
    return {"title": attribute_or_default(summary, "title"), "subject": attribute_or_default(summary, "subject"),
            "author": attribute_or_default(summary, "author"), "keywords": attribute_or_default(summary, "keywords"),
            "comment": attribute_or_default(summary, "comment"), "code": attribute_or_default(summary, "formcode"),
            "form_id": attribute_or_default(params, "form-id") or attribute_or_default(summary, "form-id"),
            "created": attribute_or_default(params, "creationdate"), "modified": modified,
            "version": attribute_or_default(params, "xmlcore-version"), "scripts": len(binds_with_unsupported_scripts(fo)),
            "actions": count_never_run_scripts(fo), "downloaded_lists": has_downloaded_lists(fo)}


def filled_form(session, key=None, cert=None):
    doc = publisher_layer(parse_fo(read_form_fo(form_path(session["name"]))))
    remove_data_objects(doc)
    etree.SubElement(doc, f"{{{DS}}}Object").append(copy.deepcopy(engine.data_element(session["doc"])))
    if key is None:
        return etree.tostring(doc, xml_declaration=True, encoding="UTF-8")
    return zfo_sign.sign_form(etree.tostring(doc), key, cert, "sha256")


def stamped_with_saving_time(zip_info):
    stamped = copy.copy(zip_info)
    stamped.date_time = time.localtime()[:6]
    return stamped


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
    path = form_path(session["name"])
    xml = etree.tostring(engine.data_element(session["doc"]), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return {"name": f"{path.stem if SINGLE_FILE else session['name']}-data.xml",
            "folder": str(path.parent if SINGLE_FILE else ROOT / "out"),
            "xml": xml.decode("utf-8")}


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
        target = form_path(name) if SINGLE_FILE else ROOT / "out" / f"{name}-ulozeny.zfo"
    write_zfo(name, fo, target)
    return {"saved": str(target), "signatures": zfo_signatures(fo)}


def first_query_value(query, key, default=""):
    return parse_qs(query).get(key, [default])[0]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # A windowed Windows build has no stderr.
        if sys.stderr:
            super().log_message(format, *args)

    def _send_response(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
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

    def _serve_session_pdf(self, query):
        session = SESSIONS.get(first_query_value(query, "s"))
        if session is None or "pdf" not in session:
            return self._send_not_found()
        return self._send_response(200, session["pdf"], "application/pdf")

    def _serve_form_list(self):
        links = "".join(f'<li><a href="/?form={quote(name)}">{name}</a></li>' for name in sorted(FORMS))
        self._send_response(200, f"<meta charset=utf-8><h1>Formuláře</h1><ul>{links}</ul>".encode(), HTML_UTF8)

    def _serve_form_page(self, name, query):
        try:
            data = IMPORTS.pop(first_query_value(query, "import"), None)
            page = (render_pdf_page(name) if form_path(name).suffix.lower() == ".pdf"
                    else render_zfo_page(name, first_query_value(query, "blank") == "1", data))
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
        if url.path == "/pdf":
            return self._serve_session_pdf(url.query)
        if url.path == "/identities":
            return self._send_json(certstore.signing_identities())
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

    def _export_or_import_data(self, session, path, body):
        try:
            result = export_data(session) if path == "/export-data" else import_data(session, body)
        except (ValueError, OSError) as error:
            return self._send_error_message(error)
        return self._send_json(result)

    def do_POST(self):
        if self._is_foreign_request():
            return self._send_response(403, b"forbidden", PLAIN_TEXT)
        url = urlparse(self.path)
        session = SESSIONS.get(first_query_value(url.query, "s"))
        if session is None:
            return self._send_response(404, "Relace vypršela, načti formulář znovu".encode(), PLAIN_TEXT_UTF8)
        if url.path == "/add-certificate":
            return self._add_certificate()
        body = self._read_body()
        if url.path == "/revocation":  # outside the lock: it may wait for a download, the form must stay usable
            return self._report_revocation(body)
        with LOCK:
            if url.path == "/ipc":
                return self._answer_engine_message(session, body)
            if url.path == "/save":
                return self._save_zfo(session, url.query, body)
            if url.path in ("/pdf-save", "/pdf-print"):
                return self._save_or_print_pdf(session, url, body)
            if url.path in ("/export-data", "/import-data"):
                return self._export_or_import_data(session, url.path, body)
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
