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

import submit

import tools
import os
import base64

import evaluate
import expr_quoting

import logging
log = logging.getLogger("zfo.events")

import msg_types as mt
import msg_class as mc
import msgs
import gtk
import global_var
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
    if self.num_buttons == 0:
      md = gtk.MessageDialog(buttons = gtk.BUTTONS_NONE)
    elif self.num_buttons == 1:
      md = gtk.MessageDialog(buttons = gtk.BUTTONS_OK)
    elif self.num_buttons == 2:
      md = gtk.MessageDialog(buttons = gtk.BUTTONS_YES_NO)
    else:
      md = gtk.MessageDialog()
    
    md.set_markup(self.text)
    response = md.run()
    # if user clicked "No", he wants to break processing
    if response == gtk.RESPONSE_NO:
      self.set_break_processing()
    
    md.destroy()
    
    return self._create_response_dict(msgs.APP_EVENT_OK, msg_type=mt.INFO)

class Saveform(Event):
  def __init__(self, format="fo", file_xpath=None, dlg=None):
    self.format = format
    self.file_xpath = file_xpath
    self.dlg = dlg
  
  def perform(self):
    sf = global_var.Global.get_save_func()
    saved_to = sf()
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
    if self.ask_before_quit:
      md = gtk.MessageDialog(buttons = gtk.BUTTONS_YES_NO)
      md.set_markup(msgs.QUIT_QUESTION)
      res = md.run()
      if res == gtk.RESPONSE_YES:
        global_var.Global.set_quit()
        self.set_break_processing()
      md.destroy()
    else:
      md = gtk.MessageDialog(buttons = gtk.BUTTONS_OK)
      md.set_markup(msgs.QUIT_INFO)
      md.run()
      global_var.Global.set_quit()
      self.set_break_processing()
      
    return self._create_response_dict(msgs.APP_EVENT_OK, msg_type=mt.INFO)

