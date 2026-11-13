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
import base64
import binascii
import copy
import logging
import re

import lxml.etree as etree

from . import evaluate as ev
from . import file_kinds, form_stylesheets, js602, ns, zfo_doc

log = logging.getLogger("zfo.form_scripts")

MAX_WRITES_PER_RUN = 5_000  # each write runs dependent calculations, and evaluating XPath spends none of a script's budget
MAX_WRITTEN_CHARACTERS_PER_RUN = 32_000_000
MAX_SECTION_CHANGES_PER_RUN = 200
MAX_MESSAGES_PER_RUN = 5
MAX_QUESTIONS_PER_RUN = 8
MAX_NAMED_PARAMETERS = 1_000
MAX_LOOKUPS_PER_RUN = 3
MAX_ATTACHED_BYTES = 17 * 1024 * 1024  # what MPSV forms take for all the attachments of one application
MAX_ATTACHED_CHARACTERS = 2 * MAX_ATTACHED_BYTES  # in base64; a file over the limit still decodes, for its refusal to name the limit
ATTACHMENT_FIELD_TYPE = "bin"
# The newer MPSV forms, written for FormApps Server: a button hands a chosen file to its script (bib) or adds each chosen
# file to a repeating section (btm), and a link shows the file an attachment holds (bil).
UPLOAD_BUTTON_TYPE, FILE_LIST_BUTTON_TYPE, ATTACHMENT_LINK_TYPE = "bib", "btm", "bil"
SIZE_OF_ATTACHMENT_FORMAT = "attch-size-kB"
NAME_OF_UNNAMED_ATTACHMENT = "priloha"
FIELD_ONLY_GIVES_ITS_FILE = "save"  # form602menu of a field that hands its file back to the user and takes none
NO_ANTIVIRUS_NOTICE = "Blanketa vložený soubor antivirem nekontroluje."
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
SCRIPT_DATA_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
PLAIN_FILE_NAME = r"[^\\/:*?\"<>|\x00-\x1f.][^\\/:*?\"<>|\x00-\x1f]{0,200}"
SAVED_DATA_NAME = re.compile(PLAIN_FILE_NAME + r"\.xml", re.I)
ATTACHED_FILE_NAME = re.compile(PLAIN_FILE_NAME)
PRINTED_FORM_NAME = re.compile(PLAIN_FILE_NAME + r"\.pdf", re.I)
XML_LANG_ATTRIBUTE = "{http://www.w3.org/XML/1998/namespace}lang"
FO_NAMESPACES = {"fo": ns.FO_URI, "fm": ns.FM_URI}
TRAILING_POSITION = re.compile(r"^(.*)\[(\d+)\]$", re.S)
FIRST_OF_AN_ATTRIBUTE = re.compile(r"(/@[^/\[\]]+)\[1\]$")
ARES_REGISTERS = {register: f"https://ares.gov.cz/ekonomicke-subjekty-v-be/rest/{register}/"
                  for register in ("ekonomicke-subjekty", "ekonomicke-subjekty-res")}
ARES_SUBJECT_URL = re.compile(r"https://ares\.gov\.cz/ekonomicke-subjekty-v-be/rest/(ekonomicke-subjekty(?:-res)?)/([0-9]{8})")
CLOSED_ARES_URL = re.compile(r"https?://wwwinfo\.mfcr\.cz/cgi-bin/ares/.*", re.S)
INCOMPLETE_CHECK_NOTICE = "Některou podmínku formuláře Blanketa neumí vyhodnotit, kontrola formuláře proto není úplná."
PDF_FROM_PRINT_NOTICE = "Formulář chtěl uložit PDF. V\u00a0dialogu tisku zvolte uložení do PDF."
CLOSED_ARES_NOTICE = "Formulář se ptá staré služby ARES, která už nefunguje. Údaje firmy vyplňte ručně."
HTTP_OK = 200
MPSV_WEB = "https://mpsv.gov.cz/"
MPSV_DOCUMENT_PATH = re.compile(r"documents/[0-9]{1,12}/[0-9]{1,12}/[A-Za-z0-9_\-]{1,100}\.pdf")
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


