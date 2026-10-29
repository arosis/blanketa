"""Local web app: render a 602XML form on the Mac, let the user fill it, then save it signed.

The form logic (calculations, constraints, repeating sections) runs in zfoengine, the
Python 3 port of CZ.NIC zfo-editor's backend; the page talks to it through /ipc.

usage: app.py [PORT]               opens forms from samples/*/form.zfo, saves to out/
       app.py --file PATH [PORT]   opens one form, saves the signed copy next to it
PORT 0 picks a free port; the chosen one is printed as "PORT <n>".
"""
import io, os, re, json, sys, copy, base64, secrets, tempfile, threading, zipfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, quote
from lxml import etree
from cryptography import x509

import certstore
import pdf_sign
import zfo_sign
import zfo_verify
from zfoengine import api as engine

ROOT = Path(__file__).resolve().parent
RESOURCES = Path(getattr(sys, "_MEIPASS", ROOT))
EDITOR = RESOURCES / "vendor/zfo-editor-src/zfo-editor-0.3.2"
PDFJS = RESOURCES / "vendor/pdfjs"
DS = "http://www.w3.org/2000/09/xmldsig#"
XSLT = etree.XSLT(etree.parse(str(RESOURCES / "ui/fo2html.xsl")))
FORMS = {}
SINGLE_FILE = False
SESSIONS = {}
IMPORTS = {}
LOCK = threading.Lock()
# Sessions hold a whole document each; a window only ever uses its newest few (reload, reset, import).
MAX_SESSIONS = 8

SHELL = (RESOURCES / "ui/shell.html").read_text()
PDF_PAGE = (RESOURCES / "ui/pdf.html").read_text().replace(
    "__SHELL_STYLE__", re.search(r"<style>.*?</style>", SHELL, re.S).group(0).replace("__FONTS__", ""))


def js(value):
    """JSON for a <script> block: a form's own texts (title, signer names, button captions) cannot end the script."""
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
    candidate, n = source.with_name(f"{stem}-podepsany{source.suffix}"), 2
    while candidate.exists() and candidate != source:
        candidate, n = source.with_name(f"{stem}-podepsany-{n}{source.suffix}"), n + 1
    return candidate


def read_fo(path):
    return zipfile.ZipFile(path).read("form.fo")


def publisher_layer(doc):
    """Peel user signature layers off so the form can be re-filled and signed again."""
    while not is_publisher_signature(doc):
        doc = doc.find(f"{{{DS}}}Object/{{{DS}}}Signature")
    return doc


def is_publisher_signature(sig):
    cert = sig.find(f"{{{DS}}}KeyInfo//{{{DS}}}X509Certificate").text
    subject = x509.load_der_x509_certificate(base64.b64decode(cert)).subject.rfc4514_string()
    return "602XML" in subject


def render_empty():
    """The application window before a form is opened (Mac app started without a file)."""
    return SHELL.replace("__SESSION__", "").replace("__FILE__", '""').replace("__FONTS__", "").replace("__EMPTY__", "true")


def pdf_signatures(pdf):
    found = pdf_sign.signatures(pdf)
    for sig, verdict in zip(found, certstore.trust([sig.pop("der") for sig in found])):
        sig.update(trusted=verdict["trusted"], reason=verdict.get("reason", ""))
    return found


def pdf_info(pdf):
    reader = pdf_sign.PdfReader(io.BytesIO(pdf))
    meta = reader.metadata or {}
    return {"title": meta.get("/Title") or "", "author": meta.get("/Author") or "", "pages": len(reader.pages),
            "fields": len(pdf_sign.form_fields(pdf)), "producer": meta.get("/Producer") or "",
            "signature_field": pdf_sign.empty_signature_field(pdf)}


def render_pdf(name):
    """PDF forms are shown by PDF.js; the file itself is fetched from /pdf."""
    pdf = form_path(name).read_bytes()
    session = new_session({"name": name, "pdf": pdf})
    return (PDF_PAGE.replace("__SESSION__", session)
            .replace("__FILE__", js(form_path(name).name))
            .replace("__SIGNATURES__", js(pdf_signatures(pdf)))
            .replace("__INFO__", js(pdf_info(pdf))))


