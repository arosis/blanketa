"""Synthetic XAdES signatures and ASiC containers signed with the pki/ test identity (or throwaway keys)."""
import asyncio
import base64
import copy
import datetime
import hashlib
import io
import zipfile
from pathlib import Path
from urllib.parse import quote

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa, utils
from lxml import etree
from pyhanko.sign.timestamps import DummyTimeStamper

ROOT = Path(__file__).resolve().parent.parent
DS = "http://www.w3.org/2000/09/xmldsig#"
XADES = "http://uri.etsi.org/01903/v1.3.2#"
ASIC = "http://uri.etsi.org/02918/v1.2.1#"
MANIFEST_NS = "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"
SIGNED_PROPERTIES_TYPE = "http://uri.etsi.org/01903#SignedProperties"
MANIFEST_TYPE = DS + "Manifest"
SIGNING_TIME = "2026-01-15T10:00:00Z"

SHA1 = DS + "sha1"
SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
SHA384 = "http://www.w3.org/2001/04/xmldsig-more#sha384"
SHA512 = "http://www.w3.org/2001/04/xmlenc#sha512"
MD5 = "http://www.w3.org/2001/04/xmldsig-more#md5"
RSA_SHA1 = DS + "rsa-sha1"
RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ECDSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#ecdsa-sha256"
C14N10 = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
C14N11 = "http://www.w3.org/2006/12/xml-c14n11"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ENVELOPED = DS + "enveloped-signature"
BASE64 = DS + "base64"

DIGESTS = {SHA1: hashlib.sha1, SHA256: hashlib.sha256, SHA384: hashlib.sha384, SHA512: hashlib.sha512, MD5: hashlib.md5}
SIGNATURE_HASHES = {RSA_SHA1: hashes.SHA1, RSA_SHA256: hashes.SHA256, ECDSA_SHA256: hashes.SHA256}


_tsa = []


def _tsa_stamper():
    if not _tsa:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "Blanketa TEST TSA")])
        start = datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(start)
                .not_valid_after(start + datetime.timedelta(days=36500))
                .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                .add_extension(x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
                .sign(key, hashes.SHA256()))
        _tsa.append(DummyTimeStamper(
            tsa_cert=asn1_x509.Certificate.load(cert.public_bytes(serialization.Encoding.DER)),
            tsa_key=asn1_keys.PrivateKeyInfo.load(key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                                                                    serialization.NoEncryption())),
            fixed_dt=datetime.datetime(2026, 1, 15, 10, 5, tzinfo=datetime.timezone.utc)))
    return _tsa[0]


def _add_stamp(signature, stamp, c14n=C14N10):
    if stamp == "signed-info-c14n":
        c14n_used = signature.find(f"{_ds('SignedInfo')}/{_ds('CanonicalizationMethod')}").get("Algorithm")
        data = _canonical(signature.find(_ds("SignatureValue")), c14n_used)
    else:
        data = b"other bytes" if stamp == "wrong" else _canonical(signature.find(_ds("SignatureValue")), c14n)
    token = asyncio.run(_tsa_stamper().async_timestamp(hashlib.sha256(data).digest(), "sha256")).dump()
    qualifying = signature.find(f".//{_xades('QualifyingProperties')}")
    stamp_element = etree.SubElement(etree.SubElement(etree.SubElement(qualifying, _xades("UnsignedProperties")),
                                                      _xades("UnsignedSignatureProperties")), _xades("SignatureTimeStamp"))
    if c14n != C14N10:
        etree.SubElement(stamp_element, _ds("CanonicalizationMethod"), Algorithm=c14n)
    etree.SubElement(stamp_element, _xades("EncapsulatedTimeStamp")).text = base64.b64encode(token).decode()


def _ds(name):
    return f"{{{DS}}}{name}"


def _xades(name):
    return f"{{{XADES}}}{name}"


def test_identity():
    key = serialization.load_pem_private_key((ROOT / "pki/user.key").read_bytes(), password=None)
    cert = x509.load_pem_x509_certificate((ROOT / "pki/user.crt").read_bytes())
    return key, cert


def _canonical(element, algorithm, with_comments=False):
    exclusive = algorithm.startswith(EXC_C14N)
    return etree.tostring(element, method="c14n", exclusive=exclusive, with_comments=with_comments)


def _add_reference(parent, uri, digest, transforms=(), reference_type=None):
    reference = etree.SubElement(parent, _ds("Reference"))
    if uri is not None:
        reference.set("URI", uri)
    if reference_type:
        reference.set("Type", reference_type)
    if transforms:
        transforms_element = etree.SubElement(reference, _ds("Transforms"))
        for algorithm in transforms:
            etree.SubElement(transforms_element, _ds("Transform"), Algorithm=algorithm)
    etree.SubElement(reference, _ds("DigestMethod"), Algorithm=digest)
    etree.SubElement(reference, _ds("DigestValue"))
    return reference


