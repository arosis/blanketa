"""Code of the pages in ui/ run in Node.js on the answers the engine and the hosts give."""
import json
import re
import unittest

from support import needs_node, run_in_node, run_ui_scripts, ui_source

import desktop


@needs_node
class SignatureReplacementNotice(unittest.TestCase):
    """Runs the button answers of ui/shell.html: a change of the data shows that the data signature will be replaced."""
    SCRIPT = """
    const window = globalThis;
    let changed = false, ready = true, notices = 0;
    const renderSignatures = () => notices++;
    const queue = Promise.resolve(), document = {getElementsByName: () => []}, calls = [];
    for (const name of ['gtk_handler_start', 'gtk_handler_finished', 'send', 'set_inputs_init_values', 'redrawSections',
                        'calc_repeatable_sections', 'set_readonly_inputs', 'set_multiswitch_controls', 'setStatus', 'alert',
                        'focusField']) window[name] = () => calls.push(name);
    const showCheck = (problems, scripts = []) => calls.push(scripts.length ? 'showCheck saying it is not complete' : 'showCheck');
    const clearCheck = ({keepStatus}) => calls.push(keepStatus ? 'clearCheck keeping the status' : 'clearCheck');
    const plainTextOf = text => text;
    %s
    eval(process.argv[1]);
    queue.then(() => console.log(JSON.stringify({notices, calls})));
    """
    ANSWER = {"values": {}, "changed": False, "messages": [], "notices": [], "focus": None, "problems": []}

    def page_after(self, call):
        page = ui_source("shell.js")
        parts = [re.search(pattern, page, re.S).group(0) for pattern in (
            r"const noteChange = .*?\n  \};", r"window\.perform_click_action_callback = .*?\n  \};",
            r"window\.form_script_callback = .*?\n  \};", r"window\.data_changed_callback = .*?;\n")]
        script = self.SCRIPT % "\n".join(parts)
        return json.loads(run_in_node(script, call))

    def notices_after(self, call):
        return self.page_after(call)["notices"]

    def test_xpath_button_that_changed_the_data(self):
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: true})'), 1)
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: false})'), 0)

    def test_script_button_that_changed_the_data(self):
        changed = {**self.ANSWER, "changed": True}
        self.assertEqual(self.notices_after(f"form_script_callback({json.dumps(changed)})"), 1)
        self.assertEqual(self.notices_after(f"form_script_callback({json.dumps(self.ANSWER)})"), 0)

    def test_notice_is_drawn_once(self):
        self.assertEqual(self.notices_after('perform_click_action_callback({changed: true}); perform_click_action_callback({changed: true})'), 1)

    def test_engine_answer_that_the_data_changed(self):
        self.assertEqual(self.notices_after('data_changed_callback()'), 1)

    def test_a_check_that_found_nothing_clears_the_earlier_one(self):
        calls = lambda answer: self.page_after(f"form_script_callback({json.dumps({**self.ANSWER, **answer})})")["calls"]
        self.assertIn("clearCheck", calls({}))
        self.assertIn("clearCheck keeping the status", calls({"notices": ["Adresy nebyly ověřeny."]}))
        self.assertFalse({"clearCheck", "showCheck"} & set(calls({"problems": None})))
        fault = [{"field": "a", "index": 0, "kind": "form", "message": "F001"}]
        self.assertIn("showCheck", calls({"problems": fault}))
        self.assertIn("showCheck saying it is not complete", calls({"problems": fault, "scripts": ["podmínka"]}))

    def test_script_answer_refreshes_every_copy_of_the_sections(self):
        # result.values names a field only by its bind; the values of the second and further copies come with the refresh.
        self.assertIn("calc_repeatable_sections", self.page_after(f"form_script_callback({json.dumps(self.ANSWER)})")["calls"])


