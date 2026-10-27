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
Module provides function to get the absolute path to the necessary 
ZFO Editor directories.
'''

import os
import sys

try:
  local_dir = os.path.dirname(os.path.abspath(__file__))
except NameError:
  local_dir = os.path.dirname(sys.executable)
while os.path.isfile(local_dir):
  # under cxFreeze this can happen because the path will be inside the exe file
  local_dir = os.path.dirname(local_dir)


def find_dir(name):
  # try to find it between root package siblings
  dirname = os.path.join(local_dir, '..', name)
  if os.path.isdir(dirname):
    return dirname    
  
  # try to find the directory inside egg
  dirname = os.path.join(local_dir, '..', 'share', 'zfoeditor')
  if os.path.isdir(dirname):
    # we are running inside egg very probably
    # and the share folder is next to the root package
    if os.path.isdir(os.path.join(dirname, name)):
      return os.path.join(dirname, name)    
  
  # try to find it in the system
  dirname = os.path.join(sys.prefix, 'share', 'zfoeditor')
  if os.path.isdir(dirname):    
    if os.path.isdir(os.path.join(dirname, name)):
      return os.path.join(dirname, name)
    
  return None

