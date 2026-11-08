"""602 JavaScript in forms: the scripts offices publish, zfoengine/js602.py, and the same expressions in Node.js."""
import json
import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from lxml import etree
from support import NS, ROOT, form_with_stylesheets, needs_node, run_in_node, stylesheet

import app
import zfo_verify
from zfoengine import api as engine
from zfoengine import form_scripts, js602

FORM_WITH_ATTRIBUTE = """<dsig:Signature xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" Id="_1"><dsig:Object Id="_2" MimeType="text/xml">
<fo:root xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:fm="http://software602.cz/forms" xmlns:d="http://software602.cz/sample">
<fm:formparams><fm:model><fm:instance><d:root><d:e a=""/></d:root></fm:instance><fm:bind Id="k" nodeset="/d:root"><fm:bind Id="e" nodeset="d:e">
<fm:bind Id="a" nodeset="@a" calculate='%s'/></fm:bind></fm:bind></fm:model></fm:formparams></fo:root></dsig:Object></dsig:Signature>"""

class FormScripts(unittest.TestCase):
    """samples/skripty/vypocty.zfo comes from tools/make-script-samples.py, shaped like what offices publish."""

    def setUp(self):
        self.doc = engine.open_document(zipfile.ZipFile(ROOT / "samples/skripty/vypocty.zfo").read("form.fo"))

    def value(self, name):
        return self.doc._get_data_root().xpath(f"/d:root/d:{name}", namespaces=NS)[0].text or ""

    def fill(self, name, value):
        return engine.handle(self.doc, "store_val", {"field": name, "value": value, "input_id": name, "repe": ""})

    def problems(self):
        return engine.handle(self.doc, "check_form", "")[0]

    def test_filled_check_follows_every_branch(self):
        engine.handle(self.doc, "recalc_form", "")
        self.assertEqual(self.value("vyplneno"), "1")
        self.fill("jmeno", "Jana")
        self.assertEqual(self.value("vyplneno"), "0")
        self.fill("tisk", "0")
        self.fill("jmeno", "")
        self.assertEqual(self.value("vyplneno"), "0")

    def test_no_result_leaves_the_value(self):
        self.fill("tisk", "2")
        self.fill("jmeno", "Jana")
        before = self.value("vyplneno")
        self.fill("prijmeni", "Nová")
        self.assertEqual(self.value("vyplneno"), before)

    def test_library_function_joins_fields(self):
        self.fill("jmeno", "Jana")
        self.fill("prijmeni", "Nová")
        self.assertEqual(self.value("cele_jmeno"), "Jana Nová")

    def test_birth_number_gives_a_date_and_a_later_calculation_uses_it(self):
        self.fill("rc", "855815/0007")
        self.assertEqual(self.value("narozen"), "1985-08-15")
        self.assertEqual(self.value("lhuta"), "14.9.1985")

    def test_invalid_birth_number_gives_nothing(self):
        self.fill("rc", "855815/0014")
        self.assertEqual(self.value("narozen"), "")

    def test_a_forms_own_regexp_function_and_the_constructor_coexist(self):
        self.fill("psc", "11000")
        self.assertEqual(self.value("psc_ok"), "true")
        self.fill("psc", "abc")
        self.assertEqual(self.value("psc_ok"), "false")

    def test_constraint_script_is_checked(self):
        self.assertIn("Neplatná hodnota", json.dumps(self.fill("email", "neni-adresa"), ensure_ascii=False))
        self.assertNotIn("Neplatná hodnota", json.dumps(self.fill("email", "jana@example.cz"), ensure_ascii=False))
        self.assertEqual(self.value("email"), "jana@example.cz")
        self.assertEqual(self.problems()["data"], [])

    def test_script_reaching_for_the_network_changes_nothing_and_is_reported(self):
        self.fill("jmeno", "Jana")
        self.assertEqual(self.value("odeslano"), "puvodni")
        self.assertTrue(self.problems()["scripts"])

    def test_failing_expression_leaves_no_helper_in_the_data(self):
        form = FORM_WITH_ATTRIBUTE % '{ Result(GetDocument().GetElement().DataXpath("(((")); }'
        doc = engine.open_document(form.encode())
        engine.handle(doc, "recalc_form", "")
        self.assertNotIn(b"tmp", etree.tostring(doc._get_data_root().getroot()))
        self.assertTrue(doc.script_problems)

    def test_scripts_get_nothing_of_the_application_through_xpath(self):
        for call in ('GetEnvVariable(\\"HOME\\")', "CurrentUserName()", 'SendAppEvent(\\"saveformsign\\")', 'SaveAs(\\"x\\")',
                     'SetAttribute(\\"a\\", \\"b\\")', 'GetListItemData(\\"x\\", concat(\\"Get\\", \\"EnvVariable()\\"), \\"@a\\")'):
            with self.subTest(call):
                form = FORM_WITH_ATTRIBUTE % ('{ Result(GetDocument().GetElement().DataXpath("%s")); }' % call)
                doc = engine.open_document(form.encode())
                engine.handle(doc, "recalc_form", "")
                self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "", call)
                self.assertFalse(doc.script_running)

    def test_form_description_reports_what_does_not_run(self):
        fo = etree.fromstring(zipfile.ZipFile(ROOT / "samples/skripty/vypocty.zfo").read("form.fo"))
        self.assertEqual(app.binds_with_unsupported_scripts(fo), ["odeslano"])
        self.assertEqual(app.count_never_run_scripts(fo), 0)

    def test_button_stores_a_value_to_a_field_saved_as_an_attribute(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "").encode())
        root = doc._get_data_root().getroot()
        root.set("form602bindref", "a")
        doc.perform_click_action("SetValueToData(vlozeno)")
        self.assertEqual(root[0].get("a"), "vlozeno")
        self.assertEqual(doc._get_computable_data_root().getroot()[0].get("a"), "vlozeno")

    def test_button_tells_the_page_whether_it_changed_the_data(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "").encode())

        def answer(command):
            return engine.handle(doc, "perform_click", {"command": command})[0]["data"]

        self.assertIn('{"changed": false}', answer("SetValueToData(vlozeno)"))
        doc._get_data_root().getroot().set("form602bindref", "a")
        self.assertIn('{"changed": true}', answer("SetValueToData(vlozeno)"))
        self.assertIn('{"changed": false}', answer("SetValueToData(vlozeno)"))

    def test_xpath_number_is_stored_as_xpath_writes_it(self):
        doc = engine.open_document((FORM_WITH_ATTRIBUTE % "1 + 1").encode())
        engine.handle(doc, "recalc_form", "")
        self.assertEqual(doc._get_data_root().getroot()[0].get("a"), "2")

    def test_cadastre_form_agrees_with_form_filler(self):
        # samples/ref-filler was saved by Form Filler: with tisk=0 all three checks are 0.
        fo = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
        doc = engine.open_document(fo)
        engine.handle(doc, "recalc_form", "")
        data = doc._get_data_root()
        for name in ("s2a_vyplnene", "s2a_vyplnene2", "s2b_vyplnene"):
            filled = {node.text for node in data.iter(f"{{{data.getroot().nsmap['d']}}}{name}")}
            self.assertEqual(filled, {"0"}, name)
        self.assertEqual(doc.script_problems, set())

    def test_saved_form_keeps_earlier_layers_untouched_by_scripts(self):
        fo = zipfile.ZipFile(ROOT / "samples/cuzk/form.zfo").read("form.fo")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "form.zfo"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("form.fo", fo)
            app.FORMS[path.stem] = path
            session = {"name": path.stem, "doc": engine.open_document(fo)}
            engine.handle(session["doc"], "recalc_form", "")
            saved = etree.fromstring(app.filled_form(session), etree.XMLParser(huge_tree=True))
            layer = etree.tostring(app.publisher_layer(saved).find("{http://www.w3.org/2000/09/xmldsig#}SignatureValue"))
            original = etree.fromstring(fo, etree.XMLParser(huge_tree=True))
            self.assertEqual(layer, etree.tostring(app.publisher_layer(original).find("{http://www.w3.org/2000/09/xmldsig#}SignatureValue")))
            self.assertEqual(zfo_verify.describe_signature_layers(saved)[0]["intact"], True)


