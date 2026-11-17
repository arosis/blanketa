"""A time stamp requested from a service while a PDF is being signed."""
import asyncio
import base64
import http.client
import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from asn1crypto import keys as asn1_keys
from asn1crypto import tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore
from support import ROOT

import app
import pdf_sign
import time_stamps

BLANK = (ROOT / "samples-pdf/zadost.pdf").read_bytes()
STAMPED_AT = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
REJECTION_WITHOUT_A_TOKEN = bytes.fromhex("30053003020102")


def issue(name, stamping=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(x509.NameOID.COUNTRY_NAME, "CZ"), x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
               .not_valid_after(now + timedelta(days=365)))
    if stamping:
        builder = builder.add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return key, builder.sign(key, hashes.SHA256())


KEY, CERT = issue("Testovaci Podepisujici")
SERVICE_KEY, SERVICE_CERT = issue("Blanketa TEST TSA", stamping=True)
ISSUES_TOKENS = DummyTimeStamper(
    tsa_cert=asn1_x509.Certificate.load(SERVICE_CERT.public_bytes(serialization.Encoding.DER)),
    tsa_key=asn1_keys.PrivateKeyInfo.load(SERVICE_KEY.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())),
    certs_to_embed=SimpleCertificateStore(), fixed_dt=STAMPED_AT)


