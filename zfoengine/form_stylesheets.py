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

# Stylesheets a form carries in fm:template, run over its data for the form's own scripts. Forms made for MSXML give them
# helper functions in JScript (msxsl:script); those run in the interpreter of form scripts, never anywhere else.
import functools

import lxml.etree as etree

from . import js602, ns
from .xpath_ext import stylesheet_loads_other_stylesheets

MSXSL_URI = "urn:schemas-microsoft-com:xslt"
SCRIPT_LANGUAGES = {"jscript", "javascript"}


def _to_script_value(value):
  if isinstance(value, bool):
    return value
  if isinstance(value, float):
    return value
  if isinstance(value, list):  # a node-set passed where the function reads a text: XPath's string() of it
    first = value[0] if value else ""
    return "".join(first.itertext()) if etree.iselement(first) else str(first)
  return str(value)


def _to_xpath_value(value):
  if isinstance(value, (bool, float)):
    return value
  if value is js602.UNDEFINED or value is None:
    return ""
  return js602.to_string(value)


def _call(functions, name, _context, *args):
  return _to_xpath_value(functions.call(name, [_to_script_value(arg) for arg in args]))


def _script_functions(template, budget):
  sources = {}
  for script in template.iter("{%s}script" % MSXSL_URI):
    namespace = script.nsmap.get(script.get("implements-prefix"))
    if namespace and (script.get("language") or "").lower() in SCRIPT_LANGUAGES and script.text:
      sources.setdefault(namespace, []).append(script.text)
  extensions = {}
  for namespace, namespace_sources in sources.items():
    functions = js602.StylesheetFunctions(namespace_sources, budget)
    for name in functions.names():
      extensions[(namespace, name)] = functools.partial(_call, functions, name)
  return extensions


def _apply(template, name, source, budget):
  if template is None or stylesheet_loads_other_stylesheets(template):
    raise js602.Unsupported(f"šablona {name}")
  try:
    # without DENY_ALL a form's stylesheet reads files and addresses with document() and writes files with exsl:document
    stylesheet = etree.XSLT(template, access_control=etree.XSLTAccessControl.DENY_ALL, extensions=_script_functions(template, budget))
    return stylesheet(source)
  except (etree.XSLTParseError, etree.XSLTApplyError) as error:
    raise js602.Unsupported(f"šablona {name}") from error


def transform(document, name, budget):
  return _apply(document.get_template_text(name), name, document._get_data_root(), budget)


def transform_list(form_root, name, enum):
  '''
  A list the form downloads, in the shape its own stylesheet (fm:enum/@updatexsl) gives it, as Form Filler does after
  the download.
  '''
  templates = form_root.xpath("//fm:template[@name=$name]", namespaces={"fm": ns.FM_URI}, name=name)
  template = templates[0][0] if templates and len(templates[0]) else None
  return _apply(template, name, etree.ElementTree(enum), js602.Budget()).getroot()