@needs_node
class SwitchedPartsOnThePage(unittest.TestCase):
    """Runs the answer that shows and hides the parts of a switch on ui/shell.html: the engine names the parts to show."""
    SCRIPT = """
    const window = globalThis;
    let finished = 0;
    const gtk_handler_finished = () => finished++;
    const parts = Object.fromEntries(['', '2', '3'].map(value => [`div-krok-${value}`, {tagName: 'DIV', style: {display: ''}}]));
    const elementsNamed = name => parts[name] ? [parts[name]] : [];
    const $ = part => ({show: () => { part.style.display = ''; }, hide: () => { part.style.display = 'none'; }});
    %s
    window.set_multiswitch_controls_callback({data: [{bindref: 'krok', cases: ['', '2', '3'], shown: JSON.parse(process.argv[1])}]});
    console.log(JSON.stringify({finished, shown: Object.keys(parts).filter(name => parts[name].style.display !== 'none')}));
    """

    def shown_after(self, shown):
        answer = re.search(r"window\.set_multiswitch_controls_callback = .*?\n  \};", ui_source("shell.js"), re.S).group(0)
        return json.loads(run_in_node(self.SCRIPT % answer, json.dumps(shown)))

    def test_only_the_parts_the_engine_names_are_shown(self):
        for shown, parts in ((["2"], ["div-krok-2"]), ([], []), ([""], ["div-krok-"]), (["", "2", "3"], ["div-krok-", "div-krok-2", "div-krok-3"])):
            with self.subTest(shown):
                self.assertEqual(self.shown_after(shown), {"finished": 1, "shown": parts})


@needs_node
class FieldLabelsOnThePage(unittest.TestCase):
    """Runs the field labels of ui/shell.js that head the help and the check: the text the user sees at the field."""
    SCRIPT = """
    const FIELD = null;
    const row = (...texts) => {
      const element = {cells: []};
      element.cells = texts.map(text => ({textContent: text ?? '', querySelector: () => text === FIELD ? {} : null, parentElement: element}));
      element.cells.forEach((cell, index) => {
        cell.previousElementSibling = element.cells[index - 1] ?? null;
        cell.nextElementSibling = element.cells[index + 1] ?? null;
      });
      return element;
    };
    const table = rows => { rows.forEach((element, index) => { element.previousElementSibling = rows[index - 1] ?? null; }); return rows; };
    const field = (type, cell) => ({type, name: 'Data.Zadatel.ZadostPodava.ZadostPodava', closest: () => cell});
    %s
    const [heading, boxes] = table([row('Žádost podává', '', ''), row('', '', 'zástupce žadatele')]);
    const [left] = table([row('Požaduji', '', 'nepožaduji uvést symbol')]);
    const [units] = table([row('Částka', '', 'Kč')]);
    const [titled, bare] = table([row('', 'Datum narození'), row('', '')]);
    const [alone] = table([row('', '')]);
    const [pair] = table([row(FIELD, '', FIELD, 'ne')]);
    console.log(JSON.stringify([
      fieldHeading(field('checkbox', boxes.cells[1])), fieldHeading(field('checkbox', left.cells[1])),
      fieldHeading(field('radio', left.cells[1])), fieldHeading(field('text', units.cells[1])), fieldHeading(field('text', bare.cells[1])),
      fieldHeading(field('checkbox', alone.cells[1])), fieldHeading(field('text', null)), labelFor(field('text', null)),
      fieldHeading(field('checkbox', pair.cells[0])), shortOneLine('a'.repeat(100)).length, shortOneLine('  krátký\\n popisek '),
    ]));
    """

    def labels(self):
        code = re.search(r"  const UNLABELLED_FIELD = .*?\n  const fieldHeading = .*?;\n", ui_source("shell.js"), re.S).group(0)
        return json.loads(run_in_node(self.SCRIPT % code))

    def test_a_check_box_is_labelled_by_the_text_to_its_right(self):
        self.assertEqual(self.labels()[:3], ["zástupce žadatele", "nepožaduji uvést symbol", "nepožaduji uvést symbol"])

    def test_other_fields_are_labelled_from_the_left_then_from_above(self):
        self.assertEqual(self.labels()[3:5], ["Částka", "Datum narození"])

    def test_a_field_without_any_label_never_shows_its_internal_name(self):
        self.assertEqual(self.labels()[5:8], ["Pole bez popisku", "Pole bez popisku", ""])

    def test_the_label_of_another_field_is_not_taken(self):
        self.assertEqual(self.labels()[8], "Pole bez popisku")

    def test_a_long_label_is_shortened_with_an_ellipsis(self):
        self.assertEqual(self.labels()[9:], [90, "krátký popisek"])


