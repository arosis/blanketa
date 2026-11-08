"""XAdES signatures and ASiC containers, built from the pki/ test identity or throwaway keys."""
import base64
import datetime
import http.client
import io
import json
import os
import re
import struct
import tempfile
import threading
import unicodedata
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import asic_samples
from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from lxml import etree
from support import ROOT

import app
import asic_verify
import xades_verify

DS = asic_samples.DS
XADES = asic_samples.XADES
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
FILES = {"smlouva.pdf": b"%PDF-1.7 smlouva"}
PDF = FILES["smlouva.pdf"]
ASIC_E_MIMETYPE = b"application/vnd.etsi.asic-e+zip"
UNSUPPORTED = "Tento typ podpisu zatím neumíme ověřit."
UNREADABLE = "soubor s podpisem nejde přečíst"


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

    def test_countersignature_does_not_break_the_signature(self):
        def add_countersignature(signature):
            qualifying = signature.find(f"{{{DS}}}Object/{{{XADES}}}QualifyingProperties")
            unsigned = etree.SubElement(etree.SubElement(qualifying, f"{{{XADES}}}UnsignedProperties"),
                                        f"{{{XADES}}}UnsignedSignatureProperties")
            counter = etree.SubElement(etree.SubElement(unsigned, f"{{{XADES}}}CounterSignature"), f"{{{DS}}}Signature", Id="CS1")
            etree.SubElement(etree.SubElement(counter, f"{{{DS}}}Object"), f"{{{XADES}}}QualifyingProperties", Target="#CS1")
        signature = asic_samples.xades_signature(FILES, mutate=add_countersignature)
        result = xades_verify.verify(signature, resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])

    def test_signed_element_inside_an_object_verifies(self):
        def sign_object_content(signature):
            data = etree.SubElement(etree.SubElement(signature, f"{{{DS}}}Object"), "{urn:example}Data", Id="data-1")
            data.text = "podepsaná data"
            asic_samples._add_reference(signature.find(f"{{{DS}}}SignedInfo"), "#data-1", asic_samples.SHA256, [asic_samples.EXC_C14N])
        result = xades_verify.verify(asic_samples.xades_signature(FILES, mutate=sign_object_content), resolver(FILES))
        self.assertTrue(result["intact"], result["broken_reason"])
        self.assertEqual(result["references"], [{"name": "smlouva.pdf", "outcome": "ok"}])

    def test_signed_element_inside_a_countersignature_is_wrapping(self):
        def sign_countersignature_content(signature):
            qualifying = signature.find(f"{{{DS}}}Object/{{{XADES}}}QualifyingProperties")
            unsigned = etree.SubElement(etree.SubElement(qualifying, f"{{{XADES}}}UnsignedProperties"),
                                        f"{{{XADES}}}UnsignedSignatureProperties")
            counter = etree.SubElement(etree.SubElement(unsigned, f"{{{XADES}}}CounterSignature"), f"{{{DS}}}Signature", Id="CS1")
            etree.SubElement(etree.SubElement(counter, f"{{{DS}}}Object"), "{urn:example}Data", Id="inner")
            asic_samples._add_reference(signature.find(f"{{{DS}}}SignedInfo"), "#inner", asic_samples.SHA256, [asic_samples.EXC_C14N])
        result = xades_verify.verify(asic_samples.xades_signature(FILES, mutate=sign_countersignature_content), resolver(FILES))
        self.assertBroken(result, xades_verify.WRAPPED)

    def test_id_reused_by_another_signature_in_the_file_is_allowed(self):
        def add_reference_id(signature):
            signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference").set("Id", "r-id-1")
        first = asic_samples.xades_signature(FILES, signature_id="S1", mutate=add_reference_id)
        second = asic_samples.xades_signature(FILES, signature_id="S2", mutate=add_reference_id)
        root = etree.fromstring(asic_samples.signatures_xml(first, second))
        results = [xades_verify.verify(signature, resolver(FILES)) for signature in xades_verify.find_signatures(root)]
        self.assertEqual([result["intact"] for result in results], [True, True], [result["broken_reason"] for result in results])

    def test_id_repeated_inside_one_signature_is_wrapping(self):
        def repeat_id(signature):
            signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference").set("Id", "r-id-1")
            etree.SubElement(signature, f"{{{DS}}}Object", Id="r-id-1")
        result = xades_verify.verify(asic_samples.xades_signature(FILES, mutate=repeat_id), resolver(FILES))
        self.assertBroken(result, xades_verify.WRAPPED)

    def test_unloadable_public_key_is_a_bad_value(self):
        load = x509.load_der_x509_certificate

        class UnloadableKey:
            def __init__(self, cert):
                self.cert = cert

            def __getattr__(self, name):
                return getattr(self.cert, name)

            def public_key(self):
                raise UnsupportedAlgorithm("explicit curve")

        with mock.patch.object(xades_verify.x509, "load_der_x509_certificate", lambda der: UnloadableKey(load(der))):
            result = xades_verify.verify(asic_samples.xades_signature(FILES), resolver(FILES))
        self.assertBroken(result, xades_verify.BAD_VALUE)

    def test_key_info_moved_into_an_object_is_not_the_signers(self):
        def move_key_info_into_object(signature):
            key_info = signature.find(f"{{{DS}}}KeyInfo")
            etree.SubElement(signature, f"{{{DS}}}Object").append(key_info)
        signature = asic_samples.xades_signature(FILES, sign_key_info=True, mutate=move_key_info_into_object)
        self.assertBroken(xades_verify.verify(signature, resolver(FILES)), xades_verify.CERTIFICATE_MISMATCH)

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
        self.assertEqual(result["references"], [{"name": "#S1-content", "outcome": "ok", "content": b"%PDF-1.7", "mime": "application/pdf"}])

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

    def test_time_stamp_made_with_the_signed_info_method_is_reported(self):
        signature = asic_samples.xades_signature(FILES, c14n=asic_samples.EXC_C14N, stamp="signed-info-c14n")
        self.assertIsNone(signature.find(f".//{{{XADES}}}SignatureTimeStamp/{{{DS}}}CanonicalizationMethod"))
        stamp, = xades_verify.verify(signature, resolver(FILES))["stamps"]
        self.assertTrue(stamp["intact"])

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


