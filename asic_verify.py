import io
import re
import zipfile
import zlib
from urllib.parse import unquote, urlsplit

import xades_verify

MAX_ENTRIES = 1000
MAX_TOTAL_SIZE = 100 * 1024 * 1024
MAX_RATIO = 100

ZIP_MAGIC = b"PK"
META_INF = "META-INF/"
MANIFEST = "META-INF/manifest.xml"
ASIC_S_SIGNATURES = "META-INF/signatures.xml"
MIMETYPES = {"application/vnd.etsi.asic-s+zip": "asic-s", "application/vnd.etsi.asic-e+zip": "asic-e"}
XADES_ENTRY = re.compile(r"META-INF/[^/]*signatures[^/]*\.xml")
UNSUPPORTED_ENTRIES = {
    "CAdES": re.compile(r"META-INF/[^/]*signature[^/]*\.p7s"),
    "timestamp": re.compile(r"META-INF/[^/]*timestamp[^/]*\.tst"),
    "evidence": re.compile(r"META-INF/evidencerecord\.[^/]+"),
}
DRIVE_LETTER = re.compile(r"[A-Za-z]:")
CHUNK = 64 * 1024

ENVELOPED_CONTENT = "podepsaný obsah"
CONTENT_EXTENSIONS = {"application/pdf": ".pdf", "application/xml": ".xml", "text/xml": ".xml", "text/plain": ".txt",
                      "image/png": ".png", "image/jpeg": ".jpg"}
LABELS = {"asic-s": "ASiC-S", "asic-e": "ASiC-E"}
SIGNATURE_LABELS = {"XAdES": "s podpisy XAdES", "CAdES": "s podpisy CAdES", "timestamp": "s časovým razítkem",
                    "evidence": "se záznamem důkazů"}
STANDALONE_LABEL = "Samostatný podpis XAdES"
UNSUPPORTED = "Tento typ podpisu zatím neumíme ověřit."
UNREADABLE = "soubor s podpisem nejde přečíst"
NO_SIGNATURE = "soubor s podpisem neobsahuje žádný podpis"
MIMETYPE_MISPLACED = "Soubor mimetype není na prvním místě nebo je komprimovaný."
MIMETYPE_UNKNOWN = "Soubor mimetype neuvádí typ kontejneru ASiC."
MANIFEST_MISSING = "V kontejneru chybí seznam souborů META-INF/manifest.xml."
ASIC_S_FILES = "Kontejner ASiC-S obsahuje víc než jeden datový soubor."


class ContainerError(ValueError):
    pass


def uri_to_name(uri, names, *, single):
    if uri is None:
        return single
    if urlsplit(uri).scheme:
        return None
    try:
        name = unquote(uri, errors="strict")
    except UnicodeDecodeError:
        return None
    if name.startswith("/"):
        name = name[1:]
    if "\\" in name or name.startswith("/") or ".." in name.split("/"):
        return None
    if name.startswith(META_INF) and name != MANIFEST:
        return None
    return name if name in names else None


def _check_name(name):
    if name.startswith("/") or "\\" in name or DRIVE_LETTER.match(name) or ".." in name.split("/"):
        raise ContainerError("unsafe entry name")


def _open(data):
    try:
        # ASiC requires UTF-8 names; zipfile would otherwise read names without the UTF-8 flag as cp437.
        return zipfile.ZipFile(io.BytesIO(data), metadata_encoding="utf-8")
    except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, ValueError, EOFError, NotImplementedError) as error:
        raise ContainerError("not a readable ZIP") from error


def _signature_kinds(names):
    kinds = []
    if any(XADES_ENTRY.fullmatch(name) for name in names):
        kinds.append("XAdES")
    kinds += [kind for kind, pattern in UNSUPPORTED_ENTRIES.items() if any(pattern.fullmatch(name) for name in names)]
    return kinds


def is_signed_file(data):
    if data.startswith(ZIP_MAGIC):
        try:
            with _open(data) as archive:
                names = archive.namelist()
        except ContainerError:
            return False
        return "form.fo" not in names and bool(_signature_kinds(names))
    if b"QualifyingProperties" not in data:
        return False
    try:
        root = xades_verify.parse(data)
    except xades_verify.XadesError:
        return False
    return any(_is_xades(signature) for signature in xades_verify.find_signatures(root))


def _is_xades(signature):
    return next(signature.iter(*(f"{{{namespace}}}QualifyingProperties" for namespace in xades_verify.XADES_NAMESPACES)), None) is not None


def _check_info(info):
    _check_name(info.filename)
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        raise ContainerError("unsupported compression")
    if info.flag_bits & 1:
        raise ContainerError("encrypted entry")
    if info.file_size > MAX_RATIO * max(info.compress_size, 1):
        raise ContainerError("compression ratio over the limit")


def _read_entry(archive, info, budget):
    content = bytearray()
    with archive.open(info) as entry:
        while chunk := entry.read(CHUNK):
            content += chunk
            if len(content) > min(info.file_size, budget):
                raise ContainerError("entry larger than declared")
    return bytes(content)