class Submit(Event):
  def __init__(self, submit_id):
    self.submit_id = submit_id
  
  def perform(self):
    zfo_doc = global_var.Global.get_doc()
    submit_node = zfo_doc.get_submit_node(self.submit_id)
    if submit_node is None:
      return self._create_response_dict(msgs.APP_EVENT_SUBMIT_PROFILE_NOT_FOUND.format(self.submit_id), 
                                        msg_type=mt.ERROR)
    else:      
      method = submit_node.get("method").lower()
      if method == "soap":
        status, reason, response = self._submit_soap(submit_node)
        if status is None:
          return self._create_response_dict(msgs.CONNECTION_FAILURE.format(reason),
                                        msg_type=mt.ERROR)
        return self._create_response_dict(str(status) + "\n" + reason + "\n" + response, 
                                        msg_class = mc.MESSAGEBOX_OK,
                                        msg_type=mt.INFO)
      elif method == "http":        
        status, reason, response = self._submit_http(submit_node)
        if status is None:
          return self._create_response_dict(msgs.CONNECTION_FAILURE.format(reason),
                                        msg_type=mt.ERROR)
        return self._create_response_dict(str(status) + "\n" + reason + "\n" + response, 
                                        msg_class = mc.MESSAGEBOX_OK,
                                        msg_type=mt.INFO)
      elif method == "file":
        stored_to = self._submit_file(submit_node)
        return self._create_response_dict(msgs.APP_EVENT_FILE_SUBMIT_OK.format(stored_to),
                                        msg_type=mt.INFO)
      elif method == "email":
        return self._create_response_dict("email: " + msgs.APP_EVENT_SUBMIT_METHOD_NOT_IMPLEMENTED,
                                        msg_type=mt.WARN)
      else:
        return self._create_response_dict(method + ": "+ msgs.APP_EVENT_SUBMIT_METHOD_NOT_IMPLEMENTED,
                                        msg_type=mt.WARN)      
  
  def _xsl_transform(self, node, template_name=None, template=None):
    '''
    Gets the node to be transformed and the name of template.
    Return serialized transformed node.
    '''
    if template_name is not None:
      zfo_doc = global_var.Global.get_doc()
      tmplt = zfo_doc.get_template_text(template_name)      
    if template is not None:
      tmplt = template
      
    transform = etree.XSLT(tmplt)
    transformed = transform(node)      
    return transformed
  
    
  def _submit_file(self, submit_node):
    '''
    Performs submit type of storing to file.
    Action att contains filename.
    '''
    filename = submit_node.get("action")
    if filename is None:      
      dlg = tools.create_save_dialog()
      response = dlg.run()
      if response == gtk.RESPONSE_OK:
        filename = dlg.get_filename()
      dlg.destroy()
    else:
      filename = tools.transform_zfo_filepath(filename)
    
    zfo_doc = global_var.Global.get_doc()
    bindelem = submit_node.get("bindelement")
    if bindelem is None:       
      model = zfo_doc._get_data_root().getroot()
    else:
      model = zfo_doc.get_data_node(bindelem)
    
    template = submit_node.get("xls")    
    transformed_model = None
    if template is not None:
      transformed_model = self._xsl_transform(model, template_name=template)
       
    
    must_cutns = submit_node.get("cutnamespaces")
    if must_cutns is not None:
      if must_cutns.lower() == "true":        
        if transformed_model is not None:
          cut_ns = self._cut_ns(transformed_model)
        else:
          cut_ns = self._cut_ns(model)
      else:
        if transformed_model is not None:
          cut_ns = transformed_model
        else:
          cut_ns = model
      to_send = etree.tostring(cut_ns)
    else:
      if transformed_model is not None:
        to_send = etree.tostring(transformed_model)
      else:
        to_send = etree.tostring(model)
    
    return submit.submit_file(filename, to_send)    
  
  def _parse_headers(self, hdrs):
    '''
    Creates headers dictionary from string got
    from sendheaders' attribute.    
    '''
    if hdrs is None:
      return None
    
    result = {}
    lines = hdrs.split("\n")
    for line in lines:
      parts = line.split(":")
      key = parts[0].strip()
      value = parts[1].strip()
      result[key] = value
    
    return result
    
  
  def _parse_action_url(self, zfo_doc, url):
    '''
    Creates url from the content of action att.
    Url is either an url or is in form $(eval(xpath))
    which means that url is result of evaluation of the xpath
    on the bindelement node.
    '''
    original = url
    url = url.strip()
    # if starts with $, there is Eval after it (maybe...?)
    if url.startswith("$"):
      url = url[1:].strip().strip("()").strip()
      # find opening bracket
      opening = url.find("(")
      func = url[:opening]
      # chop into funcname and params
      param = url[opening:].strip().strip("()").strip()
      if func.lower() == "eval":
        if param.startswith('"'):
          param = expr_quoting.quote_expression(param)
          res = zfo_doc.root.xpath(param, namespaces=zfo_doc._get_used_ns_prefix(param))          
        else:
          res = zfo_doc.get_value_path(param)                
        return res
      else:
        log.warning("Do not know how to decode action: %s" % url )
        return original
    else:
      return url
  
  def _cut_ns(self, element):
    '''
    runs transformation of input element with template
    that cuts namespace prefixes.
    '''
    transfrom = global_var.Global.get_cutns_transform_func()
    cut_ns = transfrom(element)
    return cut_ns
  
  def _prepare_data_to_send(self, submit_node):
    '''
    Prepares data to submit according to sumbit_node information.
    Applies XSLT if any, cut namespaces if required and 
    returns serialized part of the data model to submit.
    '''
    zfo_doc = global_var.Global.get_doc()
    
    # set the context node of this operation
    bindelem = submit_node.get("bindelement")
    if bindelem is None:
      model = zfo_doc._get_data_root().getroot()
    else:
      model = zfo_doc.get_data_node(bindelem)
      
    soap_rq_template = submit_node.get("soaprequestxls")
    
    if soap_rq_template is None:
      soap_rq_template = submit_node.get("xls")
   
    # if there is some xsl referenced, run the transformation on the data model node
    transformed_model = None
    if soap_rq_template is not None:
      transformed_model = self._xsl_transform(model, template_name=soap_rq_template)      
    
    # now check if have to cut the namsespace prefixes
    must_cutns = submit_node.get("cutnamespaces")
    if must_cutns is not None:
      if must_cutns.lower() == "true":        
        if transformed_model is not None:
          cut_ns = self._cut_ns(transformed_model)
        else:
          cut_ns = self._cut_ns(model)
    else:
      if transformed_model is not None:
        cut_ns = transformed_model
      else:
        cut_ns = model
    # serialize prepared element    
    to_send = etree.tostring(cut_ns)
    
    return to_send
    
  def _submit_soap(self, submit_node): 
    '''
    Sends soap request.
    '''    
    zfo_doc = global_var.Global.get_doc()
    
    to_send = self._prepare_data_to_send(submit_node)
    
    action = submit_node.get("action")
    # action could contains $(Eval()) expression
    # if yes, evaluate the argument of Eval (not in 602 documentation, just guess)
    url = self._parse_action_url(zfo_doc, action)
    
    # prepare header to the form used by httplib (dictionary with header names as keys)
    hdr = self._parse_headers(submit_node.get("sendheader"))
    
    status, reason, data = submit.submit_soap(url, hdr, to_send)
    
    return status, reason, data
  
  def _submit_http(self, submit_node):     
    zfo_doc = global_var.Global.get_doc()
    to_send = self._prepare_data_to_send(submit_node)
    
    action = submit_node.get("action")   
    url = self._parse_action_url(zfo_doc, action)
    
    # prepare header to the form used by httplib (dictionary with header names as keys)
    hdr = self._parse_headers(submit_node.get("sendheader"))
    
    status, reason, data = submit.submit_http(url, hdr, to_send)
    return status, reason, data 
    
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
    if self.data_xpath == "":
      zfo_doc.store_value(self.bind_id, value)
    else:
      zfo_doc.store_value_path(bind_el[0], value, self.data_xpath)
    
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
    if self.data_xpath == "":
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
    zfo_doc = global_var.Global.get_doc()
    filepath = zfo_doc.get_value_path(self.file_xpath)
    filepath = tools.transform_zfo_filepath(filepath)
    try:
      os.remove(filepath)
      return self._create_response_dict(msgs.APP_EVENT_FILE_DELETED.format(filepath), 
                                        msg_type=mt.INFO)
    except Exception as ex:
      log.error(ex)
      return self._create_response_dict(msgs.APP_EVENT_FAILED, msg_type=mt.WARN)
      
    
  
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
    '''
    tools.show_form_data(global_var.Global.get_doc())

class Savebase64datatofile(Event):
  def __init__(self, xpath_to_b64_data, file_path_or_ext_list):
    self.path_to_data = xpath_to_b64_data
    self.file_path_or_ext_list = str(file_path_or_ext_list)
  
  def perform(self):
    zfo_doc = global_var.Global.get_doc()
    data_node = zfo_doc.get_data_node_path(self.path_to_data)
    data = data_node.text
    decoded = base64.b64decode(data)
    
    file_path = tools.transform_zfo_filepath(self.file_path_or_ext_list)
    
    f = open(file_path, "wb")
    f.write(decoded)
    f.flush()
    f.close()
    
    return self._create_response_dict(msgs.APP_EVENT_FILE_SAVED_OK.format(file_path), 
                                      msg_type=mt.INFO)
