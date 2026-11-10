#*    This library is free software; you can redistribute it and/or
#*    modify it under the terms of the GNU Library General Public
#*    License as published by the Free Software Foundation; either
#*    version 2 of the License, or (at your option) any later version.
#*
#*    This library is distributed in the hope that it will be useful,
#*    but WITHOUT ANY WARRANTY; without even the implied warranty of
#*    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#*    Library General Public License for more details.
#*
#*    Written in November 2026 by Richard Winkler Bejr for "Blanketa".

# Scripts of buttons and field changes. They reach the form's data and nothing else, and a run counts as a whole: a script
# that stops halfway leaves the data as they were.
#
# A dialog cannot wait for its answer here, the page lives elsewhere. A script asking a question is undone, the page asks,
# and the script runs again from its start with the answers given so far.
import copy
import logging
import re

import lxml.etree as etree

from . import evaluate as ev
from . import form_stylesheets, js602, ns, zfo_doc

log = logging.getLogger("zfo.form_scripts")

MAX_WRITES_PER_RUN = 5_000  # each write runs dependent calculations, and evaluating XPath spends none of a script's budget
MAX_WRITTEN_CHARACTERS_PER_RUN = 32_000_000
MAX_SECTION_CHANGES_PER_RUN = 200
MAX_MESSAGES_PER_RUN = 5
MAX_QUESTIONS_PER_RUN = 8
MAX_NAMED_PARAMETERS = 1_000
MAX_LOOKUPS_PER_RUN = 3
MAX_STYLESHEET_RUNS_PER_RUN = 50  # a stylesheet's own work spends no budget; MPSV forms run 7 per click
# Lists of the addresses a form would send to MPSV's own service, in the two generations of MPSV forms. The form gets an
# empty list: Blanketa checks the addresses itself in RÚIAN (ruian.py), after the user agrees.
ADDRESS_LIST_STYLESHEETS = {"Validace_AdresaWS_XpathAdres", "XsltGetAdresy"}
# An address of an MPSV form is an element with these parts, marked by the place its check result goes, in both generations.
ADDRESS_PARTS = ("Ulice", "CisloPopisne", "CisloEvidencni", "CisloOrientacni", "Psc", "Obec", "CastObce")
ADDRESSES_IN_DATA = ("//*[*[local-name() = 'Ruian' or local-name() = '__PomValidovanaAdresa']]"
                     "[not(*[local-name() = '__ValidovanaAdresa'] = 'true')]"
                     "[*[local-name() = 'Stat'] = 'CZ' or not(*[local-name() = 'Stat'])][not(*[local-name() = 'Cislo'])]")
MAX_ADDRESSES_CHECKED = 10
KEEP_EARLIER_CHECK_RESULTS = "noclean"
CHECK_RESULT_TAG = "control"
CHECK_WARNING_TITLE = "Varování"
WHISPERER_FIELD_TYPE = "acm"
WHISPERER_LIST_ATTRIBUTE = "blanketa-list"
DATA_ROOT_PATH = "/*"
XML_LANG_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
FO_NAMESPACES = {"fo": ns.FO_URI, "fm": ns.FM_URI}
TRAILING_POSITION = re.compile(r"^(.*)\[(\d+)\]$", re.S)
EMPTY_ELEMENT = re.compile(r"<([\w.\-]+)\s*(?:/>|></\1\s*>)")
ARES_REGISTERS = {register: f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/{register}/"
                  for register in ("ekonomicke-subjekty", "ekonomicke-subjekty-res")}
ARES_SUBJECT_URL = re.compile(r"https://ares\.gov\.cz/ekonomicke-subjekty-v-be/rest/(ekonomicke-subjekty(?:-res)?)/([0-9]{8})")
CLOSED_ARES_URL = re.compile(r"https?://wwwinfo\.mfcr\.cz/cgi-bin/ares/.*", re.S)
INCOMPLETE_CHECK_NOTICE = "Některou podmínku formuláře Blanketa neumí vyhodnotit, kontrola formuláře proto není úplná."
CLOSED_ARES_NOTICE = "Formulář se ptá staré služby ARES, která už nefunguje. Údaje firmy vyplňte ručně."
HTTP_OK = 200
HTTP_NOT_FOUND = 404


