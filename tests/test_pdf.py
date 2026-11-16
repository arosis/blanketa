"""PDF forms: their data files, signature field names, choice fields after signing and time stamps."""
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from asn1crypto import cms
from asn1crypto import crl as asn1_crl
from asn1crypto import pdf as asn1_pdf
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.sign import fields, signers
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from support import ROOT

import app
import pdf_form_data
import pdf_sign
import trustlist


class SignatureFieldNames(unittest.TestCase):
    """Czech offices name signature fields like "El.podpis [0]", which pyHanko's analysis of later changes refuses."""

    KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    NAME = x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CZ"), x509.NameAttribute(x509.NameOID.COMMON_NAME, "Test")])
    CERT = (x509.CertificateBuilder().subject_name(NAME).issuer_name(NAME).public_key(KEY.public_key()).serial_number(1)
            .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1)).not_valid_after(datetime.now(timezone.utc) + timedelta(days=1))
            .sign(KEY, hashes.SHA256()))

    def signed(self):
        writer, out = PdfWriter(), io.BytesIO()
        writer.add_blank_page(200, 200)
        field = writer._add_object(DictionaryObject({
            NameObject("/FT"): NameObject("/Sig"), NameObject("/T"): TextStringObject("El.podpis [0]"),
            NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4), NameObject("/F"): NumberObject(132)}))
        writer.pages[0][NameObject("/Annots")] = ArrayObject([field])
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field])}))
        writer.write(out)
        return pdf_sign.sign_pdf(out.getvalue(), self.KEY, self.CERT)

    def test_last_signature_covering_the_file_is_intact(self):
        found = pdf_sign.describe_signatures(self.signed())[0]
        self.assertTrue(found["intact"])
        self.assertTrue(found["covers_whole"])
        self.assertFalse(found["changes_unchecked"])

    def test_later_changes_that_cannot_be_analysed_are_reported(self):
        first, second = pdf_sign.describe_signatures(pdf_sign.sign_pdf(self.signed(), self.KEY, self.CERT))
        self.assertTrue(first["intact"])
        self.assertFalse(first["covers_whole"])
        self.assertTrue(first["changes_unchecked"])
        self.assertFalse(first["modified"])
        self.assertFalse(second["changes_unchecked"])

    def test_changed_content_is_still_broken(self):
        signed = self.signed()
        at = signed.index(b"/MediaBox")
        found = pdf_sign.describe_signatures(signed[:at] + b"/MediaBoy" + signed[at + 9:])[0]
        self.assertFalse(found["intact"])
        self.assertFalse(found["changes_unchecked"])

    def test_ordinary_field_is_analysed(self):
        document = pdf_sign.sign_pdf(pdf_sign.sign_pdf((ROOT / "samples-pdf/zadost.pdf").read_bytes(), self.KEY, self.CERT), self.KEY, self.CERT)
        self.assertFalse(any(found["changes_unchecked"] for found in pdf_sign.describe_signatures(document)))


