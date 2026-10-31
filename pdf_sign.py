"""Filling and signing PDF forms (AcroForm). Every change is an incremental update, so earlier signatures stay valid."""
import io
import logging
import struct
from datetime import timezone
from pathlib import Path

from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from pyhanko.pdf_utils.font import opentype
from pyhanko.pdf_utils.font.opentype import GlyphAccumulatorFactory
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign import fields, signers
from pyhanko.sign.validation import validate_pdf_signature, validate_pdf_timestamp
from pyhanko.sign.validation.status import SignatureCoverageLevel
from pyhanko.stamp import TextStampStyle
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    StreamObject,
)

# Trust is judged by certstore.trust; pyHanko's own path building only logs noise about it.
logging.getLogger("pyhanko").setLevel(logging.CRITICAL)
logging.getLogger("pyhanko_certvalidator").setLevel(logging.CRITICAL)
SIGNATURE_STAMP = "1.2.840.113549.1.9.16.2.14"  # id-aa-signatureTimeStampToken
STAMP_FONT = Path("/System/Library/Fonts/Supplemental/Arial.ttf")

# pyHanko writes glyph widths and kerning in font units, but PDF counts them in thousandths of an em, so a TrueType
# font with 2048 units per em (Arial) comes out letter-spaced. Shaping and widths are rescaled to 1000 units per em.
_accumulator_init = opentype.GlyphAccumulator.__init__
_glyph_width = opentype.GlyphAccumulator._get_cid_and_width


def _accumulator_init_per_mille(self, *args, **kwargs):
    _accumulator_init(self, *args, **kwargs)
    self.font_units_per_em = self.units_per_em
    self.hb_font.scale = (1000, 1000)
    self.units_per_em = 1000


def _glyph_width_per_mille(self, glyph_id):
    if glyph_id in self._glyphs:
        return self._glyphs[glyph_id]
    cid, width = _glyph_width(self, glyph_id)
    self._glyphs[glyph_id] = result = cid, round(width * 1000 / self.font_units_per_em)
    return result


opentype.GlyphAccumulator.__init__ = _accumulator_init_per_mille
opentype.GlyphAccumulator._get_cid_and_width = _glyph_width_per_mille


def form_fields(pdf):
    """Fillable fields by full name with their current values; signature fields are left out."""
    reader = PdfReader(io.BytesIO(pdf))
    return {name: field.get("/V") for name, field in (reader.get_fields() or {}).items() if field.get("/FT") != "/Sig"}


