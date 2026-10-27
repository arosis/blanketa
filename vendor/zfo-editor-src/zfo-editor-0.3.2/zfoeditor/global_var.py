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
"Global variables" module. Contains variables needed across application.
'''

class Global(object):
  quit = False
  save_function = None
  document = None
  last_opened_path = None
  last_saved_path = None
  
  @classmethod
  def set_quit(cls, *args, **kwargs):
    cls.quit = True
  
  @classmethod
  def set_doc(cls, doc):
    cls.document = doc
  
  @classmethod
  def get_doc(cls):
    return cls.document
        
  @classmethod
  def set_browser(cls, browser):
    cls.browser = browser
  
  @classmethod
  def get_browser(cls):
    return cls.browser
    
  @classmethod
  def set_statusbar(cls, statusbar):
    cls.status_bar = statusbar
  
  @classmethod
  def get_statusbar(cls):
    return cls.status_bar
        
  @classmethod
  def set_message_list(cls, message_list_store):
    cls.message_list = message_list_store
      
  @classmethod
  def get_message_list(cls):
    return cls.message_list
  
  @classmethod
  def set_fieldinfo_list(cls, fieldifno_list_store):
    cls.fieldinfo_list = fieldifno_list_store
      
  @classmethod
  def get_fieldinfo_list(cls):
    return cls.fieldinfo_list
  
  @classmethod
  def set_save_func(cls, save_func):
    cls.save_func = staticmethod(save_func)
      
  @classmethod
  def get_save_func(cls):
    return cls.save_func
    
  @classmethod
  def set_forms_path(cls, forms_abs_path):
    cls.forms_path = forms_abs_path
  
  @classmethod
  def get_forms_path(cls):
    return cls.forms_path
    
  @classmethod
  def set_js_path(cls, js_abs_path):
    cls.js_path = js_abs_path
  
  @classmethod
  def get_js_path(cls):
    return cls.js_path
    
  @classmethod
  def set_css_path(cls, css_abs_path):
    cls.css_path = css_abs_path
  
  @classmethod
  def get_css_path(cls):
    return cls.css_path
  
  @classmethod
  def set_xsl_path(cls, xsl_abs_path):
    cls.xsl_path = xsl_abs_path
  
  @classmethod
  def get_xsl_path(cls):
    return cls.xsl_path
    
  @classmethod
  def set_startpage_path(cls, startpage_abs_path):
    cls.startpage_path = startpage_abs_path
  
  @classmethod
  def get_startpage_path(cls):
    return cls.startpage_path
  
  @classmethod
  def set_cfg_path(cls, cfg_abs_path):
    cls.cfg_path = cfg_abs_path
    
  @classmethod
  def get_cfg_path(cls):
    return cls.cfg_path
  
  @classmethod
  def set_cutns_transform_func(cls, cutns_transform_func):
    '''
    Sets transformation function, that transforms xml into xml 
    without namespace prefixes.
    '''
    cls.cutns_transform_func = cutns_transform_func
  
  @classmethod
  def get_cutns_transform_func(cls):
    return cls.cutns_transform_func
  
  @classmethod
  def get_last_opened_path(cls):
    return cls.last_opened_path
  
  @classmethod
  def set_last_opened_path(cls, path):
    cls.last_opened_path = path
  
  @classmethod
  def get_last_saved_path(cls):
    return cls.last_saved_path
  
  @classmethod
  def set_last_saved_path(cls, path):
    cls.last_saved_path = path
  
  @classmethod
  def set_web_send_func(cls, func):
    cls.web_send_func = staticmethod(func)
  
  @classmethod
  def get_web_send_func(cls):
    return cls.web_send_func
