import base64
import binascii
import re
import unicodedata
from dataclasses import dataclass, field

from asn1crypto import cms
from lxml import etree

CMS_SEQUENCE = b"\x30"
# The WS documentation writes the host as isds.czebox.cz in places; the schemas and real messages use isds.czechpoint.cz.
ISDS_NAMESPACE = re.compile(r"^http://isds\.(?:czechpoint|czebox)\.cz/v20/(message|SentMessage|delivery)$")
KINDS = {"message": "received", "SentMessage": "sent", "delivery": "receipt"}
BODIES = {"received": "dmReturnedMessage", "sent": "dmReturnedMessage", "receipt": "dmDelivery"}
WINDOWS_RESERVED_STEMS = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
MAX_FILE_NAME = 200
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
    return int(text) if text.isdigit() else None


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
    except (TypeError, KeyError, AttributeError, OverflowError) as error:
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
    name = name.lstrip(".").rstrip(". ")
    if not name:
        return f"priloha-{number}"
    stem, dot, extension = name.rpartition(".") if "." in name else (name, "", "")
    if stem.upper() in WINDOWS_RESERVED_STEMS:
        name, stem = "_" + name, "_" + stem
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
