"""EU trusted lists: which lists are authentic, and which certificates they vouch for. Runs without a network.

    python -I tests/test_trustlist.py
"""
import base64
import hashlib
import os
import re
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"

import certstore  # noqa: E402
import trustlist  # noqa: E402

NS = "http://uri.etsi.org/02231/v2#"
DSNS = "http://www.w3.org/2000/09/xmldsig#"
NOW = datetime.now(timezone.utc)
CZ_URL = "https://list.test/cz.xml"
RSA_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def certificate(name, key, issuer=None, issuer_key=None, start=None, end=None, country="CZ", ca=True):
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, country), x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer.subject if issuer else subject)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(start or NOW - timedelta(days=3000)).not_valid_after(end or NOW + timedelta(days=3000))
               .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True))
    return builder.sign(issuer_key or key, hashes.SHA256())


def der(cert):
    return cert.public_bytes(serialization.Encoding.DER)


def b64(cert):
    return base64.b64encode(der(cert)).decode()


def fingerprint(cert):
    return hashlib.sha256(der(cert)).hexdigest()


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def sign(xml, cert, key=RSA_KEY):
    """Signs a list the way the real ones are: an enveloped signature over the whole document, exclusive c14n."""
    root = etree.fromstring(xml)
    digest = base64.b64encode(hashlib.sha256(etree.tostring(root, method="c14n", exclusive=True)).digest()).decode()
    info = (f'<ds:SignedInfo xmlns:ds="{DSNS}"><ds:CanonicalizationMethod Algorithm="{trustlist.EXC}"/>'
            f'<ds:SignatureMethod Algorithm="{trustlist.MORE}rsa-sha256"/><ds:Reference URI=""><ds:Transforms>'
            f'<ds:Transform Algorithm="{trustlist.ENVELOPED}"/><ds:Transform Algorithm="{trustlist.EXC}"/></ds:Transforms>'
            f'<ds:DigestMethod Algorithm="http://www.w3.org/2001/04/xmlenc#sha256"/><ds:DigestValue>{digest}</ds:DigestValue>'
            f'</ds:Reference></ds:SignedInfo>')
    canonical = etree.tostring(etree.fromstring(info), method="c14n", exclusive=True)
    value = base64.b64encode(key.sign(canonical, padding.PKCS1v15(), hashes.SHA256())).decode()
    signature = etree.fromstring(f'<ds:Signature xmlns:ds="{DSNS}">{info}<ds:SignatureValue>{value}</ds:SignatureValue>'
                                 f'<ds:KeyInfo><ds:X509Data><ds:X509Certificate>{b64(cert)}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></ds:Signature>')
    root.append(signature)
    return etree.tostring(root)


def service(cert, status="granted", start=None, history=(), uses=("ForeSignatures",), kind=trustlist.CA_QC):
    prefix = "http://uri.etsi.org/TrstSvc/TrustedList/"
    extensions = "".join(f"<Extension><AdditionalServiceInformation><URI>{prefix}SvcInfoExt/{use}</URI></AdditionalServiceInformation></Extension>" for use in uses)
    past = "".join(f"<ServiceHistoryInstance><ServiceStatus>{prefix}Svcstatus/{s}</ServiceStatus><StatusStartingTime>{stamp(t)}</StatusStartingTime></ServiceHistoryInstance>"
                   for s, t in history)
    return (f"<TSPService><ServiceInformation><ServiceTypeIdentifier>{kind}</ServiceTypeIdentifier>"
            f"<ServiceDigitalIdentity><DigitalId><X509Certificate>{b64(cert)}</X509Certificate></DigitalId></ServiceDigitalIdentity>"
            f"<ServiceStatus>{prefix}Svcstatus/{status}</ServiceStatus><StatusStartingTime>{stamp(start or NOW - timedelta(days=3500))}</StatusStartingTime>"
            f"<ServiceInformationExtensions>{extensions}</ServiceInformationExtensions></ServiceInformation>"
            f"<ServiceHistory>{past}</ServiceHistory></TSPService>")


def national(services, signer, territory="CZ", sequence=5, next_update=None):
    xml = (f'<TrustServiceStatusList xmlns="{NS}"><SchemeInformation><TSLSequenceNumber>{sequence}</TSLSequenceNumber>'
           f"<SchemeTerritory>{territory}</SchemeTerritory><NextUpdate><dateTime>{stamp(next_update or NOW + timedelta(days=100))}</dateTime></NextUpdate></SchemeInformation>"
           f"<TrustServiceProviderList><TrustServiceProvider><TSPServices>{''.join(services)}</TSPServices></TrustServiceProvider></TrustServiceProviderList>"
           f"</TrustServiceStatusList>")
    return sign(xml, signer)


