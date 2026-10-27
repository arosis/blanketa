#! /usr/bin/python
# encoding: utf-8

#*    dsgui - a graphical user interface for Datove schranky
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


import sys
import glob
import os
from setuptools import setup
import release



data_files = [('share/zfoeditor/scripts', glob.glob('scripts/*.js')),
             ('share/zfoeditor/styles',glob.glob("styles/*.css")),
	           ('share/zfoeditor/styles/smoothness', glob.glob("styles/smoothness/*.css")),
	           ('share/zfoeditor/styles/smoothness/images', glob.glob("styles/smoothness/images/*.png")),
             ('share/zfoeditor/xslt',glob.glob('xslt/*.xsl')),
	           ('share/zfoeditor/config', ['config/log.conf']),
	           ('share/zfoeditor/start_page', ['start_page/start.html'])
             ] 

if not "sdist_dsc" in sys.argv and not "bdist_deb" in sys.argv:
  # we are not building a debian package, so we have to take care
  # of these things by hand
  # but only if it is not user installation
  if not "--user" in sys.argv:
    data_files += [('/usr/share/mime/packages', ('zfo-editor.xml',)),
                   ('/usr/share/applications', ('zfo-editor.desktop',)),
                   ]

data = dict(
  name = 'zfo-editor',
  version = release.ZFO_EDITOR_VERSION,
  description = "zfo editor",
  author = "CZ.NIC Labs",
  author_email = "zfo-editor@labs.nic.cz",
  url = "http://labs.nic.cz/zfo-editor/",
  license = "GNU LGPL",
  platforms = ["Unix"],
  long_description = "ZFO editor allows user to interact with ZFO forms.",  
  py_modules = ["release"],
  packages=["zfoeditor"],
  package_dir = {'zfoeditor': 'zfoeditor'},
  data_files = data_files,
  scripts = ['zfo_editor'],
  requires=["webkit", "lxml"],
  zip_safe=False
  )


set = setup(**data)