REQUIRED_A = stylesheet("Kontrola", """<control><xsl:if test="js:isBlank(string(/d:root/d:a))"><item><text code="ID1">F001: Položka
 je povinná.</text><focus>/d:root[1]/d:a[1]</focus></item></xsl:if><item><text>F900: Formulář jako celek.</text><focus>/d:root[1]/d:neni[1]</focus></item>
</control>""", "function isBlank(value) { return value.replace(/ /g, '') == ''; }")
NO_FAULTS = stylesheet("Bez", "<control/>")
ADDRESS_PATHS = stylesheet("Seznam", "<seznam><xpath>/d:root/d:a</xpath></seznam>")


class FormStylesheetChecks(unittest.TestCase):
    """Forms of MPSV check their data with their own stylesheets, document.XSLCheck() in their scripts."""

    def click(self, script, value="", stylesheets=(REQUIRED_A, NO_FAULTS, ADDRESS_PATHS)):
        doc = engine.open_document(form_with_stylesheets(script, stylesheets, value))
        return form_scripts.run_button_script(doc, 0, "", [])

    def test_faults_come_with_their_field_and_their_words(self):
        result = self.click("if (!GetDocument().XSLCheck('Kontrola')) MessageBox('chyby');")
        self.assertEqual(result["problems"], [{"field": "a", "index": 0, "kind": "form", "message": "F001: Položka je povinná."},
                                              {"field": None, "index": 0, "kind": "form", "message": "F900: Formulář jako celek."}])
        self.assertEqual(result["messages"], [{"text": "chyby", "title": ""}])

    def test_check_without_faults_passes(self):
        result = self.click("if (GetDocument().XSLCheck('Bez')) MessageBox('v poradku');", value="vyplneno")
        self.assertEqual((result["problems"], result["messages"]), ([], [{"text": "v poradku", "title": ""}]))

    def test_noclean_adds_to_the_earlier_check_and_a_plain_check_starts_anew(self):
        twice = self.click("var d = GetDocument(); d.XSLCheck('Kontrola'); d.XSLCheck('Kontrola', 'noclean');")
        self.assertEqual(len(twice["problems"]), 4)
        cleaned = self.click("var d = GetDocument(); d.XSLCheck('Kontrola'); d.XSLCheck('Bez');")
        self.assertEqual(cleaned["problems"], [])

    def test_form_data_of_a_stylesheet_reaches_the_script(self):
        result = self.click("var p = new DataParam(); p.xslName = 'Seznam'; var r = GetDocument().GetFormData(p);"
                            "GetDocument().GetElement().SetDataTo('a', /<xpath>([^<]*)<\\/xpath>/.exec(r.data)[1]);")
        self.assertEqual(result["values"], {"a": "/d:root/d:a"})

    def test_address_lists_of_both_generations_are_empty_with_a_notice(self):
        for name in ("XsltGetAdresy", "Validace_AdresaWS_XpathAdres"):
            with self.subTest(name):
                result = self.click(f"var p = new DataParam(); p.xslName = '{name}';"
                                    "if (GetDocument().GetFormData(p).data == '') GetDocument().GetElement().SetDataTo('a', 'prazdny');",
                                    stylesheets=(stylesheet(name, "<x><xpath>/d:root/d:a</xpath></x>"),))
                self.assertEqual(result["values"], {"a": "prazdny"})
                self.assertEqual(result["notices"], [form_scripts.ADDRESS_NOT_CHECKED_NOTICE])

    def test_unknown_stylesheet_is_refused(self):
        self.assertEqual(self.click("var p = new DataParam(); p.xslName = 'Neni'; GetDocument().GetFormData(p);")["refused"], "GetFormData")
        self.assertIn("refused", self.click("GetDocument().XSLCheck('Neni');"))


