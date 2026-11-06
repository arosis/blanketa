import io
import logging
import re
from typing import NamedTuple

from lxml import etree
from pypdf import PdfReader
from pypdf.errors import PyPdfError
from pypdf.generic import (
    ArrayObject,
    DictionaryObject,
    IndirectObject,
    NameObject,
    TextStringObject,
    read_object,
)

XFDF_NAMESPACE = "http://ns.adobe.com/xfdf/"
FDF_HEADER = b"%FDF-"
FDF_HEADER_SEARCH_LENGTH = 1024
FDF_OBJECT_KEYWORD = re.compile(rb"\bobj\b")
PDF_WHITESPACE = b" \t\r\n\f\x00"
PDF_DIGITS = b"0123456789"
MAX_FIELD_DEPTH = 32
FIELD_FLAG_READ_ONLY = 1
FIELD_FLAG_RADIO = 1 << 15
FIELD_FLAG_PUSHBUTTON = 1 << 16
FIELD_FLAG_EDITABLE_CHOICE = 1 << 18
UNCHECKED_STATES = ("", "Off")

# pypdf logs every object it cannot read; a damaged or hostile data file would flood the log.
logging.getLogger("pypdf").setLevel(logging.ERROR)


class FormField(NamedTuple):
    kind: str | None
    is_read_only: bool
    offered_values: frozenset | None

    def offers(self, text):
        is_unselected_button = self.kind in ("checkbox", "radio") and text in UNCHECKED_STATES
        is_empty_choice = self.kind == "choice" and text == ""
        return self.offered_values is None or text in self.offered_values or is_unselected_button or is_empty_choice


class _FdfObjects:
    strict = False

    def __init__(self, objects_by_number):
        self.objects_by_number = objects_by_number

    def get_object(self, reference):
        return self.objects_by_number.get(reference.idnum)


def _field_tree(values):
    tree = {}
    for name, value in values.items():
        node = tree
        *parents, last = name.split(".")
        for part in parents:
            node = node.setdefault(part, {}).setdefault("kids", {})
        node.setdefault(last, {})["value"] = value
    return tree


def _text(value):
    if isinstance(value, NameObject):
        return str(value).lstrip("/")
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)


def _value_texts(value):
    return [_text(item) for item in value] if isinstance(value, list) else [_text(value)]


def write_xfdf(values, pdf_name):
    root = etree.Element(f"{{{XFDF_NAMESPACE}}}xfdf", nsmap={None: XFDF_NAMESPACE})
    root.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    etree.SubElement(root, f"{{{XFDF_NAMESPACE}}}f", href=pdf_name)

    def add_fields(parent, tree):
        for part, node in tree.items():
            field = etree.SubElement(parent, f"{{{XFDF_NAMESPACE}}}field", name=part)
            if "value" in node:
                for text in _value_texts(node["value"]):
                    etree.SubElement(field, f"{{{XFDF_NAMESPACE}}}value").text = text
            add_fields(field, node.get("kids", {}))

    add_fields(etree.SubElement(root, f"{{{XFDF_NAMESPACE}}}fields"), _field_tree(values))
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True).decode("utf-8")


def _fdf_value(value):
    if isinstance(value, list):
        return ArrayObject(_fdf_value(item) for item in value)
    if isinstance(value, NameObject):
        return value
    return TextStringObject(_text(value))


def write_fdf(values, pdf_name):
    def fdf_fields(tree):
        result = ArrayObject()
        for part, node in tree.items():
            field = DictionaryObject({NameObject("/T"): TextStringObject(part)})
            if "value" in node:
                field[NameObject("/V")] = _fdf_value(node["value"])
            if node.get("kids"):
                field[NameObject("/Kids")] = fdf_fields(node["kids"])
            result.append(field)
        return result

    catalog = DictionaryObject({NameObject("/FDF"): DictionaryObject({
        NameObject("/F"): TextStringObject(pdf_name), NameObject("/Fields"): fdf_fields(_field_tree(values))})})
    out = io.BytesIO()
    out.write(b"%FDF-1.2\n1 0 obj\n")
    catalog.write_to_stream(out)
    out.write(b"\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
    return out.getvalue().decode("ascii")


def _read_xfdf(data):
    try:
        root = etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False))
    except etree.XMLSyntaxError as error:
        raise ValueError(f"Soubor není platné XFDF: {error}") from None
    if etree.QName(root).localname != "xfdf":
        raise ValueError("Soubor není FDF ani XFDF.")
    values = {}

    def collect(element, prefix, depth):
        for field in element:
            if not isinstance(field.tag, str) or etree.QName(field).localname != "field" or depth > MAX_FIELD_DEPTH:
                continue
            name = prefix + field.get("name", "")
            texts = [value.text or "" for value in field if isinstance(value.tag, str) and etree.QName(value).localname == "value"]
            if texts:
                values[name] = texts if len(texts) > 1 else texts[0]
            collect(field, name + ".", depth + 1)

    for fields in root:
        if isinstance(fields.tag, str) and etree.QName(fields).localname == "fields":
            collect(fields, "", 0)
    return values


def _start_of_run(data, end, characters):
    start = end
    while start > 0 and data[start - 1] in characters:
        start -= 1
    return start


