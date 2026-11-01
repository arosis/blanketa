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
from . import js602, ns

log = logging.getLogger("zfo.form_scripts")

MAX_WRITES_PER_RUN = 5_000  # each write runs dependent calculations, and evaluating XPath spends none of a script's budget
MAX_WRITTEN_CHARACTERS_PER_RUN = 32_000_000
MAX_SECTION_CHANGES_PER_RUN = 200
MAX_MESSAGES_PER_RUN = 5
MAX_QUESTIONS_PER_RUN = 8
MAX_NAMED_PARAMETERS = 1_000
ADDRESS_CHECK_STYLESHEET = "Validace_AdresaWS_XpathAdres"
ADDRESS_NOT_CHECKED_NOTICE = "Adresy nebyly ověřeny v registru RÚIAN, Blanketa formulářům síť nepovoluje. Před odesláním je zkontrolujte."
WHISPERER_FIELD_TYPE = "acm"
DATA_ROOT_PATH = "/*"
XML_LANG_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
FO_NAMESPACES = {"fo": ns.FO_URI, "fm": ns.FM_URI}
TRAILING_POSITION = re.compile(r"^(.*)\[(\d+)\]$", re.S)
EMPTY_ELEMENT = re.compile(r"<([\w.\-]+)\s*(?:/>|></\1\s*>)")


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
  def __init__(self, document, element, repe, answers):
    self.document = document
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
    self.focus_bind = None
    self.has_section_changes = False

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
    self.problems = self.document.find_form_problems()
    return not self.problems

  def form_data(self, stylesheet_name):
    if stylesheet_name != ADDRESS_CHECK_STYLESHEET:
      raise js602.Unsupported("GetFormData")
    if ADDRESS_NOT_CHECKED_NOTICE not in self.notices:
      self.notices.append(ADDRESS_NOT_CHECKED_NOTICE)
    return ""

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
    if len(self.messages) >= MAX_MESSAGES_PER_RUN:
      raise js602.Exhausted("zprávy")
    self.messages.append({"text": text, "title": title})
    return js602.IDOK


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


def _value_for_page(document, bind_id, value):
  on_value = document._get_checkbox_on_value(bind_id)
  if on_value is None:
    return value
  return "on" if value == on_value else "off"


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


def _result_of_finished_run(document, host, values_before):
  if host.has_section_changes:
    document.recalc_form()
  values_after = _page_values(document)
  changed = {bind_id: _value_for_page(document, bind_id, value) for bind_id, value in values_after.items()
             if values_before.get(bind_id) != value}
  if changed or host.has_section_changes:
    document.is_changed = True
  result = {"values": changed, "messages": host.messages, "notices": host.notices, "focus": host.focus_bind,
            "problems": host.problems}
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


def _run(document, element, script, repe, answers, retry):
  _use_czech_for_form_texts(document)
  saved_data = copy.deepcopy(document._get_data_root())
  saved_computing_data = copy.deepcopy(document._get_computable_data_root())
  saved_parameters = dict(document.script_named_parameters)
  values_before = _page_values(document)
  host = FormActionHost(document, element, repe, answers)
  was_script_running = document.script_running
  document.script_running = True
  outcome = None
  try:
    js602.run_form_action(document.script_interpreter(), script, host)
  except _AnswerNeeded as question:
    outcome = {"question": {"text": question.text, "title": question.title, "retry": {**retry, "repe": repe or "", "answers": list(answers)}}}
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
      return _result_of_finished_run(document, host, values_before)
    except (etree.LxmlError, ValueError, TypeError, IndexError) as error:  # a hostile form's data may not fit its own binds
      log.info("Form script left data the form cannot show: %s", error)
      outcome = {"refused": "chyba ve skriptu formuláře"}
  document.data_root = saved_data
  document.computing_data_tree = saved_computing_data
  document.script_named_parameters = saved_parameters
  return outcome


def _treat_typed_text_as_chosen_item(document, bind_id, repe):
  # Such a field offers items downloaded from the office, and its script clears a text no item was chosen for.
  # A form gets no network here, so the text the user typed has to count.
  typed = document.get_value(bind_id, repe or None) or ""
  document.script_named_parameters.update({"ACIItemId": typed, "ACIText": typed})


def run_button_script(document, position, repe, answers):
  buttons = button_elements(document.root)
  if not 0 <= position < len(buttons):
    return None
  element = buttons[position]
  return _run(document, element, element.get("form602btnclick"), repe, answers, {"button": position})


def run_field_change_script(document, bind_id, repe, answers):
  # The same field drawn twice carries the same script twice, it runs once. It does not start other fields' scripts.
  if not isinstance(bind_id, str) or not re.fullmatch(r"[\w.\-]+", bind_id):
    return None
  fields = document.root.xpath("//*[@form602bind=$bind and @form602onchange]", bind=bind_id)
  scripts = list(dict.fromkeys(field.get("form602onchange") for field in fields if is_action_script(field.get("form602onchange"))))
  if not scripts:
    return None
  if len(scripts) > 1:
    log.info("Field %s has %d different change scripts, the first runs", bind_id, len(scripts))
  element = next(field for field in fields if field.get("form602onchange") == scripts[0])
  if element.get("form602ct") == WHISPERER_FIELD_TYPE:
    _treat_typed_text_as_chosen_item(document, bind_id, repe)
  return _run(document, element, scripts[0], repe, answers, {"field": bind_id})
