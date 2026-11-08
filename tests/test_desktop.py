"""The desktop host desktop.py: file associations, file dialogs, saving attachments, printing and fonts on Linux."""
import importlib.util
import json
import plistlib
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from support import ROOT, isds_sample

import app
import desktop
import isds_message
import linux_print

PYWEBVIEW_FILE_TYPE_PATTERN = r"^([\w ]+)\((\*(?:\.(?:\w+|\*))*(?:;\*(?:\.(?:\w+|\*))*)*)\)$"

class FileAssociations(unittest.TestCase):
    FO_MIME_TYPE = "application/vnd.software602.filler.form+xml"

    def test_macos_declares_the_type_and_the_document(self):
        with open(ROOT / "macapp/Info.plist", "rb") as info:
            plist = plistlib.load(info)
        declared = next(item for item in plist["UTImportedTypeDeclarations"]
                        if item["UTTypeTagSpecification"]["public.filename-extension"] == ["fo"])
        document_types = [content_type for item in plist["CFBundleDocumentTypes"] for content_type in item["LSItemContentTypes"]]
        self.assertIn(declared["UTTypeIdentifier"], document_types)

    def test_windows_registers_the_extension(self):
        script = (ROOT / "packaging/windows/blanketa.iss").read_text(encoding="utf-8")
        self.assertIn(r'Software\Classes\.fo\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.fo"', script)
        self.assertIn(r'Software\Classes\Blanketa.fo\shell\open\command', script)

    def test_linux_packages_declare_the_type(self):
        for name in ("blanketa.desktop", "install.sh"):
            self.assertIn(self.FO_MIME_TYPE, (ROOT / "packaging/linux" / name).read_text(encoding="utf-8"), name)
        for name in ("blanketa-mime.xml", "install.sh"):
            self.assertIn('<glob pattern="*.fo" weight="60"/>', (ROOT / "packaging/linux" / name).read_text(encoding="utf-8"), name)

    ASIC_TYPES = {"application/vnd.etsi.asic-e+zip": ("asice", "sce"), "application/vnd.etsi.asic-s+zip": ("asics", "scs")}

    def test_asic_containers_are_registered_on_every_platform(self):
        with open(ROOT / "macapp/Info.plist", "rb") as info:
            plist = plistlib.load(info)
        document_types = [content_type for item in plist["CFBundleDocumentTypes"] for content_type in item["LSItemContentTypes"]]
        for mime, extensions in self.ASIC_TYPES.items():
            declared = next(item for item in plist["UTImportedTypeDeclarations"]
                            if item["UTTypeTagSpecification"]["public.mime-type"] == mime)
            self.assertEqual(declared["UTTypeTagSpecification"]["public.filename-extension"], list(extensions))
            self.assertIn("public.zip-archive", declared["UTTypeConformsTo"])
            self.assertIn(declared["UTTypeIdentifier"], document_types)
        script = (ROOT / "packaging/windows/blanketa.iss").read_text(encoding="utf-8")
        self.assertIn(r'Software\Classes\Blanketa.asic\shell\open\command', script)
        for extension in ("asice", "sce", "asics", "scs"):
            self.assertIn(rf'Software\Classes\.{extension}\OpenWithProgids"; ValueType: string; ValueName: "Blanketa.asic"', script)
        for extension in ("asice", "asics"):
            self.assertIn(rf'Software\Classes\.{extension}"; ValueType: string; ValueName: ""; ValueData: "Blanketa.asic"; Tasks: asicdefault', script)
        for name in ("blanketa.desktop", "install.sh"):
            text = (ROOT / "packaging/linux" / name).read_text(encoding="utf-8")
            for mime in self.ASIC_TYPES:
                self.assertIn(mime + ";", text, name)
        for name in ("blanketa-mime.xml", "install.sh"):
            text = (ROOT / "packaging/linux" / name).read_text(encoding="utf-8")
            for mime, extensions in self.ASIC_TYPES.items():
                self.assertIn(f'<mime-type type="{mime}">', text, name)
                for extension in extensions:
                    self.assertIn(f'<glob pattern="*.{extension}"/>', text, name)

    def test_zfo_type_names_data_messages_too(self):
        description = "Formulář 602XML nebo datová zpráva (ZFO)"
        for path in ("macapp/Info.plist", "packaging/windows/blanketa.iss", "packaging/linux/blanketa-mime.xml",
                     "packaging/linux/install.sh"):
            text = (ROOT / path).read_text(encoding="utf-8")
            self.assertIn(description, text, path)
            self.assertNotIn("Formulář 602XML (ZFO)", text, path)
        for path in ("packaging/linux/blanketa.desktop", "packaging/linux/install.sh", "packaging/linux/control"):
            self.assertIn("datových zpráv", (ROOT / path).read_text(encoding="utf-8"), path)

    def test_file_dialogs_offer_the_extension(self):
        self.assertTrue(any("*.fo" in file_type for file_type in desktop.OPEN_DIALOG_FILE_TYPES))
        for file_type in desktop.OPEN_DIALOG_FILE_TYPES:
            self.assertRegex(file_type, PYWEBVIEW_FILE_TYPE_PATTERN)
        self.assertIn('UTType(filenameExtension: "fo")', (ROOT / "macapp/main.swift").read_text(encoding="utf-8"))


