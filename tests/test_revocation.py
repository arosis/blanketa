"""Certificate revocation lists: which are believed, and what they say about a certificate. Runs without a network.

    python -I tests/test_revocation.py
"""
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"

import revocation  # noqa: E402
import trustlist  # noqa: E402

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
