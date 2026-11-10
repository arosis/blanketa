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

# The values a field accepts, as Software602 checks them in the HTML export of a form (Checker.Check of the stylesheet
# "Improvement 602XML forms" inside Filler602.exe): the length, the data type of the bind, the type of the field and the
# characters it allows. The check reads the value as the form stores it, dates and times in the XML notation.
import re
from dataclasses import dataclass

DECIMAL = re.compile(r"[-+]?\d*\.?\d*")
INTEGER = re.compile(r"[-+]?\d+")
DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}")
TIME = re.compile(r"([0-9]|0[0-9]|1[0-9]|2[0-3]):[0-5]\d(:[0-5]\d(\.\d{1,6})?)?")
DATE_TIME = re.compile(DATE.pattern + "T" + TIME.pattern)

NOT_A_NUMBER = "Hodnota není číslo."
NOT_A_WHOLE_NUMBER = "Hodnota není celé číslo v rozsahu, který pole připouští."
NOT_A_DATE = "Hodnota není datum."
NOT_A_TIME = "Hodnota není čas."
NOT_A_DATE_AND_TIME = "Hodnota není datum s časem."
NOT_YES_OR_NO = "Hodnota není ano, nebo ne."
NOT_THE_REQUIRED_SHAPE = "Hodnota nemá tvar, který formulář vyžaduje."

# Bounds of the XML Schema integer types; None leaves that side open.
INTEGER_TYPES = {
  "xs:integer": (None, None), "xs:long": (-2**63, 2**63 - 1), "xs:int": (-2**31, 2**31 - 1), "xs:short": (-2**15, 2**15 - 1),
  "xs:byte": (-128, 127), "xs:nonPositiveInteger": (None, 0), "xs:nonNegativeInteger": (0, None), "xs:positiveInteger": (1, None),
  "xs:negativeInteger": (None, -1), "xs:unsignedLong": (0, 2**64 - 1), "xs:unsignedInt": (0, 2**32 - 1),
  "xs:unsignedShort": (0, 2**16 - 1), "xs:unsignedByte": (0, 255),
}
SHAPED_TYPES = {
  "xs:float": (DECIMAL, NOT_A_NUMBER), "xs:decimal": (DECIMAL, NOT_A_NUMBER), "xs:double": (DECIMAL, NOT_A_NUMBER),
  "xs:date": (DATE, NOT_A_DATE), "xs:time": (TIME, NOT_A_TIME), "xs:dateTime": (DATE_TIME, NOT_A_DATE_AND_TIME),
  "xs:boolean": (re.compile("true|false|0|1"), NOT_YES_OR_NO),
}
FIELD_TYPES = {"num": (DECIMAL, NOT_A_NUMBER), "dat": (DATE, NOT_A_DATE), "tim": (TIME, NOT_A_TIME)}

# Character groups of form602chars written as names joined by "|". The sets are Form Filler's own, Czech letters included
# as it lists them (without ů, ň, ť and ď).
CHARACTER_GROUPS = {
  "uppercase": "A-ZÝÁÍÉÓÚŽŠČŘĚ0-9 ",
  "lowercase": "a-zýáíéóúžščřě0-9 ",
  "decimal_digits": "0-9.",
  "space_characters": " \t\n\r\f\v",
  "punctuation": ".,!?:",
  "blank_characters": " \t",
  "hexadecimal_digits": "0-9a-fA-F",
  "any_linguistic": "a-zýáíéóúžščřěA-ZÝÁÍÉÓÚŽŠČŘĚ ",
}


@dataclass(frozen=True)
class FieldRules:
  field_type: str = ""
  allowed_characters: str = ""
  max_characters: str = ""
  data_type: str = ""
  pattern: str = ""


def format_date(date, pattern):
  # Form Filler writes dates by the Windows date pictures: d, dd, M, MM, yy and yyyy.
  tokens = {"dd": f"{date.day:02}", "d": str(date.day), "MM": f"{date.month:02}", "M": str(date.month),
            "yyyy": str(date.year), "yy": f"{date.year % 100:02}"}
  return re.sub("dd|d|MM|M|yyyy|yy", lambda match: tokens[match.group(0)], pattern)


def characters_word(count):
  return "znak" if count == 1 else "znaky" if 2 <= count <= 4 else "znaků"


def _type_problem(value, data_type):
  if data_type in INTEGER_TYPES:
    low, high = INTEGER_TYPES[data_type]
    if not INTEGER.fullmatch(value):
      return NOT_A_WHOLE_NUMBER
    number = int(value)
    return NOT_A_WHOLE_NUMBER if (low is not None and number < low) or (high is not None and number > high) else None
  shape, message = SHAPED_TYPES.get(data_type, (None, None))
  return message if shape is not None and not shape.fullmatch(value) else None


def _characters_left(value, allowed):
  if allowed.startswith("[") and allowed.endswith("]"):
    pattern = allowed
  else:
    groups = [CHARACTER_GROUPS[name] for name in allowed.split("|") if name in CHARACTER_GROUPS]
    if not groups:
      return ""
    pattern = "[" + "".join(groups) + "]"
  try:
    return re.sub(pattern, "", value)
  except re.error:
    return ""


def value_problem(value, rules):
  if value == "":
    return None
  if rules.max_characters.isdigit() and len(value) > int(rules.max_characters):
    count = int(rules.max_characters)
    return f"Hodnota může mít nejvýše {count} {characters_word(count)}."
  problem = _type_problem(value, rules.data_type)
  if problem:
    return problem
  if rules.pattern:
    # Form Filler looks for the pattern anywhere in the value, not for the whole value as XML Schema would.
    try:
      if not re.search(rules.pattern, value):
        return NOT_THE_REQUIRED_SHAPE
    except re.error:
      pass
  if rules.field_type in FIELD_TYPES:
    shape, message = FIELD_TYPES[rules.field_type]
    if not shape.fullmatch(value):
      return message
  if rules.allowed_characters:
    left = _characters_left(value, rules.allowed_characters)
    if left and rules.field_type == "num":
      # The form stores a decimal point, the user types the comma the field allows.
      left = left and _characters_left(value.replace(".", ","), rules.allowed_characters)
    if left:
      shown = ", ".join(f"„{character}“" for character in dict.fromkeys(left) if not character.isspace()) or "mezery"
      return f"Hodnota obsahuje znaky, které pole nepřipouští: {shown}."
  return None
