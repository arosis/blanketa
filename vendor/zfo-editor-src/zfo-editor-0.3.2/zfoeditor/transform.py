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
XSL Transformation for zfos
'''
import lxml.etree as etree
from global_var import Global

def result_html_fname(fo_file):
  return fo_file + ".result.html"

def run_xslt(template_name, fo_file):
  '''
  Runs XSLT transformation.
  template_name - path to XSLT
  fo_file - source file containg fo
  '''
  # create transformer
  t = etree.parse(template_name)
  transform = etree.XSLT(t)
  # run transformation
  f = etree.parse(fo_file)
  scripts_path = Global.get_js_path()
  styles_path = Global.get_css_path()
  
  result = transform(f, PATH_TO_JS="'"+scripts_path+"'", PATH_TO_CSS="'"+styles_path+"'")
  # write the result
  result.write(result_html_fname(fo_file), 
               pretty_print=True, 
               method="html", 
               encoding='UTF-8')