class DesktopDataFileDialog(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.window = mock.Mock()

    def saved_file(self, *chosen_names):
        self.window.create_file_dialog.side_effect = [str(self.folder / name) for name in chosen_names]
        bridge = desktop.Bridge(mock.Mock(), mock.Mock())
        bridge._save_file_from_dialog(self.window, {"name": "zadost-data.xfdf", "folder": str(self.folder), "content": "XFDF",
                                                    "contents": {"xfdf": "XFDF", "fdf": "FDF"}})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["file_types"], ("XFDF (*.xfdf)", "FDF (*.fdf)"))
        return {path.name: path.read_text(encoding="utf-8") for path in self.folder.iterdir()}

    def test_existing_file_with_the_added_extension_is_asked_about_again(self):
        (self.folder / "data.xfdf").write_text("old", encoding="utf-8")
        self.assertEqual(self.saved_file("data", "data.xfdf"), {"data.xfdf": "XFDF"})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["save_filename"], "data.xfdf")

    def test_content_follows_the_chosen_extension(self):
        self.assertEqual(self.saved_file("data.fdf"), {"data.fdf": "FDF"})
        self.assertEqual(self.saved_file("data.XFDF"), {"data.fdf": "FDF", "data.XFDF": "XFDF"})

    def test_name_without_a_known_extension_gets_the_default_one(self):
        self.assertEqual(self.saved_file("data"), {"data.xfdf": "XFDF"})


