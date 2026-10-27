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

'''
Module that handles evaluation of calculate/readonly/relevant and other attributes.
'''
import logging
log = logging.getLogger("zfo.evaluate")
import expr_quoting
import math
import tools

import lxml.etree as etree

from xpath_ext import StopEvalException


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
      return  (b_id, str(res), str(res))
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
  else:   
    
    return (b_id, res.encode("utf-8"), res.encode("utf-8"))

def eval_action_command(zfo_doc, expression):
  '''
  Evaluates 'click' expression.
  xpath function that is called is usually
  SendAppEvent or LastParam.
  SendAppEvent returns string with info message
  '''
  try:
    quoted = expr_quoting.quote_expression(expression)
    quoted = unicode(quoted)    
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
  if (type(data_node) is etree._ElementStringResult) \
      or (type(data_node) is etree._ElementUnicodeResult):
    parent = data_node.getparent()
    
    xpath_expr = _replace_string_length_call(xpath_expr)
                
    tmp = tools.create_temp_elem(parent, data_node)
    res = tmp.xpath(xpath_expr, namespaces= nsmap)
    
    tools.delete_temp_elem(parent, tmp)
    return res
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
    res = tmp.xpath(xpath_expr, namespaces= nsmap)
    
    tools.delete_temp_elem(parent, tmp)
    return res
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
  if bind.attrib.has_key("constraint"):       
    expr = bind.get("constraint")
    if len(expr) == 0:
      log.debug("Empty constraint = no constraint")
      return True, constr_comment
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
      return False 
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
  if bind.attrib.has_key("relevant"):
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
  if bind.attrib.has_key("required"):
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
  
  if bind.attrib.has_key("readonly"):
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

