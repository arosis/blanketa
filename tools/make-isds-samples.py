#!/usr/bin/env python3
"""Writes samples-isds/: data messages and delivery receipts as ISDS hands them out, sealed by a throwaway test
authority, for tests/test_isds.py, tests/test_security.py and tools/fuzz.py.

    .venv/bin/python tools/make-isds-samples.py

Nothing real goes in: the keys live only while this runs, the certificates and the people say TEST. The shape follows
Provozní řád ISDS, příloha 2 „WS manipulace s datovými zprávami“ (https://datovka.gov.cz/info/cs/80.html).
"""
import asyncio
import base64
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

from asn1crypto import cms
from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from lxml import etree
from pyhanko.sign import signers
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "samples-isds"
ISDS = "http://isds.czechpoint.cz/v20"
SEALED = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
START, END = datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2120, 1, 1, tzinfo=timezone.utc)
EXPIRED_END = datetime(2021, 1, 1, tzinfo=timezone.utc)
ROOT_ELEMENTS = {"received": ("message", "MessageDownloadResponse", "dmReturnedMessage"),
                 "sent": ("SentMessage", "MessageDownloadResponse", "dmReturnedMessage"),
                 "receipt": ("delivery", "GetDeliveryInfoResponse", "dmDelivery")}
RECEIPT_EVENTS = (("2026-03-02T10:30:00.000+01:00", "EV0: Zpráva byla podána."),
                  ("2026-03-02T10:30:05.000+01:00", "EV5: Zpráva byla dodána do schránky příjemce."),
                  ("2026-03-03T08:12:40.000+01:00", "EV11: Zpráva byla doručena přihlášením oprávněné osoby."))
DEFAULT_ATTACHMENTS = (("rozhodnuti.pdf", "application/pdf", "main", b"%PDF-1.4\n% TEST priloha\n"),
                       ("data.xml", "text/xml", "enclosure", "<data xmlns='urn:test'><polozka>1</polozka></data>"),
                       ("../../evil.txt", "text/plain", "enclosure", b"TEST\n"))


def issue(names, issuer=None, issuer_key=None, ca=False, stamping=False, not_after=END):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(oid, value) for oid, value in names])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(START).not_valid_after(not_after)
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if stamping:
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return builder.sign(issuer_key or key, hashes.SHA256()), key


def test_names(common_name, organization="Blanketa TEST", organization_id=None):
    names = [(x509.NameOID.COUNTRY_NAME, "CZ"), (x509.NameOID.ORGANIZATION_NAME, organization)]
    if organization_id:
        names.append((x509.NameOID.ORGANIZATION_IDENTIFIER, organization_id))
    return names + [(x509.NameOID.COMMON_NAME, common_name)]


def asn1_certificate(cert):
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def asn1_key(key):
    return asn1_keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()))


CA, CA_KEY = issue(test_names("Blanketa TEST CA"), ca=True)
TSA, TSA_KEY = issue(test_names("Blanketa TEST TSA"), CA, CA_KEY, stamping=True)
ISDS_SEAL, ISDS_SEAL_KEY = issue(test_names("ISDS TEST", "Digitální a informační agentura TEST", "NTRCZ-17651921"), CA, CA_KEY)
EXPIRED_SEAL, EXPIRED_SEAL_KEY = issue(test_names("ISDS TEST 2020", "Ministerstvo vnitra TEST", "NTRCZ-00007064"), CA,
                                       CA_KEY, not_after=EXPIRED_END)
OTHER_SEAL, OTHER_SEAL_KEY = issue(test_names("Někdo jiný TEST"), CA, CA_KEY)
SEALS = {"isds": (ISDS_SEAL, ISDS_SEAL_KEY), "expired": (EXPIRED_SEAL, EXPIRED_SEAL_KEY), "other": (OTHER_SEAL, OTHER_SEAL_KEY)}
EMBEDDED = SimpleCertificateStore()
EMBEDDED.register_multiple([asn1_certificate(CA)])


def stamper():
    return DummyTimeStamper(tsa_cert=asn1_certificate(TSA), tsa_key=asn1_key(TSA_KEY), certs_to_embed=EMBEDDED,
                            fixed_dt=SEALED)


def child(parent, name, text=None, **attributes):
    element = etree.SubElement(parent, f"{{{ISDS}}}{name}", **attributes)
    if text is not None:
        element.text = text
    return element


