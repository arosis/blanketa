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
#*    Written in October 2026 by Richard Winkler Bejr for "Blanketa".

# Interpreter of the JavaScript forms carry, for their calculations and constraints.
#
# A form is a hostile file, so its scripts run here and not in the page, which can sign. They reach nothing but the values
# below: no Python object is ever handed to them, an unknown name stops the script instead of being guessed, and a budget
# of steps stops any loop. A regular expression has its own matcher, because Python's re cannot be stopped once it backtracks.
#
# Numbers are floats, undefined is UNDEFINED and null is None. Local time is kept as if it were UTC, scripts only do date
# arithmetic with it.
import datetime
import json
import math
import re

STEP_BUDGET = 2_000_000  # for one script run; the largest forms published by offices stay far below it
MAX_CALL_DEPTH = 150
MAX_STRING_LENGTH = 4_000_000
MAX_ARRAY_ITEMS = 200_000
MAX_SOURCE_LENGTH = 8_000_000


class Unsupported(Exception):
  # The script needs something this interpreter does not offer, or cannot be read. Nothing it did counts.
  pass


class Exhausted(Exception):
  pass


class Thrown(Exception):
  def __init__(self, value):
    super().__init__(value)
    self.value = value


class _Undefined:
  __slots__ = ()
  def __repr__(self):
    return "undefined"


UNDEFINED = _Undefined()
_BREAK, _CONTINUE = object(), object()


class _Return:
  __slots__ = ("value",)
  def __init__(self, value):
    self.value = value


class ScriptObject:
  __slots__ = ("props",)
  def __init__(self, props=None):
    self.props = props if props is not None else {}


class ScriptArray:
  __slots__ = ("items", "props")
  def __init__(self, items=None):
    self.items = items if items is not None else []
    self.props = {}


class NativeFunction:
  __slots__ = ("call", "construct", "props")
  def __init__(self, call, construct=None, props=None):
    self.call = call
    self.construct = construct
    self.props = props if props is not None else {}


class HostObject(ScriptObject):
  # An object of the 602 application: asking it for anything else than what is offered stops the script.
  __slots__ = ("name",)
  def __init__(self, name, props):
    super().__init__(props)
    self.name = name


class Date:
  __slots__ = ("ms", "props")
  def __init__(self, milliseconds):
    self.ms = milliseconds
    self.props = {}


class _Scope:
  __slots__ = ("vars", "parent")
  def __init__(self, parent, variables=None):
    self.vars = variables if variables is not None else {}
    self.parent = parent



_TOKEN = re.compile(r'''
    (?P<skip>(?:\s+|//[^\n]*|/\*.*?\*/)+)
  | (?P<name>[^\W\d][\w]*|\$[\w$]*)
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<str>"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*')
  | (?P<punct>>>>=|===|!==|>>>|<<=|>>=|=>|==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|<<|>>|[{}()\[\];,.?:~!<>=+\-*/%&|^])
''', re.X | re.S)
_REGEX = re.compile(r'/((?:[^/\\\n\[]|\\.|\[(?:[^\]\\\n]|\\.)*\])+)/([a-z]*)')
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0", "\n": ""}
_ESCAPE = re.compile(r'\\(u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|.)', re.S)
_KEYWORDS_BEFORE_REGEX = {"return", "typeof", "case", "in", "of", "void", "delete", "do", "else", "new", "instanceof"}


def _unescape(match):
  text = match.group(1)
  if len(text) > 1:
    return chr(int(text[1:], 16))
  return _ESCAPES.get(text, text)


def _tokenize(source):
  if len(source) > MAX_SOURCE_LENGTH:
    raise Unsupported("skript je příliš dlouhý")
  found = []
  position, end, newline = 0, len(source), False
  while position < end:
    if source[position] == "/" and source[position:position + 2] not in ("//", "/*"):
      last = found[-1] if found else None
      after_value = last and (last[0] in ("num", "str", "re") or (last[0] == "name" and last[1] not in _KEYWORDS_BEFORE_REGEX)
                              or (last[0] == "punct" and last[1] in (")", "]", "}")))
      if not after_value:
        match = _REGEX.match(source, position)
        if match is None:
          raise Unsupported("nečitelný regulární výraz")
        found.append(("re", (match.group(1), match.group(2)), newline))
        position, newline = match.end(), False
        continue
    match = _TOKEN.match(source, position)
    if match is None:
      raise Unsupported("nečitelný skript")
    kind = match.lastgroup
    text = match.group()
    position = match.end()
    if kind == "skip":
      newline = newline or "\n" in text
      continue
    if kind == "num":
      value = float(int(text, 16)) if text[:2] in ("0x", "0X") else float(text)
    elif kind == "str":
      value = _ESCAPE.sub(_unescape, text[1:-1]) if "\\" in text else text[1:-1]
    else:
      value = text
    found.append((kind, value, newline))
    newline = False
  found.append(("eof", "", newline))
  return found



_NUMERIC = re.compile(r'[+-]?(?:\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?|Infinity)\Z')


def to_number(value):
  if type(value) is float:
    return value
  if type(value) is str:
    text = value.strip()
    if not text:
      return 0.0
    if _NUMERIC.match(text):
      return float(text.replace("Infinity", "inf"))
    if text[:2] in ("0x", "0X"):
      try:
        return float(int(text[2:], 16))
      except ValueError:
        return math.nan
    return math.nan
  if value is True:
    return 1.0
  if value is False or value is None:
    return 0.0
  if value is UNDEFINED:
    return math.nan
  if isinstance(value, Date):
    return value.ms
  return to_number(to_primitive(value))


def number_text(value):
  if value != value:
    return "NaN"
  if value in (math.inf, -math.inf):
    return "Infinity" if value > 0 else "-Infinity"
  if value == int(value) and abs(value) < 2 ** 53:
    return str(int(value))
  text = repr(value)
  if "e" not in text:
    return text[:-2] if text.endswith(".0") else text
  mantissa, exponent = text.split("e")
  exponent = int(exponent)
  digits = mantissa.replace(".", "").replace("-", "")
  sign = "-" if value < 0 else ""
  if -7 < exponent < 0:
    return f"{sign}0.{'0' * (-exponent - 1)}{digits}"
  if 0 < exponent < 21:  # the shortest digits, then zeros: not the exact binary value
    return sign + digits + "0" * (exponent + 1 - len(digits))
  return f"{mantissa}e{'+' if exponent > 0 else '-'}{abs(exponent)}"


def to_string(value):
  if type(value) is str:
    return value
  if type(value) is float:
    return number_text(value)
  if value is True:
    return "true"
  if value is False:
    return "false"
  if value is None:
    return "null"
  if value is UNDEFINED:
    return "undefined"
  return to_string(to_primitive(value))


def to_primitive(value):
  if isinstance(value, ScriptArray):
    return ",".join("" if item is None or item is UNDEFINED else to_string(item) for item in value.items)
  if isinstance(value, Date):
    return _date_text(value.ms)
  if isinstance(value, Regex):
    return f"/{value.source}/{value.flags}"
  if isinstance(value, (Function, NativeFunction)):
    return "function () { [native code] }"
  if isinstance(value, ScriptObject):
    return "[object Object]"
  return value


def truthy(value):
  if type(value) is bool:
    return value
  if type(value) is str:
    return value != ""
  if type(value) is float:
    return value == value and value != 0.0
  return value is not None and value is not UNDEFINED


def to_int32(value):
  number = to_number(value)
  if number != number or number in (math.inf, -math.inf):
    return 0
  whole = int(number) & 0xFFFFFFFF
  return whole - 0x100000000 if whole >= 0x80000000 else whole


def strict_equal(left, right):
  if type(left) is float and type(right) is float:
    return left == right
  if type(left) is not type(right):
    return False
  if type(left) in (str, bool):
    return left == right
  return left is right


def loose_equal(left, right):
  if type(left) is type(right):
    return strict_equal(left, right)
  if (left is None or left is UNDEFINED) or (right is None or right is UNDEFINED):
    return (left is None or left is UNDEFINED) and (right is None or right is UNDEFINED)
  if type(left) in (float, str, bool) and type(right) in (float, str, bool):
    return to_number(left) == to_number(right)
  if type(left) in (float, str, bool):
    return loose_equal(left, to_primitive(right))
  if type(right) in (float, str, bool):
    return loose_equal(to_primitive(left), right)
  return left is right


def _less_than(left, right):
  # a < b, None when a NaN takes part.
  left, right = to_primitive(left), to_primitive(right)
  if type(left) is str and type(right) is str:
    return left < right
  left, right = to_number(left), to_number(right)
  if left != left or right != right:
    return None
  return left < right


def type_of(value):
  if value is UNDEFINED:
    return "undefined"
  if type(value) is float:
    return "number"
  if type(value) is str:
    return "string"
  if type(value) is bool:
    return "boolean"
  if isinstance(value, (Function, NativeFunction)):
    return "function"
  return "object"



def _days_from_civil(year, month, day):
  year -= month <= 2
  era = year // 400
  of_era = year - era * 400
  of_year = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
  return era * 146097 + of_era * 365 + of_era // 4 - of_era // 100 + of_year - 719468