def known_lookup(url):
  # The only address a form may ask. The user confirms each lookup, and only the IČO in the address leaves the computer.
  match = ARES_SUBJECT_URL.fullmatch(url) if isinstance(url, str) else None
  if match is None:
    return None
  register, number = match.groups()
  return {"url": url, "register": register, "number": number,
          "question": f"Formulář chce doplnit údaje firmy z\u00a0registru ARES.\n\nBlanketa pošle IČO {number} Ministerstvu financí (ares.gov.cz). "
                      "Nic jiného z\u00a0formuláře neodejde.\n\nPoslat?"}


def _lookup_failure(lookup, status):
  if status == HTTP_NOT_FOUND:
    return f"Registr ARES subjekt s\u00a0IČO {lookup['number']} nezná. Údaje vyplňte ručně."
  return f"Registr ARES údaje nevrátil (chyba {status}). Údaje vyplňte ručně."


def _usable_lookups(lookups):
  # They come from the page, which got them from the server: anything else in them is left out, not trusted.
  if not isinstance(lookups, dict):
    return {}
  return {url: answer for url, answer in lookups.items()
          if known_lookup(url) and isinstance(answer, dict) and type(answer.get("status")) is int and isinstance(answer.get("body"), str)}


class _LookupNeeded(js602.Unsupported):
  def __init__(self, lookup):
    super().__init__("SendData")
    self.lookup = lookup


class _LookupFailed(js602.Unsupported):
  def __init__(self, message):
    super().__init__("SendData")
    self.message = message


class _AnswerNeeded(js602.Unsupported):
  def __init__(self, text, title):
    super().__init__("MessageBox")
    self.text = text
    self.title = title


def is_action_script(value):
  return bool(value) and value.lstrip().startswith("{")


def button_elements(root):
  # Their position is how the page and the engine, each with its own copy of the form, agree on a button.
  return [element for element in root.iter() if isinstance(element.tag, str) and is_action_script(element.get("form602btnclick"))]


def name_buttons_for_page(root):
  for position, element in enumerate(button_elements(root)):
    element.set("blanketa-button", str(position))


def name_repeating_sections_by_bind(root):
  # Some forms mark a repeating section by its bind alone, the engine and the page know sections by form602repe.
  for element in root.iter():
    if isinstance(element.tag, str) and element.get("form602ct") == "rep" and not element.get("form602repe") and element.get("form602bind"):
      element.set("form602repe", element.get("form602bind"))
      element.set("blanketa-section-by-bind", "true")


def _plain_text(element):
  return "" if element is None else " ".join("".join(element.itertext()).split())


def _node_key(node):
  if etree.iselement(node):
    return node
  return (node.getparent(), node.attrname) if getattr(node, "is_attribute", False) else node.getparent()


def _repetitions_of(repe):
  repetitions = {}
  for token in filter(None, (repe or "").split("|||")):
    name = token[token.find("---") + 3:]
    bind_id, separator, index = name.rpartition("-rep-")
    if separator and index.isdigit():
      repetitions[bind_id] = int(index)
  return repetitions


def _repe_text(repetitions):
  return "|||".join(f"block---{bind_id}-rep-{index}" for bind_id, index in repetitions.items()) or None