class Interpreter(unittest.TestCase):
    """What matters about zfoengine/js602.py that a form cannot show."""

    def run_script(self, source, answer="", library=()):
        return js602.evaluate(js602.Interpreter(library), source, lambda expression: answer, "/d:root/d:x")

    def test_python_stays_out_of_reach(self):
        for source in ('Result("".__class__)', 'Result([].__class__)', 'Result(Result.__globals__)', 'Result(GetDocument.__self__)',
                       'Result((function(){}).__code__)', 'Result("".constructor)', 'Result(({}).__proto__)', 'Result(new Date().__class__)'):
            with self.subTest(source):
                self.assertEqual(self.run_script("{" + source + "}"), "undefined")

    def test_host_objects_offer_only_what_they_list(self):
        for source in ("GetDocument().SendData()", "GetDocument().Print2PDF()", "GetDocument().__class__", "GetDocument().GetElement().SetData('a', 'b')",
                       "GetDocument().GetElement().props", "GetDocument().x = 1"):
            with self.subTest(source), self.assertRaises(js602.Unsupported):
                self.run_script("{" + source + "}")

    def test_unknown_names_stop_the_script(self):
        for source in ("open('/etc/passwd')", "require('fs')", "eval('1')", "Function('return 1')()", "MessageBox('x')", "new ActiveXObject('x')",
                       "__import__('os')", "print(1)"):
            with self.subTest(source), self.assertRaises(js602.Unsupported):
                self.run_script("{" + source + "}")

    def test_runaway_scripts_are_stopped(self):
        for source in ("while (true) {}", "for (;;) { var a = 1; }", "function f() { f(); } f();", "var a = 'x'; while (true) a += a;",
                       "var a = []; while (true) a.push(1);", "var a = []; a[1000000000] = 1;", "/(a+)+$/.test('" + "a" * 40 + "!');",
                       "/(a|a)*$/.test('" + "a" * 40 + "!');", "/(.*)*x/.test('" + "a" * 3000 + "');", "'abc'.repeat(1e9);", "new Array(1e9);"):
            with self.subTest(source), self.assertRaises((js602.Exhausted, js602.Thrown)):
                self.run_script("{" + source + "}")

    def test_deep_assignments_compile_at_once(self):
        for chain in ("a[" * 60 + "0" + "]=0" * 60, "++a[" * 60 + "0" + "]" * 60, "a[" * 60 + "0" + "]++" * 60, "delete a[" * 50 + "0" + "]" * 50):
            with self.subTest(chain[:12]):
                started = time.monotonic()
                try:
                    self.run_script("{ var a = []; " + chain + "; }")
                except (js602.Unsupported, js602.Thrown, js602.Exhausted):
                    pass
                self.assertLess(time.monotonic() - started, 5)

    def test_big_texts_are_paid_for_by_size(self):
        for source in ('var s = "x".repeat(3000000), b = []; while (true) b.push(s + "y");',
                       'var s = "x".repeat(3000000); while (true) s.toUpperCase();',
                       'var s = "x".repeat(3000000); while (true) s.indexOf("q");',
                       'var s = "x".repeat(3000000); var a = [s]; while (true) a.join();'):
            with self.subTest(source[-25:]), self.assertRaises(js602.Exhausted):
                self.run_script("{" + source + "}")

    def test_one_scripts_variables_do_not_reach_another(self):
        interpreter = js602.Interpreter()
        js602.evaluate(interpreter, '{ var a = "zustava"; }', lambda expression: "", "")
        self.assertEqual(js602.evaluate(interpreter, '{ var a; Result(a + ""); }', lambda expression: "", ""), "undefined")

    def test_unreadable_scripts_are_unsupported(self):
        for source in ("{ var }", "{ if ( }", "{ /(/.test('') }", "{ 'abc }", "{" * 500, "{ a" + "[0" * 500, "{" + "(" * 500, "{ /" + "(" * 200 + "/ }", "\x00", "{ @ }"):
            with self.subTest(source[:20]), self.assertRaises(js602.Unsupported):
                self.run_script(source)

    def test_broken_library_script_does_not_stop_the_others(self):
        self.assertEqual(self.run_script("{ Result(dobra()); }", library=("function ( {", "function dobra() { return 'ano'; }")), "ano")

    def test_is_script_tells_xpath_from_javascript(self):
        for value in ("{ var a = 1; }", "  { Result(1); }", "{GetDocument()}", "{\nfunction f() {}\n}"):
            self.assertTrue(js602.is_script(value), value)
        for value in ("{../d:a}", "../d:a + 1", 'If(DataXpath({../d:a}), 1, 2)', "", "{Result}", "{../d:function}", "{d:var x}", "{../d:return}"):
            self.assertFalse(js602.is_script(value), value)