def zip_bytes(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries:
            archive.writestr(zipfile.ZipInfo(name), data, compress_type=zipfile.ZIP_STORED if name == "mimetype" else zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def signatures_file(files, **options):
    return asic_samples.signatures_xml(asic_samples.xades_signature(files, **options))


def break_value(signature):
    value = signature.find(f"{{{DS}}}SignatureValue")
    raw = bytearray(base64.b64decode(value.text))
    raw[10] ^= 1
    value.text = base64.b64encode(bytes(raw)).decode()
    return signature


def states(description):
    return [(file["name"], file["state"]) for file in description["files"]]


class ContainerTest(unittest.TestCase):
    def test_asic_e_lists_files_with_states(self):
        signed = {"smlouva.pdf": PDF, "cenik.xlsx": b"x"}
        data = asic_samples.asic_e({**signed, "poznamka.txt": b"y"}, {"META-INF/signatures001.xml": signatures_file(signed)})
        self.assertTrue(asic_verify.is_signed_file(data))
        description, contents = asic_verify.describe(data)
        self.assertEqual((description["kind"], description["label"]), ("asic-e", "ASiC-E s\u00a0podpisy XAdES"))
        self.assertEqual(states(description), [("smlouva.pdf", "signed"), ("cenik.xlsx", "signed"), ("poznamka.txt", "unsigned")])
        self.assertEqual([(file["size"], file["pdf"]) for file in description["files"]], [(len(PDF), True), (1, False), (1, False)])
        self.assertEqual(contents, [PDF, b"x", b"y"])
        [signature] = description["signatures"]
        self.assertTrue(signature["intact"], signature["broken_reason"])
        self.assertEqual(signature["files"], ["smlouva.pdf", "cenik.xlsx"])
        self.assertNotIn("references", signature)
        self.assertEqual((description["warnings"], description["unsupported"]), ([], ""))

    def test_changed_file_is_changed_and_others_invalid(self):
        signed = {"smlouva.pdf": PDF, "cenik.xlsx": b"x"}
        data = asic_samples.asic_e({"smlouva.pdf": PDF, "cenik.xlsx": b"zmeneno"}, {"META-INF/signatures001.xml": signatures_file(signed)})
        description, _ = asic_verify.describe(data)
        self.assertEqual(states(description), [("smlouva.pdf", "invalid"), ("cenik.xlsx", "changed")])
        [signature] = description["signatures"]
        self.assertFalse(signature["intact"])
        self.assertTrue(signature["content_changed"])
        self.assertEqual(signature["broken_reason"], xades_verify.CONTENT_CHANGED)

    def test_second_intact_signature_keeps_file_signed(self):
        broken = break_value(asic_samples.xades_signature(FILES, signature_id="S2"))
        data = asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures_file(FILES),
                                           "META-INF/signatures002.xml": asic_samples.signatures_xml(broken)})
        description, _ = asic_verify.describe(data)
        self.assertEqual(states(description), [("smlouva.pdf", "signed")])
        self.assertEqual([signature["intact"] for signature in description["signatures"]], [True, False])

    def test_asic_s_reference_without_uri(self):
        def drop_uri(signature):
            del signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference").attrib["URI"]
        signatures = signatures_file(FILES, mutate=drop_uri)
        description, _ = asic_verify.describe(asic_samples.asic_s("smlouva.pdf", PDF, signatures))
        self.assertEqual((description["kind"], description["label"]), ("asic-s", "ASiC-S s\u00a0podpisy XAdES"))
        self.assertEqual(states(description), [("smlouva.pdf", "signed")])
        self.assertEqual(description["signatures"][0]["files"], ["smlouva.pdf"])
        in_asic_e, _ = asic_verify.describe(asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures}))
        self.assertEqual(in_asic_e["signatures"][0]["broken_reason"], xades_verify.BAD_REFERENCE)
        self.assertEqual(states(in_asic_e), [("smlouva.pdf", "unsigned")])

    def test_cades_container_is_unverified(self):
        data = asic_samples.asic_s("smlouva.pdf", PDF, b"0\x00", kind="cades")
        self.assertTrue(asic_verify.is_signed_file(data))
        description, contents = asic_verify.describe(data)
        self.assertEqual(states(description), [("smlouva.pdf", "unverified")])
        self.assertEqual((description["signatures"], description["unsupported"]), ([], UNSUPPORTED))
        self.assertEqual(contents, [PDF])

    def test_time_stamp_and_evidence_record_containers_are_unverified(self):
        for name in ("META-INF/timestamp.tst", "META-INF/evidencerecord.ers"):
            data = zip_bytes([("mimetype", b"application/vnd.etsi.asic-s+zip"), ("smlouva.pdf", PDF), (name, b"0\x00")])
            self.assertTrue(asic_verify.is_signed_file(data), name)
            description, _ = asic_verify.describe(data)
            self.assertEqual(states(description), [("smlouva.pdf", "unverified")])
            self.assertEqual(description["unsupported"], UNSUPPORTED)

    def test_manifest_xml_reference_is_allowed_and_hidden(self):
        manifest = b"<manifest/>"
        signatures = signatures_file({**FILES, "META-INF/manifest.xml": manifest})
        data = asic_samples.asic_e(FILES, {"META-INF/manifest.xml": manifest, "META-INF/signatures001.xml": signatures}, manifest=False)
        description, contents = asic_verify.describe(data)
        [signature] = description["signatures"]
        self.assertTrue(signature["intact"], signature["broken_reason"])
        self.assertEqual(states(description), [("smlouva.pdf", "signed")])
        self.assertEqual(signature["files"], ["smlouva.pdf"])
        self.assertEqual(contents, [PDF])

    def test_reference_into_meta_inf_is_refused(self):
        other = b"<jine/>"
        signatures = signatures_file({**FILES, "META-INF/jine.xml": other})
        data = asic_samples.asic_e(FILES, {"META-INF/jine.xml": other, "META-INF/signatures001.xml": signatures})
        description, _ = asic_verify.describe(data)
        [signature] = description["signatures"]
        self.assertFalse(signature["intact"])
        self.assertEqual(signature["broken_reason"], xades_verify.BAD_REFERENCE)
        self.assertEqual(states(description), [("smlouva.pdf", "invalid")])

    def test_enveloping_base64_content_is_listed(self):
        data = asic_samples.enveloping(PDF, "application/pdf", base64_transform=True)
        self.assertTrue(asic_verify.is_signed_file(data))
        description, contents = asic_verify.describe(data)
        self.assertEqual(description["kind"], "xades")
        self.assertEqual(states(description), [("podepsaný obsah.pdf", "signed")])
        self.assertTrue(description["files"][0]["pdf"])
        self.assertEqual(contents, [PDF])
        [signature] = description["signatures"]
        self.assertTrue(signature["intact"], signature["broken_reason"])
        self.assertEqual(signature["files"], ["podepsaný obsah.pdf"])

    def test_enveloping_content_without_known_type_is_bin(self):
        description, contents = asic_verify.describe(asic_samples.enveloping(b"data", "application/x-neco", base64_transform=True))
        self.assertEqual(states(description), [("podepsaný obsah.bin", "signed")])
        self.assertEqual(contents, [b"data"])

    def test_changed_enveloping_content_is_changed(self):
        def change_content(signature):
            signature.find(f"{{{DS}}}Object[@Id='S1-content']").text = base64.b64encode(b"jiny obsah").decode()
        data = asic_samples.enveloping(PDF, "application/pdf", base64_transform=True)
        root = etree.fromstring(data)
        change_content(root)
        description, contents = asic_verify.describe(etree.tostring(root))
        self.assertEqual(states(description), [("podepsaný obsah.pdf", "changed")])
        self.assertEqual(contents, [b"jiny obsah"])

    def test_enveloping_xml_content_is_listed_as_verified(self):
        data = asic_samples.enveloping(PDF, "application/pdf", base64_transform=False)
        description, contents = asic_verify.describe(data)
        self.assertEqual(states(description), [("podepsaný obsah.xml", "signed")])
        content = etree.fromstring(data).find(f"{{{DS}}}Object[@Id='S1-content']")
        self.assertEqual(contents, [etree.tostring(content, method="c14n")])
        self.assertFalse(description["files"][0]["pdf"])

    def test_enveloped_document_verifies(self):
        data = asic_samples.enveloped(etree.fromstring(b"<faktura><castka>100</castka></faktura>"))
        self.assertTrue(asic_verify.is_signed_file(data))
        description, contents = asic_verify.describe(data)
        self.assertEqual((description["kind"], description["files"], contents), ("xades", [], []))
        [signature] = description["signatures"]
        self.assertTrue(signature["intact"], signature["broken_reason"])
        self.assertEqual((signature["files"], signature["covers_document"]), ([], True))

    def test_only_a_verified_enveloped_reference_covers_the_document(self):
        def change_document(signature):
            signature.getparent().find("castka").text = "999"
        root = etree.fromstring(asic_samples.enveloped(etree.fromstring(b"<faktura><castka>100</castka></faktura>")))
        change_document(root.find(f"{{{DS}}}Signature"))
        [changed] = asic_verify.describe(etree.tostring(root))[0]["signatures"]
        self.assertEqual((changed["intact"], changed["covers_document"]), (False, False))
        [enveloping] = asic_verify.describe(asic_samples.enveloping(PDF, "application/pdf", base64_transform=True))[0]["signatures"]
        self.assertFalse(enveloping["covers_document"])
        [container] = asic_verify.describe(asic_samples.asic_e(FILES, {"signatures.xml": signatures_file(FILES)}))[0]["signatures"]
        self.assertFalse(container["covers_document"])

    def test_plain_zfo_is_not_a_signed_file(self):
        zfo = (ROOT / "samples/mac-vyplneny/form.zfo").read_bytes()
        self.assertFalse(asic_verify.is_signed_file(zfo))
        with zipfile.ZipFile(io.BytesIO(zfo)) as archive:
            form = archive.read("form.fo")
        self.assertFalse(asic_verify.is_signed_file(form))
        self.assertFalse(asic_verify.is_signed_file(zip_bytes([("form.fo", form), ("META-INF/signatures.xml", signatures_file(FILES))])))
        self.assertFalse(asic_verify.is_signed_file(b"%PDF-1.7"))
        self.assertFalse(asic_verify.is_signed_file(zip_bytes([("smlouva.pdf", PDF)])))

    def test_signed_path_reads_only_the_zip_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            zfo, container = Path(folder) / "form.zfo", Path(folder) / "smlouva.asice"
            zfo.write_bytes((ROOT / "samples/mac-vyplneny/form.zfo").read_bytes())
            container.write_bytes(asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures_file(FILES)}))
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("read whole file")):
                self.assertFalse(asic_verify.is_signed_path(zfo))
                self.assertTrue(asic_verify.is_signed_path(container))

    def test_signature_with_empty_qualifying_properties_is_xades(self):
        data = f'<ds:Signature xmlns:ds="{DS}" xmlns:xades="{XADES}"><ds:Object><xades:QualifyingProperties/></ds:Object></ds:Signature>'.encode()
        self.assertTrue(asic_verify.is_signed_file(data))
        description, _ = asic_verify.describe(data)
        self.assertFalse(description["signatures"][0]["intact"])

    def test_percent_encoded_czech_name_resolves(self):
        nfc = unicodedata.normalize("NFC", "Příloha.pdf")
        nfd = unicodedata.normalize("NFD", nfc)
        signed = {nfc: PDF}
        data = asic_samples.asic_e({nfc: PDF, nfd: PDF}, {"META-INF/signatures001.xml": signatures_file(signed)})
        description, _ = asic_verify.describe(data)
        self.assertEqual(states(description), [(nfc, "signed"), (nfd, "unsigned")])
        self.assertEqual(description["signatures"][0]["files"], [nfc])

    def test_refused_reference_marks_the_file_invalid(self):
        nfc = unicodedata.normalize("NFC", "Příloha.pdf")
        def xpath_transform(uri):
            def mutate(signature):
                reference = signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference")
                reference.set("URI", uri)
                transforms = etree.Element(f"{{{DS}}}Transforms")
                etree.SubElement(transforms, f"{{{DS}}}Transform", Algorithm="http://www.w3.org/TR/1999/REC-xpath-19991116")
                reference.insert(0, transforms)
            return mutate
        for name, uri in (("smlouva.pdf", "/smlouva.pdf"), (nfc, "P%C5%99%C3%ADloha.pdf")):
            signatures = signatures_file({name: PDF}, mutate=xpath_transform(uri))
            description, _ = asic_verify.describe(asic_samples.asic_e({name: PDF}, {"META-INF/signatures001.xml": signatures}))
            [signature] = description["signatures"]
            self.assertEqual(signature["broken_reason"], xades_verify.UNKNOWN_TRANSFORM)
            self.assertEqual(states(description), [(name, "invalid")])
            self.assertEqual(signature["files"], [name])

    def test_unparsable_reference_uri_is_refused(self):
        def odd_uri(signature):
            signature.find(f"{{{DS}}}SignedInfo/{{{DS}}}Reference").set("URI", "//[x")
        signatures = signatures_file(FILES, mutate=odd_uri)
        description, _ = asic_verify.describe(asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures}))
        self.assertEqual(description["signatures"][0]["broken_reason"], xades_verify.BAD_REFERENCE)
        self.assertEqual(states(description), [("smlouva.pdf", "unsigned")])

    def test_files_behind_a_changed_manifest_are_changed(self):
        files = {"smlouva.pdf": PDF, "priloha.txt": b"priloha"}
        signature = asic_samples.xades_signature(files, manifest=True)
        signature.find(f"{{{DS}}}Object/{{{DS}}}Manifest/{{{DS}}}Reference/{{{DS}}}DigestValue").text = base64.b64encode(bytes(32)).decode()
        data = asic_samples.asic_e(files, {"META-INF/signatures001.xml": asic_samples.signatures_xml(signature)})
        description, _ = asic_verify.describe(data)
        [described] = description["signatures"]
        self.assertTrue(described["content_changed"])
        self.assertEqual(states(description), [("smlouva.pdf", "changed"), ("priloha.txt", "changed")])
        self.assertEqual(described["files"], ["smlouva.pdf", "priloha.txt"])

    def test_malformed_signature_file_is_one_invalid_card(self):
        data = asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures_file(FILES),
                                           "META-INF/signatures002.xml": b"<asic:XAdESSignatures"})
        description, _ = asic_verify.describe(data)
        intact, broken = description["signatures"]
        self.assertTrue(intact["intact"])
        self.assertEqual((broken["intact"], broken["broken_reason"], broken["subject"], broken["files"]), (False, UNREADABLE, "", []))
        self.assertEqual(states(description), [("smlouva.pdf", "signed")])

    def test_directories_and_mimetype_are_not_listed(self):
        signed = {"smlouva.pdf": PDF, "slozka/priloha.txt": b"p"}
        data = zip_bytes([("mimetype", ASIC_E_MIMETYPE), ("slozka/", b""), *signed.items(),
                          ("META-INF/", b""), ("META-INF/signatures001.xml", signatures_file(signed))])
        description, contents = asic_verify.describe(data)
        self.assertEqual(states(description), [("smlouva.pdf", "signed"), ("slozka/priloha.txt", "signed")])
        self.assertEqual(contents, [PDF, b"p"])

    def test_rule_breaks_are_warnings(self):
        signatures = signatures_file(FILES)
        without_manifest, _ = asic_verify.describe(asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures}, manifest=False))
        late_mimetype, _ = asic_verify.describe(zip_bytes([*FILES.items(), ("mimetype", ASIC_E_MIMETYPE),
                                                           ("META-INF/signatures001.xml", signatures)]))
        for description in (without_manifest, late_mimetype):
            self.assertNotEqual(description["warnings"], [])
            self.assertTrue(description["signatures"][0]["intact"])
            self.assertEqual(states(description), [("smlouva.pdf", "signed")])

    def test_uri_to_name(self):
        nfc = unicodedata.normalize("NFC", "Příloha.pdf")
        names = {"smlouva.pdf", nfc, "META-INF/manifest.xml", "META-INF/signatures.xml", "a/b.txt"}
        accepted = {"smlouva.pdf": "smlouva.pdf", "/smlouva.pdf": "smlouva.pdf", "P%C5%99%C3%ADloha.pdf": nfc,
                    "META-INF/manifest.xml": "META-INF/manifest.xml", "a/b.txt": "a/b.txt", "a%2Fb.txt": "a/b.txt"}
        for uri, name in accepted.items():
            self.assertEqual(asic_verify.uri_to_name(uri, names, single=None), name, uri)
        for uri in ("//smlouva.pdf", "../smlouva.pdf", "a/../smlouva.pdf", "%2E%2E/smlouva.pdf", "file:///smlouva.pdf",
                    "http://example.com/smlouva.pdf", "a\\b.txt", "a%5Cb.txt", "META-INF/signatures.xml",
                    "/META-INF/signatures.xml", "%FF.pdf", "chybi.pdf", "", "//[x", "http://[::1/smlouva.pdf"):
            self.assertIsNone(asic_verify.uri_to_name(uri, names, single="smlouva.pdf"), uri)
        self.assertEqual(asic_verify.uri_to_name(None, names, single="smlouva.pdf"), "smlouva.pdf")
        self.assertIsNone(asic_verify.uri_to_name(None, names, single=None))


