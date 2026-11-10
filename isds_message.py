import asyncio
import base64
import binascii
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timezone

from asn1crypto import cms, core
from lxml import etree
from pyhanko.sign.validation import generic_cms
from pyhanko_certvalidator import ValidationContext

CMS_SEQUENCE = b"\x30"
# The WS documentation writes the host as isds.czebox.cz in places; the schemas and real messages use isds.czechpoint.cz.
ISDS_NAMESPACE = re.compile(r"^http://isds\.(?:czechpoint|czebox)\.cz/v20/(message|SentMessage|delivery)$")
KINDS = {"message": "received", "SentMessage": "sent", "delivery": "receipt"}
BODIES = {"received": "dmReturnedMessage", "sent": "dmReturnedMessage", "receipt": "dmDelivery"}
WINDOWS_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
MAX_FILE_NAME = 200
# "C:x" is relative to drive C: whatever folder it is joined to, "a:b" names an NTFS stream.
WINDOWS_FORBIDDEN_CHARACTERS = re.compile(r'[:<>"|?*]')
SIGNATURE_TIME_STAMP_TOKEN_OID = "1.2.840.113549.1.9.16.2.14"
DIGITAL_AND_INFORMATION_AGENCY_ID = "NTRCZ-17651921"
INTERIOR_MINISTRY_ID = "NTRCZ-00007064"
ISDS_OPERATOR_IDS = (DIGITAL_AND_INFORMATION_AGENCY_ID, INTERIOR_MINISTRY_ID)
# What asn1crypto raises on a damaged structure; its ObjectDescriptor, InstanceOf and Real have no Python value at all.
DAMAGED_STRUCTURE = (TypeError, KeyError, AttributeError, OverflowError)
XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=True)


@dataclass
class Message:
    kind: str
    info: dict
    attachments: list = field(default_factory=list)
    signed_data: cms.SignedData = None
    content: bytes = b""
    xml_root: etree._Element = None


def looks_like_message(data):
    return data[:1] == CMS_SEQUENCE


def _children(element, name):
    return element.xpath("*[local-name()=$name]", name=name) if element is not None else []


def _child(element, name):
    found = _children(element, name)
    return found[0] if found else None


def _text(element, name):
    found = _child(element, name)
    return (found.text or "").strip() if found is not None else ""


def _number(text):
    return int(text) if text.isascii() and text.isdecimal() else None


def _party(dm, role, box_element):
    return {"name": _text(dm, f"dm{role}"), "address": _text(dm, f"dm{role}Address"), "box": _text(dm, box_element)}


