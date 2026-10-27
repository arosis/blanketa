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
Module handling "application events" - these are XPath expressions
inside SendAppEvent.
SendAppEvent functions' first parameter is event name - these are names of
functions in this module.
First arguments is followed by tuple of arguments, according to the number
of arguments of SendAppEvent.
Each method takes as its '0th' parameter reference to the underlying zfo document 
'''
import logging
log = logging.getLogger("zfo.app_events")

import lxml.etree as etree

from . import events

import itertools
import threading

# zfoengine: the original pickled the event objects, base64-encoded them into
# the element text and unpickled them in gtkgui_events_handlers.perform_click.
# Here the element text is a token into this registry instead (no pickle; the
# objects may hold lxml smart strings that do not pickle in Py3).
_EVENT_REGISTRY = {}
_EVENT_COUNTER = itertools.count(1)
_EVENT_LOCK = threading.Lock()
_EVENT_TOKEN_PREFIX = "zfoengine-event-"
# events that were created but never performed (e.g. inside a calculate) are
# dropped once the registry grows above this size
_EVENT_REGISTRY_MAX = 1000

def load_event(token):
  '''
  zfoengine: returns (and forgets) the event object stored under token,
  None if token is not an event token.
  '''
  if token is None:
    return None
  token = token.strip()
  if not token.startswith(_EVENT_TOKEN_PREFIX):
    return None
  with _EVENT_LOCK:
    return _EVENT_REGISTRY.pop(token, None)

def _el_to_str(element):
    
  return etree.tostring(element, encoding="unicode")
  

def _create_response_node(event_name, obj):
  '''
  Creates response etree element.
  text contains pickled base 64 encoded event object
  event is name of event that is responding
  js_data is json string with data potentially sent to browser
  '''    
  res = etree.Element(event_name)
  for att_name in obj.__dict__:
    att = getattr(obj, att_name)
    if isinstance(att, list):
      if len(att):
        if isinstance(att[0], etree._Element):
          dic = _el_to_str(att[0])
          setattr(obj, att_name, dic)
  with _EVENT_LOCK:
    if len(_EVENT_REGISTRY) > _EVENT_REGISTRY_MAX:
      _EVENT_REGISTRY.clear()
    token = _EVENT_TOKEN_PREFIX + str(next(_EVENT_COUNTER))
    _EVENT_REGISTRY[token] = obj
  res.text = token
  return res


def foreach(zfo_doc, args_tuple):
  event = events.Foreach(args_tuple[0],args_tuple[1],args_tuple[2],args_tuple[3])
  return _create_response_node("foreach", event)

def command_failure(ex):
  event = events.Failure(ex)
  return _create_response_node("failure", event)

def unknow_event(zfo_doc, event_name):
  event = events.Unknown(event_name)
  return _create_response_node("unknown", event)

def saveform(zfo_doc, args_tuple):
  log.info("'saveform' app event occured.")
  event = events.Saveform()  
  return _create_response_node("saveform", event)
  

def closeapp(zfo_doc, args_tuple):
  log.info("Exitting, 'closeapp' application event occured.")
  event = events.Closeapp()
  return _create_response_node("closeapp", event)
  
def printpdf(zfo_doc, args_tuple):
  event = events.Printpdf()
  return _create_response_node("printpdf", event)

def print_(zfo_doc, args_tuple):
  event = events.Print()
  return _create_response_node("print", event)

def checkdata(zfo_doc, args_tuple):
  event = events.Checkdata()
  return _create_response_node("checkdata", event)

def recalc(zfo_doc, args_tuple):
  event = events.Recalc()
  #return _create_response_node("recalc", type=msg_types.INFO, msg=msgs.APP_EVENT_OK, js_data=r)
  return _create_response_node("recalc", event)

def submit(zfo_doc, args):    
  event = events.Submit(args[0])  
  return _create_response_node("submit", event)

def submitpart(zfo_doc, args_tuple):
  event = events.Submitpart()
  return _create_response_node("submitpart", event)

def addsect(zfo_doc, args_tuple):
  event = events.Addsect()
  return _create_response_node("addsect", event)

def delsect(zfo_doc, args_tuple):
  event = events.Delsect()
  return _create_response_node("delsect", event)

def fillsect(zfo_doc, args_tuple):
  event = events.Fillsect()
  return _create_response_node("fillsect", event)

def loadformdata(zfo_doc, args_tuple):
  event = events.Loadformdata()
  return _create_response_node("loadformdata", event)

def openform(zfo_doc, args_tuple):
  event = events.Openform()
  return _create_response_node("openform", event)

def saveformsign(zfo_doc, args_tuple):
  event = events.Saveformsign()
  return _create_response_node("saveformsign", event)  

def openbinddata(zfo_doc, args_tuple):
  event = events.Openbinddata()
  return _create_response_node("openbinddata", event)

def setdatato(zfo_doc, args_tuple):
  event = events.Setdatato()
  return _create_response_node("setdatato", event)

def setevaldata(zfo_doc, args_tuple):
  event = events.Setevaldata(args_tuple[0], args_tuple[1], args_tuple[2])
  return _create_response_node("setvaldata", event)

def setdatavalue(zfo_doc, args_tuple):
  event = events.Setdatavalue(args_tuple[0], args_tuple[1], args_tuple[2])
  return _create_response_node("setdatavalue", event)

def messagebox(zfo_doc, args_tuple):  
  if len(args_tuple) > 2:
    dlgtype = int(args_tuple[2])
    event = events.Messagebox(str(args_tuple[0]), dlgtype)
  else:
    event = events.Messagebox(str(args_tuple[0]))
      
  return _create_response_node("messagebox", event)

def gotofield(zfo_doc, args_tuple):
  event = events.Gotofield()
  return _create_response_node("gotofield", event)

def startfiller(zfo_doc, args_tuple):
  event = events.Startfiller()
  return _create_response_node("startfiller", event)

def formtohtml(zfo_doc, args_tuple):
  event = events.Formtohtml()
  return _create_response_node("formtohtml", event)

def printparam(zfo_doc, args_tuple):
  event = events.Printparam()
  return _create_response_node("printparam", event)

def openfiller(zfo_doc, args_tuple):
  event = events.Openfiller()
  return _create_response_node("openfiller", event)

def loaddata(zfo_doc, args_tuple):
  event = events.Loaddata()
  return _create_response_node("loaddata", event)

def signclick(zfo_doc, args_tuple):
  event = events.Signclick()
  return _create_response_node("signclick", event)

def csvtoxml(zfo_doc, args_tuple):
  event = events.Csvtoxml()
  return _create_response_node("csvtoxml", event)

def openfilefrombuffer(zfo_doc, args_tuple):
  event = events.Openfilefrombuffer()
  return _create_response_node("openfilefrombuffer", event)

def printpreview(zfo_doc, args_tuple):
  event = events.Printpreview()
  return _create_response_node("printpreview", event)

def deletefile(zfo_doc, args_tuple):
  event = events.Deletefile(args_tuple[0])
  return _create_response_node("deletefile", event)

def savebinddata(zfo_doc, args_tuple):
  event = events.Savebinddata()
  return _create_response_node("savebinddata", event)

def openlink(zfo_doc, args_tuple):
  event = events.Openlink()
  return _create_response_node("openlink", event)

def viewdata(zfo_doc, args_tuple):
  event = events.Viewdata()
  return _create_response_node("viewdata", event)

def savebase64datatofile(zfo_doc, args_tuple):
  event = events.Savebase64datatofile(args_tuple[0], args_tuple[1])
  return _create_response_node("savebase64datatofile", event)
