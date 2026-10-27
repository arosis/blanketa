"""End-to-end check of zfoengine on the CUZK form, driven like zfo_script.js drives it.

Plain script (no pytest needed):  .venv/bin/python -I tests/test_engine.py

The page HTML is rendered with the editor's fo2html.xsl (as app.py does) and the
browser side is simulated: ids are assigned like identify_inputs()/identify_date_inputs(),
payloads are built like set_inputs_init_values(), get_parent_repe(), server_add_section().
"""
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lxml import etree, html as lhtml  # noqa: E402

from zfoengine import api  # noqa: E402

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s: %(message)s")
# calculate binds written in 602's JavaScript ({ var doc=GetDocument(); ...}) cannot be
# evaluated as XPath - the original logs and skips them too; keep the output readable
logging.getLogger("zfo.evaluate").setLevel(logging.CRITICAL)

EDITOR = ROOT / "vendor/zfo-editor-src/zfo-editor-0.3.2"
DS = "http://www.w3.org/2000/09/xmldsig#"
D = {"d": "http://software602.cz/sample"}
CHECKS = []


def check(cond, what):
    CHECKS.append(what)
    print(("ok   " if cond else "FAIL ") + what)
    assert cond, what


def load_fo(path):
    return api.read_form_fo((ROOT / path).read_bytes())


def render_html(fo):
    """Same transformation as app.py render()."""
    doc = etree.fromstring(fo, etree.XMLParser(huge_tree=True))
    for props in doc.xpath("//dsig:SignatureProperties", namespaces={"dsig": DS}):
        props.getparent().remove(props)
    xslt = etree.XSLT(etree.parse(str(EDITOR / "xslt/fo2html.xsl")))
    out = etree.tostring(xslt(etree.ElementTree(doc), PATH_TO_JS="'/s/'", PATH_TO_CSS="'/s/'"),
                         method="html", encoding="unicode")
    return lhtml.fromstring(out)


def identify(page):
    """identify_date_inputs() + identify_inputs() from helpers.js."""
    n = 0
    for i, el in enumerate(page.xpath("//input[contains(concat(' ', @class, ' '), ' date-pick ')]")):
        el.set("id", "dateinput-%d" % i)
    for el in page.xpath("//input[not(@type='radio') and not(contains(concat(' ', @class, ' '), ' date-pick '))]"):
        el.set("id", "input-%d" % n); n += 1
    for el in page.xpath("//select"):
        el.set("id", "select-%d" % n); n += 1


def rep_classes(el):
    return [c for c in (el.get("class") or "").split() if c in
            ("602-repetition-block", "602-repetition-row", "602-repetition-table")]


def get_parent_repe(el):
    """get_parent_repe() from repe_sections.js."""
    parts = []
    chain = [el] + [a for a in el.iterancestors()]
    for e in chain:
        if rep_classes(e):
            cls = e.get("class")
            parts.append(cls[cls.rfind("-") + 1:] + "---" + e.get("name"))
    return "|||".join(parts)


def js_call(resp, func):
    """Returns the JSON argument of `func(...)` in a javascript response."""
    for r in resp:
        if r["type"] == "javascript" and r["data"].startswith(func + "("):
            arg = r["data"][len(func) + 1:-1]
            return json.loads(arg) if arg else None
    raise AssertionError("no %s in %r" % (func, [r.get("data", "")[:60] for r in resp]))


def valid(resp, name):
    ok = isinstance(resp, list) and all(isinstance(r, dict) and r.get("type") in ("javascript", "gtk") for r in resp)
    ok = ok and all(isinstance(r["data"], str) and r["data"].strip() for r in resp if r["type"] == "javascript")
    json.dumps(resp, ensure_ascii=False)
    check(ok, "%s: response is a list of valid dicts (%d items)" % (name, len(resp)))
    errors = [r["data"]["to_display"] for r in resp if r["type"] == "gtk" and r["info_type"] == "error"]
    return resp, errors


