import base64
import hashlib
import io
import sys
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
DIGEST_FUNCTIONS = {DS + "sha1": hashlib.sha1, "http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256}
ZIP_MAGIC = b"PK"
SIGNATURE_HASHES = {DS + "rsa-sha1": hashes.SHA1(), "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256()}


def is_zip_form(path):
    with open(path, "rb") as form:
        return form.read(len(ZIP_MAGIC)) == ZIP_MAGIC


def read_form_fo(path):
    data = open(path, "rb").read()
    if data.startswith(ZIP_MAGIC):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            return archive.read("form.fo")
    return data


def load_form_root(path):
    return etree.fromstring(read_form_fo(path), etree.XMLParser(huge_tree=True))


def canonicalize(element, algorithm):
    return etree.tostring(element, method="c14n", exclusive=(algorithm == EXC), with_comments=False)


def signed_object(signature, uri):
    # In 602XML always a dsig:Object of the layer itself, and the only element in the document with that Id. Anything else
    # is refused, so a pristine copy hidden elsewhere (e.g. in the unsigned KeyInfo) cannot stand in for a changed original
    # (signature wrapping).
    if not uri or not uri.startswith("#"):
        return None
    found = signature.getroottree().xpath("//*[@Id=$id]", id=uri[1:])
    if len(found) != 1 or found[0].getparent() is not signature or found[0].tag != f"{{{DS}}}Object":
        return None
    return found[0]


def _digest_matches(signature, reference):
    try:
        target = signed_object(signature, reference.get("URI"))
        if target is None:
            return False
        algorithms = [transform.get("Algorithm") for transform in reference.iter(f"{{{DS}}}Transform")]
        digest = DIGEST_FUNCTIONS[reference.find(f"{{{DS}}}DigestMethod").get("Algorithm")](canonicalize(target, algorithms[-1] if algorithms else C14N)).digest()
        return digest == base64.b64decode(reference.findtext(f"{{{DS}}}DigestValue"))
    except (AttributeError, KeyError, TypeError, ValueError, etree.LxmlError):
        return False


def check_signature_layer(signature):
    try:
        cert = x509.load_der_x509_certificate(base64.b64decode(signature.findtext(f"{{{DS}}}KeyInfo//{{{DS}}}X509Certificate").replace("\n", "")))
        # The names are only parsed when first read, and describe() needs them.
        cert.subject.rfc4514_string(), cert.issuer.rfc4514_string()
    except (AttributeError, ValueError, x509.InvalidVersion) as error:
        # Nothing can be said about who signed; the form is refused rather than shown with an anonymous signature.
        raise ValueError("signature without a readable certificate") from error
    signed_info = signature.find(f"{{{DS}}}SignedInfo")
    digests = []
    for reference in signed_info.findall(f"{{{DS}}}Reference") if signed_info is not None else ():
        digests.append((reference.get("URI"), _digest_matches(signature, reference)))
    try:
        algorithm = signed_info.find(f"{{{DS}}}CanonicalizationMethod").get("Algorithm")
        cert.public_key().verify(base64.b64decode(signature.findtext(f"{{{DS}}}SignatureValue")), canonicalize(signed_info, algorithm),
                                 padding.PKCS1v15(), SIGNATURE_HASHES[signed_info.find(f"{{{DS}}}SignatureMethod").get("Algorithm")])
        status = "OK"
    except Exception as error:
        status = f"FAIL {error!r}"
    return digests, status, cert


def signature_layers(signature):
    while signature is not None:
        yield signature
        signed_object_element = signature.find(f"{{{DS}}}Object")
        signature = signed_object_element.find(f"{{{DS}}}Signature") if signed_object_element is not None else None


def describe_signature_layers(root):
    result = []
    for signature in signature_layers(root):
        digests, status, cert = check_signature_layer(signature)
        subject_names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        issuer_names = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        signed_at = signature.xpath(f"dsig:Object/dsig:SignatureProperties/dsig:SignatureProperty[@Target='#{signature.get('Id')}']//@datetime",
                              namespaces={"dsig": DS})
        result.append({
            "subject": subject_names[0].value if subject_names else cert.subject.rfc4514_string(),
            "issuer": issuer_names[0].value if issuer_names else cert.issuer.rfc4514_string(),
            "publisher": "602XML" in cert.subject.rfc4514_string(),
            # Form Filler: "Publication Key" = the form's publisher; "License Key" = only the licence of the
            # designer tool, a form with no publication layer "has not been published yet".
            "role": ("publication" if "Publication Key" in cert.subject.rfc4514_string()
                     else "license" if "License Key" in cert.subject.rfc4514_string() else None),
            "intact": status == "OK" and bool(digests) and all(ok for _, ok in digests),
            "not_after": cert.not_valid_after_utc.isoformat(),
            "signed_at": signed_at[0] if signed_at else None,
            "der": cert.public_bytes(serialization.Encoding.DER),
        })
    return result


def print_verification(signature):
    ok = True
    for depth, layer in enumerate(signature_layers(signature)):
        indent = "  " * depth
        digests, status, cert = check_signature_layer(layer)
        for uri, match in digests:
            print(indent + f"digest {uri}: {'OK' if match else 'MISMATCH'}")
        print(indent + f"signature {layer.get('Id')}: {status} | {cert.subject.rfc4514_string()} | {cert.not_valid_after_utc:%Y-%m-%d}")
        ok &= status == "OK" and bool(digests) and all(match for _, match in digests)
    return ok


if __name__ == "__main__":
    sys.exit(0 if print_verification(load_form_root(sys.argv[1])) else 1)