def _cert_digest(parent, cert, algorithm):
    cert_digest = etree.SubElement(etree.SubElement(parent, _xades("Cert")), _xades("CertDigest"))
    etree.SubElement(cert_digest, _ds("DigestMethod"), Algorithm=algorithm)
    der = cert.public_bytes(serialization.Encoding.DER)
    etree.SubElement(cert_digest, _ds("DigestValue")).text = base64.b64encode(DIGESTS[algorithm](der).digest()).decode()
    return cert_digest.getparent()


def _skeleton(parent, signature_id, signature_method, c14n, cert, signing_certificate):
    signature = etree.Element(_ds("Signature"), nsmap={"ds": DS, "xades": XADES}, Id=signature_id)
    if parent is not None:
        parent.append(signature)
    signed_info = etree.SubElement(signature, _ds("SignedInfo"))
    etree.SubElement(signed_info, _ds("CanonicalizationMethod"), Algorithm=c14n)
    etree.SubElement(signed_info, _ds("SignatureMethod"), Algorithm=signature_method)
    etree.SubElement(signature, _ds("SignatureValue"), Id=f"{signature_id}-value")
    x509_data = etree.SubElement(etree.SubElement(signature, _ds("KeyInfo")), _ds("X509Data"))
    etree.SubElement(x509_data, _ds("X509Certificate")).text = base64.b64encode(cert.public_bytes(serialization.Encoding.DER)).decode()
    qualifying = etree.SubElement(etree.SubElement(signature, _ds("Object")), _xades("QualifyingProperties"), Target=f"#{signature_id}")
    signed_properties = etree.SubElement(qualifying, _xades("SignedProperties"), Id=f"{signature_id}-signed-properties")
    properties = etree.SubElement(signed_properties, _xades("SignedSignatureProperties"))
    etree.SubElement(properties, _xades("SigningTime")).text = SIGNING_TIME
    if signing_certificate == "v2":
        _cert_digest(etree.SubElement(properties, _xades("SigningCertificateV2")), cert, SHA256)
    elif signing_certificate == "v1":
        cert_element = _cert_digest(etree.SubElement(properties, _xades("SigningCertificate")), cert, SHA1)
        issuer_serial = etree.SubElement(cert_element, _xades("IssuerSerial"))
        etree.SubElement(issuer_serial, _ds("X509IssuerName")).text = cert.issuer.rfc4514_string()
        etree.SubElement(issuer_serial, _ds("X509SerialNumber")).text = str(cert.serial_number)
    return signature, signed_info


def _signed_properties_reference(signature, signed_info, digest):
    _add_reference(signed_info, f"#{signature.get('Id')}-signed-properties", digest, [EXC_C14N], SIGNED_PROPERTIES_TYPE)


def _element_by_id(signature, element_id):
    found = signature.getroottree().getroot().xpath("//*[@Id=$id]", id=element_id)
    return found[0] if found else None


def _without_signature(signature):
    root = signature.getroottree().getroot()
    path = []
    element = signature
    while element is not root:
        path.append(element.getparent().index(element))
        element = element.getparent()
    document = copy.deepcopy(signature.getroottree())
    target = document.getroot()
    for index in reversed(path):
        target = target[index]
    target.getparent().remove(target)
    return document


def _reference_input(signature, reference, files):
    uri = reference.get("URI")
    algorithms = [transform.get("Algorithm") for transform in reference.iter(_ds("Transform"))]
    c14n = next((algorithm for algorithm in reversed(algorithms) if algorithm not in (ENVELOPED, BASE64)), C14N10)
    if uri == "":
        return etree.tostring(_without_signature(signature), method="c14n", exclusive=c14n.startswith(EXC_C14N), with_comments=False)
    if uri is not None and uri.startswith("#"):
        target = _element_by_id(signature, uri[1:])
        if target is None:
            return None
        if BASE64 in algorithms:
            return base64.b64decode("".join(target.itertext()))
        return _canonical(target, c14n)
    name = next((name for name in files if quote(name) == uri or name == uri), None)
    if name is None and uri is None and len(files) == 1:
        name = next(iter(files))
    return files.get(name) if name is not None else None


def _fill_digests(signature, parent, files):
    for reference in parent.findall(_ds("Reference")):
        data = _reference_input(signature, reference, files)
        algorithm = DIGESTS.get(reference.find(_ds("DigestMethod")).get("Algorithm"), hashlib.sha256)
        reference.find(_ds("DigestValue")).text = base64.b64encode(algorithm(data).digest()).decode() if data is not None else ""


def _sign(signature, signed_info, key):
    method = signed_info.find(_ds("SignatureMethod")).get("Algorithm")
    c14n = signed_info.find(_ds("CanonicalizationMethod")).get("Algorithm")
    data = _canonical(signed_info, c14n, with_comments=c14n.endswith("WithComments"))
    hash_algorithm = SIGNATURE_HASHES[method]()
    if isinstance(key, ec.EllipticCurvePrivateKey):
        r, s = utils.decode_dss_signature(key.sign(data, ec.ECDSA(hash_algorithm)))
        size = (key.curve.key_size + 7) // 8
        value = r.to_bytes(size, "big") + s.to_bytes(size, "big")
    else:
        value = key.sign(data, padding.PKCS1v15(), hash_algorithm)
    signature.find(_ds("SignatureValue")).text = base64.b64encode(value).decode()