@needs_node
class CheckBoxGroupsOnThePage(unittest.TestCase):
    """Runs the check boxes of ui/shell.html: boxes of one bind with their own values make one choice in each copy."""
    SCRIPT = """
    const window = globalThis;
    const SECTION_COPY = '.copy', sent = [];
    const box = (name, value, copy, off) => ({type: 'checkbox', name, value, id: name, checked: false, dataset: off === undefined ? {} : {off},
                                             closest: () => copy});
    const boxes = [box('podava', '1', null), box('podava', '2', null), box('podava', '3', null), box('podava', '1', 'kopie'),
                   box('souhlas', 'ano', null, 'ne')];
    const elementsNamed = name => boxes.filter(element => element.name === name);
    const $ = element => [element];
    const gtk_handler_start = () => {}, get_parent_repe = () => '';
    window.field_changed_handler = () => sent.push('other field');
    const send = message => sent.push(JSON.parse(message).data.value);
    %s
    eval(process.argv[1]);
    console.log(JSON.stringify({checked: boxes.map(element => element.checked), sent}));
    """

    def page_after(self, call):
        source = ui_source("shell.js")
        code = re.search(r"  const checkboxGroupOf = .*?\n  \};", source, re.S).group(0)
        return json.loads(run_in_node(self.SCRIPT % code, call))

    def test_the_value_of_the_field_checks_its_box_in_that_copy_only(self):
        self.assertEqual(self.page_after("set_checkbox_value(boxes[1], '2')")["checked"], [False, True, False, False, False])
        self.assertEqual(self.page_after("set_checkbox_value(boxes[0], 'off')")["checked"], [False] * 5)

    def test_checking_a_box_writes_its_value_and_unchecks_the_others(self):
        after = self.page_after("boxes[0].checked = true; boxes[1].checked = true; field_changed_handler(boxes[1])")
        self.assertEqual(after, {"checked": [False, True, False, False, False], "sent": ["2"]})

    def test_unchecking_a_box_writes_its_off_value_or_nothing(self):
        self.assertEqual(self.page_after("field_changed_handler(boxes[1])")["sent"], [""])
        self.assertEqual(self.page_after("field_changed_handler(boxes[4])")["sent"], ["ne"])

    def test_other_fields_change_as_before(self):
        self.assertEqual(self.page_after("field_changed_handler({type: 'text'})")["sent"], ["other field"])


@needs_node
class RegisterLookupOnThePage(unittest.TestCase):
    """Runs the lookup handling of ui/shell.js: the user confirms, the server asks the register, the script runs again."""
    SCRIPT = """
    const SESSION = 's1', sent = [], statuses = [], alerts = [], asked = [];
    const setStatus = text => statuses.push(text), alert = text => alerts.push(text), send = message => sent.push(JSON.parse(message));
    const [confirmed, server] = JSON.parse(process.argv[1]);
    const confirm = () => confirmed;
    const fetch = async (address, request) => { asked.push([address, JSON.parse(request.body)]);
      if (server === 'down') throw new Error('offline'); return {ok: server !== 'refused', json: async () => server}; };
    %s
    askRegister({url: 'https://ares.gov.cz/x/00006947', question: 'Poslat?', retry: {field: 'ico', repe: '', answers: [], lookups: {}}})
      .then(() => console.log(JSON.stringify({sent, statuses, alerts, asked})));
    """

    def run_page(self, confirmed, server):
        page = ui_source("shell.js")
        part = re.search(r"  const registerUnreachable = .*?\n  const askRegister = .*?\n  \};", page, re.S).group(0)
        return json.loads(run_in_node(self.SCRIPT % part, json.dumps([confirmed, server])))

    def test_confirmed_lookup_runs_the_script_again_with_the_answer(self):
        page = self.run_page(True, {"status": 200, "body": "{}"})
        self.assertEqual(page["asked"], [["/lookup?s=s1", {"url": "https://ares.gov.cz/x/00006947"}]])
        self.assertEqual(page["sent"], [{"type": "run_form_script", "data": {"field": "ico", "repe": "", "answers": [],
                                         "lookups": {"https://ares.gov.cz/x/00006947": {"status": 200, "body": "{}"}}}}])

    def test_declined_lookup_asks_nothing(self):
        page = self.run_page(False, None)
        self.assertEqual((page["asked"], page["sent"]), ([], []))
        self.assertIn("nepotvrdili", page["statuses"][-1])

    def test_unreachable_register_says_so_and_runs_nothing(self):
        for server in ("down", "refused", {"error": "Registr ARES teď není dostupný."}):
            with self.subTest(server=server):
                page = self.run_page(True, server)
                self.assertEqual(page["sent"], [])
                self.assertEqual(len(page["alerts"]), 1)


