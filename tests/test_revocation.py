"""Certificate revocation lists: which are believed, and what they say about a certificate. Runs without a network."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import ocsp

import revocation
import trustlist

NOW = datetime.now(timezone.utc)
URL = "http://crl.test/ca.crl"
SECOND_URL = "http://crl2.test/ca.crl"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def name(text):
    return x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CZ"), x509.NameAttribute(x509.NameOID.COMMON_NAME, text)])


AUTHORITY = (x509.CertificateBuilder().subject_name(name("CA")).issuer_name(name("CA")).public_key(KEY.public_key())
             .serial_number(1).not_valid_before(NOW - timedelta(days=900)).not_valid_after(NOW + timedelta(days=900))
             .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True).sign(KEY, hashes.SHA256()))


def certificate(serial=77, urls=(URL, SECOND_URL), end=None):
    builder = (x509.CertificateBuilder().subject_name(name("Signer")).issuer_name(AUTHORITY.subject).public_key(OTHER_KEY.public_key())
               .serial_number(serial).not_valid_before(NOW - timedelta(days=100)).not_valid_after(end or NOW + timedelta(days=100)))
    if urls:
        points = [x509.DistributionPoint([x509.UniformResourceIdentifier(u)], None, None, None) for u in urls]
        builder = builder.add_extension(x509.CRLDistributionPoints(points), critical=False)
    return builder.sign(KEY, hashes.SHA256())


def der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def crl(revoked=(), key=KEY, issuer=None, last=None, following=None, extensions=()):
    builder = (x509.CertificateRevocationListBuilder().issuer_name(issuer or AUTHORITY.subject)
               .last_update(last or NOW - timedelta(hours=1)).next_update(following or NOW + timedelta(days=1)))
    for serial, when, reason in revoked:
        entry = x509.RevokedCertificateBuilder().serial_number(serial).revocation_date(when)
        if reason:
            entry = entry.add_extension(x509.CRLReason(reason), critical=False)
        builder = builder.add_revoked_certificate(entry.build())
    for extension in extensions:
        builder = builder.add_extension(extension, critical=True)
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


class Revocation(unittest.TestCase):
    def setUp(self):
        revocation._lists_in_memory.clear()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.pages = {}
        self.authority = AUTHORITY
        for patcher in (mock.patch.object(trustlist, "download_file", self.download),
                        mock.patch.object(trustlist, "issuing_authority", lambda *_: self.authority)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def download(self, url):
        if url not in self.pages:
            raise OSError("no such page")
        return self.pages[url]

    def check(self, cert=None):
        revocation._lists_in_memory.clear()
        return revocation.check_certificate_revocation(der(cert or certificate()), self.folder / "lists", self.folder / "crl")

    def test_certificate_not_on_the_list_is_good(self):
        self.pages[URL] = crl()
        self.assertEqual(self.check()["status"], "good")

    def test_revoked_certificate_reports_when(self):
        when = (NOW - timedelta(days=3)).replace(microsecond=0)
        self.pages[URL] = crl([(77, when, None)])
        verdict = self.check()
        self.assertEqual(verdict["status"], "revoked")
        self.assertEqual(datetime.fromisoformat(verdict["revoked_at"]), when)
        self.assertFalse(verdict["suspended"])

    def test_suspended_certificate_is_marked(self):
        self.pages[URL] = crl([(77, NOW - timedelta(days=3), x509.ReasonFlags.certificate_hold)])
        verdict = self.check()
        self.assertEqual(verdict["status"], "revoked")
        self.assertTrue(verdict["suspended"])

    def test_certificate_released_from_suspension_is_good(self):
        self.pages[URL] = crl([(77, NOW - timedelta(days=3), x509.ReasonFlags.remove_from_crl)])
        self.assertEqual(self.check()["status"], "good")

    def test_second_address_is_used_when_the_first_fails(self):
        self.pages[SECOND_URL] = crl([(77, NOW - timedelta(days=3), None)])
        self.assertEqual(self.check()["status"], "revoked")

    def assert_not_believed(self, data):
        self.pages[URL] = data
        verdict = self.check()
        self.assertEqual(verdict["status"], "unknown", verdict)
        self.assertEqual(list((self.folder / "crl").glob("*")) if (self.folder / "crl").exists() else [], [])

    def test_list_signed_by_another_key_is_not_believed(self):
        self.assert_not_believed(crl(key=OTHER_KEY))

    def test_list_of_another_issuer_is_not_believed(self):
        self.assert_not_believed(crl(issuer=name("Other")))

    def test_outdated_list_is_not_believed(self):
        self.assert_not_believed(crl(last=NOW - timedelta(days=9), following=NOW - timedelta(days=1)))

    def test_list_from_the_future_is_not_believed(self):
        self.assert_not_believed(crl(last=NOW + timedelta(days=1), following=NOW + timedelta(days=2)))

    def test_partial_list_is_not_believed(self):
        partial = x509.IssuingDistributionPoint(None, None, False, True, None, False, False)
        self.assert_not_believed(crl(extensions=[partial]))

    def test_delta_list_is_not_believed(self):
        self.assert_not_believed(crl(extensions=[x509.DeltaCRLIndicator(4)]))

    def test_list_with_an_impossible_version_is_not_believed(self):
        good = crl()
        self.assert_not_believed(good.replace(b"\x02\x01\x01", b"\x02\x01\x00", 1))

    def test_garbage_is_not_believed(self):
        self.assert_not_believed(b"not a list")

    def test_expired_certificate_absent_from_the_list_is_unknown(self):
        self.pages[URL] = crl()
        verdict = self.check(certificate(end=NOW - timedelta(days=1)))
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("vypršel", verdict["reason"])

    def test_expired_certificate_on_the_list_is_revoked(self):
        self.pages[URL] = crl([(77, NOW - timedelta(days=30), None)])
        self.assertEqual(self.check(certificate(end=NOW - timedelta(days=1)))["status"], "revoked")

    def test_issuer_outside_the_lists_downloads_nothing(self):
        self.authority = None
        with mock.patch.object(trustlist, "download_file", side_effect=AssertionError("must not download")):
            self.assertEqual(self.check()["status"], "unknown")

    def test_only_web_addresses_are_used(self):
        with mock.patch.object(trustlist, "download_file", side_effect=AssertionError("must not download")):
            self.assertEqual(self.check(certificate(urls=("file:///etc/passwd", "ldap://x/y")))["status"], "unknown")

    def test_certificate_without_addresses_is_unknown(self):
        self.assertEqual(self.check(certificate(urls=()))["status"], "unknown")

    def test_damaged_certificate_gives_a_verdict(self):
        self.assertEqual(revocation.check_certificate_revocation(b"\x30\x03abc", self.folder, self.folder)["status"], "unknown")

    def test_no_network_says_it_cannot_tell(self):
        verdict = self.check()
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("připojení", verdict["reason"])

    def test_old_certificate_without_a_list_does_not_blame_the_network(self):
        verdict = self.check(certificate(end=NOW - timedelta(days=1)))
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("vypršel", verdict["reason"])

    def test_cached_list_works_offline_until_its_next_update(self):
        self.pages[URL] = crl([(77, NOW - timedelta(days=3), None)])
        self.assertEqual(self.check()["status"], "revoked")
        self.pages.clear()
        self.assertEqual(self.check()["status"], "revoked")
        path = next((self.folder / "crl").glob("*.crl"))
        path.write_bytes(crl(last=NOW - timedelta(days=9), following=NOW - timedelta(days=1)))
        self.assertEqual(self.check()["status"], "unknown")

    def test_list_that_cannot_be_stored_is_still_used(self):
        self.pages[URL] = crl([(77, NOW - timedelta(days=3), None)])
        with mock.patch.object(trustlist, "store_atomically", side_effect=OSError("disk full")):
            self.assertEqual(self.check()["status"], "revoked")

    def test_dead_server_is_not_asked_again_for_the_next_signature(self):
        calls = []
        with mock.patch.object(trustlist, "download_file", side_effect=lambda url: calls.append(url) or (_ for _ in ()).throw(OSError())):
            revocation._lists_in_memory.clear()
            for _ in range(3):
                revocation.check_certificate_revocation(der(certificate()), self.folder, self.folder / "crl")
        self.assertEqual(calls, [URL, SECOND_URL])


SIGNED = NOW - timedelta(days=30)
RESPONDER_KEY = ec.generate_private_key(ec.SECP256R1())


def responder(key=KEY, usages=(x509.ExtendedKeyUsageOID.OCSP_SIGNING,), end=None):
    return (x509.CertificateBuilder().subject_name(name("Responder")).issuer_name(AUTHORITY.subject)
            .public_key(RESPONDER_KEY.public_key()).serial_number(5).not_valid_before(NOW - timedelta(days=100))
            .not_valid_after(end or NOW + timedelta(days=100))
            .add_extension(x509.ExtendedKeyUsage(list(usages)), critical=False).sign(key, hashes.SHA256()))


def answer(about=None, status=ocsp.OCSPCertStatus.GOOD, issued=None, following=None, revoked_at=None, reason=None,
           by=None, key=KEY):
    builder = ocsp.OCSPResponseBuilder().add_response(
        about or certificate(), AUTHORITY, hashes.SHA1(), status, issued or SIGNED + timedelta(minutes=5),
        following, revoked_at, reason).responder_id(ocsp.OCSPResponderEncoding.HASH, by or AUTHORITY)
    if by is not None:
        builder = builder.certificates([by])
    return builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.DER)


class StatusKeptInAFile(unittest.TestCase):
    """Lists and answers a PDF carries from the time it was signed. SIGNED is the time a stamp proves."""

    def setUp(self):
        self.authority = AUTHORITY
        patcher = mock.patch.object(trustlist, "issuing_authority", lambda *_: self.authority)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, lists=(), answers=(), cert=None, proven_at=SIGNED):
        kept = {"lists": list(lists), "answers": list(answers)}
        return revocation.check_revocation_kept_in_file(der(cert or certificate()), kept, proven_at, Path("unused"))

    def list_issued_after_the_signing(self, revoked=()):
        return crl(revoked, last=SIGNED + timedelta(hours=1), following=SIGNED + timedelta(days=1))

    def test_list_issued_after_the_signing_says_the_certificate_was_good(self):
        verdict = self.check([self.list_issued_after_the_signing()])
        self.assertEqual(verdict["status"], "good")
        self.assertEqual(verdict["from_file"]["kind"], "crl")
        self.assertFalse(verdict["from_file"]["before_signing"])
        self.assertEqual(datetime.fromisoformat(verdict["from_file"]["issued_at"]), (SIGNED + timedelta(hours=1)).replace(microsecond=0))

    def test_list_works_for_a_certificate_that_has_expired_since(self):
        expired = certificate(end=NOW - timedelta(days=1))
        self.assertEqual(self.check([self.list_issued_after_the_signing()], cert=expired)["status"], "good")

    def test_later_list_issued_after_the_certificate_expired_proves_nothing(self):
        expired = certificate(end=NOW - timedelta(days=1))
        self.assertEqual(self.check([crl()], cert=expired)["status"], "unknown")

    def list_in_force_at_the_signing(self, revoked=()):
        return crl(revoked, last=SIGNED - timedelta(hours=1), following=SIGNED + timedelta(days=1))

    def test_list_issued_before_the_signing_but_in_force_at_it_is_marked_as_earlier(self):
        verdict = self.check([self.list_in_force_at_the_signing()])
        self.assertEqual(verdict["status"], "good")
        self.assertTrue(verdict["from_file"]["before_signing"])

    def test_statement_issued_after_the_signing_is_preferred_to_an_earlier_one(self):
        verdict = self.check([self.list_in_force_at_the_signing(), self.list_issued_after_the_signing()], [answer(issued=SIGNED - timedelta(hours=1), following=SIGNED + timedelta(days=1))])
        self.assertFalse(verdict["from_file"]["before_signing"])

    def test_revocation_on_a_list_in_force_at_the_signing_is_reported(self):
        verdict = self.check([self.list_in_force_at_the_signing([(77, SIGNED - timedelta(days=2), None)])])
        self.assertEqual(verdict["status"], "revoked")

    def test_list_out_of_force_at_the_signing_proves_nothing(self):
        verdict = self.check([crl(last=SIGNED - timedelta(days=9), following=SIGNED - timedelta(days=1))])
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("už neplatil", verdict["reason"])

    def test_revocation_on_a_kept_list_is_reported(self):
        when = (SIGNED - timedelta(days=2)).replace(microsecond=0)
        verdict = self.check([self.list_issued_after_the_signing([(77, when, None)])])
        self.assertEqual(verdict["status"], "revoked")
        self.assertEqual(datetime.fromisoformat(verdict["revoked_at"]), when)

    def test_revocation_wins_over_a_good_statement(self):
        revoked = self.list_issued_after_the_signing([(77, SIGNED - timedelta(days=2), None)])
        self.assertEqual(self.check([self.list_issued_after_the_signing(), revoked], [answer()])["status"], "revoked")

    def test_forged_list_is_not_believed(self):
        forged = crl(key=OTHER_KEY, last=SIGNED + timedelta(hours=1), following=SIGNED + timedelta(days=1))
        verdict = self.check([forged, b"not a list"])
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("nepodepsal", verdict["reason"])

    def test_without_a_proven_time_nothing_is_believed(self):
        verdict = self.check([self.list_issued_after_the_signing()], proven_at=None)
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("razítko", verdict["reason"])

    def test_issuer_outside_the_trusted_lists_is_not_believed(self):
        self.authority = None
        self.assertEqual(self.check([self.list_issued_after_the_signing()])["status"], "unknown")

    def test_answer_given_after_the_signing_is_good(self):
        verdict = self.check(answers=[answer()])
        self.assertEqual(verdict["status"], "good")
        self.assertEqual(verdict["from_file"]["kind"], "ocsp")

    def test_answer_given_before_the_signing_but_in_force_at_it_is_marked_as_earlier(self):
        verdict = self.check(answers=[answer(issued=SIGNED - timedelta(hours=1), following=SIGNED + timedelta(days=1))])
        self.assertEqual(verdict["status"], "good")
        self.assertTrue(verdict["from_file"]["before_signing"])

    def test_answer_given_before_the_signing_without_an_end_proves_nothing(self):
        verdict = self.check(answers=[answer(issued=SIGNED - timedelta(hours=1))])
        self.assertEqual(verdict["status"], "unknown")
        self.assertIn("už neplatil", verdict["reason"])

    def test_answer_that_ended_before_the_signing_proves_nothing(self):
        ended = answer(issued=SIGNED - timedelta(days=3), following=SIGNED - timedelta(days=2))
        self.assertEqual(self.check(answers=[ended])["status"], "unknown")

    def test_answer_of_a_responder_named_by_the_authority_is_good(self):
        by = responder()
        self.assertEqual(self.check(answers=[answer(by=by, key=RESPONDER_KEY)])["status"], "good")

    def test_responder_must_be_named_by_the_authority_for_answering(self):
        for by in (responder(key=OTHER_KEY), responder(usages=[x509.ExtendedKeyUsageOID.CLIENT_AUTH]),
                   responder(end=SIGNED - timedelta(days=5))):
            verdict = self.check(answers=[answer(by=by, key=RESPONDER_KEY)])
            self.assertEqual(verdict["status"], "unknown", by)

    def test_forged_answer_is_not_believed(self):
        genuine = answer()
        forged = genuine[:-1] + bytes([genuine[-1] ^ 1])
        self.assertEqual(self.check(answers=[forged, b"not an answer"])["status"], "unknown")

    def test_answer_about_another_certificate_is_not_used(self):
        other = answer(about=certificate(serial=78))
        self.assertEqual(self.check(answers=[other])["status"], "unknown")

    def test_revocation_in_an_answer_is_reported(self):
        when = (SIGNED - timedelta(days=2)).replace(microsecond=0)
        revoked = answer(status=ocsp.OCSPCertStatus.REVOKED, revoked_at=when,
                         reason=x509.ReasonFlags.certificate_hold)
        verdict = self.check(answers=[revoked])
        self.assertEqual(verdict["status"], "revoked")
        self.assertTrue(verdict["suspended"])
        self.assertEqual(datetime.fromisoformat(verdict["revoked_at"]), when)

    def test_answer_given_after_the_certificate_expired_proves_nothing(self):
        expired = certificate(end=NOW - timedelta(days=1))
        self.assertEqual(self.check(answers=[answer(about=expired, issued=NOW)], cert=expired)["status"], "unknown")
