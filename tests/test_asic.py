"""XAdES signatures and ASiC containers, built from the pki/ test identity or throwaway keys.

    python -I tests/test_asic.py
"""
import base64
import datetime
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

import asic_samples  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402
from lxml import etree  # noqa: E402

import xades_verify  # noqa: E402

DS = asic_samples.DS
XADES = asic_samples.XADES
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
FILES = {"smlouva.pdf": b"%PDF-1.7 smlouva"}


def resolver(files):
    return lambda uri: (uri, files[uri]) if uri in files else None


def throwaway_ecdsa_identity():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Throwaway ECDSA")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .sign(key, hashes.SHA256()))
    return key, cert


class XadesVerifyTest(unittest.TestCase):
    def assertBroken(self, result, reason, content_changed=False):
        self.assertFalse(result["intact"])
        self.assertEqual(result["broken_reason"], reason)
        self.assertEqual(result["content_changed"], content_changed)

    def test_intact_signature_verifies(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES), resolver(FILES))
        self.assertTrue(result["intact"])
        self.assertEqual(result["broken_reason"], "")
        self.assertFalse(result["content_changed"])
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "ok"}])
        self.assertEqual(result["signed_at"], "2026-01-15T10:00:00+00:00")
        self.assertEqual(result["subject"], "Testovaci Podepisujici")
        self.assertIsInstance(result["der"], bytes)
        self.assertEqual(result["stamps"], [])

    def test_changed_file_breaks_signature(self):
        signature = asic_samples.xades_signature(FILES)
        result = xades_verify.verify(signature, resolver({"smlouva.pdf": b"%PDF-1.7 jina smlouva"}))
        self.assertBroken(result, xades_verify.CONTENT_CHANGED, content_changed=True)
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "changed"}])

    def test_changed_signed_properties_break_signature(self):
        signature = asic_samples.xades_signature(FILES)
        signature.find(f".//{{{XADES}}}SigningTime").text = "2025-01-15T10:00:00Z"
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.CONTENT_CHANGED, content_changed=True)

    def test_changed_signature_value_is_invalid(self):
        signature = asic_samples.xades_signature(FILES)
        value = signature.find(f"{{{DS}}}SignatureValue")
        raw = bytearray(base64.b64decode(value.text))
        raw[10] ^= 1
        value.text = base64.b64encode(bytes(raw)).decode()
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.BAD_VALUE)

    def test_missing_signing_certificate_is_invalid(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, signing_certificate=None), resolver(FILES))
        self.assertFalse(result["intact"])
        self.assertIn(result["broken_reason"], (xades_verify.NO_SIGNED_PROPERTIES, xades_verify.CERTIFICATE_MISMATCH))
        self.assertEqual((result["subject"], result["der"]), ("", None))

    def test_v1_signing_certificate_with_sha1_verifies(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, signing_certificate="v1"), resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])

    def test_sha1_reference_digest_verifies(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, digest=asic_samples.SHA1), resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])

    def test_md5_digest_is_invalid(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, digest=asic_samples.MD5), resolver(FILES))
        self.assertBroken(result, xades_verify.UNKNOWN_DIGEST)
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "refused"}])

    def test_rsa_sha1_signature_is_invalid(self):
        signature = asic_samples.xades_signature(FILES, signature_method=asic_samples.RSA_SHA1)
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.UNKNOWN_SIGNATURE_ALGORITHM)

    def test_ecdsa_signature_verifies(self):
        key, cert = throwaway_ecdsa_identity()
        signature = asic_samples.xades_signature(FILES, signature_method=asic_samples.ECDSA_SHA256, key=key, cert=cert)
        result = xades_verify.verify(signature, resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])
        self.assertEqual(result["subject"], "Throwaway ECDSA")

    def test_signed_manifest_references_are_checked(self):
        files = {"smlouva.pdf": b"%PDF-1.7 smlouva", "priloha.txt": b"priloha"}
        signature = asic_samples.xades_signature(files, manifest=True)
        intact = xades_verify.verify(signature, resolver(files))
        self.assertTrue(intact["intact"], intact["broken_reason"])
        self.assertEqual(intact["references"], [{"name": "smlouva.pdf", "outcome": "ok"}, {"name": "priloha.txt", "outcome": "ok"}])
        changed = xades_verify.verify(signature, resolver({**files, "priloha.txt": b"jina priloha"}))
        self.assertBroken(changed, xades_verify.CONTENT_CHANGED, content_changed=True)
        self.assertEqual(changed["references"], [{"name": "smlouva.pdf", "outcome": "ok"}, {"name": "priloha.txt", "outcome": "changed"}])

    def test_c14n11_without_xml_id_verifies(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, c14n=asic_samples.C14N11), resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])

    def test_c14n11_with_xml_id_is_unsupported(self):
        signature = asic_samples.xades_signature(FILES, c14n=asic_samples.C14N11,
                                                 mutate=lambda signature: signature.find(f"{{{DS}}}SignedInfo").set(XML_ID, "si"))
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.UNSUPPORTED_C14N)

    def test_doctype_is_refused(self):
        with self.assertRaises(xades_verify.XadesError):
            xades_verify.parse(b'<!DOCTYPE x [<!ENTITY a "b">]><x/>')

    def test_malformed_xml_is_refused(self):
        with self.assertRaises(xades_verify.XadesError):
            xades_verify.parse(b"<x>")

    def test_parsed_signatures_file_verifies(self):
        root = xades_verify.parse(asic_samples.signatures_xml(asic_samples.xades_signature(FILES)))
        [signature] = xades_verify.find_signatures(root)
        self.assertTrue(xades_verify.verify(signature, resolver(FILES))["intact"])

    def test_missing_file_is_a_bad_reference(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES), resolver({}))
        self.assertBroken(result, xades_verify.BAD_REFERENCE)
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "refused"}])

    def test_signed_key_info_verifies(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, sign_key_info=True), resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "ok"}])

    def test_key_info_outside_signature_is_wrapping(self):
        def move_key_info_into_object(signature):
            key_info = signature.find(f"{{{DS}}}KeyInfo")
            etree.SubElement(signature, f"{{{DS}}}Object").append(key_info)
        signature = asic_samples.xades_signature(FILES, sign_key_info=True, mutate=move_key_info_into_object)
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.WRAPPED)

    def test_duplicate_key_info_id_is_wrapping(self):
        def duplicate_key_info(signature):
            copy = etree.fromstring(etree.tostring(signature.find(f"{{{DS}}}KeyInfo")))
            etree.SubElement(signature, f"{{{DS}}}Object").append(copy)
        signature = asic_samples.xades_signature(FILES, sign_key_info=True, mutate=duplicate_key_info)
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.WRAPPED)

    def test_relative_namespace_in_signed_info_is_unsupported(self):
        signature = asic_samples.xades_signature(FILES)
        signature.find(f"{{{DS}}}SignedInfo").append(etree.fromstring(b'<r:x xmlns:r="rel"/>'))
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.UNSUPPORTED_C14N)

    def test_relative_namespace_in_canonicalized_file_is_unsupported(self):
        files = {"data.xml": b'<a xmlns="rel"/>'}
        def canonicalize_file(signature):
            reference = signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference")
            transforms = etree.Element(f"{{{DS}}}Transforms")
            etree.SubElement(transforms, f"{{{DS}}}Transform", Algorithm=asic_samples.C14N10)
            reference.insert(0, transforms)
        signature = asic_samples.xades_signature(files, mutate=canonicalize_file)
        result = xades_verify.verify(signature, resolver(files))
        self.assertBroken(result, xades_verify.UNSUPPORTED_C14N)
        self.assertEqual(result["references"], [{"name": "data.xml", "outcome": "refused"}])

    def test_signing_time_out_of_range_does_not_crash(self):
        signature = asic_samples.xades_signature(FILES)
        signature.find(f".//{{{XADES}}}SigningTime").text = "0001-01-01T00:00:00+14:00"
        result = xades_verify.verify(signature, resolver(FILES))
        self.assertFalse(result["intact"])
        self.assertIsNone(result["signed_at"])

    def test_duplicate_id_is_wrapping(self):
        def duplicate_signed_properties(signature):
            key_info = signature.find(f"{{{DS}}}KeyInfo")
            key_info.append(etree.fromstring(etree.tostring(signature.find(f".//{{{XADES}}}SignedProperties"))))
        signature = asic_samples.xades_signature(FILES, mutate=duplicate_signed_properties)
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.WRAPPED)

    def test_standalone_enveloping_with_base64_verifies(self):
        [signature] = xades_verify.find_signatures(xades_verify.parse(asic_samples.enveloping(b"%PDF-1.7", "application/pdf", base64_transform=True)))
        result = xades_verify.verify(signature, resolver({}), standalone=True)
        self.assertTrue(result["intact"], result["broken_reason"])

    def test_base64_transform_outside_standalone_is_refused(self):
        [signature] = xades_verify.find_signatures(xades_verify.parse(asic_samples.enveloping(b"%PDF-1.7", "application/pdf", base64_transform=True)))
        self.assertBroken(xades_verify.verify(signature, resolver({})), xades_verify.UNKNOWN_TRANSFORM)

    def test_standalone_enveloped_verifies(self):
        document = etree.fromstring(b"<faktura><castka>100</castka></faktura>")
        root = xades_verify.parse(asic_samples.enveloped(document))
        [signature] = xades_verify.find_signatures(root)
        result = xades_verify.verify(signature, resolver({}), allow_empty_uri=True, standalone=True)
        self.assertTrue(result["intact"], result["broken_reason"])
        root.find("castka").text = "999"
        changed = xades_verify.verify(signature, resolver({}), allow_empty_uri=True, standalone=True)
        self.assertBroken(changed, xades_verify.CONTENT_CHANGED, content_changed=True)

    def test_empty_uri_needs_permission(self):
        root = xades_verify.parse(asic_samples.enveloped(etree.fromstring(b"<faktura/>")))
        [signature] = xades_verify.find_signatures(root)
        self.assertBroken(xades_verify.verify(signature, resolver({}), standalone=True), xades_verify.BAD_REFERENCE)


    def test_time_stamp_is_reported(self):
        stamp, = xades_verify.verify(asic_samples.xades_signature(FILES, stamp=True), resolver(FILES))["stamps"]
        self.assertEqual((stamp["kind"], stamp["intact"]), ("signature", True))
        self.assertIsNotNone(stamp["der"])
        self.assertEqual(stamp["at"], "2026-01-15T10:05:00+00:00")

    def test_no_time_stamp_gives_no_stamps(self):
        self.assertEqual(xades_verify.verify(asic_samples.xades_signature(FILES), resolver(FILES))["stamps"], [])

    def test_time_stamp_over_other_data_is_not_intact(self):
        result = xades_verify.verify(asic_samples.xades_signature(FILES, stamp="wrong"), resolver(FILES))
        stamp, = result["stamps"]
        self.assertFalse(stamp["intact"])
        self.assertTrue(result["intact"])

    def test_damaged_time_stamp_token_is_not_intact(self):
        def damage(signature):
            element = signature.find(f".//{{{XADES}}}EncapsulatedTimeStamp")
            token = bytearray(base64.b64decode(element.text))
            for index in range(len(token) // 2, len(token) // 2 + 8):
                token[index] ^= 0xFF
            element.text = base64.b64encode(bytes(token)).decode()
        signature = asic_samples.xades_signature(FILES, stamp=True)
        damage(signature)
        result = xades_verify.verify(signature, resolver(FILES))
        stamp, = result["stamps"]
        self.assertFalse(stamp["intact"])
        self.assertTrue(result["intact"])

    def test_unreadable_time_stamp_is_not_intact(self):
        signature = asic_samples.xades_signature(FILES, stamp=True)
        signature.find(f".//{{{XADES}}}EncapsulatedTimeStamp").text = "!!not base64!!"
        stamp, = xades_verify.verify(signature, resolver(FILES))["stamps"]
        self.assertFalse(stamp["intact"])


if __name__ == "__main__":
    unittest.main()