class Service(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.server.asked.append((dict(self.headers), body))
        status, reply = self.server.answer(body)
        self.send_response(status)
        self.send_header("Content-Length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, *_):
        pass


def token_for(body):
    return 200, asyncio.run(ISSUES_TOKENS.async_request_tsa_response(tsp.TimeStampReq.load(body))).dump()


class ServiceRunning(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
        self.server.asked = []
        self.server.answer = token_for
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/tsa"
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(time_stamps.certstore, "config_dir", return_value=Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def signed_with_stamp(self, pdf=BLANK, **service):
        return pdf_sign.sign_pdf(pdf, KEY, CERT, time_stamper=time_stamps.ServiceTimeStamper(self.url, **service))

    def refusal(self, **service):
        with self.assertRaises(time_stamps.TimeStampUnavailable) as raised:
            self.signed_with_stamp(**service)
        return str(raised.exception)


class StampWhileSigning(ServiceRunning):
    def test_signature_carries_the_stamp_of_the_service(self):
        signature = pdf_sign.describe_signatures(self.signed_with_stamp())[0]
        self.assertTrue(signature["intact"])
        self.assertEqual([(stamp["kind"], stamp["intact"], stamp["at"], stamp["issuer"]) for stamp in signature["stamps"]],
                         [("signature", True, STAMPED_AT.isoformat(), "Blanketa TEST TSA")])

    def test_service_is_asked_once_for_one_signature(self):
        self.signed_with_stamp()
        self.assertEqual(len(self.server.asked), 1)

    def test_service_gets_a_digest_and_nothing_of_the_document(self):
        self.signed_with_stamp()
        _headers, body = self.server.asked[0]
        request = tsp.TimeStampReq.load(body)
        self.assertEqual(len(request["message_imprint"]["hashed_message"].native), 32)
        self.assertLess(len(body), 200)

    def test_earlier_signature_stays_intact_after_a_stamped_one(self):
        first, second = pdf_sign.describe_signatures(self.signed_with_stamp(pdf_sign.sign_pdf(BLANK, KEY, CERT)))
        self.assertTrue(first["intact"])
        self.assertTrue(second["intact"])
        self.assertEqual(len(second["stamps"]), 1)

    def test_signing_without_a_service_asks_nobody(self):
        self.assertEqual(pdf_sign.describe_signatures(pdf_sign.sign_pdf(BLANK, KEY, CERT))[0]["stamps"], [])
        self.assertEqual(self.server.asked, [])

    def test_name_and_password_go_to_the_service_as_basic_authentication(self):
        self.signed_with_stamp(user="uzivatel", password="tajne")
        headers, _body = self.server.asked[0]
        self.assertEqual(headers["Authorization"], "Basic " + base64.b64encode(b"uzivatel:tajne").decode())

    def test_service_without_a_name_gets_no_authentication(self):
        self.signed_with_stamp()
        self.assertNotIn("Authorization", self.server.asked[0][0])


class ServiceThatFails(ServiceRunning):
    def test_refused_login_says_to_check_name_and_password(self):
        self.server.answer = lambda _body: (401, b"")
        self.assertIn("jméno a heslo", self.refusal(user="uzivatel", password="spatne"))

    def test_error_of_the_service_names_its_code(self):
        self.server.answer = lambda _body: (503, b"")
        self.assertIn("503", self.refusal())

    def test_reply_that_is_no_stamp_is_refused(self):
        self.server.answer = lambda _body: (200, b"<html>Prihlaste se</html>")
        self.assertIn("neposlala časové razítko", self.refusal())

    def test_request_the_service_rejects_is_refused(self):
        self.server.answer = lambda _body: (200, REJECTION_WITHOUT_A_TOKEN)
        self.assertIn("razítko nevydala", self.refusal())

    def test_endless_reply_is_cut_off(self):
        self.server.answer = lambda _body: (200, bytes(time_stamps.LONGEST_REPLY + 10))
        self.assertIn("příliš dlouhou", self.refusal())

    def test_stamp_larger_than_its_room_is_refused_instead_of_a_broken_file(self):
        with mock.patch.object(time_stamps, "ROOM_FOR_TOKEN", 100):
            self.assertIn("větší", self.refusal())

    def test_service_that_does_not_answer_is_reported(self):
        self.server.shutdown()
        self.server.server_close()
        self.assertIn("nepodařilo zastihnout", self.refusal())

    def test_redirect_is_not_followed(self):
        self.server.answer = lambda _body: (302, b"")
        self.assertIn("302", self.refusal())


class Settings(ServiceRunning):
    def test_nothing_stored_means_no_stamp_and_the_public_service(self):
        self.assertEqual(time_stamps.settings(), {"enabled": False, "service": "public", "url": "", "user": ""})

    def test_stored_settings_come_back(self):
        wanted = {"enabled": True, "service": "own", "url": "https://tsa.example.cz/tsa", "user": "uzivatel"}
        self.assertEqual(time_stamps.store_settings(wanted), wanted)
        self.assertEqual(time_stamps.settings(), wanted)

    def test_password_is_never_stored(self):
        time_stamps.store_settings({"enabled": True, "service": "own", "url": "https://tsa.example.cz/tsa",
                                    "user": "uzivatel", "password": "tajne"})
        self.assertNotIn("tajne", (time_stamps.certstore.config_dir() / time_stamps.SETTINGS_FILE).read_text(encoding="utf-8"))

    def test_damaged_settings_file_means_no_stamp(self):
        (time_stamps.certstore.config_dir() / time_stamps.SETTINGS_FILE).write_text('["enabled"]', encoding="utf-8")
        self.assertFalse(time_stamps.settings()["enabled"])

    def test_settings_of_a_wrong_kind_fall_back(self):
        (time_stamps.certstore.config_dir() / time_stamps.SETTINGS_FILE).write_text(
            '{"enabled": "yes", "service": 5, "url": null, "user": "uzivatel"}', encoding="utf-8")
        self.assertEqual(time_stamps.settings(), {"enabled": False, "service": "public", "url": "", "user": "uzivatel"})

    def test_own_service_needs_a_web_address(self):
        for address in ("", "tsa.example.cz", "file:///etc/passwd", "ftp://tsa.example.cz"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                time_stamps.store_settings({"enabled": True, "service": "own", "url": address})

    def test_password_never_travels_unencrypted(self):
        with self.assertRaises(ValueError):
            time_stamps.store_settings({"enabled": True, "service": "own", "url": "http://tsa.example.cz/tsa", "user": "uzivatel"})

    def test_credentials_inside_the_address_are_refused(self):
        with self.assertRaises(ValueError):
            time_stamps.store_settings({"enabled": True, "service": "own", "url": "https://uzivatel:tajne@tsa.example.cz/tsa"})

    def test_unfinished_own_service_can_be_stored_while_stamps_are_off(self):
        self.assertEqual(time_stamps.store_settings({"enabled": False, "service": "own", "url": ""})["service"], "own")

    def test_public_service_is_the_qualified_one(self):
        time_stamps.store_settings({"enabled": True, "service": "public"})
        self.assertEqual(time_stamps.service_time_stamper().url, "http://timestamp.sectigo.com/qualified")

    def test_own_service_gets_its_address_name_and_the_password_of_this_signing(self):
        time_stamps.store_settings({"enabled": True, "service": "own", "url": "https://tsa.example.cz/tsa", "user": "uzivatel"})
        stamper = time_stamps.service_time_stamper("tajne")
        self.assertEqual((stamper.url, stamper.user, stamper.password), ("https://tsa.example.cz/tsa", "uzivatel", "tajne"))


class PdfReadyToSign(ServiceRunning):
    def setUp(self):
        super().setUp()
        self.session = {"name": "zadost", "pdf": BLANK}
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.target = Path(self.folder.name) / "zadost-podepsany.pdf"
        for patcher in (mock.patch.object(app.certstore, "load_identity", return_value=(KEY, CERT)),
                        mock.patch.object(app, "output_path", return_value=self.target),
                        mock.patch.object(app.time_stamps, "service_time_stamper",
                                          side_effect=lambda password=None: time_stamps.ServiceTimeStamper(self.url))):
            patcher.start()
            self.addCleanup(patcher.stop)


class SavingASignedPdf(PdfReadyToSign):
    def test_stamp_is_added_when_the_page_asks_for_it(self):
        app.save_pdf(self.session, {}, "pfx:1", time_stamp={})
        self.assertEqual(len(self.server.asked), 1)
        self.assertEqual(len(pdf_sign.describe_signatures(self.target.read_bytes())[0]["stamps"]), 1)

    def test_no_stamp_unless_the_page_asks_for_it(self):
        app.save_pdf(self.session, {}, "pfx:1")
        self.assertEqual(self.server.asked, [])

    def test_nothing_is_saved_when_the_stamp_cannot_be_had(self):
        self.server.answer = lambda _body: (503, b"")
        with self.assertRaises(time_stamps.TimeStampUnavailable):
            app.save_pdf(self.session, {}, "pfx:1", time_stamp={})
        self.assertFalse(self.target.exists())
        self.assertEqual(self.session["pdf"], BLANK)


class OverTheLocalServer(PdfReadyToSign):
    def setUp(self):
        super().setUp()
        self.app_server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.app_server.serve_forever, daemon=True).start()
        self.addCleanup(self.app_server.server_close)
        self.addCleanup(self.app_server.shutdown)
        self.token = app.new_session(self.session)

    def post(self, path, body):
        port = self.app_server.server_address[1]
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        connection.request("POST", f"{path}{'&' if '?' in path else '?'}s={self.token}", body=json.dumps(body).encode(),
                           headers={"Host": f"127.0.0.1:{port}"})
        response = connection.getresponse()
        return response.status, response.read().decode()

    def test_page_learns_that_only_the_stamp_failed(self):
        self.server.answer = lambda _body: (503, b"")
        status, reason = self.post("/pdf-save?cert=pfx:1", {"values": {}, "time_stamp": {}})
        self.assertEqual(status, 424)
        self.assertIn("503", reason)
        self.assertFalse(self.target.exists())

    def test_signing_again_without_the_stamp_succeeds(self):
        self.server.answer = lambda _body: (503, b"")
        status, _saved = self.post("/pdf-save?cert=pfx:1", {"values": {}, "time_stamp": None})
        self.assertEqual(status, 200)
        self.assertEqual(pdf_sign.describe_signatures(self.target.read_bytes())[0]["stamps"], [])

    def test_page_stores_the_chosen_service(self):
        wanted = {"enabled": True, "service": "own", "url": "https://tsa.example.cz/tsa", "user": ""}
        status, stored = self.post("/time-stamp-settings", wanted)
        self.assertEqual((status, json.loads(stored)), (200, wanted))
        self.assertEqual(time_stamps.settings(), wanted)

    def test_unusable_service_is_refused_with_a_reason(self):
        status, reason = self.post("/time-stamp-settings", {"enabled": True, "service": "own", "url": "tsa.example.cz"})
        self.assertEqual(status, 400)
        self.assertIn("http://", reason)

    def test_settings_that_are_no_object_are_refused(self):
        self.assertEqual(self.post("/time-stamp-settings", ["enabled"])[0], 400)


if __name__ == "__main__":
    unittest.main()
