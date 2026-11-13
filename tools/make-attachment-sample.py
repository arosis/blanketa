#!/usr/bin/env python3
"""Writes samples/skripty/prilohy.zfo: a form with a list of attachments as the newer forms of the labour ministry have it,
for trying attachments in the built apps (tools/try-attachments-on-windows.ps1).

    .venv/bin/python tools/make-attachment-sample.py

Everything in it is made up here. The apps open only a form whose publisher's certificate can be read, so the form names
the certificate of the test authority (pki/ca.crt); it is not signed by it and the apps say it has no publisher.
"""
import base64
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from cryptography import x509
from cryptography.hazmat.primitives import serialization

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "samples/skripty/prilohy.zfo"
TEST_AUTHORITY = ROOT / "pki/ca.crt"
LARGEST_FILE_IN_BYTES = 17 * 1024 * 1024

NOTE_THAT_THE_LIST_IS_FILLED = "{ GetDocument().GetElement().SetDataTo('empty', '0'); }"
JUDGE_LISTED_FILE = """{
var ele = GetDocument().GetElement();
if (ele.CheckBinData(".", MIMETYPE_CHECK | ANTIVIRUS_CHECK) != 0) {
  MessageBox("Soubor nebyl vložen.");
  if (!ele.RemoveSection(".")) {
    ele.SetData("../d:filename", "att.filename", ""); ele.SetBinData(".", new Variant()); ele.SetData("../../d:empty", "empty", "1");
  }
}
}"""
REMOVE_LISTED_FILE = """{
var ele = GetDocument().GetElement();
if (!ele.RemoveSection(".")) {
  ele.SetData("../d:filename", "att.filename", ""); ele.SetBinData("ancestor::*[@form602ct='rep'][1]//fo:basic-link", new Variant());
  ele.SetData("../d:size", "att.size", ""); ele.SetData("../../d:empty", "empty", "1");
}
}"""

FORM = """<?xml version="1.0" encoding="UTF-8"?>
<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1">
<dsig:KeyInfo><dsig:X509Data><dsig:X509Certificate>{certificate}</dsig:X509Certificate></dsig:X509Data></dsig:KeyInfo>
<dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:attachments>
<d:attachment><d:data/><d:filename/><d:size/><d:extension/><d:timestamp>0</d:timestamp></d:attachment><d:empty>1</d:empty></d:attachments>
</d:root></fm:instance>
<fm:bind Id="k" nodeset="/d:root"><fm:bind Id="att" nodeset="d:attachments/d:attachment"><fm:bind Id="att.data" nodeset="d:data"/>
<fm:bind Id="att.filename" nodeset="d:filename"/><fm:bind Id="att.size" nodeset="d:size"/><fm:bind Id="att.extension" nodeset="d:extension"/>
<fm:bind Id="att.timestamp" nodeset="d:timestamp"/></fm:bind><fm:bind Id="empty" nodeset="d:attachments/d:empty"/></fm:bind>
</fm:model></fm:formparams>
<fo:page-sequence master-reference="p"><fo:flow flow-name="xsl-region-body">
<fo:block font-weight="bold">Zkušební formulář s přílohami</fo:block>
<fo:block fa-data-id="attachments-section">
<fo:inline form602ct="btm" form602bindref="att.data" form602attchsec="ancestor::*[@fa-data-id='attachments-section']//*[@form602ct='rep']"
 form602maxallfilessize="{largest}" form602maxonefilesize="{largest}" form602maxoccurs="3" form602webaccept=".pdf,.png"
 form602webaccepttype="strict" form602attchuploadstartaction={start}>Nahrajte ze zařízení</fo:inline>
<fo:block form602ct="rep" form602bind="att">
<fo:basic-link form602ct="bil" form602bindref="att.data" filenamebind="att.filename" filesizebind="att.size" mediatypebind="att.extension"
 form602onchange={judge}/>
<fo:inline form602ct="out" form602bindref="att.data" form602format="attch-size-kB"/>
<fo:inline form602ct="btn" form602bindref="att.data" form602btnclick={remove}>Odebrat přílohu</fo:inline>
</fo:block></fo:block>
</fo:flow></fo:page-sequence></fo:root></dsig:Object></dsig:Signature>
"""


def form_of_the_sample():
    authority = x509.load_pem_x509_certificate(TEST_AUTHORITY.read_bytes())
    return FORM.format(certificate=base64.b64encode(authority.public_bytes(serialization.Encoding.DER)).decode(),
                       largest=LARGEST_FILE_IN_BYTES, start=quoteattr(NOTE_THAT_THE_LIST_IS_FILLED),
                       judge=quoteattr(JUDGE_LISTED_FILE), remove=quoteattr(REMOVE_LISTED_FILE)).encode("utf-8")


def main():
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("form.fo", form_of_the_sample())
    print(f"written {TARGET}", file=sys.stdout)


if __name__ == "__main__":
    main()