class FormActionHost:
  def __init__(self, document, element, repe, answers, lookups):
    self.document = document
    self.lookups = lookups
    self.lookups_asked = 0
    self.element = element
    self.repetitions = _repetitions_of(repe)
    self.answers = list(answers)
    self.named_parameters = document.script_named_parameters
    self.writes = 0
    self.written_characters = 0
    self.section_changes = 0
    self.questions = 0
    self.messages = []
    self.notices = []
    self.problems = None
    self.check_is_incomplete = False
    self.stylesheet_runs = 0
    self.focus_bind = None
    self.has_section_changes = False
    self.addresses = None

  def _current_path(self):
    owner = next(iter(self.element.xpath("ancestor-or-self::*[@form602bind][1]")), None)
    binds = self.document._find_bind_elem(owner.get("form602bind")) if owner is not None else []
    if not len(binds):
      return DATA_ROOT_PATH
    return self.document._get_abs_data_path(binds[0], self.repetitions)

  def current_path(self, *_):
    return self._current_path()

  def _data_query(self):
    path = self._current_path()
    if path == DATA_ROOT_PATH:  # says nothing about the namespaces an expression needs, unlike the path of a bound element
      return ev.script_data_query(self.document, node=self.document._get_computable_data_root().getroot())
    return ev.script_data_query(self.document, path)

  def data_xpath(self, expression):
    return self._data_query()(expression)

  def list_item(self, *args):
    return ev.script_list_item_query(self._data_query())(*args)

  def _form_elements(self, path):
    try:
      found = self.element.xpath(path, namespaces=FO_NAMESPACES)
    except (etree.LxmlError, ValueError, TypeError) as error:
      raise js602.Unsupported("cesta ve formuláři") from error
    return [item for item in found if etree.iselement(item)] if isinstance(found, list) else []

  def attribute(self, name, path):
    found = self._form_elements(path)
    return found[0].get(name, "") if found else ""

  def _charge_write(self, value):
    self.writes += 1
    self.written_characters += len(value)
    if self.writes > MAX_WRITES_PER_RUN or self.written_characters > MAX_WRITTEN_CHARACTERS_PER_RUN:
      raise js602.Exhausted("zápisy")

  def _store(self, bind, path, value):
    document = self.document
    self._charge_write(value)
    try:
      if not document._get_data_root().xpath(path, namespaces=document._get_used_ns_prefix(path)):
        raise js602.Unsupported("SetData")
      document.store_value_path(bind, value, path, suppress_constraints=True)
      document.calculate_values(bind.get("Id"), _repe_text(self.repetitions))
    except (etree.LxmlError, ValueError, TypeError, IndexError) as error:
      raise js602.Unsupported("SetData") from error

  def _bind(self, bind_id):
    binds = self.document._find_bind_elem(bind_id)
    if not len(binds):
      raise js602.Unsupported("SetData")
    return binds[0]

  def set_data(self, path, bind_id, value):
    absolute = path if path.startswith("/") else self._current_path() + "/" + path
    self._store(self._bind(bind_id), absolute, value)

  def set_data_to(self, bind_id, value):
    bind = self._bind(bind_id)
    self._store(bind, self.document._get_abs_data_path(bind, self.repetitions), value)

  def _section_and_position(self, path):
    found = self._form_elements(path)
    position = None
    if not found:
      # The form holds each section once, Form Filler counts its copies in such a path.
      trailing = TRAILING_POSITION.match(path)
      if trailing:
        found = self._form_elements(trailing.group(1))
        position = int(trailing.group(2)) - 1
    sections = found[0].xpath("ancestor-or-self::*[@form602repe][1]") if found else []
    if not sections:
      raise js602.Unsupported("sekce")
    return sections[0].get("form602repe"), position

  def _charge_section_change(self):
    self.section_changes += 1
    if self.section_changes > MAX_SECTION_CHANGES_PER_RUN:
      raise js602.Exhausted("sekce")
    self.has_section_changes = True

  def insert_section(self, path):
    bind_id, _ = self._section_and_position(path)
    self._charge_section_change()
    self.document.clone_section(bind_id, _repe_text({name: index for name, index in self.repetitions.items() if name != bind_id}))
    return True

  def remove_section(self, path):
    document = self.document
    bind_id, position = self._section_and_position(path)
    if position is None:
      position = self.repetitions.get(bind_id, 0)
    others = {name: index for name, index in self.repetitions.items() if name != bind_id}
    copies_path = document._get_abs_data_path(self._bind(bind_id), others)
    copies = document._get_data_root().xpath(copies_path, namespaces=document._get_used_ns_prefix(copies_path))
    if len(copies) <= 1 or position >= len(copies) or position < 0:
      return False
    self._charge_section_change()
    document.delete_section(f"{bind_id}-rep-{position}", _repe_text({**others, bind_id: position}))
    return True

  def _only_data_element(self, tree, path):
    document = self.document
    try:
      found = tree.xpath(path, namespaces=document._get_used_ns_prefix(path))
    except (etree.LxmlError, ValueError, TypeError) as error:
      raise js602.Unsupported("document.LoadFormDataPart") from error
    if not isinstance(found, list) or len(found) != 1 or not etree.iselement(found[0]):
      raise js602.Unsupported("document.LoadFormDataPart")
    return found[0]

  def _replace_with_published(self, tree, path, published):
    current = self._only_data_element(tree, path)
    current.getparent().replace(current, copy.deepcopy(published))

  def load_empty_data_part(self, content, path):
    # Forms clear a part of their data by loading it empty. Any other content would come from a file or the network.
    empty_element = EMPTY_ELEMENT.fullmatch(content)
    document = self.document
    published = self._only_data_element(document.original_model, path)
    if not empty_element or empty_element.group(1) != etree.QName(published).localname or published.getparent() is None:
      raise js602.Unsupported("document.LoadFormDataPart")
    self._charge_section_change()
    self._replace_with_published(document._get_data_root(), path, published)
    self._replace_with_published(document._get_computable_data_root(), path, published)

  def activate_field(self, path):
    found = self._form_elements(path)
    if not found:
      return
    fields = found[0].xpath("descendant-or-self::*[@form602bind and not(@form602repe)][1]")
    if fields:
      self.focus_bind = fields[0].get("form602bind")

  def check_data(self):
    left_out_before = self.document.conditions_left_out
    self.problems = self.document.find_form_problems()
    if self.document.conditions_left_out > left_out_before:
      self.check_is_incomplete = True
      if INCOMPLETE_CHECK_NOTICE not in self.notices:
        self.notices.append(INCOMPLETE_CHECK_NOTICE)
    return not self.problems

  def form_data(self, stylesheet_name):
    if stylesheet_name in ADDRESS_LIST_STYLESHEETS:
      self.addresses = czech_addresses(self.document)
      return ""
    if self.document.get_template_text(stylesheet_name) is None:
      raise js602.Unsupported("GetFormData")
    return str(self._run_stylesheet(stylesheet_name))

  def _run_stylesheet(self, stylesheet_name):
    self.stylesheet_runs += 1
    if self.stylesheet_runs > MAX_STYLESHEET_RUNS_PER_RUN:
      raise js602.Exhausted("šablony")
    return form_stylesheets.transform(self.document, stylesheet_name, self.document.script_interpreter().budget)

  def _fields_by_data_node(self):
    document = self.document
    fields = {}
    for bind in document.root.xpath("//fm:bind", namespaces={"fm": ns.FM_URI}):
      path = document._get_abs_data_path(bind)
      try:
        nodes = document._get_data_root().xpath(path, namespaces=document._get_used_ns_prefix(path))
      except (etree.LxmlError, ValueError, TypeError):
        continue
      for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        fields.setdefault(_node_key(node), (bind.get("Id"), index))
    return fields

  def _check_problem(self, item, fields):
    field, index = None, 0
    path = (item.findtext("focus") or "").strip()
    if path:
      document = self.document
      try:
        found = document._get_data_root().xpath(path, namespaces=document._get_used_ns_prefix(path))
      except (etree.LxmlError, ValueError, TypeError):
        found = []
      if isinstance(found, list) and found:
        field, index = fields.get(_node_key(found[0]), (None, 0))
    return {"field": field, "index": index, "kind": "form", "message": _plain_text(item.find("text"))}

  def xsl_check(self, stylesheet_name, mode):
    # The stylesheet answers with <control><item><text/><focus/></item>…<warning><text/></warning>…</control>: an item
    # for each fault it found, a warning for what the user should only know. Form Filler shows the warnings only when
    # there is no fault, and they do not fail the check.
    output = self._run_stylesheet(stylesheet_name).getroot()
    if output is None or output.tag != CHECK_RESULT_TAG:
      raise js602.Unsupported("XSLCheck")
    items = output.findall("item")
    if mode != KEEP_EARLIER_CHECK_RESULTS or self.problems is None:
      self.problems = []
    fields = self._fields_by_data_node() if items else {}
    self.problems.extend(self._check_problem(item, fields) for item in items)
    warnings = [text for text in (_plain_text(warning.find("text")) for warning in output.findall("warning")) if text]
    if warnings and not items:
      self._add_message("\n".join(warnings), CHECK_WARNING_TITLE)
    return not items

  def send_data(self, method, url):
    if CLOSED_ARES_URL.fullmatch(url):
      raise _LookupFailed(CLOSED_ARES_NOTICE)
    lookup = known_lookup(url) if method == js602.SM_GET else None
    self.lookups_asked += 1
    if lookup is None or self.lookups_asked > MAX_LOOKUPS_PER_RUN:
      raise js602.Unsupported("SendData")
    answer = self.lookups.get(url)
    if answer is None:
      raise _LookupNeeded(lookup)
    if answer["status"] != HTTP_OK:
      raise _LookupFailed(_lookup_failure(lookup, answer["status"]))
    self._charge_write(answer["body"])
    return answer["body"]

  def set_named_parameter(self, name, value):
    if name not in self.named_parameters and len(self.named_parameters) >= MAX_NAMED_PARAMETERS:
      raise js602.Exhausted("parametry")
    self._charge_write(value)
    self.named_parameters[name] = value

  def show_message(self, text, title, flags):
    if int(flags) & int(js602.MB_YESNO):
      self.questions += 1
      if self.questions > MAX_QUESTIONS_PER_RUN:
        raise js602.Unsupported("MessageBox")
      if not self.answers:
        raise _AnswerNeeded(text, title)
      return js602.IDYES if self.answers.pop(0) is True else js602.IDNO
    self._add_message(text, title)
    return js602.IDOK

  def _add_message(self, text, title):
    if len(self.messages) >= MAX_MESSAGES_PER_RUN:
      raise js602.Exhausted("zprávy")
    self.messages.append({"text": text, "title": title})