class _IncrementWriter(PdfWriter):
    """Writes the incremental update itself, for two cases pypdf gets wrong:
    - after a classic xref table pypdf adds a cross-reference stream, which macOS (CoreGraphics, Preview) cannot
      read; such files get a classic table again;
    - pypdf refuses encrypted files. Government forms are often encrypted with an owner password only (anyone may
      open and fill them); the changed objects are encrypted with the document's own key."""

    def write_stream(self, stream):
        if not (self.incremental and self._reader.is_encrypted):
            return super().write_stream(stream)
        self._reader.stream.seek(0)
        stream.write(self._reader.stream.read(-1))
        if self.list_objects_in_increment():
            self._write_increment(stream)

    def _write_increment(self, stream):
        reader = self._reader
        start = reader._startxref
        reader.stream.seek(start)
        classic = reader.stream.read(4) == b"xref"
        encryption = reader._encryption if reader.is_encrypted else None
        if not classic and encryption is None:
            return super()._write_increment(stream)
        positions = {}
        for i, obj in enumerate(self._objects):
            if obj is not None and (i >= len(self._original_hash) or obj.hash_bin() != self._original_hash[i]):
                positions[i + 1] = stream.tell()
                stream.write(f"{i + 1} 0 obj\n".encode())
                (encryption.encrypt_object(obj, i + 1, 0) if encryption else obj).write_to_stream(stream)
                stream.write(b"\nendobj\n")
        trailer = DictionaryObject({NameObject("/Root"): self.root_object.indirect_reference,
                                    NameObject("/Prev"): NumberObject(start)})
        if self._info is not None:
            trailer[NameObject("/Info")] = self._info.indirect_reference
        if self._ID:
            trailer[NameObject("/ID")] = self._ID
        if encryption is not None:
            trailer[NameObject("/Encrypt")] = reader.trailer.raw_get("/Encrypt")
        xref = stream.tell()
        if classic:
            trailer[NameObject("/Size")] = NumberObject(len(self._objects) + 1)
            stream.write(b"xref\n")
            ids = sorted(positions)
            while ids:
                run = [ids.pop(0)]
                while ids and ids[0] == run[-1] + 1:
                    run.append(ids.pop(0))
                stream.write(f"{run[0]} {len(run)}\n".encode())
                stream.write(b"".join(f"{positions[i]:010d} 00000 n\r\n".encode() for i in run))
            stream.write(b"trailer\n")
            trailer.write_to_stream(stream)
        else:
            # A cross-reference stream is never encrypted; it lists itself too.
            own = len(self._objects) + 1
            positions[own] = xref
            ids = sorted(positions)
            trailer.update({NameObject("/Type"): NameObject("/XRef"), NameObject("/Size"): NumberObject(own + 1),
                            NameObject("/W"): ArrayObject([NumberObject(1), NumberObject(4), NumberObject(1)]),
                            NameObject("/Index"): ArrayObject(NumberObject(n) for i in ids for n in (i, 1))})
            table = StreamObject()
            table.update(trailer)
            table.set_data(b"".join(struct.pack(">BIB", 1, positions[i], 0) for i in ids))
            stream.write(f"{own} 0 obj\n".encode())
            table.flate_encode().write_to_stream(stream)
            stream.write(b"\nendobj\n")
        stream.write(f"\nstartxref\n{xref}\n%%EOF\n".encode())


def fill(pdf, values):
    """Writes field values ({full name: str | bool}) as an incremental update with regenerated appearances."""
    reader = PdfReader(io.BytesIO(pdf))
    writer = _IncrementWriter(reader, incremental=True)
    known = reader.get_fields() or {}
    touched = []
    for page in writer.pages:
        page_values = {}
        for annot in page.get("/Annots") or []:
            widget = annot.get_object()
            name = _full_name(widget)
            if name not in values or name not in known:
                continue
            value = values[name]
            if known[name].get("/FT") == "/Btn":
                states = [s for s in widget.get("/AP", {}).get("/N", {}) if s != "/Off"]
                on = states[0] if states else "/Yes"
                # A radio group passes the chosen export value, a checkbox true/false.
                value = (on if value is True else "/Off") if isinstance(value, bool) else f"/{str(value).lstrip('/')}"
            elif "/AP" in widget:
                # pypdf would rewrite the existing appearance stream in place. That stream belongs to an earlier,
                # possibly signed revision, and changing it counts as altering signed content; a new one does not.
                del widget["/AP"]
            page_values[name] = value
            touched.append((widget, set(widget)))
        if page_values:
            writer.update_page_form_field_values(page, page_values, auto_regenerate=False)
    # pypdf also copies resources (/DR, /DA) onto the widgets; a signature validator sees anything beyond the
    # value and appearance as a change to signed content.
    for widget, keys in touched:
        for key in set(widget) - keys - {"/AP", "/AS", "/V"}:
            del widget[key]
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _full_name(widget):
    parts, node = [], widget
    while node is not None:
        if "/T" in node:
            parts.append(str(node["/T"]))
        node = node.get("/Parent")
        node = node.get_object() if node is not None else None
    return ".".join(reversed(parts))


