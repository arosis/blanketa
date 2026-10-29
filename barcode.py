"""Barcodes that 602XML forms draw themselves (fm:barcode), as SVG pictures for the page and for printing.

Only the symbology the forms we know use is drawn: Interleaved 2 of 5. For anything else svg_data_uri() returns ""
and the page shows the barcode's text instead.
"""
import base64
import re

# Interleaved 2 of 5: each digit is five elements, two of them wide; digits are paired, the first drawn in the bars,
# the second in the spaces between them.
ITF_DIGITS = {"0": "nnwwn", "1": "wnnnw", "2": "nwnnw", "3": "wwnnn", "4": "nnwnw",
              "5": "wnwnn", "6": "nwwnn", "7": "nnnww", "8": "wnnwn", "9": "nwnwn"}
WIDE = 3
QUIET = 10


def interleaved_2_of_5(digits):
    """Element widths in narrow modules, alternating bar and space and starting with a bar."""
    if len(digits) % 2:
        digits = "0" + digits
    widths = [1, 1, 1, 1]
    for first, second in zip(digits[::2], digits[1::2]):
        for bar, space in zip(ITF_DIGITS[first], ITF_DIGITS[second]):
            widths += [WIDE if bar == "w" else 1, WIDE if space == "w" else 1]
    return widths + [WIDE, 1, 1]


def length_mm(value, default):
    match = re.fullmatch(r"\s*([\d.]+)\s*(mm|cm|pt|in)?\s*", value or "")
    if not match:
        return default
    return float(match.group(1)) * {"mm": 1, "cm": 10, "pt": 25.4 / 72, "in": 25.4, None: 1}[match.group(2)]


def svg(symbology, data, height, narrow, display_text):
    """SVG of the barcode with its quiet zones, sized in millimetres, or None when it cannot be drawn."""
    if symbology != "interleaved2of5" or not data.isdigit():
        return None
    module = length_mm(narrow, 0.33)
    bars_height = length_mm(height, 10)
    text_height = 0 if display_text == "not" else 3.5
    x, rects = QUIET, []
    for i, width in enumerate(interleaved_2_of_5(data)):
        if i % 2 == 0:
            rects.append(f'<rect x="{x}" width="{width}" height="{bars_height / module:g}"/>')
        x += width
    width_modules = x + QUIET
    height_modules = (bars_height + text_height) / module
    text = ("" if not text_height else
            f'<text x="{width_modules / 2:g}" y="{height_modules - 1:g}" font-family="monospace" '
            f'font-size="{(text_height - 0.8) / module:g}" text-anchor="middle">{data}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_modules * module:g}mm" '
            f'height="{height_modules * module:g}mm" viewBox="0 0 {width_modules:g} {height_modules:g}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<g fill="#000">{"".join(rects)}</g>{text}</svg>')


def svg_data_uri(symbology, data, height="", narrow="", display_text=""):
    drawing = svg(symbology, data.strip(), height, narrow, display_text)
    return "" if drawing is None else "data:image/svg+xml;base64," + base64.b64encode(drawing.encode()).decode()