class ContainerLimitsTest(unittest.TestCase):
    def assertDamaged(self, data):
        with self.assertRaises(asic_verify.ContainerError):
            asic_verify.describe(data)

    def signed(self, *entries):
        return zip_bytes([*entries, ("META-INF/signatures001.xml", signatures_file(FILES))])

    def test_unreadable_zip_is_damaged(self):
        self.assertDamaged(asic_samples.asic_e(FILES, {"META-INF/signatures001.xml": signatures_file(FILES)})[:-30])

    def test_unsafe_names_are_damaged(self):
        for name in ("../smlouva.pdf", "/smlouva.pdf", "a\\smlouva.pdf", "C:/smlouva.pdf"):
            self.assertDamaged(self.signed((name, PDF)))

    def test_backslash_name_is_damaged_where_zipfile_reads_it_as_a_folder(self):
        data = self.signed(("a\\smlouva.pdf", PDF))
        with mock.patch.object(os, "sep", "\\"):
            self.assertDamaged(data)

    def test_duplicate_names_are_damaged(self):
        with self.assertWarns(UserWarning):
            data = self.signed(("smlouva.pdf", PDF), ("smlouva.pdf", PDF))
        self.assertDamaged(data)

    def test_entry_larger_than_declared_is_damaged(self):
        data = bytearray(self.signed(("smlouva.pdf", PDF * 100)))
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<I", data, central + 24, 10)
        struct.pack_into("<I", data, 22, 10)
        self.assertDamaged(bytes(data))

    def test_high_compression_ratio_is_damaged(self):
        self.assertDamaged(self.signed(("nuly.bin", bytes(1_000_000))))

    def test_limits(self):
        data = self.signed(("smlouva.pdf", PDF), ("cenik.xlsx", b"x" * 50))
        for limit, value in (("MAX_ENTRIES", 2), ("MAX_TOTAL_SIZE", 100)):
            original = getattr(asic_verify, limit)
            setattr(asic_verify, limit, value)
            try:
                self.assertDamaged(data)
            finally:
                setattr(asic_verify, limit, original)

    def test_encrypted_entry_is_damaged(self):
        data = bytearray(self.signed(("smlouva.pdf", PDF)))
        data[6] |= 1
        central = data.index(b"PK\x01\x02")
        data[central + 8] |= 1
        self.assertDamaged(bytes(data))

    def test_unknown_compression_is_damaged(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("smlouva.pdf", PDF, compress_type=zipfile.ZIP_BZIP2)
            archive.writestr("META-INF/signatures001.xml", signatures_file(FILES))
        self.assertDamaged(buffer.getvalue())

    def test_xml_without_signature_is_damaged(self):
        self.assertDamaged(b"<faktura/>")


class ContainerPages(unittest.TestCase):
    PDF = b"%PDF-1.7 smlouva"
    HOSTILE = "</script><img src=x onerror=alert(1)>.txt"

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        patcher = mock.patch.object(app, "SINGLE_FILE", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def open_container(self, data, name="smlouva.asice"):
        path = self.folder / name
        path.write_bytes(data)
        app.FORMS["container-test"] = path
        self.addCleanup(app.FORMS.pop, "container-test", None)
        return path

    def signed(self, files=None, **options):
        files = files or {"smlouva.pdf": self.PDF}
        signature = asic_samples.xades_signature(files, **options)
        return asic_samples.asic_e(files, {"signatures.xml": asic_samples.signatures_xml(signature)})

    def request(self, path, host=None, header="Content-Disposition"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("GET", path, headers={"Host": host or f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.getheader("Content-Type"), response.getheader(header), response.read()

    def page_json(self, page, marker):
        return json.loads(re.search(rf"const {marker} = (.*);\n", page).group(1))

    def post(self, path, body=b"[]"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        connection.request("POST", path, body=body, headers={"Host": f"127.0.0.1:{self.port}"})
        response = connection.getresponse()
        return response.status, response.read()

    def test_container_session_answers_only_revocation_posts(self):
        _, session = self.render(self.signed())
        self.assertEqual(self.post(f"/revocation?s={session}"), (200, b"{}"))
        for path in ("/ipc", "/save", "/pdf-save", "/export-data"):
            with self.subTest(path=path):
                self.assertEqual(self.post(f"{path}?s={session}", b"{}")[0], 404)

    def render(self, data):
        self.open_container(data)
        page = app.render_container_page("container-test")
        return page, re.search(r"const SESSION = '([\w-]+)'", page).group(1)

    def test_container_page_carries_files_and_signatures_as_json(self):
        page, session = self.render(self.signed())
        container = self.page_json(page, "CONTAINER")
        self.assertEqual(self.page_json(page, "FILE"), "smlouva.asice")
        self.assertEqual([file["name"] for file in container["files"]], ["smlouva.pdf"])
        self.assertEqual(container["signatures"][0]["files"], ["smlouva.pdf"])
        self.assertNotIn("der", container["signatures"][0])
        self.assertEqual(app.SESSIONS[session]["entries"], [self.PDF])
        self.assertEqual(self.request("/?form=container-test")[0], 200)

    def test_container_file_route_needs_the_session(self):
        self.render(self.signed())
        self.assertEqual(self.request("/container-file?i=0")[0], 404)
        self.assertEqual(self.request("/container-file?s=nope&i=0")[0], 404)
        self.assertEqual(self.request("/container-file?s=x&i=0", host="attacker.example")[0], 403)

    def test_container_file_route_serves_by_index(self):
        _, session = self.render(self.signed())
        status, content_type, disposition, body = self.request(f"/container-file?s={session}&i=0")
        self.assertEqual((status, content_type, body), (200, "application/octet-stream", self.PDF))
        self.assertEqual(disposition, "attachment; filename*=UTF-8''smlouva.pdf")
        for index in ("99", "-1", "x", ""):
            self.assertEqual(self.request(f"/container-file?s={session}&i={index}")[0], 404, index)

    def test_no_entry_is_sniffed_as_a_page(self):
        _, session = self.render(self.signed({"smlouva.pdf": self.PDF, "stranka.html": b"<script>alert(1)</script>"}))
        for index in (0, 1):
            status, content_type, sniffing, _ = self.request(f"/container-file?s={session}&i={index}", header="X-Content-Type-Options")
            self.assertEqual((status, content_type, sniffing), (200, "application/octet-stream", "nosniff"), index)

    def test_shared_files_script_is_served_without_a_session(self):
        status, content_type, _, body = self.request("/files.js")
        self.assertEqual((status, content_type), (200, "text/javascript"))
        self.assertEqual(body, (ROOT / "ui/files.js").read_bytes())
        self.assertEqual(self.request("/files.js", host="attacker.example")[0], 403)

    def test_both_pages_use_the_shared_files_script(self):
        for page in ("container", "message"):
            self.assertIn('<script src="/files.js"></script>', (ROOT / f"ui/{page}.html").read_text(encoding="utf-8"), page)
        script = (ROOT / "ui/files.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\} (B|kB|MB|%)`")
        self.assertIn("Math.round(event.scale * 100) + '\u00a0%'", script)

    def test_non_pdf_entry_is_a_download_named_by_its_basename(self):
        files = {"složka/příloha č. 1.txt": b"text"}
        _, session = self.render(self.signed(files))
        status, content_type, disposition, body = self.request(f"/container-file?s={session}&i=0")
        self.assertEqual((status, content_type, body), (200, "application/octet-stream", b"text"))
        self.assertEqual(disposition, "attachment; filename*=UTF-8''p%C5%99%C3%ADloha%20%C4%8D.%201.txt")

    def test_hostile_file_name_stays_json(self):
        page, _ = self.render(self.signed({self.HOSTILE: b"x"}))
        self.assertNotIn("</script><img", page)
        self.assertNotIn("<img src=x", page)
        self.assertEqual(self.page_json(page, "CONTAINER")["files"][0]["name"], self.HOSTILE)

    def test_markers_in_file_data_are_not_substituted(self):
        page, _ = self.render(self.signed({"__FILE__.txt": b"x", "__SESSION__.txt": b"y"}))
        self.assertEqual([file["name"] for file in self.page_json(page, "CONTAINER")["files"]], ["__FILE__.txt", "__SESSION__.txt"])
        self.open_container(self.signed(), "__CONTAINER__.asice")
        page = app.render_container_page("container-test")
        self.assertEqual(self.page_json(page, "FILE"), "__CONTAINER__.asice")
        self.assertEqual(self.page_json(page, "CONTAINER")["files"][0]["name"], "smlouva.pdf")

    def test_huge_index_is_not_found(self):
        _, session = self.render(self.signed())
        self.assertEqual(self.request(f"/container-file?s={session}&i={'9' * 5000}")[0], 404)

    def test_container_without_pdf_renders(self):
        page, _ = self.render(self.signed({"a.txt": b"a", "b.txt": b"b"}))
        self.assertEqual(len(self.page_json(page, "CONTAINER")["files"]), 2)

    def test_untrusted_intact_signature_is_not_broken(self):
        page, _ = self.render(self.signed())
        signature = self.page_json(page, "CONTAINER")["signatures"][0]
        self.assertIs(signature["intact"], True)
        self.assertIs(signature["trusted"], False)

    def test_signature_file_without_certificate_still_renders(self):
        data = asic_samples.asic_e({"smlouva.pdf": self.PDF}, {"signatures.xml": b"<broken"})
        page, _ = self.render(data)
        signature = self.page_json(page, "CONTAINER")["signatures"][0]
        self.assertEqual((signature["intact"], signature["trusted"], signature["unverifiable"], signature["certificate"]),
                         (False, False, False, ""))
        self.assertNotIn("der", signature)

    def test_damaged_container_shows_the_damaged_page(self):
        data = self.signed()
        self.open_container(data[:len(data) // 2])
        status, _, _, body = self.request("/?form=container-test")
        self.assertEqual(status, 422)
        self.assertIn("Soubor nelze otevřít".encode(), body)

    def test_zfo_is_not_taken_for_a_container(self):
        self.open_container((ROOT / "samples/cuzk/form.zfo").read_bytes(), "form.zfo")
        self.assertEqual(self.request("/?form=container-test")[0], 200)
        self.assertNotIn("const CONTAINER", self.request("/?form=container-test")[3].decode())