def _page_values(document):
  values = {}
  for bind in document.root.xpath("//fm:bind", namespaces=FO_NAMESPACES):
    bind_id = bind.get("Id")
    if not bind_id or document.is_bind_file(bind_id):
      continue
    try:
      values[bind_id] = document.get_value(bind_id)
    except (etree.LxmlError, ValueError, TypeError, IndexError):
      continue
  return values


def _section_tree(document, sections):
  tree = {}
  for name, copies in sections.items():
    if isinstance(copies, list):
      tree[name] = [{"sub_rs": _section_tree(document, copy_of_section)} for copy_of_section in copies]
  return tree


def _section_templates(document, tree):
  templates = {}
  for name, copies in tree.items():
    templates[name] = document.get_repe_section_html(name)
    for section_copy in copies:
      templates.update(_section_templates(document, section_copy["sub_rs"]))
  return templates


def serialized_data(document):
  return etree.tostring(document._get_data_root())


def address_text(address, with_post_office=True):
  # One line as the register writes an address ("Na Poříčním právu 376/1, Nové Město, 12800 Praha"): what the user agrees
  # to send is exactly what is sent.
  registry = not address["CisloPopisne"] and address["CisloEvidencni"]
  number = (f"č.ev. {address['CisloEvidencni']}" if registry else address["CisloPopisne"]) + (
    f"/{address['CisloOrientacni']}" if address["CisloOrientacni"] else "")
  place = address["Ulice"] if registry else address["Ulice"] or address["CastObce"] or address["Obec"]
  building = " ".join(part for part in (place, number) if part)
  part_of_municipality = address["CastObce"] if with_post_office and address["CastObce"] not in (place, address["Obec"]) else ""
  postcode = re.sub(r"\D", "", address["Psc"]) if with_post_office else ""
  post_office = " ".join(part for part in (postcode, address["Obec"]) if part)
  return ", ".join(part for part in (building, part_of_municipality, post_office) if part)


