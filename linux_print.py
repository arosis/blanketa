import ctypes
import functools
import sys
from dataclasses import dataclass
from pathlib import Path

MAX_PRINT_DPI = 300
POINTS_PER_INCH = 72
STANDARD_FONTS = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "vendor/pdfjs/standard_fonts"
SUBSTITUTE_FACE_NAME = b"Liberation Sans"
SUBSTITUTE_FONT_FILES = {(False, False): "LiberationSans-Regular.ttf", (True, False): "LiberationSans-Bold.ttf",
                         (False, True): "LiberationSans-Italic.ttf", (True, True): "LiberationSans-BoldItalic.ttf"}
BOLD_FONT_WEIGHT = 600
WHOLE_FONT_FILE = 0


@dataclass(frozen=True)
class PrintedPage:
    rotation: int
    x: float
    y: float
    width: float
    height: float
    render_scale: float


def fit_page_on_paper(page_size, area_size, printer_dpi):
    page_width, page_height = page_size
    area_width, area_height = area_size
    is_rotated = (page_width > page_height) != (area_width > area_height)
    if is_rotated:
        page_width, page_height = page_height, page_width
    scale = min(area_width / page_width, area_height / page_height)
    width, height = page_width * scale, page_height * scale
    image_pixels_per_device_pixel = min(1, MAX_PRINT_DPI / printer_dpi)
    return PrintedPage(rotation=90 if is_rotated else 0, x=(area_width - width) / 2, y=(area_height - height) / 2,
                       width=width, height=height, render_scale=scale * image_pixels_per_device_pixel)


class FontSubstitution:
    # PDFium on Linux draws a font the PDF does not embed with a system font of the same name, otherwise with its own
    # built-in font, which has no Czech letters such as ř or ů. Form fields usually use a non-embedded Helvetica, which
    # Liberation Sans matches in glyph widths; other families would come out with letters overlapping or spread apart.

    def __init__(self):
        import pypdfium2.raw as pdfium
        self._pdfium = pdfium
        self._system = pdfium.FPDF_GetDefaultSystemFontInfo()
        self._substitute_fonts = {}
        self._charsets_without_substitute = {pdfium.FXFONT_SYMBOL_CHARSET, pdfium.FXFONT_SHIFTJIS_CHARSET, pdfium.FXFONT_HANGEUL_CHARSET,
                                             pdfium.FXFONT_GB2312_CHARSET, pdfium.FXFONT_CHINESEBIG5_CHARSET}
        callbacks = {"Release": self._release, "EnumFonts": self._enum_fonts, "MapFont": self._map_font, "GetFont": self._get_font,
                     "GetFontData": self._get_font_data, "GetFaceName": self._get_face_name,
                     "GetFontCharset": self._get_font_charset, "DeleteFont": self._delete_font}
        types = dict(pdfium.FPDF_SYSFONTINFO._fields_)
        self.info = pdfium.FPDF_SYSFONTINFO(version=1, **{name: types[name](callback) for name, callback in callbacks.items()})

    def _substitute_font(self, is_bold, is_italic):
        if (is_bold, is_italic) not in self._substitute_fonts:
            data = (STANDARD_FONTS / SUBSTITUTE_FONT_FILES[is_bold, is_italic]).read_bytes()
            self._substitute_fonts[is_bold, is_italic] = ctypes.create_string_buffer(data, len(data))
        return ctypes.addressof(self._substitute_fonts[is_bold, is_italic])

    def _substitute_data(self, font):
        return next((data for data in self._substitute_fonts.values() if ctypes.addressof(data) == font), None)

    def _release(self, this):
        pass

    def _enum_fonts(self, this, mapper):
        self._system.contents.EnumFonts(self._system, mapper)

    def _map_font(self, this, weight, is_italic, charset, pitch_family, face, is_exact):
        font = self._system.contents.MapFont(self._system, weight, is_italic, charset, pitch_family, face, is_exact)
        is_sans_serif = not pitch_family & (self._pdfium.FXFONT_FF_FIXEDPITCH | self._pdfium.FXFONT_FF_ROMAN)
        if font or charset in self._charsets_without_substitute or not is_sans_serif:
            return font
        return self._substitute_font(weight >= BOLD_FONT_WEIGHT, bool(is_italic))

    def _get_font(self, this, face):
        return self._system.contents.GetFont(self._system, face)

    def _get_font_data(self, this, font, table, buffer, size):
        data = self._substitute_data(font)
        if data is None:
            return self._system.contents.GetFontData(self._system, font, table, buffer, size)
        if table != WHOLE_FONT_FILE:
            return 0
        if buffer and size >= len(data):
            ctypes.memmove(buffer, data, len(data))
        return len(data)

    def _get_face_name(self, this, font, buffer, size):
        if self._substitute_data(font) is None:
            return self._system.contents.GetFaceName(self._system, font, buffer, size)
        name = SUBSTITUTE_FACE_NAME + b"\0"
        if buffer and size >= len(name):
            ctypes.memmove(buffer, name, len(name))
        return len(name)

    def _get_font_charset(self, this, font):
        if self._substitute_data(font) is None:
            return self._system.contents.GetFontCharset(self._system, font)
        return self._pdfium.FXFONT_ANSI_CHARSET

    def _delete_font(self, this, font):
        if self._substitute_data(font) is None:
            self._system.contents.DeleteFont(self._system, font)


@functools.cache
def _install_font_substitution():
    import pypdfium2.raw as pdfium
    substitution = FontSubstitution()
    pdfium.FPDF_SetSystemFontInfo(ctypes.pointer(substitution.info))
    return substitution


@functools.cache
def _gui_thread_runner():
    from qtpy.QtCore import QCoreApplication, QObject, Signal, Slot

    class GuiThreadRunner(QObject):
        requested = Signal(object)

        @Slot(object)
        def run(self, function):
            function()

    runner = GuiThreadRunner()
    runner.moveToThread(QCoreApplication.instance().thread())
    runner.requested.connect(runner.run)
    return runner


def run_on_gui_thread(function):
    _gui_thread_runner().requested.emit(function)


def paint_pdf_pages(document, printer):
    from qtpy.QtCore import QRectF
    from qtpy.QtGui import QImage, QPainter
    from qtpy.QtPrintSupport import QPrinter

    area = printer.pageRect(QPrinter.Unit.DevicePixel)
    painter = QPainter(printer)
    try:
        for index in range(len(document)):
            if index:
                printer.newPage()
            page = document[index]
            placed = fit_page_on_paper(page.get_size(), (area.width(), area.height()), printer.resolution())
            bitmap = page.render(scale=placed.render_scale, rotation=placed.rotation, prefer_bgrx=True)
            image = QImage(bytes(bitmap.buffer), bitmap.width, bitmap.height, bitmap.stride, QImage.Format.Format_RGB32)
            painter.drawImage(QRectF(placed.x, placed.y, placed.width, placed.height), image)
            page.close()
    finally:
        painter.end()


def show_print_preview(path, title, parent=None):
    import pypdfium2
    from qtpy.QtPrintSupport import QPrinter, QPrintPreviewDialog

    _install_font_substitution()
    document = pypdfium2.PdfDocument(path)
    try:
        document.init_forms()
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setDocName(title)
        preview = QPrintPreviewDialog(printer, parent)
        preview.setWindowTitle(f"Náhled tisku – {title}")
        preview.paintRequested.connect(lambda target: paint_pdf_pages(document, target))
        preview.exec()
    finally:
        document.close()
