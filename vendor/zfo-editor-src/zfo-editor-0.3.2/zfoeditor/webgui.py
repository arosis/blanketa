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
"""
Implements browser widget.
Browser idea from http://www.aclevername.com/articles/python-webgui/
9.8.2010
"""
import time
import Queue
import thread


import gtk
import gobject
import pango

import msgs

import logging
log = logging.getLogger("zfo.webgui")

try:
  import webkit
  have_webkit = True
except:
  have_webkit = False


class UseWebKit: pass

if have_webkit:
  use = UseWebKit
else:
  raise Exception('Failed to load webkit modules')


class WebKitMethods(object):

  @staticmethod
  def create_browser():
    return webkit.WebView()

  @staticmethod
  def inject_javascript(browser, script):
    browser.execute_script(script)

  @staticmethod
  def connect_title_changed(browser, callback):
    def callback_wrapper(webview, frame, title): callback(title)
    browser.connect('title-changed', callback_wrapper)

  @staticmethod
  def open_uri(browser, uri):
    browser.open(uri)

if use is UseWebKit:
  implementation = WebKitMethods


def asynchronous_gtk_message(fun):

  def worker((function, args, kwargs)):
    apply(function, args, kwargs)

  def fun2(*args, **kwargs):
    gobject.idle_add(worker, (fun, args, kwargs))

  return fun2


def synchronous_gtk_message(fun):

  class NoResult: pass

  def worker((R, function, args, kwargs)):
    R.result = apply(function, args, kwargs)

  def fun2(*args, **kwargs):
    class R: result = NoResult
    gobject.idle_add(worker, (R, fun, args, kwargs))
    while R.result is NoResult: time.sleep(0.01)
    return R.result

  return fun2


def _create_menubar(window, open_file_function, 
                    save_file_function, save_as_func, 
                    close_form_func,
                    show_model_function, 
                    show_source_function, quit_function=None):
  '''
  Creates upper menu bar with basic buttons. (File -> Open, Save, Close)
  '''
  file_menu = gtk.Menu()
  
  accel_group = gtk.AccelGroup()
  window.add_accel_group(accel_group)
  
  # Ctrl+M to show form model
  accel_group.connect_group(ord('M'), gtk.gdk.CONTROL_MASK, 
                            gtk.ACCEL_VISIBLE, show_model_function)
  
   # Ctrl+U to show form model
  accel_group.connect_group(ord('U'), gtk.gdk.CONTROL_MASK, 
                            gtk.ACCEL_VISIBLE, show_source_function)
  
  
  # open file
  openfile_item = gtk.MenuItem(msgs.MENU_OPEN)
  openfile_item.add_accelerator('activate',
                                accel_group,
                                ord('O'),
                                gtk.gdk.CONTROL_MASK,
                                gtk.ACCEL_VISIBLE)
  openfile_item.connect('activate', open_file_function)
  file_menu.append(openfile_item)
  openfile_item.show()
  
  # save file
  savefile_item = gtk.MenuItem(msgs.MENU_SAVE)
  savefile_item.add_accelerator('activate',
                                accel_group,
                                ord('S'),
                                gtk.gdk.CONTROL_MASK,
                                gtk.ACCEL_VISIBLE)
  savefile_item.connect('activate', save_file_function)
  file_menu.append(savefile_item)
  savefile_item.show()
  
  # save file as
  savefile_as_item = gtk.MenuItem(msgs.MENU_SAVE_AS)
  #savefile_as_item.add_accelerator('activate',
  #                              accel_group,
  #                              ord('S'),
  #                              gtk.gdk.CONTROL_MASK,
  #                              gtk.ACCEL_VISIBLE)
  savefile_as_item.connect('activate', save_as_func)
  file_menu.append(savefile_as_item)
  savefile_as_item.show()
  
  # save file as
  close_item = gtk.MenuItem(msgs.MENU_CLOSE)
  close_item.add_accelerator('activate',
                                accel_group,
                                ord('W'),
                                gtk.gdk.CONTROL_MASK,
                                gtk.ACCEL_VISIBLE)
  close_item.connect('activate', close_form_func)
  file_menu.append(close_item)
  close_item.show()
  
  # quit app
  quit_item = gtk.MenuItem(msgs.MENU_QUIT)    
  quit_item.add_accelerator('activate',
                            accel_group,
                            ord('Q'),
                            gtk.gdk.CONTROL_MASK,
                            gtk.ACCEL_VISIBLE)
  
  file_menu.append(quit_item)
  
  quit_item.connect('activate', quit_function)
  quit_item.show()
  
  # create menu bar
  menu_bar = gtk.MenuBar()
  menu_bar.show()
  file_item = gtk.MenuItem(msgs.MENU_FILE)
  file_item.show()
  file_item.set_submenu(file_menu)
  menu_bar.append(file_item)
    
  return menu_bar

def _create_msg_list_view():
  '''
  Creates TreeView with ListModel which displays
  messages from application.
  '''
  lstore = gtk.ListStore(str)
      
  tview = gtk.TreeView(lstore)
  
  col = gtk.TreeViewColumn()
  
  cell = gtk.CellRendererText()    
  
  col.pack_start(cell)
  col.set_attributes(cell, markup=0)
  
  cell.set_property("wrap-width", 350)
  cell.set_property("wrap-mode", pango.WRAP_WORD)
  
  tview.append_column(col)
  tview.set_grid_lines(gtk.TREE_VIEW_GRID_LINES_HORIZONTAL)
  
  return lstore, tview        

