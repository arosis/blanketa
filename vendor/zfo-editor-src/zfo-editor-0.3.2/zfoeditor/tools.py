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
Tools used in xpath extensions and zfo documents
'''
import datetime
import zfozip
import tempfile
import os
import urllib
import shutil
import lxml.etree as etree

import msgs

import logging
log = logging.getLogger("zfo.tools")
from global_var import Global

import gtk
from os.path import expanduser

class NotAzipException():
  pass

class FormFoNotFoundException():
  pass

def init_home_folder():
  '''
  Creates directory .zfoeditor in users home dor.
  Inside .zfoeditor creates forms directory.
  Returns path to forms directory.
  '''
  homepath = os.path.expanduser('~')
  zfoeditor_path = os.path.join(homepath, '.zfoeditor')
  forms_path = os.path.join(zfoeditor_path, 'forms')
  if not os.path.isdir(zfoeditor_path):      
    os.mkdir(zfoeditor_path) # acl?
    os.mkdir(forms_path) # acl?
    return forms_path
  else:
    # .zfoeditor exists, check forms subdir
    if not os.path.isdir(forms_path):
      os.mkdir(forms_path)
      return forms_path
    return forms_path


def create_uri(fname):    
  uri = 'file://' + urllib.pathname2url(fname)
  log.debug("Created URI : %s " % uri)
  return uri

def create_fo_id(zfo_name):
  '''
  hash the absolute path of zfo
  '''
  import hashlib
  sha1 = hashlib.new('sha1')
  sha1.update(zfo_name)
  fo_id = sha1.hexdigest()
  log.info("ID for %s : %s" % (zfo_name, fo_id))
  return fo_id


def is_pwd_required(zfo_name):
  '''
  Returns True is a password is required for this zfo/zip
  '''
  if not zfozip.is_zipfile(zfo_name):
    raise NotAzipException()
  zip_file = zfozip.ZipFile(zfo_name)  
  members = zip_file.namelist()
  
  if 'form.fo' not in members:
    raise FormFoNotFoundException()
  
  info = zip_file.getinfo('form.fo')
  is_encrypted = info.flag_bits & 0x1
  if is_encrypted:
    return True
  
  return False

def test_zfo_pwd(zfo_name, pwd):
  '''
  Checks zip password by trying to unpack form.fo into
  temporary file.
  '''
  tmpdir = tempfile.mkdtemp()
  zip_file = zfozip.ZipFile(zfo_name)  
  members = zip_file.namelist()
  try:
    not_interesting = zip_file.extract(members[0], path=tmpdir, pwd=pwd)
  except RuntimeError as er:
    log.debug(er)
    return False
  return True  
  
def unpack_zfo(zfo_name, zfo_passwd=None):
  '''
  Extract zfo:
  1. create folder for it in forms dir
  2. extract zip to that location
  Returns path to the FO file and calculated identifier of zfo
  '''
  
  def prepare_form_dir(dir_name):
    if not os.path.exists(dir_name):
      os.mkdir(dir_name)
      os.mkdir(os.path.join(dir_name, "saveform"))
  
  zfo_id = create_fo_id(zfo_name)  
  dir_name = os.path.join(Global.get_forms_path(), zfo_id)
  
  is_zip = zfozip.is_zipfile(zfo_name)
  if is_zip:
    zip_file = zfozip.ZipFile(zfo_name)  
    members = zip_file.namelist()
    
    if 'form.fo' not in members:
      raise FormFoNotFoundException()
        
    # create directory for the extraction    
    prepare_form_dir(dir_name)
    zfo_form_fname = zip_file.extract(members[0], path=dir_name, pwd=zfo_passwd)
    zip_file.close()
  else:    
    # not a zipped fo, only form.fo file
    if not zfo_name.endswith(".fo"):
      raise FormFoNotFoundException()
    # prepare working directory
    prepare_form_dir(dir_name)
    # copy the source fo file to the working directory
    shutil.copy(zfo_name, dir_name)
    zfo_form_fname = os.path.join(dir_name, zfo_name.split(os.path.sep)[-1])
    
    
  #if not zfozip.is_zipfile(zfo_name):
  #  raise NotAzipException()
  
  return zfo_form_fname, zfo_id

def get_template_name(zfo_fo_name):
  return zfo_fo_name+".temp.xsl"

def prepare_zfo_doc(zfo_file, parsed_zfos, zfo_passwd=None, DEVEL_MODE=False):
  import zfo_parser  
    
  zfo_fo_name, fo_id = unpack_zfo(zfo_file, zfo_passwd)
  
  zipped = True
  if zfo_file.endswith(".fo"):
    log.debug("Opening FO file, not ZFO")
    zipped = False
    
  log.debug("Path to FO file is %s " % zfo_fo_name)          
  # template name for xsl transformation
  template = get_template_name(zfo_fo_name)
  
  if fo_id in parsed_zfos.keys():              
    if DEVEL_MODE:
      log.info("Development mode - parsing zfo and template on each request")
      # in development, re-prepare template every turn (template is not stable yet)
      document = zfo_parser.parse_fo(zfo_fo_name, zipped)                
      #prepare template - read original and change it for the needs of current xml      
      zfo_parser.prepare_template(document.prefix_map, Global.get_xsl_path()+"/fo2html.xsl", template)
    else:
      # this ZFO has already been parsed, do not add anything to the map
      log.info("Zfo and template are already prepared")
      document = parsed_zfos[fo_id]                
  else:
    #parse fo - return its content and map of namespace prefixes 
    document = zfo_parser.parse_fo(zfo_fo_name, zipped)
    # add fo to "list of ready fos"
    parsed_zfos[fo_id] = document              
    #prepare template    
    zfo_parser.prepare_template(document.prefix_map, Global.get_xsl_path()+"/fo2html.xsl", template)
  
  document.opened_from_path = zfo_file
  return fo_id, zfo_fo_name, document

def transform_doc_to_html(zfo_fo_name):
  import transform
    
  template = get_template_name(zfo_fo_name)
  
  html_fname = transform.result_html_fname(zfo_fo_name)
  
  if not os.path.isfile(html_fname):
    transform.run_xslt(template, zfo_fo_name)
  
  return os.path.abspath(html_fname)


def create_fmt_str(string):
  string = string.replace("HH", "%H")
  string = string.replace("hh", "%H")
    
  string = string.replace("MM", "%M")
  string = string.replace("mm", "%M")
    
  string = string.replace("SS", "%S")
  string = string.replace("ss", "%S")
  
  return string

def get_curr_time(utc, format):    
  if utc:
    now = datetime.datetime.utcnow()
  else:
    now = datetime.datetime.now()
        
  if format is not None:
    f = create_fmt_str(format)
    res = now.strftime(f)
  else:
    res = str(now)
    
  return res

def get_last_match_idx(re_iterator, type):
  '''
  Gets last occurence of regexp from the iterator object (re_iterators)
  type:
  start - returns start index
  end - returns end index
  span - returns span tuple (start, end)
  '''
  tmp = []
  for match in re_iterator:
    if type=="start":
      tmp.append(match.start())
    elif type=="end":
      tmp.append(match.end())
    elif type =="span":
      tmp.append(match.span())
    else:
      raise Exception("Unknown type of index: %s" % type)
  if len(tmp) == 0:
    return None
  else:
    return tmp[len(tmp)-1]

def extract_repetition_parents_info(repe_string):
  '''
  Information about repetition parents
  '''
  if repe_string is None:
    return None
  
  rep_data = {}
      
  tokens = repe_string.split('|||')
  for token in tokens:
    type_sep = token.find('---')        
    rep_start = token.rfind("-rep-")
    # cut repetition name
    rep_name = token[type_sep + len('---'):rep_start]
    # cut repetition index (format name-rep-n)
    di = token.rfind("-")
    idx = token[di+1:]
    
    rep_data[rep_name] = int(idx,10)
      
  return rep_data

def create_event_response(info_type, info_msg, data=None, alert_msg=None):
  '''
  This is returned to client when calling 'perform_click_action'.
  info_type - type of information. Could be info/error/warn
  info_msg - message that will be printed to the info area
  data - if the reurn value should contain some data, contains dictionary of binds/values
  alert_msg - message that should be alerted
  '''
  res = {}
  res['type'] = info_type
  res['msg'] = info_msg
  res['data'] = data 
  res['alert_msg'] = alert_msg
  return res

def translate_boolean(bool_value):
  '''
  Translates boolean value to string yes/no (ano/ne...)
  ''' 
  if bool_value:
    return msgs.YES
  else:
    return msgs.NO
   
def make_zip(storage_path, user_selected_path=None):
  '''
  Creates zip archive containg form.fo file, which
  contains user-changed zfo form.
  '''
  try:      
    zfilename = user_selected_path
       
    zout = zfozip.ZipFile(zfilename, "w")      
    zout.write(storage_path, "form.fo")      
    zout.close()    
    return zfilename
    
  except Exception as ex:
    log.error(ex)
    return None

def transform_zfo_filepath(filepath):
    '''
    Transforms windows-like path used in zfo to the linux-style path
    '''
    filepath = filepath.strip("'")
    filepath = filepath.strip('"')
    filepath = filepath.strip("{}")
    chunks = filepath.split("\\")
    for chunk in chunks:
      if chunk == "%TEMP%":
        idx = chunks.index(chunk)
        chunks[idx] = tempfile.gettempdir()
    res = "/".join(chunks)
    return res

def get_file_from_user_gtk(filter_str=None):  
  dialog = create_open_dialog(filter_str)
  result = dialog.run()  
  if result == gtk.RESPONSE_OK:
    fname = dialog.get_filename() 
    dialog.destroy()
    return fname
  else:
    dialog.destroy()
    return None

def create_open_dialog(filter_string=None):
  '''
  Creates dialog for selecting file. Filter_string
  is a string from ZFO form which specifies file filters in form FF|FF...
  where FF consist of NAME|PATTERN.
  If filter_string is None, default ext_filter is created for *.zfo
  '''
  dialog = gtk.FileChooserDialog(msgs.OPEN_ZFO_CAPTION,
                                  None,
                                  gtk.FILE_CHOOSER_ACTION_OPEN,
                                  (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                   gtk.STOCK_OPEN, gtk.RESPONSE_OK))
  
  dialog.set_default_response(gtk.RESPONSE_CANCEL)
  last_path = Global.get_last_opened_path()
  if last_path is None:
    dialog.set_current_folder(expanduser('~'))
  else:
    dialog.set_current_folder(last_path)
  
  if filter_string is None:
    ext_filter = gtk.FileFilter()
    ext_filter.set_name("ZFO/FO")   
    ext_filter.add_pattern("*.zfo")
    ext_filter.add_pattern("*.fo")    
    dialog.add_filter(ext_filter)
  else:
    filters = parse_filefilter_string(filter_string)
    for f in filters:
      ext_filter = gtk.FileFilter()
      ext_filter.set_name(f[0])   
      patterns = f[1].split(";")
      for pattern in patterns:
        if pattern != "":
          ext_filter.add_pattern(pattern)
      dialog.add_filter(ext_filter)
   
  return dialog

def create_save_dialog():  
  dialog = gtk.FileChooserDialog(msgs.SAVE_ZFO_CAPTION,
                                  None,
                                  gtk.FILE_CHOOSER_ACTION_SAVE,
                                  (gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                                   gtk.STOCK_OPEN, gtk.RESPONSE_OK))
  
  dialog.set_default_response(gtk.RESPONSE_CANCEL)
  # is this ok? do we need to store to the last location by default?
  last_path = Global.get_last_saved_path()
  if last_path is None:
    dialog.set_current_folder(expanduser('~'))
  else:
    dialog.set_current_folder(last_path)
  
  ext_filter = gtk.FileFilter()
  ext_filter.set_name("ZFO")   
  ext_filter.add_pattern("*.zfo")
  dialog.add_filter(ext_filter)
  
  return dialog

def parse_filefilter_string(filter_str):
  '''
  Creates list of tuples containing filter name and pattern
  '''
  chunks = filter_str.split("|")  
  names = chunks[::2]
  patterns = chunks[1::2]
  res = zip(names, patterns)  
  return res

def open_window_with_html_source(html):
  '''
  Opens window with html source of form.
  Only for debug purposes.
  '''
  window = gtk.Window(gtk.WINDOW_TOPLEVEL)    
  window.set_title(msgs.SOURCE_WINDOW_TITLE)
  window.set_default_size(500, 500)
  
  tb = gtk.TextBuffer()
  tb.set_text(html)
  tv = gtk.TextView(tb)
  tv.set_editable(False)
      
  scroller = gtk.ScrolledWindow()
  scroller.add(tv)
  scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
  
  window.add(scroller)
  window.show_all()

def create_relative_path(from_path, to_path):
  '''
  Construct xpath expression, that leads from a to b, both are absoulte paths
  e.g:
  /a/b/c and /a/b/d/e/f => ../d/e/f
  
  Returns common aboslute path and relative path from a to b
  '''
  chunks_patha = from_path.split("/")
  chunks_pathb = to_path.split("/")
  
  tuples = zip(chunks_patha, chunks_pathb)
  cnt = 0
  for t in tuples:
    if t[0] == t[1]:
      cnt += 1
    else:
      break
  common_path = "/".join(chunks_patha[:cnt])
  if common_path == "":
    common_path = "/"
    
  relative_b_path = "/".join(chunks_pathb[cnt:])
  
  num_hops_after_common_in_a = len(from_path.split("/")[cnt:])
  buff = ""
  for i in xrange(num_hops_after_common_in_a):
    buff = buff + "../"
  path_from_a_to_b =  buff + relative_b_path
  
  return common_path, path_from_a_to_b

def show_form_data(doc):
  '''
  Shows window with the data tree of specified document
  '''
  model = doc.get_serialized_datamodel()
  window = gtk.Window(gtk.WINDOW_TOPLEVEL)    
  window.set_title(msgs.MODEL_WINDOW_TITLE)
  window.set_default_size(500, 500)
  
  tb = gtk.TextBuffer()
  tb.set_text(model)
  tv = gtk.TextView(tb)
  tv.set_editable(False)
      
  scroller = gtk.ScrolledWindow()
  scroller.add(tv)
  scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
  
  window.add(scroller)
  window.show_all()

def start_page_uri():
  start_file = os.path.abspath(Global.get_startpage_path() + '/start.html')
  return create_uri(start_file)

# tag for temporal element

TEMPORAL_ELEM_TAG = "some-foo-tmp-elem-whose-name-is-not-among-children"

def create_temp_elem(parent, elem_text):
  '''
  Creates temporal xml element - it represents attribute, but is an etree.Element.
  Do this workaround to run xpath expression on an attribute.
  '''
  log.debug("Creating temporal element")
  tmp = etree.Element(TEMPORAL_ELEM_TAG)
  tmp.text = "%s" % elem_text       
  parent.append(tmp)   
  return tmp

def delete_temp_elem(parent, temp_elem):
  '''
  Deletes previously created element
  '''
  parent.remove(temp_elem)
  log.debug("Temporal element removed")