def message_xml(kind, subject, attachments, events, status, sender, recipient, suspicious):
    namespace_suffix, root_name, body_name = ROOT_ELEMENTS[kind]
    root = etree.Element(f"{{{ISDS}/{namespace_suffix}}}{root_name}", nsmap={"q": f"{ISDS}/{namespace_suffix}", "p": ISDS})
    body = child(root, body_name, **({"specMessFlag": "1"} if suspicious else {}))
    dm = child(body, "dmDm")
    for name, text in (("dmID", "1234567"), ("dbIDSender", "abc12de"), ("dmSender", sender),
                       ("dmSenderAddress", "Testovací 1, 110 00 Praha 1"), ("dmSenderType", "10"),
                       ("dmRecipient", recipient), ("dmRecipientAddress", "Zkušební 2, 602 00 Brno"),
                       ("dbIDRecipient", "xyz34fg"), ("dmToHands", "Jan Testovací"), ("dmAnnotation", subject),
                       ("dmRecipientRefNumber", "TEST-123/2026"), ("dmRecipientIdent", "SZ-TEST-9"),
                       ("dmSenderRefNumber", "MV-TEST-456/2026"), ("dmSenderIdent", "SZ-TEST-1"),
                       ("dmPersonalDelivery", "true"), ("dmAllowSubstDelivery", "true")):
        child(dm, name, text)
    if kind != "receipt":
        files = child(dm, "dmFiles")
        for name, mime, meta, content in attachments:
            file = child(files, "dmFile", dmMimeType=mime, dmFileMetaType=meta, dmFileDescr=name)
            if isinstance(content, bytes):
                child(file, "dmEncodedContent", base64.b64encode(content).decode())
            else:
                child(file, "dmXMLContent").append(etree.fromstring(content))
    digest = hashlib.sha256(etree.tostring(dm, method="c14n")).digest()
    child(body, "dmHash", base64.b64encode(digest).decode(), algorithm="SHA-256")
    token = asyncio.run(stamper().async_timestamp(digest, "sha256")).dump()
    child(body, "dmQTimestamp", base64.b64encode(token).decode())
    if kind == "receipt":
        listed = child(body, "dmEvents")
        for time, description in events:
            event = child(listed, "dmEvent")
            child(event, "dmEventTime", time)
            child(event, "dmEventDescr", description)
    child(body, "dmDeliveryTime", "2026-03-02T10:30:05.123+01:00")
    child(body, "dmAcceptanceTime", "2026-03-03T08:12:40.456+01:00")
    child(body, "dmMessageStatus", str(status))
    if kind != "receipt":
        child(body, "dmAttachmentSize", "1")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def seal(content, signer="isds", time_stamp=True):
    cert, key = SEALS[signer]
    sealer = signers.SimpleSigner(signing_cert=asn1_certificate(cert), signing_key=asn1_key(key), cert_registry=EMBEDDED)
    return asyncio.run(sealer.async_sign_general_data(content, "sha256", detached=False, use_cades=True,
                                                      timestamper=stamper() if time_stamp else None)).dump()


def build_message(kind="received", subject="Rozhodnutí ve věci TEST", attachments=DEFAULT_ATTACHMENTS,
                  events=RECEIPT_EVENTS, status=6, sender="Úřad TEST", recipient="Firma TEST s.r.o.", signer="isds",
                  time_stamp=None, suspicious=False, xml=None):
    content = xml if xml is not None else message_xml(kind, subject, attachments, events, status, sender, recipient,
                                                      suspicious)
    return seal(content, signer, kind != "receipt" if time_stamp is None else time_stamp)


def ber_length(length):
    if length < 0x80:
        return bytes([length])
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def to_ber(der, chunk=1000):
    """Re-encodes a sealed message the way ISDS sends it: indefinite lengths and the XML in chunks."""
    signed_data = cms.ContentInfo.load(der)["content"]
    content = signed_data["encap_content_info"]["content"].native
    chunks = b"".join(b"\x04" + ber_length(len(content[i:i + chunk])) + content[i:i + chunk]
                      for i in range(0, len(content), chunk))
    encapsulated = (b"\x30\x80" + signed_data["encap_content_info"]["content_type"].dump()
                    + b"\xa0\x80\x24\x80" + chunks + b"\x00\x00\x00\x00\x00\x00")
    parts = [signed_data["version"].dump(), signed_data["digest_algorithms"].dump(), encapsulated,
             signed_data["certificates"].dump(), signed_data["signer_infos"].dump()]
    body = b"\x30\x80" + b"".join(parts) + b"\x00\x00"
    return b"\x30\x80" + cms.ContentType("signed_data").dump() + b"\xa0\x80" + body + b"\x00\x00\x00\x00"


def changed_after_sealing(der):
    content = cms.ContentInfo.load(der)["content"]["encap_content_info"]["content"].native
    return der.replace(content, content.replace("TEST".encode(), "TESt".encode(), 1))


def main():
    received = build_message()
    samples = {
        "prijata": received,
        "prijata-ber": to_ber(received),
        "odeslana": build_message("sent"),
        "dorucenka": build_message("receipt"),
        "cizi-pecet": build_message(signer="other"),
        "pozmenena": changed_after_sealing(received),
        "vyprsela": build_message("receipt", signer="expired"),
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, data in samples.items():
        (TARGET / f"{name}.zfo").write_bytes(data)
    (TARGET / "test-ca.der").write_bytes(CA.public_bytes(serialization.Encoding.DER))
    print(f"{len(samples)} samples in {TARGET}", file=sys.stdout)


if __name__ == "__main__":
    main()
