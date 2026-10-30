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
#*    Modified in October 2026 by Richard Winkler Bejr for "Blanketa": ported to Python 3,
#*    separated from the GTK user interface and extended (recalculation, form check, default
#*    values); the git history of the project records each change.

'''
Tools used in xpath extensions and zfo documents.

zfoengine port: only the GUI-less helpers of the original tools.py are kept.
Dropped (GTK / filesystem workflow of the desktop app): init_home_folder,
create_uri, create_fo_id, is_pwd_required, test_zfo_pwd, unpack_zfo,
prepare_zfo_doc, transform_doc_to_html, make_zip, get_file_from_user_gtk,
create_open_dialog, create_save_dialog, open_window_with_html_source,
show_form_data, start_page_uri. The engine gets form.fo bytes from its caller.
'''
import datetime
import logging
import tempfile

import lxml.etree as etree

from . import msgs

log = logging.getLogger("zfo.tools")


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

def parse_filefilter_string(filter_str):
  '''
  Creates list of tuples containing filter name and pattern
  '''
  chunks = filter_str.split("|")
  names = chunks[::2]
  patterns = chunks[1::2]
  res = list(zip(names, patterns, strict=False))
  return res

def create_relative_path(from_path, to_path):
  '''
  Construct xpath expression, that leads from a to b, both are absoulte paths
  e.g:
  /a/b/c and /a/b/d/e/f => ../d/e/f

  Returns common aboslute path and relative path from a to b
  '''
  chunks_patha = from_path.split("/")
  chunks_pathb = to_path.split("/")

  tuples = zip(chunks_patha, chunks_pathb, strict=False)
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
  for _ in range(num_hops_after_common_in_a):
    buff = buff + "../"
  path_from_a_to_b =  buff + relative_b_path

  return common_path, path_from_a_to_b

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