def startup(doc, page):
    """$(document).ready + init_rep_sections_callback chain of zfo_script.js."""
    resp, _ = valid(api.handle(doc, "document.ready", ""), "document.ready")
    reps = {}
    for el in page.xpath("//*[starts-with(@class, '602-repetition-')]"):
        reps[el.get("name")] = lhtml.tostring(el, encoding="unicode")
    resp, err = valid(api.handle(doc, "set_html_templates", reps), "set_html_templates")
    check(resp[0]["data"] == "empty_callback()" and not err, "set_html_templates: %d templates accepted" % len(reps))

    resp, err = valid(api.handle(doc, "init_rep_sections", ""), "init_rep_sections")
    data = js_call(resp, "init_rep_sections_callback")
    check(not err and "opak1" in data["sections"] and data["templates"].get("opak1"),
          "init_rep_sections: sections %s, templates for all" % sorted(data["sections"]))
    check(all(data["templates"][k] for k in data["templates"]), "init_rep_sections: no empty template")

    resp, err = valid(api.handle(doc, "get_readonly_binds", ""), "get_readonly_binds")
    ro = js_call(resp, "set_readonly_inputs_callback")["readonly_names"]
    check(not err and "o2_cislo" in ro, "get_readonly_binds: %d readonly binds (calculated ones included)" % len(ro))

    identify(page)
    payload = {}
    for el in page.xpath("//input[@type='text' or @type='date']"):
        if el.get("name"):
            payload[el.get("id")] = {"type": "text", "bind": el.get("name")}
    for el in page.xpath("//input[@type='radio']"):
        payload[el.get("name")] = {"type": "radio", "bind": el.get("name")}
    for el in page.xpath("//input[@type='checkbox']"):
        payload[el.get("id")] = {"type": "check", "bind": el.get("name")}
    for el in page.xpath("//*[contains(concat(' ', @class, ' '), ' outputelement ')]"):
        if el.get("id"):
            payload[el.get("id")] = {"type": "output", "bind": el.get("name")}
    t = time.time()
    resp, err = valid(api.handle(doc, "get_init_values", payload), "get_init_values")
    init = js_call(resp, "set_inputs_init_values_callback")
    check(not err and len(init) == len(payload) and all("value" in v for v in init.values() if v["bind"]),
          "get_init_values: %d inputs answered in %.2fs" % (len(init), time.time() - t))

    resp, err = valid(api.handle(doc, "get_repeatable", ""), "get_repeatable")
    rep = js_call(resp, "calc_repeatable_sections_callback")
    check(not err and isinstance(rep["opak1"], list), "get_repeatable: %d top-level sections" % len(rep))

    resp, err = valid(api.handle(doc, "get_selected_listboxes", ""), "get_selected_listboxes")
    lbs = js_call(resp, "get_selected_listboxes_callback")
    check(not err and all(v["type"] in ("value", "itemtext") for v in lbs.values()),
          "get_selected_listboxes: %d listboxes" % len(lbs))

    resp, err = valid(api.handle(doc, "get_multiswitch_list", ""), "get_multiswitch_list")
    ms = js_call(resp, "set_multiswitch_controls_callback")["data"]
    check(not err and ms and all("cases" in m for m in ms), "get_multiswitch_list: %d multiswitches" % len(ms))
    return init


def store(doc, page, name, value, nth=0):
    el = page.xpath("//*[@name=$n]", n=name)[nth]
    data = {"field": name, "value": value, "repe": get_parent_repe(el), "input_id": el.get("id") or name}
    resp, err = valid(api.handle(doc, "store_val", data), "store_val %s=%r (repe %r)" % (name, value, data["repe"]))
    return resp, err


def text(doc, xpath):
    return [e.text for e in api.data_element(doc).xpath(xpath, namespaces=D)]