def published_document(url):
  # MPSV forms name their help by a path under an address their portal supplies; a downloaded form has no such address.
  path = url.removeprefix(MPSV_WEB)
  return MPSV_WEB + path if MPSV_DOCUMENT_PATH.fullmatch(path) else None


def _question_before_opening(url):
  return (f"Formulář chce zobrazit dokument z\u00a0webu Ministerstva práce a\u00a0sociálních věcí:\n\n{url}\n\n"
          "Blanketa ho otevře v\u00a0prohlížeči. Z\u00a0formuláře nic neodejde.\n\nOtevřít?")


def _names_the_same(loaded, published):
  loaded_name, published_name = etree.QName(loaded), etree.QName(published)
  return loaded_name.localname == published_name.localname and loaded_name.namespace in (None, published_name.namespace)


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


def name_what_attachments_show(root):
  # The link of an attachment refers to the file itself and shows its name; the output beside it refers to the file too
  # and shows its size. The page and the values of a repeating section go by the bind that is shown.
  links = [element for element in root.iter() if isinstance(element.tag, str) and element.get("form602ct") == ATTACHMENT_LINK_TYPE]
  sizes_of_files = {link.get("form602bindref"): link.get("filesizebind") for link in links if link.get("filesizebind")}
  for link in links:
    if link.get("filenamebind"):
      link.set("blanketa-shown-bind", link.get("filenamebind"))
  for element in root.iter():
    if (isinstance(element.tag, str) and element.get("form602format") == SIZE_OF_ATTACHMENT_FORMAT
        and element.get("form602bindref") in sizes_of_files):
      element.set("blanketa-shown-bind", sizes_of_files[element.get("form602bindref")])


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


def _data_bind_of(field):
  return field.get("form602bind") or field.get("form602bindref")


