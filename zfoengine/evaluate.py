#*    zfoeditor - an editor of ZFO documents
#*    Copyright (C) 2009-2010  CZ.NIC, z.s.p.o. (http://www.nic.cz)
#*
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
#*    You should have received a copy of the GNU Library General Public
#*    License along with this library; if not, write to the Free
#*    Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
#*
#*    Modified in October 2026 by Richard Winkler Bejr for "Blanketa": ported to Python 3,
#*    separated from the GTK user interface and extended (recalculation, form check, default
#*    values, scripts); the git history of the project records each change.

'''
Module that handles evaluation of calculate/readonly/relevant and other attributes.
'''
import logging

log = logging.getLogger("zfo.evaluate")
import math

import lxml.etree as etree

from . import expr_quoting, js602, tools
from .xpath_ext import StopEvalException

MAX_RUNAWAY_SCRIPTS = 3  # a form whose scripts keep spending their whole budget would freeze the window with each change


def _script_answer(res):
  '''What DataXpath gives a script for a result of an xpath evaluation.'''
  if isinstance(res, list):
    if not res:
      return ""
    res = res[0]
    if isinstance(res, etree._Element):
      return res.text or ""
  if isinstance(res, bool) or res is None:
    return res
  if isinstance(res, float):
    return res
  return str(res)


def script_data_query(zfo_doc, path=None, node=None):
  def ask(expression):
    try:
      if node is None:
        return _script_answer(eval_expression_path(zfo_doc, expression, path))
      return _script_answer(eval_expression_node(zfo_doc, expression, node, zfo_doc._get_used_ns_prefix(expression)))
    except (etree.LxmlError, ValueError, TypeError, IndexError) as e:
      raise js602.Unsupported("DataXpath") from e
  return ask


def script_list_item_query(ask):
  def list_item(*args):
    if len(args) < 2 or any("'" in arg for arg in args):
      raise js602.Unsupported("GetListItemData")
    quoted = ", ".join("'%s'" % arg for arg in args)
    answer = _script_answer(ask("GetListItemData(%s)" % quoted))
    return "" if answer is None else js602.to_string(answer)
  return list_item


def eval_script(zfo_doc, source, path=None, node=None):
  '''
  Runs a bind's JavaScript. Returns the text it gave to Result, or None when it gave nothing or could not finish;
  that is noted in zfo_doc.script_problems and the value stays as it was.
  '''
  if zfo_doc.runaway_scripts >= MAX_RUNAWAY_SCRIPTS:
    return None

  ask = script_data_query(zfo_doc, path, node)
  list_item = script_list_item_query(ask)
  current = path or ""
  if not current and isinstance(node, etree._Element):
    current = node.getroottree().getpath(node)
  was_script_running = zfo_doc.script_running
  zfo_doc.script_running = True
  try:
    return js602.evaluate(zfo_doc.script_interpreter(), source, ask, current, list_item)
  except js602.Exhausted:
    zfo_doc.runaway_scripts += 1
    zfo_doc.script_problems.add("výpočet nedoběhl")
  except js602.Unsupported as e:
    zfo_doc.script_problems.add(str(e))
  except js602.Thrown as e:
    log.info("Script stopped with its own error: %s", e)
    zfo_doc.script_problems.add("chyba ve skriptu formuláře")
  finally:
    zfo_doc.script_running = was_script_running
  return None