def _attachment(file):
    encoded = _child(file, "dmEncodedContent")
    xml_content = _child(file, "dmXMLContent")
    if encoded is not None:
        try:
            content = base64.b64decode("".join((encoded.text or "").split()), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("attachment is not valid base64") from error
    elif xml_content is not None and len(xml_content):
        content = etree.tostring(xml_content[0], xml_declaration=True, encoding="UTF-8")
    else:
        raise ValueError("attachment without content")
    return {"name": file.get("dmFileDescr") or "", "mime": file.get("dmMimeType") or "",
            "main": file.get("dmFileMetaType") == "main"}, content


def _signed_content(data):
    try:
        info = cms.ContentInfo.load(data, strict=True)
        if info["content_type"].native != "signed_data":
            raise ValueError("not a signed message")
        signed_data = info["content"]
        encapsulated = signed_data["encap_content_info"]
        if encapsulated["content_type"].native != "data" or encapsulated["content"].native is None:
            raise ValueError("signed message without content")
        return signed_data, encapsulated["content"].native
    except DAMAGED_STRUCTURE as error:
        raise ValueError("damaged signed message") from error


def _parse_xml(content):
    try:
        tree = etree.fromstring(content, XML_PARSER).getroottree()
    except etree.XMLSyntaxError as error:
        raise ValueError("message content is not XML") from error
    if tree.docinfo.doctype or tree.docinfo.internalDTD is not None:
        raise ValueError("message content declares a DTD")
    return tree.getroot()


def read_message(data):
    if not looks_like_message(data):
        raise ValueError("not a signed message")
    signed_data, content = _signed_content(data)
    root = _parse_xml(content)
    namespace = ISDS_NAMESPACE.match(etree.QName(root).namespace or "")
    if namespace is None:
        raise ValueError("not an ISDS message")
    kind = KINDS[namespace.group(1)]
    body = _child(root, BODIES[kind])
    dm = _child(body, "dmDm")
    if dm is None:
        raise ValueError("ISDS message without its data")
    described = [_attachment(file) for file in _children(_child(dm, "dmFiles"), "dmFile")] if kind != "receipt" else []
    names = safe_file_names([description["name"] for description, _ in described])
    attachments = [{**description, "name": name, "size": len(content)} for (description, content), name in zip(described, names, strict=True)]
    info = {
        "kind": kind, "id": _text(dm, "dmID"), "subject": _text(dm, "dmAnnotation"),
        "sender": _party(dm, "Sender", "dbIDSender"), "recipient": _party(dm, "Recipient", "dbIDRecipient"),
        "to_hands": _text(dm, "dmToHands"), "sender_ref": _text(dm, "dmSenderRefNumber"),
        "sender_ident": _text(dm, "dmSenderIdent"), "recipient_ref": _text(dm, "dmRecipientRefNumber"),
        "recipient_ident": _text(dm, "dmRecipientIdent"), "personal_delivery": _text(dm, "dmPersonalDelivery") in ("true", "1"),
        "delivered": _text(body, "dmDeliveryTime") or None, "accepted": _text(body, "dmAcceptanceTime") or None,
        "status": _number(_text(body, "dmMessageStatus")), "suspicious": body.get("specMessFlag") == "1",
        "attachments": attachments,
        "events": [{"time": _text(event, "dmEventTime"), "description": _text(event, "dmEventDescr")}
                   for event in _children(_child(body, "dmEvents"), "dmEvent")],
    }
    return Message(kind, info, [content for _, content in described], signed_data, content, root)


def _safe_file_name(name, number):
    name = re.split(r"[/\\]", name)[-1]
    name = "".join(character for character in name if not unicodedata.category(character).startswith("C"))
    name = WINDOWS_FORBIDDEN_CHARACTERS.sub("_", name).lstrip(".").rstrip(". ")
    if not name:
        return f"priloha-{number}"
    stem, dot, extension = name.rpartition(".") if "." in name else (name, "", "")
    if name.split(".")[0].rstrip(" ").upper() in WINDOWS_RESERVED_STEMS:
        name = "_" + name
    if len(name) > MAX_FILE_NAME:
        tail = dot + extension if len(extension) < 20 else ""
        name = name[:MAX_FILE_NAME - len(tail)] + tail if tail else name[:MAX_FILE_NAME]
    return name


def safe_file_names(names):
    # The names come from the sender as typed; they must never reach outside the folder the user picked.
    result, taken = [], set()
    for number, name in enumerate(names, 1):
        candidate = _safe_file_name(name, number)
        stem, dot, extension = candidate.rpartition(".") if "." in candidate else (candidate, "", "")
        copy_number = 2
        while candidate.lower() in taken:
            candidate = f"{stem} ({copy_number}){dot}{extension}"
            copy_number += 1
        taken.add(candidate.lower())
        result.append(candidate)
    return result


def _display_name(name):
    return name.native.get("common_name") or name.human_friendly


def is_isds_operator(certificate):
    # A seal of the operator, not any certificate of its staff: a person's certificate names the person.
    subject = certificate.subject.native
    return subject.get("organization_identifier") in ISDS_OPERATOR_IDS and not subject.keys() & {"given_name", "surname"}


def _content_digest(message):
    return lambda algorithm: hashlib.new(algorithm, message.content).digest()


def _stamp_details(status):
    if status is None or status.signing_cert is None or not status.intact or not status.valid:
        return {"kind": "signature", "intact": False, "at": None, "issuer": "", "der": None}
    return {"kind": "signature", "intact": True, "at": status.timestamp.astimezone(timezone.utc).isoformat(),
            "issuer": _display_name(status.signing_cert.subject), "der": status.signing_cert.dump()}


def _signature_stamps(signer_info, digest):
    attributes = signer_info["unsigned_attrs"]
    if isinstance(attributes, core.Void) or not any(attribute["type"].dotted == SIGNATURE_TIME_STAMP_TOKEN_OID for attribute in attributes):
        return [], None
    try:  # no fetching: the page must not wait for the network, and trust comes from the trusted lists
        timing = asyncio.run(generic_cms.collect_timing_info(signer_info, ValidationContext(allow_fetching=False), digest))
        return [_stamp_details(timing.get("timestamp_validity"))], timing.get("signer_reported_dt")
    except Exception:  # noqa: BLE001 - a damaged token only means the time is not confirmed
        return [_stamp_details(None)], None


def _seal_is_intact(message):
    try:
        status = asyncio.run(generic_cms.async_validate_cms_signature(
            message.signed_data, validation_context=ValidationContext(allow_fetching=False)))
        return bool(status.intact and status.valid)
    except Exception:  # noqa: BLE001 - whatever breaks in a hostile envelope, the seal is not shown as intact
        return False


def describe_seal(message):
    try:
        signer_info = message.signed_data["signer_infos"][0]
        certificate = generic_cms.extract_certs_for_validation(message.signed_data).signer_cert
    except Exception as error:  # noqa: BLE001 - pyHanko reports a missing signer certificate in its own errors
        # Nothing can be said about who sealed it; the message is refused rather than shown with an anonymous seal.
        raise ValueError("message without a readable seal certificate") from error
    try:
        stamps, reported = _signature_stamps(signer_info, _content_digest(message))
        if reported is None:
            reported = generic_cms.extract_self_reported_ts(signer_info)
        return {
            "subject": _display_name(certificate.subject), "issuer": _display_name(certificate.issuer),
            "intact": _seal_is_intact(message), "isds_operator": is_isds_operator(certificate),
            "not_after": certificate["tbs_certificate"]["validity"]["not_after"].native.isoformat(),
            "signed_at": reported.astimezone(timezone.utc).isoformat() if reported else None,
            "der": certificate.dump(), "stamps": stamps,
            "publisher": False, "modified": False, "changes_unchecked": False, "covers_whole": True,
        }
    except DAMAGED_STRUCTURE as error:
        raise ValueError("message with a damaged seal") from error


def submission_stamp(message):
    body = _child(message.xml_root, BODIES[message.kind])
    encoded = _text(body, "dmQTimestamp")
    if not encoded:
        return None
    try:
        token = cms.ContentInfo.load(base64.b64decode("".join(encoded.split()), validate=True))
        signed = token["content"]
        tst_info = signed["encap_content_info"]["content"].parsed
        stamped_hash = tst_info["message_imprint"]["hashed_message"].native
        authority = generic_cms.extract_certs_for_validation(signed).signer_cert
        message_hash = base64.b64decode("".join(_text(body, "dmHash").split()), validate=True)
        return {"at": tst_info["gen_time"].native.astimezone(timezone.utc).isoformat(),
                "issuer": _display_name(authority.subject), "matches": stamped_hash == message_hash}
    except Exception:  # noqa: BLE001 - a damaged stamp only means the submission time is not shown
        return None
