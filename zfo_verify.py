"""Verify every nested enveloping XMLDSig layer of a 602XML form (.fo or .zfo)."""
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
DIG = {DS + "sha1": hashlib.sha1, "http://www.w3.org/2001/04/xmlenc#sha256": hashlib.sha256}
SIG = {DS + "rsa-sha1": hashes.SHA1(), "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256": hashes.SHA256()}


def load_root(path):
    data = open(path, "rb").read()
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read("form.fo")
    return etree.fromstring(data, etree.XMLParser(huge_tree=True))


def c14n(el, alg):
    return etree.tostring(el, method="c14n", exclusive=(alg == EXC), with_comments=False)


def signed_object(sig, uri):
    """The element a reference signs: in 602XML always a dsig:Object of the layer itself, and the only element in
    the document with that Id. Anything else is refused, so a pristine copy hidden elsewhere (e.g. in the unsigned
    KeyInfo) cannot stand in for a changed original (signature wrapping)."""
    if not uri or not uri.startswith("#"):
        return None
    found = sig.getroottree().xpath("//*[@Id=$id]", id=uri[1:])
    if len(found) != 1 or found[0].getparent() is not sig or found[0].tag != f"{{{DS}}}Object":
        return None
    return found[0]


def _digest_matches(sig, ref):
    """Whether one reference holds; damage or an algorithm this code does not know means it does not."""
    try:
        target = signed_object(sig, ref.get("URI"))
        if target is None:
            return False
        algs = [t.get("Algorithm") for t in ref.iter(f"{{{DS}}}Transform")]
        digest = DIG[ref.find(f"{{{DS}}}DigestMethod").get("Algorithm")](c14n(target, algs[-1] if algs else C14N)).digest()
        return digest == base64.b64decode(ref.findtext(f"{{{DS}}}DigestValue"))
    except (AttributeError, KeyError, TypeError, ValueError, etree.LxmlError):
        return False


def check(sig):
    """Returns ([(reference URI, digest ok)], signature status, certificate) for one layer."""
    try:
        cert = x509.load_der_x509_certificate(base64.b64decode(sig.findtext(f"{{{DS}}}KeyInfo//{{{DS}}}X509Certificate").replace("\n", "")))
        # The names are only parsed when first read, and describe() needs them.
        cert.subject.rfc4514_string(), cert.issuer.rfc4514_string()
    except (AttributeError, ValueError, x509.InvalidVersion) as e:
        # Nothing can be said about who signed; the form is refused rather than shown with an anonymous signature.
        raise ValueError("signature without a readable certificate") from e
    si = sig.find(f"{{{DS}}}SignedInfo")
    digests = []
    for ref in si.findall(f"{{{DS}}}Reference") if si is not None else ():
        digests.append((ref.get("URI"), _digest_matches(sig, ref)))
    try:
        alg = si.find(f"{{{DS}}}CanonicalizationMethod").get("Algorithm")
        cert.public_key().verify(base64.b64decode(sig.findtext(f"{{{DS}}}SignatureValue")), c14n(si, alg),
                                 padding.PKCS1v15(), SIG[si.find(f"{{{DS}}}SignatureMethod").get("Algorithm")])
        status = "OK"
    except Exception as e:
        status = f"FAIL {e!r}"
    return digests, status, cert


def layers(sig):
    while sig is not None:
        yield sig
        obj = sig.find(f"{{{DS}}}Object")
        sig = obj.find(f"{{{DS}}}Signature") if obj is not None else None


def describe(root):
    """One dict per signature layer, outermost first, for showing in the UI."""
    result = []
    for sig in layers(root):
        digests, status, cert = check(sig)
        names = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        issuer = cert.issuer.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
        signed_at = sig.xpath(f"dsig:Object/dsig:SignatureProperties/dsig:SignatureProperty[@Target='#{sig.get('Id')}']//@datetime",
                              namespaces={"dsig": DS})
        result.append({
            "subject": names[0].value if names else cert.subject.rfc4514_string(),
            "issuer": issuer[0].value if issuer else cert.issuer.rfc4514_string(),
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


def verify(sig, depth=0):
    ok = True
    for depth, layer in enumerate(layers(sig)):
        pad = "  " * depth
        digests, status, cert = check(layer)
        for uri, match in digests:
            print(pad + f"digest {uri}: {'OK' if match else 'MISMATCH'}")
        print(pad + f"signature {layer.get('Id')}: {status} | {cert.subject.rfc4514_string()} | {cert.not_valid_after_utc:%Y-%m-%d}")
        ok &= status == "OK" and bool(digests) and all(match for _, match in digests)
    return ok


if __name__ == "__main__":
    sys.exit(0 if verify(load_root(sys.argv[1])) else 1)
