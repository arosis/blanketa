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
#*    Written in November 2026 by Richard Winkler Bejr for "Blanketa".

# What a form may ask about a file the user attached: whether its content is what its extension says.
import re

ZIP_ARCHIVE = (b"PK\x03\x04",)
OLE_DOCUMENT = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
JPEG_PICTURE = (b"\xff\xd8\xff",)
CONTENT_STARTS = {
  "pdf": (b"%PDF-",), "png": (b"\x89PNG\r\n\x1a\n",), "gif": (b"GIF87a", b"GIF89a"), "rtf": (b"{\\rtf",),
  "jpg": JPEG_PICTURE, "jpeg": JPEG_PICTURE, "jfif": JPEG_PICTURE,
  "docx": ZIP_ARCHIVE, "xlsx": ZIP_ARCHIVE, "odt": ZIP_ARCHIVE, "doc": OLE_DOCUMENT, "xls": OLE_DOCUMENT,
}
TEXT_MARKS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
EXTENSION_IN_FILTER = re.compile(r"\*\.([A-Za-z0-9]{1,10})")

UNKNOWN_FORMAT, FORMAT_MISMATCH, FORMAT_NOT_ALLOWED = "unknown format", "format mismatch", "format not allowed"


def extension_as_written(name):
  return name.rsplit(".", 1)[1] if "." in name else ""


def extension_of(name):
  return extension_as_written(name).lower()


def extensions_in_filter(file_filter):
  # form602ffilter is the filter of a Windows file dialog: "PDF soubory (*.pdf)|*.pdf|XML soubory (*.xml)|*.xml".
  # Every second part is what the dialog matches, the parts before them only describe it.
  patterns = ";".join((file_filter or "").split("|")[1::2]).split(";")
  found = (EXTENSION_IN_FILTER.fullmatch(pattern.strip()) for pattern in patterns)
  return {extension.group(1).lower() for extension in found if extension}


def _starts_as_xml(content):
  for mark in TEXT_MARKS:
    if content.startswith(mark):
      content = content[len(mark):]
  return content.replace(b"\x00", b"").lstrip().startswith(b"<")


def fault_of_kind(name, content, allowed_extensions):
  extension = extension_of(name)
  if not extension or not content:
    return UNKNOWN_FORMAT
  if allowed_extensions and extension not in allowed_extensions:
    return FORMAT_NOT_ALLOWED
  if extension == "xml":
    return None if _starts_as_xml(content) else FORMAT_MISMATCH
  starts = CONTENT_STARTS.get(extension)
  if starts is not None and not content.startswith(starts):
    return FORMAT_MISMATCH
  return None