class KeychainSigner(signers.Signer):
    """pyHanko signer whose private key stays in the macOS keychain or token (see keychain.KeychainKey)."""

    def __init__(self, key, cert):
        self.key = key
        der = cert.public_bytes(serialization.Encoding.DER)
        super().__init__(signing_cert=asn1_x509.Certificate.load(der),
                         cert_registry=SimpleCertificateStore.from_certs([asn1_x509.Certificate.load(der)]),
                         prefer_pss=False)

    async def async_sign_raw(self, data, digest_algorithm, dry_run=False):
        if dry_run:
            return bytes(self.signing_cert.public_key.bit_size // 8)
        return self.key.sign(data, padding.PKCS1v15(), hashes.SHA256())


def _open(pdf):
    """pyHanko reader; a document encrypted with an owner password only opens with an empty user password."""
    reader = PdfFileReader(io.BytesIO(pdf), strict=False)
    if reader.encrypted:
        reader.decrypt("")
    return reader


def empty_signature_field(pdf):
    """Name of the form's first unsigned signature field, where the signature appears; None signs invisibly."""
    reader = _open(pdf)
    for name, _value, _ in fields.enumerate_sig_fields(reader, filled_status=False):
        return name
    return None


def sign(pdf, key, cert, reason=None, location=None, placement=None):
    """Signs into the form's empty signature field. Without one, a new field is added: visible where `placement`
    ({"page": 0-based index, "box": [x1, y1, x2, y2] in PDF points}) puts it, otherwise invisible."""
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf), strict=False)
    if writer.prev.encrypted:
        writer.encrypt("")
    field = empty_signature_field(pdf)
    new_field = None
    if field is None:
        field = f"Podpis{len(signatures(pdf)) + 1}"
        new_field = (fields.SigFieldSpec(field, on_page=int(placement["page"]), box=tuple(map(float, placement["box"])))
                     if placement else fields.SigFieldSpec(field))
    meta = signers.PdfSignatureMetadata(field_name=field, md_algorithm="sha256", reason=reason, location=location,
                                        subfilter=fields.SigSeedSubFilter.PADES)
    stamp = TextStampStyle(stamp_text="Elektronicky podepsal(a):\n%(signer)s\nDatum: %(ts)s",
                           timestamp_format="%d. %m. %Y %H:%M:%S %Z",
                           text_box_style=TextBoxStyle(font=GlyphAccumulatorFactory(STAMP_FONT)) if STAMP_FONT.exists() else TextBoxStyle())
    out = signers.PdfSigner(meta, KeychainSigner(key, cert), new_field_spec=new_field, stamp_style=stamp)
    return out.sign_pdf(writer).getvalue()


def _evaluate(sig):
    """(intact, coverage name, modified, changes_unchecked) of one signature."""
    unchecked = False
    try:
        status = validate_pdf_signature(sig, skip_diff=False)
    except Exception:
        # pyHanko's analysis of later revisions gives up on documents it cannot read, such as a field named "El.podpis":
        # that says nothing about the signed bytes. A hostile file can provoke it too, so later changes stay a warning.
        unchecked = True
        try:
            status = validate_pdf_signature(sig, skip_diff=True)
        except Exception:
            return False, None, False, False
    intact = status.intact and status.valid
    covers = status.coverage.name
    modified = status.modification_level is not None and status.modification_level.name not in (
        "NONE", "LTA_UPDATES", "FORM_FILLING")
    return intact, covers, modified, unchecked and intact and covers != "ENTIRE_FILE"


def _stamp_details(status, kind):
    """What a time stamp's own check found; whether its authority counts is judged later, against the trusted lists."""
    if status is None or status.signing_cert is None or not status.intact or not status.valid:
        return {"kind": kind, "intact": False, "at": None, "issuer": "", "der": None}
    cert = status.signing_cert
    return {"kind": kind, "intact": True, "at": status.timestamp.astimezone(timezone.utc).isoformat(),
            "issuer": cert.subject.native.get("common_name") or cert.subject.human_friendly, "der": cert.dump()}