class PdfFormData(unittest.TestCase):
    SOURCE = ROOT / "samples-pdf/zadost.pdf"
    VALUES = {"jmeno": "Jan Žluťoučký (ml.) \\ a závorka )", "souhlas": True, "doruceni": "posta", "ucel": "Pro soud"}
    KEY, CERT = SignatureFieldNames.KEY, SignatureFieldNames.CERT

    def setUp(self):
        app.FORMS["pdf-data-test"] = self.SOURCE
        self.addCleanup(app.FORMS.pop, "pdf-data-test", None)

    def session(self, pdf=None):
        return {"name": "pdf-data-test", "pdf": pdf or self.SOURCE.read_bytes()}

    def exported(self, pdf=None, values=VALUES):
        return app.export_pdf_data(self.session(pdf), values)

    def test_both_formats_bring_the_values_back(self):
        contents = self.exported()["contents"]
        for extension in ("xfdf", "fdf"):
            with self.subTest(extension=extension):
                result = app.import_pdf_data(self.session(), contents[extension].encode("utf-8"))
                self.assertEqual({name: result["values"][name] for name in self.VALUES}, self.VALUES)
                self.assertEqual(result["values"]["email"], "")
                self.assertEqual((result["missing"], result["unchangeable"], result["not_offered"], result["several_values"]), ([], [], [], []))

    def test_export_names_the_pdf_and_holds_saved_values(self):
        exported = self.exported(values={})
        self.assertEqual(exported["name"], "pdf-data-test-data.xfdf")
        self.assertIn('href="zadost.pdf"', exported["contents"]["xfdf"])
        self.assertIn("/F (zadost\\056pdf)", exported["contents"]["fdf"])
        self.assertEqual(app.import_pdf_data(self.session(), exported["contents"]["fdf"].encode())["values"]["ucel"], "Pro banku")
        self.assertTrue(exported["contents"]["fdf"].isascii())

    def test_names_of_nested_fields_are_kept(self):
        values = {"osoba.jmeno": "Jan", "osoba.adresa.obec": "Brno", "osoba": "kořen"}
        for write in (pdf_form_data.write_xfdf, pdf_form_data.write_fdf):
            with self.subTest(write=write.__name__):
                self.assertEqual(pdf_form_data.read_form_data(write(values, "x.pdf").encode()), values)

    def test_fields_missing_in_the_form_are_listed(self):
        data = pdf_form_data.write_xfdf({"jmeno": "Jan", "stare_pole": "x", "jine.pole": "y"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(), data)
        self.assertEqual(result["values"], {"jmeno": "Jan"})
        self.assertEqual(sorted(result["missing"]), ["jine.pole", "stare_pole"])

    def test_values_the_form_does_not_offer_are_listed(self):
        data = pdf_form_data.write_xfdf({"ucel": "Pro úřad", "doruceni": "kurýrem", "souhlas": "Ne", "jmeno": "Jan"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(), data)
        self.assertEqual(result["values"], {"jmeno": "Jan"})
        self.assertEqual(result["not_offered"], [{"field": "ucel", "value": "Pro úřad"}, {"field": "doruceni", "value": "kurýrem"},
                                                 {"field": "souhlas", "value": "Ne"}])

    def test_unchecked_states_clear_buttons(self):
        for unchecked in ("Off", ""):
            with self.subTest(unchecked=unchecked):
                data = pdf_form_data.write_xfdf({"souhlas": unchecked, "doruceni": unchecked}, "x.pdf").encode()
                result = app.import_pdf_data(self.session(), data)
                self.assertEqual((result["values"], result["not_offered"]), ({"souhlas": False, "doruceni": unchecked}, []))

    def test_several_values_for_one_field_are_listed(self):
        for write in (pdf_form_data.write_xfdf, pdf_form_data.write_fdf):
            with self.subTest(write=write.__name__):
                result = app.import_pdf_data(self.session(), write({"ucel": ["Pro soud", "Ostatní"], "email": ["a@b.cz"]}, "x.pdf").encode())
                self.assertEqual((result["values"], result["several_values"]), ({"email": "a@b.cz"}, ["ucel"]))

    def test_read_only_and_signature_fields_are_not_changed(self):
        writer = PdfWriter(clone_from=self.SOURCE)
        for annotation in writer.pages[0]["/Annots"]:
            if annotation.get_object().get("/T") == "jmeno":
                annotation.get_object()[NameObject("/Ff")] = NumberObject(pdf_form_data.FIELD_FLAG_READ_ONLY)
        out = io.BytesIO()
        writer.write(out)
        data = pdf_form_data.write_xfdf({"jmeno": "Jan", "podpis_zadatele": "x", "email": "a@b.cz"}, "x.pdf").encode()
        result = app.import_pdf_data(self.session(out.getvalue()), data)
        self.assertEqual(result["values"], {"email": "a@b.cz"})
        self.assertEqual(sorted(result["unchangeable"]), ["jmeno", "podpis_zadatele"])

    def signed_with(self, **options):
        writer = IncrementalPdfFileWriter(io.BytesIO(self.SOURCE.read_bytes()), strict=False)
        metadata = signers.PdfSignatureMetadata(**{"field_name": "podpis_zadatele", "md_algorithm": "sha256", **options.pop("metadata", {})})
        signer = signers.PdfSigner(metadata, pdf_sign.ExternalKeySigner(self.KEY, self.CERT), **options)
        return signer.sign_pdf(writer).getvalue()

    def filled_after_signing(self, values):
        signed = pdf_sign.sign_pdf(self.SOURCE.read_bytes(), self.KEY, self.CERT)
        data = pdf_form_data.write_xfdf(values, "zadost.pdf").encode()
        filled = pdf_sign.fill_form_fields(signed, app.import_pdf_data(self.session(signed), data)["values"])
        self.assertTrue(filled.startswith(signed))
        return filled, pdf_sign.describe_signatures(filled)[0]

    def test_values_loaded_into_a_signed_pdf_keep_the_signature_intact(self):
        filled, found = self.filled_after_signing(self.VALUES)
        self.assertTrue(found["intact"])
        self.assertEqual(PdfReader(io.BytesIO(filled)).get_fields()["jmeno"]["/V"], self.VALUES["jmeno"])

    def test_text_and_checkbox_loaded_after_signing_count_as_form_filling(self):
        _, found = self.filled_after_signing({"jmeno": "Jan", "email": "a@b.cz", "souhlas": True})
        self.assertTrue(found["intact"])
        self.assertFalse(found["modified"])

    def test_fields_locked_by_a_signature_are_not_changed(self):
        locked = self.signed_with(metadata={"field_name": "zamek"},
                                  new_field_spec=fields.SigFieldSpec("zamek", field_mdp_spec=fields.FieldMDPSpec(
                                      fields.FieldMDPAction.INCLUDE, ["jmeno"])))
        result = app.import_pdf_data(self.session(locked), self.exported()["contents"]["xfdf"].encode())
        self.assertIn("jmeno", result["unchangeable"])
        self.assertNotIn("jmeno", result["values"])
        self.assertEqual(result["values"]["ucel"], "Pro soud")

    def test_pdf_certified_against_changes_is_refused(self):
        certified = self.signed_with(metadata={"certify": True, "docmdp_permissions": fields.MDPPerm.NO_CHANGES})
        with self.assertRaisesRegex(ValueError, "nedovoluje žádné další změny"):
            app.import_pdf_data(self.session(certified), self.exported()["contents"]["xfdf"].encode())

    def test_other_files_get_a_message(self):
        for data in (b"", b"plain text", b"<html/>", b"%FDF-1.2\n%%EOF"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                app.import_pdf_data(self.session(), data)


class ChoiceFieldsAfterSigning(unittest.TestCase):
    """A choice field keeps its selected indices (/I) only where ISO 32000-1 lets /V outrank them."""

    def field(self, name):
        return PdfReader(io.BytesIO(self.filled)).get_fields()[name]

    def test_single_select_keeps_earlier_signature_clean(self):
        signed = pdf_sign.sign_pdf((ROOT / "samples-pdf/zadost.pdf").read_bytes(), SignatureFieldNames.KEY, SignatureFieldNames.CERT)
        self.filled = pdf_sign.fill_form_fields(signed, {"ucel": "Pro soud"})
        self.assertFalse(pdf_sign.describe_signatures(self.filled)[0]["modified"])
        self.assertEqual(self.field("ucel")["/V"], "Pro soud")

    def test_multi_select_indices_follow_the_new_value(self):
        writer, out = PdfWriter(), io.BytesIO()
        writer.add_blank_page(200, 200)
        field = writer._add_object(DictionaryObject({
            NameObject("/FT"): NameObject("/Ch"), NameObject("/T"): TextStringObject("volby"),
            NameObject("/Ff"): NumberObject(pdf_sign.MULTI_SELECT), NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"), NameObject("/Rect"): ArrayObject([NumberObject(0)] * 4),
            NameObject("/Opt"): ArrayObject(TextStringObject(option) for option in ("a", "b", "c")),
            NameObject("/V"): ArrayObject([TextStringObject("a")]), NameObject("/I"): ArrayObject([NumberObject(0)])}))
        writer.pages[0][NameObject("/Annots")] = ArrayObject([field])
        writer.root_object[NameObject("/AcroForm")] = writer._add_object(DictionaryObject({NameObject("/Fields"): ArrayObject([field])}))
        writer.write(out)
        self.filled = pdf_sign.fill_form_fields(out.getvalue(), {"volby": ["b", "c"]})
        self.assertEqual(self.field("volby").indirect_reference.get_object()["/I"], [1, 2])


class TimeStamps(unittest.TestCase):
    """samples-pdf/razitka comes from tools/make-timestamp-samples.py."""
    FOLDER = ROOT / "samples-pdf/razitka"

    def read(self, name):
        return pdf_sign.describe_signatures((self.FOLDER / f"{name}.pdf").read_bytes())

    @staticmethod
    def stamp(kind="signature", at="2026-01-15T10:00:00+00:00", intact=True):
        return {"kind": kind, "intact": intact, "at": at if intact else None, "issuer": "TSA", "der": b"certificate"}

    def judge(self, stamps, verdict):
        sig = {"stamps": stamps}
        with mock.patch.object(app.certstore, "stamp_trust_verdict", return_value=verdict) as asked:
            app.judge_time_stamps(sig)
        return sig, asked

    def test_signature_without_a_stamp_has_none(self):
        self.assertEqual(self.read("bez-razitka")[0]["stamps"], [])

    def test_stamp_inside_a_signature_gives_its_time_and_authority(self):
        found = self.read("razitko-v-podpisu")[0]
        self.assertTrue(found["intact"])
        self.assertEqual(found["stamps"][0]["kind"], "signature")
        self.assertEqual(found["stamps"][0]["at"], "2026-01-15T10:00:00+00:00")
        self.assertEqual(found["stamps"][0]["issuer"], "Blanketa TEST TSA")

    def test_document_stamp_is_not_a_broken_signature(self):
        signature, stamp = self.read("razitko-dokumentu")
        self.assertTrue(stamp["document_stamp"])
        self.assertTrue(stamp["intact"])
        self.assertTrue(signature["intact"])

    def test_document_stamp_proves_a_signature_it_covers(self):
        signature = self.read("razitko-dokumentu")[0]
        self.assertEqual([(s["kind"], s["at"]) for s in signature["stamps"]], [("document", "2026-01-16T10:00:00+00:00")])

    def test_document_stamp_before_the_signature_proves_nothing(self):
        stamp, signature = self.read("razitko-pred-podpisem")
        self.assertTrue(stamp["document_stamp"])
        self.assertEqual(signature["stamps"], [])

    def test_document_stamp_judged_for_its_card_and_its_signature(self):
        data = (self.FOLDER / "razitko-dokumentu.pdf").read_bytes()
        trust = [{"trusted": False, "unverifiable": False, "source": "system"}] * 2
        with (mock.patch.object(app.certstore, "stamp_trust_verdict", return_value={"qualified": True, "unverifiable": False, "reason": ""}),
              mock.patch.object(app.certstore, "trust_verdicts", return_value=trust)):
            signature, stamp = app.pdf_signatures(data)
        self.assertEqual(signature["proof"]["kind"], "document")
        self.assertEqual(stamp["proof"]["kind"], "document")

    def test_document_stamp_moved_behind_a_signature_proves_nothing(self):
        found = {bool(sig.get("document_stamp")): sig for sig in self.read("razitko-premistene")}
        self.assertTrue(found[True]["intact"])
        self.assertEqual(found[False]["stamps"], [])

    def test_damaged_stamp_attribute_keeps_the_panel_working(self):
        data = (self.FOLDER / "razitko-v-podpisu.pdf").read_bytes()
        at = data.lower().index(b"060b2a864886f70d010910020e")  # the attribute's type; a signature does not cover it
        found = pdf_sign.describe_signatures(data[:at] + b"05" + data[at + 2:])[0]
        self.assertFalse(found["stamps"][0]["intact"])

    def test_stamp_of_another_document_does_not_break_the_signature(self):
        found = self.read("razitko-jineho-dokumentu")[0]
        self.assertTrue(found["intact"])
        self.assertFalse(found["stamps"][0]["intact"])

    def test_changed_content_breaks_a_document_stamp(self):
        data = (self.FOLDER / "razitko-dokumentu.pdf").read_bytes()
        at = data.index(b"/MediaBox")
        stamp = pdf_sign.describe_signatures(data[:at] + b"/MediaBoy" + data[at + 9:])[1]
        self.assertFalse(stamp["intact"])

    def test_qualified_stamp_is_proof(self):
        sig, asked = self.judge([self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertEqual(sig["proof"]["at"], "2026-01-15T10:00:00+00:00")
        self.assertEqual(sig["stamp_problem"], "")
        self.assertEqual(asked.call_args.args[1], datetime(2026, 1, 15, 10, tzinfo=timezone.utc))
        self.assertNotIn("der", sig["proof"])

    def test_earliest_good_stamp_counts(self):
        later = self.stamp("document", "2030-01-01T00:00:00+00:00")
        sig, _ = self.judge([later, self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertEqual(sig["proof"]["kind"], "signature")

    def test_untrusted_stamp_is_no_proof_and_says_why(self):
        sig, _ = self.judge([self.stamp()], {"qualified": False, "unverifiable": False, "reason": "Není kvalifikované."})
        self.assertIsNone(sig["proof"])
        self.assertEqual(sig["stamp_problem"], "Není kvalifikované.")

    def test_damaged_stamp_is_no_proof_and_asks_nothing(self):
        sig, asked = self.judge([self.stamp(intact=False)], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertIsNone(sig["proof"])
        self.assertIn("poškozené", sig["stamp_problem"])
        asked.assert_not_called()

    def test_unverifiable_stamp_is_not_called_untrusted(self):
        sig, _ = self.judge([self.stamp()], {"qualified": False, "unverifiable": True, "reason": "Seznam nejde načíst."})
        self.assertIsNone(sig["proof"])
        self.assertTrue(sig["stamp_unverifiable"])

    def test_good_stamp_hides_a_bad_one(self):
        sig, _ = self.judge([self.stamp(intact=False), self.stamp()], {"qualified": True, "unverifiable": False, "reason": ""})
        self.assertIsNotNone(sig["proof"])
        self.assertEqual(sig["stamp_problem"], "")


class RevocationKeptInAFile(unittest.TestCase):
    """A PDF may carry revocation lists and issuer's answers from the time it was signed (PAdES-LT)."""
    SIGNED = (ROOT / "samples-pdf/razitka/razitko-v-podpisu.pdf").read_bytes()

    @staticmethod
    def with_document_store(pdf, lists=(), answers=()):
        writer = IncrementalPdfFileWriter(io.BytesIO(pdf), strict=False)
        store = generic.DictionaryObject({
            generic.pdf_name(name): generic.ArrayObject([writer.add_object(generic.StreamObject(stream_data=data)) for data in kept])
            for name, kept in (("/CRLs", lists), ("/OCSPs", answers))})
        writer.root[generic.pdf_name("/DSS")] = writer.add_object(store)
        writer.update_root()
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue()

    @staticmethod
    def revocation_list(issued=None):
        issued = issued or datetime.now(timezone.utc)
        return (x509.CertificateRevocationListBuilder().issuer_name(SignatureFieldNames.NAME).last_update(issued)
                .next_update(issued + timedelta(days=1)).sign(SignatureFieldNames.KEY, hashes.SHA256()).public_bytes(serialization.Encoding.DER))

    def test_signature_of_a_file_without_any_carries_nothing(self):
        self.assertEqual(pdf_sign.describe_signatures(self.SIGNED)[0]["kept_revocation"], {"lists": [], "answers": []})

    def test_document_store_is_read_and_the_signature_stays_intact(self):
        found = pdf_sign.describe_signatures(self.with_document_store(self.SIGNED, [b"list"], [b"answer", b"answer"]))[0]
        self.assertEqual(found["kept_revocation"], {"lists": [b"list"], "answers": [b"answer"]})
        self.assertTrue(found["intact"])
        self.assertFalse(found["modified"])

    def test_only_a_bounded_number_is_read(self):
        many = [b"list %d" % number for number in range(100)]
        found = pdf_sign.describe_signatures(self.with_document_store(self.SIGNED, many))[0]
        self.assertEqual(found["kept_revocation"]["lists"], many[:32])

    def test_damaged_store_carries_nothing(self):
        damaged = self.with_document_store(self.SIGNED, [b"list"]).replace(b"/CRLs [", b"/CRLs 7 %")
        self.assertEqual(pdf_sign.describe_signatures(damaged)[0]["kept_revocation"]["lists"], [])

    def test_lists_kept_inside_the_signature_are_read(self):
        kept = self.revocation_list()
        archive = asn1_pdf.RevocationInfoArchival({"crl": [asn1_crl.CertificateList.load(kept)]})
        attribute = cms.CMSAttribute({"type": "adobe_revocation_info_archival", "values": [archive]})
        signature = mock.Mock(signer_info={"signed_attrs": cms.CMSAttributes([attribute])})
        self.assertEqual(pdf_sign._revocation_kept_in_signature(signature), {"lists": [kept], "answers": []})

    def test_status_is_judged_at_the_time_the_stamp_proves(self):
        trust = [{"trusted": True, "unverifiable": False, "source": "eu"}]
        said = {"status": "good", "revoked_at": None, "suspended": False, "reason": "", "from_file": {"kind": "crl", "issued_at": "x"}}
        with (mock.patch.object(app.certstore, "stamp_trust_verdict", return_value={"qualified": True, "unverifiable": False, "reason": ""}),
              mock.patch.object(app.certstore, "trust_verdicts", return_value=trust),
              mock.patch.object(app.certstore, "revocation_status_kept_in_file", return_value=said) as judged):
            signature = app.pdf_signatures(self.with_document_store(self.SIGNED, [b"list"]))[0]
        self.assertEqual(signature["status_in_file"], said)
        self.assertNotIn("kept_revocation", signature)
        self.assertEqual(judged.call_args.args[1:], ({"lists": [b"list"], "answers": []}, datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)))

    def test_file_without_kept_status_is_not_judged(self):
        with (mock.patch.object(app.certstore, "trust_verdicts", return_value=[{"trusted": True, "unverifiable": False, "source": "eu"}]),
              mock.patch.object(app.certstore, "revocation_status_kept_in_file") as judged):
            signature = app.pdf_signatures(self.SIGNED)[0]
        self.assertNotIn("status_in_file", signature)
        judged.assert_not_called()

    def test_genuine_list_in_a_stamped_file_says_the_certificate_was_good(self):
        signed_at = datetime.now(timezone.utc) - timedelta(days=1)

        def stamped(signature):
            signature.pop("stamps")
            signature.update(proof={"kind": "signature", "at": signed_at.isoformat()}, stamp_problem="", stamp_unverifiable=False)

        pdf = self.with_document_store(SignatureFieldNames().signed(), [self.revocation_list(signed_at + timedelta(hours=1))])
        with (mock.patch.object(app, "judge_time_stamps", stamped),
              mock.patch.object(app.certstore, "trust_verdicts", return_value=[{"trusted": True, "unverifiable": False, "source": "eu"}]),
              mock.patch.object(trustlist, "issuing_authority", return_value=SignatureFieldNames.CERT)):
            status = app.pdf_signatures(pdf)[0]["status_in_file"]
        self.assertEqual((status["status"], status["from_file"]["kind"]), ("good", "crl"))