def pointer(territory, url, signers, mime="application/vnd.etsi.tsl+xml"):
    identities = "".join(f"<ServiceDigitalIdentity><DigitalId><X509Certificate>{b64(c)}</X509Certificate></DigitalId></ServiceDigitalIdentity>" for c in signers)
    return (f"<OtherTSLPointer><ServiceDigitalIdentities>{identities}</ServiceDigitalIdentities><TSLLocation>{url}</TSLLocation>"
            f"<AdditionalInformation><OtherInformation><SchemeTerritory>{territory}</SchemeTerritory></OtherInformation>"
            f"<OtherInformation><MimeType>{mime}</MimeType></OtherInformation></AdditionalInformation></OtherTSLPointer>")


def lotl(entries, signer, sequence=5):
    xml = (f'<TrustServiceStatusList xmlns="{NS}"><SchemeInformation><TSLSequenceNumber>{sequence}</TSLSequenceNumber><SchemeTerritory>EU</SchemeTerritory>'
           f"<PointersToOtherTSL>{''.join(entries)}</PointersToOtherTSL>"
           f"<NextUpdate><dateTime>{stamp(NOW + timedelta(days=100))}</dateTime></NextUpdate></SchemeInformation></TrustServiceStatusList>")
    return sign(xml, signer)


LOTL_SIGNER = certificate("Test LOTL signer", RSA_KEY)
CZ_SIGNER = certificate("Test CZ list signer", RSA_KEY)
AUTHORITY = certificate("Test Qualified CA", RSA_KEY)
ISSUED = NOW - timedelta(days=100)
LEAF = certificate("Podepisujici", EC_KEY, AUTHORITY, RSA_KEY, start=ISSUED, ca=False)