def czech_addresses(document):
  addresses = []
  for element in document._get_data_root().xpath(ADDRESSES_IN_DATA):
    parts = {child.xpath("local-name()"): (child.text or "").strip() for child in element if isinstance(child.tag, str)}
    address = {part: parts.get(part, "") for part in ADDRESS_PARTS}
    if any(address.values()) and address not in addresses:
      addresses.append(address)
  return addresses[:MAX_ADDRESSES_CHECKED]


def _addresses_to_ask(document, addresses):
  # Form Filler marks an address it checked and asks no more; Blanketa leaves the data as they are, so it remembers which
  # addresses it asked about, and asks again only about one that changed since.
  new = [address for address in addresses if address_text(address) not in document.addresses_asked]
  document.addresses_asked.update(address_text(address) for address in new)
  return new


def _result_of_finished_run(document, host, values_before, data_before):
  if host.has_section_changes:
    document.recalc_form()
  values_after = _page_values(document)
  changed_values = {bind_id: value for bind_id, value in values_after.items()
                    if values_before.get(bind_id) != value}
  data_changed = serialized_data(document) != data_before
  if data_changed:
    document.is_changed = True
  result = {"values": changed_values, "changed": data_changed, "messages": host.messages, "notices": host.notices,
            "focus": host.focus_bind, "problems": host.problems}
  if host.check_is_incomplete:
    result["scripts"] = [zfo_doc.UNREADABLE_CONDITION]
  if host.addresses is not None:
    document.addresses_to_check = _addresses_to_ask(document, host.addresses)
    result["addresses"] = [address_text(address) for address in document.addresses_to_check]
  if host.has_section_changes:
    tree = _section_tree(document, document.get_repeatable_sections())
    result["sections"] = tree
    result["templates"] = _section_templates(document, tree)
  return result