def main():
    t = time.time()
    fo = load_fo("samples/cuzk/form.zfo")
    doc = api.open_document(fo)
    page = render_html(fo)
    print("opened + rendered samples/cuzk/form.zfo in %.2fs" % (time.time() - t))
    check(api.data_element(doc).tag == "{http://software602.cz/sample}root", "data_element is d:root")

    startup(doc, page)

    # --- store_val
    resp, err = store(doc, page, "o1_prijmeni", "Nováková")
    ok = js_call(resp, "field_changed_ok_callback")
    check(not err and ok["calculated"] == {} or isinstance(ok["calculated"], dict), "store_val: field_changed_ok_callback")
    check(text(doc, "/d:root/d:sekce1/d:opak1/d:o1_prijmeni") == ["Nováková"], "data element contains o1_prijmeni=Nováková")

    # --- calculation: prepinac2a drives s2a_kontrola_stavy_pozemky = If(prepinac2a = 1, 1, 0)
    before = text(doc, "//d:pomocne/d:s2a_kontrola_stavy_pozemky")
    resp, err = store(doc, page, "prepinac2a", "1")
    calc = js_call(resp, "field_changed_ok_callback")["calculated"]
    print("     calculated binds:", calc)
    after = text(doc, "//d:pomocne/d:s2a_kontrola_stavy_pozemky")
    check(before in ([None], ["0"]) and after == ["1"] and str(calc.get("s2a_kontrola_stavy_pozemky")) == "1",
          "calculate bind s2a_kontrola_stavy_pozemky: %s -> %s" % (before, after))
    check("moze_sa_tlacit__hlavicka" in calc, "dependent plain-XPath calculate reported (moze_sa_tlacit__hlavicka=%s)"
          % calc.get("moze_sa_tlacit__hlavicka"))
    resp, err = valid(api.handle(doc, "calculate", {"field": "prepinac2a", "repe": ""}), "calculate")
    recalc = js_call(resp, "recalculate_form_callback")
    check(not err and str(recalc.get("s2a_kontrola_stavy_pozemky")) == "1",
          "calculate handler: recalculate_form_callback reports %d binds" % len(recalc))

    # --- constraint: prepinac2a requires one of prepinac2a/2b/2c = 1 -> "0" is refused and rolled back
    resp, err = store(doc, page, "prepinac2a", "0")
    refused = js_call(resp, "field_changed_error_callback")
    check(refused["curr_val"] == "1" and text(doc, "//d:pomocne/d:prepinac2a") == ["1"]
          and text(doc, "//d:pomocne/d:s2a_kontrola_stavy_pozemky") == ["1"],
          "constraint violation: field_changed_error_callback, value and dependents rolled back")

    # --- repeating section add/del (opak1 = table repeating section)
    count = lambda: len(api.data_element(doc).xpath("/d:root/d:sekce1/d:opak1", namespaces=D))
    rs = page.xpath("//*[@name='opak1-rep-0']")[0]
    check(count() == 1, "opak1 count before add = 1")
    data = {"name": "opak1-rep-0", "rs_id": rs.get("id") or "opak1-rep-0", "repe": get_parent_repe(rs)}
    resp, err = valid(api.handle(doc, "add_section", data), "add_section %r" % data["repe"])
    added = js_call(resp, "server_add_section_callback")
    check(not err and added["repe_html"] and added["attach_after"] == data["rs_id"], "server_add_section_callback has template")
    check(count() == 2, "opak1 count after add = 2")
    # value into the new row: the page names it opak1-rep-1
    row_data = {"field": "o1_prijmeni", "value": "Druhý", "repe": "table---opak1-rep-1", "input_id": "input-x"}
    resp, err = valid(api.handle(doc, "store_val", row_data), "store_val into opak1-rep-1")
    js_call(resp, "field_changed_ok_callback")
    check(text(doc, "/d:root/d:sekce1/d:opak1/d:o1_prijmeni") == ["Nováková", "Druhý"], "second opak1 row stored")
    resp, err = valid(api.handle(doc, "get_repeatable", ""), "get_repeatable after add")
    check(len(js_call(resp, "calc_repeatable_sections_callback")["opak1"]) == 2, "get_repeatable reports 2 opak1 rows")
    resp, err = valid(api.handle(doc, "del_section", {"name": "opak1-rep-1", "repe": "table---opak1-rep-1"}),
                      "del_section")
    js_call(resp, "server_del_section_callback")
    check(count() == 1 and text(doc, "/d:root/d:sekce1/d:opak1/d:o1_prijmeni") == ["Nováková"],
          "opak1 count after del = 1, first row kept")

    # --- odd/GUI-only messages must not crash
    for mt, payload in [("get_input_info", {"id": "input-1", "name": "o1_prijmeni", "text": "", "ro": False}),
                        ("clear_input_info", ""),
                        ("select_file", {"field": "x", "filter": "", "repe": "", "input_id": "i"}),
                        ("store_to_file", ""), ("show_source", "<html/>"),
                        ("perform_click", {"command": "SendAppEvent(addsect,.,1)"}),
                        ("perform_click", {"command": page.xpath("//*[contains(@zfo-app-event, 'checkdata')]/@zfo-app-event")[0]}),
                        ("no_such_message", {}), ("_construct_web_response", "x"), ("json", "")]:
        resp, err = valid(api.handle(doc, mt, payload), "GUI/unknown message %s" % mt)
        check(not err, "%s: no error response" % mt)
        print("     ->", [(r["type"], r["data"] if r["type"] == "javascript" else r["data"]["to_display"]) for r in resp][:4])
    check(text(doc, "//d:tisk") == ["0"], "print button chain restored tisk=0")

    # --- pre-filled form from the real FormFiller
    ref = api.open_document(load_fo("samples/ref-filler/form.zfo"))
    check(text(ref, "//d:sekce3/d:s3_dalsi_sdeleni") == ["Test"], "ref-filler: s3_dalsi_sdeleni == 'Test' in data_element")
    resp, err = valid(api.handle(ref, "get_init_values", {"input-9": {"type": "text", "bind": "s3_dalsi_sdeleni"}}),
                      "ref-filler get_init_values")
    check(js_call(resp, "set_inputs_init_values_callback")["input-9"]["value"] == "Test", "ref-filler: input gets 'Test'")
    # the first document is unaffected by the second (separate data + xpath extensions)
    check(text(doc, "//d:sekce3/d:s3_dalsi_sdeleni") == [None], "first document still empty in s3_dalsi_sdeleni")
    store(doc, page, "prepinac2b", "1")
    resp, err = store(doc, page, "prepinac2a", "0")
    js_call(resp, "field_changed_ok_callback")
    check(text(doc, "//d:pomocne/d:s2a_kontrola_stavy_pozemky") == ["0"], "calculation still bound to first document")

    print("\nALL %d CHECKS PASSED" % len(CHECKS))


if __name__ == "__main__":
    main()
