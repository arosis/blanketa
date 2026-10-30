'''
Public API of zfoengine - a GUI-less Python 3 port of the backend of CZ.NIC's
zfo-editor 0.3.2 (GNU LGPL v2 or later). It replaces the GTK main loop of zfo_gui.main(): the
browser JavaScript (zfo_script.js, repe_sections.js) sends
{"type": ..., "data": ...} messages over HTTP instead of document.title, and
the caller returns the response list produced here.

  doc = open_document(form_fo_bytes)
  responses = handle(doc, "init_rep_sections", "")
  root = data_element(doc)

Responses are dicts:
  {"type": "javascript", "data": "<js to evaluate in the page>"}
  {"type": "gtk", "info_area": "field_info"|"message_area",
   "info_type": "info"|"warn"|"error", "data": {"to_display": ...},
   ["messagebox": True]}

lxml XPath extension functions and the "current document" of application
events are process-global (as in the original), so calls are serialized by a
module lock; documents may be used from several threads.
'''
import copy
import io
import json
import logging
import threading
import zipfile

from . import gtkgui_events_handlers as geh
from . import msg_types, msgs, zfo_parser
from .global_var import Global

log = logging.getLogger("zfo.api")

_LOCK = threading.RLock()

# message types the desktop app handled in zfo_gui.main() itself
_DOCUMENT_READY = "document.ready"


def _activate(doc):
  '''
  Makes doc the document the global state (xpath extensions, Global.document)
  points at.
  '''
  doc._register_xpath_extensions()
  Global.set_doc(doc)


def read_form_fo(zfo_bytes):
  '''
  Returns the form.fo bytes from a .zfo archive (or the input itself if it is
  not a zip). Password-protected archives are not supported.
  '''
  if zfo_bytes[:2] != b"PK":
    return zfo_bytes
  with zipfile.ZipFile(io.BytesIO(zfo_bytes)) as zf:
    return zf.read("form.fo")


def open_document(fo_bytes):
  '''
  Builds the editor's document object (zfo_doc.ZFO_document) from the raw
  form.fo bytes (the whole signed file). If filled data exist (a dsig:Object
  without Id appended to the publisher signature), the data model starts from
  them, otherwise from the empty form:instance.
  '''
  with _LOCK:
    doc = zfo_parser.parse_fo(fo_bytes, comes_from_zip=True)
    _activate(doc)
    # build the data model now (the GTK app did it lazily on the first message)
    doc._get_data_root()
    # done on "document.ready" in zfo_gui.main(); also needed before that
    # message for forms with attachment inputs
    doc.prepare_attachment_binds()
    return doc


def handle(doc, msg_type, data):
  '''
  Dispatches one browser message exactly like zfo_gui.main() did: the handler
  of the same name in gtkgui_events_handlers is called with (doc, data) and
  its response list is returned. Unknown types give []. A handler that raises
  is logged and answered with empty_callback() plus an error message, so the
  page's pending-request indicator is released.
  '''
  with _LOCK:
    _activate(doc)
    responses = []
    if msg_type == _DOCUMENT_READY:
      doc.prepare_attachment_binds()
      responses += geh._construct_gtk_info_response(msgs.FORM_READY, area="message_area",
                                                    type=msg_types.INFO)
    if not msg_type or msg_type.startswith("_") or not hasattr(geh, msg_type):
      if msg_type != _DOCUMENT_READY:
        log.warning('Handler for %r does not exist.', msg_type)
      return responses
    func = getattr(geh, msg_type)
    if not callable(func) or getattr(func, "__module__", None) != geh.__name__:
      log.warning('Handler for %r does not exist.', msg_type)
      return responses
    try:
      responses += func(doc, data)
    except Exception as ex:
      log.exception("Handler %r failed", msg_type)
      responses += geh._construct_web_response("empty_callback()")
      responses += geh._construct_gtk_info_response("%s: %s" % (msg_type, ex), area="message_area",
                                                    type=msg_types.ERROR)
    return responses


def handle_json(doc, message):
  '''
  Convenience: takes the JSON string the page passes to send() and returns
  the response list.
  '''
  msg = json.loads(message)
  if "type" not in msg:
    log.warning('Message type missing')
    return []
  return handle(doc, msg["type"], msg.get("data"))


def data_element(doc):
  '''
  Returns a copy of the current data instance root (e.g.
  {http://software602.cz/sample}root) as it should be saved - the original
  (not zero-filled) data tree, after calculations.
  '''
  with _LOCK:
    return copy.deepcopy(doc._get_data_root().getroot())


def readonly_binds(doc):
  '''
  Convenience: list of bind ids that currently evaluate as readonly.
  '''
  with _LOCK:
    _activate(doc)
    return doc.get_readonly_binds()["readonly_names"]