def save_pdf(session, values, identity_id=None, placement=None, password=None):
    """Field values go in as an incremental update, so earlier signatures stay valid. Signed: a new
    "-podepsany" file, which the window then continues with. Unsigned: the open file itself."""
    name = session["name"]
    pdf = pdf_sign.fill(session["pdf"], values) if values else session["pdf"]
    if identity_id:
        pdf = pdf_sign.sign(pdf, *signer(identity_id, password), placement=placement)
        target = output_path(name)
    else:
        target = form_path(name) if SINGLE_FILE else ROOT / "out" / f"{name}-ulozeny.pdf"
    target.write_bytes(pdf)
    session["pdf"] = pdf
    if SINGLE_FILE:
        FORMS[name] = target
    return {"saved": str(target), "signatures": pdf_signatures(pdf), "signature_field": pdf_sign.empty_signature_field(pdf)}


def print_pdf(session, values):
    """The filled PDF as a temporary file for the system print panel."""
    pdf = pdf_sign.fill(session["pdf"], values) if values else session["pdf"]
    target = Path(tempfile.gettempdir()) / f"zfo-tisk-{secrets.token_hex(4)}-{form_path(session['name']).name}"
    target.write_bytes(pdf)
    return {"path": str(target)}


def render(name, blank=False, data=None):
    fo = read_fo(form_path(name))
    if blank or data is not None:
        # Form Filler's "Obnovit výchozí hodnoty" (no data) or "Import dat do formuláře" (given data):
        # the form as published, with the user's signatures and old data removed
        stripped = publisher_layer(etree.fromstring(fo, etree.XMLParser(huge_tree=True)))
        for old in stripped.xpath("dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
            stripped.remove(old)
        if data is not None:
            etree.SubElement(stripped, f"{{{DS}}}Object").append(data)
        fo = etree.tostring(stripped)
    session = new_session({"name": name, "doc": engine.open_document(fo)})
    doc = etree.fromstring(fo, etree.XMLParser(huge_tree=True))
    for hidden in doc.xpath("//dsig:SignatureProperties | //dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
        hidden.getparent().remove(hidden)
    html = etree.tostring(XSLT(etree.ElementTree(doc), PATH_TO_JS="'/static/scripts/'",
                               PATH_TO_CSS="'/static/styles/'"), method="html", encoding="unicode")
    shell = (SHELL.replace("__SESSION__", session)
             .replace("__FILE__", js(form_path(name).name if SINGLE_FILE else name))
             .replace("__SIGNATURES__", js(signatures(fo)))
             .replace("__PAGE__", js(page_geometry(doc)))
             .replace("__COLORS__", js(field_colors(doc)))
             .replace("__INFO__", js(form_info(name)))
             .replace("__BLANK__", js(blank))
             .replace("__FONTS__", embedded_fonts(doc))
             .replace("__BUTTONS__", js(user_buttons(doc)))
             .replace("__CODES__", js(code_lists(doc)))
             .replace("__EMPTY__", "false"))
    return html.replace("</body>", shell + "</body>", 1)


def code_lists(doc):
    """Bindings of the lists that show their item's code rather than its label (form602displaycode)."""
    return sorted({cell.get("form602bind") for cell in doc.iter("{http://www.w3.org/1999/XSL/Format}table-cell")
                   if cell.get("form602displaycode") == "true" and cell.get("form602bind")})


def user_buttons(doc):
    """Designer's toolbar buttons (fm:userbutton1…n in fm:formsettings), in their numbered order."""
    settings = doc.find(".//{http://software602.cz/forms}formsettings")
    found = []
    for el in (settings if settings is not None else []):
        number = re.fullmatch(r"userbutton(\d+)", etree.QName(el).localname if isinstance(el.tag, str) else "")
        if number and el.get("express"):
            found.append((int(number.group(1)), {"caption": el.get("caption", ""), "tooltip": el.get("tooltip", ""),
                                                 "icon": el.get("iconid", ""), "text": el.get("showtext", "yes") != "no",
                                                 "express": el.get("express")}))
    return [button for _, button in sorted(found, key=lambda pair: pair[0])]


def embedded_fonts(doc):
    """@font-face rules for TrueType fonts the form carries in fm:fonts. They land in a <style> block, so a font
    whose name or data could close it is left out."""
    rules = []
    for font in doc.iter("{http://software602.cz/forms}font"):
        name, data = font.get("name"), "".join((font.text or "").split())
        if name and data and re.fullmatch(r"[\w .-]+", name) and re.fullmatch(r"[A-Za-z0-9+/]+=*", data):
            rules.append(f'@font-face {{ font-family: {json.dumps(name)}; src: url(data:font/ttf;base64,{data}) format("truetype"); }}')
    return "\n  ".join(rules)


def field_colors(doc):
    """Background colours of editable and read-only fields as set by the form designer."""
    params = doc.find(".//{http://software602.cz/forms}formparams")
    get = lambda attr, default: params.get(attr, default) if params is not None else default
    return {"field": get("proprequiredformfieldbkcolor", "rgb(255,255,180)"),
            "readonly": get("proproformfieldbkcolor", "rgb(220,220,220)")}


def page_geometry(doc):
    """Paper size and margins from the form's first fo:simple-page-master, as CSS."""
    master = next(doc.iter("{http://www.w3.org/1999/XSL/Format}simple-page-master"), None)
    if master is None:
        return {}
    margin = lambda side: master.get(f"margin-{side}", "15mm")
    return {"width": master.get("page-width", "21cm"),
            "padding": " ".join(margin(s) for s in ("top", "right", "bottom", "left"))}


def signatures(fo):
    """Signature layers of a form.fo with integrity and the system's trust verdicts, outermost first."""
    found = zfo_verify.describe(etree.fromstring(fo, etree.XMLParser(huge_tree=True)))
    for sig, verdict in zip(found, certstore.trust([sig.pop("der") for sig in found])):
        sig.update(trusted=verdict["trusted"], reason=verdict.get("reason", ""))
    return found


def signer(identity_id, password=None):
    return certstore.load(identity_id, password)


def form_info(name):
    """Form description as Form Filler shows it under "Popis formuláře"."""
    with zipfile.ZipFile(form_path(name)) as z:
        summary = etree.fromstring(z.read("summary/summary.xml")).find("summary") if "summary/summary.xml" in z.namelist() else None
    fo = etree.fromstring(read_fo(form_path(name)), etree.XMLParser(huge_tree=True))
    params = fo.find(".//{http://software602.cz/forms}formparams")
    get = lambda el, attr: el.get(attr, "") if el is not None else ""
    return {"title": get(summary, "title"), "author": get(summary, "author"), "form_id": get(params, "form-id"),
            "created": get(params, "creationdate"), "version": get(params, "xmlcore-version")}


def filled_form(session, key=None, cert=None):
    """form.fo bytes with the session's data under the publisher signature, signed by the user when a key is given."""
    doc = publisher_layer(etree.fromstring(read_fo(form_path(session["name"])), etree.XMLParser(huge_tree=True)))
    for stale in doc.xpath("dsig:Object[not(@Id)]", namespaces={"dsig": DS}):
        doc.remove(stale)
    etree.SubElement(doc, f"{{{DS}}}Object").append(copy.deepcopy(engine.data_element(session["doc"])))
    if key is None:
        return etree.tostring(doc, xml_declaration=True, encoding="UTF-8")
    return zfo_sign.sign(etree.tostring(doc), key, cert, "sha256")


def write_zfo(name, fo, target):
    out = io.BytesIO()
    with zipfile.ZipFile(form_path(name)) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in zin.infolist():
            data = fo if info.filename == "form.fo" else zin.read(info.filename)
            zout.writestr(info, data, compress_type=info.compress_type)
    target.write_bytes(out.getvalue())


def export_data(session):
    """Form Filler's "Uložit data do souboru": the filled data instance as XML, with a suggested file name and folder.
    The user picks where to save it."""
    path = form_path(session["name"])
    xml = etree.tostring(engine.data_element(session["doc"]), xml_declaration=True, encoding="UTF-8", pretty_print=True)
    return {"name": f"{path.stem if SINGLE_FILE else session['name']}-data.xml",
            "folder": str(path.parent if SINGLE_FILE else ROOT / "out"),
            "xml": xml.decode("utf-8")}


def import_data(session, xml):
    """Form Filler's "Import dat do formuláře": checks the XML matches the form's data model, returns a reload token."""
    try:
        data = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, no_network=True))
    except etree.XMLSyntaxError as e:
        raise ValueError(f"Soubor není platné XML: {e}")
    expected = engine.data_element(session["doc"])
    if data.tag != expected.tag:
        raise ValueError(f"Data patří k jinému formuláři (kořen {etree.QName(data).localname}, "
                         f"formulář čeká {etree.QName(expected).localname})")
    token = secrets.token_urlsafe(12)
    IMPORTS[token] = data
    return {"import": token}


