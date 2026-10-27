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

zfoengine port: only the non-GUI state is kept (current document, quit flag,
save function). Browser, status bar, message lists, paths to scripts/styles
and the web send function belonged to the GTK window and are gone.
api.handle() sets the current document before every dispatch, because
application events (events.py) look the document up here.
'''

class Global(object):
  quit = False
  save_func = None
  document = None

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
  def set_save_func(cls, save_func):
    cls.save_func = staticmethod(save_func)

  @classmethod
  def get_save_func(cls):
    return cls.save_func