@needs_node
class InterpreterAgainstNode(unittest.TestCase):
    """Our own expressions in both engines: what the language means is not this project's to decide."""

    CASES = r"""
    1 + 2; "1" + 2; 1 + "2"; "3" * "4"; "a" * 1; 7 % 3; -7 % 3; 5.5 % 2; 1 / 0; -1 / 0; 0 / 0; 0.1 + 0.2; 1e21; 1e-7; 123456789012345680000; 1.5e300 * 1e300;
    "" == 0; "0" == 0; null == undefined; null == 0; undefined == 0; "1" == 1; true == 1; true == "1"; [] == ""; [1] == 1; "a" === "a"; 1 === 1.0; NaN == NaN; null === null;
    "a" < "b"; "10" < "9"; "10" < 9; null >= 0; undefined >= 0; NaN < 1; "abc" < "abd";
    !""; !"0"; !0; !null; ![]; !!{}; "" || "x"; "a" && "b"; 0 || null; 1 && 0;
    1 & 3; 1 | 2; 5 ^ 1; ~5; 1 << 3; -8 >> 1; -8 >>> 28; 2147483648 | 0; 1.9 | 0;
    typeof 1; typeof "a"; typeof null; typeof undefined; typeof []; typeof {}; typeof function(){}; typeof neni;
    parseInt("12px"); parseInt("08"); parseInt("0x1f"); parseInt("z"); parseInt("101", 2); parseInt(""); parseInt("-3.9"); parseFloat("3.14abc"); parseFloat(".5"); parseFloat("x"); Number(""); Number(" 12 "); Number("1e3"); Number("12px"); Number(null); Number(true); Number("0x10");
    isNaN("a"); isNaN("1"); isFinite("1"); isFinite(1/0); String(1); String(null); String(undefined); String(true); String([1,2]); Boolean(""); Boolean("0");
    (1.005).toFixed(2); (2.5).toFixed(0); (1.45).toFixed(1); (0).toFixed(2); (1234.5678).toFixed(1); (255).toString(16); (10).toString(2);
    Math.floor(-1.5); Math.ceil(-1.5); Math.round(2.5); Math.round(-2.5); Math.trunc(-1.5); Math.abs(-1); Math.max(1, 2); Math.min(); Math.max(1, "a"); Math.pow(2, 10);
    "abc".length; "abc".charAt(1); "abc".charAt(9); "abc"[1]; "abc".charCodeAt(0); "abcabc".indexOf("c"); "abcabc".indexOf("c", 3); "abcabc".lastIndexOf("a"); "abcabc".lastIndexOf("a", 2); "abc".indexOf("");
    "abcdef".substring(1, 3); "abcdef".substring(3, 1); "abcdef".substring(-1, 2); "abcdef".substring(4); "abcdef".substr(1, 2); "abcdef".substr(-2); "abcdef".slice(1, -1); "abcdef".slice(-2); "abcdef".slice(3, 1);
    "AbC".toLowerCase(); "AbC".toUpperCase(); "  a ".trim(); "a,b,,c".split(","); "abc".split(""); "a1b2c".split(/[0-9]/); "a1b".split(/([0-9])/); "abc".split(); "".split(","); "a,b".split(",", 1); "a".concat("b", 1);
    "aXbX".replace("X", "-"); "aXbX".replace(/X/, "-"); "aXbX".replace(/X/g, "-"); "abc".replace(/(a)(b)/, "$2$1"); "abc".replace(/b/, "[$&]"); "a.b".replace(/\./g, ""); "abc".replace(/b/, function(m) { return m + m; }); "$".replace("$", "$$");
    "abc".match(/b/)[0]; "abab".match(/a/g).length; "abc".match(/x/); "abc".search(/c/); /a/.test("cat"); /^a/.test("cat"); /t$/.test("cat"); /A/i.test("cat"); /^\d+$/.test("123"); /^\d+$/.test("12a"); /\bcat\b/.test("a cat b"); /^\w+$/.test("čau"); /\s/.test("a b");
    /^[0-9]{6}\/[0-9]{3,4}$/.test("123456/7890"); /^[1-7][0-9]{4}$/.test("11000"); /^(\-|\+)?([0-9]+|Infinity)$/.test("-5"); /(a)|(b)/.exec("b")[2]; /(a)|(b)/.exec("b")[1]; /a{2}/.test("aa"); /a{2,}/.test("a"); /a{,2}/.test("a{,2}"); /[A-z]/.test("_"); /[^0-9]/.test("5"); /a.c/.test("a\nc"); /a(?=b)/.test("ab"); /a(?!b)/.test("ab"); /(a)\1/.test("aa"); /a*?b/.exec("aab")[0]; /\//.test("/"); /[\/]/.test("/"); /[a\-z]/.test("-");
    (function() { var r = /a/g; r.test("aa"); return r.lastIndex; })(); (function() { var r = /a/g; var n = 0; while (r.exec("aXa")) n++; return n; })(); new RegExp("^[0-9]+$").test("12"); new RegExp("a", "i").test("A");
    [1, 2, 3].length; [1, 2, 3].join("-"); [1, [2, 3]].join(); [null, undefined, 1].join(); [3, 1, 2].sort().join(); [10, 9, 1].sort().join(); [3, 1].sort(function(a, b) { return a - b; }).join(); [1, 2].concat(3, [4]).join(); [1, 2, 3].slice(1).join(); [1, 2, 3].indexOf(2); [1, 2, 3].reverse().join(); [1, 2, 3].pop(); [1, 2, 3].shift(); [1, 2, 3].map(function(x) { return x * 2; }).join(); [1, 2, 3].filter(function(x) { return x > 1; }).length;
    (function() { var a = [1, 2, 3]; a.splice(1, 1); return a.join(); })(); (function() { var a = []; a[3] = 1; return a.length; })(); (function() { var a = [1]; a.push(2, 3); return a.length; })(); (function() { var a = [1, 2]; a.length = 1; return a.join(); })(); Array(3).length; new Array(1, 2).length; Array.isArray([]);
    ({a: 1}).a; ({a: 1}).b; ({"x y": 2})["x y"]; (function() { var o = {}; o.a = 1; o["b"] = 2; var s = ""; for (var k in o) s += k; return s; })(); (function() { var s = ""; for (var i in [5, 6]) s += i; return s; })(); ("a" in {a: 1}); JSON.stringify({a: [1, "x", null, true]}); JSON.parse('{"a": [1, {"b": 2}]}').a[1].b;
    (function() { var d = new Date(2024, 0, 31); d.setMonth(1); return d.getMonth() + "/" + d.getDate(); })(); new Date(2024, 1, 29).getDate(); new Date(2023, 1, 29).getDate(); new Date(2024, 11, 31).getDay(); new Date(99, 0, 1).getFullYear(); (function() { var d = new Date(2024, 0, 1); d.setDate(d.getDate() - 1); return d.getFullYear() + "-" + d.getMonth() + "-" + d.getDate(); })(); new Date("x").getTime(); new Date("2024-02-29").getDate();
    (function() { var i = 0, s = ""; do { s += i; i++; } while (i < 3); return s; })(); (function() { var s = ""; for (var i = 0; i < 5; i++) { if (i == 1) continue; if (i == 3) break; s += i; } return s; })(); (function() { switch ("b") { case "a": return 1; case "b": case "c": return 2; default: return 3; } })(); (function() { var s = ""; switch (2) { case 1: s += "1"; case 2: s += "2"; case 3: s += "3"; break; default: s += "d"; } return s; })(); (function() { switch (9) { default: return "d"; case 1: return "1"; } })();
    (function() { var f = function(n) { return n < 2 ? 1 : n * f(n - 1); }; return f(5); })(); (function(a, b) { return arguments.length + "" + b; })(1); (function() { return typeof x; var x = 1; })(); (function() { return g(); function g() { return "hoisted"; } })(); (function() { var c = 0; function add() { c++; } add(); add(); return c; })(); (function() { var o = {v: 7, f: function() { return this.v; }}; return o.f(); })();
    (function() { try { throw "x"; } catch (e) { return e + "!"; } })(); (function() { try { null.a; } catch (e) { return "caught"; } })(); (function() { var s = ""; try { s += "t"; } finally { s += "f"; } return s; })();
    (function() { var a = 1; a += 2; a *= 3; a -= 1; a /= 2; a %= 3; return a; })(); (function() { var i = 1; return i++ + ++i; })(); (function() { var s = "a"; s += 1; return s; })(); (function() { var o = {n: 1}; o.n++; o["n"] += 1; return o.n; })(); (1, 2); void 0; true ? "y" : "n"; "" ? "y" : "n";
    """

    @staticmethod
    def cases():
        # the list above is split by a small reader of our own text, not of a form's: it only has to know quotes and brackets
        found, depth, start, quote, at = [], 0, 0, None, 0
        text = InterpreterAgainstNode.CASES
        while at < len(text):
            c = text[at]
            if quote:
                if c == "\\":
                    at += 1
                elif c == quote or (quote == "/" and c == "\n"):
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == "/" and text[at - 1] in " (,=!" and text[at + 1] not in " ":
                quote = "/"
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == ";" and depth == 0:
                found.append(text[start:at].strip())
                start = at + 1
            at += 1
        return [case for case in found if case]

    def test_same_answers(self):
        cases = self.cases()
        self.assertGreater(len(cases), 200)
        show = "function s(v){return Array.isArray(v)?'['+v.map(s).join('|')+']':String(v)}"
        lines = "".join(f"try{{out.push(s(eval({json.dumps('(' + case + ')')})))}}catch(e){{out.push('!'+e.name)}}\n" for case in cases)
        script = f"var out=[];{show}\n{lines}console.log(JSON.stringify(out))"
        expected = json.loads(run_in_node(script, env={**os.environ, "TZ": "UTC"}))

        def shown(value):
            if isinstance(value, js602.ScriptArray):
                return "[" + "|".join(shown(item) for item in value.items) + "]"
            return js602.to_string(value)

        interpreter = js602.Interpreter()
        wrong = []
        for case, wanted in zip(cases, expected, strict=True):
            box = []
            try:
                keep = js602.NativeFunction(lambda this, args, kept=box: kept.append(args[0]))
                interpreter.run(f"__keep(({case}));", {"__keep": keep})
                got = shown(box[0])
            except js602.Thrown as error:
                got = "!" + js602.to_string(error.value.props["name"])
            if got != wanted:
                wrong.append(f"{case}: node {wanted!r}, here {got!r}")
        self.assertEqual(wrong, [])
