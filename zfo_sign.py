"""Fill a 602XML form and add an outer enveloping XMLDSig layer, mirroring what Software602 Form Filler produces.

usage: zfo_sign.py IN.zfo OUT.zfo KEY.pem CERT.pem [--sha1] [field=value ...]

Filled data live in an unreferenced dsig:Object appended to the publisher signature
(the top-level signature of the unfilled form), so the publisher layers stay valid
and only the user's signature covers the data.
"""
import sys, copy, zipfile, hashlib, base64, random
from datetime import datetime, timezone
from lxml import etree
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DS = "http://www.w3.org/2000/09/xmldsig#"
FM = "http://software602.cz/forms"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
EXC = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALGS = {
    "sha1": (DS + "rsa-sha1", DS + "sha1", hashes.SHA1(), hashlib.sha1),
    "sha256": ("http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
               "http://www.w3.org/2001/04/xmlenc#sha256", hashes.SHA256(), hashlib.sha256),
}


def d(tag):
    return f"{{{DS}}}{tag}"


def new_ids(root, count):
    used = set(root.xpath("//@Id"))
    ids = []
    while len(ids) < count:
        candidate = f"_{random.randint(10**7, 10**8 - 1)}"
        if candidate not in used:
            used.add(candidate)
            ids.append(candidate)
    return ids


def fill(inner, values, strict=True):
    data_objects = inner.xpath("dsig:Object[not(@Id)]", namespaces={"dsig": DS})
    if data_objects:
        data = data_objects[0][0]
    else:
        instance = inner.xpath("//form:model/form:instance/*", namespaces={"form": FM})[0]
        data = copy.deepcopy(instance)
        etree.SubElement(inner, d("Object")).append(data)
    for name, value in values.items():
        matches = data.xpath(".//*[local-name()=$n]", n=name)
        if matches:
            matches[0].text = value
        elif strict:
            raise SystemExit(f"field not found: {name}")


def sign(fo_bytes, key, cert, alg="sha256", values=None, strict=True):
    sig_uri, dig_uri, sig_hash, dig_fn = ALGS[alg]
    inner = etree.fromstring(fo_bytes, etree.XMLParser(huge_tree=True))
    if values:
        fill(inner, values, strict)
    sig_id, obj_id = new_ids(inner, 2)

    sig = etree.Element(d("Signature"), nsmap={"dsig": DS}, Id=sig_id)
    si = etree.SubElement(sig, d("SignedInfo"))
    etree.SubElement(si, d("CanonicalizationMethod"), Algorithm=EXC)
    etree.SubElement(si, d("SignatureMethod"), Algorithm=sig_uri)
    ref = etree.SubElement(si, d("Reference"), URI="#" + obj_id)
    transforms = etree.SubElement(ref, d("Transforms"))
    etree.SubElement(transforms, d("Transform"), Algorithm=C14N)
    etree.SubElement(ref, d("DigestMethod"), Algorithm=dig_uri)
    digest_value = etree.SubElement(ref, d("DigestValue"))
    sig_value = etree.SubElement(sig, d("SignatureValue"), Id="_" + obj_id)
    ki = etree.SubElement(sig, d("KeyInfo"))
    x509data = etree.SubElement(ki, d("X509Data"))
    etree.SubElement(x509data, d("X509Certificate")).text = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER)).decode()

    obj = etree.SubElement(sig, d("Object"), Id=obj_id, MimeType="text/xml")
    obj.append(inner)
    props = etree.SubElement(obj, d("SignatureProperties"))
    prop = etree.SubElement(props, d("SignatureProperty"), Target="#" + sig_id)
    etree.SubElement(prop, f"{{{FM}}}systeminfo", nsmap={"fm": FM},
                     datetime=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    digest_value.text = base64.b64encode(
        dig_fn(etree.tostring(obj, method="c14n", exclusive=False)).digest()).decode()
    signed_info = etree.tostring(si, method="c14n", exclusive=True)
    sig_value.text = base64.b64encode(key.sign(signed_info, padding.PKCS1v15(), sig_hash)).decode()
    return etree.tostring(sig, xml_declaration=True, encoding="UTF-8")


def main():
    src, dst, key_path, cert_path = sys.argv[1:5]
    alg = "sha1" if "--sha1" in sys.argv else "sha256"
    values = dict(arg.split("=", 1) for arg in sys.argv[5:] if "=" in arg)
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), None)
    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "form.fo":
                data = sign(data, key, cert, alg, values)
            zout.writestr(info, data, compress_type=info.compress_type)


if __name__ == "__main__":
    main()