class FormActionHost:
  def __init__(self, document, element, repe, answers, lookups, is_started_by_button, uploaded_file=None):
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
    self.file_to_save = None
    self.closes_window = False
    self.prints_form = False
    self.document_to_open = None
    self.is_started_by_button = is_started_by_button
    self.uploaded_file = uploaded_file
    self.types_the_upload_takes = set()
    self.offers_navigation = False

  def _current_path(self):
    # A button or a link of the newer MPSV forms stands at the data it refers to (form602bindref): their scripts write to
    # "../d:error" and "../d:filename" beside it.
    owner = next(iter(self.element.xpath("ancestor-or-self::*[@form602bind or @form602bindref][1]")), None)
    binds = self.document._find_bind_elem(_data_bind_of(owner)) if owner is not None else []
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

  def _store(self, bind, path, value, is_users_own=False):
    document = self.document
    if not is_users_own:
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
    # MPSV forms write the size of an attachment to ".../@Velikost[1]"; an element has one attribute of a name.
    path = FIRST_OF_AN_ATTRIBUTE.sub(r"\1", path)
    absolute = path if path.startswith("/") else self._current_path() + "/" + path
    self._store(self._bind(bind_id), absolute, value)

  def set_data_to(self, bind_id, value):
    bind = self._bind(bind_id)
    self._store(bind, self.document._get_abs_data_path(bind, self.repetitions), value)

  def _write_about_file(self, field, data, name, extension, size):
    for bind_id, value in ((_data_bind_of(field), data), (field.get("filenamebind"), name), (field.get("mediatypebind"), extension),
                           (field.get("filesizebind"), size)):
      if bind_id:
        bind = self._bind(bind_id)
        self._store(bind, self.document._get_abs_data_path(bind, self.repetitions), value, is_users_own=True)

  def take_file(self, name, content):
    # Form Filler writes the file in base64 into the field's data, its name and its extension, without the dot and as
    # the name has it, into the binds the field names (seen in Form Filler 4.81 on an MPSV form). The web application of
    # MPSV writes the same and the size in bytes beside them (seen in its saved data in November 2026).
    self._write_about_file(self.element, base64.b64encode(content).decode("ascii"), name, file_kinds.extension_as_written(name),
                           str(len(content)))

  def _attachment_field(self, path, asked_by="element.GetBinData"):
    fields = [self.element] if path.strip() in ("", ".") else self._form_elements(path)
    field = next((field for field in fields if field.get("form602ct") in (ATTACHMENT_FIELD_TYPE, ATTACHMENT_LINK_TYPE)
                  and _data_bind_of(field)), None)
    if field is None:
      raise js602.Unsupported(asked_by)
    return field

  def put_file(self, path, name, content):
    field = self._attachment_field(path, "element.SetBinData")
    self._write_about_file(field, base64.b64encode(content).decode("ascii"), name, file_kinds.extension_as_written(name), str(len(content)))

  def clear_file(self, path):
    self._write_about_file(self._attachment_field(path, "element.SetBinData"), "", "", "", "")

  def attachment_with_name(self, path):
    field = self._attachment_field(path)
    return (self._value_of_bind(field.get("filenamebind")) if field.get("filenamebind") else ""), self._content_of(field)

  def uploaded(self):
    if self.uploaded_file is None:
      raise js602.Unsupported("element.GetUploadedFileData")
    return self.uploaded_file

  def _value_of_bind(self, bind_id):
    document = self.document
    path = document._get_abs_data_path(self._bind(bind_id), self.repetitions)
    nodes = document._get_data_root().xpath(path, namespaces=document._get_used_ns_prefix(path))
    if not nodes:
      return ""
    return nodes[0] if isinstance(nodes[0], str) else nodes[0].text or ""

  def _content_of(self, field):
    try:
      return base64.b64decode(self._value_of_bind(_data_bind_of(field)))
    except (binascii.Error, ValueError) as error:
      raise js602.Unsupported("element.GetBinData") from error

  def attachment(self, path):
    return self._content_of(self._attachment_field(path))

  def fault_of_attachment(self, path, checks):
    field = self._attachment_field(path)
    if checks & int(js602.ANTIVIRUS_CHECK) and NO_ANTIVIRUS_NOTICE not in self.notices:
      self.notices.append(NO_ANTIVIRUS_NOTICE)
    if not checks & int(js602.MIMETYPE_CHECK):
      return None
    name = self._value_of_bind(field.get("filenamebind")) if field.get("filenamebind") else ""
    allowed = file_kinds.extensions_in_filter(field.get("form602ffilter")) or self.types_the_upload_takes
    return file_kinds.fault_of_kind(name, self._content_of(field), allowed)

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

  def _replace_part(self, tree, path, part):
    current = self._only_data_element(tree, path)
    current.getparent().replace(current, copy.deepcopy(part))

  def load_data_part(self, content, path):
    # Forms clear a part of their data by loading it empty, set the number of its items by loading as many empty ones,
    # and fill it from a file the user chose. Whatever they load lands in the part as the form published it.
    document = self.document
    published = self._only_data_element(document.original_model, path)
    self._charge_write(content)
    try:
      loaded = etree.fromstring(content.encode(), SCRIPT_DATA_PARSER)
    except (etree.LxmlError, ValueError) as error:
      raise js602.Unsupported("document.LoadFormDataPart") from error
    is_whole_document = published.getparent() is None
    if is_whole_document or loaded.getroottree().docinfo.doctype or not _names_the_same(loaded, published):
      raise js602.Unsupported("document.LoadFormDataPart")
    self._charge_section_change()
    filled = copy.deepcopy(published)
    self._fill_published_with(filled, loaded)
    self._replace_part(document._get_data_root(), path, filled)
    try:
      document.computing_data_tree = document._create_computing_data_tree(document._get_data_root())
    except IndexError as error:  # a numeric field of the form has no place in the loaded data
      raise js602.Unsupported("document.LoadFormDataPart") from error

  def _fill_published_with(self, published, loaded):
    for name, value in loaded.attrib.items():
      if name in published.attrib:
        published.set(name, value)
    loaded_children = [child for child in loaded if isinstance(child.tag, str)]
    if not loaded_children:
      if len(published) == 0:
        published.text = loaded.text
      return
    first_of_each_name = {child.tag: copy.deepcopy(child) for child in reversed(published) if isinstance(child.tag, str)}
    text_of_first = {}
    filled_copies = {}
    for child in loaded_children:
      tag = next((tag for tag, first in first_of_each_name.items() if _names_the_same(child, first)), None)
      if tag is None:
        continue
      copies = published.findall(tag)
      position = filled_copies.get(tag, 0)
      filled_copies[tag] = position + 1
      if position == len(copies):
        if tag not in text_of_first:
          text_of_first[tag] = etree.tostring(first_of_each_name[tag], encoding="unicode")
        self._charge_write(text_of_first[tag])
        copies[-1].addnext(copy.deepcopy(first_of_each_name[tag]))
        copies = published.findall(tag)
      self._fill_published_with(copies[position], child)
    for tag, written in filled_copies.items():
      for published_beyond_the_written in published.findall(tag)[written:]:
        published.remove(published_beyond_the_written)

  def xpath_in_text(self, content, expression):
    try:
      found = etree.fromstring(content.encode(), SCRIPT_DATA_PARSER).getroottree().xpath(
        expression, namespaces=self.document._get_used_ns_prefix(expression))
    except (etree.LxmlError, ValueError, TypeError) as error:
      raise js602.Unsupported("EvalXPath") from error
    return ev.text_of_script_answer(found)

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

  def form_data(self, stylesheet_name, data_part=""):
    if stylesheet_name in ADDRESS_LIST_STYLESHEETS:
      self.addresses = czech_addresses(self.document)
      return ""
    if not stylesheet_name:
      return self._data_part(data_part)
    if self.document.get_template_text(stylesheet_name) is None:
      raise js602.Unsupported("GetFormData")
    if data_part:
      return self.transform_data(self._data_part(data_part), stylesheet_name)
    return str(self._run_stylesheet(stylesheet_name))

  def _data_part(self, path):
    data = self.document._get_data_root()
    if not path:
      return etree.tostring(data, encoding="unicode")
    try:
      found = data.xpath(path, namespaces=self.document._get_used_ns_prefix(path))
    except (etree.LxmlError, ValueError, TypeError) as error:
      raise js602.Unsupported("GetFormData") from error
    if not isinstance(found, list) or not found or not etree.iselement(found[0]):
      raise js602.Unsupported("GetFormData")
    return etree.tostring(found[0], encoding="unicode")

  def transform_data(self, content, stylesheet_name):
    try:
      source = etree.ElementTree(etree.fromstring(content.encode(), SCRIPT_DATA_PARSER))
    except (etree.XMLSyntaxError, ValueError) as error:
      raise js602.Unsupported("TransformData") from error
    return str(self._run_stylesheet(stylesheet_name, source))

  def _run_stylesheet(self, stylesheet_name, source=None):
    self.stylesheet_runs += 1
    if self.stylesheet_runs > MAX_STYLESHEET_RUNS_PER_RUN:
      raise js602.Exhausted("šablony")
    return form_stylesheets.transform(self.document, stylesheet_name, self.document.script_interpreter().budget, source)

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

  def document_on_the_web(self, method, url):
    address = published_document(url) if method == js602.SM_GET else None
    if address is not None:
      self.lookups_asked += 1
      if self.lookups_asked > MAX_LOOKUPS_PER_RUN:
        raise js602.Unsupported("SendData")
    return address

  def _only_after_a_button(self, what):
    # A form is someone else's file: what reaches beyond its data happens only when the user presses a button of the
    # form, not while they type.
    if not self.is_started_by_button:
      raise js602.Unsupported(what + " bez stisku tlačítka")

  def show_document_on_the_web(self, url):
    self._only_after_a_button("dokument z webu")
    if self.document_to_open is not None:
      raise js602.Unsupported("ShowData")
    self.document_to_open = url

  def set_named_parameter(self, name, value):
    if name not in self.named_parameters and len(self.named_parameters) >= MAX_NAMED_PARAMETERS:
      raise js602.Exhausted("parametry")
    self._charge_write(value)
    self.named_parameters[name] = value

  def save_data(self, content, name):
    self._only_after_a_button("uložení dat")
    # The page saves the file through the save dialog of its window; the form names it, the user chooses where it goes.
    if self.file_to_save is not None or not SAVED_DATA_NAME.fullmatch(name):
      raise js602.Unsupported("SaveData")
    self._charge_write(content)
    self.file_to_save = {"name": name, "content": content}

  def print_form(self):
    self._only_after_a_button("tisk")
    self.prints_form = True

  def save_printed_form(self, name):
    if not PRINTED_FORM_NAME.fullmatch(name):
      raise js602.Unsupported("SaveData")
    self.print_form()
    if PDF_FROM_PRINT_NOTICE not in self.notices:
      self.notices.append(PDF_FROM_PRINT_NOTICE)

  def close_window(self):
    self._only_after_a_button("zavření okna")
    self.closes_window = True

  def offer_navigation(self):
    self._only_after_a_button("přechod na část formuláře")
    self.offers_navigation = True

  def show_message(self, text, title, flags):
    if int(flags) & int(js602.MB_YESNO):
      if self.prints_form:  # MPSV forms ask before each further page of their PDF; the print dialog shows the whole form once
        return js602.IDNO
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
  if host.file_to_save is not None:
    result["save"] = host.file_to_save
  if host.document_to_open is not None:
    result["open"] = {"url": host.document_to_open, "question": _question_before_opening(host.document_to_open)}
  if host.prints_form:
    result["print"] = True
  if host.closes_window:
    result["close"] = True
  if host.offers_navigation:
    result["navigate"] = True
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


