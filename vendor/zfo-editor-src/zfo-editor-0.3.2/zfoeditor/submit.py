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
Module implementing submit actions of application events.
File can be submitted to over http-s, email, file...
'''

import logging
log = logging.getLogger("zfo.submit")

import httplib

HTTP_TIMEOUT = 10

def submit_http(url, headers, data_to_send):
  chunks = url.split("/")
  protocol = chunks[0]
  port = 80
  machine= chunks[2]
  m_parts = machine.split(':')
  if len(m_parts) == 2:
    machine = m_parts[0]
    port = int(m_parts[1])
  
  url = '/'.join(chunks[3:])
  
  if protocol.startswith("https"):
    con = httplib.HTTPSConnection(machine, port, timeout=HTTP_TIMEOUT)
  
  elif protocol.startswith("http"):
    con = httplib.HTTPConnection(machine, port, timeout=HTTP_TIMEOUT)
  
  else:
    log.error("Unknown submit protocol: "+protocol)
    raise Exception()
  
  try:
    log.info("Connecting to '{0}', port={1}, https={2}, timeout={3}"\
             .format(machine, port, protocol.startswith("https"), HTTP_TIMEOUT))
    httplib.HTTPConnection.debuglevel = 1
    if headers is None:
      con.request("POST", url, body=data_to_send)
    else:    
      con.request("POST", url, body=data_to_send, headers=headers)
   
    resp = con.getresponse()
  except Exception as ex:
    log.error(ex)
    return None, str(ex), None
  
  log.debug("Response status: {0} Reason: {1}".format(resp.status, resp.reason))
  data = resp.read()
  return resp.status, resp.reason, data
  


def submit_soap(url, headers, data_to_send):
  return submit_http(url, headers, data_to_send)
  
def submit_file(filename, data_to_write):
  '''
  Simply writes data to file with the name. File is overwritten if exists.
  '''
  f = open(filename, "w")
  f.write(data_to_write)
  f.close()
  
  return filename