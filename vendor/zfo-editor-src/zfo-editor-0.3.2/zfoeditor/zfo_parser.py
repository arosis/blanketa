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
Used to xml-parse zfo forms.
'''
import logging
log = logging.getLogger("zfo.zfo_parser")

import lxml.etree as etree
import zfo_doc

DOCTYPE = '''
<!DOCTYPE xsl:stylesheet [
  <!ENTITY anchor "<xsl:apply-templates select='@id' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
  <!ENTITY add-style "<xsl:call-template name='add-style-attribute' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
  <!ENTITY add-onclick "<xsl:call-template name='add-onclick-attribute' xmlns:xsl='http://www.w3.org/1999/XSL/Transform'/>">
]>'''

def parse_xmlns(file):
  '''
  Parse xml and return parsed ElementTree with
  list of used prefixes of namespaces.
  '''  
  events = "start", "start-ns"
  
  root = None  
  # get all prefixes from the document - add to xsl all those who are not
  # dsig, fo or fm
  prefix_map = []

  for event, elem in etree.iterparse(file, events):
      if event == "start-ns":                    
        prefix_map.append(elem)
      elif event == "start":
          if root is None:
              root = elem         
  
  return etree.ElementTree(root), prefix_map

def prepare_template(prefix_map, source_file, dest_file):
  '''
  Prepares template for use with specified xml.
  Creates copy of original template, which is stored in dest_file.
  '''
  tplt = etree.parse(source_file)
  
  tproot = tplt.getroot()
  
  known_prefixes = ['dsig', 'fo', 'fm', 'xsl']
  to_add = []
  # which ns prefixes should be added to the xsl template?
  for prefix in prefix_map:
    if prefix not in known_prefixes:
      log.debug("adding prefix %s - %s" % (prefix, prefix_map[prefix]))
      known_prefixes.append(prefix)
      to_add.append(prefix)
      
  # this should be namespace for xmlns prefix in case it is needed
  for prefix in to_add:
    tproot.set("{http://www.w3.org/2000/xmlns/}"+prefix, prefix_map[prefix])
  
  tpstr = etree.tostring(tproot, pretty_print=True, 
                         xml_declaration=True, encoding="UTF-8")
  
  # find end of xml declaration
  END_DECLAR_TAG = "?>"
  end_decl = tpstr.find(END_DECLAR_TAG) + len(END_DECLAR_TAG)
  declaration = tpstr[:end_decl]
  
  #cut the  beginning of root element
  end_root = tpstr.find("<xsl:output")
  root = tpstr[end_decl:end_root]
  
  # and the rest of document
  the_rest = tpstr[end_root:]
  
  # replace ns0 tag, which is created at serialization
  root = root.replace('xmlns:ns0="http://www.w3.org/2000/xmlns/"', '')
  root = root.replace('ns0:', 'xmlns:')
  
  #write all four parts (declaration, doctype, root and rest)
  f = open(dest_file, "w")
  
  f.write(declaration)
  f.write(DOCTYPE)
  f.write(root)
  f.write(the_rest)
  
  f.flush()
  f.close()
  
  
def parse_fo(fo_file, comes_from_zip=True):
  '''
  Parses fo file - returns parsed fo and list of tuples of all found prefixes (_tuple contains prefix and namespace) 
  '''
  parsed, prefix_map = parse_xmlns(fo_file)
  # create dictionary with prefixes and their uris. Assume, that prefixes are not 
  # redefined in thedocument
  prefixes = {}
  for _tuple in prefix_map:
    if _tuple[0] == "":
      # leave out empty prefixes - they are not needed in xslt
      continue
    if not prefixes.has_key(_tuple[0]):
      prefixes[_tuple[0]] = _tuple[1]
      
  doc = zfo_doc.ZFO_document(parsed, prefixes, fo_file, comes_from_zip)
  return doc

