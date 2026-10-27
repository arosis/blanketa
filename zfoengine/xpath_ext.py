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
#*    Modified in October 2026 by Richard Winkler Bejr for "ZFO na Macu": ported to Python 3,
#*    separated from the GTK user interface and extended (recalculation, form check, default
#*    values); the git history of the project records each change.

'''
Module with implementation of XPath extension used in ZFO by 602.
'''
import os
import os.path
import datetime
import lxml.etree as etree
import hashlib
import getpass
from functools import reduce

from . import ns
from . import tools
from . import msg_types
from . import tags
import copy

from . import app_events
import logging
log = logging.getLogger("zfo.xpath_ext")


class StopEvalException(Exception):
  '''
  Exception that represents end of evaluation of compound command.
  '''
  pass

class ExtendedXpathEvaluator:
  '''
  Implements XPath extensions defined by 602.
  '''
    
  def __init__(self, doc):
    '''
    Initialize evaluator. Set the working document.
    '''
    self._doc = doc
    
  def TempNodeContextStringLength(self, context, *args):
    """
    Calculates string-length in case it is called from the context of the temporary element.
    """
    
    def count_length(node):
      l = 0
      if node.text is not None:
        l = l + len(node.text.strip())
      if node.tail is not None:
        l = l + len(node.tail.strip())
      for child in node:
        if child.tag != "some-foo-tmp-elem-whose-name-is-not-among-children":
          l = l + count_length(child)
      return l
    
    if len(args) == 0:
      return count_length(context.context_node)
    elem = args[0][0]
    if etree.iselement(elem):
      return count_length(elem)
    else:
      return len(str(args[0]))
    
  def RCCheck(self, context, rc):
    '''
    "Rodne cislo" check - must have 9 or 10 digits and be dividable by 11
    '''
    rc = rc.replace('/', '').strip()
    if (len(rc) < 9) or (len(rc)>10):
      log.debug("RCCheck failed - has less than 9 or more than 10 digits")
      return False
    rc = int(rc)
    if rc%11:
      log.debug("RCCheck failed - not dividable by 11")
      return False
    return True
    
    
  
  def GetListItemData(self, context, list_name, data_xpath, attr=None, elem=None):    
    nss = {"fm": ns.FM_URI}
    lst = self._doc.root.xpath("//fm:enum[@name='" + list_name + "']", namespaces = nss)[0]
    
    data = context.context_node.xpath(data_xpath, 
                                      namespaces = self._doc._get_used_ns_prefix(data_xpath))[0]
    
    if type(data) is etree._Element:
      data = data.text
    
    
    items = lst.xpath("fm:list/fm:listitem[normalize-space(@value)='"+data+"']", namespaces = nss)
    if len(items) == 0:
      items = lst.xpath("fm:list/fm:listitem[normalize-space(@itemtext)='"+data+"']", namespaces = nss)
    item = items[0]
    
    if (attr.startswith("@")):
      val = item.get(attr[1:])
    else:
      val= item.xpath(attr, namespaces = nss)[0].text
    
    return val
  
  def DataXpath(self, context, xpath_expr):
    return self.DataXPath(context, xpath_expr)
  
  def DataXPath(self, context, xpath_expr):
    if not isinstance(xpath_expr, str):
      # expression was already evaluated as it was standard xpath
      return  xpath_expr
    expr = xpath_expr.lstrip('{').rstrip('}')
    
    data = context.context_node.xpath(expr, 
                                      namespaces = self._doc._get_used_ns_prefix(expr))
    potential = data
    # if the result is list, return first item or none
    if type(data) is list:
      if len(data) > 0:
        potential =  data[0]
      else:
        return None
    # otherwise return the result of evaluation    
    
    if type(potential) is etree._Element:
      if potential.text is None:
        return ""
      return potential.text
    else:
      return potential
  
  def If(self, context, condition, passed, failed):    
    if condition:
      return passed
    else:
      return failed
    
  def IsEmpty(self, context, str):
    '''
    Evaluate "str" parameter?
    '''
    if str is None:
      return True
    if str == "":
      return True
    return False
  
  def StrCat(self, context, *args):
    result = ""
    for arg in args:      
      # zfoengine: Py3 does not concatenate str with numbers/node-sets
      if isinstance(arg, list):
        arg = arg[0] if len(arg) else ""
        if etree.iselement(arg):
          arg = arg.text or ""
      if isinstance(arg, float) and arg.is_integer():
        arg = int(arg)
      result = result + str(arg)
    return result
  
  def StrCmp(self, context, s1, s2):
    s1 = s1.strip('{}')
    s2 = s2.strip('{}')
    return s1 == s2
  
  def Round(self, context, number, accuracy):
    acc = float(accuracy)
    num = float(number)
    res = round(num, acc)
    return res
  
  def And(self, context, op1, op2):
    return op1 and op2
  
  def Or(self, context, *args):
    '''
    Or should take only 2 args, but there are forms that have more arguments. 
    '''    
    return reduce(lambda x,y: x or y, args)
  
  def Not(self, context, op):  
    return not op
  
  def CallDll(self, context, *args):
    log.warning("CallDll won't be implemented")
    return ""
  
  def CmdLineParam(self, context):
    log.warning("CmdLineParam not implemented")
    return ""

  def CurrentUserName(self, context):
    # zfoengine: os.getlogin() fails without a controlling terminal
    return getpass.getuser()
  
  def CurrentFileName(self, context):    
    return self._doc.opened_from_path
    
  def CurrentDate(self, context, format=None):
    return datetime.datetime.now().strftime("%Y-%m-%d")
  
  def CurrentTime(self, context, format=None):
    return tools.get_curr_time(False, format) 
    
  def CurrentTimeUTC(self, context, format=None):
    return tools.get_curr_time(True, format) 
    
    
  def FormID(self, context):
    return self._doc.form_id
    
  
  def LastParam(self, context, *params):  
    '''
    put the subcommands returns value in reversed order,
    if the LastParam is accessed from calculate method,
    first element is accessed (that means - return value of last command).
    If LastParam is called from onclick action, whole node is used
    '''    
    results = etree.Element(tags.LASTPARAM_RESULT_TAG)
    cn = context.context_node
    try:
      for param in params:
        # if parameter is extension function (is quoted). it must be evaluated
        if str(param).startswith('"'):                
          results.insert(0, cn.xpath(str(param)))
        else:
          # NOTE: deepcopy of object because of segmentation fault occuring in lxml
          # (just shot in the dark...)
          # Maybe there is some problem that xpath functions try to free
          # object, which was already free-ed?
          # Segfault occurs in libc free() call. 
          if isinstance(param, list):
            if len(param) > 0:        
              results.insert(0, copy.deepcopy(param[0]))
          elif isinstance(param, etree._Element):
            results.insert(0, copy.deepcopy(param))
          else:
            node = etree.Element(tags.SUBCMD_RETVAL_TAG)
            node.set("msg_type", msg_types.INFO)
            node.text = str(param)
            results.insert(0, node)
    except StopEvalException:
      log.info("StopEvalException caught, stopping execution of command chain")
      return results
    # return node which child nodes contains return return values
    # of subcommands as their texts   
    return results
  
  def GetNumSigns(self, context):
    '''
    Returns number of signatures in the form.
    '''
    sigs = self._doc.root.xpath("//dsig:Signature", namespaces={"dsig": ns.DSIG_URI})
    return len(sigs)
  
  def GetEnvVariable(self, context, var_name):
    try:
      return os.environ[var_name]
    except:
      return ""
    
  def GetSignProperty(self, context, sigIndex, sigParam, oid):
    log.warning("GetSignProperty not implemented")
    return ""
  
  def GetDataStr(self, context, xpath_exclude):
    '''
    Returns serialized data model, with xpath_exclude
    nodes cut out.
    '''
    copied = copy.deepcopy(self._doc._get_data_root())
    # zfoengine: fixed 'namesapces' typo and getroottree() on an ElementTree
    excludes = copied.xpath(xpath_exclude, 
                            namespaces=self._doc._get_used_ns_prefix(xpath_exclude))
    for exclude in excludes:
      exclude.getparent().remove(exclude)
    res = etree.tostring(copied, pretty_print=False, encoding="unicode")
    return res
  
  def GetHash(self, context, str, method):
    '''
    Returns hashed str. Method can be MD5 or SHA1
    '''
    if method.lower() == "md5":
      alg = hashlib.md5()
    if method.lower() == "sha1":
      alg = hashlib.sha1()
    
    alg.update(str.encode("utf-8"))
    return alg.hexdigest()
  
  def SignLevel(self, context, level):
    '''
    Returns true if number of signatures is equal to level.
    '''    
    sigs = self._doc.root.xpath("//dsig:Signature", namespaces={"dsig": ns.DSIG_URI})
    try:
      level = int(level)
    except:
      level = 0
    return len(sigs) == level
  
  def PartSignLevel(self, context, level):
    log.warning("PartSignLevel not implemented")
    return 0
  
  def GetFormParam(self, context, xpath):
    '''
    Get the xpath evaluated from the fm:formparams element as root
    '''
    log.warning("GetFormParam not implemented")
    formparams = self._doc.root.xpath("//fm:formparams", namespaces={'fm': ns.FM_URI})[0]
    params = formparams.xpath(xpath, namespaces=self._doc._get_used_ns_prefix(xpath))
    if not len(params):
      return ""
    else:
      param = params[0]
      if self._doc._path_contains_att(xpath):
        return str(param)
      else:
        return param.text   
    return 
  
  def GetNumPages(self, context):
    log.warning("GetNumPages not implemented")
    return 0
  
  def SetMultipleDataTo(self, context, *args):
    '''
    Sets data to binds, arguments are logical couples of
    bind identifier and value 
    '''
    res = {}
    is_value = False
    bind_id = None
    for arg in args:
      if is_value:
        self._doc.store_value(bind_id, arg)
        res[bind_id] = arg
      else:
        bind_id = arg
      
      is_value = not(is_value)
      
  def GetAttribute(self, context, att_name, node=None):
    '''
    Gets the attribute value from the specified node. If node is
    none, return attribute from context node
    '''    
    return context.context_node.get(att_name, default="")
    
  
  def SetAttribute(self, context, att_name, value):
    '''
    Sets attribute value on context node.
    '''
    context.context_node.set(str(att_name), str(value))
  
  def CleanForm(self, context):
    log.warning("CleanForm not implemented")    
    return
  
  def Recalc(self, context):
    log.warning("Recalc not implemented")
    # Implemented as application event, usually used there
    return
  
  def CheckData(self, context):
    log.warning("CheckData not implemented")    
    return ""
  
  def SendAppEvent(self, context, event_name, *args):    
    try:
      # in case of 'print' event
      if event_name == "print":
        event_name = event_name + "_"
      meth = getattr(app_events, event_name)
      return meth.__call__(self._doc, args)
    except AttributeError as att_err:
      log.warning(str(att_err))      
      log.warning("Unknown application event: %s" % event_name)
      return app_events.unknow_event(self._doc, event_name)
    except Exception as ex:
      log.error(ex)
      return app_events.command_failure(ex)
      
  
  def DataTransform(self, context, template_name, data_xpath, *args):    
    template = self._doc.get_template_text(template_name)
    if template is None:
      log.error("No template for data transformation found, returning empty string")
      return ""
        
    data = context.context_node.xpath(data_xpath, 
                                      namespaces = self._doc._get_used_ns_prefix(data_xpath))[0]
    transform = etree.XSLT(template)
    result = transform(data)   
    result = str(result)
    log.info("DataTransform result: {0}".format(result))
    
    return result
  
  def FormCreationDate(self, context):
    '''
    Creation date of the form.
    '''
    cdate = self._doc.root.xpath("//fm:formparams/@creationdate", 
                                 namespaces={"fm": ns.FM_URI})
    if len(cdate):
      return str(cdate[0])
    else:
      return "1970-01-01"
  
  def SaveData(self, context, fileName, user=None, password=None, 
               proxyuser = None, proxypwd=None):
    log.warning("SaveData not implemented")
    return
  
  def Save(self, context, fileName=None):
    self._doc.save_to_file(fileName)
  
  def SaveSigned(self, context, fileName=None):
    log.warning("SaveSigned implemented as non-signed version")
    self._doc.save_to_file(fileName)
      
  def RefreshElements(self, context, xpath):
    log.warning("RefreshElements not implemented")
    return
  
  def SaveAs(self, context, fileName):
    log.warning("SaveAs not implemented")
    res = self._doc.save_to_file(fileName)
    if res["type"] == msg_types.INFO:
      return res["filename"]
    else:
      return ""
  
  def SaveAsSigned(self, context, fileName):    
    log.warning("SaveSigned implemented as non-signed version")    
    return self.SaveAs(context, fileName)
  
  def GetTmpFileName(self, context, fileName=None):
    log.warning("GetTmpFileName not implemented")
    return ""
  
  def Is_Changed(self, context):
    '''
    is_changed flag is set after first successful store_value
    '''
    return self._doc.is_changed
  
  def Stop(self, context, stop_state):
    log.info("Stop event occured")
    if stop_state:
      log.debug("Stop state evaluated to True, raising StopEvalException")
      raise StopEvalException()
    return  
  
  def Transform(self, context, stylesheetname, refxpath, mode, modeuri):
    log.warning("Transform not implemented")
    return
  
  def GetSaveFileNameDialog(self, context, type=None, name=None):
    log.warning("GetSaveFileNameDialog not implemented")
    return
  
  def LoadFormData(self, type=None, name=None):
    log.warning("LoadFormData not implemented")     
    return
  
  def InsertSection(self, context, xpath, data):
    log.warning("InsertSection not implemented")
    return
  
  def RemoveSection(self, context, xpath):
    log.warning("RemoveSection not implemented")
    return
  
  def GetOpenFileNameDialog(self, context, type=None, name=None):
    log.warning("GetOpenFileNameDialog not implemented")
    return
  
  def PageNo(self, context):
    log.warning("PageNo not implemented")
    return 0
  
  def SectionNo(self, context):
    log.warning("SectionNo not implemented")
    return 0
  
  def RowNo(self, context):
    log.warning("RowNo not implemented")
    return 0
  
  def CellNo(self, context):
    log.warning("CellNo not implemented")
    return 0
  
  def FormatNumb(self, value, format):
    log.warning("FormatNumb not implemented - format not specified")
    return value
  
  def IntAdd(self, context, *args):
    '''
    Returns sum of arguments
    '''
    sum = 0
    for arg in args:
      try:
        num = int(arg, 10)
      except:
        num = 0
      sum += num      
    return sum
  
  def IntMul(self, context, *args):
    '''
    Returns product of arguments
    '''
    prod = 1
    for arg in args:
      try:
        num = int(arg, 10)
      except:
        num = 1
      prod *= num      
    # zfoengine: original returned the builtin 'sum' here
    return prod
      
  def Message_Box(self, context, message, title):
    log.warning("Message_Box not implemented")
    return
  
  def QueryTextDialog(self, context, title, comment):
    log.warning("QueryTextDialog not implemented")
    return 
  
  def XpathExpr(self, context, param, relat=None):
    log.warning("XpathExpr not implemented")
    return
  
  def GetFormDataXML(self, context, sign=None):
    '''
    Returns form data - in form of an ElementTree.
    "signed" is ignored.
    '''
    droot = copy.deepcopy(self._doc._get_data_root())    
    return droot
  
  def GetFormXML(self, context, sign=None):
    '''
    Returns whole form as a text
    '''
    formroot = self._doc.root
    return etree.tostring(formroot, encoding="unicode")
  
  def Print(self):
    log.warning("Print not implemented")    
    return
  
  def ActivateField(self, context, xpath, reference=None):
    log.warning("ActivateField not implemented")
    return ""
  
  def GetCurrentDataXpath(self, context, bindxpath=None):
    '''
    Returns absolute data path of context node.
    If bindxpath is not None, relative path from context node
    to node specified in bindxpath is returned.
    '''
    node = context.context_node
    tree = self._doc._get_data_root()
    path = tree.getpath(node)
    if bindxpath is not None:
      t = tree.xpath(bindxpath, namespaces=self._doc._get_used_ns_prefix(bindxpath))
      if not len(t):
        return ""
      else:
        # this is the data node, we want relative xpath to (from the context node)
        other_node = t[0]
        other_node_path = tree.getpath(other_node)
        path = tools.create_relative_path(path, other_node_path)
    return path
  
  def SetValueToData(self, context, value):
    # get form602bindref attribute
    bindref = context.context_node.get("form602bindref")
    if bindref is None:
      return
    else:
      # get the referenced bind
      bind = self._doc._find_bind_elem(bindref)
      if not len(bind):
        return
      else:
        bind = bind[0]
        path = self._doc._get_abs_data_path(bind)
        # get the data node
        datanode = self._doc._get_data_root().xpath(path,
                                                    namespaces = self._doc._get_used_ns_prefix(path))
        if not len(datanode):
          return
        else:
          # store value to that node
          datanode = datanode[0]
          att = self._doc._path_contains_att(path)
          if att is None:
            datanode.text = str(value)
          else:
            datanode.set(att, str(value))
           
  def SetListDataTo(self, context, node, bindXpath):
    log.warning("SetListDataTo not implemented")
    return
  
  def Switch(self, context, dataxpath, defval, val, *args):
    log.warning("Switch not implemented")
    return
  
  def SubmitData(self, context, profile):
    log.warning("SubmitData implemented as application event")
    return
  
  def GetFieldSignerIndex(self, context, fieldxpath):
    log.warning("GetFieldSignerIndex not implemented")
    return 0
  
  def XSLCheck(self, context, xsltname):
    '''
    Unclear specification.
    '''
    log.warning("XSLCheck not implemented")
    return ""
  
  def SystemDataChanged(self, context):
    log.warning("SystemDataChanged not implemented")
    return ""
  
  def TransformDataBranche(self, context, stylesheetname, bind1, bind2):
    '''
    Runs transformation on the data node from bind1 and stores the result in the bind2.
    '''
    log.warning("TransformDataBranche not implemented")
    return
  
  def ReadBinDataFileAndB64Encode(self, context, fileName):
    '''
    Not in documentation but used in forms.
    '''
    log.warning("ReadBinDataFileAndB64Encode not implemented")
    return ""

