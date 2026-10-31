#!/usr/bin/env python3
"""Writes samples-pdf/razitka/: PDFs with time stamps from a throwaway test authority, for tests/test_app.py.

    .venv/bin/python tools/make-timestamp-samples.py

Nothing real goes in: the keys live only while this runs and the certificates say TEST.
"""
import hashlib
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "samples-pdf/razitka"
SIGNED = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)
LATER = SIGNED + timedelta(days=1)
# Far enough that the samples never expire under the tests.
START, END = datetime(2020, 1, 1, tzinfo=timezone.utc), datetime(2120, 1, 1, tzinfo=timezone.utc)


def issue(name, issuer=None, issuer_key=None, ca=False, stamping=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CZ"),
                         x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "Blanketa TEST"),
                         x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(START).not_valid_after(END)
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    if stamping:
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return builder.sign(issuer_key or key, hashes.SHA256()), key


def asn1_certificate(cert):
    return asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER))


def asn1_key(key):
    return asn1_keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                           serialization.NoEncryption()))


CA, CA_KEY = issue("Blanketa TEST CA", ca=True)
USER, USER_KEY = issue("Testovaci Podepisujici", CA, CA_KEY)
TSA, TSA_KEY = issue("Blanketa TEST TSA", CA, CA_KEY, stamping=True)
OTHER_TSA, OTHER_TSA_KEY = issue("Nedoveryhodna TEST TSA", CA, CA_KEY, stamping=True)
EMBEDDED = SimpleCertificateStore()
EMBEDDED.register_multiple([asn1_certificate(CA)])
SIGNER = signers.SimpleSigner(signing_cert=asn1_certificate(USER), signing_key=asn1_key(USER_KEY), cert_registry=EMBEDDED)


def stamper(when=SIGNED, cert=TSA, key=TSA_KEY, kind=DummyTimeStamper):
    return kind(tsa_cert=asn1_certificate(cert), tsa_key=asn1_key(key), certs_to_embed=EMBEDDED, fixed_dt=when)


class WrongDocumentStamper(DummyTimeStamper):
    """Issues a correct token over something else, as a token moved over from another document would be."""

    async def async_timestamp(self, message_digest, md_algorithm):
        return await super().async_timestamp(hashlib.sha256(b"jiny dokument").digest(), md_algorithm)


def sign(pdf, field="Podpis1", timestamper=None):
    meta = signers.PdfSignatureMetadata(field_name=field, md_algorithm="sha256", subfilter=fields.SigSeedSubFilter.PADES)
    out = signers.PdfSigner(meta, SIGNER, timestamper=timestamper, new_field_spec=fields.SigFieldSpec(field))
    return out.sign_pdf(IncrementalPdfFileWriter(io.BytesIO(pdf), strict=False)).getvalue()


def stamp_document(pdf, when=LATER):
    writer = IncrementalPdfFileWriter(io.BytesIO(pdf), strict=False)
    return signers.PdfTimeStamper(stamper(when)).timestamp_pdf(writer, "sha256").getvalue()


def main():
    blank = (ROOT / "samples-pdf/zadost.pdf").read_bytes()
    samples = {
        "bez-razitka": sign(blank),
        "razitko-v-podpisu": sign(blank, timestamper=stamper()),
        "razitko-dokumentu": stamp_document(sign(blank)),
        "razitko-pred-podpisem": sign(stamp_document(blank, SIGNED - timedelta(days=1))),
        "razitko-jineho-dokumentu": sign(blank, timestamper=stamper(kind=WrongDocumentStamper)),
        "razitko-nedoveryhodne": sign(blank, timestamper=stamper(cert=OTHER_TSA, key=OTHER_TSA_KEY)),
    }
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, data in samples.items():
        (TARGET / f"{name}.pdf").write_bytes(data)
    (TARGET / "tsa.crt").write_bytes(TSA.public_bytes(serialization.Encoding.PEM))
    print(f"{len(samples)} samples in {TARGET}", file=sys.stdout)


if __name__ == "__main__":
    main()