def _finish(signature, signed_info, key, files, mutate):
    if mutate is not None:
        mutate(signature)
    for manifest in signature.iter(_ds("Manifest")):
        _fill_digests(signature, manifest, files)
    _fill_digests(signature, signed_info, files)
    _sign(signature, signed_info, key)
    return signature


def _identity(key, cert):
    if key is None or cert is None:
        default_key, default_cert = test_identity()
        return key or default_key, cert or default_cert
    return key, cert


def xades_signature(files, *, signature_id="S1", digest=SHA256, signature_method=RSA_SHA256, c14n=C14N10,
                    signing_certificate="v2", key=None, cert=None, stamp=False, manifest=False, sign_key_info=False, mutate=None):
    key, cert = _identity(key, cert)
    wrapper = etree.Element(f"{{{ASIC}}}XAdESSignatures", nsmap={"asic": ASIC, "ds": DS})
    signature, signed_info = _skeleton(wrapper, signature_id, signature_method, c14n, cert, signing_certificate)
    if manifest:
        manifest_element = etree.SubElement(etree.SubElement(signature, _ds("Object")), _ds("Manifest"), Id=f"{signature_id}-manifest")
        for name in files:
            _add_reference(manifest_element, quote(name), digest)
        _add_reference(signed_info, f"#{signature_id}-manifest", digest, [C14N10], MANIFEST_TYPE)
    else:
        for name in files:
            _add_reference(signed_info, quote(name), digest)
    if sign_key_info:
        signature.find(_ds("KeyInfo")).set("Id", f"{signature_id}-key-info")
        _add_reference(signed_info, f"#{signature_id}-key-info", SHA256, [C14N10])
    _signed_properties_reference(signature, signed_info, digest)
    _finish(signature, signed_info, key, files, mutate)
    if stamp:
        _add_stamp(signature, stamp)
    return signature


def signatures_xml(*signatures):
    root = etree.Element(f"{{{ASIC}}}XAdESSignatures", nsmap={"asic": ASIC, "ds": DS})
    for signature in signatures:
        root.append(signature)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def enveloping(data, mime, *, base64_transform, signature_id="S1", mutate=None):
    key, cert = _identity(None, None)
    signature, signed_info = _skeleton(None, signature_id, RSA_SHA256, C14N10, cert, "v2")
    content = etree.SubElement(signature, _ds("Object"), Id=f"{signature_id}-content", MimeType=mime)
    content.text = base64.b64encode(data).decode()
    _add_reference(signed_info, f"#{signature_id}-content", SHA256, [BASE64] if base64_transform else [C14N10])
    _signed_properties_reference(signature, signed_info, SHA256)
    _finish(signature, signed_info, key, {}, mutate)
    return etree.tostring(signature, xml_declaration=True, encoding="UTF-8")


def enveloped(document, *, signature_id="S1", mutate=None):
    key, cert = _identity(None, None)
    signature, signed_info = _skeleton(document, signature_id, RSA_SHA256, C14N10, cert, "v2")
    _add_reference(signed_info, "", SHA256, [ENVELOPED, C14N10])
    _signed_properties_reference(signature, signed_info, SHA256)
    _finish(signature, signed_info, key, {}, mutate)
    return etree.tostring(document.getroottree(), xml_declaration=True, encoding="UTF-8")


def _manifest_xml(names):
    root = etree.Element(f"{{{MANIFEST_NS}}}manifest", nsmap={"manifest": MANIFEST_NS})
    etree.SubElement(root, f"{{{MANIFEST_NS}}}file-entry", {f"{{{MANIFEST_NS}}}full-path": "/",
                                                             f"{{{MANIFEST_NS}}}media-type": "application/vnd.etsi.asic-e+zip"})
    for name in names:
        etree.SubElement(root, f"{{{MANIFEST_NS}}}file-entry", {f"{{{MANIFEST_NS}}}full-path": name,
                                                                 f"{{{MANIFEST_NS}}}media-type": "application/octet-stream"})
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8")


def _container(mimetype, entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        if mimetype:
            archive.writestr(zipfile.ZipInfo("mimetype"), mimetype, compress_type=zipfile.ZIP_STORED)
        for name, data in entries:
            archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()


def asic_e(files, signature_files, *, mimetype=True, manifest=True):
    entries = list(files.items())
    if manifest:
        entries.append(("META-INF/manifest.xml", _manifest_xml(files)))
    entries += [(name if "/" in name else f"META-INF/{name}", data) for name, data in signature_files.items()]
    return _container("application/vnd.etsi.asic-e+zip" if mimetype else None, entries)


def asic_s(name, data, signature_file, *, kind="xades"):
    signature_name = "META-INF/signatures.xml" if kind == "xades" else "META-INF/signature.p7s"
    return _container("application/vnd.etsi.asic-s+zip", [(name, data), (signature_name, signature_file)])