def _civil_from_days(days):
  days += 719468
  era = days // 146097
  of_era = days - era * 146097
  year_of_era = (of_era - of_era // 1460 + of_era // 36524 - of_era // 146096) // 365
  of_year = of_era - (365 * year_of_era + year_of_era // 4 - year_of_era // 100)
  shifted_month = (5 * of_year + 2) // 153
  day = of_year - (153 * shifted_month + 2) // 5 + 1
  month = shifted_month + (3 if shifted_month < 10 else -9)
  return year_of_era + era * 400 + (month <= 2), month, day


def _make_time(year, month, day=1.0, hour=0.0, minute=0.0, second=0.0, milliseconds=0.0):
  parts = (year, month, day, hour, minute, second, milliseconds)
  if any(part != part or part in (math.inf, -math.inf) for part in parts):
    return math.nan
  year, month, day, hour, minute, second, milliseconds = (int(part) for part in parts)
  if abs(year) > 300000 or abs(month) > 4000000 or abs(day) > 110000000:
    return math.nan
  year += month // 12
  days = _days_from_civil(year, month % 12 + 1, 1) + day - 1
  return float(days * 86400000 + hour * 3600000 + minute * 60000 + second * 1000 + milliseconds)


def _date_fields(milliseconds):
  whole = int(milliseconds)
  days, rest = divmod(whole, 86400000)
  year, month, day = _civil_from_days(days)
  return [year, month - 1, day, rest // 3600000, rest // 60000 % 60, rest // 1000 % 60, rest % 1000, (days + 4) % 7]


def _date_text(milliseconds):
  if milliseconds != milliseconds:
    return "Invalid Date"
  year, month, day, hour, minute, second, _, weekday = _date_fields(milliseconds)
  names = "Sun Mon Tue Wed Thu Fri Sat".split()
  months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
  return f"{names[weekday]} {months[month]} {day:02d} {year} {hour:02d}:{minute:02d}:{second:02d} GMT+0000"


_DATE_TEXT = re.compile(r'(\d{4})-(\d{2})(?:-(\d{2}))?(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?')


def _parse_date(text):
  match = _DATE_TEXT.fullmatch(text.strip())
  if match is None:
    return math.nan
  year, month, day, hour, minute, second = (float(part) if part else None for part in match.groups())
  return _make_time(year, month - 1, day or 1.0, hour or 0.0, minute or 0.0, second or 0.0)


def _current_local_time():
  now = datetime.datetime.now()
  return _make_time(float(now.year), float(now.month - 1), float(now.day), float(now.hour), float(now.minute), float(now.second))


_DATE_GETTERS = {"getFullYear": 0, "getMonth": 1, "getDate": 2, "getHours": 3, "getMinutes": 4, "getSeconds": 5,
                 "getMilliseconds": 6, "getDay": 7}
_DATE_SETTERS = {"setFullYear": 0, "setMonth": 1, "setDate": 2, "setHours": 3, "setMinutes": 4, "setSeconds": 5, "setMilliseconds": 6}


def _date_method(date, name):
  if name in _DATE_GETTERS:
    index = _DATE_GETTERS[name]
    return NativeFunction(lambda this, args: math.nan if date.ms != date.ms else float(_date_fields(date.ms)[index]))
  if name in _DATE_SETTERS:
    index = _DATE_SETTERS[name]
    def setter(this, args):
      if date.ms != date.ms:
        return math.nan
      parts = [float(part) for part in _date_fields(date.ms)[:7]]
      for offset, arg in enumerate(args[:7 - index]):
        parts[index + offset] = to_number(arg)
      date.ms = _make_time(*parts)
      return date.ms
    return NativeFunction(setter)
  if name in ("getTime", "valueOf"):
    return NativeFunction(lambda this, args: date.ms)
  if name == "toString":
    return NativeFunction(lambda this, args: _date_text(date.ms))
  return UNDEFINED



class Regex:
  __slots__ = ("source", "flags", "matcher", "groups", "last_index", "props", "budget")

  def __init__(self, source, flags, budget):
    if set(flags) - set("gims"):
      raise Unsupported(f"příznak regulárního výrazu {flags}")
    self.source, self.flags, self.budget = source, flags, budget
    self.last_index = 0.0
    self.props = {}
    parser = _RegexParser(source, flags, budget)
    self.matcher = parser.parse()
    self.groups = parser.groups

  def match_at(self, text, start):
    if len(text) > MAX_STRING_LENGTH:
      raise Exhausted("text")
    for begin in range(start, len(text) + 1):
      captures = [None] * (self.groups + 1)
      end = self.matcher(text, begin, captures, lambda position, caps: position)
      self.budget.tick()
      if end is not None:
        return begin, end, captures
    return None


_CLASS_ESCAPES = {
  "d": lambda character: "0" <= character <= "9",
  "w": lambda character: character.isascii() and (character.isalnum() or character == "_"),
  "s": lambda character: character.isspace() or character == "﻿",
}


class _RegexParser:
  # Builds a backtracking matcher: matcher(text, at, captures, then) gives what then gave, None when nothing matches.

  def __init__(self, source, flags, budget):
    self.s, self.i, self.budget = source, 0, budget
    self.ignore_case, self.multiline, self.dotall = "i" in flags, "m" in flags, "s" in flags
    self.groups = 0
    self.depth = 0

  def parse(self):
    node = self.alternation()
    if self.i != len(self.s):
      raise Unsupported("nečitelný regulární výraz")
    return node

  def alternation(self):
    self.depth += 1
    if self.depth > 60:
      raise Unsupported("příliš zanořený regulární výraz")
    options = [self.sequence()]
    while self.i < len(self.s) and self.s[self.i] == "|":
      self.i += 1
      options.append(self.sequence())
    self.depth -= 1
    if len(options) == 1:
      return options[0]
    def alternatives(text, position, caps, then):
      for option in options:
        end = option(text, position, caps, then)
        if end is not None:
          return end
      return None
    return alternatives

  def sequence(self):
    items = []
    while self.i < len(self.s) and self.s[self.i] not in "|)":
      items.append(self.term())
    if not items:
      return lambda text, position, caps, then: then(position, caps)
    node = items[-1]
    for item in reversed(items[:-1]):
      node = self._chain(item, node)
    return node

  @staticmethod
  def _chain(first, second):
    return lambda text, position, caps, then: first(text, position, caps, lambda end, character: second(text, end, character, then))

  def term(self):
    tested, node = self.atom()
    return self.quantifier(tested, node)

  def _single(self, test):
    budget = self.budget
    def single(text, position, caps, then):
      budget.tick()
      return then(position + 1, caps) if position < len(text) and test(text[position]) else None
    return test, single

  def atom(self):
    character = self.s[self.i]
    self.i += 1
    if character == "^":
      multi = self.multiline
      return None, lambda text, position, caps, then: then(position, caps) if position == 0 or (multi and text[position - 1] == "\n") else None
    if character == "$":
      multi = self.multiline
      return None, lambda text, position, caps, then: then(position, caps) if position == len(text) or (multi and text[position] == "\n") else None
    if character == "(":
      return None, self.group()
    if character == ".":
      dotall = self.dotall
      return self._single(lambda current_character: dotall or current_character not in "\n\r  ")
    if character == "[":
      return self._single(self.char_class())
    if character == "\\":
      return self.escape()
    if character in "*+?":
      raise Unsupported("nečitelný regulární výraz")
    return self._single(self._literal(character))

  def _literal(self, character):
    if self.ignore_case:
      lower = character.lower()
      return lambda current_character: current_character.lower() == lower
    return lambda current_character: current_character == character

  def group(self):
    capture = None
    look = None
    if self.s.startswith("?:", self.i):
      self.i += 2
    elif self.s.startswith("?=", self.i) or self.s.startswith("?!", self.i):
      look = self.s[self.i + 1]
      self.i += 2
    elif self.s.startswith("?", self.i):
      raise Unsupported("tento regulární výraz")
    else:
      self.groups += 1
      capture = self.groups
    inner = self.alternation()
    if self.i >= len(self.s) or self.s[self.i] != ")":
      raise Unsupported("nečitelný regulární výraz")
    self.i += 1
    if look == "=":
      def ahead(text, position, caps, then):
        return then(position, caps) if inner(text, position, caps, lambda end, character: end) is not None else None
      return ahead
    if look == "!":
      def not_ahead(text, position, caps, then):
        return then(position, caps) if inner(text, position, list(caps), lambda end, character: end) is None else None
      return not_ahead
    if capture is None:
      return inner
    def captured(text, position, caps, then):
      def closed(end, character):
        saved = character[capture]
        character[capture] = text[position:end]
        result = then(end, character)
        if result is None:
          character[capture] = saved
        return result
      return inner(text, position, caps, closed)
    return captured

  def _escape_char(self, character):
    if character == "x" and re.fullmatch(r'[0-9a-fA-F]{2}', self.s[self.i:self.i + 2]):
      self.i += 2
      return chr(int(self.s[self.i - 2:self.i], 16))
    if character == "u" and re.fullmatch(r'[0-9a-fA-F]{4}', self.s[self.i:self.i + 4]):
      self.i += 4
      return chr(int(self.s[self.i - 4:self.i], 16))
    if character == "c" and self.i < len(self.s) and self.s[self.i].isalpha():
      self.i += 1
      return chr(ord(self.s[self.i - 1]) % 32)
    return {"n": "\n", "t": "\t", "r": "\r", "f": "\f", "v": "\v", "0": "\0"}.get(character, character)

  def escape(self):
    if self.i >= len(self.s):
      raise Unsupported("nečitelný regulární výraz")
    character = self.s[self.i]
    self.i += 1
    if character in _CLASS_ESCAPES:
      return self._single(_CLASS_ESCAPES[character])
    if character in ("D", "W", "S"):
      test = _CLASS_ESCAPES[character.lower()]
      return self._single(lambda current_character: not test(current_character))
    if character in "bB":
      word = _CLASS_ESCAPES["w"]
      wanted = character == "b"
      def boundary(text, position, caps, then):
        before = position > 0 and word(text[position - 1])
        after = position < len(text) and word(text[position])
        return then(position, caps) if (before != after) == wanted else None
      return None, boundary
    if character in "123456789":
      number = int(character)
      def backreference(text, position, caps, then):
        seen = caps[number] if number < len(caps) else None
        if seen is None:
          return then(position, caps)
        return then(position + len(seen), caps) if text.startswith(seen, position) else None
      return None, backreference
    return self._single(self._literal(self._escape_char(character)))

  def char_class(self):
    negated = self.s.startswith("^", self.i)
    if negated:
      self.i += 1
    chars, ranges, tests = set(), [], []
    while True:
      if self.i >= len(self.s):
        raise Unsupported("nečitelný regulární výraz")
      character = self.s[self.i]
      self.i += 1
      if character == "]":
        break
      low = self._class_atom(character, tests)
      if low is None:
        continue
      if self.s.startswith("-", self.i) and self.i + 1 < len(self.s) and self.s[self.i + 1] != "]":
        self.i += 1
        high = self._class_atom(self.s[self.i], tests, advance=True)
        if high is None:
          chars.update((low, "-"))
        elif low > high:
          raise Unsupported("nečitelný regulární výraz")
        else:
          ranges.append((low, high))
      else:
        chars.add(character if character != "\\" else low)
    if self.ignore_case:
      chars |= {character.lower() for character in chars} | {character.upper() for character in chars}
    ignore_case = self.ignore_case
    def inside(current_character):
      if current_character in chars:
        return True
      for low, high in ranges:
        if low <= current_character <= high or (ignore_case and (low <= current_character.lower() <= high or low <= current_character.upper() <= high)):
          return True
      return any(test(current_character) for test in tests)
    return (lambda current_character: not inside(current_character)) if negated else inside

  def _class_atom(self, character, tests, advance=False):
    if advance:
      self.i += 1
    if character != "\\":
      return character
    if self.i >= len(self.s):
      raise Unsupported("nečitelný regulární výraz")
    e = self.s[self.i]
    self.i += 1
    if e in _CLASS_ESCAPES:
      tests.append(_CLASS_ESCAPES[e])
      return None
    if e in ("D", "W", "S"):
      test = _CLASS_ESCAPES[e.lower()]
      tests.append(lambda current_character, test=test: not test(current_character))
      return None
    if e == "b":
      return "\b"
    return self._escape_char(e)

  def quantifier(self, tested, node):
    if self.i >= len(self.s):
      return node
    character = self.s[self.i]
    if character == "*":
      low, high = 0, math.inf
      self.i += 1
    elif character == "+":
      low, high = 1, math.inf
      self.i += 1
    elif character == "?":
      low, high = 0, 1
      self.i += 1
    elif character == "{":
      match = re.compile(r'\{(\d+)(,(\d*))?\}').match(self.s, self.i)
      if match is None:
        return node  # a brace that counts nothing is an ordinary character in this dialect
      low = int(match.group(1))
      high = low if match.group(2) is None else (int(match.group(3)) if match.group(3) else math.inf)
      self.i = match.end()
      if low > high:
        raise Unsupported("nečitelný regulární výraz")
    else:
      return node
    greedy = True
    if self.s.startswith("?", self.i):
      greedy = False
      self.i += 1
    return self._repeat_char(tested, low, high, greedy) if tested else self._repeat(node, low, high, greedy)

  def _repeat_char(self, test, low, high, greedy):
    budget = self.budget
    def repeat_char(text, position, caps, then):
      size = len(text)
      if greedy:  # a loop, not recursion: a character class over a long text must not use up the stack
        end = position
        while end < size and end - position < high and test(text[end]):
          end += 1
        budget.spend(end - position + 1)
        while end - position >= low:
          budget.tick()
          result = then(end, caps)
          if result is not None:
            return result
          end -= 1
        return None
      end = position
      while end - position < low:
        if end >= size or not test(text[end]):
          return None
        end += 1
      while True:
        budget.tick()
        result = then(end, caps)
        if result is not None:
          return result
        if end >= size or end - position >= high or not test(text[end]):
          return None
        end += 1
    return repeat_char

  def _repeat(self, node, low, high, greedy):
    budget = self.budget
    def repeat(text, at, caps, then):
      def attempt(position, count, character):
        budget.tick()
        def more():
          if count >= high:
            return None
          def after(end, next_character):
            if end == position and count >= low:
              return None  # an empty round would go on for ever
            return attempt(end, count + 1, next_character)
          return node(text, position, character, after)
        if count < low:
          return more()
        if greedy:
          result = more()
          return result if result is not None else then(position, character)
        result = then(position, character)
        return result if result is not None else more()
      return attempt(at, 0, caps)
    return repeat



def _int_arg(args, index, default=0):
  if index >= len(args) or args[index] is UNDEFINED:
    return default
  number = to_number(args[index])
  if number != number:
    return 0
  if number in (math.inf, -math.inf):
    return 2 ** 53 if number > 0 else -2 ** 53
  return int(number)


def _array_position(key):
  return int(key) if key >= 0 and key != math.inf and key == math.floor(key) else -1


def _clamp_slice(value, size):
  return max(size + value, 0) if value < 0 else min(value, size)


class Function:
  # A function of a script; its body is compiled when first called, most of a form's library never is.
  __slots__ = ("interpreter", "definition", "scope", "props")

  def __init__(self, interpreter, definition, scope):
    self.interpreter, self.definition, self.scope = interpreter, definition, scope
    self.props = {}

  def call(self, this, args):
    return self.interpreter.invoke(self, this, args)


class _Definition:
  __slots__ = ("name", "params", "tokens", "start", "end", "body", "var_names", "functions")

  def __init__(self, name, params, tokens, start, end):
    self.name, self.params, self.tokens, self.start, self.end = name, params, tokens, start, end
    self.body = None
    self.var_names = ()
    self.functions = ()


class _Budget:
  __slots__ = ("left",)
  def __init__(self):
    self.left = STEP_BUDGET
  def tick(self):
    self.left -= 1
    if self.left < 0:
      raise Exhausted("kroky")
  def spend(self, steps):
    self.left -= steps
    if self.left < 0:
      raise Exhausted("kroky")
  def spend_text(self, text):
    # Long texts are paid for by size: each copy takes memory and time that one step would not show.
    self.spend(len(text) >> 8)


def _callable(value):
  return isinstance(value, (Function, NativeFunction))


class Interpreter:
  # One form's scripts. The library stays compiled for all runs, its globals live as long as the form is open.

  def __init__(self, library=()):
    self.budget = _Budget()
    self.depth = 0
    self.runs_in_progress = 0
    self.globals = _Scope(None, self._builtins())
    self.builtin_constructors = {name: self.globals.vars[name] for name in _CONSTRUCTORS}
    self.string_methods = self._string_methods()
    self.array_methods = self._array_methods()
    self.compiled = {}
    self.library = list(library)
    self.library_loaded = False

  def _load_library(self):
    # Later, not when the form opens: many forms never need it. A broken script of the library takes nothing else down.
    if self.library_loaded:
      return
    self.library_loaded = True
    for source in self.library:
      try:
        self._run_program(source, self.globals)
      except (Unsupported, Thrown, Exhausted, RecursionError):
        continue

  def _run_program(self, source, scope):
    definition = self.compiled.get(source)
    if definition is None:
      tokens = _tokenize(source)
      definition = _Definition("", [], tokens, 0, len(tokens) - 1)
      _Compiler(self, definition).compile()
      self.compiled[source] = definition
    self._hoist(definition, scope)
    for statement in definition.body:
      signal = statement(scope)
      if signal is not None:
        break

  def run(self, source, host):
    # A calculation can run inside a button's script, when the script writes a value. It spends the budget of that script:
    # with a fresh budget for each one, a loop of writes would never be stopped.
    is_outermost = self.runs_in_progress == 0
    if is_outermost:
      self.budget.left = STEP_BUDGET
      self.depth = 0
      self._load_library()
      self.budget.left = STEP_BUDGET
    replaced_globals = {name: self.globals.vars[name] for name in host if name in self.globals.vars}
    self.globals.vars.update(host)
    self.runs_in_progress += 1
    try:
      self._run_program(source, _Scope(self.globals))  # what one field's script declares is not another's business
    except RecursionError as error:
      raise Exhausted("zanoření") from error
    except MemoryError as error:
      raise Exhausted("paměť") from error
    except (Unsupported, Exhausted, Thrown):
      raise
    except Exception as error:  # a slip of this interpreter must not take the window down over a hostile file
      raise Unsupported("skript") from error
    finally:
      self.runs_in_progress -= 1
      self.globals.vars.update(replaced_globals)

  def _hoist(self, definition, scope):
    variables = scope.vars
    for name in definition.var_names:
      if name not in variables:
        variables[name] = UNDEFINED
    for function in definition.functions:
      variables[function.name] = Function(self, function, scope)

  def invoke(self, function, this, args):
    definition = function.definition
    if definition.body is None:
      _Compiler(self, definition).compile()
    self.depth += 1
    if self.depth > MAX_CALL_DEPTH:
      self.depth -= 1
      raise Exhausted("zanoření")
    self.budget.tick()
    variables = {"arguments": ScriptArray(list(args)), "this": this}
    scope = _Scope(function.scope, variables)
    self._hoist(definition, scope)
    count = len(args)
    for index, name in enumerate(definition.params):
      variables[name] = args[index] if index < count else UNDEFINED
    try:
      for statement in definition.body:
        signal = statement(scope)
        if signal is not None:
          return signal.value if type(signal) is _Return else UNDEFINED
      return UNDEFINED
    finally:
      self.depth -= 1

  def call(self, callee, this, args, name=""):
    if not _callable(callee):
      raise Unsupported(name or "volání")
    return callee.call(this, args)

  def construct(self, callee, args, name=""):
    if isinstance(callee, NativeFunction):
      if callee.construct is None:
        raise Unsupported(name or "new")
      return callee.construct(args)
    if isinstance(callee, Function):
      created = ScriptObject()
      result = callee.call(created, args)
      return result if isinstance(result, (ScriptObject, ScriptArray, Date, Regex, Function, NativeFunction)) else created
    raise Unsupported(name or "new")


  def get(self, target, key):
    kind = type(target)
    if kind is ScriptArray:
      if type(key) is float:
        index = _array_position(key)
        return target.items[index] if 0 <= index < len(target.items) else UNDEFINED
      name = to_string(key)
      if name == "length":
        return float(len(target.items))
      if name.isdigit():
        return target.items[int(name)] if int(name) < len(target.items) else UNDEFINED
      if name in target.props:
        return target.props[name]
      return self.array_methods.get(name, UNDEFINED) if name in self.array_methods else UNDEFINED
    if kind is str:
      if type(key) is float:
        index = _array_position(key)
        return target[index] if 0 <= index < len(target) else UNDEFINED
      name = to_string(key)
      if name == "length":
        return float(len(target))
      if name.isdigit():
        return target[int(name)] if int(name) < len(target) else UNDEFINED
      return self.string_methods.get(name, UNDEFINED)
    if target is UNDEFINED or target is None:
      raise Thrown(self.error("TypeError", f"Cannot read properties of {to_string(target)} (reading '{to_string(key)}')"))
    name = to_string(key)
    if kind is HostObject:
      if name not in target.props:
        raise Unsupported(f"{target.name}.{name}")
      return target.props[name]
    if kind is ScriptObject or kind is Function or kind is NativeFunction:
      if name in target.props:
        return target.props[name]
      if name == "toString":
        return NativeFunction(lambda this, args: to_string(this))
      if name == "hasOwnProperty" and kind is ScriptObject:
        return NativeFunction(lambda this, args: to_string(args[0] if args else UNDEFINED) in this.props)
      return UNDEFINED
    if kind is float:
      if name == "toString":
        return NativeFunction(_number_to_string)
      if name == "toFixed":
        return NativeFunction(_to_fixed)
      if name == "valueOf":
        return NativeFunction(lambda this, args: this)
      return UNDEFINED
    if kind is bool:
      if name == "toString":
        return NativeFunction(lambda this, args: to_string(this))
      return UNDEFINED
    if kind is Date:
      return _date_method(target, name)
    if kind is Regex:
      if name == "lastIndex":
        return target.last_index
      if name == "source":
        return target.source
      if name == "global":
        return "g" in target.flags
      if name == "test":
        return NativeFunction(lambda this, args: self.regex_exec(this, to_string(args[0] if args else UNDEFINED)) is not None)
      if name == "exec":
        return NativeFunction(lambda this, args: self.regex_result(this, to_string(args[0] if args else UNDEFINED)))
      if name == "toString":
        return NativeFunction(lambda this, args: to_string(this))
      return UNDEFINED
    raise Unsupported("vlastnost")

  def put(self, target, key, value):
    kind = type(target)
    if kind is ScriptArray:
      index = None
      if type(key) is float and _array_position(key) >= 0:
        index = _array_position(key)
      elif type(key) is str and key.isdigit():
        index = int(key)
      if index is not None:
        if index >= MAX_ARRAY_ITEMS:
          raise Exhausted("pole")
        items = target.items
        if index >= len(items):
          self.budget.spend(index - len(items) + 1)
          items.extend([UNDEFINED] * (index - len(items) + 1))
        items[index] = value
      elif to_string(key) == "length":
        size = _int_arg([value], 0)
        if not 0 <= size < MAX_ARRAY_ITEMS:
          raise Exhausted("pole")
        del target.items[size:]
        target.items.extend([UNDEFINED] * (size - len(target.items)))
      else:
        target.props[to_string(key)] = value
    elif kind is Regex and to_string(key) == "lastIndex":
      target.last_index = to_number(value)
    elif kind is HostObject:
      raise Unsupported(f"{target.name}.{to_string(key)}")
    elif kind in (ScriptObject, Function, NativeFunction, Date, Regex):
      target.props[to_string(key)] = value
    elif target is UNDEFINED or target is None:
      raise Thrown(self.error("TypeError", "Cannot set properties"))
    return value

  def error(self, name, message):
    return ScriptObject({"name": name, "message": message})


  def regex_exec(self, regex, text):
    tracked = "g" in regex.flags
    start = _int_arg([regex.last_index], 0) if tracked else 0
    if start > len(text) or start < 0:
      regex.last_index = 0.0
      return None
    found = regex.match_at(text, start)
    if tracked:
      regex.last_index = float(found[1]) if found else 0.0
    return found

  def regex_result(self, regex, text):
    found = self.regex_exec(regex, text)
    if found is None:
      return None
    return self._match_array(found, text)

  def _match_array(self, found, text):
    begin, end, captures = found
    result = ScriptArray([text[begin:end], *(UNDEFINED if character is None else character for character in captures[1:])])
    result.props["index"] = float(begin)
    result.props["input"] = text
    return result


  def _make_regex(self, args):
    pattern = args[0] if args else UNDEFINED
    flags = to_string(args[1]) if len(args) > 1 and args[1] is not UNDEFINED else ""
    if isinstance(pattern, Regex):
      return Regex(pattern.source, flags or pattern.flags, self.budget)
    return Regex("(?:)" if pattern is UNDEFINED else to_string(pattern), flags, self.budget)

  def _make_date(self, args):
    if not args:
      return Date(_current_local_time())
    if len(args) == 1:
      value = args[0]
      if isinstance(value, Date):
        return Date(value.ms)
      if type(to_primitive(value)) is str:
        return Date(_parse_date(to_string(value)))
      return Date(to_number(value))
    parts = [to_number(arg) for arg in args[:7]]
    if 0 <= parts[0] <= 99:
      parts[0] += 1900
    return Date(_make_time(*parts))

  def _make_array(self, args):
    if len(args) == 1 and type(args[0]) is float:
      size = _int_arg(args, 0)
      if not 0 <= size < MAX_ARRAY_ITEMS or size != args[0]:
        raise Thrown(self.error("RangeError", "Invalid array length"))
      self.budget.spend(size)
      return ScriptArray([UNDEFINED] * size)
    return ScriptArray(list(args))

  def _builtins(self):
    math_props = {name: NativeFunction(lambda this, args, function=function: _math_object(function, args)) for name, function in {
      "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc, "abs": abs, "sqrt": math.sqrt, "exp": math.exp,
      "log": math.log, "sin": math.sin, "cos": math.cos, "tan": math.tan, "atan": math.atan,
    }.items()}
    math_props.update({
      "round": NativeFunction(lambda this, args: _javascript_round(to_number(args[0] if args else UNDEFINED))),
      "max": NativeFunction(lambda this, args: _extreme(args, max, -math.inf)),
      "min": NativeFunction(lambda this, args: _extreme(args, min, math.inf)),
      "pow": NativeFunction(lambda this, args: _power(args)),
      "random": NativeFunction(lambda this, args: 0.5),  # a calculation must give the same answer every time it runs
      "PI": math.pi, "E": math.e,
    })
    date = NativeFunction(lambda this, args: _date_text(_current_local_time()), construct=self._make_date, props={
      "now": NativeFunction(lambda this, args: _current_local_time()),
    })
    string = NativeFunction(lambda this, args: to_string(args[0]) if args else "", props={
      "fromCharCode": NativeFunction(lambda this, args: "".join(chr(to_int32(arg) & 0xFFFF) for arg in args)),
    })
    number = NativeFunction(lambda this, args: to_number(args[0]) if args else 0.0, props={
      "isInteger": NativeFunction(lambda this, args: bool(args) and type(args[0]) is float and args[0] == args[0]
                          and abs(args[0]) != math.inf and args[0] == int(args[0])),
      "isNaN": NativeFunction(lambda this, args: bool(args) and type(args[0]) is float and args[0] != args[0]),
    })
    return {
      "undefined": UNDEFINED, "NaN": math.nan, "Infinity": math.inf,
      "Math": ScriptObject(math_props),
      "Date": date, "String": string, "Number": number,
      "Boolean": NativeFunction(lambda this, args: truthy(args[0]) if args else False),
      "Array": NativeFunction(lambda this, args: self._make_array(args), construct=self._make_array, props={
        "isArray": NativeFunction(lambda this, args: bool(args) and isinstance(args[0], ScriptArray)),
      }),
      "Object": NativeFunction(lambda this, args: ScriptObject(), construct=lambda args: ScriptObject()),
      "RegExp": NativeFunction(lambda this, args: self._make_regex(args), construct=self._make_regex),
      "Error": NativeFunction(lambda this, args: self.error("Error", to_string(args[0]) if args else ""),
                      construct=lambda args: self.error("Error", to_string(args[0]) if args else "")),
      "parseInt": NativeFunction(lambda this, args: _parse_int(args)),
      "parseFloat": NativeFunction(lambda this, args: _parse_float(args)),
      "isNaN": NativeFunction(lambda this, args: to_number(args[0] if args else UNDEFINED) != to_number(args[0] if args else UNDEFINED)),
      "isFinite": NativeFunction(lambda this, args: _finite(to_number(args[0] if args else UNDEFINED))),
      "JSON": ScriptObject({"parse": NativeFunction(lambda this, args: self._json_parse(args)),
                   "stringify": NativeFunction(lambda this, args: self._json_text(args))}),
    }

  def _json_parse(self, args):
    try:
      text = to_string(args[0] if args else UNDEFINED)
      if len(text) > MAX_STRING_LENGTH:
        raise Exhausted("text")
      self.budget.spend_text(text)
      return _from_json(json.loads(text))
    except (ValueError, RecursionError) as error:
      raise Thrown(self.error("SyntaxError", "JSON")) from error

  def _json_text(self, args):
    try:
      result = json.dumps(_to_json(args[0] if args else UNDEFINED), ensure_ascii=False, separators=(",", ":"))
      self.budget.spend_text(result)
      return result
    except (ValueError, RecursionError) as error:
      raise Thrown(self.error("TypeError", "JSON")) from error

  def _string_methods(self):
    budget = self.budget

    def method(function):
      def call(this, args):
        text = to_string(this)
        budget.spend_text(text)
        result = function(text, args)
        if type(result) is str:
          budget.spend_text(result)
        elif type(result) is ScriptArray:
          budget.spend(len(result.items))
        return result
      return NativeFunction(call)

    def substring(text, args):
      size = len(text)
      left = min(max(_int_arg(args, 0), 0), size)
      right = min(max(_int_arg(args, 1, size), 0), size)
      return text[min(left, right):max(left, right)]

    def substr(text, args):
      start = _clamp_slice(_int_arg(args, 0), len(text))
      length = _int_arg(args, 1, len(text))
      return text[start:start + max(length, 0)]

    def slice_(text, args):
      size = len(text)
      left, right = _clamp_slice(_int_arg(args, 0), size), _clamp_slice(_int_arg(args, 1, size), size)
      return text[left:right] if left < right else ""

    def split(s, args):
      separator = args[0] if args else UNDEFINED
      limit = _int_arg(args, 1, MAX_ARRAY_ITEMS)
      if separator is UNDEFINED:
        parts = [s]
      elif isinstance(separator, Regex):
        parts = self._regex_split(s, separator)
      else:
        text = to_string(separator)
        parts = list(s) if text == "" else s.split(text)
      return ScriptArray(parts[:limit])

    def replace(s, args):
      pattern = args[0] if args else UNDEFINED
      replacement = args[1] if len(args) > 1 else UNDEFINED
      if isinstance(pattern, Regex):
        return self._regex_replace(s, pattern, replacement)
      text = to_string(pattern)
      position = s.find(text)
      if position < 0:
        return s
      found = (position, position + len(text), [None])
      return s[:position] + self._replacement(replacement, s, found) + s[position + len(text):]

    def match(s, args):
      pattern = args[0] if args else UNDEFINED
      regex = pattern if isinstance(pattern, Regex) else self._make_regex([pattern])
      if "g" not in regex.flags:
        return self.regex_result(regex, s)
      found = [text for text in (match[0] for match in self._all_matches(regex, s))]
      return ScriptArray(found) if found else None

    def search(text, args):
      pattern = args[0] if args else UNDEFINED
      regex = pattern if isinstance(pattern, Regex) else self._make_regex([pattern])
      found = regex.match_at(text, 0)
      return float(found[0]) if found else -1.0

    def concat(text, args):
      result = text + "".join(to_string(arg) for arg in args)
      if len(result) > MAX_STRING_LENGTH:
        raise Exhausted("text")
      return result

    def repeat(text, args):
      count = _int_arg(args, 0)
      if count < 0 or len(text) * count > MAX_STRING_LENGTH:
        raise Exhausted("text")
      return text * count

    def pad(text, args, left):
      size = _int_arg(args, 0)
      filler = to_string(args[1]) if len(args) > 1 and args[1] is not UNDEFINED else " "
      if size <= len(text) or not filler:
        return text
      if size > MAX_STRING_LENGTH:
        raise Exhausted("text")
      extra = (filler * (size // len(filler) + 1))[:size - len(text)]
      return extra + text if left else text + extra

    return {name: method(function) for name, function in {
      "charAt": lambda text, value: (lambda index: text[index] if 0 <= index < len(text) else "")(_int_arg(value, 0)),
      "charCodeAt": lambda text, value: (lambda index: float(ord(text[index])) if 0 <= index < len(text) else math.nan)(_int_arg(value, 0)),
      "indexOf": lambda text, value: float(text.find(to_string(value[0] if value else UNDEFINED), min(max(_int_arg(value, 1), 0), len(text)))),
      "lastIndexOf": lambda text, value: float(text.rfind(to_string(value[0] if value else UNDEFINED), 0,
                                                len(text) if len(value) < 2 else max(_int_arg(value, 1), 0) + len(to_string(value[0])))),
      "substring": substring, "substr": substr, "slice": slice_, "split": split, "replace": replace,
      "match": match, "search": search, "concat": concat, "repeat": repeat,
      "toLowerCase": lambda text, value: text.lower(), "toUpperCase": lambda text, value: text.upper(),
      "trim": lambda text, value: text.strip(), "toString": lambda text, value: text, "valueOf": lambda text, value: text,
      "startsWith": lambda text, value: text.startswith(to_string(value[0] if value else UNDEFINED), max(_int_arg(value, 1), 0)),
      "endsWith": lambda text, value: text[:_int_arg(value, 1, len(text))].endswith(to_string(value[0] if value else UNDEFINED)),
      "includes": lambda text, value: to_string(value[0] if value else UNDEFINED) in text,
      "padStart": lambda text, value: pad(text, value, True), "padEnd": lambda text, value: pad(text, value, False),
      "localeCompare": lambda text, value: (lambda target: float((text > target) - (text < target)))(to_string(value[0] if value else UNDEFINED)),
    }.items()}

  def _all_matches(self, regex, text):
    regex.last_index = 0.0
    found = []
    position = 0
    while position <= len(text):
      match = regex.match_at(text, position)
      if match is None:
        break
      found.append((text[match[0]:match[1]], match))
      position = match[1] if match[1] > match[0] else match[1] + 1
      if len(found) > MAX_ARRAY_ITEMS:
        raise Exhausted("pole")
    return found

  def _regex_split(self, text, regex):
    if not text:
      return [] if regex.match_at(text, 0) else [""]
    parts, last, position = [], 0, 0
    while position < len(text):
      match = regex.match_at(text, position)
      if match is None or match[0] >= len(text):
        break
      begin, end, captures = match
      if end == last:  # an empty match where the last piece ended separates nothing yet
        position += 1
        continue
      parts.append(text[last:begin])
      parts.extend(UNDEFINED if character is None else character for character in captures[1:])
      last = position = end
      if len(parts) > MAX_ARRAY_ITEMS:
        raise Exhausted("pole")
    parts.append(text[last:])
    return parts

  def _replacement(self, replacement, text, found):
    begin, end, captures = found
    if _callable(replacement):
      args = [text[begin:end], *(UNDEFINED if character is None else character for character in captures[1:]), float(begin), text]
      return to_string(replacement.call(UNDEFINED, args))
    template = to_string(replacement)
    if "$" not in template:
      return template

    def expand(match):
      token = match.group(1)
      if token == "$":
        return "$"
      if token == "&":
        return text[begin:end]
      if token == "`":
        return text[:begin]
      if token == "'":
        return text[end:]
      number = int(token)
      if number >= len(captures) and len(token) == 2:
        number, rest = int(token[0]), token[1]
      else:
        rest = ""
      if not 1 <= number < len(captures):
        return match.group()
      return (captures[number] or "") + rest
    return re.sub(r"\$(\$|&|`|'|\d\d?)", expand, template)

  def _regex_replace(self, text, regex, replacement):
    if "g" in regex.flags:
      matches = [match[1] for match in self._all_matches(regex, text)]
      regex.last_index = 0.0
    else:
      found = regex.match_at(text, 0)
      matches = [found] if found else []
    parts, last = [], 0
    size = 0
    for found in matches:
      piece = self._replacement(replacement, text, found)
      parts.extend((text[last:found[0]], piece))
      size += len(piece) + found[0] - last
      if size > MAX_STRING_LENGTH:
        raise Exhausted("text")
      last = found[1]
    parts.append(text[last:])
    return "".join(parts)

  def _array_methods(self):
    def method(function):
      return NativeFunction(lambda this, args: function(self._need_array(this), args))

    def push(value, args):
      if len(value.items) + len(args) > MAX_ARRAY_ITEMS:
        raise Exhausted("pole")
      value.items.extend(args)
      return float(len(value.items))

    def join(value, args):
      separator = to_string(args[0]) if args and args[0] is not UNDEFINED else ","
      self.budget.spend(len(value.items))
      result = separator.join("" if item is None or item is UNDEFINED else to_string(item) for item in value.items)
      if len(result) > MAX_STRING_LENGTH:
        raise Exhausted("text")
      self.budget.spend_text(result)
      return result

    def slice_(value, args):
      size = len(value.items)
      self.budget.spend(size)
      return ScriptArray(value.items[_clamp_slice(_int_arg(args, 0), size):_clamp_slice(_int_arg(args, 1, size), size)])

    def splice(value, args):
      size = len(value.items)
      start = _clamp_slice(_int_arg(args, 0), size)
      count = min(max(_int_arg(args, 1, size - start), 0), size - start)
      removed = value.items[start:start + count]
      value.items[start:start + count] = args[2:]
      self.budget.spend(size)
      return ScriptArray(removed)

    def concat(value, args):
      result = list(value.items)
      for arg in args:
        if isinstance(arg, ScriptArray):
          result.extend(arg.items)
        else:
          result.append(arg)
        if len(result) > MAX_ARRAY_ITEMS:
          raise Exhausted("pole")
      self.budget.spend(len(result))
      return ScriptArray(result)

    def index_of(value, args):
      wanted = args[0] if args else UNDEFINED
      self.budget.spend(len(value.items))
      for index, item in enumerate(value.items):
        if strict_equal(item, wanted):
          return float(index)
      return -1.0

    def last_index_of(value, args):
      wanted = args[0] if args else UNDEFINED
      self.budget.spend(len(value.items))
      for index in range(len(value.items) - 1, -1, -1):
        if strict_equal(value.items[index], wanted):
          return float(index)
      return -1.0

    def sort(value, args):
      import functools
      compare = args[0] if args and _callable(args[0]) else None
      self.budget.spend(len(value.items) * 8)
      def order(base, exponent):
        if base is UNDEFINED or exponent is UNDEFINED:
          return (base is UNDEFINED) - (exponent is UNDEFINED)
        if compare is not None:
          number = to_number(compare.call(UNDEFINED, [base, exponent]))
          return 0 if number != number else (number > 0) - (number < 0)
        base, exponent = to_string(base), to_string(exponent)
        return (base > exponent) - (base < exponent)
      value.items.sort(key=functools.cmp_to_key(order))
      return value

    def visit(value, args):
      function = args[0] if args else UNDEFINED
      if not _callable(function):
        raise Thrown(self.error("TypeError", "not a function"))
      for index, item in enumerate(list(value.items)):
        self.budget.tick()
        yield item, function.call(UNDEFINED, [item, float(index), value])

    def for_each(value, args):
      for _ in visit(value, args):
        pass
      return UNDEFINED

    return {name: method(f) for name, f in {
      "push": push, "join": join, "slice": slice_, "splice": splice, "concat": concat,
      "indexOf": index_of, "lastIndexOf": last_index_of, "sort": sort, "forEach": for_each,
      "pop": lambda value, args: value.items.pop() if value.items else UNDEFINED,
      "shift": lambda value, args: value.items.pop(0) if value.items else UNDEFINED,
      "unshift": lambda value, args: (value.items.__setitem__(slice(0, 0), list(args)), float(len(value.items)))[1],
      "reverse": lambda value, args: (value.items.reverse(), value)[1],
      "toString": lambda value, args: to_string(value),
      "map": lambda value, args: ScriptArray([result for _, result in visit(value, args)]),
      "filter": lambda value, args: ScriptArray([item for item, result in visit(value, args) if truthy(result)]),
      "some": lambda value, args: any(truthy(result) for _, result in visit(value, args)),
      "every": lambda value, args: all(truthy(result) for _, result in visit(value, args)),
    }.items()}

  def _need_array(self, value):
    if not isinstance(value, ScriptArray):
      raise Unsupported("metoda pole")
    return value


def _number_to_string(this, args):
  radix = _int_arg(args, 0, 10)
  if radix == 10 or this != this or abs(this) == math.inf:
    return number_text(this)
  if not 2 <= radix <= 36:
    raise Unsupported("toString")
  whole = int(this)
  if whole != this:
    raise Unsupported("toString")
  digits = "0123456789abcdefghijklmnopqrstuvwxyz"
  text, number = "", abs(whole)
  while number:
    number, rest = divmod(number, radix)
    text = digits[rest] + text
  return ("-" if whole < 0 else "") + (text or "0")


def _to_fixed(this, args):
  from decimal import ROUND_HALF_UP, Decimal
  places = _int_arg(args, 0)
  if not 0 <= places <= 100:
    raise Unsupported("toFixed")
  if this != this or abs(this) == math.inf or abs(this) >= 1e21:
    return number_text(this)
  # the exact binary value decides, as in JavaScript: (1.005).toFixed(2) is 1.00
  return format(Decimal(this).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP), "f")


def _math_object(function, args):
  number = to_number(args[0] if args else UNDEFINED)
  if number != number:
    return math.nan
  try:
    if abs(number) == math.inf and function in (math.floor, math.ceil, math.trunc):
      return number
    return float(function(number))
  except ValueError:
    return math.nan
  except OverflowError:
    return math.inf


def _javascript_round(number):
  if number != number or abs(number) == math.inf:
    return number
  return float(math.floor(number + 0.5))


def _extreme(args, pick, empty):
  numbers = [to_number(arg) for arg in args]
  if any(number != number for number in numbers):
    return math.nan
  return pick(numbers) if numbers else empty


def _power(args):
  base, exponent = to_number(args[0] if args else UNDEFINED), to_number(args[1] if len(args) > 1 else UNDEFINED)
  try:
    return float(math.pow(base, exponent))
  except (ValueError, ZeroDivisionError):
    return math.nan
  except OverflowError:
    return math.inf


def _finite(number):
  return number == number and abs(number) != math.inf


def _parse_int(args):
  text = to_string(args[0] if args else UNDEFINED).strip()
  radix = _int_arg(args, 1)
  sign = 1
  if text[:1] in ("+", "-"):
    sign = -1 if text[0] == "-" else 1
    text = text[1:]
  if radix in (0, 16) and text[:2] in ("0x", "0X"):
    text, radix = text[2:], 16
  radix = radix or 10
  if not 2 <= radix <= 36:
    return math.nan
  value = None
  for character in text:
    digit = int(character, 36) if character.isascii() and character.isalnum() else 99
    if digit >= radix:
      break
    value = (value or 0) * radix + digit
  return math.nan if value is None else float(sign * value)


_FLOAT_START = re.compile(r'[+-]?(?:Infinity|\d+\.?\d*(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)')


def _parse_float(args):
  match = _FLOAT_START.match(to_string(args[0] if args else UNDEFINED).strip())
  return float(match.group().replace("Infinity", "inf")) if match else math.nan


def _from_json(value):
  if isinstance(value, dict):
    return ScriptObject({key: _from_json(item) for key, item in value.items()})
  if isinstance(value, list):
    return ScriptArray([_from_json(item) for item in value])
  if isinstance(value, bool) or value is None or isinstance(value, str):
    return value
  return float(value)


def _to_json(value):
  if value is UNDEFINED or _callable(value):
    return None
  if isinstance(value, ScriptArray):
    return [_to_json(item) for item in value.items]
  if isinstance(value, Date):
    return to_string(value)
  if isinstance(value, ScriptObject):
    return {key: _to_json(item) for key, item in value.props.items() if item is not UNDEFINED and not _callable(item)}
  if type(value) is float:
    if value != value or abs(value) == math.inf:
      return None
    return int(value) if value == int(value) and abs(value) < 2 ** 53 else value
  return value



_BINARY = {
  "||": 1, "&&": 2, "|": 3, "^": 4, "&": 5, "==": 6, "!=": 6, "===": 6, "!==": 6,
  "<": 7, ">": 7, "<=": 7, ">=": 7, "instanceof": 7, "in": 7,
  "<<": 8, ">>": 8, ">>>": 8, "+": 9, "-": 9, "*": 10, "/": 10, "%": 10,
}
_CONSTRUCTORS = ("RegExp", "Date", "Array", "Object", "Error")
_ASSIGN = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", ">>>="}
_RESERVED = {"var", "let", "const", "function", "return", "if", "else", "for", "while", "do", "switch", "case", "default",
             "break", "continue", "new", "typeof", "void", "delete", "throw", "try", "catch", "finally", "this", "in",
             "instanceof", "null", "true", "false"}


def _javascript_add(left, right, budget):
  if type(left) is float and type(right) is float:
    return left + right
  left, right = to_primitive(left), to_primitive(right)
  if type(left) is str or type(right) is str:
    result = to_string(left) + to_string(right)
    if len(result) > MAX_STRING_LENGTH:
      raise Exhausted("text")
    budget.spend_text(result)
    return result
  return to_number(left) + to_number(right)


def _divide(left, right):
  left, right = to_number(left), to_number(right)
  if right == 0:
    if left == 0 or left != left:
      return math.nan
    return math.copysign(math.inf, left) * math.copysign(1.0, right)
  try:
    return left / right
  except OverflowError:
    return math.inf


def _remainder(left, right):
  left, right = to_number(left), to_number(right)
  if left != left or right != right or abs(left) == math.inf or right == 0:
    return math.nan
  if abs(right) == math.inf:
    return left
  return math.fmod(left, right)


def _times(left, right):
  return to_number(left) * to_number(right)


def _minus(left, right):
  return to_number(left) - to_number(right)


def _shift_left(left, right):
  value = (to_int32(left) << (to_int32(right) & 31)) & 0xFFFFFFFF
  return float(value - 0x100000000 if value >= 0x80000000 else value)


_OPERATIONS = {
  "-": _minus, "*": _times, "/": _divide, "%": _remainder,
  "&": lambda left, right: float(to_int32(left) & to_int32(right)),
  "|": lambda left, right: float(to_int32(left) | to_int32(right)),
  "^": lambda left, right: float(to_int32(left) ^ to_int32(right)),
  "<<": _shift_left,
  ">>": lambda left, right: float(to_int32(left) >> (to_int32(right) & 31)),
  ">>>": lambda left, right: float((to_int32(left) & 0xFFFFFFFF) >> (to_int32(right) & 31)),
  "==": loose_equal, "!=": lambda left, right: not loose_equal(left, right),
  "===": strict_equal, "!==": lambda left, right: not strict_equal(left, right),
  "<": lambda left, right: _less_than(left, right) is True,
  ">": lambda left, right: _less_than(right, left) is True,
  "<=": lambda left, right: _less_than(right, left) is False,
  ">=": lambda left, right: _less_than(left, right) is False,
}


class _Compiler:
  MAX_NESTING = 120

  def __init__(self, interpreter, definition):
    self.it = interpreter
    self.definition = definition
    self.tokens = definition.tokens
    self.pos = definition.start
    self.end = definition.end
    self.var_names = set()
    self.functions = []
    self.nesting = 0
    budget = interpreter.budget
    self.operations = {**_OPERATIONS, "+": lambda left, right: _javascript_add(left, right, budget)}


  def fail(self):
    token = self.tokens[self.pos] if self.pos < len(self.tokens) else ("eof", "", False)
    raise Unsupported(f"nečitelný skript u „{token[1]}“")

  def at(self, value):
    token = self.tokens[self.pos]
    return token[1] == value and token[0] in ("punct", "name") and self.pos < self.end

  def accept(self, value):
    if self.at(value):
      self.pos += 1
      return True
    return False

  def expect(self, value):
    if not self.accept(value):
      self.fail()

  def name(self):
    token = self.tokens[self.pos]
    if token[0] != "name" or token[1] in _RESERVED or self.pos >= self.end:
      self.fail()
    self.pos += 1
    return token[1]

  def semicolon(self):
    if self.accept(";"):
      return
    token = self.tokens[self.pos]
    if self.pos >= self.end or token[2] or token[1] == "}":
      return
    self.fail()

  def deeper(self):
    self.nesting += 1
    if self.nesting > self.MAX_NESTING:
      raise Unsupported("příliš zanořený skript")


  def compile(self):
    body = []
    while self.pos < self.end:
      statement = self.statement()
      if statement is not None:
        body.append(statement)
    self.definition.var_names = tuple(self.var_names)
    self.definition.functions = tuple(self.functions)
    self.definition.body = body

  def function_definition(self, name):
    self.expect("(")
    params = []
    if not self.at(")"):
      params.append(self.name())
      while self.accept(","):
        params.append(self.name())
    self.expect(")")
    if not self.at("{"):
      self.fail()
    start = self.pos + 1
    self._skip_block()
    return _Definition(name, params, self.tokens, start, self.pos - 1)

  def _skip_block(self):
    depth = 0
    tokens = self.tokens
    position = self.pos
    while position < self.end:
      token = tokens[position]
      if token[0] == "punct":
        if token[1] == "{":
          depth += 1
        elif token[1] == "}":
          depth -= 1
          if depth == 0:
            self.pos = position + 1
            return
      position += 1
    self.pos = position
    self.fail()


  def block(self):
    self.expect("{")
    self.deeper()
    body = []
    while not self.at("}"):
      if self.pos >= self.end:
        self.fail()
      statement = self.statement()
      if statement is not None:
        body.append(statement)
    self.pos += 1
    self.nesting -= 1
    return self._sequence(body)

  @staticmethod
  def _sequence(body):
    if len(body) == 1:
      return body[0]
    def sequence(scope):
      for statement in body:
        signal = statement(scope)
        if signal is not None:
          return signal
      return None
    return sequence

  def statement(self):
    if self.pos >= self.end:
      self.fail()
    token = self.tokens[self.pos]
    value = token[1]
    if token[0] == "punct":
      if value == "{":
        return self.block()
      if value == ";":
        self.pos += 1
        return None
    elif token[0] == "name" and value in _RESERVED:
      handler = {"var": self.declaration, "let": self.declaration, "const": self.declaration,
                 "function": self.function_statement, "if": self.if_statement, "for": self.for_statement,
                 "while": self.while_statement, "do": self.do_statement, "switch": self.switch_statement,
                 "return": self.return_statement, "break": self.break_statement, "continue": self.continue_statement,
                 "throw": self.throw_statement, "try": self.try_statement}.get(value)
      if handler is not None:
        return handler()
    expression = self.expression()
    self.semicolon()
    tick = self.it.budget.tick
    def expression_statement(scope):
      tick()
      expression(scope)
    return expression_statement

  def declaration(self):
    self.pos += 1
    statement = self._declarators()
    self.semicolon()
    return statement

  def _declarators(self, allow_in=True):
    assignments = []
    while True:
      name = self.name()
      self.var_names.add(name)
      if self.accept("="):
        assignments.append((name, self.assignment(allow_in)))
      if not self.accept(","):
        break
    if not assignments:
      return None
    tick = self.it.budget.tick
    def declare(scope):
      tick()
      for name, value in assignments:
        _store(scope, name, value(scope))
    return declare

  def function_statement(self):
    self.pos += 1
    name = self.name()
    self.functions.append(self.function_definition(name))
    return None

  def _statement_or_empty(self):
    statement = self.statement()
    return statement if statement is not None else (lambda scope: None)

  def if_statement(self):
    self.pos += 1
    self.expect("(")
    test = self.expression()
    self.expect(")")
    self.deeper()
    yes = self._statement_or_empty()
    no = self._statement_or_empty() if self.accept("else") else None
    self.nesting -= 1
    tick = self.it.budget.tick
    def if_(scope):
      tick()
      if truthy(test(scope)):
        return yes(scope)
      return no(scope) if no is not None else None
    return if_

  def while_statement(self):
    self.pos += 1
    self.expect("(")
    test = self.expression()
    self.expect(")")
    self.deeper()
    body = self._statement_or_empty()
    self.nesting -= 1
    tick = self.it.budget.tick
    def while_(scope):
      while True:
        tick()
        if not truthy(test(scope)):
          return None
        signal = body(scope)
        if signal is _BREAK:
          return None
        if signal is not None and signal is not _CONTINUE:
          return signal
    return while_

  def do_statement(self):
    self.pos += 1
    self.deeper()
    body = self._statement_or_empty()
    self.nesting -= 1
    self.expect("while")
    self.expect("(")
    test = self.expression()
    self.expect(")")
    self.accept(";")
    tick = self.it.budget.tick
    def do(scope):
      while True:
        tick()
        signal = body(scope)
        if signal is _BREAK:
          return None
        if signal is not None and signal is not _CONTINUE:
          return signal
        if not truthy(test(scope)):
          return None
    return do

  def for_statement(self):
    self.pos += 1
    self.expect("(")
    start = None
    token = self.tokens[self.pos]
    if token[0] == "name" and token[1] in ("var", "let", "const"):
      mark = self.pos
      self.pos += 1
      name = self.name()
      if self.accept("in"):
        self.var_names.add(name)
        return self._for_in(name)
      self.pos = mark + 1
      start = self._declarators(allow_in=False)
    elif not self.at(";"):
      mark = self.pos
      if token[0] == "name" and token[1] not in _RESERVED and self.tokens[self.pos + 1][1] == "in":
        self.pos += 2
        return self._for_in(token[1])
      self.pos = mark
      start = self.expression(allow_in=False)
    self.expect(";")
    test = None if self.at(";") else self.expression()
    self.expect(";")
    update = None if self.at(")") else self.expression()
    self.expect(")")
    self.deeper()
    body = self._statement_or_empty()
    self.nesting -= 1
    tick = self.it.budget.tick
    def for_(scope):
      if start is not None:
        start(scope)
      while True:
        tick()
        if test is not None and not truthy(test(scope)):
          return None
        signal = body(scope)
        if signal is _BREAK:
          return None
        if signal is not None and signal is not _CONTINUE:
          return signal
        if update is not None:
          update(scope)
    return for_

  def _for_in(self, name):
    target = self.expression()
    self.expect(")")
    self.deeper()
    body = self._statement_or_empty()
    self.nesting -= 1
    tick = self.it.budget.tick
    def for_in(scope):
      value = target(scope)
      if isinstance(value, ScriptArray):
        keys = [str(index) for index in range(len(value.items))]
      elif type(value) is str:
        keys = [str(index) for index in range(len(value))]
      elif isinstance(value, HostObject):
        raise Unsupported(value.name)
      elif isinstance(value, ScriptObject):
        keys = list(value.props)
      else:
        keys = []
      for key in keys:
        tick()
        _store(scope, name, key)
        signal = body(scope)
        if signal is _BREAK:
          return None
        if signal is not None and signal is not _CONTINUE:
          return signal
      return None
    return for_in

  def switch_statement(self):
    self.pos += 1
    self.expect("(")
    subject = self.expression()
    self.expect(")")
    self.expect("{")
    self.deeper()
    cases = []  # (test or None for default, statements)
    while not self.accept("}"):
      if self.accept("case"):
        test = self.expression()
      elif self.accept("default"):
        test = None
      else:
        self.fail()
      self.expect(":")
      body = []
      while not (self.at("case") or self.at("default") or self.at("}")):
        statement = self.statement()
        if statement is not None:
          body.append(statement)
      cases.append((test, self._sequence(body) if body else None))
    self.nesting -= 1
    tick = self.it.budget.tick
    def switch(scope):
      tick()
      value = subject(scope)
      first = None
      for index, (test, _) in enumerate(cases):
        if test is not None and strict_equal(value, test(scope)):
          first = index
          break
      if first is None:
        for index, (test, _) in enumerate(cases):
          if test is None:
            first = index
            break
      if first is None:
        return None
      for _, body in cases[first:]:
        if body is None:
          continue
        signal = body(scope)
        if signal is _BREAK:
          return None
        if signal is not None:
          return signal
      return None
    return switch

  def return_statement(self):
    self.pos += 1
    token = self.tokens[self.pos]
    if self.at(";") or self.at("}") or token[2] or self.pos >= self.end:
      self.semicolon()
      return lambda scope: _Return(UNDEFINED)
    value = self.expression()
    self.semicolon()
    tick = self.it.budget.tick
    def return_(scope):
      tick()
      return _Return(value(scope))
    return return_

  def break_statement(self):
    self.pos += 1
    self.semicolon()
    return lambda scope: _BREAK

  def continue_statement(self):
    self.pos += 1
    self.semicolon()
    return lambda scope: _CONTINUE

  def throw_statement(self):
    self.pos += 1
    value = self.expression()
    self.semicolon()
    def throw(scope):
      raise Thrown(value(scope))
    return throw

  def try_statement(self):
    self.pos += 1
    body = self.block()
    handler = None
    name = None
    if self.accept("catch"):
      self.expect("(")
      name = self.name()
      self.expect(")")
      self.var_names.add(name)
      handler = self.block()
    final = self.block() if self.accept("finally") else None
    if handler is None and final is None:
      self.fail()
    def guarded(scope):
      try:
        return body(scope)
      except Thrown as error:
        if handler is None:
          raise
        _store(scope, name, error.value)
        return handler(scope)
    if final is None:
      return guarded
    def try_(scope):
      try:
        signal = guarded(scope)
      except Thrown:
        after = final(scope)
        if after is not None:
          return after  # a finally that leaves overrides the exception in this language
        raise
      after = final(scope)
      return after if after is not None else signal
    return try_


  def expression(self, allow_in=True):
    first = self.assignment(allow_in)
    if not self.at(","):
      return first
    parts = [first]
    while self.accept(","):
      parts.append(self.assignment(allow_in))
    def comma(scope):
      value = UNDEFINED
      for part in parts:
        value = part(scope)
      return value
    return comma

  def assignment(self, allow_in=True):
    self.deeper()
    left = self.conditional(allow_in)
    token = self.tokens[self.pos]
    if token[0] != "punct" or token[1] not in _ASSIGN or self.pos >= self.end:
      self.nesting -= 1
      return left
    target = self._target(left)
    self.pos += 1
    value = self.assignment(allow_in)
    self.nesting -= 1
    return target(token[1], value)

  def _target(self, left):
    # What an assignment writes to: the left side was compiled once, as a reading of the same place.
    if isinstance(left, _Variable):
      return self._name_target(left.js_name)
    if isinstance(left, _Member):
      return self._member_target(left.base, left.key)
    self.fail()
    return None

  def _property_name(self):
    token = self.tokens[self.pos]
    if token[0] != "name" or self.pos >= self.end:
      self.fail()
    self.pos += 1
    return token[1]

  def _name_target(self, name):
    operations = self.operations
    def build(operator, value):
      if operator == "=":
        def assign(scope):
          result = value(scope)
          _store(scope, name, result)
          return result
        return assign
      operation = operations[operator[:-1]]
      def update(scope):
        result = operation(_load(scope, name), value(scope))
        _store(scope, name, result)
        return result
      return update
    return build

  def _member_target(self, base, key):
    put, get = self.it.put, self.it.get
    def build(operator, value):
      if operator == "=":
        def assign(scope):
          target, name = base(scope), key(scope)
          return put(target, name, value(scope))
        return assign
      operation = self.operations[operator[:-1]]
      def update(scope):
        target, name = base(scope), key(scope)
        return put(target, name, operation(get(target, name), value(scope)))
      return update
    return build

  def conditional(self, allow_in):
    test = self.binary(1, allow_in)
    if not self.accept("?"):
      return test
    yes = self.assignment()
    self.expect(":")
    no = self.assignment(allow_in)
    return lambda scope: yes(scope) if truthy(test(scope)) else no(scope)

  def binary(self, level, allow_in):
    left = self.unary()
    while True:
      token = self.tokens[self.pos]
      if self.pos >= self.end or token[0] not in ("punct", "name"):
        return left
      operator = token[1]
      precedence = _BINARY.get(operator)
      if precedence is None or precedence < level or (operator == "in" and not allow_in):
        return left
      if token[0] == "name" and operator not in ("in", "instanceof"):
        return left
      self.pos += 1
      right = self.binary(precedence + 1, allow_in)
      left = self._operator(operator, left, right)

  def _operator(self, operator, left, right):
    if operator == "&&":
      def both(scope):
        value = left(scope)
        return right(scope) if truthy(value) else value
      return both
    if operator == "||":
      def either(scope):
        value = left(scope)
        return value if truthy(value) else right(scope)
      return either
    if operator == "in":
      def has(scope):
        key, target = left(scope), right(scope)
        if isinstance(target, ScriptArray):
          return 0 <= to_number(key) < len(target.items)
        if isinstance(target, HostObject):
          return to_string(key) in target.props
        if isinstance(target, ScriptObject):
          return to_string(key) in target.props
        raise Thrown(self.it.error("TypeError", "in"))
      return has
    if operator == "instanceof":
      def instance(scope):
        value, kind = left(scope), right(scope)
        builtins = self.it.globals.vars
        if kind is builtins.get("Array"):
          return isinstance(value, ScriptArray)
        if kind is builtins.get("Date"):
          return isinstance(value, Date)
        if kind is builtins.get("RegExp"):
          return isinstance(value, Regex)
        if kind is builtins.get("Object"):
          return isinstance(value, (ScriptObject, ScriptArray, Date, Regex, Function, NativeFunction))
        raise Unsupported("instanceof")
      return instance
    operation = self.operations[operator]
    return lambda scope: operation(left(scope), right(scope))

  def unary(self):
    token = self.tokens[self.pos]
    if self.pos >= self.end:
      self.fail()
    value = token[1]
    if token[0] == "punct" and value in ("!", "-", "+", "~"):
      self.pos += 1
      self.deeper()
      inner = self.unary()
      self.nesting -= 1
      if value == "!":
        return lambda scope: not truthy(inner(scope))
      if value == "-":
        return lambda scope: -to_number(inner(scope))
      if value == "+":
        return lambda scope: to_number(inner(scope))
      return lambda scope: float(~to_int32(inner(scope)))
    if token[0] == "punct" and value in ("++", "--"):
      self.pos += 1
      return self._increment(self.unary(), 1.0 if value == "++" else -1.0, prefix=True)
    if token[0] == "name" and value == "typeof":
      self.pos += 1
      next_token = self.tokens[self.pos]
      following = self.tokens[self.pos + 1] if self.pos + 1 < len(self.tokens) else ("eof", "", False)
      inner_name = next_token[1] if next_token[0] == "name" and next_token[1] not in _RESERVED and following[1] not in (".", "[", "(") else None
      inner = self.unary()
      if inner_name is not None:
        def type_of_name(scope):
          probe = scope
          while probe is not None:
            if inner_name in probe.vars:
              return type_of(probe.vars[inner_name])
            probe = probe.parent
          return "undefined"
        return type_of_name
      return lambda scope: type_of(inner(scope))
    if token[0] == "name" and value == "void":
      self.pos += 1
      inner = self.unary()
      def void(scope):
        inner(scope)
        return UNDEFINED
      return void
    if token[0] == "name" and value == "delete":
      self.pos += 1
      inner = self.unary()
      if not isinstance(inner, _Member):
        self.fail()
      base, key = inner.base, inner.key
      def delete(scope):
        target, name = base(scope), to_string(key(scope))
        if isinstance(target, HostObject):
          raise Unsupported(target.name)
        if hasattr(target, "props"):
          target.props.pop(name, None)
        return True
      return delete
    return self.postfix()

  def postfix(self):
    value = self.member()
    token = self.tokens[self.pos]
    if token[0] == "punct" and token[1] in ("++", "--") and not token[2] and self.pos < self.end:
      self.pos += 1
      return self._increment(value, 1.0 if token[1] == "++" else -1.0, prefix=False)
    return value

  def _increment(self, place, step, prefix):
    if isinstance(place, _Variable):
      name = place.js_name
      def bump(scope):
        old = to_number(_load(scope, name))
        _store(scope, name, old + step)
        return old + step if prefix else old
      return bump
    if not isinstance(place, _Member):
      self.fail()
    base, key = place.base, place.key
    get, put = self.it.get, self.it.put
    def bump_member(scope):
      target, name = base(scope), key(scope)
      old = to_number(get(target, name))
      put(target, name, old + step)
      return old + step if prefix else old
    return bump_member

  def arguments(self):
    self.expect("(")
    args = []
    if not self.at(")"):
      args.append(self.assignment())
      while self.accept(","):
        args.append(self.assignment())
    self.expect(")")
    return args

  def _read(self, base, key):
    return _Member(self.it.get, base, key)

  def _call_of(self, value, base, key, args):
    # A call of value; when it was just read off an object (base, key), that object becomes its this.
    tick = self.it.budget.tick
    interpreter = self.it
    if base is not None:
      get = self.it.get
      def method_call(scope):
        tick()
        this = base(scope)
        name = key(scope)
        function = get(this, name)
        if not _callable(function):
          raise Unsupported(to_string(name))
        return function.call(this, [arg(scope) for arg in args])
      return method_call
    name = getattr(value, "js_name", "")
    def call(scope):
      tick()
      function = value(scope)
      return interpreter.call(function, UNDEFINED, [arg(scope) for arg in args], name)
    return call

  def member(self):
    if self.at("new"):
      value = self.new_expression()
    else:
      value = self.primary()
    base, key = None, None  # the object and key value was read from, for this in a call
    while self.pos < self.end:
      if self.accept("."):
        key_value = self._property_name()
        base, key = value, _constant(key_value)
        value = self._read(base, key)
      elif self.accept("["):
        inner = self.expression()
        self.expect("]")
        base, key = value, inner
        value = self._read(base, key)
      elif self.at("("):
        args = self.arguments()
        value = self._call_of(value, base, key, args)
        base, key = None, None
      else:
        break
    return value

  def new_expression(self):
    self.pos += 1
    if self.at("new"):
      callee = self.new_expression()
    else:
      callee = self.primary()
    label = getattr(callee, "js_name", "")
    if label in _CONSTRUCTORS:
      callee = self._constructor(label, callee)
    while self.pos < self.end:
      if self.accept("."):
        key = _constant(self._property_name())
        callee = self._read(callee, key)
      elif self.accept("["):
        key = self.expression()
        self.expect("]")
        callee = self._read(callee, key)
      else:
        break
    args = self.arguments() if self.at("(") else []
    interpreter = self.it
    tick = self.it.budget.tick
    def new(scope):
      tick()
      return interpreter.construct(callee(scope), [arg(scope) for arg in args], label)
    return new

  def _constructor(self, name, callee):
    # Forms of one family define their own RegExp(value, type) for validation next to code using new RegExp(pattern),
    # which only makes sense if new still reaches the language's constructor.
    builtin = self.it.builtin_constructors[name]
    def constructor(scope):
      value = callee(scope)
      return value if isinstance(value, NativeFunction) else builtin
    return constructor

  def primary(self):
    if self.pos >= self.end:
      self.fail()
    kind, value, _ = self.tokens[self.pos]
    self.pos += 1
    if kind == "num" or kind == "str":
      return _constant(value)
    if kind == "re":
      source, flags = value
      interpreter = self.it
      Regex(source, flags, interpreter.budget)  # an unreadable one stops the script now, not half way
      return lambda scope: Regex(source, flags, interpreter.budget)
    if kind == "name":
      if value == "true":
        return _constant(True)
      if value == "false":
        return _constant(False)
      if value == "null":
        return _constant(None)
      if value == "this":
        return _this
      if value == "function":
        return self.function_expression()
      if value in _RESERVED:
        self.pos -= 1
        self.fail()
      return _Variable(value)
    if value == "(":
      self.deeper()
      inner = self.expression()
      self.nesting -= 1
      self.expect(")")
      return inner
    if value == "[":
      return self.array_literal()
    if value == "{":
      return self.object_literal()
    self.pos -= 1
    self.fail()
    return None

  def function_expression(self):
    token = self.tokens[self.pos]
    name = ""
    if token[0] == "name" and token[1] not in _RESERVED:
      name = token[1]
      self.pos += 1
    definition = self.function_definition(name)
    interpreter = self.it
    def make(scope):
      if name:
        scope = _Scope(scope)
        function = Function(interpreter, definition, scope)
        scope.vars[name] = function
        return function
      return Function(interpreter, definition, scope)
    return make

  def array_literal(self):
    self.deeper()
    items = []
    while not self.accept("]"):
      if self.at(","):
        self.pos += 1
        items.append(_constant(UNDEFINED))
        continue
      items.append(self.assignment())
      if not self.at("]"):
        self.expect(",")
    self.nesting -= 1
    return lambda scope: ScriptArray([item(scope) for item in items])

  def object_literal(self):
    self.deeper()
    properties = []
    while not self.accept("}"):
      kind, value, _ = self.tokens[self.pos]
      if kind not in ("name", "str", "num") or self.pos >= self.end:
        self.fail()
      self.pos += 1
      self.expect(":")
      properties.append((to_string(value), self.assignment()))
      if not self.at("}"):
        self.expect(",")
    self.nesting -= 1
    return lambda scope: ScriptObject({key: item(scope) for key, item in properties})


def _constant(value):
  return lambda scope: value


class _Member:
  # Reading a[b]. Stays inspectable: an assignment or ++ then writes to the same place.
  __slots__ = ("get", "base", "key")
  def __init__(self, get, base, key):
    self.get, self.base, self.key = get, base, key
  def __call__(self, scope):
    return self.get(self.base(scope), self.key(scope))


class _Variable:
  __slots__ = ("js_name",)
  def __init__(self, name):
    self.js_name = name
  def __call__(self, scope):
    name = self.js_name
    while scope is not None:
      variables = scope.vars
      if name in variables:
        return variables[name]
      scope = scope.parent
    raise Unsupported(name)


def _this(scope):
  while scope is not None:
    if "this" in scope.vars:
      return scope.vars["this"]
    scope = scope.parent
  return UNDEFINED


def _load(scope, name):
  while scope is not None:
    if name in scope.vars:
      return scope.vars[name]
    scope = scope.parent
  raise Unsupported(name)


def _store(scope, name, value):
  first = scope
  while scope is not None:
    if name in scope.vars:
      scope.vars[name] = value
      return
    if scope.parent is None:
      scope.vars[name] = value  # an undeclared name becomes a global, as in this dialect
      return
    scope = scope.parent
  first.vars[name] = value



# Not after : or /, that is an element of quoted XPath such as {../d:function}.
_SCRIPT_MARK = re.compile(r'(?<![:/])\b(?:var\s|function\b|GetDocument\s*\(|Result\s*\()')


def is_script(value):
  # Whether a bind's expression is JavaScript. Braces alone also quote XPath.
  return value.lstrip().startswith("{") and _SCRIPT_MARK.search(value) is not None


def _birth_number_valid(text):
  # As the RCCheck of expressions in xpath_ext: 9 or 10 digits, divisible by 11.
  digits = text.replace("/", "").strip()
  return digits.isascii() and digits.isdigit() and 9 <= len(digits) <= 10 and int(digits) % 11 == 0


def evaluate(interpreter, source, data_xpath, current_path="", list_item=None):
  # Runs a bind's script and gives the text it passed to Result, None when it did not call it.
  # data_xpath(expression) answers the script's queries with a value: text, number, boolean or None.
  results = []

  def to_value(answer):
    if type(answer) is bool or type(answer) is str:
      return answer
    if answer is None:
      return ""
    return float(answer)

  def ask(this, args):
    answer = to_value(data_xpath(to_string(args[0] if args else UNDEFINED)))
    return to_string(answer)

  def element_method(this, args):
    return element

  element_props = {
    "DataXpath": NativeFunction(ask),
    "GetCurrentDataXPath": NativeFunction(lambda this, args: current_path),
  }
  if list_item is not None:
    element_props["GetListItemData"] = NativeFunction(lambda this, args: list_item(*(to_string(arg) for arg in args)))
  element = HostObject("element", element_props)
  document = HostObject("document", {
    "GetElement": NativeFunction(element_method),
    # only tell the user that something runs: nothing here takes long enough, and nothing may be shown from a calculation
    "SetProgressInfo": NativeFunction(lambda this, args: UNDEFINED),
  })
  interpreter.run(source, {
    "GetDocument": NativeFunction(lambda this, args: document),
    "Result": NativeFunction(lambda this, args: results.append(to_string(args[0] if args else UNDEFINED))),
    "DbgLog": NativeFunction(lambda this, args: UNDEFINED),
    "RCCheck": NativeFunction(lambda this, args: _birth_number_valid(to_string(args[0] if args else UNDEFINED))),
  })
  return results[-1] if results else None


_FORM_ACTION_CONSTANTS = {
  "PI_BEGINPROGRESS": 0.0, "PI_SETTEXT": 1.0, "PI_ENDPROGRESS": 2.0,
  "MB_OK": 0.0, "MB_YESNO": 4.0, "MB_ICONWARNING": 48.0, "MB_ICONINFORMATION": 64.0, "MB_DEFAULTBTN2": 256.0,
  "MB_HTMLCONTENT": 65536.0, "MB_ICON2WARNINGRED": 131072.0, "MB_ICON2CHECK": 262144.0,
  "IDOK": 1.0, "IDYES": 6.0, "IDNO": 7.0,
  "VarStr": 0.0, "VarFXML": 1.0, "VarFPdf": 2.0, "VarFFo": 3.0, "VarFUndef": 4.0, "VarCUTF8": 5.0,
  "GFD_INCLUDINGIRRELEVANT": 1.0, "GFD_CUTNAMESPACES": 2.0, "GFD_ORIGINAL": 4.0, "GFD_SIGNDATA": 8.0,
}
MB_YESNO = _FORM_ACTION_CONSTANTS["MB_YESNO"]
IDOK, IDYES, IDNO = _FORM_ACTION_CONSTANTS["IDOK"], _FORM_ACTION_CONSTANTS["IDYES"], _FORM_ACTION_CONSTANTS["IDNO"]


def run_form_action(interpreter, source, host):
  # Runs the script of a button or of a field change against host (form_scripts.FormActionHost). What the application
  # offered beyond these names, sending, saving, printing and signing above all, is absent here, so it stops the script.
  # A script can keep these functions in a global: they refuse outside the run they were made for, and in the calculations
  # nested in it.
  run_level = interpreter.runs_in_progress + 1
  is_finished = []

  def native(function):
    def guarded(this, args):
      if is_finished or interpreter.runs_in_progress != run_level:
        raise Unsupported("skript mimo tlačítko")
      return function(*args)
    return NativeFunction(guarded)

  def text(value=UNDEFINED):
    return to_string(value)

  def object_factory():
    return NativeFunction(lambda this, args: ScriptObject(), construct=lambda args: ScriptObject({"data": ""}))

  def message_box(content=UNDEFINED, title=UNDEFINED, flags=0.0):
    return host.show_message(text(content), "" if title is UNDEFINED else text(title), to_number(flags))

  def data_xpath(expression=UNDEFINED):
    answer = host.data_xpath(text(expression))
    return to_string("" if answer is None else answer)

  def form_data(parameters=UNDEFINED):
    xsl_name = interpreter.get(parameters, "xslName") if isinstance(parameters, ScriptObject) else UNDEFINED
    return ScriptObject({"data": host.form_data(to_string(xsl_name))})

  element = HostObject("element", {
    "DataXpath": native(data_xpath),
    "GetCurrentDataXPath": native(host.current_path),
    "GetListItemData": native(lambda *args: host.list_item(*(to_string(arg) for arg in args))),
    "GetAttribute": native(lambda name=UNDEFINED, path=UNDEFINED: host.attribute(text(name), text(path))),
    "SetData": native(lambda path=UNDEFINED, bind=UNDEFINED, value=UNDEFINED: host.set_data(text(path), text(bind), text(value)) or UNDEFINED),
    "SetDataTo": native(lambda bind=UNDEFINED, value=UNDEFINED: host.set_data_to(text(bind), text(value)) or UNDEFINED),
    "InsertSection": native(lambda path=UNDEFINED: host.insert_section(text(path))),
    "RemoveSection": native(lambda path=UNDEFINED: host.remove_section(text(path))),
    "ActivateField": native(lambda path=UNDEFINED, *_: host.activate_field(text(path)) or UNDEFINED),
    "ActivateFieldEx": native(lambda path=UNDEFINED, *_: host.activate_field(text(path)) or UNDEFINED),
  })
  document = HostObject("document", {
    "GetElement": native(lambda *_: element),
    "SetProgressInfo": native(lambda *_: UNDEFINED),
    "CheckData": native(lambda *_: host.check_data()),
    "GetFormData": native(form_data),
  })
  named_parameters = host.named_parameters
  globals_for_action = {
    **_FORM_ACTION_CONSTANTS,
    "GetDocument": native(lambda *_: document),
    "MessageBox": native(message_box),
    "DbgLog": native(lambda *_: UNDEFINED),
    "RCCheck": native(lambda value=UNDEFINED, *_: _birth_number_valid(text(value))),
    "GetNamedParam": native(lambda name=UNDEFINED, *_: named_parameters.get(text(name), "")),
    "SetNamedParam": native(lambda name=UNDEFINED, value=UNDEFINED, *_: host.set_named_parameter(text(name), text(value)) or UNDEFINED),
    "GetCustomItem": native(lambda *_: ""),
    "Variant": object_factory(),
    "DataParam": object_factory(),
  }
  try:
    interpreter.run(source, globals_for_action)
  finally:
    is_finished.append(True)
