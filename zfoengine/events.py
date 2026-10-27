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
Module implementing applications events.
Events are prepared inside xpath extensions during 'button pressed' handler.
Each event is derived from base class Event and has to
override perform() method.
Expected return value from result is dictionary with msg, msg_type and classname of
the class responsible for the event.
If needed, perform() should set the 'break_processing' flag, which ensures,
that evaluation of events list is ended after current event (i.e. after user
clicks No in message dialog with question or in case of exception in processed event).  
'''

from . import tools
import os
import base64

from . import evaluate
from . import expr_quoting
import logging
log = logging.getLogger("zfo.events")

from . import msg_types as mt
from . import msg_class as mc
from . import msgs
from . import global_var
import json
import lxml.etree as etree

class Event():  
  break_processing = False
  def __init__(self):
    #self.break_processing = False
    pass    
    
  def perform(self):
    return self._create_response_dict(msgs.APP_EVENT_NOTIMPL)
  
  def set_break_processing(self):
    self.break_processing = True
  
  def get_break_processing(self):
    return self.break_processing
  
  def _create_response_dict(self, msg, msg_class=None, msg_type=mt.WARN, js_data=None, show=True):
    res = {}
    if msg is None:
      res["msg"] = ""
    else:
      res["msg"] = msg
    
    # set class  
    if msg_class is None:
      res["msg_class"] = mc.MSG_LIST
    else:
      res["msg_class"] = msg_class
    
    # set type
    res["msg_type"] =  msg_type
    
    # set data for webkit
    if js_data is not None:
      res["web_data"] = json.dumps(js_data)
    
    # set if data should be hidden or not
    res["show"] = show
    
    return res

class Foreach(Event):
  def __init__(self, arg0, arg1, arg2, arg3):
    self.a0 = arg0
    self.a1 = arg1
    self.a2 = arg2
    self.a3 = arg3

class Failure(Event):
  '''
  Represents failure in evaluation of application event.
  '''
  def __init__(self, exception):
    self.exc = exception
    self.break_processing = True
    
  def perform(self):
    return self._create_response_dict(str(self.exc), msg_type=mt.ERROR)
  
class Unknown(Event):
  '''
  Represents event, which name is not known from
  documentation.
  '''
  def __init__(self, event_name):
    self.event_name = event_name
    
  def perform(self):
    return self._create_response_dict(self.event_name + " - " + msgs.APP_EVENT_UNKNOW, 
                                      msg_type=mt.WARN, 
                                      show=True)  

class Messagebox(Event):
  def __init__(self, text, num_buttons=1):
    self.text = text
    self.num_buttons = num_buttons
  
  def perform(self):    
    # zfoengine: no dialog - the text is handed to the caller as a message box
    # response; a Yes/No box (num_buttons == 2) cannot be answered, processing
    # continues as if the user clicked Yes
    log.info("Messagebox: %s" % self.text)
    return self._create_response_dict(self.text, msg_class=mc.MESSAGEBOX_OK, msg_type=mt.INFO)

class Saveform(Event):
  def __init__(self, format="fo", file_xpath=None, dlg=None):
    self.format = format
    self.file_xpath = file_xpath
    self.dlg = dlg
  
  def perform(self):
    sf = global_var.Global.get_save_func()
    # zfoengine: no save function unless the caller sets one
    saved_to = sf() if sf is not None else None
    if saved_to is not None:
      show = True
    else:
      show = False
    return self._create_response_dict(msgs.APP_EVENT_OK, 
                                      msg_type=mt.INFO, show=show)

class Closeapp(Event):
  def __init__(self, ask_before_quit=0):
    self.ask_before_quit = ask_before_quit
  
  def perform(self):
    # zfoengine: the engine does not quit; the request is reported and the
    # rest of the command chain is stopped, like after quitting
    log.info("'closeapp' requested by the form")
    self.set_break_processing()
    return self._create_response_dict(msgs.QUIT_INFO, msg_type=mt.INFO)

class Submit(Event):
  '''
  zfoengine: submitting (soap/http/file/email) is not performed by the engine.
  The original posted the data model over http(s) or wrote it to a path taken
  from the form; the submit profile is still looked up and reported.
  '''
  def __init__(self, submit_id):
    self.submit_id = submit_id
  
  def perform(self):
    zfo_doc = global_var.Global.get_doc()
    submit_node = zfo_doc.get_submit_node(self.submit_id)
    if submit_node is None:
      return self._create_response_dict(msgs.APP_EVENT_SUBMIT_PROFILE_NOT_FOUND.format(self.submit_id), 
                                        msg_type=mt.ERROR)
    else:      
      method = (submit_node.get("method") or "").lower()
      log.warning("Submit '%s' (method %s) is not performed by zfoengine" % (self.submit_id, method))
      return self._create_response_dict(method + ": "+ msgs.APP_EVENT_SUBMIT_METHOD_NOT_IMPLEMENTED,
                                        msg_type=mt.WARN)
    
class Recalc(Event):
  def __init__(self):
    pass
  
  def perform(self):
    try:
      zfo_doc = global_var.Global.get_doc()
      res = zfo_doc.recalc_form()
      return self._create_response_dict(msgs.APP_EVENT_RECALCULATED, msg_type=mt.INFO, js_data=res)
    except Exception as ex:
      log.error(ex)
      # exception occurred, stop processing
      self.set_break_processing()
      return self._create_response_dict(msgs.APP_EVENT_FAILED, msg_type=mt.ERROR)
    
  
class Printpdf(Event):
  pass

class Print(Event):
  pass

class Checkdata(Event):
  pass

class Submitpart(Event):
  pass

class Addsect(Event):
  pass

class Delsect(Event):
  pass

class Fillsect(Event):
  pass

class Loadformdata(Event):
  pass

class Openform(Event):
  pass

class Saveformsign(Event):
  pass

class Openbinddata(Event):
  pass

class Setdatato(Event):
  pass

class Setevaldata(Event):
  def __init__(self, bind, data_xpath, data_expr):
    self.bind_id = bind
    self.data_xpath = data_xpath
    self.data_expr = data_expr
  
  def perform(self):
    '''
    Not implemented yet, data_expr contains undocumented xpath extensions
    '''
    zfo_doc = global_var.Global.get_doc()
    # set value to the both trees - computable and original
    # call store_value_path on the document (get it bind, path and value)    
    bind_el = zfo_doc._find_bind_elem(self.bind_id)
    bind_el = bind_el[0]
    path = zfo_doc._get_abs_data_path(bind_el)
    nsmap = zfo_doc._get_used_ns_prefix(path)
    self.data_expr = expr_quoting.quote_expression('"'+self.data_expr.strip("'")+'"')    
    value = evaluate.eval_expression_node(zfo_doc, self.data_expr, bind_el, nsmap)
    if str(self.data_xpath).strip() in ("", "()"):
      zfo_doc.store_value(self.bind_id, value)
    else:
      # zfoengine: original indexed the element (bind_el[0]) instead of the list
      zfo_doc.store_value_path(bind_el, value, self.data_xpath)
    
    return self._create_response_dict(msgs.APP_EVENT_VALUE_SET_TO.format(value, self.bind_id), 
                                      msg_type=mt.INFO)

class Setdatavalue(Event):
  def __init__(self, bind, data_xpath, value):
    self.bind_id = str(bind)
    self.data_xpath = str(data_xpath)
    self.value = str(value)
  
  def perform(self):
    zfo_doc = global_var.Global.get_doc()
    # set value to the both trees - computable and original
    # call store_value_path on the document (get it bind, path and value)
    bind_el = zfo_doc._find_bind_elem(self.bind_id)
    # zfoengine: forms pass "()" for "no data xpath" (CUZK print button)
    if self.data_xpath.strip() in ("", "()"):
      zfo_doc.store_value(self.bind_id, self.value)
    else:
      zfo_doc.store_value_path(bind_el[0], self.value, self.data_xpath)
    return self._create_response_dict(msgs.APP_EVENT_VALUE_SET_TO.format(self.value, self.bind_id), 
                                      msg_type=mt.INFO)

class Gotofield(Event):
  pass

class Startfiller(Event):
  pass

class Formtohtml(Event):
  pass

class Printparam(Event):
  pass

class Openfiller(Event):
  pass

class Loaddata(Event):
  pass

class Signclick(Event):
  pass

class Csvtoxml(Event):
  pass

class Openfilefrombuffer(Event):
  pass

class Printpreview(Event):
  pass

class Deletefile(Event):
  '''
  Request for a file delete.
  '''
  def __init__(self, data_file_xpath):
    self.file_xpath = data_file_xpath
  
  def perform(self):
    # zfoengine: deleting a file whose path comes from the form data is disabled
    zfo_doc = global_var.Global.get_doc()
    filepath = zfo_doc.get_value_path(self.file_xpath)
    filepath = tools.transform_zfo_filepath(filepath)
    log.warning("deletefile '%s' is not performed by zfoengine" % filepath)
    return self._create_response_dict(msgs.APP_EVENT_NOTIMPL, msg_type=mt.WARN)
      
    
  
class Savebinddata(Event):
  pass

class Openlink(Event):
  pass

class Viewdata(Event):
  def __init__(self):
    pass
  
  def perform(self):
    '''
    Show the data model of the opened form.
    zfoengine: there is no window to show it in (original returned None here,
    which crashed perform_click).
    '''
    return self._create_response_dict(msgs.APP_EVENT_NOTIMPL, msg_type=mt.WARN)

class Savebase64datatofile(Event):
  def __init__(self, xpath_to_b64_data, file_path_or_ext_list):
    self.path_to_data = xpath_to_b64_data
    self.file_path_or_ext_list = str(file_path_or_ext_list)
  
  def perform(self):
    # zfoengine: writing to a path that comes from the form is disabled
    file_path = tools.transform_zfo_filepath(self.file_path_or_ext_list)
    log.warning("savebase64datatofile '%s' is not performed by zfoengine" % file_path)
    return self._create_response_dict(msgs.APP_EVENT_NOTIMPL, msg_type=mt.WARN)
