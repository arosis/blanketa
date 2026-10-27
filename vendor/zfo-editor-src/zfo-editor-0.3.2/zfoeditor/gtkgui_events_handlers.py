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
Handlers for events fired by javascript in webkit component.
'''

import json
import msgs
import tools

import msg_types
import msg_class as mc
import pickle 
import tags

import cgi
import logging
log = logging.getLogger("zfo.gtkgui_events_handlers")

import gtk

LOG_MSG_MAX_LEN = 160

def _show_messagebox(mtype, eventname, message):
  dialog = gtk.MessageDialog(buttons=gtk.BUTTONS_OK)
  
  msg = cgi.escape(message)
  dialog.set_markup(msg)
  dialog.run()
  dialog.destroy()
  

def _construct_web_response(javascript):
  '''
  Creates response object, its data will be sent to browser
  '''
  resp = {}
  resp["type"] = "javascript"
  resp["data"] = javascript
  
  return [resp]

def _construct_gtk_info_response(data_to_display, area="field_info", type=msg_types.INFO):
  '''
  Creates response object for gtk gui, its data contains
  list of messages.
  area specifies where the message should be written.
  Default is field_info, other value is message_area.
  These messages are then written into information areas.
  '''
  resp = {}
  resp["type"] = "gtk"
  resp["info_area"] = area
  resp["info_type"] = type
  resp["data"] = {}    
  resp["data"]["to_display"] = data_to_display
  
  return [resp]
  
def get_init_values(document, msg_data):  
  initial = json.dumps(document.get_initial_values(msg_data))    
  return _construct_web_response("set_inputs_init_values_callback(%s)" % initial)

def get_repeatable(document, msg_data=None):
  rep = document.get_repeatable_sections()
  rep = json.dumps(rep)  
  return _construct_web_response("calc_repeatable_sections_callback(%s)" % rep)

def init_rep_sections(document, msg_data=None):
  rep = document.get_repeatable_sections()
  res = {}  
  templates = {}
     
  def create_section_dict(repdata):
    results = {}
    for rsname in repdata:
      if isinstance(repdata[rsname], list):
        html = document.get_repe_section_html(rsname)
        templates[rsname] = html
        results[rsname] = []
        for repeating in repdata[rsname]:
          item = {}
          #item["name"] = rsname
          item["sub_rs"] = create_section_dict(repeating)
          results[rsname].append(item)
    return results
  
  sections = create_section_dict(rep)
  
  res["sections"] = sections
  res["templates"] = templates
   
  res = json.dumps(res)  
  return _construct_web_response("init_rep_sections_callback(%s)" % res)

def get_selected_listboxes(document, msg_data=None):
  selected = document.get_selected_listbox_values()
  selected = json.dumps(selected)  
  return _construct_web_response("get_selected_listboxes_callback(%s)" % selected)
  
def store_val(document, msg_data):   
  field = msg_data["field"]
  value = msg_data["value"]
  html_id = msg_data["input_id"]
  repe_name = msg_data["repe"] if msg_data["repe"] != "" else None
  
  stored_val, to_bind, constr_ok, \
  constr_comment, old_val, calculated_binds = document.store_value(field, value, repe_name)
        
  if constr_ok:
     res = {}
     # shorten the value for the message
     if len(stored_val) > LOG_MSG_MAX_LEN: 
       stored_val = stored_val[:LOG_MSG_MAX_LEN]
     res["msg"] = "Stored: value: {%s} to bind: {%s}" %(stored_val, to_bind)
     res["input_id"] = html_id
     res["repe"] = msg_data["repe"]
     res["calculated"] = calculated_binds    
     return _construct_web_response("field_changed_ok_callback(%s)" % json.dumps(res)) +\
            _construct_gtk_info_response(msgs.FIELD_VALUE_STORED.format(stored_val, to_bind), area="message_area")
   
  else:     
     res = {}
     res["curr_val"] = old_val
     res["input_id"] = html_id
     res["error"] = None
     #res["msg"] = "Constraint violated: %s : %s" % (to_bind, constr_comment)
     res["msg"] = constr_comment
     
     return _construct_web_response("field_changed_error_callback(%s)" % json.dumps(res)) + \
            _construct_gtk_info_response(constr_comment, area="message_area", type=msg_types.ERROR)

def delete_attachment(document, msg_data):
  '''
  Deletes attachment.    
  '''
  field = msg_data["field"]  
  filename= msg_data["value"]
  repe = msg_data["repe"] if msg_data["repe"] != "" else None
  res = {}
  # storing empty string instead of the file content
  stored_val, to_bind, constr_ok, \
      constr_comment, old_val, calculated = document.store_value(field, "", repe)
  # remove the file name from the map
  document.remove_filename_path(field, repe)
        
  res["input_id"] = msg_data["input_id"]
  res["calculated_data"] = calculated
  # send these two to allow javascript delete fields with this values
  # even if they are not bound to the file content
  res["filename_bind_name"] = document.get_binds_filename_bind(field)
  res["mediatype_bind_name"] = document.get_binds_mediatype_bind(field)    
  
  res = json.dumps(res)
  return _construct_web_response("delete_file_callback(%s)" % res)+\
            _construct_gtk_info_response(msgs.ATTACHMENT_DELETED.format(filename), 
                                 area="message_area", type=msg_types.INFO)

def select_file(document, msg_data):
  '''
  Shows dialog allowing user to select file which will be
  attached to the form.  
  '''
  field = msg_data["field"]
  filter_str = msg_data["filter"]
  repe_name = msg_data["repe"] if msg_data["repe"] != "" else None
  res = {}
  filename = tools.get_file_from_user_gtk(filter_str)
  # store filename in attribute filenamebind and mimetype(?) 
  # in mediatypebind attribute  
  if filename is not None:    
    stored_val, to_bind, constr_ok, \
      constr_comment, old_val, calculated_binds = document.attach_file(filename, field, repe_name)
    
    if constr_ok:
      res["input_id"] = msg_data["input_id"]
      res["filename"] = filename 
      res["calculated_data"] = calculated_binds
      # send these two to allow javascript unset the flag for staying inputs empty
      res["filename_bind_name"] = document.get_binds_filename_bind(field)
      res["mediatype_bind_name"] = document.get_binds_mediatype_bind(field) 
         
      res = json.dumps(res)
    
      return _construct_web_response("append_file_callback(%s)" % res)+\
              _construct_gtk_info_response(msgs.ATTACHMENT_ADDED.format(filename), 
                                   area="message_area", type=msg_types.INFO)
    else:      
      res["filename"] = ""
      res = json.dumps(res)
      return _construct_web_response("append_file_callback(%s)" % res)+\
              _construct_gtk_info_response(msgs.ATTACHMENT_FAILED.format(constr_comment), 
                                   area="message_area", type=msg_types.ERROR)
  else:
    res["filename"] = ""
    res = json.dumps(res)
    return _construct_web_response("append_file_callback(%s)" % res)
    

def calculate(document, msg_data):
  field = msg_data["field"]                
  repe_data = msg_data["repe"] if msg_data["repe"] != "" else None
  
  # take the results from calculate_values, change_log is not necessary
  res, change_log = document.calculate_values(field, repe_data)
  res = json.dumps(res)
  
  return _construct_web_response("recalculate_form_callback(%s)" % res)         

def get_readonly_binds(document, msg_data=None):
  ro_b = document.get_readonly_binds()
  ro_b = json.dumps(ro_b)
    
  return _construct_web_response("set_readonly_inputs_callback(%s)" % ro_b)

def get_multiswitch_list(document, msg_data=None):
  multies = document.get_multiswitch_values()
  multies = json.dumps(multies)
  
  return _construct_web_response("set_multiswitch_controls_callback(%s)" % multies)

def add_section(document, msg_data):
  field_name = msg_data["name"]                
  repe_data = msg_data["repe"] if msg_data["repe"] != "" else None  
  # used to identify the html object, after which the template will be added
  rep_section_id = msg_data["rs_id"]
  
  end_of_name = field_name.rfind("-rep-")
  field_name_no_rep = field_name[:end_of_name]
  document.clone_section(field_name_no_rep, repe_data)
  repe_html = document.get_repe_section_html(field_name_no_rep)
  
  data = {}
  data["repe_html"] = repe_html
  data["attach_after"] = rep_section_id
      
  data = json.dumps(data)
  
  return _construct_web_response("server_add_section_callback(%s)" % data)


def del_section(document, msg_data):    
  field_name = msg_data["name"]                
  repe_data = msg_data["repe"] if msg_data["repe"] != "" else None
  
  document.delete_section(field_name, repe_data)
  # calculate chages of data model
  calculated = document.recalc_form() 
  calculated = json.dumps(calculated)
  
  return _construct_web_response("server_del_section_callback(%s)" % calculated)

def store_to_file(document, msg_data=None):
  result = document.save_to_file()
  result = json.dumps(result)
  
  return _construct_web_response("save_to_file_handler_gtk(%s)" % result) +\
         _construct_gtk_info_response(msgs.FILE_SAVED, area="message_area")
          
def perform_click(document, msg_data):    
  if not msg_data.has_key("command"):
    return _construct_web_response("perform_click_action_callback(%s)" % "")  
  command = msg_data["command"]  
  if (len(command)):      
    log.debug("Button clicked, command: %s" % command)
    res = document.perform_click_action(command)
  else:
    res = []
  
  events = []
  # remember to put messages in reversed order - to get it back to the execution order :)
  for r in res:
    if len(r) > 1:      
      for child in r:        
        obj = pickle.loads(child.text.decode("base64"))
        events.insert(0, obj)         
    else:
      if r.tag == tags.LASTPARAM_RESULT_TAG:
        for child in r:        
          obj = pickle.loads(child.text.decode("base64"))
          events.insert(0, obj)
      else:  
        obj = pickle.loads(r.text.decode("base64"))
        events.insert(0, obj)
  # go through events, each returns some msg, which should be converted into gtk info response
  msgs = []
  for event in events:
    event_result = event.perform()
    
    if not event_result["show"]:
      continue
    # construct gtk info response from event_result    
    event_name = event.__class__.__name__    
    if event_result["msg_class"] == mc.MSG_LIST:
      msgs.append((event_result["msg_type"], event_name, event_result["msg"]))
    elif event_result["msg_class"] == mc.MESSAGEBOX_OK:
      _show_messagebox(event_result["msg_type"], event_name, event_result["msg"])
    # should we stop with this event? 
    if event.get_break_processing():
      log.info("Processing break occured!")
      break
  
  responses = _construct_web_response("perform_click_action_callback(%s)" % "")   
  for msg in msgs:    
    responses += _construct_gtk_info_response(msg[1]+'-'+msg[2], area="message_area", type=msg[0])    
  return responses



def get_input_info(document, msg_data):
  res = {}
  res[msgs.ATT_ID] = msg_data["id"]
  if (msg_data.has_key("ro")):
    res[msgs.ATT_READONLY] = tools.translate_boolean(msg_data["ro"]) 
  if msg_data.has_key("text") and msg_data["text"]!= "":
    res[msgs.ATT_NOTE] = msg_data["text"]
  bind_id = msg_data["name"]
  res[msgs.ATT_NAME] = bind_id
  required = document.is_required(bind_id)
  res[msgs.ATT_REQUIRED] = tools.translate_boolean(required)
     
  return _construct_gtk_info_response(res)

def clear_input_info(document, msg_data):
  # update info area with empty dictionary
  return _construct_gtk_info_response({"":""})

def set_html_templates(document, rep_sections):
  '''
  Sets templates for html repeating sections
  '''
  counter = 0
  for rs in rep_sections:
    end_of_name = rs.rfind("-rep-")
    repe_name = rs[:end_of_name]
    document.set_repe_section_html(repe_name, rep_sections[rs])
    counter = counter + 1
  
  log.debug("{0} repeating sections templates added".format(str(counter)))
  
  return _construct_web_response("empty_callback()") 

def show_source(document, html):
  tools.open_window_with_html_source(html)
  return _construct_web_response("empty_callback()")
    