def eval_calculate(zfo_doc, bind, path=None, node=None, nsmap=None):
  '''
  Evaluates calculate attribute on a bind.
  bind argument is a bind element, path is absolute path to the data element
  in the calculate data tree.
  Returns tuple (bind_id, evaluated_value, value to store to the data model).
  If there is no calculate attribute or is empty, 
  returns None.
  '''
  xpath_expr = bind.get("calculate")
  # if bind does not have any calculate attribute, continue
  if xpath_expr is None:
    return None
  if xpath_expr.strip() == "":
    return None
  
  if js602.is_script(xpath_expr):
    res = eval_script(zfo_doc, xpath_expr, path, node)
    return None if res is None else (bind.get("Id"), res, res)

  # calculate attribute is enclosed in quots - see 602 xpath documentation
  unquoted = xpath_expr
  
  try:
    if node is None:
      res = eval_expression_path(zfo_doc, xpath_expr, path)  
    if path is None:      
      res = eval_expression_node(zfo_doc, xpath_expr, node, nsmap)      
  except Exception as e:
    log.error(e)        
    log.error("Unable to evaluate expression (skipping): %s . Unquoted version: %s" % (xpath_expr, unquoted))
    return None
  # store calculated values
  b_id = bind.get("Id")
  log.debug("Calculated value for %s: %s" %(b_id, res)) 
  # is the result of type float?
  if type(res) is float:
    # store value if it is not a nan
    if not math.isnan(res):      
      return (b_id, js602.number_text(res), js602.number_text(res))
  elif type(res) is list:
    if len(res) == 0:     
      return (b_id, '', '')
    else:
      val = res[0]
      if type(val) is etree._Element:
        if val.text is None:
          val = ""
        else:
          val = val.text
      
      return (b_id, val, val)
    
  elif type(res) is bool:
    to_write = 'false'
    if res:
      to_write = 'true'       
    
    return (b_id, res, to_write)
  elif res is None:
    # zfoengine: an extension function returned nothing (Py2 crashed on None.encode)
    return None
  else:   
    # zfoengine: Py2 encoded to utf-8 bytes here; in Py3 values stay str
    res = str(res)
    return (b_id, res, res)

def eval_action_command(zfo_doc, expression):
  '''
  Evaluates 'click' expression.
  xpath function that is called is usually
  SendAppEvent or LastParam.
  SendAppEvent returns string with info message
  '''
  try:
    quoted = expr_quoting.quote_expression(expression)
    quoted = str(quoted)
    # actions are evaluated upon data root
    root = zfo_doc._get_data_root()
    nsmap = zfo_doc._get_used_ns_prefix(quoted)
    res = root.xpath(quoted, namespaces=nsmap)
    if not isinstance(res, list):
      res = [res]
    return res
  except StopEvalException:
    log.info("Command execution stopped due to Stop call")
    return []
  

def _replace_string_length_call(xpath_expr):
  '''
  Replaces calls to string-length function with call to special string-length function
  implementation, which skips temporary nodes. 
  '''
  if "string-length(" in xpath_expr:
      return xpath_expr.replace("string-length(", "TempNodeContextStringLength(")
  else:
    return xpath_expr
  
def eval_expression_node(zfo_doc, xpath_expr, data_node, nsmap):
  '''
  Evaluates xpat_expr on a specified node. nsmap contains map
  of ns prefixes used in expression (maybe move nsmap away)
  '''
  xpath_expr = expr_quoting.quote_expression(xpath_expr)
  # zfoengine: lxml 5+ has only _ElementUnicodeResult (string results of attribute/text xpaths)
  if isinstance(data_node, etree._ElementUnicodeResult):
    parent = data_node.getparent()
    
    xpath_expr = _replace_string_length_call(xpath_expr)
                
    tmp = tools.create_temp_elem(parent, data_node)
    # zfoengine: an expression that fails must not leave the helper in data that get saved
    try:
      return tmp.xpath(xpath_expr, namespaces= nsmap)
    finally:
      tools.delete_temp_elem(parent, tmp)
  else:
    res = data_node.xpath(xpath_expr, namespaces= nsmap)        
    return res
    

def eval_expression_path(zfo_doc, xpath_expr, data_path):
  '''
  Evaluates boolean expression on a node specified by data_path 
  - used in 'constraint' and 'relevant'  attributes
  '''
  log.debug("Evaluating expression %s on %s" % (xpath_expr, data_path ))
  xpath_expr = expr_quoting.quote_expression(xpath_expr)
  dr = zfo_doc._get_computable_data_root()
  
  nsmap = zfo_doc._get_used_ns_prefix(data_path)
  elem = dr.xpath(data_path, namespaces= nsmap)            
  elem = elem[0] if len(elem) else None
  # if the element in data model is not found, it wasn't stored at
  # last form edit action. That means, it is not relevant, so we
  # do not care about it - return None and above layer has to ensure the
  # semantics of "element we do not care about"
  if elem is None:
    return None
  # if found element is an attribute, we must run xpath with it as context
  # lxml cannot run xpath with attribute as a context
  # workaround: add temporal element, containing only text value which
  # is the same as the attribute value, and run xpath against it
  # Remove the temporal element afterwards        
  if (data_path.find('@')) >= 0:
    parent = elem.getparent()
                 
    tmp = tools.create_temp_elem(parent, elem)
    xpath_expr = _replace_string_length_call(xpath_expr)
    # zfoengine: an expression that fails must not leave the helper in data that get saved
    try:
      return tmp.xpath(xpath_expr, namespaces= nsmap)
    finally:
      tools.delete_temp_elem(parent, tmp)
  else:
    res = elem.xpath(xpath_expr, namespaces= nsmap)        
    return res

