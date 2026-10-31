import base64
import copy
import hashlib
import random
import sys
import zipfile
from datetime import datetime, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from lxml import etree

DS = "http://www.w3.org/2000/09/xmldsig#"
FM = "http://software602.cz/forms"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
EXCLUSIVE_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALGORITHMS = {
    "sha1": (DS + "rsa-sha1", DS + "sha1", hashes.SHA1(), hashlib.sha1),
    "sha256": ("http://www.w3.org/2001/04/xmldsig-more#rsa-sha256",
               "http://www.w3.org/2001/04/xmlenc#sha256", hashes.SHA256(), hashlib.sha256),
}


def ds_tag(tag):
    return f"{{{DS}}}{tag}"


def new_unique_ids(root, count):
    used = set(root.xpath("//@Id"))
    ids = []
    while len(ids) < count:
        candidate = f"_{random.randint(10**7, 10**8 - 1)}"
        if candidate not in used:
            used.add(candidate)
            ids.append(candidate)
    return ids


def fill_data_instance(inner, values, strict=True):
    # The data live in an unreferenced dsig:Object appended to the publisher signature, so the publisher layers stay valid
    # and only the user's signature covers the data.
    data_objects = inner.xpath("dsig:Object[not(@Id)]", namespaces={"dsig": DS})
    if data_objects:
        data = data_objects[0][0]
    else:
        instance = inner.xpath("//form:model/form:instance/*", namespaces={"form": FM})[0]
        data = copy.deepcopy(instance)
        etree.SubElement(inner, ds_tag("Object")).append(data)
    for name, value in values.items():
        matches = data.xpath(".//*[local-name()=$name]", name=name)
        if matches:
            matches[0].text = value
        elif strict:
            raise SystemExit(f"field not found: {name}")


def sign_form(fo_bytes, key, cert, algorithm="sha256", values=None, strict=True):
    signature_method, digest_method, signature_hash, digest_function = ALGORITHMS[algorithm]
    inner = etree.fromstring(fo_bytes, etree.XMLParser(huge_tree=True))
    if values:
        fill_data_instance(inner, values, strict)
    signature_id, object_id = new_unique_ids(inner, 2)

    signature = etree.Element(ds_tag("Signature"), nsmap={"dsig": DS}, Id=signature_id)
    signed_info_element = etree.SubElement(signature, ds_tag("SignedInfo"))
    etree.SubElement(signed_info_element, ds_tag("CanonicalizationMethod"), Algorithm=EXCLUSIVE_C14N)
    etree.SubElement(signed_info_element, ds_tag("SignatureMethod"), Algorithm=signature_method)
    reference = etree.SubElement(signed_info_element, ds_tag("Reference"), URI="#" + object_id)
    transforms = etree.SubElement(reference, ds_tag("Transforms"))
    etree.SubElement(transforms, ds_tag("Transform"), Algorithm=C14N)
    etree.SubElement(reference, ds_tag("DigestMethod"), Algorithm=digest_method)
    digest_value = etree.SubElement(reference, ds_tag("DigestValue"))
    signature_value = etree.SubElement(signature, ds_tag("SignatureValue"), Id="_" + object_id)
    key_info = etree.SubElement(signature, ds_tag("KeyInfo"))
    x509data = etree.SubElement(key_info, ds_tag("X509Data"))
    etree.SubElement(x509data, ds_tag("X509Certificate")).text = base64.b64encode(
        cert.public_bytes(serialization.Encoding.DER)).decode()

    signed_object = etree.SubElement(signature, ds_tag("Object"), Id=object_id, MimeType="text/xml")
    signed_object.append(inner)
    properties = etree.SubElement(signed_object, ds_tag("SignatureProperties"))
    signature_property = etree.SubElement(properties, ds_tag("SignatureProperty"), Target="#" + signature_id)
    etree.SubElement(signature_property, f"{{{FM}}}systeminfo", nsmap={"fm": FM},
                     datetime=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    digest_value.text = base64.b64encode(
        digest_function(etree.tostring(signed_object, method="c14n", exclusive=False)).digest()).decode()
    signed_info = etree.tostring(signed_info_element, method="c14n", exclusive=True)
    signature_value.text = base64.b64encode(key.sign(signed_info, padding.PKCS1v15(), signature_hash)).decode()
    return etree.tostring(signature, xml_declaration=True, encoding="UTF-8")


def main():
    source, target, key_path, cert_path = sys.argv[1:5]
    algorithm = "sha1" if "--sha1" in sys.argv else "sha256"
    values = dict(arg.split("=", 1) for arg in sys.argv[5:] if "=" in arg)
    key = serialization.load_pem_private_key(open(key_path, "rb").read(), None)
    cert = x509.load_pem_x509_certificate(open(cert_path, "rb").read())
    with zipfile.ZipFile(source) as source_archive, zipfile.ZipFile(target, "w") as signed_archive:
        for info in source_archive.infolist():
            data = source_archive.read(info.filename)
            if info.filename == "form.fo":
                data = sign_form(data, key, cert, algorithm, values)
            signed_archive.writestr(info, data, compress_type=info.compress_type)


if __name__ == "__main__":
    main()