def _run(document, element, script, repe, answers, lookups, retry, uploaded_file=None, before_script=None):
  _use_czech_for_form_texts(document)
  saved_data = copy.deepcopy(document._get_data_root())
  saved_computing_data = copy.deepcopy(document._get_computable_data_root())
  saved_parameters = dict(document.script_named_parameters)
  values_before = _page_values(document)
  data_before = serialized_data(document)
  lookups = _usable_lookups(lookups)
  host = FormActionHost(document, element, repe, answers, lookups, is_started_by_button="button" in retry, uploaded_file=uploaded_file)
  was_script_running = document.script_running
  document.script_running = True
  outcome = None
  try:
    if before_script is not None:
      before_script(host)
    if script is not None:
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


def file_fields(root):
  # Their position is how the page and the engine agree on one: MPSV forms bind the field that loads saved data and the
  # field that takes an attachment to the same place in the data.
  return [element for element in root.iter() if isinstance(element.tag, str)
          and element.get("form602ct") in (ATTACHMENT_FIELD_TYPE, ATTACHMENT_LINK_TYPE) and _data_bind_of(element)]


def name_file_fields_for_page(root):
  for position, element in enumerate(file_fields(root)):
    element.set("blanketa-file-field", str(position))
  for position, element in enumerate(file_list_buttons(root)):
    element.set("blanketa-file-list", str(position))


