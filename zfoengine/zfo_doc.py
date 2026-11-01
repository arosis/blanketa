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
Module containing class that represents ZFO document with
actions that could be performed with it.
'''
import copy
import logging
import os
import re

import lxml.etree as etree

from . import ns

log = logging.getLogger("zfo.zfo_doc")

import base64

from . import evaluate as ev
from . import js602, msg_types, tools

XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"


class ZFO_document:
  
  def __init__(self, parsed_content, prefix_map, file_name, comes_from_zip):
    '''
    Creates ZFO document objects. Sets its content, root and
    prefixes map (dictionary of prefix -> uri)
    '''
    self.is_changed = False
    # zfoengine: scripts of this form, see script_interpreter()
    self.interpreter = None
    self.script_problems = set()
    self.runaway_scripts = 0
    self.script_running = False
    self.script_named_parameters = {}
    self.user_action_running = False
    self.prefix_map = prefix_map    
    self.content = parsed_content 
    self.root = parsed_content.getroot()
    # location of unpacked form.fo file - ~/.zfoeditor/forms/<hash>/form.fo
    self.storage = file_name
    # path selected by user when opening zfo form
    self.opened_from_path = None
    # flagg that says if this form was zippedd in zfo or it is only fo file
    self.comes_from_zip = comes_from_zip
    # element, which holds the data branch
    self.data_root = None
    # tree which the computation will be executed on
    self.computing_data_tree = None
    # holds original (empty) model - it is used to create new repeatable sections
    self.original_model = None
    self.readonlybinds = ""
    # cache for found absolute path of binds
    self.bind_abs_paths = {}
    # namespaces used in path
    self.path_namespaces = None
    
    # html templates for repeating sections
    self._repe_sections_templates = {}
    
    # set from id 
    self.form_id = None
    form_id = self.root.xpath("//fm:formparams/@form-id", namespaces = {"fm" : ns.FM_URI})
    if len(form_id):
      self.form_id = str(form_id[0])    
    # set form creation date
    self.form_creation_date = None
    cd = self.root.xpath("//fm:formparams/@creationdate", namespaces = {"fm" : ns.FM_URI})
    if len(cd):
      self.form_creation_date = str(cd[0])
      
    # register extended xpath functions and allow them to use our document
    from . import xpath_ext
    self._ext_xpath_evaluator = xpath_ext.ExtendedXpathEvaluator(self) 
    self._register_xpath_extensions()
    
    # get the list of all file inputs and list them
    self.file_binds = self._create_file_binds_list()
    # zfoengine: filled in by prepare_attachment_binds (document.ready);
    # initialized here so file binds do not crash before that message
    self.filenames_binds_map = {}
    self.mediatypes_bind_map = {}
    
  def _register_xpath_extensions(self):
    '''
    zfoengine: lxml's FunctionNamespace(None) is process-global. The GTK app
    had one document at a time; the engine may hold several, so api.handle()
    re-registers the extensions of the document it is about to work on.
    '''
    ext_xpath_evaluator = self._ext_xpath_evaluator
    func_ns = etree.FunctionNamespace(None)
    # get all "public" methods from xpath extensions class
    for func in dir(ext_xpath_evaluator):
      if func.startswith('_'):
        continue
      func_ns[func] = getattr(ext_xpath_evaluator, func)
    # zfoengine: forms write SendAppEvent(print()) - expr_quoting leaves the
    # lowercase "call" unquoted, so print() must evaluate to the event name
    # (SendAppEvent maps "print" to app_events.print_)
    func_ns["print"] = lambda context, *args: "print"
    
  def _create_file_binds_list(self):
    '''
    Returns list of all binds, that are bound to the input of type 'bin'
    - inputs where user selects a file. 
    '''
    result = []
    xpath_expr = '//*[@form602ct="bin"]'
    fo_elems = self.root.xpath(xpath_expr)
    for fo_elem in fo_elems:
      bind_id = fo_elem.get("form602bind")
      result.append(bind_id)
    
    log.debug("{0} binds of file type found.".format(len(result)))
    return result
  
  def set_repe_section_html(self, repe_name, html):
    '''
    Sets html template for repeating section.
    '''
    self._repe_sections_templates[repe_name] = html
  
  def get_repe_section_html(self, repe_name):
    '''
    Gets html template for repeating section.
    '''
    if (repe_name in self._repe_sections_templates):
      return self._repe_sections_templates[repe_name]
    else:
      return None
  
  def _get_abs_data_path(self, bind_elem, repe_data=None):
    '''
    Goes up through bind elements until it reaches the nodeset with absolute
    path, then concatenates all nodesets to get the path from data root to the data
    '''
    # if repetition data are passed, we must construct xpath with indexes 
    if repe_data is not None:
      e = bind_elem
      buf = "" 
       
      while not buf.startswith("/"):       
        nodeset = e.get("nodeset")
        id = e.get("Id")
        xpath_index = ""
        if (id in repe_data):
          xpath_index = "[" + str(repe_data[id] + 1)+"]"
        buf = nodeset + xpath_index + "/" + buf
        e = e.getparent()
      
      # cut potential trailing / to avoid invalid xpath expression
      if buf.endswith('/'):
        buf = buf[:len(buf)-1]
      
      return buf
    # if there are no repetition data, work as usual
    # did we find this bind's path before?
    id = bind_elem.get("Id")
    if (id in self.bind_abs_paths):
      return self.bind_abs_paths[id]
    # there is no such key in map of calculated paths
    e = bind_elem
    buf = e.get("nodeset")    
    
    while not buf.startswith("/"):
      e = e.getparent()
      buf = e.get("nodeset") + "/" + buf
    # store calculated path for future use
    self.bind_abs_paths[id] = buf
    return buf
  
  def __copy_model_data_root(self, doc_data_root):
    '''
    Creates copy of the model data tree in document    
    '''
    new_data_tree = copy.deepcopy(doc_data_root)
    return new_data_tree
  
  def _update_model_with_data(self, model, data):
    '''
    Updates model tree with data tree. Changes are made in model tree.
    '''
    def __get_ns(tag):      
      endns_index = tag.find('}')
      if endns_index == -1 :
        return ''
      return tag[:endns_index + 1]
    
    def __get_local_name(tag):
      endns_index = tag.find('}')
      if endns_index == -1 :
        return tag
      return tag[endns_index + 1:]
      
    def __cmp_tags(tag_a, tag_b):
      '''
      Compares two tags, ignores namespace
      '''      
      ln_a = __get_local_name(tag_a) 
      ln_b = __get_local_name(tag_b)
      return ln_a == ln_b
    
    # if data element does not have any namespace, get it 
    # the model namespace
    m_ns = __get_ns(model.tag)
    d_ns = __get_ns(data.tag)
    
    if d_ns == '':
      data.tag = ''.join([m_ns, data.tag])
    
    for att in data.attrib.keys():
      #if model.att does not contain attribute, add it to the model attributes      
      model.attrib[att] = data.attrib[att]
      
    model.tail = data.tail
    model.text = data.text
    
    processed_tags = []
    for child in data:
      # zfoengine: skip comments / processing instructions
      if not isinstance(child.tag, str):
        continue
      if child.tag in processed_tags:
        continue
      processed_tags.append(child.tag)
      # get count of this tag elements in the data
      data_elements = [x for x in list(data) if isinstance(x.tag, str) and __cmp_tags(x.tag, child.tag)]       
      # get count of this tag elements in the model 
      model_elements = [x for x in list(model) if isinstance(x.tag, str) and __cmp_tags(x.tag, child.tag)]
                  
      diff = len(data_elements) -  len(model_elements)
      # if there are some elements missing, crete them and then
      # fill them
      if diff != 0:
        for _ in range(diff):
          # child with no namespace gets the parent's ns
          child_ns = __get_ns(child.tag)
          child_tag = child.tag
          if child_ns == '':
            child_tag = ''.join([m_ns, child.tag])
             
          new_el = etree.Element(child_tag)
          model.append(new_el)
          model_elements.append(new_el)
      for i in range(len(data_elements)):
        # just recursively run itself to copy text and attributes        
        self._update_model_with_data(model_elements[i], data_elements[i])
    
  def _create_computing_data_tree(self, data_tree):
    '''
    Creates copy of data model tree. Nodes which shuold
    contain number and are empty are filled with 0.
    This must be done to calculate values of depending nodes.
    E.g. adding of two empty (number-) nodes should be considered
    zero, not Nan.  
    '''
    # get the copy of original data tree
    # cdt - computation data tree
    cdt = copy.deepcopy(data_tree)
    # get all inputs that have form602ct=num
    doc_root = self.content.getroot()
    num_inputs = doc_root.xpath('//*[@form602ct="num"]')
    for num_input in num_inputs:          
      bind_id = num_input.get("form602bind")
      if bind_id is None:
        log.warning("Num input has no form602bind attribute")
        continue
      bind = self._find_bind_elem(bind_id)
      # get paths to elements that are bound to number inputs
      path = self._get_abs_data_path(bind[0])      
      # if the value of the element is empty, set it to 0
      node = cdt.xpath(path, namespaces=self._get_used_ns_prefix(path))      
      # situation when there is no such node cannot happen, data tree contains whole model
      if etree.iselement(node[0]):        
        # this is an element, set its text value
        if node[0].text is None:
          node[0].text = "0"
      else:
        if node[0] == "":
          # this element is an empty attribute        
          att_name = self._path_contains_att(path)
          p = node[0].getparent()
          p.set(att_name, "0")
    
    return cdt
    
    
  def __get_model(self, obj):
    '''
    Finds model element in dsig:Object element.
    '''      
    models = obj.xpath("//fo:root/fm:formparams/fm:model/fm:instance/*[1]",
                                  namespaces = {'fo': ns.FO_URI, 'fm': ns.FM_URI})
    #return first match (there is always only one, but...) 
    model = models[0]
    return model
  
  def script_interpreter(self):
    '''The interpreter of this form's scripts, with their library.'''
    if self.interpreter is None:
      library = [element.text for element in self.root.iter("{%s}script" % ns.FM_URI) if element.text]
      self.interpreter = js602.Interpreter(library)
    return self.interpreter

  def _get_computable_data_root(self):
    '''
    Returns computation data tree. If it was not created before, is created in this function.
    If even data model tree hasn't been built yet, returns None
    '''
    if self.computing_data_tree is None:
      if self._get_data_root() is not None:
        self.computing_data_tree = self._create_computing_data_tree(self._get_data_root())
      else:
        return None
    
    return self.computing_data_tree
      
  
  def _get_data_root(self):
    '''
    Returns dsig:Object element, where data are going to be stored.
    doc_root is document root element        
    '''    
    if self.data_root is not None:      
      return self.data_root    
    # data root will be copied "out of the tree" - memory data of fo doc
    # are not changed during work, changes are made in the copy
    # find data branch, which should be copied to the end of document
    doc_root = self.content.getroot()
    # zfoengine: data saved by a filler (602 FormFiller, or this engine's caller)
    # live in a dsig:Object without Id appended to the publisher signature, which
    # can sit at any nesting depth once the filled form was signed again. Merge
    # them into the empty model, like the original does for the layouts below.
    filled = self._find_filled_data_object()
    if filled is not None:
      log.info("Doc contains filled data in a dsig:Object without Id - merge them into the data model")
      model = self.__copy_model_data_root(self.__get_model(doc_root))
      self.original_model = self.__copy_model_data_root(self.__get_model(doc_root))
      self._update_model_with_data(model, self._first_child_element(filled))
      self.data_root = etree.ElementTree(model)
      self.computing_data_tree = self._create_computing_data_tree(self.data_root)
      return self.data_root
    # look for Object elements under the document root
    objects = doc_root.xpath("dsig:Object", namespaces={'dsig' : ns.DSIG_URI})    
    if len(objects) == 2:
      # check if there is not signed object, if no, merge
      # both objects into one, otherwise use directly the second 
      # Object - it must have been created by merging data model and data
      # from the signed data inside inner Signature
      inner_sigs = objects[0].xpath("dsig:Signature", namespaces={'dsig' : ns.DSIG_URI})
      if len(inner_sigs) == 0:  
        # we have two objects and no inner signature
        # this means - we have a signed form and some data at the end of
        # document, which are not signed - we do not know who the
        # data come from - we have to merge the model(from first Object) and 
        # data from second Object
        
        # create copy of model  
        log.info("Doc contains two Object elements under root Signature element \
\nand no inner Signatures - update data model from the first Object with \
\nthe data from the second Object")      
        model = self.__copy_model_data_root(self.__get_model(objects[0]))
        # create one copy as a template for repeatable sections
        self.original_model = self.__copy_model_data_root(self.__get_model(objects[0]))
        #log.debug("'Clean' model - without data: %s" % etree.tostring(model, pretty_print=True))
        # fill the copy of model with data branch
        self._update_model_with_data(model, objects[1][0])        
        #log.debug("Model filled with prepared data : %s" % etree.tostring(model, pretty_print=True))
        self.data_root = etree.ElementTree(model)      
        #return self.data_root
      else:
        # if we have a situation with two objects on highest level
        # and an inner signature, this form was already processed
        # so the second top-level Object is already prepared data root 
        # (return first child object of Object element)
        log.info("Doc contain two Object elements under root Signature element\
\nand inner signature - merge models from second and first object and use it as data root")                
        # take the empty model from the first object
        model = self.__copy_model_data_root(self.__get_model(objects[0]))
        # merge data into the model
        self._update_model_with_data(model, objects[1][0])
        
        self.data_root = etree.ElementTree(model)   
        # create copy for repeating sections
        self.original_model = self.__copy_model_data_root(model)
        #return self.data_root
        
    elif len(objects) == 1:
      # find data to copy
      tmp = doc_root.xpath("dsig:Object/dsig:Signature", namespaces = {'dsig' : ns.DSIG_URI})
      
      if len(tmp) == 0:
        # copy the model from first object
        #model = objects[0].xpath("fo:root/fm:formparams/fm:model/fm:instance/*[1]",
        #                          namespaces = {'fo': ns.FO_URI, 'fm': ns.FM_URI})[0]
        log.debug("Doc contains only one Object element under root. This Object\
\ndoes not contain inner Signature - copy the data model from this Object and\
\nuse it as the data root.")
        model = self.__copy_model_data_root(self.__get_model(objects[0]))
        # create one copy as a template for repeatable sections
        self.original_model = self.__copy_model_data_root(self.__get_model(objects[0]))
        
        self.data_root = etree.ElementTree(model)      
        #return self.data_root
      elif len(tmp) == 1:
        # find Objects hung under the inner Signature
        o = tmp[0].xpath("dsig:Object", namespaces = {'dsig': ns.DSIG_URI})        
        # if there are two, copy the contents of the second - it is filled with required data
        if len(o) == 2:
          # we have inner signature with two elements
          # we have to copy the model from first object,
          # and fill it with data found in second object, 
          # all this goes to the end of document
          # get the first child of Object element
          #origin = o[1].xpath("*[1]")[0]
          log.info("Doc contains only one Object under root and this Object\
\ncontains inner Signature. Inner signature contains two Object elements:\
\nfirst is data model, second is pre-filled data (also signed). Merge these two data\
\nbranches together and use it as data root.")
          model = self.__copy_model_data_root(self.__get_model(o[0]))
          # create one copy as a template for repeatable sections
          self.original_model = self.__copy_model_data_root(self.__get_model(o[0]))
          data = o[1][0] # first child of Object element 
          self._update_model_with_data(model, data)     
          self.data_root = etree.ElementTree(model)      
          #return self.data_root
        elif len(o) == 1:
          #origin = o[0].xpath("fo:root/fm:formparams/fm:model/fm:instance/*[1]",
          #                    namespaces = {'fo': ns.FO_URI, 'fm': ns.FM_URI})[0]
          log.info("Doc contains only one Object under root and this Object\
\ncontains inner Signature. Inner signature contains one Object element with data model.\
\nCopy it and use it as data root.")
          model = self.__copy_model_data_root(self.__get_model(o[0]))
          # create one copy as a template for repeatable sections
          self.original_model = self.__copy_model_data_root(self.__get_model(o[0]))
          
          self.data_root = etree.ElementTree(model)      
          #return self.data_root
        else:
          raise Exception("Got 0 or more than 2 dsig:Objects -> this is probably not 602.cz ZFO")
     
      else:
        raise Exception("Too many Object elements under Object/Signature element")
    else:
      raise Exception("Got 0 or more than top level 2 dsig:Objects -> this is probably not 602.cz ZFO")
    
    # now create the data tree for computations. That tree has set 0 to the number nodes
    cdt = self._create_computing_data_tree(self.data_root)
    self.computing_data_tree = cdt
    return self.data_root
    
   
  @staticmethod
  def _first_child_element(elem):
    '''
    zfoengine: first element child (skips comments/PIs that elem[0] would return).
    '''
    for child in elem:
      if isinstance(child.tag, str):
        return child
    return None
  
  @staticmethod
  def _is_publisher_signature(signature):
    '''
    zfoengine: True if the signing certificate's subject contains "602XML"
    (the form publisher's signature, to which fillers append the data Object).
    '''
    certs = signature.xpath("dsig:KeyInfo//dsig:X509Certificate/text()",
                            namespaces={'dsig': ns.DSIG_URI})
    subjects = signature.xpath("dsig:KeyInfo//dsig:X509SubjectName/text()",
                               namespaces={'dsig': ns.DSIG_URI})
    for subject in subjects:
      if "602XML" in subject:
        return True
    for cert_b64 in certs:
      try:
        from cryptography import x509
        cert = x509.load_der_x509_certificate(base64.b64decode("".join(cert_b64.split())))
        if "602XML" in cert.subject.rfc4514_string():
          return True
      except Exception as ex:
        log.debug("Cannot read signing certificate: %s" % ex)
    return False
  
  def _find_filled_data_object(self):
    '''
    zfoengine: returns the dsig:Object without Id holding filled form data
    (its first element has the same name as the model instance root), preferring
    the one appended to the publisher signature. None if the form is empty.
    '''
    doc_root = self.content.getroot()
    try:
      model_tag = self.__get_model(doc_root).tag
    except IndexError:
      return None
    local_name = model_tag[model_tag.find('}')+1:]
    candidates = []
    for obj in doc_root.xpath("//dsig:Signature/dsig:Object[not(@Id)]",
                              namespaces={'dsig': ns.DSIG_URI}):
      first = self._first_child_element(obj)
      if first is None:
        continue
      if first.tag[first.tag.find('}')+1:] == local_name:
        candidates.append(obj)
    if not len(candidates):
      return None
    for obj in candidates:
      if self._is_publisher_signature(obj.getparent()):
        return obj
    return candidates[0]
  
  def _get_used_ns_prefix(self, path):
    '''
    Returns a dictionary containing 
    '''
    result = {}            
    prefixes = re.findall(r"[a-zA-Z\-_0-9]*:", path)
    for prefix in prefixes:
      prefix = prefix.rstrip(':')
      if (prefix not in result):
        if (prefix in self.prefix_map):          
          result[prefix] = self.prefix_map[prefix]
    # if path contains no prefixes, add dumy prefix - 
    # - lxml does not support empty namespaces
    if len(result) == 0:
      result["dummy"] = "http://dum.my"
    return result
     
      
  def _find_bind_elem(self, bind_id):
    '''
    Returns list of matching bind elements (should be only 1)
    '''   
    bind_path = "//fm:bind[@Id='"+ bind_id + "']"
    binds = self.root.xpath(bind_path, namespaces={"fm" : ns.FM_URI})
    if not len(binds):
      log.warning("No binding attribute found with id {0}".format(bind_id))
    return binds
    
  def _path_contains_att(self, path):
    '''
    Returns attribute name, if the path is referencing some attribute.
    Else returns None
    '''
    if path.find('@') > -1:          
      # get the attribute name
      att_index = path.rfind('@')
      att_name = path[att_index+1:]
      return att_name
    
    return None 

  def _get_checkbox_value_generic(self, name, type):
    '''
    Generic method for getting checkbox on/off value from form.
    '''
    path = "//fo:character[@form602bind='"+ name +"' and @form602ct='chb']"        
    res = self.root.xpath(path, namespaces={'fo': ns.FO_URI})
    if len(res) == 0:
      return None
    # if there are more characters, they all must have form602on/off attribute the same (they are referencing the same bind)
    if type == "on":
      on_value = res[0].get("form602on")
      return on_value
    if type == "off":
      off_value = res[0].get("form602off")
      return off_value

  def _get_checkbox_on_value(self, name):
    '''
    Returns value of attribute form602on, which indicates value which
    is written to the model if the checkbox is on
    '''        
    return self._get_checkbox_value_generic(name, "on")

  def _get_checkbox_off_value(self, name):
    '''
    Returns value of attribute form602off, which indicates value which
    is written to the model if the checkbox is off
    '''        
    return self._get_checkbox_value_generic(name, "off")
  
  def _store_one_value_to_node(self, bind_id, data_node, value, att_name=None):
    '''
    Stores provided value to the data_node.
    '''
    if value == 'on':
      on_value = self._get_checkbox_on_value(bind_id)
      if on_value is not None:                
        value = on_value        
    if value == 'off':
      off_value = self._get_checkbox_off_value(bind_id)
      if off_value is not None:
        value = off_value
     
    # utf-8 decode (zfoengine: Py3 strings are text already, only bytes need decoding)
    if isinstance(value, (bytes, bytearray)):
      value = value.decode("utf-8")
    elif (value is not None) and (not isinstance(value, str)):
      value = str(value)
    # this is not a checkbox, handle it as an regular value 
    if att_name is not None:
      # zfoengine: lxml takes a namespace only in Clark notation, a form may bind xml:lang
      data_node.set(att_name.replace("xml:", "{%s}" % XML_NAMESPACE, 1) if att_name.startswith("xml:") else att_name, value)
    else:
      data_node.text = value
  
  def _store_one_value_to_tree(self, tree, name, path, value):
    '''
    Stores value to the specified path in the specified tree 
    (data tree or calculate tree).
    '''                          
    # get the element on the 'nodeset' xpath
    el = tree.xpath(path, 
                         namespaces = self._get_used_ns_prefix(path) 
                         )
    result = el[0]
    att_name = self._path_contains_att(path)
    if att_name is not None:
      # we were looking for an attribute - element is its parent
      result = result.getparent()      
    # maybe checkbox value. Could possibly be text field with 'on'/'off' value - 
    # - this will be determined when getting the on value  
    self._store_one_value_to_node(name, result, value, att_name=att_name)
    
    if (value is not None) and (len(value) > 50):
      log.debug("Wrote %s to %s" % (value[:50]+'...', path))
    else:
      log.debug("Wrote %s to %s" % (value, path))
        
  def _store_one_value(self, name, path, value):
    '''
    Stores value to the specified path in both trees - "calculable" and original.
    '''
    self._store_one_value_to_tree(self._get_data_root(), name, path, value)
    self._store_one_value_to_tree(self._get_computable_data_root(), name, path, value)
  
  def _get_ns_prefix(self, ns_uri):
    '''
    Returns prefix used in document for specified namespace
    '''
    pm = self.prefix_map
    for prefix in pm.keys():
      if pm[prefix] == ns_uri:
        return prefix
    return None
  
  def _update_rep_sections_in_cdt(self, path_of_rep_elem, action, template=None):
    '''
    Clones or deletes repeating element on specified path.
    Action could be 'clone' or 'delete'
    template is the element from original model, which is in case 'clone' copied.
    If template is not specified, last added repetition is cloned.
    '''
    rep_elem = self.computing_data_tree.xpath(path_of_rep_elem, 
                                     namespaces = self._get_used_ns_prefix(path_of_rep_elem))[0]
    if action == 'clone':
      if template is not None:
        new_elem = copy.deepcopy(template)
      else:
        new_elem = copy.deepcopy(rep_elem)    
      # hang the new element under the parent of found element (make it a sibling)   
      rep_elem.getparent().append(new_elem)
    elif action == 'delete':
      parent = rep_elem.getparent()
      parent.remove(rep_elem)    
    else:
      raise Exception("Action not specified. Expected 'clone' or 'delete'")
  
  def _get_filename_of_bind(self, bind, path):
    '''
    Returns filename of bind with specified path in data model.
    '''
    data_node = self._get_data_root().xpath(path, namespaces=self._get_used_ns_prefix(path))
    data_node = data_node[0]
    return self._get_filename_of_data_node(bind, data_node)
    
  def _get_filename_of_data_node(self, bind, data_node):
    '''
    Given a node of data model, returns filename of the content it holds    
    '''
    content_path = self._get_abs_data_path(bind)    
    fnamebind_id = self.get_binds_filename_bind(bind.get("Id"))
    # if the filename bind does not exist, return empty string
    # future: this could be an image, its content should be returned in html-like form
    if fnamebind_id is None:
      return ""
    fnamebind = self._find_bind_elem(fnamebind_id)[0]      
    fname_path = self._get_abs_data_path(fnamebind)
    common_path, from_a_to_b = tools.create_relative_path(content_path, fname_path)
    
    # if data node is attribute, there is not available to call xpath expression
    # use old trick with temporal child element
    if not etree.iselement(data_node):
      att_owner = data_node.getparent()
      tmp = tools.create_temp_elem(att_owner, str(data_node))
      real_fname_node = tmp.xpath(from_a_to_b, namespaces = self._get_used_ns_prefix(from_a_to_b))
      tools.delete_temp_elem(att_owner, tmp)
    else:
      real_fname_node = data_node.xpath(from_a_to_b, namespaces = self._get_used_ns_prefix(from_a_to_b))
    
    real_fname_node = real_fname_node[0]
    if self._path_contains_att(fname_path):
      return str(real_fname_node)
    else:
      return real_fname_node.text
  
  def _remove_filename_of_data_node(self, bind, data_node):
    '''
    Given a node of data model, returns filename of the content it holds    
    '''
    content_path = self._get_abs_data_path(bind)    
    fnamebind_id = self.get_binds_filename_bind(bind.get("Id"))
    fnamebind = self._find_bind_elem(fnamebind_id)[0]      
    fname_path = self._get_abs_data_path(fnamebind)
    common_path, from_a_to_b = tools.create_relative_path(content_path, fname_path)
    
    # if data node is attribute, there is not available to call xpath expression
    # use old trick with temporal child element
    if not etree.iselement(data_node):
      att_owner = data_node.getparent()
      tmp = tools.create_temp_elem(att_owner, str(data_node))
      real_fname_node = tmp.xpath(from_a_to_b, namespaces = self._get_used_ns_prefix(from_a_to_b))
      tools.delete_temp_elem(att_owner, tmp)
    else:
      real_fname_node = data_node.xpath(from_a_to_b, namespaces = self._get_used_ns_prefix(from_a_to_b))
    
    real_fname_node = real_fname_node[0]
    att_name = self._path_contains_att(fname_path) 
    if att_name is not None:
      real_fname_node.getparent().set(att_name,"")            
    else:
      real_fname_node.text = ""
      
      
  def _count_values_on_xpath(self, xpath_relative_to_data_root):
    '''
    Returns number of values stores in data branch on specified xpath
    '''
    data_root = self._get_data_root()
    res = data_root.xpath(xpath_relative_to_data_root, namespaces=self._get_used_ns_prefix(xpath_relative_to_data_root)) 
    return len(res)
    
  def _get_dependent_binds(self, bind_id):
    '''
    get the binds, that depend on this bind
    '''
    binds = self.root.xpath("//fm:bind", namespaces={"fm": ns.FM_URI})
    dependent_binds = []
    for bind in binds:
      deps = bind.get("depends")
      if (deps is None) or (deps == ""):
        continue      
      if bind_id in deps.split("|"):
        dependent_binds.append(bind)
    return dependent_binds
  
  def _get_binds_with_no_dependents(self):
    '''
    get the binds that do not depend on any other bind (they do not have 'depends' attribute)
    '''
    no_depend = self.root.xpath("//fm:bind[not(@depends)]", namespaces={"fm": ns.FM_URI})
    empty_depend = self.root.xpath('//fm:bind[@depends=""]', namespaces={"fm": ns.FM_URI})  
    no_depend.extend(empty_depend)
    return no_depend
  
  def _get_input_direct_children(self, rep_block, rep_block_xpath=None, 
                                repe_ancestors=None):
    '''    
    Returns list of direct input children - not inputs in child repeating sections
    WORKS WITH PRESENTATION PART
    '''
    results = {}    
    curr_level = repe_ancestors + 1
    # get all relevant descendants of the repetition block
    children = rep_block.xpath("descendant::*[@form602bind]")
    # remove children, that are under other repeating block or
    # that are repetition blocks
    # store children repetition blocks aside to special list
    direct = []
    repe_children = []
    for child in children:
      # if child is a repeating block, add it to the mentioned list      
      if child.get("form602repe") is not None:
        repe_children.append(child)
        continue
              
      # how many repeating ancestors (self included) does children have?
      # if there are more than current level, it is nested repeating section 
      # or input inside it - just skip it, we have the repetition bloxk stored aside
      # zfoengine: count distinct repeating sections, not elements - 602 splits
      # one repeating section into several fo elements with the same
      # form602repe (CUZK opak1 = 6 tables), the original then skipped all inputs
      child_anc_len = len(set(child.xpath("ancestor-or-self::*/@form602repe")))
      if child_anc_len > curr_level:
        continue
      # ok, now we have regular child, which represents some input box where a 
      # value should be inserted
      direct.append(child)
      # this always MUST be only 1 element -> binds' ids are unique
      bs = self._find_bind_elem(child.get("form602bind"))
      if not len(bs):        
        continue
      bind = bs[0]
      # !!! construct path correctly - append the nodeset path to given
      # path. This is important, because of nested repeating sections
      tmp_abs_path = self._get_abs_data_path(bind)
      # chop the temporary path
      # this is the path to the bind without repeating section info
      chunks = tmp_abs_path.split('/')
      # chop the rep_block_xpath - xpath with RS info about repeating block
      chunks2 = rep_block_xpath.split('/')
      # merge the chunks - take the beginning from the path
      # with RS info and the rest od path naviigates
      # only inside the RS 
      merged_chunks = chunks2 + chunks[len(chunks2):]
      path = "/".join(merged_chunks)
             
      # also value is only one - we are in the case of simple not-repeating children
      val = self._get_data_root().xpath(path, namespaces=self._get_used_ns_prefix(path))
      # zfoengine: an input bound to a bind outside this repeating section
      # (form authoring error, e.g. CUZK sekce2c inputs bound to sekce2b binds)
      # has no node under the section - skip it instead of IndexError
      if not len(val):
        log.warning("Input bound to %s has no data under %s, skipping" % (bind.get("Id"), rep_block_xpath))
        continue
      # store value to result - distinguish between attribute and element
      if self.is_bind_file(bind.get("Id")):
        #results[bind.get("Id")] = self._get_filename_of_bind(path)
        results[bind.get("Id")] = self._get_filename_of_data_node(bind, val[0])
      else:
        if self._path_contains_att(path) is None:
          t = val[0].text       
          results[bind.get("Id")] = "" if t is None else t 
        else:
          results[bind.get("Id")] = val[0]
      #results.append({"key": bind.get("Id"), "value": val[0]})
    # now go through repeating children
    for rep_child in repe_children:
      id = rep_child.get("form602bind")
      bs = self._find_bind_elem(id)
      if not len(bs):
        continue
      rbind = bs[0]      
      rpath = rep_block_xpath + "/" + rbind.get("nodeset")
      # how many times does this repetition child repeat?
      num_values = self._count_values_on_xpath(rpath)
      #data = {"key": id, "value": []}
      results[id] = []
      #results.append(data)
      # in each repetition call recursively processing function
      for i in range(num_values):
        # store this to the result, identify the repetition number
        #results[str(id) + "-rep-" + str(i)] = \
        results[id].append(self._get_input_direct_children(
                                       rep_child, 
                                       rpath+"["+ str(i+1)+"]", 
                                       repe_ancestors=repe_ancestors+1)
                            ) 
    log.debug("Path: %s; Bind id : %s; all: %d; Direct-children: %d; Repe-children: %d" % (
                                                                          rep_block_xpath,
                                                                          rep_block.get("form602bind"), 
                                                                          len(children), 
                                                                          len(direct), 
                                                                          len(repe_children))
    )    
    return results

    
  def is_required(self, bind_id):
    '''
    Finds bind element, data path and evaluates if the bind value is required
    '''
    binds = self._find_bind_elem(bind_id)
    path = self._get_abs_data_path(binds[0])
    result = ev.eval_required(self, binds[0], path)
    return result
  
  def clone_section(self, section_name, rep_ancestors = None):
    '''
    Clones specified section in data tree
    '''
    # find the bind and absolute path to its nodeset
    binds = self._find_bind_elem(section_name)
    
    rep_data = tools.extract_repetition_parents_info(rep_ancestors)
    
    path = self._get_abs_data_path(binds[0], rep_data)
    # create copy of the element on the path    
    path_in_original_model = self._get_abs_data_path(binds[0], None)
    template = self.original_model.xpath(path_in_original_model, namespaces = self._get_used_ns_prefix(path))[0]         
    orig_elem = self._get_data_root().xpath(path, namespaces = self._get_used_ns_prefix(path))[0]    
    
    new_elem = copy.deepcopy(template)    
    # hang the new element under the parent of found element (make it a sibling)   
    orig_elem.getparent().append(new_elem)
    
    self._update_rep_sections_in_cdt(path, 'clone', template)

  def delete_section(self, section_name, rep_ancestors = None):
    '''
    Clones specified section in data tree
    ''' 
    # parse the bind name form section_name -> cut the trailing "-rep-n"
    end_of_name = section_name.rfind("-rep-")
    bind_id = section_name[:end_of_name]
    # find the bind and absolute path to its nodeset
    binds = self._find_bind_elem(bind_id)
    
    rep_data = tools.extract_repetition_parents_info(rep_ancestors)
    
    path = self._get_abs_data_path(binds[0], rep_data)
         
    to_delete = self._get_data_root().xpath(path, namespaces = self._get_used_ns_prefix(path))[0]
        
    parent = to_delete.getparent()
    parent.remove(to_delete)    
    
    self._update_rep_sections_in_cdt(path, 'delete')
  
  def get_initial_values(self, input_dic):
    '''
    Returns dictionary with default values of the form values - values from the data root subtree
    '''
    # zfoengine: a control with no name at all has an empty id, and the page's selector "#" would stop it loading the rest
    input_dic.pop("", None)
    for input_id in input_dic:
      type = input_dic[input_id]["type"]
      # zfoengine: JSON.stringify drops an undefined "bind" (input without name)
      bind_id = input_dic[input_id].get("bind") or ""
      if bind_id == "":
        continue
      val = ""
      # repe data not necessary, will be corrected in get repeatable
      if self.is_bind_file(bind_id):
        val = self.get_value_file(bind_id)
      else:        
        val = self.get_value(bind_id)
      if type == 'check':
        #off_val = self._get_checkbox_off_value(bind)
        on_val = self._get_checkbox_on_value(bind_id)
        if val == on_val:
          input_dic[input_id]["value"] = "on"
        else:
          input_dic[input_id]["value"] = "off"      
      else:
        input_dic[input_id]["value"] = val
    return input_dic
  
  def get_readonly_binds(self):
    '''
    Returns string with ids of all binds, which evaluates as readonly      
    '''
    buffer = []
    binds = self.root.xpath("//fm:bind[@readonly] | //fm:bind[@calculate]", namespaces={'fm': ns.FM_URI})
    
    for bind in binds:
      path = self._get_abs_data_path(bind)        
      is_ro = ev.eval_readonly(self, bind, path)
      if is_ro:
        buffer.append(bind.get("Id"))
        
    logging.info("%d binds found, %d are readonly" % (len(binds), len(buffer)))
    result = {}
    result["readonly_names"] = buffer
    
    return result
   
  
  def find_form_problems(self):
    # zfoengine: required fields and constraints, one problem per data node; index is the node's position among the bind's repetitions
    problems = []
    for bind in self.root.xpath("//fm:bind[@required or @constraint]", namespaces={"fm": ns.FM_URI}):
      bind_id = bind.get("Id")
      path = self._get_abs_data_path(bind)
      nsmap = self._get_used_ns_prefix(path)
      for index, node in enumerate(self._get_data_root().xpath(path, namespaces=nsmap)):
        value = node if isinstance(node, str) else (node.text or "")
        required = bind.get("required")
        if required is not None and not str(value).strip():
          is_required = True if required == "" else \
            ev.eval_expression_node(self, required, node, self._get_used_ns_prefix(required))
          if is_required:
            problems.append({"field": bind_id, "index": index, "kind": "required", "message": ""})
            continue
        if bind.get("constraint"):
          ok, comment = ev.eval_constraint(self, bind, None, node=node)
          if not ok:
            problems.append({"field": bind_id, "index": index, "kind": "constraint", "message": comment})
    return problems

  def get_multiswitch_values(self):
    '''
    Finds all multiswitches, looks for their value in the model.
    Returns dictionary which is sent to client as JSON   
    '''
    switches = self.root.xpath("//fm:data-switch[@form602ct='dsw']",
                              namespaces= {'fm': ns.FM_URI})
    r = {}
    res = []
    
    for switch in switches:
      bindref = switch.get("form602bindref")
      sw = {}
      sw["bindref"] = bindref      
      binds = self._find_bind_elem(bindref)
      if not len(binds):
        log.warning("Dataswitch references unknown bind, skipping")
        continue      
      path = self._get_abs_data_path(binds[0])
      
      data_root = self._get_data_root()               
      # get the element on the 'nodeset' xpath
      el = data_root.xpath(path, 
                           namespaces=self._get_used_ns_prefix(path)
                           )
      
      el = el[0] if len(el) else None
      value = None
      if el is not None:
        # are we looking for an attribute value?
        if path.rfind('@') >= 0:
          value = str(el)
        else:
          value = el.text
      
      if value is None:
        value = ''
            
      sw["active_value"] = value
      # now get all multicase siblings
      siblings = switch.xpath("../fo:multi-case", namespaces = {'fo' : ns.FO_URI})
      
      sw["cases"] = []
      for sibling in siblings:
        case_name = sibling.get("case-name")        
        sw["cases"].append(case_name)

      # zfoengine: an empty value shows the first case, as Form Filler does
      # (e.g. an untouched checkbox switch shows its "off" case)
      if value == '' and sw["cases"]:
        sw["active_value"] = sw["cases"][0]
      res.append(sw)
          
    r["data"] = res
    return r
  
  
  def get_repeatable_sections(self):
    '''
    Get all repeating elements, find their binds, 
    count instances of their nodesets, get their data and all that pack into
    one dictionary. Every repeating section could be 
    inside another repeating section
    '''    
    # get all repeating fo:elements
    repes = [x.getparent() for x in self.root.xpath("//@form602repe")]
    d = {}
    # divide elements into lists by number of repeating parents
    for repe in repes:
      pars = [y.getparent() for y in repe.xpath("ancestor::*[@form602repe]")]
      ancestor_count = len(pars)
      if (ancestor_count not in d):
        d[ancestor_count] = []    
      d[ancestor_count].append(repe)
    # take the repeating elements closest to the root and process them and their children
    res = {}
    if len(d):
      first_lev = d[0]
      for r in first_lev:
        bind_att = r.get("form602bind")
        # could happen
        if bind_att is None:
          continue
        bind = self._find_bind_elem(bind_att)[0]
        path = self._get_abs_data_path(bind)    
        num_vals = self._count_values_on_xpath(path)
        id = bind.get("Id")
        
        res[id] = []
        for i in range(num_vals):
          # call the recursive function for each top-level repaeating element          
          res[id].append(self._get_input_direct_children(r, 
                                 rep_block_xpath = path +"["+ str(i+1)+"]",
                                 repe_ancestors = 0)
                               )
    return res
  
  def recalc_form(self):
    '''
    Go through binds, for each evaluate calculate attribute.
    Starts with binds that do not depend on any other bind
    and go through all depending binds. No circle detection.   
    '''  
    results = {}
        
    def calculate_one_bind(bind):
      res = {}
      path = self._get_abs_data_path(bind)
      nsmap = self._get_used_ns_prefix(path)
      # how many data instances of this bind are there in the data model?
      # (repeating sections)        
      nss = self._get_used_ns_prefix(path)
      calculable_copies = self._get_computable_data_root().xpath(path, namespaces=nss)
      copies = self._get_data_root().xpath(path, namespaces=nss)
      # both must have same length, they are updated at once 
      # update each instance in data model        
      for i in range(len(copies)):
        calc_copy = calculable_copies[i]
        copy = copies[i]        
        # calculate over the model that goes to the original file        
        calculated = ev.eval_calculate(self, bind, node=copy, nsmap=nsmap)
        # evaluate over the model that has empty numbers set to zero
        cal_calculated = ev.eval_calculate(self, bind, node=calc_copy, nsmap=nsmap)
        
        if (calculated is None) or (cal_calculated is None):
          continue 
        # store value to both copies - one in the original tree, one into
        # data tree changed at init and filled with zeros
        att = self._path_contains_att(path)
        if att is None:            
          self.store_value_node(bind.get("Id"), copy, calculated[2])
          self.store_value_node(bind.get("Id"), calc_copy, cal_calculated[2])
        else:
          att_owner = copy.getparent()
          self.store_value_node(bind.get("Id"), att_owner, calculated[2], att_name=att)
          calc_att_owner = calc_copy.getparent()
          self.store_value_node(bind.get("Id"), calc_att_owner, cal_calculated[2], att_name=att)
        # to the results put the returned value from evaluation
        if self.is_bind_file(bind.get("Id")):
          #res[calculated[0]] = self._get_filename_of_bind(path)
          res[calculated[0]] = self._get_filename_of_data_node(bind, copy)
        else:
          res[calculated[0]] = calculated[1]
      return res
         
    def recalculate(binds):      
      for bind in binds:   
        log.debug("Recalculating {0}".format(bind.get("Id")))     
        calculated = calculate_one_bind(bind)       
        results.update(calculated)        
        deps = self._get_dependent_binds(bind.get("Id"))
        recalculate(deps)
        log.debug("{0} recalculated".format(bind.get("Id")))
    
    roots = self._get_binds_with_no_dependents()
    # maybe keep track of calculated binds? Now, there is each bind
    # as many times how many dependents it has.
    recalculate(roots)
    return results
    
  
  def calculate_values(self, bind_id, repe_data):
    '''
    Goes through binds that depend on
    specified bind and recalculate their values.
    Calculated values are returned in dictionary where
    keys are bind names.
    '''    
    #changed_bind = self._find_bind_elem(bind_id)[0]
    dependent_binds = self._get_dependent_binds(bind_id)
    
    rep = tools.extract_repetition_parents_info(repe_data)
    
    working_queue = []
    for db in dependent_binds:
      working_queue.append(db)    
    results = {}
    # keep track of changed values
    change_log = []
    
    for dependent_bind in working_queue:            
      dependants = self._get_dependent_binds(dependent_bind.get("Id"))
      log.debug("Adding %d new binds into working queue" % len(dependants))
      for dep in dependants:
        working_queue.append(dep)
      # 'rep' is ok, if the rep data is bound to another bind, it is ignored
      path = self._get_abs_data_path(dependent_bind, rep) 
      
      calculated = ev.eval_calculate(self, dependent_bind, path=path)
      if calculated is None:
        continue
      # store calculated value - third elemnt in tuple
      value, bind_id,\
      constr_res, constr_comment,\
      original_value = self.store_value_path(dependent_bind, calculated[2], path)      
      # mark the changes in changelog if the constraint fails
      change_log_entry = (bind_id, path, original_value, calculated[2])
      change_log.append(change_log_entry)
      
      # to the results put the returned value from evaluation
      # if the path is in the list of files, put the filename instead
      if self.is_bind_file(dependent_bind.get("Id")):
        results[calculated[0]] = self._get_filename_of_bind(dependent_bind, path)
      else:
        results[calculated[0]] = calculated[1]
    
      
    return results, change_log
  
  def get_selected_listbox_values(self):
    '''
    1, get all fo that are form602ct=ilb    
    2. get their binds
    3. construct absolute data path in the data model    
    4. get the value from model
    5. put value to the dictionary    
    '''
    result = {}
    lbs = self.root.xpath("//*[@form602ct='ilb']")
    for lb in lbs:
      bind_id = lb.get("form602bind") 
      
      bind = self._find_bind_elem(bind_id)[0]
      path = self._get_abs_data_path(bind)
      
      values = self._get_data_root().xpath(path,
                                          namespaces = self._get_used_ns_prefix(path))
      value = None
      
      if len(values):
        value = values[0]
     
      # path does not contain attribute - we have to extract text
      if self._path_contains_att(path) is None:
        value = value.text
      
      if value is None:
        value = ''
        
      # is the value a value attribute or itemttext attribute?
      listname = lb.get("form602listname")
      data = {}
      if listname is None:        
        # get the form602reference xpath
        refpath = lb.get("form602reference")
        # concatenate it with @valuexp
        valuexp = lb.get("valuexp")
        selected_data_path = refpath + "/" + valuexp 
        # get the values from that path - this means getting the list items 
        option_values = self._get_data_root().xpath(selected_data_path,
                                                   namespaces = self._get_used_ns_prefix(selected_data_path))
        # if the value matches one of the list, return it, else return empty str
        if value in option_values:
          data['value'] = value
        else:
          data['value'] = ''
        # return it as type 'value'        
        data['type'] = 'value'
      else:
        lists = self.root.xpath("//fm:enum[@name='"+listname+"']", 
                               namespaces = {"fm": ns.FM_URI})
        # if list with this name is not found, continue (this could happen if form creator makes mistake)
        if not len(lists):
          log.warning("No list found for name %s" % listname)
          continue
        
        list = lists[0]
        option_value = list.xpath("fm:list/fm:listitem[@value='"+value+"']", 
                                  namespaces = {"fm": ns.FM_URI})
        # if we were looking for option value or the item text      
        type = "value" if len(option_value)>0 else "itemtext"
        
        data['type'] = type
        data['value'] = value
      
      result[bind_id] = data
        
    return result
  
  def get_value(self, bind_id, repe_data=None):
    '''
    Returns data connected with specified bind id
    '''
    binds = self._find_bind_elem(bind_id)
    if not len(binds):
      # this should never happen in correct form
      # zfoengine: return empty value instead of IndexError below
      return ""
    repe = tools.extract_repetition_parents_info(repe_data) 
    # extract function returns None if gets None
    path = self._get_abs_data_path(binds[0], repe)
    
    return self.get_value_path(path)
  
  def get_value_file(self, bind_id, repe_data=None):
    '''
    Returns filename connected with bind_id. Use only on binds
    that are in zfo_doc.file_binds
    '''
    binds = self._find_bind_elem(bind_id)
    if not len(binds):
      return "" # this should never happen in correct form
    repe = tools.extract_repetition_parents_info(repe_data) 
    # extract function returns None if gets None
    path = self._get_abs_data_path(binds[0], repe)
    
    return self._get_filename_of_bind(binds[0], path)
  
  def get_value_path(self, abs_path):
    '''
    Returns value stored at absolute path. Use
    this method if the path is pre-calculated from repe-data
    '''
    dr = self._get_data_root()
    values = dr.xpath(abs_path, namespaces = self._get_used_ns_prefix(abs_path))
    
    if isinstance(values, str):
      return str(values)
     
    if len(values) == 0:
      return ""
    if self._path_contains_att(abs_path):
      return values[0]
    else:
      return values[0].text
  
  def perform_click_action(self, expression):
    '''
    Calls xpath function specified in html button onclick event
    - handles button click in zfo forms 
    '''
    if not expression.strip().startswith('"'):
      expression = '"' + expression + '"'
    self.user_action_running = True
    try:
      r = ev.eval_action_command(self, expression)
    finally:
      self.user_action_running = False
        
    return r
  
  def store_value_node(self, bind_id, node, value, att_name=None):
    '''
    Stores value to the node text, if att name is specified,
    stores it as an attribute of the node.
    Node is always an element, never an attribute.
    bind_id is used to check if we are not storing checkbox value
    node is element in the data model
    value is value to be stored
    att_name contains name of attribute where to store value (otherwise stored as text)
    '''
    self._store_one_value_to_node(bind_id, node, value, att_name)
  
  def store_value_path(self, bind, value, abs_path, suppress_constraints=False):
    '''
    Stores value on absolute path. Use this method if you 
    have pre-computed repetition data and absolute path.
    '''
    bind_id = bind.get("Id")
    log.debug("Temporally setting user value")
    # store original value    
    original_value = self.get_value_path(abs_path)
    # store temporal value
    self._store_one_value(bind_id, abs_path, value)
    # if constraint is not checked, this has to be set
    constr_res = True
    constr_comment = ""
    if not suppress_constraints:
      # evaluate constraint    
      constr_res, constr_comment = ev.eval_constraint(self, bind, abs_path)
    
    # if the constraint is fulfilled, keep the stored data
    if constr_res:
      if suppress_constraints:
        log.debug("Constraint suppressed, storing user data to the model")
      else:
        log.debug("Constraint ok, storing user data to the model")
      log.debug("Storing value %s to bind %s" % (value[:80], bind_id))
    # else set back the original value
    else:
      log.debug("Constraint not fulfilled : setting old value to the data model")
      self._store_one_value(bind_id, abs_path, original_value)
      log.debug("Constraint violation, bind id %s" % bind_id )
    
    return value, bind_id, constr_res, constr_comment, original_value 
  
  def store_value(self, bind_id, value, repe = None):
    '''
    Stores value to the specified bind.
    Argument repe contains string with information about
    repeating sections parent. 
    Before store, values in dependent binds are recalculated.
    Then the constraint is checked. If it is ok, value 
    Returns:
    - new value to be stored
    - id of bind where the value is stored
    - result of constraint evaluation
    - constraint comment (if result of constr. eval. is False)
    - original value in the data model ("old" value) 
    - calculated_binds - calculated vaues for dependent binds
    '''
    binds = self._find_bind_elem(bind_id)
    if not len(binds):
      log.error("No bind found for %s" % bind_id)
      return None, None, None, None, None 
    repe_data = tools.extract_repetition_parents_info(repe) 
    # extract function returns None if gets None
    path = self._get_abs_data_path(binds[0], repe_data)
    
    # store without constraint and run calculate, note the original value
    a,b,c,d, original = self.store_value_path(binds[0], value, path, suppress_constraints=True)
    calculated_binds, change_log = self.calculate_values(bind_id, repe)
    if len(change_log):
      log.debug("{0} binds affected by store action".format(str(len(change_log))))
    
    stored_val, to_bind, constr_ok, \
      constr_comment, old_val = self.store_value_path(binds[0], value, path)
    
    if constr_ok:
      self.is_changed = True
      # original value is the one sotred in origianl before first call to store_pat 
      return stored_val, to_bind, constr_ok, constr_comment, original, calculated_binds
    else:
      # needs to rollback changes in document
      # take the change_log and on each path set the old value (second memeber of tuple)
      self.rollback_data_changes(change_log) 
      # zfoengine: the second store_value_path "restored" the value stored by the
      # first (suppressed) call, so the refused value stayed in the model;
      # put back the value from before this store
      self._store_one_value(bind_id, path, original)
      return stored_val, to_bind, constr_ok, constr_comment, original, None
  
  def rollback_data_changes(self, change_log):
    '''
    Goes through the change log - which is list of tuples.
    On the path specified by first element in tuple sets the "old" value
    if different from "new" value (second and third element in tuple)
    '''
    for entry in change_log:
      if entry[2] != entry[3]:
        log.debug("Rollback bind '{0}', path '{1}', setting old value '{2}'".\
            format(entry[0], entry[1], entry[2]))
        self._store_one_value(entry[0], entry[1], entry[2])
  
  def save_to_file(self, user_selected_path=None):
    '''
    Stores xml from memory into its original file
    '''    
    def _get_final_file_parts():
      '''
      Reads original file of the form and finds place, where the data
      model will be placed.
      Returns 3 strings - first part before seriualized datamodel, then the datamodel
      and then the closing tags of <dsig:Object>
      '''
      # find prefix for xml signature namespace, probably always is used "dsig"
      dsig_pref = self._get_ns_prefix(ns.DSIG_URI)
      if dsig_pref is None:
        log.error("Cannot find prefix used for xml signature namespace, using default: dsig")
        dsig_pref = "dsig"
      
      # serialize data tree and wrap it into Object
      prepared_data = "<"+dsig_pref+":Object>"\
                          +etree.tostring(self._get_data_root().getroot(), pretty_print=True, encoding="unicode")\
                          +"</"+dsig_pref+":Object>"
      
      f = open(self.storage, "r", encoding="utf-8")
      zfo = f.read()
      f.close()
      
      # find the position, where to insert data branch
      dr = self._get_data_root().getroot()
      tag = dr.prefix + ":"
      tn = dr.tag[dr.tag.find('}')+1:]
      # model could be stored without namespace in some cases 
      tag = ''.join(['(',tag,'){0,1}',tn])
      log.debug(tag)
      
      # prepare regular expressions for start and end of data branch and file (/root_tag)
      data_end = "</"+tag+r">\s*</"+dsig_pref+r":Object>\s*</"+dsig_pref+":Signature>"
      data_start = "<"+dsig_pref+r":Object>\s*<"+tag
      file_end = "</"+dsig_pref+":Signature>"
      
      re_data_end = re.compile(data_end)
      re_data_start = re.compile(data_start)
      re_file_end = re.compile(file_end)
      
      root_end_idx = tools.get_last_match_idx(re_file_end.finditer(zfo), "start")
      end_part = zfo[root_end_idx:]
      # find all potential data end matches (sequence </datatag></object></signature>)
      # there could be two in case the form is prepared and signed after preparation
      m = re_data_end.finditer(zfo)
      matches = []
      for match in m:
        matches.append(match.span())
      
      if len(matches) == 0:
        log.debug("Openend zfo file does not have data branch, adding branch to the end of document")
        
        #root_end_idx = self.get_last_match_idx(re_file_end.finditer(zfo), "start")      
        first_part = zfo[:root_end_idx]      
        
      elif len(matches) == 1:
        log.debug("1 data end match detected, is it at the end of file?")
        # get the string after the match and find there some printable chars - if there are some,
        # this mathc is not at the end of the document
        data_end_idx = matches[0][1]
        re_any_nonws = re.compile(r"\S+")
        match = re_any_nonws.match(zfo[data_end_idx:])
        if match is None:
          log.debug("No non-whitespace characters found after potential data end, this is the real data end tag")
          log.debug("Behaves like in case there are two data branches and replacing the second one")
          start_of_data = tools.get_last_match_idx(re_data_start.finditer(zfo), "start")
        
          first_part = zfo[:start_of_data]      
         
        else:
          log.debug("Non-whitespace chars found, this end of data is inside the signed part of document. Data will come into the new Object")
          log.debug("Behaves like in case there is no data branch at the end of document")
          root_end_idx = tools.get_last_match_idx(re_file_end.finditer(zfo), "start")
        
          first_part = zfo[:root_end_idx]
        
      elif len(matches) == 2:
        log.debug("2 data end matches detected, replacing the second one")      
        
        start_of_data = tools.get_last_match_idx(re_data_start.finditer(zfo), "start")
        
        first_part = zfo[:start_of_data] 
      return first_part, prepared_data, end_part     
    
    def _write_zfo(file_to_write_fo, start_zfo, data, end_zfo, zip_filename=None):
      '''
      Writes parts of fo/zfo to the file_to_write_fo and if
      zip_filename is not None creates zip archive with that name.
      '''      
      # replace ".zfo" with ".fo" if we are storing fo form
      if file_to_write_fo.endswith(".zfo") and not self.comes_from_zip:
        last_dot = file_to_write_fo.rfind(".") 
        file_to_write_fo = file_to_write_fo[:last_dot] + ".fo"
      tmp_file = open(file_to_write_fo, "w", encoding="utf-8")
      tmp_file.write(start_zfo)
      tmp_file.write(data)
      tmp_file.write(end_zfo)
      tmp_file.flush()
      tmp_file.close()
      if zip_filename is not None:
        tools.make_zip(file_to_write_fo, zip_filename)
        return  zip_filename
      else:
        return file_to_write_fo
    
    # zfoengine: documents opened from bytes have no unpacked form.fo on disk
    # and no user-chosen path; the caller saves api.data_element() itself.
    if self.storage is None:
      log.warning("save_to_file is not supported by zfoengine, use api.data_element()")
      return {"type": "error", "msg": "Saving is done by the caller (zfoengine has no storage)"}
    
    # rewrite content of file with new content consisting of original 
    # part at the beginning and the new data branch at the end
    head = os.path.split(self.storage)[0]
    saveform_dir = os.path.join(head, "saveform")
    # previous version did not create saveform dir
    if not os.path.exists(saveform_dir):
      os.mkdir(saveform_dir)
            
    tmp_file_path = os.path.join(saveform_dir, "form.fo")
    
    first_part, prepared_data, end_part =  _get_final_file_parts()
    
    if user_selected_path is None:
      if self.comes_from_zip:
        head, tail = os.path.split(self.opened_from_path)
        zipname = os.path.join(head, "Vyplneno_"+tail)
        zipname = self.opened_from_path
        stored_to = _write_zfo(tmp_file_path, 
                               first_part, prepared_data, end_part, 
                               zipname)    
        
      else:
        stored_to = _write_zfo(self.opened_from_path, 
                               first_part, prepared_data, end_part 
                               )
                
    else:
      if self.comes_from_zip:
        stored_to = _write_zfo(tmp_file_path, 
                               first_part, prepared_data, end_part, 
                               user_selected_path)
                
      else:
        stored_to = _write_zfo(user_selected_path, 
                               first_part, prepared_data, end_part 
                               )
        
    result = {}
    if stored_to is None:
      result["type"] = "error"
      result["msg"] = "ZFO creation failed, check log."
    else:      
      result["type"] = msg_types.INFO
      result["msg"] = "Stored to: %s" % stored_to
      result["filename"] = stored_to
     
    return result
    
  def get_submit_node(self, submit_id):
    '''
    Returns submit node with specified id, if not found, returns None.
    '''
    xp = '//fm:submit[@Id="{0}"]'.format(submit_id)
    submits = self.root.xpath(xp, namespaces = {'fm': ns.FM_URI})
    if not len(submits):
      return None
    else:
      return submits[0]
  
  def get_template_text(self, template_name):
    '''
    Get the xslt template from tm:template element.
    Returns the template as an element, not as a text.
    '''
    xp ='//fm:template[@name="{0}"]'.format(template_name)
    templates = self.root.xpath(xp, namespaces = {'fm': ns.FM_URI})
    if not len(templates):
      return None
    else:
      template = templates[0]
      tmplt_root = template[0]
      #str = etree.tostring(tmplt_root)
      return tmplt_root
  
  def get_data_node(self, bind_id):
    '''
    Return element from original (not computable)
    data model tree. This node is used in submit actions,
    it will be probably sent to some server processing.
    ''' 
    binds = self._find_bind_elem(bind_id)
    bind = binds[0]
    abs_path = self._get_abs_data_path(bind)
    return self.get_data_node_path(abs_path)
    
    
  def get_data_node_path(self, abs_path):
    '''
    Returns the data_node at the specified path
    '''
    droot = self._get_data_root()
    nss = self._get_used_ns_prefix(abs_path)
    result = droot.xpath(abs_path, namespaces = nss)
    if not len(result):
      log.warning("Invalid xpath: %s" % abs_path)
      return None
    else:
      return result[0]
  
  def get_serialized_datamodel(self):
    '''
    Returns serialized data model, pretty-printed.
    '''
    dr = self._get_data_root()
    str = etree.tostring(dr, pretty_print=True, encoding="unicode")
    
    return str
   
  def attach_file(self, filename, field, repe):
    '''
    Uses store_value to store base64 encoded content of file to the model.
    Besides stores also filename and mimetype infromation about file.
    Returns what store_value returns.    
    '''
    f = open(filename, "rb")
    content = f.read()    
    content = base64.b64encode(content).decode("ascii")
    
    repe_data = tools.extract_repetition_parents_info(repe)
    
    expr = '//*[@form602bind="{0}"][@form602ct="bin"]'.format(field)
    inputs = self.root.xpath(expr)
    if not len(inputs):
      log.warning("Field in presentation part not found, cannot store filename and mimetype info")
    else:
      input = inputs[0]
      fnamebind_id = input.get("filenamebind")
      old_filename = None
      fname_bind = None
      
      if fnamebind_id is not None:        
        # store filename 
        fname_bind = self._find_bind_elem(fnamebind_id)
        if len(fname_bind):
          bind = fname_bind[0]
          abs_path = self._get_abs_data_path(bind, repe_data)
          a, b, c, d, old_filename = self.store_value_path(bind, filename, abs_path, suppress_constraints=True)
        
        # store filename with content path to dictionary
        content_bind = self._find_bind_elem(field)[0]
        self._get_abs_data_path(content_bind, repe_data)
                
      mtypebind_id = input.get("mediatypebind")
      old_mtype = None
      if mtypebind_id is not None:
        # store mimetype info
        # 602 xml filler considers file extension as a mimetype...
        dotindex = filename.rfind('.')
        mimetype = filename[dotindex+1:] # .included
        mtypebind = self._find_bind_elem(mtypebind_id)
        if len(mtypebind):
          bind = mtypebind[0]
          abs_path = self._get_abs_data_path(bind, repe_data)
          a, b, c, d, old_mtype = self.store_value_path(bind, mimetype, abs_path, suppress_constraints=True)
    
    stored_val, to_bind, constr_ok, \
      constr_comment, old_val, calculated_binds = self.store_value(field, content, repe)
    
    # if the storing of content failed, return filename and mimetype to the previous state
    if not constr_ok:
      if (fnamebind_id is not None) and (old_filename is not None) and (len(fname_bind)):
        self.store_value_path(fname_bind[0], old_filename, abs_path, suppress_constraints=True)
      # same with mediatype
      if (mtypebind_id is not None) and (old_mtype is not None) and (len(mtypebind)):
        self.store_value_path(mtypebind[0], old_mtype, abs_path, suppress_constraints=True)
          
    return stored_val, to_bind, constr_ok, constr_comment, old_val, calculated_binds
  
  def is_bind_file(self, bind_id):
    '''
    Returns true if the bind has "file" type - is bound
    with input type file.
    '''
    return bind_id in self.file_binds
  
  def remove_filename_path(self, bind_id, repe):
    '''
    Removes filename of stored file from its bind.
    '''
    binds = self._find_bind_elem(bind_id)
    bind = binds[0]
    repe_data = tools.extract_repetition_parents_info(repe)
    path = self._get_abs_data_path(bind, repe_data)
    data_node = self._get_data_root().xpath(path, namespaces=self._get_used_ns_prefix(path))
    self.get_binds_filename_bind(bind_id)
   
    if len(data_node) == 0:
      log.warning("Invalid xpath for filecontent")
      return 
    
    self._remove_filename_of_data_node(bind, data_node[0])
     
  def prepare_attachment_binds(self):
    '''
    Creates dictionaries with information used for binds storing files.
    Creates map of filenames of binds and their mediatypes 
    '''
    inputs = self.root.xpath('//*[@form602ct="bin"]')
    
    bind_ids = [input.get("form602bind") for input in inputs]
    filename_binds = [input.get("filenamebind") for input in inputs]
    mediatype_binds = [input.get("mediatypebind") for input in inputs]
    
    filenames_map = dict(zip(bind_ids, filename_binds, strict=True))
    mediatypes_map = dict(zip(bind_ids, mediatype_binds, strict=True))
    
    self.filenames_binds_map = filenames_map
    self.mediatypes_bind_map = mediatypes_map
   
  def get_binds_filename_bind(self, bind_id):
    '''
    Returns name of bind that holds filename of specified bind.
    '''    
    return self.filenames_binds_map.get(bind_id, "")
  
  def get_binds_mediatype_bind(self, bind_id): 
    '''
    Returns name of bind that holds mimetype of specified bind.
    '''       
    return self.mediatypes_bind_map.get(bind_id, "")
  
  def check_document(self):
    '''
    Checks constraint on each bind.
    Returns list of results, which is a dictionary with keys as bind names
    and values are the lists with tuples, either (True, "") or (False, constr_comment)
    '''
    binds = self.root.xpath("//fm:bind[@constraint]", namespaces={"fm": ns.FM_URI})
    results = {}
    for bind in binds:
      id = bind.get("Id")
      results[id] = []
      path = self._get_abs_data_path(bind)
      datanodes = self._get_data_root().xpath(path, 
                                              namespaces=self._get_used_ns_prefix(path))
      for dnode in datanodes:
        res = ev.eval_constraint(self, bind, None, node=dnode)
        results[id].append(res)
        
    return results