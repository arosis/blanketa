"""Signs a PDF and a ZFO form with an identity from certstore and checks the result, without a window.
Used by CI on every platform:

    python -I tools/selftest.py SOURCE [PASSWORD]

SOURCE is an identity source (system, test, file); the first identity of that source is used. Exits non-zero
when signing or verification fails.
"""
import io
import os
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["ZFO_OFFLINE"] = "1"  # tests never download trusted lists

from lxml import etree  # noqa: E402
from pypdf import PdfWriter  # noqa: E402

import asic_verify  # noqa: E402
import certstore  # noqa: E402
import pdf_sign  # noqa: E402
import zfo_sign  # noqa: E402
import zfo_verify  # noqa: E402


def main(source, password=None):
    found = [i for i in certstore.signing_identities() if i["source"] == source]
    print("identities:", [(i["id"], i["subject"]) for i in certstore.signing_identities()])
    if not found:
        sys.exit(f"no identity from source {source!r}")
    key, cert = certstore.load_identity(found[0]["id"], password)

    pdf = pdf_sign.sign_pdf(pdf_sign.fill_form_fields((ROOT / "samples-pdf/zadost.pdf").read_bytes(), {"jmeno": "Jana Nováková"}), key, cert)
    sigs = pdf_sign.describe_signatures(pdf)
    print("pdf:", [(s["subject"], s["intact"], s["covers_whole"]) for s in sigs])
    assert sigs and sigs[-1]["intact"] and sigs[-1]["covers_whole"], "PDF signature does not verify"

    # Government forms are often encrypted with an owner password only; they must fill and sign the same way.
    writer = PdfWriter(clone_from=io.BytesIO((ROOT / "samples-pdf/zadost.pdf").read_bytes()))
    writer.encrypt(user_password="", owner_password="selftest", algorithm="AES-128")
    encrypted = io.BytesIO()
    writer.write(encrypted)
    pdf = pdf_sign.sign_pdf(pdf_sign.fill_form_fields(encrypted.getvalue(), {"jmeno": "Jana Nováková"}), key, cert)
    sigs = pdf_sign.describe_signatures(pdf)
    print("encrypted pdf:", [(s["subject"], s["intact"], s["covers_whole"]) for s in sigs],
          pdf_sign.form_fields(pdf).get("jmeno"))
    assert sigs and sigs[-1]["intact"] and pdf_sign.form_fields(pdf).get("jmeno") == "Jana Nováková", \
        "encrypted PDF does not fill or verify"

    fo = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
    signed = zfo_sign.sign_form(fo, key, cert, "sha256")
    root = etree.parse(io.BytesIO(signed), etree.XMLParser(huge_tree=True)).getroot()
    layers = zfo_verify.describe_signature_layers(root)
    print("zfo:", [(l["subject"], l["intact"]) for l in layers])
    assert layers[0]["intact"] and layers[0]["subject"] == found[0]["subject"], "ZFO signature does not verify"

    sys.path.insert(0, str(ROOT / "tests"))
    import asic_samples
    files = {"smlouva.pdf": b"%PDF-1.7 selftest"}
    signatures = asic_samples.signatures_xml(asic_samples.xades_signature(files, key=key, cert=cert))
    description, _ = asic_verify.describe(asic_samples.asic_e(files, {"signatures.xml": signatures}))
    print("asic:", [(s["subject"], s["intact"]) for s in description["signatures"]], [f["state"] for f in description["files"]])
    assert description["signatures"][0]["intact"] and description["files"][0]["state"] == "signed", "ASiC signature does not verify"
    description, _ = asic_verify.describe(asic_samples.asic_e({"smlouva.pdf": b"%PDF-1.7 Selftest"}, {"signatures.xml": signatures}))
    assert description["files"][0]["state"] == "changed" and not description["signatures"][0]["intact"], \
        "a changed ASiC file is not detected"

    print("trust:", certstore.trust_verdicts([der for der in [l["der"] for l in layers[:1]]]))
    print("selftest ok")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