def file_of_field(document, position, repe):
  # The file a field holds, for the user to save: its name comes from the data and so from whoever filled the form in.
  fields = file_fields(document.root)
  if type(position) is not int or not 0 <= position < len(fields):
    return None
  field = fields[position]
  host = FormActionHost(document, field, repe, [], {}, is_started_by_button=False)
  try:
    content = host._content_of(field)
    name = host._value_of_bind(field.get("filenamebind")) if field.get("filenamebind") else ""
  except (js602.Unsupported, etree.LxmlError, ValueError, TypeError, IndexError):
    return None
  if not content:
    return None
  return (name if ATTACHED_FILE_NAME.fullmatch(name) else NAME_OF_UNNAMED_ATTACHMENT), content


def _field_that_takes_files(document, position):
  fields = file_fields(document.root)
  if type(position) is not int or not 0 <= position < len(fields):
    return None
  field = fields[position]
  takes_files = field.get("form602ct") == ATTACHMENT_FIELD_TYPE and field.get("form602menu") != FIELD_ONLY_GIVES_ITS_FILE
  return field if takes_files else None


def _content_of_chosen_file(file):
  if not isinstance(file, dict) or not isinstance(file.get("name"), str) or not isinstance(file.get("content"), str):
    return None
  if not ATTACHED_FILE_NAME.fullmatch(file["name"]) or len(file["content"]) > MAX_ATTACHED_CHARACTERS:
    return None
  try:
    return base64.b64decode(file["content"], validate=True)
  except (binascii.Error, ValueError):
    return None