def _create_field_info_view():
  '''
  Creates TreeView with ListModel, which holds and
  displays information about clicked form fields.
  '''
  lstore = gtk.ListStore(str, str)
  
  tview = gtk.TreeView(lstore)
  
  col_att = gtk.TreeViewColumn(msgs.ATTRIBUTE)
  cell_att = gtk.CellRendererText()
  col_att.pack_start(cell_att)
  col_att.set_attributes(cell_att, text=0)
  
  col_val = gtk.TreeViewColumn(msgs.VALUE)
  cell_val = gtk.CellRendererText()
  col_val.pack_start(cell_val)
  col_val.set_attributes(cell_val, text=1)
  
  cell_val.set_property("wrap-width", 350)
  cell_val.set_property("wrap-mode", pango.WRAP_WORD)
  
  tview.append_column(col_att)
  tview.append_column(col_val)
  
  tview.set_grid_lines(gtk.TREE_VIEW_GRID_LINES_BOTH)
  return lstore, tview

def _prepare_browser_panel():
  '''
  Prepares panel with browser
  '''
  browser_scroll = gtk.ScrolledWindow()    
  browser = implementation.create_browser()    
  browser_scroll.add(browser)
  browser_scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
  return browser_scroll, browser 
  
def _prepare_info_panel():
  '''
  Prepares panel with information: top will be field info,
  bottom a message area
  '''
  info_frame_up = gtk.Frame(msgs.FIELD_INFO)
  field_info_list_model, field_info_tv = _create_field_info_view()
  info_frame_up.add(field_info_tv)
  
  info_frame_up.set_label_align(0.5, 0.0)
  info_frame_up.set_shadow_type(gtk.SHADOW_ETCHED_OUT)
  
  scroll_upper_info = gtk.ScrolledWindow()
  scroll_upper_info.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
  scroll_upper_info.add_with_viewport(info_frame_up)
  
  info_frame_down = gtk.Frame()
  msg_store, treeview = _create_msg_list_view()    
  info_frame_down.add(treeview)    
  
  info_frame_down.set_label_align(0.5, 0.0)
  info_frame_up.set_shadow_type(gtk.SHADOW_ETCHED_OUT)
  
  scroll_lower_info = gtk.ScrolledWindow()
  scroll_lower_info.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
  scroll_lower_info.add_with_viewport(info_frame_down)
  
  right_pane = gtk.VBox(homogeneous=True)
  right_pane.pack_start(scroll_upper_info)
  right_pane.pack_start(scroll_lower_info)    
  
  right_scroll = gtk.ScrolledWindow()
  right_scroll.add_with_viewport(right_pane)
  right_scroll.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_NEVER)
  right_scroll.set_size_request(260, -1)
  
  return right_scroll, msg_store, field_info_list_model

def _prepare_web_functions(browser, echo=True):
  '''
  Prepares functions for sending and receiving messages,
  binds title_changed event to adding new  messages to queue.
  '''
  message_queue = Queue.Queue()

  def title_changed(title):
      if title != 'null': message_queue.put(title)

  implementation.connect_title_changed(browser, title_changed)
  
  def web_recv():
      MAX_ECHO_LENGTH = 750
      if message_queue.empty():
          return None
      else:
          msg = message_queue.get()
          if echo: 
            if len(msg) < MAX_ECHO_LENGTH:
              log.info("Received msg: %s" % msg)
            else:
              log.info("Received msg (truncated): %s ...." % msg[:MAX_ECHO_LENGTH])
          return msg

  def web_send(msg):
      MAX_ECHO_LENGTH = 750
      if echo:
        if len(msg) < MAX_ECHO_LENGTH: 
          log.info("Sent msg: %s" % msg)
        else:
          log.info("Sent msg(truncated): %s" % msg[:MAX_ECHO_LENGTH])
      asynchronous_gtk_message(implementation.inject_javascript)(browser, msg)
  
  return web_recv, web_send
  
def launch_browser(uri, open_file_function, save_file_function, save_as_func, close_form_func,
                   show_model_function, show_source_function, quit_function=None):

  window = gtk.Window()    
  window.set_title(msgs.APP_TITLE)
  window.maximize()

  box = gtk.VBox(homogeneous=False, spacing=0)
  window.add(box)

  if quit_function is not None:
      menu_bar = _create_menubar(window, open_file_function, 
                                 save_file_function, save_as_func, 
                                 close_form_func,
                                 show_model_function, 
                                 show_source_function, quit_function)
      box.pack_start(menu_bar, expand=False, fill=True, padding=0)  
      
      window.connect('destroy', quit_function)
  
  paned = gtk.HPaned()
  
  browser_panel, browser = _prepare_browser_panel()
  info_panel, msg_store, field_info_list_model = _prepare_info_panel()
  
  #hbox.pack_start(browser_scroll, expand=True, fill=True, padding=0)
  paned.pack1(browser_panel, True, False)    
  paned.pack2(info_panel, True, False)    
  paned.set_position(1500)
  
  box.pack_start(paned)
  status_bar = gtk.Statusbar()
  box.pack_end(status_bar, expand=False)
  
  window.set_default_size(800, 600)
  window.show_all()
  
  web_recv, web_send = _prepare_web_functions(browser)
  
  implementation.open_uri(browser, uri)
  
  return browser, status_bar, web_recv, web_send, msg_store, field_info_list_model

def load_browser_page(browser, new_uri):
  implementation.open_uri(browser, new_uri)
    
def start_gtk_thread():
  # Start GTK in its own thread:
  gtk.gdk.threads_init()
  thread.start_new_thread(gtk.main, ())

def kill_gtk_thread():
  asynchronous_gtk_message(gtk.main_quit)()
