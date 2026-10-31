#!/usr/bin/env python3
"""Writes samples/skripty/vypocty.zfo: a form with the kinds of 602 JavaScript that published forms use, for tests/test_app.py.

    .venv/bin/python tools/make-script-samples.py

Everything in it is made up here, and it stays unsigned: scripts are the point, not the signature.
"""
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "samples/skripty/vypocty.zfo"

LIBRARY = r"""
function Tisk_Spojeni(a, b)
{
  var ele = GetDocument().GetElement();
  var out = "";
  var parts = [ele.DataXpath(a), ele.DataXpath(b)];
  for (var i = 0; i < parts.length; i++) {
    if (parts[i] != "") out += (out == "" ? "" : " ") + parts[i];
  }
  return out;
}

function Pom_RodneCisloNaDatum(rc)
{
  var out = "";
  if (rc !== "" && RCCheck(rc.replace(/\//g, "")) && rc.indexOf("/") == 6) {
    rc = rc.replace(/\//g, "");
    var rok = rc.substring(0, 2);
    var mesic = parseInt(rc.substring(2, 4));
    var den = parseInt(rc.substring(4, 6));
    if (rc.length == 9) rok = "19" + rok;
    else parseInt(rok) < 54 ? rok = "20" + rok : rok = "19" + rok;
    if (mesic > 50) mesic = mesic - 50;
    out = rok + "-" + (mesic < 10 ? "0" : "") + mesic + "-" + (den < 10 ? "0" : "") + den;
  }
  return out;
}

function Pom_PridatDny(datum, dny)
{
  var reg = new RegExp("^([0-9]{4})-([0-9]{2})-([0-9]{2})$");
  if (!reg.test(datum)) return "";
  var m = reg.exec(datum);
  var d = new Date(parseInt(m[1]), parseInt(m[2]) - 1, parseInt(m[3]));
  d.setDate(d.getDate() + dny);
  return d.getDate() + "." + (d.getMonth() + 1) + "." + d.getFullYear();
}

function RegExp(x, typ)
{
  switch (typ) {
    case "PSC": var r = /^[1-7][0-9]{4}$/; break;
    default: var r = /^.*$/;
  }
  return r.test(x);
}

function Validace_Email(x)
{
  return x == "" || /^[_A-Za-z0-9-\+]+(\.[_A-Za-z0-9-]+)*@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(\.[A-Za-z]{2,})$/.test(x);
}
"""

BINDS = {
  # the shape of the cadastre form: nothing but DataXpath, concatenation and Result
  "vyplneno": ('calculate', """{ var doc=GetDocument();
var elem=doc.GetElement();
var cely_text = (elem.DataXpath("../d:jmeno") + elem.DataXpath("../d:prijmeni"));
var tisk = elem.DataXpath("../d:tisk");
if (cely_text == "" && tisk == "1") { Result("1"); }
if (cely_text != "" && tisk == "1") { Result("0"); }
if (tisk == "0") { Result("0"); } }""", "jmeno|prijmeni|tisk"),
  # the shape of the labour ministry's forms: one call of their library
  "cele_jmeno": ('calculate', '{ Result("" + Tisk_Spojeni("../d:jmeno", "../d:prijmeni")); }', "jmeno|prijmeni"),
  "narozen": ('calculate', '{ Result(Pom_RodneCisloNaDatum(GetDocument().GetElement().DataXpath("../d:rc"))); }', "rc"),
  "lhuta": ('calculate', '{ Result(Pom_PridatDny(GetDocument().GetElement().DataXpath("../d:narozen"), 30)); }', "narozen"),
  "psc_ok": ('calculate', '{ Result(RegExp(GetDocument().GetElement().DataXpath("../d:psc"), "PSC") + ""); }', "psc"),
  "email": ('constraint', """{
  var elem = GetDocument().GetElement();
  Result((((!(elem.DataXpath("../d:tisk = '1'") === "true" && ((function() { function constraint() {
  return Validace_Email(GetDocument().GetElement().DataXpath("."));
} return constraint(); })() === false))))) + "");}""", ""),
  # what must never run: its value has to stay as it was, and the form must say so
  "odeslano": ('calculate', '{ var doc = GetDocument(); doc.SendData(new Variant(VarStr, "x"), new SendParam()); Result("hotovo"); }', "jmeno"),
}

FIELDS = ("tisk", "jmeno", "prijmeni", "rc", "psc", *BINDS)
START = {"tisk": "1", "odeslano": "puvodni"}


def main():
    instance = "".join(f"<d:{name}>{START.get(name, '')}</d:{name}>" for name in FIELDS)
    binds = "".join(f'<fm:bind Id="{name}" nodeset="d:{name}"/>' for name in FIELDS if name not in BINDS)
    for name, (attribute, script, depends) in BINDS.items():
        binds += f'<fm:bind Id="{name}" nodeset="d:{name}" {attribute}={quoteattr(script)}' + (f' depends="{depends}"' if depends else "")
        binds += ' constraintcomment="Neplatná hodnota"/>' if attribute == "constraint" else "/>"
    form = f"""<?xml version="1.0" encoding="UTF-8"?>
<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root>{instance}</d:root></fm:instance><fm:bind Id="koren" nodeset="/d:root">{binds}</fm:bind></fm:model>
<fm:script name="knihovna" usage="1"><![CDATA[{LIBRARY}]]></fm:script></fm:formparams>
<fo:block form602btnclick='{{ Submit_ares(GetDocument().GetElement().DataXpath("/d:root/d:jmeno")); }}'/>
</fo:root></dsig:Object></dsig:Signature>
"""
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("form.fo", form.encode("utf-8"))
    print(f"written {TARGET}", file=sys.stdout)


if __name__ == "__main__":
    main()
