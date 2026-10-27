# -*- coding: UTF-8 -*-
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
#*    Modified in October 2026 by Richard Winkler Bejr for "ZFO na Macu": ported to Python 3,
#*    separated from the GTK user interface and extended (recalculation, form check, default
#*    values); the git history of the project records each change.
'''
Messages and button values for ZFO editor
'''

FORM_READY = "Formulář připraven"
FORM_CLOSED = "Formulář zavřen"
FIELD_VALUE_STORED = "Hodnota <b>{0}</b> uložena do pole <b>{1}</b>"

FILE_SAVED = "Formulář uložen"
FILE_SAVING_OK = "Formulář uložen do souboru <b><tt>{0}</tt></b>"
FILE_SAVING_FAIL = "Chyba při ukládání, zkontrolujte log"

ATTRIBUTE = "Atribut"
VALUE = "Hodnota"
APP_TITLE = "ZFO editor"
ATT_ID = "ID"
ATT_READONLY = "Jen pro čtení"
ATT_REQUIRED = "Povinné"
ATT_NOTE = "Poznámka"
ATT_NAME = "Jméno"

FIELD_INFO = "Informace o poli"
NO_ZFO_OPENED = "Není otevřen ZFO formulář"
ZFO_OPENED = "ZFO: {0}"
CHOOSE_ZFO = "Vyberte ZFO formulář"

SAVE_ZFO_CAPTION = "Uložit ZFO"
OPEN_ZFO_CAPTION = "Otevřít ZFO"

MENU_OPEN = "Otevřít"
MENU_SAVE = "Uložit"
MENU_SAVE_AS = "Uložit jako"
MENU_QUIT = "Ukončit"
MENU_FILE = "Soubor"
MENU_CLOSE = "Zavřít formulář"

ZFO_PASS_PROTECTED = "ZFO s heslem"
ZFO_PASS_INFO =  "ZFO formulář '{0}' je chráněn heslem.\n"
ZFO_PASS_LABEL = "Heslo:"
ZFO_PASS_INVALID = "Neplatné heslo"

MODEL_WINDOW_TITLE = "Datovy model formulare"
SOURCE_WINDOW_TITLE = "HTML formulare - pouze pro ladici ucely"

ATTACHMENT_ADDED = "Přiložen soubor {0}"
ATTACHMENT_DELETED = "Odstranena priloha {0}"
ATTACHMENT_FAILED = "Chyba při přidání přílohy: {0}"

YES = "ano"
NO = "ne"

NOT_A_ZFO = "Formulář není správný ZFO archív"
NO_FORM_FO_INCLUDED = "Nenalezen soubor s formulářem"

QUIT_QUESTION = "Chcete ukončit zfo editor?"
QUIT_INFO = "Formulář vyžaduje ukončení aplikace."

CONNECTION_FAILURE = "Spojeni selhalo: {0}"

APP_EVENT_OK = "Ok"
APP_EVENT_FAILED = "Chyba ve spracování události"
APP_EVENT_NOTIMPL = "Obsluha události neimplementovana"

APP_EVENT_FILE_SAVED_OK = "Uloženou do souboru {0}"

APP_EVENT_SUBMIT_PROFILE_NOT_FOUND = "Nenalezen odesílaci profil '{0}'"
APP_EVENT_SUBMIT_METHOD_NOT_IMPLEMENTED = "Odeslání metodou neni implementovano"

APP_EVENT_FILE_SUBMIT_OK = "Data uloženy do souboru {0}"

APP_EVENT_FILE_DELETED = "Soubor '{0}' zmazán"

APP_EVENT_RECALCULATED = "Hodnoty prepočítány"

APP_EVENT_VALUE_SET_TO = "Hodnota '{0}' uložena do pole '{1}'"

APP_EVENT_UNKNOW = "Neznámy typ aplikační události"