def _refusal_of_chosen_file(field, name, content):
  if len(content) > MAX_ATTACHED_BYTES:
    return f"Soubor je větší než {MAX_ATTACHED_BYTES // (1024 * 1024)}\u00a0MB, formulář ho nepřijme."
  allowed = file_kinds.extensions_in_filter(field.get("form602ffilter"))
  if field.get("form602ffiltertype") == "strict" and allowed and file_kinds.extension_of(name) not in allowed:
    return "Formulář tu přijímá jen soubory typu " + ", ".join(sorted(extension.upper() for extension in allowed)) + "."
  return None


def attach_file(document, position, repe, answers, lookups=None, file=None):
  # The file goes into the data and the field's script runs as one whole: a script that stops leaves no file behind.
  field = _field_that_takes_files(document, position)
  content = _content_of_chosen_file(file)
  if field is None or content is None:
    return None
  refusal = _refusal_of_chosen_file(field, file["name"], content)
  if refusal is not None:
    return {"stopped": refusal}
  script = field.get("form602onchange") if is_action_script(field.get("form602onchange")) else None
  return _run(document, field, script, repe, answers, lookups, {"file_field": position, "attach": file},
              before_script=lambda host: host.take_file(file["name"], content))


def _limit_of(element, attribute):
  limit = element.get(attribute) or ""
  return min(int(limit), MAX_ATTACHED_BYTES) if limit.isascii() and limit.isdigit() else MAX_ATTACHED_BYTES


def _size_in_text(size):
  for unit, bytes_in_unit in (("MB", 1024 * 1024), ("kB", 1024)):
    if size >= bytes_in_unit:
      return f"{size / bytes_in_unit:.1f}".replace(".", ",").removesuffix(",0") + "\u00a0" + unit
  return f"{size}\u00a0B"


def _types_in_text(extensions):
  return ", ".join(sorted(extension.upper() for extension in extensions))


def _refusal_of_uploaded_file(button, name, content):
  if len(content) > _limit_of(button, "form602maxsize"):
    return f"Soubor je větší než {_size_in_text(_limit_of(button, 'form602maxsize'))}, formulář ho nepřijme."
  allowed = file_kinds.extensions_in_accept(button.get("form602webaccept"))
  if allowed and file_kinds.extension_of(name) not in allowed:
    return f"Formulář tu přijímá jen soubory typu {_types_in_text(allowed)}."
  return None


def run_button_script(document, position, repe, answers, lookups=None, uploaded_file=None):
  buttons = button_elements(document.root)
  if not 0 <= position < len(buttons):
    return None
  element = buttons[position]
  if isinstance(uploaded_file, dict):
    return _run_with_uploaded_file(document, position, element, repe, answers, lookups, uploaded_file)
  return _run(document, element, element.get("form602btnclick"), repe, answers, lookups, {"button": position})


def _run_with_uploaded_file(document, position, button, repe, answers, lookups, file):
  # A file of any kind for the script of an upload button, which puts it where the form keeps the attachment.
  content = _content_of_chosen_file(file)
  if content is None or button.get("form602ct") != UPLOAD_BUTTON_TYPE:
    return None
  refusal = _refusal_of_uploaded_file(button, file["name"], content)
  if refusal is not None:
    return {"stopped": refusal}

  def name_the_types_the_button_takes(host):
    host.types_the_upload_takes = file_kinds.extensions_in_accept(button.get("form602webaccept"))

  return _run(document, button, button.get("form602btnclick"), repe, answers, lookups, {"button": position, "upload": file},
              uploaded_file=(file["name"], content), before_script=name_the_types_the_button_takes)


def file_list_buttons(root):
  return [element for element in root.iter() if isinstance(element.tag, str) and element.get("form602ct") == FILE_LIST_BUTTON_TYPE]