class DesktopAttachmentSaving(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        port = self.server.server_address[1]
        self.session = app.new_session({"name": "zprava", "message": isds_message.read_message(isds_sample("prijata"))})
        self.window = mock.Mock()
        self.window.get_current_url.return_value = f"http://127.0.0.1:{port}/"
        self.bridge = desktop.Bridge(mock.Mock(), mock.Mock(home=f"http://127.0.0.1:{port}/", window=self.window))

    def files(self, *indexes):
        names = isds_message.read_message(isds_sample("prijata")).info["attachments"]
        return [{"url": f"/attachment?s={self.session}&i={index}", "name": names[index]["name"]} for index in indexes]

    def test_attachment_url_rule(self):
        self.assertTrue(desktop.attachment_url_is_allowed("/attachment?s=Ab_c-9&i=12"))
        for url in ("http://evil.example/attachment?s=x&i=1", "/pdf?s=x", "/attachment?s=x&i=1&j=2", "/attachment?s=x&i=1#",
                    "//evil.example/attachment?s=x&i=1", "/attachment?s=x&i=-1", 5, None):
            with self.subTest(url=url):
                self.assertFalse(desktop.attachment_url_is_allowed(url))

    def test_free_target_never_overwrites(self):
        (self.folder / "a.pdf").write_bytes(b"1")
        (self.folder / "a (2).pdf").write_bytes(b"2")
        self.assertEqual(desktop.free_target(self.folder, "a.pdf"), self.folder / "a (3).pdf")
        self.assertEqual(desktop.free_target(self.folder, "b"), self.folder / "b")
        for name in ("..", ".", "a/b", "a\\b", "a\x00b", "", "C:x.txt"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                desktop.free_target(self.folder, name)

    def test_desktop_saves_one_attachment_where_the_user_chose(self):
        self.window.create_file_dialog.return_value = str(self.folder / "jinak.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.files(0), "folder": False})
        self.assertEqual(self.window.create_file_dialog.call_args.kwargs["save_filename"], "rozhodnuti.pdf")
        self.assertEqual((self.folder / "jinak.pdf").read_bytes(), b"%PDF-1.4\n% TEST priloha\n")
        self.window.evaluate_js.assert_called_with(f"window.zfoAttachmentSaved && zfoAttachmentSaved({json.dumps(str(self.folder / 'jinak.pdf'))})")

    def test_desktop_saves_all_into_the_folder(self):
        (self.folder / "data.xml").write_bytes(b"older")
        self.window.create_file_dialog.return_value = (str(self.folder),)
        self.bridge.message({"action": "saveAttachment", "files": self.files(0, 1, 2), "folder": True})
        self.assertEqual(self.window.create_file_dialog.call_args.args[0], self.bridge._app.webview.FileDialog.FOLDER)
        self.assertEqual(sorted(path.name for path in self.folder.iterdir()), ["data (2).xml", "data.xml", "evil.txt", "rozhodnuti.pdf"])
        self.assertEqual((self.folder / "data.xml").read_bytes(), b"older")
        self.window.evaluate_js.assert_called_with(f"window.zfoAttachmentSaved && zfoAttachmentSaved({json.dumps(str(self.folder))})")

    def test_desktop_reports_a_failed_save_on_the_page(self):
        self.window.create_file_dialog.return_value = str(self.folder / "chybi" / "x.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.files(0), "folder": False})
        self.assertIn("zfoAttachmentFailed", self.window.evaluate_js.call_args.args[0])

    def test_desktop_refuses_foreign_urls_and_names(self):
        self.window.create_file_dialog.return_value = (str(self.folder),)
        for files in ([{"url": "http://evil.example/x", "name": "x"}], [{"url": f"/attachment?s={self.session}&i=0", "name": "../x"}]):
            with self.subTest(files=files):
                self.bridge.message({"action": "saveAttachment", "files": files, "folder": True})
        self.assertEqual(list(self.folder.iterdir()), [])


class DesktopContainerFileSaving(DesktopAttachmentSaving):
    def setUp(self):
        super().setUp()
        self.container = app.new_session({"entries": [b"%PDF-1.7 smlouva", b"data"], "names": ["smlouva.pdf", "data.txt"]})
        self.bridge._app.open_window = mock.Mock()

    def one(self, index=0, name="smlouva.pdf"):
        return [{"url": f"/container-file?s={self.container}&i={index}", "name": name}]

    def test_container_file_url_rule(self):
        self.assertTrue(desktop.attachment_url_is_allowed("/container-file?s=Ab_c-9&i=12"))
        for url in ("/container-file?s=x&i=1&j=2", "//evil.example/container-file?s=x&i=1", "http://127.0.0.1/container-file?s=x&i=1",
                    "/container-file?s=x&i=1#", "/container-file?s=x&i=-1", "/container-files?s=x&i=1",
                    "/container-file?s=x&i=\u0661", "/container-file?s=x&i=1\n"):
            with self.subTest(url=url):
                self.assertFalse(desktop.attachment_url_is_allowed(url))

    def test_saves_a_container_file(self):
        self.window.create_file_dialog.return_value = str(self.folder / "kopie.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False})
        self.assertEqual((self.folder / "kopie.pdf").read_bytes(), b"%PDF-1.7 smlouva")
        self.bridge._app.open_window.assert_not_called()

    def test_open_opens_the_saved_copy_in_a_new_window(self):
        self.window.create_file_dialog.return_value = str(self.folder / "kopie.pdf")
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False, "open": True})
        self.bridge._app.open_window.assert_called_once_with(self.folder / "kopie.pdf")

    def test_open_with_a_cancelled_dialog_opens_nothing(self):
        self.window.create_file_dialog.return_value = None
        self.bridge.message({"action": "saveAttachment", "files": self.one(), "folder": False, "open": True})
        self.bridge._app.open_window.assert_not_called()

    def test_open_is_refused_for_a_folder_or_several_files(self):
        files = self.one() + self.one(1, "data.txt")
        for request in ({"files": self.one(), "folder": True}, {"files": files, "folder": False}, {"files": files, "folder": True}):
            with self.subTest(request=request):
                self.bridge.message({"action": "saveAttachment", "open": True, **request})
        self.window.create_file_dialog.assert_not_called()
        self.bridge._app.open_window.assert_not_called()

    def test_open_dialog_offers_containers(self):
        self.assertEqual(desktop.OPEN_DIALOG_FILE_TYPES,
                         ("Podporované soubory (*.zfo;*.fo;*.pdf;*.asice;*.sce;*.asics;*.scs)", "Všechny soubory (*.*)"))
        first = desktop.OPEN_DIALOG_FILE_TYPES[0]
        for extension in ("zfo", "fo", "pdf", "asice", "sce", "asics", "scs"):
            self.assertIn(f"*.{extension}", first)
        main = (ROOT / "macapp/main.swift").read_text(encoding="utf-8")
        for extension in ("asice", "sce", "asics", "scs"):
            self.assertIn(f'"{extension}"', main)
        self.assertIn('&i=[0-9]+\\z"#', main)


class LinuxPdfPrinting(unittest.TestCase):
    A4 = (595.28, 841.89)

    def test_page_fills_paper_of_its_shape(self):
        placed = linux_print.fit_page_on_paper(self.A4, (4800, 6800), 600)
        self.assertEqual(placed.rotation, 0)
        self.assertEqual((placed.x, placed.width), (0, 4800))
        self.assertAlmostEqual(placed.y * 2 + placed.height, 6800)

    def test_landscape_page_turns_onto_portrait_paper(self):
        placed = linux_print.fit_page_on_paper((self.A4[1], self.A4[0]), (4800, 6800), 600)
        self.assertEqual(placed.rotation, 90)
        self.assertLessEqual(placed.width, 4800)
        self.assertLessEqual(placed.height, 6800)
        self.assertAlmostEqual(placed.width / placed.height, self.A4[0] / self.A4[1])

    def test_image_resolution_stops_at_the_print_limit(self):
        for printer_dpi, image_dpi in ((1200, linux_print.MAX_PRINT_DPI), (150, 150)):
            with self.subTest(printer_dpi=printer_dpi):
                placed = linux_print.fit_page_on_paper(self.A4, (self.A4[0] / 72 * printer_dpi, self.A4[1] / 72 * printer_dpi), printer_dpi)
                self.assertAlmostEqual(placed.render_scale * linux_print.POINTS_PER_INCH, image_dpi)

    def test_failed_preview_opens_the_file_in_the_system_viewer(self):
        path = Path("/tmp/zfo-tisk-x.pdf")
        with mock.patch.object(linux_print, "show_print_preview", side_effect=ImportError), \
                mock.patch.object(desktop, "open_with_system") as open_with_system:
            desktop.show_print_preview_or_open(path, mock.Mock())
        open_with_system.assert_called_once_with(path)


@unittest.skipUnless(importlib.util.find_spec("pypdfium2"), "pypdfium2 is installed only on Linux")
class LinuxFontSubstitution(unittest.TestCase):
    def setUp(self):
        import pypdfium2.raw as pdfium
        self.pdfium = pdfium
        self.substitution = linux_print.FontSubstitution()
        self.substitution._system = mock.Mock()
        self.substitution._system.contents.MapFont.return_value = 0

    def map_font(self, pitch_family, charset=None):
        charset = self.pdfium.FXFONT_ANSI_CHARSET if charset is None else charset
        return self.substitution._map_font(None, 400, 0, charset, pitch_family, b"Helvetica", None)

    def test_missing_sans_serif_font_gets_liberation_sans(self):
        font = self.map_font(0)
        self.assertTrue(font)
        self.assertGreater(self.substitution._get_font_data(None, font, linux_print.WHOLE_FONT_FILE, None, 0), 0)

    def test_fixed_pitch_serif_and_symbol_fonts_stay_with_pdfium(self):
        for pitch_family, charset in ((self.pdfium.FXFONT_FF_FIXEDPITCH, None), (self.pdfium.FXFONT_FF_ROMAN, None),
                                      (0, self.pdfium.FXFONT_SYMBOL_CHARSET)):
            with self.subTest(pitch_family=pitch_family, charset=charset):
                self.assertEqual(self.map_font(pitch_family, charset), 0)

    def test_installed_system_font_wins(self):
        self.substitution._system.contents.MapFont.return_value = 1234
        self.assertEqual(self.map_font(0), 1234)