def _end_of_run(data, start, characters):
    end = start
    while end < len(data) and data[end] in characters:
        end += 1
    return end


# "<number> <generation> obj" is read backwards from the keyword. A regular expression over both numbers would retry
# every digit of a long run in a hostile file, which makes the search quadratic.
def _object_number_before(data, keyword_start):
    generation_end = _start_of_run(data, keyword_start, PDF_WHITESPACE)
    generation_start = _start_of_run(data, generation_end, PDF_DIGITS)
    number_end = _start_of_run(data, generation_start, PDF_WHITESPACE)
    number_start = _start_of_run(data, number_end, PDF_DIGITS)
    has_both_numbers = number_start < number_end < generation_start < generation_end < keyword_start
    return int(data[number_start:number_end]) if has_both_numbers else None


def _fdf_objects(data):
    objects_by_number = {}
    resolver = _FdfObjects(objects_by_number)
    for match in FDF_OBJECT_KEYWORD.finditer(data):
        number = _object_number_before(data, match.start())
        if number is None:
            continue
        stream = io.BytesIO(data)
        stream.seek(_end_of_run(data, match.end(), PDF_WHITESPACE))
        try:
            objects_by_number[number] = read_object(stream, resolver)
        # A hostile file can nest arrays deep enough to exhaust the parser's recursion.
        except (PyPdfError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
            continue
    return objects_by_number


def _resolved(value, depth=0):
    while isinstance(value, IndirectObject) and depth < MAX_FIELD_DEPTH:
        value, depth = value.get_object(), depth + 1
    return None if isinstance(value, IndirectObject) else value


def _plain_value(value):
    value = _resolved(value)
    if isinstance(value, NameObject):
        return str(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, ArrayObject):
        items = [_plain_value(item) for item in value]
        return [item for item in items if isinstance(item, str)]
    return None


def _read_fdf(data):
    fdf = next((_resolved(found.get("/FDF")) for found in _fdf_objects(data).values()
                if isinstance(found, DictionaryObject) and "/FDF" in found), None)
    if not isinstance(fdf, DictionaryObject):
        raise ValueError("Soubor FDF neobsahuje žádná data formuláře.")
    values, seen = {}, set()

    def collect(fields, prefix, depth):
        fields = _resolved(fields)
        if not isinstance(fields, ArrayObject) or depth > MAX_FIELD_DEPTH:
            return
        for item in fields:
            field = _resolved(item)
            if not isinstance(field, DictionaryObject) or id(field) in seen:
                continue
            seen.add(id(field))
            part = _resolved(field.get("/T"))
            name = prefix + str(part) if isinstance(part, str) else prefix.rstrip(".")
            if "/V" in field and (value := _plain_value(field["/V"])) is not None and name:
                values[name] = value
            collect(field.get("/Kids"), name + "." if name else "", depth + 1)

    collect(fdf.get("/Fields"), "", 0)
    return values


def read_form_data(data):
    if FDF_HEADER in data[:FDF_HEADER_SEARCH_LENGTH]:
        return _read_fdf(data)
    return _read_xfdf(data)


def _field_kind(field):
    field_type, flags = field.get("/FT"), int(field.get("/Ff") or 0)
    if field_type == "/Btn":
        return "pushbutton" if flags & FIELD_FLAG_PUSHBUTTON else "radio" if flags & FIELD_FLAG_RADIO else "checkbox"
    return {"/Tx": "text", "/Ch": "choice", "/Sig": "signature"}.get(field_type)


def _option_value(option):
    option = _resolved(option)
    return _text(_resolved(option[0])) if isinstance(option, ArrayObject) and option else _text(option)


def _offered_values(field, kind):
    if kind in ("checkbox", "radio"):
        return frozenset(_text(state).lstrip("/") for state in field.get("/_States_") or []) - {"Off"} or None
    if kind == "choice" and not int(field.get("/Ff") or 0) & FIELD_FLAG_EDITABLE_CHOICE:
        return frozenset(_option_value(option) for option in _resolved(field.get("/Opt")) or [])
    return None


def form_fields(pdf):
    fields = {}
    for name, field in (PdfReader(io.BytesIO(pdf)).get_fields() or {}).items():
        kind = _field_kind(field)
        fields[name] = FormField(kind, bool(int(field.get("/Ff") or 0) & FIELD_FLAG_READ_ONLY), _offered_values(field, kind))
    return fields


def is_fillable_kind(kind):
    return kind in ("text", "choice", "checkbox", "radio")


def form_data_values(pdf):
    values = {}
    for name, field in (PdfReader(io.BytesIO(pdf)).get_fields() or {}).items():
        kind, value = _field_kind(field), field.get("/V")
        if not is_fillable_kind(kind) or value is None:
            continue
        values[name] = NameObject("/" + _text(value).lstrip("/")) if kind in ("checkbox", "radio") else value
    return values


def single_text(kind, value):
    if isinstance(value, list):
        if len(value) > 1:
            return None
        value = value[0] if value else ""
    return value.lstrip("/") if kind in ("checkbox", "radio") else value


def page_value(kind, text):
    return text not in UNCHECKED_STATES if kind == "checkbox" else text