def _read_entries(data):
    with _open(data) as archive:
        try:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ContainerError("too many entries")
            names = [info.filename for info in infos]
            if len(set(names)) != len(names):
                raise ContainerError("duplicate entry names")
            for info in infos:
                _check_info(info)
            if sum(info.file_size for info in infos) > MAX_TOTAL_SIZE:
                raise ContainerError("container larger than the limit")
            entries, total = [], 0
            for info in infos:
                content = b"" if info.is_dir() else _read_entry(archive, info, MAX_TOTAL_SIZE - total)
                total += len(content)
                entries.append((info, content))
            return entries
        except ContainerError:
            raise
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError, ValueError, RuntimeError, NotImplementedError) as error:
            raise ContainerError("damaged ZIP entry") from error


def _container_rules(entries):
    warnings = []
    infos = [info for info, _ in entries]
    mimetype = next(((index, info, content) for index, (info, content) in enumerate(entries) if info.filename == "mimetype"), None)
    kind = None
    if mimetype is not None:
        index, info, content = mimetype
        if index != 0 or info.compress_type != zipfile.ZIP_STORED or info.extra:
            warnings.append(MIMETYPE_MISPLACED)
        kind = MIMETYPES.get(content.decode("ascii", "replace").strip())
        if kind is None:
            warnings.append(MIMETYPE_UNKNOWN)
    data_names = [info.filename for info in infos if not info.is_dir() and info.filename != "mimetype"
                  and not info.filename.startswith(META_INF)]
    names = {info.filename for info in infos}
    if kind is None:
        kind = "asic-s" if len(data_names) == 1 and ASIC_S_SIGNATURES in names else "asic-e"
    return kind, data_names, warnings


def _unreadable_signature(reason):
    return {**xades_verify.unverified_result(reason), "references": []}


def _container_signatures(entries, resolve):
    signatures = []
    for info, content in entries:
        if not XADES_ENTRY.fullmatch(info.filename):
            continue
        try:
            found = xades_verify.find_signatures(xades_verify.parse(content))
        except xades_verify.XadesError:
            signatures.append(_unreadable_signature(UNREADABLE))
            continue
        if not found:
            signatures.append(_unreadable_signature(NO_SIGNATURE))
        signatures += [xades_verify.verify(signature, resolve) for signature in found]
    return signatures


def _describe_container(data):
    entries = _read_entries(data)
    kind, data_names, warnings = _container_rules(entries)
    contents = {info.filename: content for info, content in entries}
    kinds = _signature_kinds(contents)
    if not kinds:
        raise ContainerError("no signature in the container")
    if kind == "asic-s" and len(data_names) > 1:
        warnings.append(ASIC_S_FILES)
    if kind == "asic-e" and "XAdES" in kinds and MANIFEST not in contents:
        warnings.append(MANIFEST_MISSING)
    known = set(data_names) | ({MANIFEST} & contents.keys())
    single = data_names[0] if kind == "asic-s" and len(data_names) == 1 else None

    def resolve(uri):
        name = uri_to_name(uri, known, single=single)
        return (name, contents[name]) if name is not None else None

    signatures = _container_signatures(entries, resolve)
    unsupported = UNSUPPORTED if any(found in UNSUPPORTED_ENTRIES for found in kinds) else ""
    label = f"{LABELS[kind]} {SIGNATURE_LABELS[kinds[0]]}"
    files = [(name, contents[name]) for name in data_names]
    return _finish(kind, label, files, signatures, warnings, unsupported)


def _content_name(mime, taken):
    extension = CONTENT_EXTENSIONS.get(mime.split(";")[0].strip().lower(), ".bin")
    name, number = f"{ENVELOPED_CONTENT}{extension}", 1
    while name in taken:
        number += 1
        name = f"{ENVELOPED_CONTENT} ({number}){extension}"
    return name


def _describe_standalone(data):
    root = xades_verify.parse(data)
    found = xades_verify.find_signatures(root)
    if not any(_is_xades(signature) for signature in found):
        raise ContainerError("no XAdES signature")
    signatures = [xades_verify.verify(signature, lambda uri: None, allow_empty_uri=True, standalone=True) for signature in found]
    files = []
    for signature in signatures:
        for reference in signature["references"]:
            if "content" in reference:
                reference["name"] = _content_name(reference["mime"], {name for name, _ in files})
                files.append((reference["name"], reference["content"]))
    return _finish("xades", STANDALONE_LABEL, files, signatures, [], "")


def _file_state(name, signatures, unsupported):
    outcomes = [(signature["intact"], reference["outcome"]) for signature in signatures
                for reference in signature["references"] if reference["name"] == name]
    if any(outcome == "changed" for _, outcome in outcomes):
        return "changed"
    if any(intact and outcome == "ok" for intact, outcome in outcomes):
        return "signed"
    if outcomes:
        return "invalid"
    return "unverified" if unsupported else "unsigned"


def _finish(kind, label, files, signatures, warnings, unsupported):
    names = [name for name, _ in files]
    described = [{"name": name, "size": len(content), "state": _file_state(name, signatures, unsupported),
                  "pdf": content.startswith(b"%PDF-")} for name, content in files]
    for signature in signatures:
        references = signature.pop("references")
        signature["files"] = list(dict.fromkeys(reference["name"] for reference in references if reference["name"] in names))
    return ({"kind": kind, "label": label, "files": described, "signatures": signatures, "warnings": warnings,
             "unsupported": unsupported}, [content for _, content in files])


def describe(data):
    return _describe_container(data) if data.startswith(ZIP_MAGIC) else _describe_standalone(data)