@needs_node
class ValueOutsideACodeList(unittest.TestCase):
    """Runs keepingValueOutsideTheList of ui/shell.js: a field that became a list keeps a text typed in before."""
    SCRIPT = """
    class Option { constructor(text, value) { this.text = text; this.value = value; } }
    const select = value => ({tagName: 'SELECT', options: [new Option('', ''), new Option('Česká republika', 'CZ')],
                              append(option) { this.options.push(option); }});
    %s
    console.log(JSON.stringify(JSON.parse(process.argv[1]).map(value => keepingValueOutsideTheList(select(), value).options.map(option => option.value))));
    """

    def test_only_a_value_the_list_lacks_is_added(self):
        part = re.search(r"  const keepingValueOutsideTheList = .*?\n  \};", ui_source("shell.js"), re.S).group(0)
        lists = json.loads(run_in_node(self.SCRIPT % part, json.dumps(["CZ", "Česká republika", "", "cz"])))
        self.assertEqual(lists, [["", "CZ"], ["", "CZ"], ["", "CZ"], ["", "CZ", "cz"]])


@needs_node
class RadioButtonsInSectionCopies(unittest.TestCase):
    """Runs the radio button handling of ui/shell.html on a page with a group outside sections and two copies of a section."""
    SCRIPT = """
    const window = globalThis, sent = [];
    const formsById = new Map();
    const document = {body: {append() {}}, createElement: tag => tag === 'div'
      ? {children: [], append(form) { this.children.push(form); formsById.set(form.id, form); }, get childElementCount() { return this.children.length; }}
      : {id: ''}};
    const copies = [{name: 'sekce-rep-0'}, {name: 'sekce-rep-1'}];
    const radio = (name, value, copy) => ({type: 'radio', name, value, checked: false, copy, id: `${name}-${value}`, attributes: {},
      closest: () => copy, getAttribute(key) { return this.attributes[key] ?? null; }, setAttribute(key, text) { this.attributes[key] = text; },
      get form() { return formsById.get(this.attributes.form) ?? null; }});
    const radios = [radio('mimo', 'x', null), radio('mimo', 'y', null), ...copies.flatMap(copy => ['a', 'b'].map(value => radio('r', value, copy)))];
    const page = {querySelectorAll: () => radios};
    const elementsNamed = name => radios.filter(candidate => candidate.name === name);
    const $ = element => element?.wrapped ? element : {wrapped: element, hasClass: () => false};
    const is_array = Array.isArray;
    const get_html_elem = (parent, name) => copies.find(copy => copy.name === name)
      ?? radios.find(candidate => candidate.name === name && candidate.copy === (parent?.wrapped ?? null));
    const set_input_value = () => { throw new Error('a radio button reached set_input_value'); };
    const gtk_handler_start = () => {}, send = message => sent.push(JSON.parse(message)), setStatus = () => {};
    const shortOneLine = text => text, labelFor = () => '', get_parent_repe = field => field.copy ? `block---${field.copy.name}` : '';
    let lastField = null;
    %s
    eval(process.argv[1]);
    console.log(JSON.stringify({checked: radios.filter(candidate => candidate.checked).map(candidate => (candidate.copy?.name ?? 'mimo') + ':' + candidate.value),
                                forms: radios.map(candidate => candidate.getAttribute('form')), sent}));
    """

    def page_after(self, call):
        page = ui_source("shell.js")
        parts = [re.search(pattern, page, re.S).group(0) for pattern in (
            r"  const SECTION_COPY = .*?window\.fill_inputs = .*?\n  \};", r"  function applyDefault\(.*?\n  \}")]
        script = self.SCRIPT % "\n".join(parts)
        return json.loads(run_in_node(script, call))

    def test_each_copy_shows_its_own_value(self):
        page = self.page_after("fill_inputs(null, {mimo: 'y', sekce: [{r: 'b'}, {r: 'a'}]})")
        self.assertEqual(page["checked"], ["mimo:y", "sekce-rep-0:b", "sekce-rep-1:a"])

    def test_empty_value_leaves_no_button_of_the_copy_chosen(self):
        page = self.page_after("fill_inputs(null, {sekce: [{r: 'a'}, {r: 'a'}]}); fill_inputs(null, {sekce: [{r: 'a'}, {r: ''}]})")
        self.assertEqual(page["checked"], ["sekce-rep-0:a"])

    def test_each_copy_is_a_group_of_its_own(self):
        forms = self.page_after("fill_inputs(null, {})")["forms"]
        self.assertEqual(forms[:2], [None, None])
        self.assertEqual(forms[2], forms[3])
        self.assertEqual(forms[4], forms[5])
        self.assertNotEqual(forms[2], forms[4])

    def test_copy_cloned_with_the_group_of_its_original_gets_its_own(self):
        clone = ("fill_inputs(null, {}); copies[1] = {name: 'sekce-rep-1'}; radios.slice(4).forEach(button => { button.copy = copies[1]; button.setAttribute('form', radios[2].getAttribute('form')); }); "
                 "fill_inputs(null, {})")
        forms = self.page_after(clone)["forms"]
        self.assertNotEqual(forms[2], forms[4])

    def test_value_by_name_alone_goes_to_the_first_group_and_clears_its_choice(self):
        page = self.page_after("fill_inputs(null, {mimo: 'x', sekce: [{r: 'a'}, {r: 'a'}]}); set_radio_value('r', 'b'); set_radio_value('mimo', '')")
        self.assertEqual(page["checked"], ["sekce-rep-0:b", "sekce-rep-1:a"])

    def test_default_value_is_stored_for_the_copy_of_the_field(self):
        page = self.page_after("fill_inputs(null, {sekce: [{r: 'b'}, {r: 'b'}]}); lastField = radios[5]; applyDefault({field: 'r', value: 'a'})")
        self.assertEqual(page["checked"], ["sekce-rep-0:b", "sekce-rep-1:a"])
        self.assertEqual(page["sent"], [{"type": "store_val", "data": {"field": "r", "value": "a", "repe": "block---sekce-rep-1", "input_id": "r-b"}}])


