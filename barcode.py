import base64
import re

SUPPORTED_SYMBOLOGY = "interleaved2of5"
ITF_DIGIT_PATTERNS = {"0": "nnwwn", "1": "wnnnw", "2": "nwnnw", "3": "wwnnn", "4": "nnwnw",
                      "5": "wnwnn", "6": "nwwnn", "7": "nnnww", "8": "wnnwn", "9": "nwnwn"}
ITF_START_WIDTHS = [1, 1, 1, 1]
WIDE_MODULES = 3
ITF_STOP_WIDTHS = [WIDE_MODULES, 1, 1]
QUIET_ZONE_MODULES = 10
DEFAULT_NARROW_MM = 0.33
DEFAULT_BARS_HEIGHT_MM = 10
TEXT_HEIGHT_MM = 3.5
MILLIMETRES_PER_UNIT = {"mm": 1, "cm": 10, "pt": 25.4 / 72, "in": 25.4, None: 1}


def element_widths_starting_with_bar(digits):
    if len(digits) % 2:
        digits = "0" + digits
    widths = list(ITF_START_WIDTHS)
    for bar_digit, space_digit in zip(digits[::2], digits[1::2], strict=True):
        for bar, space in zip(ITF_DIGIT_PATTERNS[bar_digit], ITF_DIGIT_PATTERNS[space_digit], strict=True):
            widths += [WIDE_MODULES if bar == "w" else 1, WIDE_MODULES if space == "w" else 1]
    return widths + ITF_STOP_WIDTHS


def length_in_mm(value, default):
    match = re.fullmatch(r"\s*([\d.]+)\s*(mm|cm|pt|in)?\s*", value or "")
    if not match:
        return default
    return float(match.group(1)) * MILLIMETRES_PER_UNIT[match.group(2)]


def barcode_svg(symbology, data, height, narrow, display_text):
    if symbology != SUPPORTED_SYMBOLOGY or not data.isdigit():
        return None
    module = length_in_mm(narrow, DEFAULT_NARROW_MM)
    bars_height = length_in_mm(height, DEFAULT_BARS_HEIGHT_MM)
    text_height = 0 if display_text == "not" else TEXT_HEIGHT_MM
    position, rects = QUIET_ZONE_MODULES, []
    for index, width in enumerate(element_widths_starting_with_bar(data)):
        is_bar = index % 2 == 0
        if is_bar:
            rects.append(f'<rect x="{position}" width="{width}" height="{bars_height / module:g}"/>')
        position += width
    width_modules = position + QUIET_ZONE_MODULES
    height_modules = (bars_height + text_height) / module
    text = ("" if not text_height else
            f'<text x="{width_modules / 2:g}" y="{height_modules - 1:g}" font-family="monospace" '
            f'font-size="{(text_height - 0.8) / module:g}" text-anchor="middle">{data}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_modules * module:g}mm" '
            f'height="{height_modules * module:g}mm" viewBox="0 0 {width_modules:g} {height_modules:g}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{"".join(rects)}</g>{text}</svg>')


def svg_data_uri(symbology, data, height="", narrow="", display_text=""):
    drawing = barcode_svg(symbology, data.strip(), height, narrow, display_text)
    return "" if drawing is None else "data:image/svg+xml;base64," + base64.b64encode(drawing.encode()).decode()
