"""A throwaway time stamp service on this machine, for tests/test_time_stamps.py and tools/selftest.py."""
import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from asn1crypto import keys as asn1_keys
from asn1crypto import tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pyhanko.sign.timestamps import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

STAMPED_AT = datetime(2026, 3, 2, 9, 30, tzinfo=timezone.utc)
SERVICE_NAME = "Blanketa TEST TSA"


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
SERVICE_KEY, SERVICE_CERT = issue(SERVICE_NAME, stamping=True)
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
        if not self.server.seconds_between_bytes:
            self.wfile.write(reply)
            return
        try:
            for byte in reply:
                self.wfile.write(bytes([byte]))
                self.wfile.flush()
                time.sleep(self.server.seconds_between_bytes)
        except ConnectionError:
            return

    def log_message(self, *_):
        pass


def token_for(body):
    return 200, asyncio.run(ISSUES_TOKENS.async_request_tsa_response(tsp.TimeStampReq.load(body))).dump()


def running_service():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Service)
    server.asked = []
    server.answer = token_for
    server.seconds_between_bytes = 0
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/tsa"