def save(session, identity_id=None, password=None):
    """Signed: a new "-podepsany" file next to the form. Unsigned (Form Filler's "Uložit"): the open file itself."""
    name = session["name"]
    if identity_id:
        fo = filled_form(session, *signer(identity_id, password))
        target = output_path(name)
    else:
        fo = filled_form(session)
        target = form_path(name) if SINGLE_FILE else ROOT / "out" / f"{name}-ulozeny.zfo"
    write_zfo(name, fo, target)
    return {"saved": str(target), "signatures": signatures(fo)}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # A windowed Windows build has no stderr.
        if sys.stderr:
            super().log_message(format, *args)

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _foreign(self):
        """Any web page in the user's browser can reach localhost. A request whose Host is not this server (DNS
        rebinding) or a POST from another origin (a forged form) is refused before it reads or changes anything."""
        port = self.server.server_address[1]
        own = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host") not in own:
            return True
        origin = self.headers.get("Origin")
        return self.command == "POST" and origin is not None and origin not in {f"http://{h}" for h in own}

    def do_GET(self):
        if self._foreign():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        if url.path.startswith("/static/"):
            path = (EDITOR / url.path[len("/static/"):]).resolve()
            if EDITOR in path.parents and path.is_file():
                ctype = {".js": "text/javascript", ".css": "text/css", ".png": "image/png"}.get(path.suffix, "application/octet-stream")
                return self._send(200, path.read_bytes(), ctype)
            return self._send(404, b"not found", "text/plain")
        if url.path.startswith("/pdfjs/"):
            path = (PDFJS / url.path[len("/pdfjs/"):]).resolve()
            if PDFJS in path.parents and path.is_file():
                ctype = {".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml",
                         ".png": "image/png"}.get(path.suffix, "application/octet-stream")
                return self._send(200, path.read_bytes(), ctype)
            return self._send(404, b"not found", "text/plain")
        if url.path == "/bridge.js":
            # Started by the Windows/Linux app (desktop.py): pages talk to it through pywebview.
            flag = b"window.ZFO_DESKTOP = true;\n" if os.environ.get("ZFO_DESKTOP") else b""
            return self._send(200, flag + (RESOURCES / "ui/bridge.js").read_bytes(), "text/javascript")
        if url.path == "/pdf":
            session = SESSIONS.get(parse_qs(url.query).get("s", [""])[0])
            if session is None or "pdf" not in session:
                return self._send(404, b"not found", "text/plain")
            return self._send(200, session["pdf"], "application/pdf")
        if url.path == "/identities":
            return self._send(200, json.dumps(certstore.identities(), ensure_ascii=False).encode(), "application/json")
        name = parse_qs(url.query).get("form", [None])[0]
        if not name and SINGLE_FILE and not FORMS:
            return self._send(200, render_empty().encode(), "text/html; charset=utf-8")
        if not name and SINGLE_FILE:
            name = next(iter(FORMS))
        if not name:
            links = "".join(f'<li><a href="/?form={quote(n)}">{n}</a></li>' for n in sorted(FORMS))
            return self._send(200, f"<meta charset=utf-8><h1>Formuláře</h1><ul>{links}</ul>".encode(), "text/html; charset=utf-8")
        try:
            query = parse_qs(url.query)
            data = IMPORTS.pop(query.get("import", [""])[0], None)
            page = (render_pdf(name) if form_path(name).suffix.lower() == ".pdf"
                    else render(name, query.get("blank", [""])[0] == "1", data))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        except FileNotFoundError:
            self._send(404, b"form not found", "text/plain")

    def do_POST(self):
        if self._foreign():
            return self._send(403, b"forbidden", "text/plain")
        url = urlparse(self.path)
        session = SESSIONS.get(parse_qs(url.query).get("s", [""])[0])
        if session is None:
            return self._send(404, "Relace vypršela, načti formulář znovu".encode(), "text/plain; charset=utf-8")
        if url.path == "/add-certificate":
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            try:
                certstore.add_pfx(base64.b64decode(request["data"]), request.get("password") or "")
            except (KeyError, ValueError, OSError) as e:
                return self._send(400, str(e).encode(), "text/plain; charset=utf-8")
            return self._send(200, json.dumps(certstore.identities(), ensure_ascii=False).encode(), "application/json")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with LOCK:
            if url.path == "/ipc":
                msg = json.loads(body)
                responses = engine.handle(session["doc"], msg.get("type"), msg.get("data"))
                return self._send(200, json.dumps(responses, ensure_ascii=False).encode(), "application/json")
            if url.path == "/save":
                try:
                    result = save(session, parse_qs(url.query).get("cert", [""])[0] or None,
                                  json.loads(body or b"{}").get("password"))
                except (LookupError, RuntimeError, ValueError, OSError) as e:
                    return self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                return self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")
            if url.path in ("/pdf-save", "/pdf-print"):
                try:
                    request = json.loads(body or b"{}")
                    values = request.get("values") or {}
                    result = (print_pdf(session, values) if url.path == "/pdf-print"
                              else save_pdf(session, values, parse_qs(url.query).get("cert", [""])[0] or None,
                                            request.get("placement"), request.get("password")))
                except (LookupError, RuntimeError, ValueError, OSError) as e:
                    return self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                return self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")
            if url.path in ("/export-data", "/import-data"):
                try:
                    result = export_data(session) if url.path == "/export-data" else import_data(session, body)
                except (ValueError, OSError) as e:
                    return self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                return self._send(200, json.dumps(result, ensure_ascii=False).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


def watch_parent():
    """End together with the app that started this server, even when it is killed without cleaning up."""
    if os.environ.get("ZFO_WATCH_STDIN"):
        # The desktop shell keeps our stdin open; end of input means it is gone (works on Windows too).
        # The descriptor is read directly: a windowed Windows build has no sys.stdin object.
        try:
            while os.read(0, 4096):
                pass
        except OSError:
            return
        os._exit(0)
    parent = os.getppid()
    while os.getppid() == parent:
        threading.Event().wait(2)
    os._exit(0)


def main(args):
    global SINGLE_FILE
    if args[:1] in (["--file"], ["--empty"]):
        if args[0] == "--file":
            path = Path(args[1]).expanduser().resolve()
            FORMS[path.stem] = path
            args = args[1:]
        SINGLE_FILE, args = True, args[1:]
        threading.Thread(target=watch_parent, daemon=True).start()
    else:
        FORMS.update({p.parent.name: p for p in sorted((ROOT / "samples").glob("*/form.zfo"))})
        FORMS.update({p.stem: p for p in sorted((ROOT / "samples-pdf").glob("*.pdf"))})
    server = ThreadingHTTPServer(("127.0.0.1", int(args[0]) if args else 8602), Handler)
    if sys.stdout:
        print(f"PORT {server.server_address[1]}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
