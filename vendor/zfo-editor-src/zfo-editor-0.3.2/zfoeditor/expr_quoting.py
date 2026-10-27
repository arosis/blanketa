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
Module used to quote an xpath expressions.
'''

import re 
import logging
log = logging.getLogger("zfo.expr_quoting")

def quote_expression(expr):
  '''
  Parses the xpath expression and processes each extension function
  by putting its arguments into quotes.
  '''  
  # get the strings in quotes - extension functions  
  regexp = '\".+\)\"'
  res = ""
  end_match = 0
  iters = re.finditer(regexp, expr)
  for it in iters:
    res = res + expr[end_match : it.start()]
    end_match = it.end()
      
    func = expr[it.start(): it.end()]    
    quoted = _add_arg_quotes(func)
    
    res = res + quoted
  
  # if there is something left, add it at the end
  if len(expr) > end_match:
    res = res + expr[end_match:]
  if res == "":
    return expr
  return res

'''
Goes through the function arguments, strips the {} from it and either put argument
into quotes or run itself recursively on argument, if the argument is
a function call. 
'''
def _add_arg_quotes(func):
  # if there are quotes around, strip them
  _str = func.rstrip('"').lstrip('"')  
  open_parenth = _str.find("(")
  close_parenth = _str.rfind(")")
  
  funcname = _str[0:open_parenth].strip() 
  funcargs = _str[open_parenth + 1: close_parenth].strip()
  
  log.debug("Escaping function name: %s" % funcname)
  is_ext_fun = funcname[0].isupper()
  log.debug("Function %s is an extension function: %s" % (funcname, is_ext_fun))
  
  res = []
  args = []
  commas = _find_top_commas(funcargs)
  last = 0
  for comma in commas:
    arg = funcargs[last: comma]    
    last = comma + 1
    arg = arg.strip()#.rstrip("}").lstrip("{")
    args.append(arg)
  if last < len(funcargs):
    arg = funcargs[last:].strip()
    #arg = arg.rstrip("}").lstrip("{")
    args.append(arg)
  # process all arguments - either put " around them, or escape it recursively
  for arg in args:
    # if the argument is closed in curly bracket, it should be passed
    # to extension function unchanged - just replace curly brackets
    # with quotes - inner quotes are written as html entities (already in the form)
    if arg.startswith("{") and arg.endswith("}"):
      arg = arg.replace('"', "'")
      arg = '"' + arg.lstrip("{").rstrip("}") + '"'
      res.append(arg)
      continue    
    oidx = arg.find('(')
    # this could be also an expr in () - therefore oidx > 0, to have at least one char before (    
    if oidx > 0:
      arg_fname = arg[:oidx]
      bad_chars = re.findall('[^a-zA-Z\-_]', arg_fname)
      is_funcall = (len(bad_chars) == 0)
    else:
      is_funcall = False
      
    if not is_funcall:      
      if is_ext_fun:
        arg = arg.replace('"', "'")
        r = '"' + arg + '"'        
      else:
        r = arg
        
      res.append(r)
    else:
      # function call, recurse
      r = _add_arg_quotes(arg)
      res.append(r)
    
  quoted_args = ','.join(res)
  new_expr = funcname + "(" + quoted_args + ")"
  return new_expr

  
def _find_top_commas(str):
  '''
  Returns list of indexes of commas, that are not inside any parenthesis
  '''
  res = []
  counter = 0
  nested = 0
  curly_level = 0
  for char in str:    
    # inside curly brackets ignore all until curly bracket is closed
    if char == "{":
      curly_level += 1
    if char == "}" and curly_level == 1:
      curly_level = 0
      counter += 1
      continue
    if curly_level:
      counter += 1
      continue
    
    if char == ',':
      if not nested:
        res.append(counter)
    
    if (char in "('"):
      nested = nested + 1
    if (char in ")'"):
      nested = nested -1
      
    counter = counter + 1
  
  return res



def __testme():
  s1 = '1+2+3+"GetListItem(a,DataXpath(If(a = "a"),dodo, 2),c, number(../aa/ss))"'
  print s1 + ' => ' +quote_expression(s1)
  
  s2 = '../dap/@aa'
  print s2 + ' => ' +quote_expression(s2)
  
  s3 = '../dap/@aa + "DataXPath({a},{b},{c})"'
  print s3 + ' => ' +quote_expression(s3)
  
  s4 = '"If(DataXpath({../d:r5b = "ano"}), 00.0, DataXpath({/d:root/d:r55_sl1b}))"'
  print s4 + ' => ' +quote_expression(s4)
  
  s5 = '"DataXpath({(/d:root/d:r37_sl1 > 0) * /d:root/d:r37_sl1})"'
  print s5 + ' => ' +quote_expression(s5)
  
  s6 = '"StrCat({&quot;ReadSth(},f(),{,&quot;sec&quot;)})"'
  print s6 + ' => ' +quote_expression(s6)
  
  s7 = '"F({a(},f(),{)})"'
  print s7 + ' => ' +quote_expression(s7)

if __name__ == "__main__":
  __testme()