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
Main module of ZFO Editor. Creates application window 
and starts event-loop for managing application and form events.
'''

import signal
import os
import os.path
import time

import json
import gtk
import sys
import lxml.etree as etree

from webgui import start_gtk_thread
from webgui import launch_browser

from webgui import synchronous_gtk_message

from webgui import kill_gtk_thread


import logging
import logging.config

import msgs
import msg_types

log = logging.getLogger("zfo.zfo_gui")

import tools
import dir_finder


'''
dictionary of parsed zfos - use them to store data coming from POST
'''
parsed_zfos = {}

from global_var import Global

PASSWORD_CANCELLED = -1

def save_zfo():
  return save_zfo_file(None)

def save_zfo_as():
  return save_zfo_file_as(None)

def save_zfo_file(cls, *args, **kwargs):
  if Global.get_doc() is None:        
      add_message_to_list(Global.get_message_list(), msgs.NO_ZFO_OPENED, type=msg_types.ERROR)
      return
    
  res = Global.get_doc().save_to_file()
  if res['type'] == msg_types.ERROR: 
    add_message_to_list(Global.get_message_list(), msgs.FILE_SAVING_FAIL)
  else:
    add_message_to_list(Global.get_message_list(), msgs.FILE_SAVING_OK.format(res["filename"]))
  return res["filename"]

def save_zfo_file_as(cls, *args, **kwargs):    
  '''
  Function that shows save dialog and stores document to the specified file.
  '''
  if Global.get_doc() is None:        
      add_message_to_list(Global.get_message_list(), msgs.NO_ZFO_OPENED, type=msg_types.ERROR)
      return
  
  dialog = tools.create_save_dialog()
  response = dialog.run()
  if response == gtk.RESPONSE_OK:
    save_name = dialog.get_filename()
    # add zfo or fo suffix
    if (not save_name.endswith(".zfo")) or (not save_name.endswith(".fo")):
      if Global.get_doc().comes_from_zip:        
        save_name += ".zfo"
      else:
        save_name += ".fo"
    log.info("Saving to file %s" %save_name)
    dialog.destroy()
    
    res = Global.get_doc().save_to_file(user_selected_path=save_name)
    if res['type'] == msg_types.ERROR: 
      add_message_to_list(Global.get_message_list(), msgs.FILE_SAVING_FAIL)
    else:
      add_message_to_list(Global.get_message_list(), msgs.FILE_SAVING_OK.format(save_name))
    return save_name
      
  dialog.destroy()
  return None

def show_password_request(path_to_zfo):  
  '''
  Shows dialog to input password.
  '''  
  d = gtk.Dialog( title=msgs.ZFO_PASS_PROTECTED,              
                    buttons=(gtk.STOCK_OK, gtk.RESPONSE_OK,
                           gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL),
                    flags = gtk.DIALOG_DESTROY_WITH_PARENT
                    )
  box = gtk.VBox()
  d.vbox.pack_start(box)
  box.show()
  info = gtk.Label(msgs.ZFO_PASS_INFO.format(path_to_zfo.split('/')[-1]))
  #info.set_max_width_chars(80)  
  box.pack_start(info)
  info.show()
  hbox = gtk.HBox()
  label = gtk.Label(msgs.ZFO_PASS_LABEL)
  hbox.pack_start(label)
  label.show()
  entry = gtk.Entry()
  entry.set_visibility(False)
  hbox.pack_start(entry)
  hbox.show()
  entry.show()
  # the following will trigger OK response when enter is hit in the entry
  entry.connect( "activate", lambda w: d.response( gtk.RESPONSE_OK)) 
  # run the dialog
  box.pack_end(hbox)
  result = d.run()    
  if result == gtk.RESPONSE_OK:
    password = entry.get_text()    
  else:
    password = None  
  d.destroy()
  return password

def get_password(path_to_zfo):
  '''
  Tries to get correct zip file password from the user.
  '''
  if path_to_zfo.endswith(".fo"):
    # no password required for fo
    return None
  if tools.is_pwd_required(path_to_zfo):
    pass_ok = False
    while not pass_ok:
      # show window, get user input
      password = show_password_request(path_to_zfo)
      # if the reply is No, raise expcetion to close the open file dialog
      if password is None:
        return PASSWORD_CANCELLED     
      # if the reply is ok, try the password
      is_pass_ok = tools.test_zfo_pwd(path_to_zfo, password)
      if is_pass_ok:
        return password
      else:
        msgd = gtk.MessageDialog(flags=gtk.DIALOG_MODAL, 
                                 type=gtk.MESSAGE_ERROR,
                                 buttons=gtk.BUTTONS_OK)
        msgd.set_markup(msgs.ZFO_PASS_INVALID)
        msgd.run()
        msgd.destroy()
        continue    
  else:
    # no password required
    return None

def open_file_handler(path_to_zfo):
  pwd = get_password(path_to_zfo)
  if pwd == PASSWORD_CANCELLED:
    log.debug("User did not provide password, closing openfile dialog")
    return 
  fo_id, fo_name, document = tools.prepare_zfo_doc(path_to_zfo, parsed_zfos, zfo_passwd=pwd, DEVEL_MODE=True)
  
  log.debug("ID of opened zfo: {0}, zfo path is {1}".format(fo_id, path_to_zfo))
  Global.set_doc(document)
  
  html_fname = tools.transform_doc_to_html(fo_name)
        
  uri = tools.create_uri(html_fname)
  log.debug("Sent to browser: %s " % html_fname)
  Global.get_statusbar().push(0, msgs.ZFO_OPENED.format(path_to_zfo))
  return uri
        
def open_zfo_file(cls, *args, **kwargs):
  '''
  Function that handles open file action.s
  '''
  dialog = tools.create_open_dialog()
  response = dialog.run()
  
  if response == gtk.RESPONSE_OK:
      zfo_name = dialog.get_filename()
      # set last open path
      Global.set_last_opened_path(os.path.split(zfo_name)[0])      
      try:
        uri = open_file_handler(zfo_name)
        if uri is None:
          dialog.destroy()
          return                
        Global.get_browser().open(uri)
      except tools.NotAzipException:
        add_message_to_list(Global.get_message_list(), msgs.NOT_A_ZFO, msg_types.ERROR)
      except tools.FormFoNotFoundException:
        add_message_to_list(Global.get_message_list(), msgs.NO_FORM_FO_INCLUDED, msg_types.ERROR)
  
  dialog.destroy()

def close_zfo_file(cls, *args, **kwargs):
  '''
  Closes current zfo form and displays start page.
  '''
  if Global.get_doc() is None:
    return
  uri = tools.start_page_uri()
  Global.get_browser().open(uri)
  Global.set_doc(None)
  add_message_to_list(Global.get_message_list(), 
                      msgs.FORM_CLOSED)

def add_message_to_list(list_store, message, type=msg_types.INFO):
  wrapper = "<span foreground='{0}'>{1}</span>"
  if type == msg_types.INFO:
    list_store.insert(0, [wrapper.format("#2E7D2A", message)])
  if type == msg_types.WARN:
    list_store.insert(0, [wrapper.format("orange",message)])
  if type == msg_types.ERROR:
    list_store.insert(0, [wrapper.format("red", message)])

def show_field_info(fieldinfo_list, field_info_dict):
  fieldinfo_list.clear()
  for key in field_info_dict:
    fieldinfo_list.append([key, field_info_dict[key]])

def show_zfo_model(cls, *args, **kwargs):
  '''
  Shows window with data model of opened form.
  '''
  if Global.get_doc() is None:        
    add_message_to_list(Global.get_message_list(), msgs.NO_ZFO_OPENED, type=msg_types.ERROR)
    return
  doc = Global.get_doc()
  if doc is None:
    return
  else:
    tools.show_form_data(doc)

def show_html_source(cls, *args, **kwargs):
  '''
  Sends request to the webkit component to return current
  html source of the opened form.
  Window is opened and filled with html in response from 
  get_html_source() 
  '''
  if Global.get_doc() is None:
    return
  sendfunc = Global.get_web_send_func()
  sendfunc("get_html_source()")
  
def main(selected_zfo=None):    
  start_gtk_thread()
  
  uri = tools.start_page_uri()
  
  browser, statusbar, web_recv, web_send, msg_list, field_info = \
      synchronous_gtk_message(launch_browser)(uri,
                                              open_zfo_file,
                                              save_zfo_file,
                                              save_zfo_file_as,
                                              close_zfo_file,
                                              show_zfo_model,
                                              show_html_source,
                                              quit_function=Global.set_quit)
  
  statusbar.push(0, msgs.CHOOSE_ZFO)    
  
  Global.set_browser(browser)
  Global.set_statusbar(statusbar)    
  Global.set_message_list(msg_list)
  Global.set_fieldinfo_list(field_info)
  Global.set_save_func(save_zfo)
  
  Global.set_web_send_func(web_send)
  
  if selected_zfo is not None:
    gtk.gdk.threads_enter()  
    try:
      user_uri = open_file_handler(selected_zfo)   
      if user_uri is not None:       
        Global.get_browser().open(user_uri)
    except tools.NotAzipException:
      add_message_to_list(Global.get_message_list(), msgs.NOT_A_ZFO, msg_types.ERROR)
    except tools.FormFoNotFoundException:
      add_message_to_list(Global.get_message_list(), msgs.NO_FORM_FO_INCLUDED, msg_types.ERROR)
          
    gtk.gdk.threads_leave()
  
  import gtkgui_events_handlers as geh
  
  while not Global.quit:
      msg = web_recv()        
      
      if msg:
          # start of "critical section"
          gtk.gdk.threads_enter()
          document = Global.get_doc()
          
          try:
            msg = json.loads(msg)            
          except Exception as ex:
            # probably some link clicked, ignore it
            gtk.gdk.threads_leave()
            continue
          
          if not 'type' in msg:
            log.warning('Message type missing')           
          else:
            msg_type = msg['type']
            msg_data = msg['data']
            
            if (msg_type == "document.ready"):
              add_message_to_list(Global.get_message_list(), msgs.FORM_READY, msg_types.INFO)
              document.prepare_attachment_binds()
            if hasattr(geh, msg_type):
              
              func = getattr(geh, msg_type)
              responses = func(document, msg_data)
              for res in responses:
                if res["type"] == "javascript":            
                  web_send(res["data"])
                elif res["type"] == "gtk":
                  if res["info_area"] == "field_info":
                    field_info = res["data"]["to_display"]                                            
                    show_field_info(Global.get_fieldinfo_list(), field_info)                                            
                  elif res["info_area"] == "message_area":
                    message = res["data"]["to_display"]                      
                    add_message_to_list(Global.get_message_list(), message, res["info_type"])
                else:
                  log.error("Unknown response type : %s" % res["type"])
            else:
              log.warning('Handler for %s does not exist.' % msg_type)
          # end of "critical section"
          gtk.gdk.threads_leave()
      else:
        time.sleep(0.1)
        
        
            
                
def my_quit_wrapper(fun, *args, **kwargs):
  signal.signal(signal.SIGINT, Global.set_quit)
  def fun2(*args, **kwargs):
      try:
          x = fun(*args, **kwargs)
      finally:          
          Global.set_quit()
          kill_gtk_thread()
      return x
  return fun2

def start_app():      
  cfgdir = dir_finder.find_dir('config')
  logging.config.fileConfig(os.path.join(cfgdir, "log.conf"))
  Global.set_cfg_path(cfgdir)
    
  path_to_forms = tools.init_home_folder()
  Global.set_forms_path(path_to_forms)
  
  jsdir = dir_finder.find_dir('scripts')
  Global.set_js_path(jsdir)
  log.info("Scripts path is " + jsdir)
  
  cssdir = dir_finder.find_dir('styles')
  Global.set_css_path(cssdir)
  log.info("Styles path is " + cssdir)
  
  xsldir = dir_finder.find_dir('xslt')
  Global.set_xsl_path(xsldir)
  log.info("XSLT path is " + xsldir)
  
  startpagedir = dir_finder.find_dir('start_page')
  Global.set_startpage_path(startpagedir)
  log.info("Startpagedir path is " + startpagedir)
  
  nscut_xslt = os.path.join(xsldir, "ns_cut.xsl")
  log.info("XSLT for namespace cut " + nscut_xslt)
  if os.path.exists(nscut_xslt):
    f = open(nscut_xslt, "r")
    t = f.read()
    tree = etree.fromstring(t)
    template = etree.XSLT(tree)
    Global.set_cutns_transform_func(template)
  else:
    log.error("XSLT for cutting xml namespace prefixes not found")
    return
  
  if len(sys.argv) > 1:
    my_quit_wrapper(main)(sys.argv[1])
  else:
    my_quit_wrapper(main)()


if __name__ == '__main__': 
  start_app()
    