def eval_constraint(zfo_doc, bind, path, node=None):
  '''
  Evaluates constraint (if any) of bind element.
  Paths in constraint are relative to the data path which is bind referencing.
  if node is specified, path is ignored and constraint is evaluated on the node
  Returns True or False - result of evaluation
  '''
  constr_comment = bind.get("constraintcomment", default="")
  if ("constraint" in bind.attrib):       
    expr = bind.get("constraint")
    if len(expr) == 0:
      log.debug("Empty constraint = no constraint")
      return True, constr_comment
    if js602.is_script(expr):
      # a script that cannot finish must not stop the user: the form's notice says that it was not checked
      if eval_script(zfo_doc, expr, path, node) == "false":
        return False, constr_comment
      return True, ""
    log.debug("Constraint expression: %s" % expr)
    if node is None:
      res = eval_expression_path(zfo_doc, expr, path)
    else:
      nsmap = zfo_doc._get_used_ns_prefix(expr)
      res = eval_expression_node(zfo_doc, expr, node, nsmap)
    log.debug("Constraint evaluated to %s" % res)
    if res is None:
      # if result is None, data element was not in the data model
      # that means - constraint is not not fulfilled
      # zfoengine: original returned bare False here, which callers could not unpack
      return False, constr_comment
    # if the constraint is ok (True), do not return constraint comment
    if res:
      return res, ""
    else:
      return res, constr_comment
  else:
    log.debug("No constraint ")
    return True, ""

def eval_relevant(zfo_doc, bind, path):
  '''
  Evaluates constraint (if any) of bind element.
  Paths in contraint are relative to the data path which is bind referencing.
  Returns True or False - result of evaluation
  '''
  if ("relevant" in bind.attrib):
    expr = bind.get("relevant")
    log.debug("'Relevant' expression: %s" % expr)
    res = eval_expression_path(zfo_doc, expr, path)
    log.debug("'Relevant' evaluated to %s" % res)
    if res is None:
      # if the value is None, data element was not in 
      # data tree, which means that evaluated value is not relevant
      return False
    return res        
  else:
    log.debug("No 'relevant' attribute")
    return False

def eval_required(zfo_doc, bind, path):
  '''
  Evaluates constraint (if any) of bind element.
  Paths in contraint are relative to the data path which is bind referencing.
  Returns True or False - result of evaluation
  '''
  if ("required" in bind.attrib):
    expr = bind.get("required")
    if len(expr) == 0:
      # empty string as  value of required att - so it is required
      log.debug("Empty 'required' attribute - returning True")
      return True
    log.debug("'Required' expression: %s" % expr)
    res = eval_expression_path(zfo_doc, expr, path)
    log.debug("'Required' evaluated to %s" % res)
    if res is None:
      # if the value is None, data element was not in 
      # data tree, which means that evaluated value is not required
      return False
    return res        
  else:
    #log.debug("No 'required' attribute")
    return False
  
def eval_readonly(zfo_doc, bind, path):
  '''
  Evaluates constraint (if any) of bind element.
  Paths in constraint are relative to the data path which is bind referencing.
  Returns True or False - result of evaluation
  '''
  
  if ("readonly" in bind.attrib):
    expr = bind.get("readonly")
    if len(expr) == 0:
      # empty string as  value of readonly att - so it is required
      log.debug("Empty 'readonly' attribute, returning False");
      return False
    
    log.debug("'Readonly' expression: %s" % expr)
    res = eval_expression_path(zfo_doc, expr, path)
    log.debug("'Readonly; evaluated to %s" % res)
    if res is None:
      # if the value is None, data element was not in 
      # data tree, which means that evaluated value should be readonly
      return True
    return res        
  else:
    # if does not have readonly attribute, must have depends attribute
    # because we chose elements with readonly or calculate att
    #log.debug("No 'readonly' attribute")
    calc = bind.get("calculate").strip()
    if calc == "":
      return False
    else:
      return True