@needs_node
class SavedFileNames(unittest.TestCase):
    """Runs plainName from ui/files.js: a name the hosts refuse would drop the whole save request silently."""

    def test_every_name_becomes_one_the_hosts_accept(self):
        names = ["smlouva: v2.pdf", "složka/příloha.pdf", "a:b.pdf", "C:\\dokumenty\\x.pdf", "slozka/", ".", "..", "a/..",
                 "", "x\x00y.txt", "tab\tname.txt", "obyčejný název.pdf"]
        script = "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(window.zfoFiles.plainName)));"
        out = run_ui_scripts(["bridge.js", "files.js"], script, json.dumps(names))
        plain = json.loads(out)
        self.assertTrue(all(desktop.is_plain_file_name(name) for name in plain), plain)
        self.assertEqual(plain[:6], ["smlouva_ v2.pdf", "příloha.pdf", "a_b.pdf", "x.pdf", "soubor", "soubor"])
        self.assertEqual(plain[-1], "obyčejný název.pdf")


@needs_node
class RevocationVerdict(unittest.TestCase):
    """Runs the decision in ui/bridge.js: the certificate below was revoked on 1 June."""
    BEFORE, AFTER = "2026-01-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00"
    SCRIPT = """
    window.fetch = async () => ({ok: true, json: async () => ({c: {status: 'revoked', revoked_at: '2026-06-01T00:00:00+00:00'}})});
    const sigs = JSON.parse(process.argv[1]), r = window.zfoRevocation;
    r.ask('s', sigs, () => {});
    setTimeout(() => console.log(JSON.stringify(sigs.map(sig => [r.provenBeforeRevocation(sig), r.revoked(sig), r.revokedBeforeSigning(sig)]))), 50);
    """

    def verdict(self, kind, proof_at, signed_at):
        sig = {"certificate": "c", "signed_at": signed_at, "proof": {"kind": kind, "at": proof_at} if kind else None}
        out = run_ui_scripts(["bridge.js"], self.SCRIPT, json.dumps([sig]))
        return tuple(json.loads(out)[0])

    def test_stamp_before_revocation_keeps_the_signature_valid(self):
        for kind in ("signature", "document"):
            self.assertEqual(self.verdict(kind, self.BEFORE, self.AFTER), (True, False, False))

    def test_signature_stamp_after_revocation_is_invalid(self):
        self.assertEqual(self.verdict("signature", self.AFTER, self.BEFORE), (False, True, True))

    def test_later_document_stamp_leaves_the_signers_claim(self):
        self.assertEqual(self.verdict("document", self.AFTER, self.BEFORE), (False, True, False))
        self.assertEqual(self.verdict("document", self.AFTER, self.AFTER), (False, True, True))

    def test_without_a_stamp_the_signers_claim_decides(self):
        self.assertEqual(self.verdict(None, None, self.BEFORE), (False, True, False))
        self.assertEqual(self.verdict(None, None, self.AFTER), (False, True, True))