class TrustLists(unittest.TestCase):
    def setUp(self):
        trustlist._memory.clear()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.pages = {trustlist.LOTL_URL: self.default_lotl(), CZ_URL: national([service(AUTHORITY)], CZ_SIGNER)}
        for patcher in (mock.patch.object(trustlist, "PINNED_SIGNERS", frozenset({fingerprint(LOTL_SIGNER)})),
                        mock.patch.object(trustlist, "download", self.download)):
            patcher.start()
            self.addCleanup(patcher.stop)

    @staticmethod
    def default_lotl(sequence=5, signer=LOTL_SIGNER, eu_signers=(LOTL_SIGNER,)):
        return lotl([pointer("EU", trustlist.LOTL_URL, eu_signers), pointer("CZ", "https://list.test/cz.pdf", [CZ_SIGNER], "application/pdf"),
                     pointer("CZ", CZ_URL, [CZ_SIGNER])], signer, sequence)

    def download(self, url):
        if url not in self.pages:
            raise OSError("no such page")
        return self.pages[url]

    def check(self, cert=LEAF):
        trustlist._memory.clear()
        return trustlist.check(der(cert), self.folder)

    def test_certificate_of_a_listed_authority_is_trusted(self):
        self.assertEqual(self.check(), {"trusted": True, "unverifiable": False, "reason": ""})

    def test_other_authority_is_not_listed(self):
        other = certificate("Other CA", RSA_KEY)
        verdict = self.check(certificate("X", EC_KEY, other, RSA_KEY, start=ISSUED, ca=False))
        self.assertFalse(verdict["trusted"])
        self.assertFalse(verdict["unverifiable"])

    def test_same_authority_name_with_another_key_is_not_trusted(self):
        forged = certificate("Test Qualified CA", ec.generate_private_key(ec.SECP256R1()))
        self.assertFalse(self.check(certificate("X", EC_KEY, forged, EC_KEY, start=ISSUED, ca=False))["trusted"])

    def test_authority_must_be_supervised_at_issue(self):
        restored = NOW - timedelta(days=10)
        self.pages[CZ_URL] = national([service(AUTHORITY, "granted", restored, [("withdrawn", NOW - timedelta(days=3000))])], CZ_SIGNER)
        self.assertFalse(self.check()["trusted"])
        recent = certificate("Recent", EC_KEY, AUTHORITY, RSA_KEY, start=NOW - timedelta(days=2), ca=False)
        self.assertTrue(self.check(recent)["trusted"])

    def test_withdrawn_authority_cannot_be_confirmed(self):
        withdrawn = NOW - timedelta(days=10)
        self.pages[CZ_URL] = national([service(AUTHORITY, "withdrawn", withdrawn, [("granted", NOW - timedelta(days=3000))])], CZ_SIGNER)
        verdict = self.check()
        self.assertFalse(verdict["trusted"])
        self.assertTrue(verdict["unverifiable"])
        self.assertIn("pod dohledem", verdict["reason"])

    def stamp_verdict(self, cert, moment, services):
        trustlist._memory.clear()
        self.pages[CZ_URL] = national(services, CZ_SIGNER)
        return trustlist.check_stamp(der(cert), moment, self.folder)

    def test_listed_time_stamping_unit_is_qualified(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        verdict = self.stamp_verdict(unit, NOW - timedelta(days=1), [service(unit, kind=trustlist.TSA_QTST)])
        self.assertEqual(verdict, {"qualified": True, "unverifiable": False, "reason": ""})

    def test_unit_issued_by_a_listed_stamping_authority_is_qualified(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        self.assertTrue(self.stamp_verdict(unit, NOW - timedelta(days=1), [service(AUTHORITY, kind=trustlist.TSA_QTST)])["qualified"])

    def test_same_name_with_another_key_is_not_qualified(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, EC_KEY, ca=False)
        self.assertFalse(self.stamp_verdict(unit, NOW - timedelta(days=1), [service(AUTHORITY, kind=trustlist.TSA_QTST)])["qualified"])

    def test_authority_for_signatures_does_not_vouch_for_stamps(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        self.assertFalse(self.stamp_verdict(unit, NOW - timedelta(days=1), [service(AUTHORITY)])["qualified"])

    def test_stamp_from_a_unit_retired_later_stays_good(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        retired = service(unit, "withdrawn", NOW - timedelta(days=10), [("granted", NOW - timedelta(days=3000))], kind=trustlist.TSA_QTST)
        self.assertTrue(self.stamp_verdict(unit, NOW - timedelta(days=20), [retired])["qualified"])

    def test_stamp_after_the_unit_was_retired_is_refused(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        retired = service(unit, "withdrawn", NOW - timedelta(days=10), [("granted", NOW - timedelta(days=3000))], kind=trustlist.TSA_QTST)
        self.assertFalse(self.stamp_verdict(unit, NOW - timedelta(days=1), [retired])["qualified"])

    def test_stamp_outside_the_certificate_validity_is_refused(self):
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, start=NOW - timedelta(days=5), ca=False)
        self.assertFalse(self.stamp_verdict(unit, NOW - timedelta(days=20), [service(unit, kind=trustlist.TSA_QTST)])["qualified"])

    def test_stamp_cannot_be_checked_without_lists(self):
        self.pages.clear()
        trustlist._memory.clear()
        unit = certificate("TSU", EC_KEY, AUTHORITY, RSA_KEY, ca=False)
        verdict = trustlist.check_stamp(der(unit), NOW - timedelta(days=1), self.folder)
        self.assertFalse(verdict["qualified"])
        self.assertTrue(verdict["unverifiable"])

    def test_damaged_stamp_certificate_gives_a_verdict(self):
        verdict = trustlist.check_stamp(b"garbage", NOW, self.folder)
        self.assertFalse(verdict["qualified"])

    def test_damaged_certificate_gives_a_verdict(self):
        builder = (x509.CertificateBuilder().subject_name(LEAF.subject).issuer_name(AUTHORITY.subject).public_key(EC_KEY.public_key())
                   .serial_number(5).not_valid_before(ISSUED).not_valid_after(NOW + timedelta(days=9))
                   .add_extension(x509.UnrecognizedExtension(x509.oid.ExtensionOID.KEY_USAGE, b"junk"), critical=False))
        for damaged in (der(builder.sign(RSA_KEY, hashes.SHA256())), b"\x30\x03abc"):
            self.assertFalse(trustlist.check(damaged, self.folder)["trusted"])

    def test_certificate_with_an_impossible_version_gives_a_verdict(self):
        damaged = der(LEAF).replace(b"\xa0\x03\x02\x01\x02", b"\xa0\x03\x02\x01\x06", 1)
        self.assertFalse(trustlist.check(damaged, self.folder)["trusted"])
        self.assertIsNone(trustlist.issuer(damaged, self.folder))

    def test_unreadable_authority_does_not_hide_the_others(self):
        odd = service(AUTHORITY).replace(b64(AUTHORITY), base64.b64encode(b"junk").decode())
        self.pages[CZ_URL] = national([odd, service(AUTHORITY)], CZ_SIGNER)
        self.assertTrue(self.check()["trusted"])

    def test_incomplete_list_entry_gives_a_verdict(self):
        incomplete = re.sub(r"<StatusStartingTime>[^<]*</StatusStartingTime>", "", service(AUTHORITY))
        self.pages[CZ_URL] = national([incomplete], CZ_SIGNER)
        verdict = self.check()
        self.assertFalse(verdict["trusted"])
        self.assertTrue(verdict["unverifiable"])
        self.assertIn("neočekávaný", verdict["reason"])

    def test_comment_cannot_shorten_a_signed_value(self):
        signed = national([service(AUTHORITY, "grantedNot")], CZ_SIGNER)
        self.pages[CZ_URL] = signed.replace(b"Svcstatus/grantedNot", b"Svcstatus/granted<!-- -->Not")
        self.assertFalse(self.check()["trusted"])

    def test_cached_lotl_cannot_vouch_for_itself(self):
        self.assertTrue(self.check()["trusted"])
        stranger = certificate("Stranger", RSA_KEY)
        (self.folder / "EU.xml").write_bytes(self.default_lotl(sequence=9, signer=stranger, eu_signers=(stranger,)))
        trustlist._memory.clear()
        self.assertNotIn(fingerprint(stranger), trustlist._lotl_signers(self.folder))
        self.assertTrue(self.check()["trusted"])  # the bad file is ignored and the list downloaded again
        self.assertEqual(trustlist._sequence(trustlist._parse((self.folder / "EU.xml").read_bytes())), 5)

    def test_authority_for_websites_only_is_not_trusted(self):
        self.pages[CZ_URL] = national([service(AUTHORITY, uses=("ForWebSiteAuthentication",))], CZ_SIGNER)
        self.assertFalse(self.check()["trusted"])

    def test_timestamp_service_is_not_trusted(self):
        self.pages[CZ_URL] = national([service(AUTHORITY, kind="http://uri.etsi.org/TrstSvc/Svctype/TSA/QTST")], CZ_SIGNER)
        self.assertFalse(self.check()["trusted"])

    def assert_list_rejected(self, cert=LEAF):
        verdict = self.check(cert)
        self.assertFalse(verdict["trusted"])
        self.assertTrue(verdict["unverifiable"], verdict)

    def test_changed_list_is_rejected(self):
        signed = national([service(certificate("Other CA", RSA_KEY))], CZ_SIGNER)
        root = etree.fromstring(signed)
        services = root.find(f"{{{NS}}}TrustServiceProviderList/{{{NS}}}TrustServiceProvider/{{{NS}}}TSPServices")
        services.append(etree.fromstring(service(AUTHORITY).replace("<TSPService>", f'<TSPService xmlns="{NS}">')))
        self.pages[CZ_URL] = etree.tostring(root)
        self.assert_list_rejected()

    def test_services_hidden_in_the_signature_are_ignored(self):
        signed = national([], CZ_SIGNER)
        root = etree.fromstring(signed)
        hidden = etree.fromstring(f'<ds:Object xmlns:ds="{DSNS}" xmlns="{NS}"><TrustServiceProviderList><TrustServiceProvider><TSPServices>'
                                  f"{service(AUTHORITY)}</TSPServices></TrustServiceProvider></TrustServiceProviderList></ds:Object>")
        root.find(f"{{{DSNS}}}Signature").append(hidden)
        self.pages[CZ_URL] = etree.tostring(root)
        self.assertFalse(self.check()["trusted"])

    def test_list_signed_by_someone_else_is_rejected(self):
        self.pages[CZ_URL] = national([service(AUTHORITY)], certificate("Stranger", RSA_KEY))
        self.assert_list_rejected()

    def test_lotl_not_signed_by_a_pinned_certificate_is_rejected(self):
        self.pages[trustlist.LOTL_URL] = self.default_lotl(signer=certificate("Stranger", RSA_KEY))
        self.assert_list_rejected()

    def test_unsigned_list_is_rejected(self):
        root = etree.fromstring(national([service(AUTHORITY)], CZ_SIGNER))
        root.remove(root.find(f"{{{DSNS}}}Signature"))
        self.pages[CZ_URL] = etree.tostring(root)
        self.assert_list_rejected()

    def test_doctype_is_rejected(self):
        self.pages[CZ_URL] = b'<!DOCTYPE x [<!ENTITY a "b">]>' + national([service(AUTHORITY)], CZ_SIGNER)
        self.assert_list_rejected()

    def test_list_past_its_next_update_is_rejected(self):
        self.pages[CZ_URL] = national([service(AUTHORITY)], CZ_SIGNER, next_update=NOW - timedelta(days=1))
        self.assert_list_rejected()

    def test_damaged_cached_list_is_downloaded_again(self):
        self.assertTrue(self.check()["trusted"])
        (self.folder / "CZ.xml").write_bytes(b"<broken")
        self.assertTrue(self.check()["trusted"])
        self.assertIn(b"TrustServiceStatusList", (self.folder / "CZ.xml").read_bytes())

    def test_list_of_another_country_is_rejected(self):
        self.pages[CZ_URL] = national([service(AUTHORITY)], CZ_SIGNER, territory="SK")
        self.assert_list_rejected()

    def test_no_network_and_no_cache_says_it_cannot_tell(self):
        self.pages.clear()
        verdict = self.check()
        self.assertTrue(verdict["unverifiable"])
        self.assertIn("nepodařilo", verdict["reason"])

    def test_cached_lists_work_offline(self):
        self.assertTrue(self.check()["trusted"])
        self.pages.clear()
        self.assertTrue(self.check()["trusted"])

    def test_old_cached_list_is_refreshed_behind_it_and_never_downgraded(self):
        self.assertTrue(self.check()["trusted"])
        cached = self.folder / "CZ.xml"

        def age():
            os.utime(cached, (time.time() - 2 * trustlist.REFRESH_AFTER,) * 2)

        def settle():
            for thread in threading.enumerate():
                if thread is not threading.current_thread() and thread.daemon:
                    thread.join(10)
        newer = national([service(AUTHORITY)], CZ_SIGNER, sequence=6)
        self.pages[CZ_URL] = newer
        age()
        self.assertTrue(self.check()["trusted"])
        settle()
        self.assertEqual(cached.read_bytes(), newer)
        self.pages[CZ_URL] = national([], CZ_SIGNER, sequence=4)
        age()
        self.check()
        settle()
        self.assertEqual(cached.read_bytes(), newer)

    def test_signer_announced_by_the_cached_lotl_takes_over(self):
        successor = certificate("Next LOTL signer", RSA_KEY)
        self.pages[trustlist.LOTL_URL] = self.default_lotl(eu_signers=(LOTL_SIGNER, successor))
        self.assertTrue(self.check()["trusted"])
        self.pages[trustlist.LOTL_URL] = self.default_lotl(sequence=6, signer=successor, eu_signers=(successor,))
        os.utime(self.folder / "EU.xml", (time.time() - 2 * trustlist.REFRESH_AFTER,) * 2)
        self.assertTrue(self.check()["trusted"])
        for thread in threading.enumerate():
            if thread is not threading.current_thread() and thread.daemon:
                thread.join(10)
        self.assertIn(b"<SchemeTerritory>EU", (self.folder / "EU.xml").read_bytes())
        self.assertEqual(trustlist._sequence(trustlist._parse((self.folder / "EU.xml").read_bytes())), 6)

    def test_country_cannot_escape_the_folder(self):
        authority = certificate("Odd CA", RSA_KEY, country="..")
        verdict = self.check(certificate("X", EC_KEY, authority, RSA_KEY, start=ISSUED, ca=False))
        self.assertFalse(verdict["trusted"])
        self.assertFalse((self.folder.parent / ".xml").exists())

    def test_country_without_a_list_needs_no_download(self):
        self.pages = {trustlist.LOTL_URL: self.default_lotl()}
        authority = certificate("British CA", RSA_KEY, country="GB")
        verdict = self.check(certificate("X", EC_KEY, authority, RSA_KEY, start=ISSUED, ca=False))
        self.assertFalse(verdict["unverifiable"])


class Verdicts(unittest.TestCase):
    def verdicts(self, system, listed):
        with mock.patch.object(certstore, "_system_trust", return_value=[system]), \
                mock.patch.object(certstore.trustlist, "check", return_value=listed) as check:
            return certstore.trust([b"certificate"])[0], check

    def test_system_trust_needs_no_list(self):
        verdict, check = self.verdicts({"trusted": True, "reason": ""}, None)
        self.assertEqual(verdict["source"], "system")
        check.assert_not_called()

    def test_list_vouches_for_what_the_system_does_not_trust(self):
        verdict, _ = self.verdicts({"trusted": False, "reason": "no"}, {"trusted": True, "unverifiable": False, "reason": ""})
        self.assertEqual((verdict["trusted"], verdict["source"]), (True, "eu"))

    def test_unavailable_list_is_passed_on(self):
        verdict, _ = self.verdicts({"trusted": False, "reason": "no"}, {"trusted": False, "unverifiable": True, "reason": "nelze"})
        self.assertEqual((verdict["trusted"], verdict["unverifiable"], verdict["source"]), (False, True, "system"))

    def test_pinned_certificates_are_those_of_the_official_journal(self):
        self.assertEqual(len(trustlist.PINNED_SIGNERS), 6)
        self.assertTrue(all(len(p) == 64 for p in trustlist.PINNED_SIGNERS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