def _section_and_link_of_list(button):
  try:
    found = button.xpath(button.get("form602attchsec") or "", namespaces=FO_NAMESPACES)
  except (etree.LxmlError, ValueError, TypeError):
    return None
  sections = [element for element in found if etree.iselement(element) and element.get("form602repe")] if isinstance(found, list) else []
  links = sections[0].xpath(".//*[@form602ct=$link]", link=ATTACHMENT_LINK_TYPE) if sections else []
  return (sections[0], links[0]) if links else None


def _sizes_of_listed_files(document, link, outer_repetitions):
  return [int(size) for size in _texts_at(document, link.get("filesizebind"), outer_repetitions) if size.isascii() and size.isdigit()]


def _texts_at(document, bind_id, repetitions):
  bind = document._find_bind_elem(bind_id or "")
  if not len(bind):
    return []
  path = document._get_abs_data_path(bind[0], repetitions)
  return [node if isinstance(node, str) else node.text or "" for node in document._get_data_root().xpath(path, namespaces=document._get_used_ns_prefix(path))]


def _items_holding_a_file(document, section_bind, link, outer_repetitions):
  # A list starts with one empty item, which the first file takes; every other item is somebody's file, whatever it lacks.
  items = len(_texts_at(document, section_bind, outer_repetitions)) or 1
  first_is_empty = not any("".join(_texts_at(document, bind_id, {**outer_repetitions, section_bind: 0}))
                           for bind_id in (_data_bind_of(link), link.get("filenamebind")))
  return 0 if items == 1 and first_is_empty else items


def _refusal_of_file_for_list(button, name, content, sizes_listed, files_listed):
  one, together = _limit_of(button, "form602maxonefilesize"), _limit_of(button, "form602maxallfilessize")
  if len(content) > one:
    return f"Soubor je větší než {_size_in_text(one)}, formulář ho nepřijme."
  allowed = file_kinds.extensions_in_accept(button.get("form602webaccept"))
  if allowed and file_kinds.extension_of(name) not in allowed:
    return f"Formulář tu přijímá jen soubory typu {_types_in_text(allowed)}."
  most = button.get("form602maxoccurs") or ""
  if most.isascii() and most.isdigit() and files_listed >= int(most):
    return f"Formulář přijme nejvýš {most} příloh."
  if sum(sizes_listed) + len(content) > together:
    return f"Přílohy by dohromady měly víc než {_size_in_text(together)}, formulář soubor nepřijme."
  return None


def add_file_to_list(document, position, repe, answers, lookups=None, file=None):
  # Each chosen file gets an item of the repeating section the button names, and the script of the item's link judges it.
  # The item and the script count as one whole, as with a field for a file.
  buttons = file_list_buttons(document.root)
  content = _content_of_chosen_file(file)
  if type(position) is not int or not 0 <= position < len(buttons) or content is None:
    return None
  button = buttons[position]
  section_and_link = _section_and_link_of_list(button)
  if section_and_link is None:
    return None
  section, link = section_and_link
  section_bind = section.get("form602repe")
  outer = {name: index for name, index in _repetitions_of(repe).items() if name != section_bind}
  refusal = _refusal_of_file_for_list(button, file["name"], content, _sizes_of_listed_files(document, link, outer),
                                      _items_holding_a_file(document, section_bind, link, outer))
  if refusal is not None:
    return {"stopped": refusal}
  item = _items_holding_a_file(document, section_bind, link, outer)
  start_script = button.get("form602attchuploadstartaction")

  def put_the_file_into_its_item(host):
    host.types_the_upload_takes = file_kinds.extensions_in_accept(button.get("form602webaccept"))
    if is_action_script(start_script):
      js602.run_form_action(document.script_interpreter(), start_script, host)
    if item > 0:
      host._charge_section_change()
      document.clone_section(section_bind, _repe_text(outer))
    host.take_file(file["name"], content)

  script = link.get("form602onchange") if is_action_script(link.get("form602onchange")) else None
  return _run(document, link, script, _repe_text({**outer, section_bind: item}), answers, lookups,
              {"file_list": position, "attach": file}, before_script=put_the_file_into_its_item)


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