def _use_czech_for_form_texts(document):
  # Form Filler fills in the language of its user when it opens a form, and the forms pick their messages by it: with
  # none, every dialog would be empty. Only the tree scripts read: the saved data stay as published.
  root = document._get_computable_data_root().getroot()
  if root.get(XML_LANG_ATTRIBUTE) == "":
    root.set(XML_LANG_ATTRIBUTE, "cs")


def _run(document, element, script, repe, answers, lookups, retry):
  _use_czech_for_form_texts(document)
  saved_data = copy.deepcopy(document._get_data_root())
  saved_computing_data = copy.deepcopy(document._get_computable_data_root())
  saved_parameters = dict(document.script_named_parameters)
  values_before = _page_values(document)
  data_before = serialized_data(document)
  lookups = _usable_lookups(lookups)
  host = FormActionHost(document, element, repe, answers, lookups)
  was_script_running = document.script_running
  document.script_running = True
  outcome = None
  try:
    js602.run_form_action(document.script_interpreter(), script, host)
  except _AnswerNeeded as question:
    outcome = {"question": {"text": question.text, "title": question.title,
                            "retry": {**retry, "repe": repe or "", "answers": list(answers), "lookups": lookups}}}
  except _LookupNeeded as needed:
    outcome = {"lookup": {"url": needed.lookup["url"], "question": needed.lookup["question"],
                          "retry": {**retry, "repe": repe or "", "answers": list(answers), "lookups": lookups}}}
  except _LookupFailed as failed:
    outcome = {"stopped": failed.message}
  except js602.Unsupported as unsupported:
    log.info("Form script stopped at %s", unsupported)
    outcome = {"refused": str(unsupported)}
  except js602.Exhausted:
    outcome = {"refused": "skript nedoběhl"}
  except js602.Thrown as thrown:
    log.info("Form script stopped with its own error: %s", thrown)
    outcome = {"refused": "chyba ve skriptu formuláře"}
  finally:
    document.script_running = was_script_running
  if outcome is None:
    try:
      return _result_of_finished_run(document, host, values_before, data_before)
    except (etree.LxmlError, ValueError, TypeError, IndexError) as error:  # a hostile form's data may not fit its own binds
      log.info("Form script left data the form cannot show: %s", error)
      outcome = {"refused": "chyba ve skriptu formuláře"}
  document.data_root = saved_data
  document.computing_data_tree = saved_computing_data
  document.script_named_parameters = saved_parameters
  return outcome


def _choose_whispered_item(document, element, bind_id, repe):
  # Such a field offers items downloaded from the office, and its script clears a text no item was chosen for. With the
  # list at hand (blanketa-list, see codelists.py) the text picks its item; without it the text the user typed has to count.
  typed = document.get_value(bind_id, repe or None) or ""
  list_name = element.get(WHISPERER_LIST_ATTRIBUTE)
  if not list_name:
    document.script_named_parameters.update({"ACIItemId": typed, "ACIText": typed})
    return
  chosen = document.root.xpath("//fm:enum[@name=$list]/fm:list/fm:listitem[@itemtext=$text][1]/@value",
                               namespaces=FO_NAMESPACES, list=list_name, text=typed)
  document.script_named_parameters.update({"ACIItemId": str(chosen[0]) if chosen else "", "ACIText": typed})


def run_button_script(document, position, repe, answers, lookups=None):
  buttons = button_elements(document.root)
  if not 0 <= position < len(buttons):
    return None
  element = buttons[position]
  return _run(document, element, element.get("form602btnclick"), repe, answers, lookups, {"button": position})


def run_field_change_script(document, bind_id, repe, answers, lookups=None, values=()):
  # The same field drawn twice carries the same script twice, it runs once. It does not start other fields' scripts.
  # Check boxes of one bind each carry their own script: the one that writes the new value runs, or the one that
  # wrote the old value when it was unchecked. values are the new value and the old one.
  if not isinstance(bind_id, str) or not re.fullmatch(r"[\w.\-]+", bind_id):
    return None
  fields = [field for field in document.root.xpath("//*[@form602bind=$bind and @form602onchange]", bind=bind_id)
            if is_action_script(field.get("form602onchange"))]
  if not fields:
    return None
  element = next((field for value in values for field in fields if field.get("form602on") == value), fields[0])
  if element.get("form602ct") == WHISPERER_FIELD_TYPE:
    _choose_whispered_item(document, element, bind_id, repe)
  return _run(document, element, element.get("form602onchange"), repe, answers, lookups, {"field": bind_id, "values": list(values)})