def _signature_stamp(sig):
    """The time stamp inside a signature, proving the signature existed by then; None when it has none."""
    try:  # no fetching: the page must not wait for the network, and trust comes from the trusted lists
        attributes = sig.signer_info["unsigned_attrs"]
        if not attributes.native or not any(a["type"].dotted == SIGNATURE_STAMP for a in attributes):
            return None
        status = validate_pdf_signature(sig, ts_validation_context=ValidationContext(allow_fetching=False), skip_diff=True)
        return _stamp_details(status.timestamp_validity, "signature")
    except Exception:  # a damaged token only means the time is not confirmed, the signature is judged apart from it
        return _stamp_details(None, "signature")


def _document_stamp(sig):
    """A stamp over the file as it was, recorded in the PDF as one more signature. (stamp details, status or None)"""
    try:
        status = validate_pdf_timestamp(sig, validation_context=ValidationContext(allow_fetching=False))
        return _stamp_details(status, "document"), status
    except Exception:
        return _stamp_details(None, "document"), None


def _stamp_covers(stamp, status, sig):
    """Did this document stamp hash the signature? Where the stamp's object sits in the file proves nothing, it can be
    rewritten into a later revision: only the bytes it hashed count, and in one block, so no hole can skip the signature."""
    if status is None or status.coverage < SignatureCoverageLevel.CONTIGUOUS_BLOCK_FROM_START:
        return False
    try:
        return sum(list(stamp.byte_range)[2:]) >= sum(list(sig.byte_range)[2:])
    except TypeError:  # a hostile range need not hold numbers
        return False


def _document_stamp_entry(sig, stamp, status):
    cert = sig.signer_cert
    return {
        "document_stamp": True,
        "subject": cert.subject.native.get("common_name") or cert.subject.human_friendly,
        "issuer": cert.issuer.native.get("common_name") or cert.issuer.human_friendly,
        "publisher": False,
        "intact": stamp["intact"],
        "modified": False,
        "changes_unchecked": False,
        "not_after": cert["tbs_certificate"]["validity"]["not_after"].native.isoformat(),
        "signed_at": stamp["at"],
        "field": sig.field_name,
        "covers_whole": status is not None and status.coverage.name == "ENTIRE_FILE",
        "der": cert.dump(),
        "stamps": [stamp],
    }


def signatures(pdf):
    """One dict per signature or document time stamp, in file order, shaped like zfo_verify.describe."""
    reader = _open(pdf)
    result = []
    found = reader.embedded_signatures
    document_stamps = [(sig, *_document_stamp(sig)) for sig in found if sig.sig_object_type == "/DocTimeStamp"]
    for sig in found:
        if sig.sig_object_type == "/DocTimeStamp":
            result.append(_document_stamp_entry(*next(entry for entry in document_stamps if entry[0] is sig)))
            continue
        cert = sig.signer_cert
        intact, covers, modified, unchecked = _evaluate(sig)
        signed_at = sig.self_reported_timestamp
        result.append({
            "subject": cert.subject.native.get("common_name") or cert.subject.human_friendly,
            "issuer": cert.issuer.native.get("common_name") or cert.issuer.human_friendly,
            "publisher": False,
            "intact": intact,
            # Later revisions change more than form fields, signatures and appearances. pyHanko also reports this
            # when a field with several widgets is filled after signing, so it is a warning, not a broken signature.
            "modified": modified,
            "changes_unchecked": unchecked,
            "not_after": cert["tbs_certificate"]["validity"]["not_after"].native.isoformat(),
            "signed_at": signed_at.astimezone(timezone.utc).isoformat() if signed_at else None,
            "field": sig.field_name,
            "covers_whole": covers == "ENTIRE_FILE",
            "der": cert.dump(),
            "stamps": [stamp for stamp in (_signature_stamp(sig), *(dict(details) for later, details, status in document_stamps
                                                                    if _stamp_covers(later, status, sig)))
                       if stamp is not None],
        })
    return result
